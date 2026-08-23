# -*- coding: utf-8 -*-

"""Configurable size limits for acquisition results.

Indexer feeds can report the completed release size before Kapowarr queues a
download. Keep those limits in the existing config table so they can be
changed without a schema migration. A limit of zero disables that side of
the range; unknown-size results stay eligible rather than silently penalising
sources that cannot report a size.
"""

from typing import Any, Dict, List, Mapping

from backend.base.custom_exceptions import InvalidKeyValue, KeyNotFound
from backend.internals.db import commit, get_db

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


def _ensure_defaults() -> None:
    get_db().executemany(
        'INSERT OR IGNORE INTO config(key, value) VALUES (?, ?);',
        _DEFAULTS.items()
    )
    commit()


def _validated_limit(key: str, value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise InvalidKeyValue(key, value)
    return value


def get_grab_size_limits() -> Dict[str, int]:
    """Return minimum/maximum grab sizes in MiB, inserting defaults lazily."""
    _ensure_defaults()
    rows = dict(get_db().execute(
        """SELECT key, value FROM config
        WHERE key IN ('minimum_grab_size_mb', 'maximum_grab_size_mb');"""
    ).fetchall())

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
