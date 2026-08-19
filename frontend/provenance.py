# -*- coding: utf-8 -*-

"""Read-only APIs for library-file provenance and known quality traits."""

from backend.features.file_provenance import (
    get_file_provenance,
    get_volume_file_provenance,
)
from backend.features.file_quality import explain_volume_file_quality
from frontend.api import api, auth, error_handler, return_api


@api.route('/files/<int:file_id>/provenance', methods=['GET'])
@error_handler
@auth
def api_file_provenance(file_id: int):
    return return_api(get_file_provenance(file_id) or {})


@api.route('/volumes/<int:volume_id>/file-provenance', methods=['GET'])
@error_handler
@auth
def api_volume_file_provenance(volume_id: int):
    return return_api(get_volume_file_provenance(volume_id))


@api.route('/volumes/<int:volume_id>/file-quality', methods=['GET'])
@error_handler
@auth
def api_volume_file_quality(volume_id: int):
    return return_api(explain_volume_file_quality(volume_id))
