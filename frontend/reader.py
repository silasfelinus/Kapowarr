# -*- coding: utf-8 -*-

"""Comic reader UI and authenticated page-delivery endpoints.

Imported at the end of ``frontend.ui`` so these routes attach to Kapowarr's
existing UI/API blueprints without widening the main API router.
"""

from __future__ import annotations

from io import BytesIO
from zipfile import BadZipFile, ZipFile

from flask import send_file

from backend.features.comic_reader import get_issue_pages
from backend.implementations.volumes import Library
from frontend.api import api, auth, error_handler, return_api
from frontend.ui import render, ui


@ui.route('/reader/<int:issue_id>', methods=['GET'])
def ui_reader(issue_id: int):
    """Render the reader shell. Issue/page access remains API-authenticated."""
    return render('reader.html', issue_id=issue_id)


@api.route('/reader/issues/<int:issue_id>', methods=['GET'])
@error_handler
@auth
def api_reader_manifest(issue_id: int):
    """Describe the readable pages linked to an issue."""
    issue = Library.get_issue(issue_id).get_data()
    volume = Library.get_volume(issue.volume_id).get_data()
    pages = get_issue_pages(issue_id)

    return return_api({
        'issue_id': issue.id,
        'volume_id': issue.volume_id,
        'issue_number': issue.issue_number,
        'issue_title': issue.title,
        'volume_title': volume.title,
        'page_count': len(pages),
        'readable': bool(pages)
    })


@api.route(
    '/reader/issues/<int:issue_id>/pages/<int:page_index>',
    methods=['GET']
)
@error_handler
@auth
def api_reader_page(issue_id: int, page_index: int):
    """Serve one page by issue/index without exposing filesystem paths."""
    # Validate the issue before touching linked files.
    Library.get_issue(issue_id)
    pages = get_issue_pages(issue_id)
    if page_index < 0 or page_index >= len(pages):
        return return_api({}, 'ReaderPageNotFound', 404)

    page = pages[page_index]
    try:
        if page['member'] is None:
            return send_file(
                page['filepath'],
                mimetype=page['mimetype'],
                conditional=True
            ), 200

        with ZipFile(page['filepath'], 'r') as archive:
            page_data = archive.read(page['member'])
        return send_file(
            BytesIO(page_data),
            mimetype=page['mimetype'],
            max_age=3600
        ), 200

    except (BadZipFile, FileNotFoundError, KeyError, OSError):
        return return_api({}, 'ReaderPageUnavailable', 404)
