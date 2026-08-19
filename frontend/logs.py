# -*- coding: utf-8 -*-

"""Human-facing system log viewer routes."""

from flask import request

from backend.base.custom_exceptions import InvalidKeyValue
from backend.base.logging import get_log_file_contents
from backend.features.log_view import (
    DEFAULT_LOG_LIMIT,
    LOG_VIEW_LEVELS,
    MAX_LOG_LIMIT,
    filter_log_entries,
    parse_log_entries,
)
from backend.internals.settings import Settings
from frontend.api import api, auth, error_handler, return_api
from frontend.ui import render, ui


@ui.route('/system/logs', methods=['GET'])
def ui_logs():
    return render('logs.html')


@api.route('/system/logs/view', methods=['GET'])
@error_handler
@auth
def api_log_view():
    raw_level = request.values.get('level', 'INFO').upper()
    raw_limit = request.values.get('limit', str(DEFAULT_LOG_LIMIT))
    query = request.values.get('query', '')

    if raw_level not in LOG_VIEW_LEVELS:
        raise InvalidKeyValue('level', raw_level)
    if not isinstance(query, str):
        raise InvalidKeyValue('query', query)

    try:
        limit = int(raw_limit)
        if limit < 1 or limit > MAX_LOG_LIMIT:
            raise ValueError
    except (TypeError, ValueError):
        raise InvalidKeyValue('limit', raw_limit)

    entries = parse_log_entries(get_log_file_contents().getvalue())
    entries = filter_log_entries(
        entries,
        LOG_VIEW_LEVELS[raw_level],
        query,
        limit,
    )

    return return_api({
        'entries': entries,
        'capture_level': Settings().sv.log_level,
        'limit': limit,
    })
