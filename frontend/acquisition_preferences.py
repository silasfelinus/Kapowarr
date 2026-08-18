# -*- coding: utf-8 -*-

"""Acquisition preference API routes attached to Kapowarr's existing API."""

from flask import request

from backend.features.acquisition_preferences import (
    get_acquisition_preferences, update_acquisition_preferences)
from frontend.api import api, auth, error_handler, return_api


@api.route('/settings/acquisition', methods=['GET', 'PUT'])
@error_handler
@auth
def api_acquisition_preferences():
    if request.method == 'GET':
        return return_api(get_acquisition_preferences())

    data: dict = request.get_json() or {}
    return return_api(update_acquisition_preferences(data))
