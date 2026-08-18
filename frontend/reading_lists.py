# -*- coding: utf-8 -*-

"""Story-arc / reading-list UI and API routes."""

from io import BytesIO
from re import sub

from flask import request, send_file

from backend.features.reading_lists import (
    create_manual_reading_list, delete_reading_list, export_cbl,
    get_reading_list, get_reading_lists, import_cbl,
    missing_reading_list_issues,
)
from backend.features.tasks import AutoSearchIssue, TaskHandler
from frontend.api import api, auth, error_handler, return_api
from frontend.ui import render, ui


@ui.route('/activity/reading-lists', methods=['GET'])
def ui_reading_lists():
    return render('reading_lists.html')


@api.route('/readinglists', methods=['GET', 'POST'])
@error_handler
@auth
def api_reading_lists():
    if request.method == 'GET':
        return return_api(get_reading_lists())

    data = request.get_json(silent=True) or {}
    title = data.get('title')
    issue_ids = data.get('issue_ids', [])
    if not isinstance(title, str) or not isinstance(issue_ids, list) or not all(
        isinstance(issue_id, int) for issue_id in issue_ids
    ):
        return return_api({}, 'InvalidReadingList', 400)

    try:
        result = create_manual_reading_list(title, issue_ids)
    except ValueError as exc:
        return return_api({}, str(exc), 400)
    return return_api(result, code=201)


@api.route('/readinglists/import', methods=['POST'])
@error_handler
@auth
def api_import_reading_list():
    upload = request.files.get('file')
    if upload is None:
        return return_api({}, 'CBL file is required', 400)

    try:
        result = import_cbl(upload.read(), upload.filename or None)
    except ValueError as exc:
        return return_api({}, str(exc), 400)
    return return_api(result, code=201)


@api.route('/readinglists/<int:reading_list_id>', methods=['GET', 'DELETE'])
@error_handler
@auth
def api_reading_list(reading_list_id: int):
    if request.method == 'GET':
        result = get_reading_list(reading_list_id)
        if result is None:
            return return_api({}, 'ReadingListNotFound', 404)
        return return_api(result)

    if not delete_reading_list(reading_list_id):
        return return_api({}, 'ReadingListNotFound', 404)
    return return_api({})


@api.route('/readinglists/<int:reading_list_id>/export', methods=['GET'])
@error_handler
@auth
def api_export_reading_list(reading_list_id: int):
    result = get_reading_list(reading_list_id)
    if result is None:
        return return_api({}, 'ReadingListNotFound', 404)

    payload = export_cbl(reading_list_id)
    if payload is None:
        return return_api({}, 'ReadingListNotFound', 404)

    safe_title = sub(r'[^A-Za-z0-9._ -]+', '_', result['title']).strip() or 'reading-list'
    return send_file(
        BytesIO(payload),
        mimetype='application/xml',
        as_attachment=True,
        download_name=f'{safe_title}.cbl'
    ), 200


@api.route('/readinglists/<int:reading_list_id>/search-missing', methods=['POST'])
@error_handler
@auth
def api_search_missing_reading_list(reading_list_id: int):
    if get_reading_list(reading_list_id) is None:
        return return_api({}, 'ReadingListNotFound', 404)

    handler = TaskHandler()
    already_queued = {
        (entry['task'].volume_id, entry['task'].issue_id)
        for entry in handler.queue
        if isinstance(entry['task'], AutoSearchIssue)
    }

    task_ids = []
    for volume_id, issue_id in missing_reading_list_issues(reading_list_id):
        if (volume_id, issue_id) in already_queued:
            continue
        task_ids.append(handler.add(AutoSearchIssue(volume_id, issue_id)))
        already_queued.add((volume_id, issue_id))

    return return_api({
        'queued': len(task_ids),
        'task_ids': task_ids
    }, code=201)
