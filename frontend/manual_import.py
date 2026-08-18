# -*- coding: utf-8 -*-

"""Manual import API route.

Decorates the existing `api` blueprint (see `frontend.discover`'s docstring
convention) rather than editing `frontend/api.py` inline. The Wanted
workbench page is this route's primary caller, but it isn't wanted-specific:
any filepaths and a volume (optionally an issue) are enough.
"""

from typing import Any, List, Union

from flask import request

from backend.base.custom_exceptions import InvalidKeyValue
from backend.features.manual_import import manual_import_files
from frontend.api import api, auth, error_handler, return_api


@api.route('/manualimport', methods=['POST'])
@error_handler
@auth
def api_manual_import():
    body: Any = request.get_json()
    if not isinstance(body, dict):
        raise InvalidKeyValue('body', body)

    volume_id = body.get('volume_id')
    if not isinstance(volume_id, int) or isinstance(volume_id, bool):
        raise InvalidKeyValue('volume_id', volume_id)

    issue_id = body.get('issue_id')
    if issue_id is not None and (
        not isinstance(issue_id, int) or isinstance(issue_id, bool)
    ):
        raise InvalidKeyValue('issue_id', issue_id)

    filepaths: Union[List[Any], Any] = body.get('filepaths')
    if (
        not isinstance(filepaths, list)
        or not filepaths
        or not all(isinstance(f, str) and f for f in filepaths)
    ):
        raise InvalidKeyValue('filepaths', filepaths)

    result = manual_import_files(volume_id, filepaths, issue_id)
    return return_api(result)
