# -*- coding: utf-8 -*-

"""System Backup UI and authenticated API routes."""

from datetime import datetime, timedelta
from os import remove
from os.path import exists, join

from flask import request, send_file

from backend.base.custom_exceptions import InvalidKeyValue
from backend.features.backups import (create_backup, delete_backup,
                                      get_backup, get_backup_folder,
                                      get_backup_path, list_backups,
                                      stage_restore)
from backend.internals.server import Server
from frontend.api import api, auth, error_handler, return_api
from frontend.ui import render, ui


def _uploaded_backup_filename() -> str:
    """Reserve a normal-looking backup filename without overwriting existing data."""
    backup_folder = get_backup_folder()
    now = datetime.now()
    for offset in range(60):
        filename = (
            'kapowarr-backup-'
            f'{(now + timedelta(seconds=offset)).strftime("%Y-%m-%d-%H%M%S")}.zip'
        )
        if not exists(join(backup_folder, filename)):
            return filename
    raise FileExistsError('Could not reserve a backup filename')


@ui.route('/system/backup', methods=['GET'])
def ui_backup():
    return render('backup.html')


@api.route('/system/backups', methods=['GET', 'POST'])
@error_handler
@auth
def api_backups():
    if request.method == 'GET':
        return return_api(list_backups())

    return return_api(create_backup(), code=201)


@api.route('/system/backups/<filename>', methods=['GET', 'DELETE'])
@error_handler
@auth
def api_backup(filename: str):
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


@api.route('/system/backups/restore', methods=['POST'])
@error_handler
@auth
def api_backup_restore_upload():
    """Persist, validate, and restore an authenticated uploaded backup archive."""
    uploaded = request.files.get('restore')
    if uploaded is None or not uploaded.filename:
        raise InvalidKeyValue('restore', 'missing backup file')

    filename = _uploaded_backup_filename()
    filepath = join(get_backup_folder(), filename)
    try:
        uploaded.save(filepath)
        result = stage_restore(filename)
    except (OSError, ValueError):
        if exists(filepath):
            remove(filepath)
        raise InvalidKeyValue('restore', uploaded.filename)

    result['uploaded_backup'] = filename
    Server().restart()
    return return_api(result)


@api.route('/system/backups/<filename>/restore', methods=['POST'])
@error_handler
@auth
def api_backup_restore(filename: str):
    try:
        result = stage_restore(filename)
    except (OSError, ValueError):
        raise InvalidKeyValue('filename', filename)

    # Server.shutdown() is delayed one second, allowing this response to reach
    # the browser before the process exits. The staged DB is applied by the new
    # process before setup_db() opens or migrates it.
    Server().restart()
    return return_api(result)
