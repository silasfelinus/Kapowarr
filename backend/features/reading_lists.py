# -*- coding: utf-8 -*-

"""Ordered story arcs / reading lists with ComicRack CBL import and export.

The feature deliberately keeps unresolved CBL entries.  A portable reading list
is useful even when part of it is not yet in the local library; those unresolved
or missing entries are precisely the gaps acquisition should be able to fill.
"""

from __future__ import annotations

from time import time
from typing import Any, Dict, Iterable, List, Optional, Tuple
from xml.etree import ElementTree as ET

from backend.base.file_extraction import extract_issue_number
from backend.implementations.matching import match_title
from backend.internals.db import get_db

MAX_CBL_BYTES = 5 * 1024 * 1024
MAX_CBL_BOOKS = 10000


def ensure_reading_list_tables() -> None:
    """Create the additive reading-list tables idempotently.

    Keeping creation beside the feature makes both upgraded and fresh databases
    safe even when a fresh install has already stamped the current DB version.
    """
    get_db().executescript("""
        CREATE TABLE IF NOT EXISTS reading_lists(
            id INTEGER PRIMARY KEY,
            title VARCHAR(255) NOT NULL,
            source VARCHAR(30) NOT NULL DEFAULT 'manual',
            source_name TEXT,
            created_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS reading_list_entries(
            id INTEGER PRIMARY KEY,
            reading_list_id INTEGER NOT NULL,
            position INTEGER NOT NULL,
            series VARCHAR(255) NOT NULL,
            issue_number VARCHAR(30) NOT NULL,
            volume_year INTEGER,
            issue_year INTEGER,
            filename TEXT,
            comicvine_volume_id INTEGER,
            comicvine_issue_id INTEGER,
            volume_id INTEGER,
            issue_id INTEGER,

            FOREIGN KEY (reading_list_id) REFERENCES reading_lists(id)
                ON DELETE CASCADE,
            FOREIGN KEY (volume_id) REFERENCES volumes(id)
                ON DELETE SET NULL,
            FOREIGN KEY (issue_id) REFERENCES issues(id)
                ON DELETE SET NULL,
            UNIQUE(reading_list_id, position)
        );
        CREATE INDEX IF NOT EXISTS reading_list_entries_list_position_index
            ON reading_list_entries(reading_list_id, position);
        CREATE INDEX IF NOT EXISTS reading_list_entries_issue_index
            ON reading_list_entries(issue_id);
    """)


def _optional_int(value: Optional[str]) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _comicvine_ids(book: ET.Element) -> Tuple[Optional[int], Optional[int]]:
    """Read ComicVine IDs from common CBL dialects."""
    for database in book.findall('Database'):
        if (database.get('Name') or '').strip().lower() in ('cv', 'comicvine'):
            return (
                _optional_int(database.get('Series')),
                _optional_int(database.get('Issue'))
            )

    # Some generators use explicit child elements instead of <Database>.
    return (
        _optional_int(book.findtext('ComicID')),
        _optional_int(book.findtext('IssueID'))
    )


def parse_cbl(content: bytes) -> Tuple[str, List[Dict[str, Any]]]:
    """Parse a ComicRack CBL document while retaining its original order."""
    if not content or len(content) > MAX_CBL_BYTES:
        raise ValueError('CBL file is empty or too large')

    lowered = content.lower()
    if b'<!doctype' in lowered or b'<!entity' in lowered:
        raise ValueError('CBL files containing DTD/entity declarations are not supported')

    try:
        root = ET.fromstring(content)
    except ET.ParseError as exc:
        raise ValueError('Invalid CBL XML') from exc

    if root.tag.split('}')[-1] != 'ReadingList':
        raise ValueError('CBL root must be ReadingList')

    title = (root.findtext('Name') or 'Imported Reading List').strip()
    if not title:
        title = 'Imported Reading List'

    books_parent = root.find('Books')
    books = list(books_parent.findall('Book')) if books_parent is not None else []
    if len(books) > MAX_CBL_BOOKS:
        raise ValueError('CBL contains too many books')

    entries: List[Dict[str, Any]] = []
    for position, book in enumerate(books, 1):
        series = (book.get('Series') or '').strip()
        number = (book.get('Number') or '').strip()
        if not series or not number:
            # Preserve ordering for valid books, but malformed anonymous rows do
            # not have enough identity to be actionable.
            continue

        cv_volume_id, cv_issue_id = _comicvine_ids(book)
        entries.append({
            'position': position,
            'series': series,
            'issue_number': number,
            'volume_year': _optional_int(book.get('Volume')),
            'issue_year': _optional_int(book.get('Year')),
            'filename': book.get('FileName'),
            'comicvine_volume_id': cv_volume_id,
            'comicvine_issue_id': cv_issue_id
        })

    return title[:255], entries


def _issue_number_as_float(issue_number: str) -> Optional[float]:
    parsed = extract_issue_number(issue_number)
    return parsed if isinstance(parsed, float) else None


def _resolve_entry(entry: Dict[str, Any]) -> Tuple[Optional[int], Optional[int]]:
    """Resolve one portable entry to local volume/issue IDs conservatively."""
    cursor = get_db()

    cv_issue_id = entry.get('comicvine_issue_id')
    if cv_issue_id:
        exact = cursor.execute("""
            SELECT volume_id, id
            FROM issues
            WHERE comicvine_id = ?
            LIMIT 1;
        """, (cv_issue_id,)).fetchone()
        if exact:
            return exact['volume_id'], exact['id']

    calculated = _issue_number_as_float(entry['issue_number'])
    if calculated is None:
        return None, None

    cv_volume_id = entry.get('comicvine_volume_id')
    if cv_volume_id:
        exact = cursor.execute("""
            SELECT i.volume_id, i.id
            FROM issues i
            INNER JOIN volumes v ON v.id = i.volume_id
            WHERE v.comicvine_id = ?
                AND i.calculated_issue_number = ?
            LIMIT 1;
        """, (cv_volume_id, calculated)).fetchone()
        if exact:
            return exact['volume_id'], exact['id']

    candidates = cursor.execute("""
        SELECT
            i.id AS issue_id,
            i.volume_id,
            i.date,
            v.title AS volume_title,
            v.year AS volume_year
        FROM issues i
        INNER JOIN volumes v ON v.id = i.volume_id
        WHERE i.calculated_issue_number = ?;
    """, (calculated,)).fetchalldict()

    scored: List[Tuple[int, int, int]] = []
    for candidate in candidates:
        if not match_title(candidate['volume_title'], entry['series']):
            continue

        score = 1
        if (
            entry.get('volume_year') is not None
            and candidate['volume_year'] == entry['volume_year']
        ):
            score += 4

        date = candidate.get('date') or ''
        if (
            entry.get('issue_year') is not None
            and len(date) >= 4
            and date[:4].isdigit()
            and int(date[:4]) == entry['issue_year']
        ):
            score += 1

        scored.append((score, candidate['volume_id'], candidate['issue_id']))

    if not scored:
        return None, None

    scored.sort(reverse=True)
    best_score = scored[0][0]
    winners = [candidate for candidate in scored if candidate[0] == best_score]
    if len(winners) != 1:
        return None, None

    _, volume_id, issue_id = winners[0]
    return volume_id, issue_id


def _entry_from_issue(issue_id: int, position: int) -> Optional[Dict[str, Any]]:
    row = get_db().execute("""
        SELECT
            i.id AS issue_id,
            i.volume_id,
            i.comicvine_id AS comicvine_issue_id,
            i.issue_number,
            i.date,
            v.comicvine_id AS comicvine_volume_id,
            v.title AS series,
            v.year AS volume_year
        FROM issues i
        INNER JOIN volumes v ON v.id = i.volume_id
        WHERE i.id = ?
        LIMIT 1;
    """, (issue_id,)).fetchonedict()
    if not row:
        return None

    date = row.pop('date') or ''
    row['issue_year'] = int(date[:4]) if len(date) >= 4 and date[:4].isdigit() else None
    row['position'] = position
    row['filename'] = None
    return row


def create_reading_list(
    title: str,
    entries: Iterable[Dict[str, Any]],
    source: str = 'manual',
    source_name: Optional[str] = None
) -> int:
    ensure_reading_list_tables()
    title = (title or '').strip()
    if not title:
        raise ValueError('Reading list title is required')

    cursor = get_db()
    cursor.execute("""
        INSERT INTO reading_lists(title, source, source_name, created_at)
        VALUES (?, ?, ?, ?);
    """, (title[:255], source[:30], source_name, round(time())))
    reading_list_id = cursor.lastrowid

    rows = []
    for entry in entries:
        volume_id = entry.get('volume_id')
        issue_id = entry.get('issue_id')
        if issue_id is None:
            volume_id, issue_id = _resolve_entry(entry)

        rows.append({
            **entry,
            'reading_list_id': reading_list_id,
            'volume_id': volume_id,
            'issue_id': issue_id
        })

    cursor.executemany("""
        INSERT INTO reading_list_entries(
            reading_list_id, position, series, issue_number,
            volume_year, issue_year, filename,
            comicvine_volume_id, comicvine_issue_id,
            volume_id, issue_id
        ) VALUES (
            :reading_list_id, :position, :series, :issue_number,
            :volume_year, :issue_year, :filename,
            :comicvine_volume_id, :comicvine_issue_id,
            :volume_id, :issue_id
        );
    """, rows)
    return reading_list_id


def import_cbl(content: bytes, source_name: Optional[str] = None) -> Dict[str, Any]:
    title, entries = parse_cbl(content)
    reading_list_id = create_reading_list(
        title, entries, source='cbl', source_name=source_name
    )
    result = get_reading_list(reading_list_id)
    if result is None:
        raise RuntimeError('Reading list disappeared after creation')
    return result


def create_manual_reading_list(title: str, issue_ids: Iterable[int]) -> Dict[str, Any]:
    entries = []
    for position, issue_id in enumerate(issue_ids, 1):
        entry = _entry_from_issue(issue_id, position)
        if entry is not None:
            entries.append(entry)

    reading_list_id = create_reading_list(title, entries)
    result = get_reading_list(reading_list_id)
    if result is None:
        raise RuntimeError('Reading list disappeared after creation')
    return result


def _summary_row(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        **row,
        'entry_count': row['entry_count'] or 0,
        'owned_count': row['owned_count'] or 0,
        'missing_count': row['missing_count'] or 0,
        'unresolved_count': row['unresolved_count'] or 0
    }


def get_reading_lists() -> List[Dict[str, Any]]:
    ensure_reading_list_tables()
    rows = get_db().execute("""
        SELECT
            l.id, l.title, l.source, l.source_name, l.created_at,
            COUNT(e.id) AS entry_count,
            SUM(CASE WHEN e.issue_id IS NOT NULL AND EXISTS(
                SELECT 1 FROM issues_files f WHERE f.issue_id = e.issue_id
            ) THEN 1 ELSE 0 END) AS owned_count,
            SUM(CASE WHEN e.issue_id IS NOT NULL AND NOT EXISTS(
                SELECT 1 FROM issues_files f WHERE f.issue_id = e.issue_id
            ) THEN 1 ELSE 0 END) AS missing_count,
            SUM(CASE WHEN e.issue_id IS NULL THEN 1 ELSE 0 END) AS unresolved_count
        FROM reading_lists l
        LEFT JOIN reading_list_entries e ON e.reading_list_id = l.id
        GROUP BY l.id
        ORDER BY l.created_at DESC, l.id DESC;
    """).fetchalldict()
    return [_summary_row(row) for row in rows]


def get_reading_list(reading_list_id: int) -> Optional[Dict[str, Any]]:
    ensure_reading_list_tables()
    cursor = get_db()
    header = cursor.execute("""
        SELECT id, title, source, source_name, created_at
        FROM reading_lists
        WHERE id = ?
        LIMIT 1;
    """, (reading_list_id,)).fetchonedict()
    if header is None:
        return None

    entries = cursor.execute("""
        SELECT
            e.id, e.position, e.series, e.issue_number,
            e.volume_year, e.issue_year, e.filename,
            e.comicvine_volume_id, e.comicvine_issue_id,
            e.volume_id, e.issue_id,
            v.title AS matched_volume_title,
            CASE
                WHEN e.issue_id IS NULL THEN 'unresolved'
                WHEN EXISTS(
                    SELECT 1 FROM issues_files f WHERE f.issue_id = e.issue_id
                ) THEN 'owned'
                ELSE 'missing'
            END AS status
        FROM reading_list_entries e
        LEFT JOIN volumes v ON v.id = e.volume_id
        WHERE e.reading_list_id = ?
        ORDER BY e.position, e.id;
    """, (reading_list_id,)).fetchalldict()

    header['entries'] = entries
    header['entry_count'] = len(entries)
    header['owned_count'] = sum(e['status'] == 'owned' for e in entries)
    header['missing_count'] = sum(e['status'] == 'missing' for e in entries)
    header['unresolved_count'] = sum(e['status'] == 'unresolved' for e in entries)
    return header


def delete_reading_list(reading_list_id: int) -> bool:
    ensure_reading_list_tables()
    cursor = get_db()
    cursor.execute('DELETE FROM reading_lists WHERE id = ?;', (reading_list_id,))
    return cursor.rowcount > 0


def missing_reading_list_issues(reading_list_id: int) -> List[Tuple[int, int]]:
    ensure_reading_list_tables()
    return [
        (row['volume_id'], row['issue_id'])
        for row in get_db().execute("""
            SELECT DISTINCT e.volume_id, e.issue_id
            FROM reading_list_entries e
            WHERE e.reading_list_id = ?
                AND e.issue_id IS NOT NULL
                AND e.volume_id IS NOT NULL
                AND NOT EXISTS(
                    SELECT 1 FROM issues_files f WHERE f.issue_id = e.issue_id
                )
            ORDER BY e.position;
        """, (reading_list_id,)).fetchalldict()
    ]


def export_cbl(reading_list_id: int) -> Optional[bytes]:
    reading_list = get_reading_list(reading_list_id)
    if reading_list is None:
        return None

    root = ET.Element('ReadingList', {
        'xmlns:xsd': 'http://www.w3.org/2001/XMLSchema',
        'xmlns:xsi': 'http://www.w3.org/2001/XMLSchema-instance'
    })
    ET.SubElement(root, 'Name').text = reading_list['title']
    ET.SubElement(root, 'NumIssues').text = str(reading_list['entry_count'])
    books = ET.SubElement(root, 'Books')

    for entry in reading_list['entries']:
        attrs = {
            'Series': entry['series'],
            'Number': entry['issue_number']
        }
        if entry['volume_year'] is not None:
            attrs['Volume'] = str(entry['volume_year'])
        if entry['issue_year'] is not None:
            attrs['Year'] = str(entry['issue_year'])
        if entry['filename']:
            attrs['FileName'] = entry['filename']

        book = ET.SubElement(books, 'Book', attrs)
        cv_volume_id = entry['comicvine_volume_id']
        cv_issue_id = entry['comicvine_issue_id']
        if cv_volume_id or cv_issue_id:
            database_attrs = {'Name': 'cv'}
            if cv_volume_id:
                database_attrs['Series'] = str(cv_volume_id)
            if cv_issue_id:
                database_attrs['Issue'] = str(cv_issue_id)
            ET.SubElement(book, 'Database', database_attrs)

    ET.SubElement(root, 'Matchers')
    return ET.tostring(root, encoding='utf-8', xml_declaration=True)
