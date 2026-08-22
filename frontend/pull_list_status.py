# -*- coding: utf-8 -*-

"""Release-calendar status and parallel manual-refresh routes."""

from datetime import date

from flask import request

from backend.base.custom_exceptions import InvalidKeyValue
from backend.base.logging import LOGGER
from backend.features.publisher_automation import set_all_publisher_subscriptions
from backend.features.pull_list_parallel import (get_pull_list_weeks,
                                                 pull_list_check_runner)
from backend.implementations.root_folders import RootFolders
from frontend.api import api, auth, error_handler, return_api


@api.route('/pulllist/weeks', methods=['GET'])
@error_handler
@auth
def api_pull_list_weeks():
    return return_api(get_pull_list_weeks())


@api.route('/pulllist/check', methods=['POST'])
@error_handler
@auth
def api_pull_list_check_start():
    data = request.get_json(silent=True) or {}
    week_start = data.get('week_start')
    requested_week = None
    if week_start is not None:
        if not isinstance(week_start, str):
            raise InvalidKeyValue('week_start', week_start)
        try:
            requested_week = date.fromisoformat(week_start)
        except ValueError:
            raise InvalidKeyValue('week_start', week_start)
        if requested_week.weekday() != 0:
            raise InvalidKeyValue('week_start', week_start)

    check = pull_list_check_runner.start(requested_week)
    return return_api(check, code=201 if check['status'] == 'queued' else 200)


@api.route('/pulllist/check/<int:check_id>', methods=['GET'])
@error_handler
@auth
def api_pull_list_check_status(check_id: int):
    check = pull_list_check_runner.get(check_id)
    if check is None:
        return return_api({}, 'PullListCheckNotFound', 404)
    return return_api(check)


@api.route('/pulllist/publishers/grab-all', methods=['POST'])
@error_handler
@auth
def api_pull_list_publishers_grab_all():
    """Enable auto-add + grab for every publisher currently in the catalogue."""
    data = request.get_json(silent=True) or {}
    root_folder_id = data.get('root_folder_id')
    if not isinstance(root_folder_id, int):
        raise InvalidKeyValue('root_folder_id', root_folder_id)
    RootFolders().get_one(root_folder_id)
    return return_api(
        set_all_publisher_subscriptions(root_folder_id),
        code=201
    )


@api.route('/pulllist/client-error', methods=['POST'])
@error_handler
@auth
def api_pull_list_client_error():
    """Record browser-side Pull List failures in Kapowarr's normal log."""
    data = request.get_json(silent=True) or {}
    context = str(data.get('context') or 'unknown')[:80]
    message = str(data.get('message') or 'unknown client error')[:500]
    stack = str(data.get('stack') or '')[:2000]
    LOGGER.error(
        'Pull List client error [%s]: %s%s',
        context,
        message,
        f'\n{stack}' if stack else ''
    )
    return return_api({})
