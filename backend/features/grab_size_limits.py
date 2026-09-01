# -*- coding: utf-8 -*-

"""Configurable size limits for acquisition results.

Indexer feeds can report the completed release size before Kapowarr queues a
download. Keep those limits in the existing config table so they can be
changed without a schema migration. A limit of zero disables that side of
the range; unknown-size results stay eligible rather than silently penalising
sources that cannot report a size.
"""

from sqlite3 import OperationalError, connect
from typing import Any, Dict, List, Mapping

from flask import has_app_context

from backend.base.custom_exceptions import InvalidKeyValue, KeyNotFound
from backend.internals.db import DBConnection, commit, get_db

MEBIBYTE = 1024 * 1024
DEFAULT_MINIMUM_GRAB_SIZE_MB = 1
DEFAULT_MAXIMUM_GRAB_SIZE_MB = 300

GRAB_SIZE_KEYS = (
    'minimum_grab_size_mb',
    'maximum_grab_size_mb'
)

_DEFAULTS = {
    'minimum_grab_size_mb': DEFAULT_MINIMUM_GRAB_SIZE_MB,
    'maximum_grab_size_mb': DEFAULT_MAXIMUM_GRAB_SIZE_MB
}


def _validated_limit(key: str, value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise InvalidKeyValue(key, value)
    return value


def _read_limits() -> Dict[str, Any]:
    """Read config without requiring a Flask request/application context.

    Search providers are also exercised by background tasks and pure parser
    tests, where Flask's ``g``-backed cursor cache is intentionally absent.
    Use Kapowarr's normal cursor when a context exists; otherwise do the tiny
    read through an independent SQLite connection.

    Read-only, in both branches and on purpose. This used to seed the config
    table with the default limits first, on the theory that a read may as
    well leave the row behind. That made a write out of a read on the
    hottest path there is -- `filter_search_results` is called for every
    indexer response, from inside `asyncio.gather` over every source -- and
    on 2026-08-31 it did what a write there eventually does: it collided
    with the library import holding the writer, raised "database is locked"
    through `Search All`, and ended the day's sweep after twenty-one
    volumes. Nothing needed the row: a missing key already means the
    default, and the settings endpoint writes explicitly when a value
    actually changes.
    """
    query = """SELECT key, value FROM config
        WHERE key IN ('minimum_grab_size_mb', 'maximum_grab_size_mb');"""

    try:
        if has_app_context():
            return dict(get_db().execute(query).fetchall())

        if not DBConnection.file:
            return {}

        with connect(DBConnection.file) as connection:
            return dict(connection.execute(query).fetchall())

    except OperationalError:
        # Missing rows, a database that does not exist yet, or one whose
        # writer is busy. Every one of those means "use the defaults", and
        # `get_grab_size_limits` supplies them for any key not returned.
        #
        # It has to mean that, because of where this is called from:
        # `filter_search_results` runs on every indexer response, inside
        # `asyncio.gather` over every source, inside `Search All`. Letting a
        # size-limit lookup raise there ends the whole nightly sweep.
        return {}


def get_grab_size_limits() -> Dict[str, int]:
    """Return minimum/maximum grab sizes in MiB."""
    rows = _read_limits()

    result: Dict[str, int] = {}
    for key, default in _DEFAULTS.items():
        try:
            value = int(rows.get(key, default))
            result[key] = _validated_limit(key, value)
        except (TypeError, ValueError, InvalidKeyValue):
            result[key] = default
    return result


def update_grab_size_limits(data: Mapping[str, Any]) -> Dict[str, int]:
    """Validate and persist supplied grab-size fields.

    Zero disables the corresponding limit. When both limits are enabled the
    minimum cannot exceed the maximum.
    """
    for key in data:
        if key not in GRAB_SIZE_KEYS:
            raise KeyNotFound(key)

    current = get_grab_size_limits()
    updated = dict(current)
    for key, value in data.items():
        updated[key] = _validated_limit(key, value)

    minimum = updated['minimum_grab_size_mb']
    maximum = updated['maximum_grab_size_mb']
    if minimum and maximum and minimum > maximum:
        raise InvalidKeyValue('maximum_grab_size_mb', maximum)

    if data:
        get_db().executemany(
            'INSERT OR REPLACE INTO config(key, value) VALUES (?, ?);',
            ((key, updated[key]) for key in data)
        )
        commit()

    return updated


def filter_search_results(results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Drop known-size results outside the configured range.

    Results without trustworthy size metadata stay eligible. This keeps
    GetComics and any minimal Newznab/Torznab feed working normally while
    applying the filter whenever an indexer actually reports bytes.
    """
    limits = get_grab_size_limits()
    minimum = limits['minimum_grab_size_mb'] * MEBIBYTE
    maximum_mb = limits['maximum_grab_size_mb']
    maximum = maximum_mb * MEBIBYTE if maximum_mb else 0

    filtered: List[Dict[str, Any]] = []
    for result in results:
        size = result.get('size')
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            filtered.append(result)
            continue
        if minimum and size < minimum:
            continue
        if maximum and size > maximum:
            continue
        filtered.append(result)

    return filtered
