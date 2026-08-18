# -*- coding: utf-8 -*-

"""Wanted/Missing workbench UI and API routes."""

from flask import request

from backend.base.custom_exceptions import InvalidKeyValue
from backend.features.wanted import (DEFAULT_WANTED_LIMIT, MAX_WANTED_LIMIT,
                                     get_wanted_issues)
from frontend.api import api, auth, error_handler, return_api
from frontend.ui import render, ui


@ui.route('/activity/wanted', methods=['GET'])
def ui_wanted():
    return render('wanted.html')


@api.route('/wanted', methods=['GET'])
@error_handler
@auth
def api_wanted():
    raw_offset = request.values.get('offset', '0')
    raw_limit = request.values.get('limit', str(DEFAULT_WANTED_LIMIT))
    try:
        offset = int(raw_offset)
        limit = int(raw_limit)
        if offset < 0 or limit < 1 or limit > MAX_WANTED_LIMIT:
            raise ValueError
    except (TypeError, ValueError):
        raise InvalidKeyValue('pagination', {
            'offset': raw_offset,
            'limit': raw_limit
        })

    query = request.values.get('query', '')
    if not isinstance(query, str):
        raise InvalidKeyValue('query', query)

    return return_api(get_wanted_issues(query, offset, limit))
