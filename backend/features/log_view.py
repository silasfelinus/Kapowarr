# -*- coding: utf-8 -*-

"""Shared parsing and filtering helpers for Kapowarr's detailed file log."""

import logging
import re
from typing import Any, Dict, List


LOG_ENTRY_PATTERN = re.compile(
    r'^(?P<timestamp>[^|]+?)\s+\|\s+'
    r'(?P<process>[^|]+?)\s+\|\s+'
    r'(?P<thread>[^|]+?)\s+\|\s+'
    r'(?P<source>[^|]+?)\s+\|\s+'
    r'(?P<level>[A-Z]+)\s+\|\s?'
    r'(?P<message>.*)$'
)
LOG_VIEW_LEVELS = {
    'DEBUG': logging.DEBUG,
    'INFO': logging.INFO,
    'WARNING': logging.WARNING,
    'ERROR': logging.ERROR,
    'CRITICAL': logging.CRITICAL,
}
DEFAULT_LOG_LIMIT = 500
MAX_LOG_LIMIT = 2000


def parse_log_entries(contents: str) -> List[Dict[str, Any]]:
    """Parse Kapowarr's detailed file log format, preserving tracebacks."""
    entries: List[Dict[str, Any]] = []
    current: Dict[str, Any] = {}

    for line in contents.splitlines():
        match = LOG_ENTRY_PATTERN.match(line)
        if match:
            if current:
                entries.append(current)

            current = match.groupdict()
            current['timestamp'] = current['timestamp'].strip()
            current['process'] = current['process'].strip()
            current['thread'] = current['thread'].strip()
            current['source'] = current['source'].strip()
            current['level'] = current['level'].strip()
            current['level_no'] = logging._nameToLevel.get(
                current['level'], logging.NOTSET
            )
        elif current:
            current['message'] += f'\n{line}'

    if current:
        entries.append(current)

    return entries


def filter_log_entries(
    entries: List[Dict[str, Any]],
    minimum_level: int,
    query: str,
    limit: int,
) -> List[Dict[str, Any]]:
    """Return the newest matching entries, newest first."""
    query = query.casefold().strip()
    filtered = []

    for entry in reversed(entries):
        if entry['level_no'] < minimum_level:
            continue

        if query:
            haystack = ' '.join((
                entry['timestamp'],
                entry['process'],
                entry['thread'],
                entry['source'],
                entry['level'],
                entry['message'],
            )).casefold()
            if query not in haystack:
                continue

        filtered.append(entry)
        if len(filtered) >= limit:
            break

    return filtered
