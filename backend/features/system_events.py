# -*- coding: utf-8 -*-

"""Lightweight operational telemetry used by the System Events surface.

Kapowarr already persists task and download histories. This module deliberately
keeps only process-local counters for metadata-provider operations so Events can
show what the current process is doing without creating a duplicate history
store. Provider operations are intentionally not presented as raw HTTP request
counts because one operation may internally batch more than one API request.
"""

from __future__ import annotations

from threading import Lock
from time import time
from typing import Any, Dict


_TELEMETRY_LOCK = Lock()
_COMICVINE_STARTED_AT = round(time())
_COMICVINE_OPERATIONS: Dict[str, Dict[str, Any]] = {}


def _empty_operation(operation: str) -> Dict[str, Any]:
    return {
        'operation': operation,
        'operations': 0,
        'success': 0,
        'rate_limit': 0,
        'not_found': 0,
        'invalid_key': 0,
        'other_error': 0,
        'last_operation_at': None,
        'last_outcome': None,
        'last_outcome_at': None,
    }


def begin_comicvine_operation(operation: str) -> str:
    """Record one ComicVine provider operation and return its stable key."""
    operation = operation.strip().lower() or 'unknown'
    now = round(time())
    with _TELEMETRY_LOCK:
        entry = _COMICVINE_OPERATIONS.setdefault(
            operation,
            _empty_operation(operation),
        )
        entry['operations'] += 1
        entry['last_operation_at'] = now
    return operation


def finish_comicvine_operation(operation: str, outcome: str) -> None:
    """Record how an already-counted ComicVine provider operation completed."""
    now = round(time())
    with _TELEMETRY_LOCK:
        entry = _COMICVINE_OPERATIONS.setdefault(
            operation,
            _empty_operation(operation),
        )
        if outcome not in entry or outcome in (
            'operation', 'operations', 'last_operation_at',
            'last_outcome', 'last_outcome_at'
        ):
            outcome = 'other_error'
        entry[outcome] += 1
        entry['last_outcome'] = outcome
        entry['last_outcome_at'] = now


def get_comicvine_operation_stats() -> Dict[str, Any]:
    """Return a JSON-safe ComicVine operation snapshot for this process."""
    with _TELEMETRY_LOCK:
        operations = [
            dict(entry)
            for _, entry in sorted(_COMICVINE_OPERATIONS.items())
        ]

    return {
        'started_at': _COMICVINE_STARTED_AT,
        'total_operations': sum(entry['operations'] for entry in operations),
        'operations': operations,
        'measurement': 'provider_operations',
    }


def reset_comicvine_operation_stats() -> None:
    """Reset counters. Intended for tests, not normal application flow."""
    with _TELEMETRY_LOCK:
        _COMICVINE_OPERATIONS.clear()
