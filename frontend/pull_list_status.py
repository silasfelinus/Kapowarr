# -*- coding: utf-8 -*-

"""Release-calendar status and parallel manual-refresh routes."""

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
