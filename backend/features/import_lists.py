# -*- coding: utf-8 -*-

"""Comic-native Import Lists.

The first provider is a remote ComicRack CBL document. CBL entries with exact
ComicVine volume IDs can safely drive automatic library additions without fuzzy
metadata searches. Entries without stable volume IDs remain unresolved and are
reported, never guessed.
"""

from __future__ import annotations

from time import monotonic, sleep, time
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urlsplit

from backend.base.custom_exceptions import VolumeAlreadyAdded
from backend.base.definitions import MonitorScheme
from backend.base.helpers import Session
from backend.base.logging import LOGGER
from backend.features.reading_lists import MAX_CBL_BYTES, parse_cbl
from backend.implementations.root_folders import RootFolders
from backend.implementations.volumes import Library
from backend.internals.db import commit, get_db


IMPORT_LIST_PROVIDER_REMOTE_CBL = 'remote_cbl'
IMPORT_LIST_CV_RESOURCE_DELAY = 30.0
IMPORT_LIST_SYNC_INTERVAL_SECONDS = 12 * 60 * 60


def ensure_import_list_tables() -> None:
    """Create additive Import List state idempotently."""
    get_db().executescript("""
        CREATE TABLE IF NOT EXISTS import_lists(
            id INTEGER PRIMARY KEY,
            name VARCHAR(255) NOT NULL,
            provider VARCHAR(30) NOT NULL,
            source_url TEXT NOT NULL,
            enabled BOOL NOT NULL DEFAULT 1,
            enable_auto BOOL NOT NULL DEFAULT 0,
            root_folder_id INTEGER NOT NULL,
            monitored BOOL NOT NULL DEFAULT 1,
            monitor_new_issues BOOL NOT NULL DEFAULT 1,
            search_on_add BOOL NOT NULL DEFAULT 0,
            last_sync INTEGER,
            last_error TEXT,
            last_item_count INTEGER NOT NULL DEFAULT 0,
            last_exact_volume_count INTEGER NOT NULL DEFAULT 0,
            last_unresolved_count INTEGER NOT NULL DEFAULT 0,
            last_added_count INTEGER NOT NULL DEFAULT 0,

            FOREIGN KEY (root_folder_id) REFERENCES root_folders(id)
                ON DELETE RESTRICT
        );
        CREATE TABLE IF NOT EXISTS import_list_exclusions(
            comicvine_volume_id INTEGER PRIMARY KEY,
            note TEXT,
            added_at INTEGER NOT NULL
        );
    """)


def _validate_source_url(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError('Import List URL is required')
    value = value.strip()
    parsed = urlsplit(value)
    if parsed.scheme not in ('http', 'https') or not parsed.netloc:
        raise ValueError('Import List URL must be http or https')
    return value


def _validate_bool(data: Dict[str, Any], key: str, default: bool) -> bool:
    value = data.get(key, default)
    if not isinstance(value, bool):
        raise ValueError(f'{key} must be true or false')
    return value


def _validate_root_folder_id(value: Any) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError('A root folder is required')
    RootFolders().get_one(value)
    return value


def _normalize_import_list(
    data: Dict[str, Any],
    current: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    current = current or {}
    name = data.get('name', current.get('name'))
    if not isinstance(name, str) or not name.strip():
        raise ValueError('Import List name is required')

    provider = data.get(
        'provider',
        current.get('provider', IMPORT_LIST_PROVIDER_REMOTE_CBL),
    )
    if provider != IMPORT_LIST_PROVIDER_REMOTE_CBL:
        raise ValueError('Unsupported Import List provider')

    source_url = _validate_source_url(
        data.get('source_url', current.get('source_url'))
    )
    root_folder_id = _validate_root_folder_id(
        data.get('root_folder_id', current.get('root_folder_id'))
    )

    return {
        'name': name.strip()[:255],
        'provider': provider,
        'source_url': source_url,
        'enabled': _validate_bool(
            data, 'enabled', bool(current.get('enabled', True))
        ),
        'enable_auto': _validate_bool(
            data, 'enable_auto', bool(current.get('enable_auto', False))
        ),
        'root_folder_id': root_folder_id,
        'monitored': _validate_bool(
            data, 'monitored', bool(current.get('monitored', True))
        ),
        'monitor_new_issues': _validate_bool(
            data,
            'monitor_new_issues',
            bool(current.get('monitor_new_issues', True)),
        ),
        'search_on_add': _validate_bool(
            data,
            'search_on_add',
            bool(current.get('search_on_add', False)),
        ),
    }


def get_import_lists() -> List[Dict[str, Any]]:
    ensure_import_list_tables()
    return get_db().execute("""
        SELECT
            l.*,
            rf.folder AS root_folder_path
        FROM import_lists l
        INNER JOIN root_folders rf ON rf.id = l.root_folder_id
        ORDER BY LOWER(l.name), l.id;
    """).fetchalldict()


def get_import_list(import_list_id: int) -> Optional[Dict[str, Any]]:
    ensure_import_list_tables()
    return get_db().execute("""
        SELECT
            l.*,
            rf.folder AS root_folder_path
        FROM import_lists l
        INNER JOIN root_folders rf ON rf.id = l.root_folder_id
        WHERE l.id = ?
        LIMIT 1;
    """, (import_list_id,)).fetchonedict()


def create_import_list(data: Dict[str, Any]) -> Dict[str, Any]:
    ensure_import_list_tables()
    normalized = _normalize_import_list(data)
    cursor = get_db()
    cursor.execute("""
        INSERT INTO import_lists(
            name, provider, source_url, enabled, enable_auto,
            root_folder_id, monitored, monitor_new_issues, search_on_add
        ) VALUES (
            :name, :provider, :source_url, :enabled, :enable_auto,
            :root_folder_id, :monitored, :monitor_new_issues, :search_on_add
        );
    """, normalized)
    import_list_id = cursor.lastrowid
    commit()
    result = get_import_list(import_list_id)
    if result is None:
        raise RuntimeError('Import List disappeared after creation')
    return result


def update_import_list(
    import_list_id: int,
    data: Dict[str, Any],
) -> Dict[str, Any]:
    current = get_import_list(import_list_id)
    if current is None:
        raise KeyError(import_list_id)
    normalized = _normalize_import_list(data, current)
    get_db().execute("""
        UPDATE import_lists
        SET
            name = :name,
            provider = :provider,
            source_url = :source_url,
            enabled = :enabled,
            enable_auto = :enable_auto,
            root_folder_id = :root_folder_id,
            monitored = :monitored,
            monitor_new_issues = :monitor_new_issues,
            search_on_add = :search_on_add
        WHERE id = :id;
    """, {**normalized, 'id': import_list_id})
    commit()
    result = get_import_list(import_list_id)
    if result is None:
        raise RuntimeError('Import List disappeared after update')
    return result


def delete_import_list(import_list_id: int) -> bool:
    ensure_import_list_tables()
    cursor = get_db()
    cursor.execute(
        'DELETE FROM import_lists WHERE id = ?;',
        (import_list_id,),
    )
    deleted = cursor.rowcount > 0
    commit()
    return deleted


def get_import_list_exclusions() -> List[Dict[str, Any]]:
    ensure_import_list_tables()
    return get_db().execute("""
        SELECT comicvine_volume_id, note, added_at
        FROM import_list_exclusions
        ORDER BY added_at DESC, comicvine_volume_id;
    """).fetchalldict()


def add_import_list_exclusion(
    comicvine_volume_id: int,
    note: str = '',
) -> Dict[str, Any]:
    ensure_import_list_tables()
    if (
        not isinstance(comicvine_volume_id, int)
        or isinstance(comicvine_volume_id, bool)
        or comicvine_volume_id < 1
    ):
        raise ValueError('ComicVine volume ID must be a positive integer')
    if not isinstance(note, str):
        raise ValueError('Exclusion note must be text')
    get_db().execute("""
        INSERT INTO import_list_exclusions(comicvine_volume_id, note, added_at)
        VALUES (?, ?, ?)
        ON CONFLICT(comicvine_volume_id) DO UPDATE SET note = excluded.note;
    """, (comicvine_volume_id, note.strip()[:500], round(time())))
    commit()
    return next(
        exclusion for exclusion in get_import_list_exclusions()
        if exclusion['comicvine_volume_id'] == comicvine_volume_id
    )


def delete_import_list_exclusion(comicvine_volume_id: int) -> bool:
    ensure_import_list_tables()
    cursor = get_db()
    cursor.execute(
        'DELETE FROM import_list_exclusions WHERE comicvine_volume_id = ?;',
        (comicvine_volume_id,),
    )
    deleted = cursor.rowcount > 0
    commit()
    return deleted


def _fetch_remote_cbl(source_url: str) -> bytes:
    with Session() as session:
        with session.get(source_url, stream=True) as response:
            response.raise_for_status()

            declared_size = response.headers.get('Content-Length')
            if (
                declared_size
                and declared_size.isdigit()
                and int(declared_size) > MAX_CBL_BYTES
            ):
                raise ValueError('Remote CBL is too large')

            chunks = []
            total = 0
            for chunk in response.iter_content(chunk_size=64 * 1024):
                if not chunk:
                    continue
                total += len(chunk)
                if total > MAX_CBL_BYTES:
                    raise ValueError('Remote CBL is too large')
                chunks.append(chunk)
            return b''.join(chunks)


def _wait_for_metadata_slot(
    request_clock: Dict[str, float],
    should_stop: Callable[[], bool],
) -> bool:
    last_started = request_clock.get('last_metadata_started')
    if last_started is not None:
        remaining = max(
            IMPORT_LIST_CV_RESOURCE_DELAY - (monotonic() - last_started),
            0.0,
        )
        while remaining > 0:
            if should_stop():
                return False
            sleep(min(1.0, remaining))
            remaining = max(
                IMPORT_LIST_CV_RESOURCE_DELAY - (monotonic() - last_started),
                0.0,
            )

    request_clock['last_metadata_started'] = monotonic()
    return True


def _record_sync(
    import_list_id: int,
    summary: Dict[str, Any],
    error: Optional[str],
) -> None:
    get_db().execute("""
        UPDATE import_lists
        SET
            last_sync = ?,
            last_error = ?,
            last_item_count = ?,
            last_exact_volume_count = ?,
            last_unresolved_count = ?,
            last_added_count = ?
        WHERE id = ?;
    """, (
        round(time()),
        error,
        summary.get('item_count', 0),
        summary.get('exact_volume_count', 0),
        summary.get('unresolved_count', 0),
        summary.get('added_count', 0),
        import_list_id,
    ))
    commit()


def sync_import_list(
    import_list_id: int,
    should_stop: Optional[Callable[[], bool]] = None,
    request_clock: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    """Fetch one list and optionally auto-add exact, non-excluded volumes."""
    should_stop = should_stop or (lambda: False)
    request_clock = request_clock if request_clock is not None else {}
    definition = get_import_list(import_list_id)
    if definition is None:
        raise KeyError(import_list_id)

    summary: Dict[str, Any] = {
        'import_list_id': import_list_id,
        'name': definition['name'],
        'item_count': 0,
        'exact_volume_count': 0,
        'unresolved_count': 0,
        'already_added_count': 0,
        'excluded_count': 0,
        'added_count': 0,
        'stopped': False,
    }

    try:
        content = _fetch_remote_cbl(definition['source_url'])
        _, entries = parse_cbl(content)
        summary['item_count'] = len(entries)

        exact_ids: List[int] = []
        seen = set()
        unresolved = 0
        for entry in entries:
            comicvine_id = entry.get('comicvine_volume_id')
            if not comicvine_id:
                unresolved += 1
                continue
            if comicvine_id not in seen:
                exact_ids.append(comicvine_id)
                seen.add(comicvine_id)

        summary['exact_volume_count'] = len(exact_ids)
        summary['unresolved_count'] = unresolved

        if not definition['enable_auto']:
            _record_sync(import_list_id, summary, None)
            return summary

        excluded = {
            row['comicvine_volume_id']
            for row in get_import_list_exclusions()
        }
        existing = {
            row['comicvine_id']
            for row in get_db().execute(
                'SELECT comicvine_id FROM volumes '
                'WHERE comicvine_id IS NOT NULL;'
            ).fetchalldict()
        }

        for comicvine_id in exact_ids:
            if should_stop():
                summary['stopped'] = True
                break
            if comicvine_id in excluded:
                summary['excluded_count'] += 1
                continue
            if comicvine_id in existing:
                summary['already_added_count'] += 1
                continue

            if not _wait_for_metadata_slot(request_clock, should_stop):
                summary['stopped'] = True
                break

            try:
                Library.add(
                    comicvine_id,
                    definition['root_folder_id'],
                    bool(definition['monitored']),
                    monitor_scheme=MonitorScheme.ALL,
                    monitor_new_issues=bool(
                        definition['monitor_new_issues']
                    ),
                    auto_search=bool(definition['search_on_add']),
                )
            except VolumeAlreadyAdded:
                summary['already_added_count'] += 1
                existing.add(comicvine_id)
                continue

            existing.add(comicvine_id)
            summary['added_count'] += 1

        _record_sync(import_list_id, summary, None)
        LOGGER.info(
            'Import List %s synced: %d CBL entries, %d exact volumes, '
            '%d added, %d unresolved',
            definition['name'],
            summary['item_count'],
            summary['exact_volume_count'],
            summary['added_count'],
            summary['unresolved_count'],
        )
        return summary

    except Exception as error:
        # Keep the last observed counts for diagnostics, then preserve the
        # original exception so Task History / Events record a real failure.
        _record_sync(
            import_list_id,
            summary,
            f'{type(error).__name__}: {error}',
        )
        raise


def sync_enabled_import_lists(
    should_stop: Optional[Callable[[], bool]] = None,
) -> List[Dict[str, Any]]:
    """Sync all enabled Import Lists in stable order with one pacing clock."""
    should_stop = should_stop or (lambda: False)
    request_clock: Dict[str, float] = {}
    summaries = []
    for definition in get_import_lists():
        if should_stop():
            break
        if not definition['enabled']:
            continue
        summaries.append(sync_import_list(
            definition['id'],
            should_stop,
            request_clock,
        ))
    return summaries
