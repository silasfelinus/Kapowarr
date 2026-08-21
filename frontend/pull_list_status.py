# -*- coding: utf-8 -*-

"""Release-calendar status and parallel manual-refresh routes."""

from flask import request

from backend.base.logging import LOGGER
from backend.features.pull_list_parallel import (get_pull_list_weeks,
                                                 pull_list_check_runner)
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
    check = pull_list_check_runner.start()
    return return_api(check, code=201 if check['status'] == 'queued' else 200)


@api.route('/pulllist/check/<int:check_id>', methods=['GET'])
@error_handler
@auth
def api_pull_list_check_status(check_id: int):
    check = pull_list_check_runner.get(check_id)
    if check is None:
        return return_api({}, 'PullListCheckNotFound', 404)
    return return_api(check)


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
