# -*- coding: utf-8 -*-

"""GetComics Discover UI and API routes."""

from flask import request

from backend.base.custom_exceptions import InvalidKeyValue
from backend.features.discover import get_discover_feed
from frontend.api import api, auth, error_handler, return_api
from frontend.ui import render, ui


@ui.route('/activity/discover', methods=['GET'])
def ui_discover():
    return render('discover.html')


@api.route('/discover', methods=['GET'])
@error_handler
@auth
def api_discover():
    raw_page = request.values.get('page', '1')
    try:
        page = int(raw_page)
        if page < 1:
            raise ValueError
    except (TypeError, ValueError):
        raise InvalidKeyValue('page', raw_page)

    items, max_page = get_discover_feed(page)
    return return_api({
        'items': items,
        'page': min(page, max_page),
        'max_page': max_page
    })
