# -*- coding: utf-8 -*-

"""Comic-native Import List settings and sync routes."""

from flask import request

from backend.features.import_list_task import (
    ImportListSync,
    ensure_import_list_interval,
)
from backend.features.import_lists import (
    add_import_list_exclusion,
    create_import_list,
    delete_import_list,
    delete_import_list_exclusion,
    get_import_list,
    get_import_list_exclusions,
    get_import_lists,
    update_import_list,
)
from backend.features.tasks import TaskHandler
from frontend.api import api, auth, error_handler, return_api
from frontend.ui import render, ui


@ui.route('/settings/importlists', methods=['GET'])
def ui_import_lists():
    return render('settings_importlists.html')


@api.route('/importlists', methods=['GET', 'POST'])
@error_handler
@auth
def api_import_lists():
    if request.method == 'GET':
        return return_api(get_import_lists())

    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return return_api({}, 'InvalidImportList', 400)
    try:
        result = create_import_list(data)
        ensure_import_list_interval()
    except ValueError as error:
        return return_api({}, str(error), 400)
    return return_api(result, code=201)


@api.route('/importlists/<int:import_list_id>', methods=['GET', 'PUT', 'DELETE'])
@error_handler
@auth
def api_import_list(import_list_id: int):
    if request.method == 'GET':
        result = get_import_list(import_list_id)
        if result is None:
            return return_api({}, 'ImportListNotFound', 404)
        return return_api(result)

    if request.method == 'DELETE':
        if not delete_import_list(import_list_id):
            return return_api({}, 'ImportListNotFound', 404)
        return return_api({})

    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return return_api({}, 'InvalidImportList', 400)
    try:
        result = update_import_list(import_list_id, data)
        ensure_import_list_interval()
    except KeyError:
        return return_api({}, 'ImportListNotFound', 404)
    except ValueError as error:
        return return_api({}, str(error), 400)
    return return_api(result)


@api.route('/importlists/<int:import_list_id>/sync', methods=['POST'])
@error_handler
@auth
def api_sync_import_list(import_list_id: int):
    if get_import_list(import_list_id) is None:
        return return_api({}, 'ImportListNotFound', 404)
    task_id = TaskHandler().add(ImportListSync(import_list_id))
    return return_api({'task_id': task_id}, code=201)


@api.route('/importlists/exclusions', methods=['GET', 'POST'])
@error_handler
@auth
def api_import_list_exclusions():
    if request.method == 'GET':
        return return_api(get_import_list_exclusions())

    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return return_api({}, 'InvalidImportListExclusion', 400)
    try:
        result = add_import_list_exclusion(
            data.get('comicvine_volume_id'),
            data.get('note', ''),
        )
    except ValueError as error:
        return return_api({}, str(error), 400)
    return return_api(result, code=201)


@api.route('/importlists/exclusions/<int:comicvine_volume_id>', methods=['DELETE'])
@error_handler
@auth
def api_import_list_exclusion(comicvine_volume_id: int):
    if not delete_import_list_exclusion(comicvine_volume_id):
        return return_api({}, 'ImportListExclusionNotFound', 404)
    return return_api({})
