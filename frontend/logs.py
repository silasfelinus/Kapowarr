# -*- coding: utf-8 -*-

"""Human-facing system log viewer routes."""

from flask import request

from backend.base.custom_exceptions import InvalidKeyValue
from backend.base.logging import clear_log_files, get_log_file_contents
from backend.features.log_view import (
    DEFAULT_PAGE_SIZE,
    LOG_LEVEL_ALL,
    LOG_VIEW_FILTERS,
    PAGE_SIZES,
    filter_log_entries,
    page_log_entries,
    parse_log_entries,
)
from frontend.api import api, auth, error_handler, return_api
from frontend.ui import render, ui


@ui.route('/system/logs', methods=['GET'])
def ui_logs():
    return render('logs.html')


def _int_arg(name: str, default: int) -> int:
    raw = request.values.get(name, str(default))
    try:
        return int(raw)
    except (TypeError, ValueError):
        raise InvalidKeyValue(name, raw)


@api.route('/system/logs/view', methods=['GET'])
@error_handler
@auth
def api_log_view():
    level = request.values.get('level', LOG_LEVEL_ALL).upper()
    if level not in LOG_VIEW_FILTERS:
        raise InvalidKeyValue('level', level)

    page_size = _int_arg('page_size', DEFAULT_PAGE_SIZE)
    if page_size not in PAGE_SIZES:
        raise InvalidKeyValue('page_size', page_size)

    page = _int_arg('page', 1)
    if page < 1:
        raise InvalidKeyValue('page', page)

    entries = parse_log_entries(get_log_file_contents().getvalue())
    matching = filter_log_entries(entries, level)
    paged, pagination = page_log_entries(matching, page, page_size)

    return return_api({
        'entries': paged,
        'level': level,
        **pagination,
    })


@api.route('/system/logs/clear', methods=['POST'])
@error_handler
@auth
def api_log_clear():
    clear_log_files()
    return return_api({})
