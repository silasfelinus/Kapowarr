# -*- coding: utf-8 -*-

"""Unified operational Events view built from Kapowarr's existing evidence."""

from datetime import datetime
from typing import Any, Dict, List

from flask import request

from backend.base.custom_exceptions import InvalidKeyValue
from backend.base.logging import get_log_file_contents
from backend.features.download_queue import get_download_history
from backend.features.system_events import get_comicvine_operation_stats
from backend.features.tasks import get_task_history
from frontend.api import api, auth, error_handler, return_api
from frontend.logs import parse_log_entries
from frontend.ui import render, ui


DEFAULT_EVENT_LIMIT = 100
MAX_EVENT_LIMIT = 250


def _task_events() -> List[Dict[str, Any]]:
    events = []
    for entry in get_task_history(0):
        display_title = entry['display_title'] or entry['task_name']
        failed_marker = '— Failed:'
        failed = failed_marker in display_title
        if failed:
            title, detail = display_title.split(failed_marker, 1)
            detail = detail.strip()
        else:
            title, detail = display_title, ''

        events.append({
            'timestamp': entry['run_at'],
            'kind': 'task',
            'level': 'ERROR' if failed else 'INFO',
            'source': 'Task',
            'title': title.strip(),
            'message': detail,
            'link': None,
            'volume_id': None,
        })
    return events


def _download_events() -> List[Dict[str, Any]]:
    events = []
    for entry in get_download_history(offset=0):
        success = entry['success']
        title = entry['file_title'] or entry['web_title'] or 'Download'
        subtitle = entry['web_sub_title'] or ''
        events.append({
            'timestamp': entry['downloaded_at'],
            'kind': 'download',
            'level': 'ERROR' if success is False else 'INFO',
            'source': entry['source'] or 'Download',
            'title': title,
            'message': subtitle,
            'link': entry['web_link'],
            'volume_id': entry['volume_id'],
        })
    return events


def _log_timestamp(value: str) -> int:
    try:
        return round(datetime.strptime(
            value,
            '%Y-%m-%dT%H:%M:%S%z',
        ).timestamp())
    except (TypeError, ValueError):
        return 0


def _log_events() -> List[Dict[str, Any]]:
    events = []
    entries = parse_log_entries(get_log_file_contents().getvalue())
    for entry in reversed(entries):
        if entry['level'] not in ('WARNING', 'ERROR', 'CRITICAL'):
            continue

        # Task history already persists task failures in a cleaner form. Avoid
        # showing the same exception twice in the unified timeline.
        if entry['source'].startswith('tasks.pyL'):
            continue

        first_line = entry['message'].splitlines()[0].strip()
        events.append({
            'timestamp': _log_timestamp(entry['timestamp']),
            'kind': 'system',
            'level': entry['level'],
            'source': entry['source'],
            'title': first_line or entry['level'].title(),
            'message': entry['message'],
            'link': None,
            'volume_id': None,
        })
        if len(events) >= 100:
            break
    return events


def get_system_events(limit: int = DEFAULT_EVENT_LIMIT) -> List[Dict[str, Any]]:
    """Merge existing task/download history with recent operational warnings."""
    events = _task_events() + _download_events() + _log_events()
    events.sort(key=lambda entry: entry['timestamp'] or 0, reverse=True)
    return events[:limit]


@ui.route('/system/events', methods=['GET'])
def ui_events():
    return render('events.html')


@api.route('/system/events', methods=['GET'])
@error_handler
@auth
def api_events():
    raw_limit = request.values.get('limit', str(DEFAULT_EVENT_LIMIT))
    try:
        limit = int(raw_limit)
        if limit < 1 or limit > MAX_EVENT_LIMIT:
            raise ValueError
    except (TypeError, ValueError):
        raise InvalidKeyValue('limit', raw_limit)

    return return_api({
        'events': get_system_events(limit),
        'comicvine': get_comicvine_operation_stats(),
        'limit': limit,
    })
