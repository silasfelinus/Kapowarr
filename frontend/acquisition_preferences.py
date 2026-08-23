# -*- coding: utf-8 -*-

"""Acquisition preference API routes attached to Kapowarr's existing API."""

from flask import request

from backend.features.acquisition_preferences import (
    get_acquisition_preferences, update_acquisition_preferences)
from backend.features.grab_size_limits import (GRAB_SIZE_KEYS,
                                                get_grab_size_limits,
                                                update_grab_size_limits)
from frontend.api import api, auth, error_handler, return_api


def _current_preferences():
    return {
        **get_acquisition_preferences(),
        **get_grab_size_limits()
    }


@api.route('/settings/acquisition', methods=['GET', 'PUT'])
@error_handler
@auth
def api_acquisition_preferences():
    if request.method == 'GET':
        return return_api(_current_preferences())

    data: dict = request.get_json() or {}
    grab_data = {
        key: value
        for key, value in data.items()
        if key in GRAB_SIZE_KEYS
    }
    preference_data = {
        key: value
        for key, value in data.items()
        if key not in GRAB_SIZE_KEYS
    }

    if preference_data:
        update_acquisition_preferences(preference_data)
    if grab_data:
        update_grab_size_limits(grab_data)

    return return_api(_current_preferences())
