# -*- coding: utf-8 -*-

"""Shared parsing, filtering and paging helpers for Kapowarr's file log."""

import logging
import re
from typing import Any, Dict, List, Tuple


LOG_ENTRY_PATTERN = re.compile(
    r'^(?P<timestamp>[^|]+?)\s+\|\s+'
    r'(?P<process>[^|]+?)\s+\|\s+'
    r'(?P<thread>[^|]+?)\s+\|\s+'
    r'(?P<source>[^|]+?)\s+\|\s+'
    r'(?P<level>[A-Z]+)\s+\|\s?'
    r'(?P<message>.*)$'
)

# The levels a reader can single out. `ALL` is the default because a log is
# read to find out what happened, and narrowing to one severity is the
# exception rather than the starting point.
LOG_LEVEL_ALL = 'ALL'
LOG_VIEW_LEVELS = {
    'DEBUG': logging.DEBUG,
    'INFO': logging.INFO,
    'WARNING': logging.WARNING,
    'ERROR': logging.ERROR,
    'CRITICAL': logging.CRITICAL,
}
LOG_VIEW_FILTERS = (LOG_LEVEL_ALL, *LOG_VIEW_LEVELS)

PAGE_SIZES = (25, 50, 100, 250, 500)
DEFAULT_PAGE_SIZE = 50


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
            for key in ('timestamp', 'process', 'thread', 'source', 'level'):
                current[key] = current[key].strip()
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
    level: str,
) -> List[Dict[str, Any]]:
    """Return matching entries newest first.

    `level` selects one severity rather than a minimum. Asking for warnings
    used to also return every info line beneath them, which is the opposite of
    what picking a level off a filter is for.
    """
    ordered = list(reversed(entries))
    if level == LOG_LEVEL_ALL:
        return ordered

    return [entry for entry in ordered if entry['level'] == level]


def page_log_entries(
    entries: List[Dict[str, Any]],
    page: int,
    page_size: int,
) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    """Slice `entries` into one page, and describe where that page sits.

    A page past the end is clamped to the last one rather than returning
    nothing, so a filter that shortens the log cannot strand the reader on an
    empty page with no indication of why.
    """
    total_entries = len(entries)
    total_pages = max(1, -(-total_entries // page_size))
    page = min(max(page, 1), total_pages)
    offset = (page - 1) * page_size

    return entries[offset:offset + page_size], {
        'page': page,
        'page_size': page_size,
        'total_entries': total_entries,
        'total_pages': total_pages,
    }
