# -*- coding: utf-8 -*-

"""Lightweight operational telemetry used by the System Events surface.

Kapowarr already persists task and download histories. This module deliberately
keeps only process-local counters for metadata-provider traffic so Events can
show what the current process is doing without creating a duplicate history
store.
"""

from __future__ import annotations

from threading import Lock
from time import time
from typing import Any, Dict


_TELEMETRY_LOCK = Lock()
_COMICVINE_STARTED_AT = round(time())
_COMICVINE_REQUESTS: Dict[str, Dict[str, Any]] = {}


def _comicvine_resource(url_path: str) -> str:
    resource = url_path.strip('/').split('/', 1)[0].strip().lower()
    return resource or 'root'


def _empty_resource(resource: str) -> Dict[str, Any]:
    return {
        'resource': resource,
        'requests': 0,
        'success': 0,
        'rate_limit': 0,
        'transport_error': 0,
        'response_error': 0,
        'not_found': 0,
        'invalid_key': 0,
        'other_error': 0,
        'last_request_at': None,
        'last_outcome': None,
        'last_outcome_at': None,
    }


def begin_comicvine_request(url_path: str) -> str:
    """Record one outgoing ComicVine request and return its resource key."""
    resource = _comicvine_resource(url_path)
    now = round(time())
    with _TELEMETRY_LOCK:
        entry = _COMICVINE_REQUESTS.setdefault(
            resource,
            _empty_resource(resource),
        )
        entry['requests'] += 1
        entry['last_request_at'] = now
    return resource


def finish_comicvine_request(resource: str, outcome: str) -> None:
    """Record how an already-counted ComicVine request completed."""
    now = round(time())
    with _TELEMETRY_LOCK:
        entry = _COMICVINE_REQUESTS.setdefault(
            resource,
            _empty_resource(resource),
        )
        if outcome not in entry or outcome in (
            'resource', 'requests', 'last_request_at',
            'last_outcome', 'last_outcome_at'
        ):
            outcome = 'other_error'
        entry[outcome] += 1
        entry['last_outcome'] = outcome
        entry['last_outcome_at'] = now


def get_comicvine_request_stats() -> Dict[str, Any]:
    """Return a JSON-safe snapshot of ComicVine traffic for this process."""
    with _TELEMETRY_LOCK:
        resources = [
            dict(entry)
            for _, entry in sorted(_COMICVINE_REQUESTS.items())
        ]

    return {
        'started_at': _COMICVINE_STARTED_AT,
        'total_requests': sum(entry['requests'] for entry in resources),
        'resources': resources,
    }


def reset_comicvine_request_stats() -> None:
    """Reset counters. Intended for tests, not normal application flow."""
    with _TELEMETRY_LOCK:
        _COMICVINE_REQUESTS.clear()
