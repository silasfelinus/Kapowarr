# -*- coding: utf-8 -*-

"""User-facing acquisition preference policy.

These settings are intentionally separate from matching correctness. They are used
only after a release is a plausible match, so a preferred protocol/pack/quality
cannot promote unrelated comics above a better match.
"""

from json import dumps, loads
from sqlite3 import OperationalError
from re import IGNORECASE, compile
from typing import Any, Dict, List, Mapping, Optional, Sequence

from flask import has_app_context

from backend.base.custom_exceptions import InvalidKeyValue, KeyNotFound
from backend.base.definitions import DownloadGroup, DownloadType
from backend.internals.db import commit, get_db

SOURCE_PREFERENCE_OPTIONS = ('direct', 'torrent', 'usenet')
DEFAULT_SOURCE_PREFERENCE = SOURCE_PREFERENCE_OPTIONS

# What the source preference is allowed to decide.
#
# 'ranking' is what it has always done: every protocol is searched at once
# and the preference breaks ties between otherwise-equal results. That is
# the right default -- it finds the most, fastest -- and it is the wrong
# behaviour for anyone whose protocols are not equally cheap. Silas has
# three pro Usenet accounts allowing around ten thousand queries a day
# between them, and three public torrent indexers allowing a hundred; under
# 'ranking' every search spends the scarce quota whether the plentiful one
# had the issue or not.
#
# 'first_match' lets the preference decide whether a protocol is asked at
# all: protocols are searched in preference order, and the first one to
# return a result that actually matches the volume ends the search. Silas,
# 2026-09-01: "I would rather miss something on the first go around, and
# then pick them up later as we drip torrent checks, then delays
# everything."
SEARCH_STRATEGY_OPTIONS = ('ranking', 'first_match')
DEFAULT_SEARCH_STRATEGY = 'ranking'

# Minimum seconds between two requests to the same indexer.
#
# Query builders produce several phrasings and the coordinator runs sources
# concurrently, so without a gate one endpoint receives a burst. Prowlarr
# relays that burst into the indexer's own limiter, which answers 429 -- and
# a 429 is a quota, so the burst costs more than it buys.
#
# One second was hardcoded for Torznab and nothing paced Newznab. Both are
# now settings, at those same defaults, because how fast an indexer wants to
# be asked is a property of the indexer and not of Kapowarr. A public
# tracker behind Prowlarr may want fifteen or thirty seconds; three pro
# Usenet accounts want no delay at all.
DEFAULT_TORRENT_SEARCH_DELAY = 1.0
DEFAULT_USENET_SEARCH_DELAY = 0.0
MAX_SEARCH_DELAY = 300.0
QUALITY_PREFERENCE_OPTIONS = ('any', 'hd', 'sd')
PACK_PREFERENCE_OPTIONS = ('neutral', 'prefer', 'avoid')
DEFAULT_INDEXER_PRIORITY = 50
DEFAULT_CLIENT_PRIORITY = 50

_DEFAULTS = {
    'acquisition_source_preference': dumps(DEFAULT_SOURCE_PREFERENCE),
    'getcomics_quality_preference': 'any',
    'pack_preference': 'neutral',
    'indexer_priorities': dumps({}),
    'client_priorities': dumps({}),
    'acquisition_search_strategy': DEFAULT_SEARCH_STRATEGY,
    'torrent_search_delay_seconds': DEFAULT_TORRENT_SEARCH_DELAY,
    'usenet_search_delay_seconds': DEFAULT_USENET_SEARCH_DELAY
}

_DOWNLOAD_TYPE_NAMES = {
    DownloadType.DIRECT: 'direct',
    DownloadType.TORRENT: 'torrent',
    DownloadType.USENET: 'usenet'
}

_HD_RE = compile(r'(^|[^a-z0-9])hd([^a-z0-9]|$)', IGNORECASE)
_SD_RE = compile(r'(^|[^a-z0-9])sd([^a-z0-9]|$)', IGNORECASE)


def _validated_source_preference(value: Any) -> List[str]:
    if not isinstance(value, (list, tuple)):
        raise InvalidKeyValue('acquisition_source_preference', value)
    result = list(value)
    if (
        len(result) != len(SOURCE_PREFERENCE_OPTIONS)
        or set(result) != set(SOURCE_PREFERENCE_OPTIONS)
    ):
        raise InvalidKeyValue('acquisition_source_preference', value)
    return result


def _validated_priority_map(value: Any) -> Dict[str, int]:
    if not isinstance(value, dict):
        raise InvalidKeyValue('indexer_priorities', value)

    result: Dict[str, int] = {}
    for key, priority in value.items():
        if not isinstance(key, str) or not key.startswith(('newznab:', 'torznab:')):
            raise InvalidKeyValue('indexer_priorities', value)
        if isinstance(priority, bool) or not isinstance(priority, int):
            raise InvalidKeyValue('indexer_priorities', value)
        if not 1 <= priority <= 100:
            raise InvalidKeyValue('indexer_priorities', value)
        result[key] = priority
    return result


def _validated_client_priority_map(value: Any) -> Dict[str, int]:
    if not isinstance(value, dict):
        raise InvalidKeyValue('client_priorities', value)

    result: Dict[str, int] = {}
    for key, priority in value.items():
        if not isinstance(key, str) or not key.isdigit() or int(key) < 1:
            raise InvalidKeyValue('client_priorities', value)
        if isinstance(priority, bool) or not isinstance(priority, int):
            raise InvalidKeyValue('client_priorities', value)
        if not 1 <= priority <= 100:
            raise InvalidKeyValue('client_priorities', value)
        result[key] = priority
    return result


def get_acquisition_preferences() -> Dict[str, Any]:
    """Return the current acquisition policy.

    Read-only. This used to seed the config table with the defaults first,
    which made a write out of a read on a path that runs per search result
    (`file_quality` consults the source preference for every candidate it
    ranks). A write there contends with whatever long job holds the SQLite
    writer, and losing that race raises "database is locked" out through
    the search and ends the task -- which is exactly how `Search All` died
    on 2026-08-31, in the sibling of this function.

    Nothing needed the rows to exist: every value below falls back to its
    default when the key is absent, and the settings endpoint writes
    explicitly when one actually changes.
    """
    if not has_app_context():
        # Background tasks and parser tests reach the search path without
        # Flask's g-backed cursor. Defaults are the right policy there, and
        # every value below already falls back to one -- the same trade
        # `grab_size_limits._read_limits` makes for the same reason.
        rows: Dict[str, Any] = {}
        return _preferences_from(rows)

    try:
        rows = dict(get_db().execute(
            """SELECT key, value FROM config
            WHERE key IN (
                'acquisition_source_preference',
                'acquisition_search_strategy',
                'torrent_search_delay_seconds',
                'usenet_search_delay_seconds',
                'getcomics_quality_preference',
                'pack_preference',
                'indexer_priorities',
                'client_priorities'
            );"""
        ).fetchall())
    except OperationalError:
        rows = {}

    return _preferences_from(rows)


def _as_delay(value: Any) -> Optional[float]:
    """A usable delay, or None. Never raises: this runs on every read."""
    if isinstance(value, bool):
        return None
    try:
        delay = float(value)
    except (TypeError, ValueError):
        return None
    if not 0 <= delay <= MAX_SEARCH_DELAY:
        return None
    return delay


def _validated_delay(key: str, value: Any) -> float:
    delay = _as_delay(value)
    if delay is None:
        # Constructed only when a caller really did send something wrong;
        # `InvalidKeyValue` logs, and a missing config row is not a warning.
        raise InvalidKeyValue(key, value)
    return delay


def _delay_or_default(value: Any, default: float) -> float:
    delay = _as_delay(value)
    return default if delay is None else delay


def _preferences_from(rows: Mapping[str, Any]) -> Dict[str, Any]:
    """Turn whatever config rows came back into a complete policy."""

    try:
        source_preference = _validated_source_preference(
            loads(rows['acquisition_source_preference'])
        )
    except (KeyError, TypeError, ValueError, InvalidKeyValue):
        source_preference = list(DEFAULT_SOURCE_PREFERENCE)

    quality = rows.get('getcomics_quality_preference', 'any')
    if quality not in QUALITY_PREFERENCE_OPTIONS:
        quality = 'any'

    pack = rows.get('pack_preference', 'neutral')
    if pack not in PACK_PREFERENCE_OPTIONS:
        pack = 'neutral'

    try:
        indexer_priorities = _validated_priority_map(
            loads(rows.get('indexer_priorities', '{}'))
        )
    except (TypeError, ValueError, InvalidKeyValue):
        indexer_priorities = {}

    try:
        client_priorities = _validated_client_priority_map(
            loads(rows.get('client_priorities', '{}'))
        )
    except (TypeError, ValueError, InvalidKeyValue):
        client_priorities = {}

    strategy = rows.get(
        'acquisition_search_strategy', DEFAULT_SEARCH_STRATEGY
    )
    if strategy not in SEARCH_STRATEGY_OPTIONS:
        strategy = DEFAULT_SEARCH_STRATEGY

    return {
        'acquisition_source_preference': source_preference,
        'acquisition_search_strategy': strategy,
        'torrent_search_delay_seconds': _delay_or_default(
            rows.get('torrent_search_delay_seconds'),
            DEFAULT_TORRENT_SEARCH_DELAY
        ),
        'usenet_search_delay_seconds': _delay_or_default(
            rows.get('usenet_search_delay_seconds'),
            DEFAULT_USENET_SEARCH_DELAY
        ),
        'getcomics_quality_preference': quality,
        'pack_preference': pack,
        'indexer_priorities': indexer_priorities,
        'client_priorities': client_priorities
    }


def update_acquisition_preferences(data: Mapping[str, Any]) -> Dict[str, Any]:
    """Validate and persist any supplied acquisition preference fields."""
    allowed = set(_DEFAULTS)
    for key in data:
        if key not in allowed:
            raise KeyNotFound(key)

    updates: Dict[str, str] = {}
    if 'acquisition_source_preference' in data:
        updates['acquisition_source_preference'] = dumps(
            _validated_source_preference(data['acquisition_source_preference'])
        )

    if 'acquisition_search_strategy' in data:
        value = data['acquisition_search_strategy']
        if value not in SEARCH_STRATEGY_OPTIONS:
            raise InvalidKeyValue('acquisition_search_strategy', value)
        updates['acquisition_search_strategy'] = value

    for key in ('torrent_search_delay_seconds', 'usenet_search_delay_seconds'):
        if key in data:
            updates[key] = str(_validated_delay(key, data[key]))

    if 'getcomics_quality_preference' in data:
        value = data['getcomics_quality_preference']
        if value not in QUALITY_PREFERENCE_OPTIONS:
            raise InvalidKeyValue('getcomics_quality_preference', value)
        updates['getcomics_quality_preference'] = value

    if 'pack_preference' in data:
        value = data['pack_preference']
        if value not in PACK_PREFERENCE_OPTIONS:
            raise InvalidKeyValue('pack_preference', value)
        updates['pack_preference'] = value

    if 'indexer_priorities' in data:
        updates['indexer_priorities'] = dumps(
            _validated_priority_map(data['indexer_priorities'])
        )

    if 'client_priorities' in data:
        updates['client_priorities'] = dumps(
            _validated_client_priority_map(data['client_priorities'])
        )

    if updates:
        get_db().executemany(
            'INSERT OR REPLACE INTO config(key, value) VALUES (?, ?);',
            updates.items()
        )
        commit()

    return get_acquisition_preferences()


def search_delay(protocol: str) -> float:
    """Minimum seconds between two requests to the same `protocol` indexer."""
    key = (
        'torrent_search_delay_seconds' if protocol == 'torrent'
        else 'usenet_search_delay_seconds'
    )
    return get_acquisition_preferences()[key]


def search_stops_at_first_match() -> bool:
    """Whether the source preference decides who gets asked, not just who wins.

    Under 'ranking' every protocol is searched at once and the preference
    only breaks ties. That finds the most and finds it fastest, and it
    assumes every protocol costs the same to ask -- which stops being true
    the moment one of them is metered far more tightly than the others.
    """
    return (
        get_acquisition_preferences()['acquisition_search_strategy']
        == 'first_match'
    )


def ordered_download_types(
    download_types: Sequence[DownloadType]
) -> List[DownloadType]:
    """Order active protocols by the configured source preference."""
    preference = get_acquisition_preferences()['acquisition_source_preference']
    positions = {name: index for index, name in enumerate(preference)}
    return sorted(
        download_types,
        key=lambda download_type: positions.get(
            _DOWNLOAD_TYPE_NAMES[download_type], len(positions)
        )
    )


def indexer_priority(protocol: str, indexer_id: int) -> int:
    """Return configured indexer priority (1 is highest, 50 is default)."""
    key = f'{protocol}:{indexer_id}'
    return get_acquisition_preferences()['indexer_priorities'].get(
        key, DEFAULT_INDEXER_PRIORITY
    )


def client_priority(client_id: int) -> int:
    """Return configured external-client priority (1 highest, 50 default)."""
    return get_acquisition_preferences()['client_priorities'].get(
        str(client_id), DEFAULT_CLIENT_PRIORITY
    )


def remove_client_priority(client_id: int) -> None:
    """Drop stale priority metadata after deleting an external client."""
    preferences = get_acquisition_preferences()
    priorities = dict(preferences['client_priorities'])
    if priorities.pop(str(client_id), None) is None:
        return
    update_acquisition_preferences({'client_priorities': priorities})


def pack_preference_rank(issue_number: Any) -> int:
    """Return a small ranking component for range-pack preference.

    Lower is better. Neutral returns zero for every result, preserving the
    historical issue-fit ranking exactly.
    """
    preference = get_acquisition_preferences()['pack_preference']
    if preference == 'neutral' or issue_number is None:
        return 0

    is_pack = isinstance(issue_number, tuple)
    if preference == 'prefer':
        return 0 if is_pack else 1
    return 1 if is_pack else 0


def availability_rank(result: Mapping[str, Any]) -> int:
    """Return a small ranking component for known-dead peer availability.

    Lower is better. This is not a user preference: a release with zero
    seeders cannot be downloaded, so it belongs behind one that can.

    Absent data ranks neutrally, and that is the whole design constraint.
    ``SearchResultAvailabilityData`` is ``total=False`` and only Torznab
    populates it -- GetComics and Newznab results carry no peer counts at
    all. Scoring "unknown" as anything worse than "healthy" would quietly
    demote every non-torrent source, so only a result that explicitly
    reports zero seeders is demoted; missing, ``None`` and positive counts
    are all equally neutral.
    """
    seeders = result.get('seeders')
    if seeders is None:
        return 0
    return 1 if seeders <= 0 else 0


def getcomics_quality_label(title: str) -> str:
    """Classify an explicit GetComics group title as HD, SD, or unknown."""
    if _HD_RE.search(title):
        return 'hd'
    if _SD_RE.search(title):
        return 'sd'
    return 'unknown'


def getcomics_quality_rank(path: Sequence[DownloadGroup]) -> int:
    """Rank a GetComics path by explicit HD/SD labels.

    Unknown/unlabelled variants stay between the preferred and explicitly
    non-preferred quality. With ``any`` this returns zero and leaves historical
    ordering untouched.
    """
    preference = get_acquisition_preferences()['getcomics_quality_preference']
    if preference == 'any':
        return 0

    labels = [getcomics_quality_label(group['web_sub_title']) for group in path]
    if preference in labels:
        return 0
    if all(label == 'unknown' for label in labels):
        return 1
    return 2


def order_getcomics_groups(groups: Sequence[DownloadGroup]) -> List[DownloadGroup]:
    """Stable-order GetComics variants by explicit HD/SD preference.

    The later path builder still owns match/range correctness. This only moves an
    explicitly preferred quality ahead of an otherwise-equivalent variant; with
    ``any`` every key is zero, preserving the page's original order.
    """
    return sorted(groups, key=lambda group: getcomics_quality_rank((group,)))
