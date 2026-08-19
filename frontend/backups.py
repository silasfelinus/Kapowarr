# -*- coding: utf-8 -*-

"""System Backup UI and authenticated API routes."""

from flask import send_file

from backend.base.custom_exceptions import InvalidKeyValue
from backend.features.backups import (create_backup, delete_backup, get_backup,
                                      get_backup_path, list_backups,
                                      stage_restore)
from backend.internals.server import Server
from frontend.api import api, auth, error_handler, return_api
from frontend.ui import render, ui


@ui.route('/system/backup', methods=['GET'])
def ui_backup():
    return render('backup.html')


@api.route('/system/backups', methods=['GET', 'POST'])
@error_handler
@auth
def api_backups():
    from flask import request

    if request.method == 'GET':
        return return_api(list_backups())

    return return_api(create_backup(), code=201)


@api.route('/system/backups/<filename>', methods=['GET', 'DELETE'])
@error_handler
@auth
def api_backup(filename: str):
    from flask import request

    try:
        backup = get_backup(filename)
    except (FileNotFoundError, ValueError):
        raise InvalidKeyValue('filename', filename)

    if request.method == 'DELETE':
        delete_backup(filename)
        return return_api({})

    return send_file(
        get_backup_path(filename),
        mimetype='application/zip',
        as_attachment=True,
        download_name=backup['filename'],
    )


@api.route('/system/backups/<filename>/restore', methods=['POST'])
@error_handler
@auth
def api_backup_restore(filename: str):
    try:
        result = stage_restore(filename)
    except (FileNotFoundError, ValueError):
        raise InvalidKeyValue('filename', filename)

    # Server.shutdown() is delayed one second, allowing this response to reach
    # the browser before the process exits. The staged DB is applied by the new
    # process before setup_db() opens or migrates it.
    Server().restart()
    return return_api(result)
