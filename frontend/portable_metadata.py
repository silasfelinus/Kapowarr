# -*- coding: utf-8 -*-

"""Authenticated preview/export/write endpoints for portable series metadata."""

from io import BytesIO

from flask import request, send_file

from backend.base.custom_exceptions import InvalidKeyValue
from backend.features.portable_metadata import (
    preview_series_json,
    serialized_series_json,
    write_series_json,
)
from frontend.api import api, auth, error_handler, return_api


@api.route('/volumes/<int:volume_id>/portable-metadata/series', methods=['GET'])
@error_handler
@auth
def api_portable_series_preview(volume_id: int):
    return return_api(preview_series_json(volume_id))


@api.route('/volumes/<int:volume_id>/portable-metadata/series', methods=['POST'])
@error_handler
@auth
def api_portable_series_write(volume_id: int):
    data = request.get_json(silent=True)
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise InvalidKeyValue(value=data)

    overwrite = data.get('overwrite', False)
    if not isinstance(overwrite, bool):
        raise InvalidKeyValue('overwrite', overwrite)

    result = write_series_json(volume_id, overwrite=overwrite)
    return return_api(result, code=201 if result['written'] else 200)


@api.route(
    '/volumes/<int:volume_id>/portable-metadata/series/download',
    methods=['GET'],
)
@error_handler
@auth
def api_portable_series_download(volume_id: int):
    content = serialized_series_json(volume_id).encode('utf-8')
    return send_file(
        BytesIO(content),
        mimetype='application/json',
        as_attachment=True,
        download_name='series.json',
    ), 200
