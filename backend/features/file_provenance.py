# -*- coding: utf-8 -*-

"""Durable acquisition provenance for library files.

This deliberately stores comparison-friendly source context without persisting
raw download links, magnets, or NZB URLs that may contain credentials/tokens.
"""

from __future__ import annotations

from os import stat
from os.path import isfile
from time import time
from typing import Any, Dict, Iterable, List, Optional

from backend.base.logging import LOGGER
from backend.internals.db import get_db


PROVENANCE_SCHEMA = """
CREATE TABLE IF NOT EXISTS file_provenance(
    file_id INTEGER PRIMARY KEY,
    source_type VARCHAR(30) NOT NULL,
    source_name VARCHAR(255) NOT NULL,
    release_title TEXT,
    web_title TEXT,
    web_sub_title TEXT,
    acquired_at INTEGER NOT NULL CHECK (acquired_at > 0),

    FOREIGN KEY (file_id) REFERENCES files(id)
        ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS file_provenance_source_index
    ON file_provenance(source_type, source_name);
"""


def ensure_file_provenance_table() -> None:
    """Create additive provenance storage on old and new databases."""
    get_db().executescript(PROVENANCE_SCHEMA)


def _source_value(download: Any) -> str:
    source_type = getattr(download, 'source_type', None)
    return str(getattr(source_type, 'value', source_type) or 'Unknown')


def record_download_file_provenance(download: Any) -> int:
    """Attach a successful download's source context to surviving library files.

    This runs after file matching/conversion. Temporary or converted-away paths
    are ignored; the files currently registered in Kapowarr receive the record.
    Replacing an existing library path updates both provenance and its stored
    size to describe the newly imported file.
    """
    ensure_file_provenance_table()
    filepaths = list(dict.fromkeys(
        filepath
        for filepath in getattr(download, 'files', [])
        if isinstance(filepath, str) and isfile(filepath)
    ))
    if not filepaths:
        return 0

    placeholders = ','.join('?' for _ in filepaths)
    rows = get_db().execute(
        f'SELECT id, filepath FROM files WHERE filepath IN ({placeholders});',
        tuple(filepaths),
    ).fetchalldict()
    if not rows:
        return 0

    acquired_at = round(time())
    source_type = _source_value(download)
    source_name = str(getattr(download, 'source_name', '') or source_type)
    release_title = str(getattr(download, 'title', '') or '') or None
    web_title = getattr(download, 'web_title', None)
    web_sub_title = getattr(download, 'web_sub_title', None)

    cursor = get_db()
    recorded = 0
    for row in rows:
        filepath = row['filepath']
        try:
            size = stat(filepath).st_size
        except OSError:
            continue

        # The recorded size and the provenance row describe the same
        # newly imported file, so they land together or not at all.
        with cursor:
            cursor.execute(
                'UPDATE files SET size = ? WHERE id = ?;',
                (size, row['id']),
            )
            cursor.execute("""
                INSERT INTO file_provenance(
                    file_id, source_type, source_name,
                    release_title, web_title, web_sub_title, acquired_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(file_id) DO UPDATE SET
                    source_type = excluded.source_type,
                    source_name = excluded.source_name,
                    release_title = excluded.release_title,
                    web_title = excluded.web_title,
                    web_sub_title = excluded.web_sub_title,
                    acquired_at = excluded.acquired_at;
            """, (
                row['id'],
                source_type,
                source_name,
                release_title,
                web_title,
                web_sub_title,
                acquired_at,
            ))
        recorded += 1

    if recorded:
        LOGGER.debug(
            'Recorded acquisition provenance for %d library file(s) from %s / %s',
            recorded,
            source_type,
            source_name,
        )
    return recorded


def get_file_provenance(file_id: int) -> Optional[Dict[str, Any]]:
    ensure_file_provenance_table()
    return get_db().execute("""
        SELECT
            p.file_id,
            p.source_type,
            p.source_name,
            p.release_title,
            p.web_title,
            p.web_sub_title,
            p.acquired_at
        FROM file_provenance p
        WHERE p.file_id = ?
        LIMIT 1;
    """, (file_id,)).fetchonedict()


def get_provenance_by_filepaths(
    filepaths: Iterable[str],
) -> Dict[str, Dict[str, Any]]:
    """Return provenance keyed by filepath for an existing file list."""
    ensure_file_provenance_table()
    unique = list(dict.fromkeys(filepaths))
    if not unique:
        return {}

    placeholders = ','.join('?' for _ in unique)
    rows: List[Dict[str, Any]] = get_db().execute(f"""
        SELECT
            f.filepath,
            p.file_id,
            p.source_type,
            p.source_name,
            p.release_title,
            p.web_title,
            p.web_sub_title,
            p.acquired_at
        FROM files f
        INNER JOIN file_provenance p ON p.file_id = f.id
        WHERE f.filepath IN ({placeholders});
    """, tuple(unique)).fetchalldict()
    return {
        row.pop('filepath'): row
        for row in rows
    }


def get_volume_file_provenance(volume_id: int) -> List[Dict[str, Any]]:
    """Return every file in a volume with nullable acquisition provenance."""
    ensure_file_provenance_table()
    return get_db().execute("""
        SELECT DISTINCT
            f.id AS file_id,
            f.filepath,
            f.size,
            p.source_type,
            p.source_name,
            p.release_title,
            p.web_title,
            p.web_sub_title,
            p.acquired_at
        FROM files f
        LEFT JOIN file_provenance p ON p.file_id = f.id
        WHERE f.id IN (
            SELECT if.file_id
            FROM issues_files if
            INNER JOIN issues i ON i.id = if.issue_id
            WHERE i.volume_id = ?

            UNION

            SELECT vf.file_id
            FROM volume_files vf
            WHERE vf.volume_id = ?
        )
        ORDER BY f.filepath;
    """, (volume_id, volume_id)).fetchalldict()
