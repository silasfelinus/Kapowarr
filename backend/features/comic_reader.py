# -*- coding: utf-8 -*-

"""Page discovery for Kapowarr's lightweight comic reader.

The first reader slice deliberately supports formats that can be served without
extracting a comic into a public directory: loose browser-readable images and
ZIP/CBZ archives. CBR/RAR and PDF can build on the same page descriptor contract
later without changing the reader UI.
"""

from __future__ import annotations

from mimetypes import guess_type
from os.path import splitext
from re import split
from typing import Any, Dict, List
from zipfile import BadZipFile, ZipFile

from backend.base.definitions import FileConstants, FileData
from backend.base.logging import LOGGER
from backend.internals.db_models import FilesDB

READER_ARCHIVE_EXTENSIONS = ('.cbz', '.zip')
READER_IMAGE_EXTENSIONS = tuple(
    extension.lower()
    for extension in FileConstants.IMAGE_EXTENSIONS
)


def natural_sort_key(value: str) -> List[Any]:
    """Sort page names naturally so ``page2`` comes before ``page10``."""
    return [
        int(part) if part.isdigit() else part.lower()
        for part in split(r'(\d+)', value)
    ]


def is_reader_supported_file(filepath: str) -> bool:
    """Return whether the first reader slice can display ``filepath``."""
    extension = splitext(filepath)[1].lower()
    return (
        extension in READER_IMAGE_EXTENSIONS
        or extension in READER_ARCHIVE_EXTENSIONS
    )


def list_archive_pages(filepath: str) -> List[str]:
    """Return naturally sorted browser-readable image members from a ZIP/CBZ."""
    try:
        with ZipFile(filepath, 'r') as archive:
            pages = [
                entry.filename
                for entry in archive.infolist()
                if (
                    not entry.is_dir()
                    and splitext(entry.filename)[1].lower()
                    in READER_IMAGE_EXTENSIONS
                )
            ]
    except (BadZipFile, OSError):
        LOGGER.warning('Comic reader could not inspect archive: %s', filepath)
        return []

    pages.sort(key=natural_sort_key)
    return pages


def build_pages_for_files(files: List[FileData]) -> List[Dict[str, Any]]:
    """Build ordered page descriptors for issue-linked files.

    Loose images become one page each. ZIP/CBZ files contribute each contained
    image without extracting it to disk. Unsupported files are ignored so an
    issue containing a PDF/CBR can report zero readable pages rather than expose
    arbitrary local files.
    """
    pages: List[Dict[str, Any]] = []

    for file_data in sorted(
        files,
        key=lambda item: natural_sort_key(item['filepath'])
    ):
        filepath = file_data['filepath']
        extension = splitext(filepath)[1].lower()

        if extension in READER_IMAGE_EXTENSIONS:
            pages.append({
                'file_id': file_data['id'],
                'filepath': filepath,
                'member': None,
                'mimetype': guess_type(filepath)[0] or 'image/jpeg'
            })
            continue

        if extension not in READER_ARCHIVE_EXTENSIONS:
            continue

        for member in list_archive_pages(filepath):
            pages.append({
                'file_id': file_data['id'],
                'filepath': filepath,
                'member': member,
                'mimetype': guess_type(member)[0] or 'image/jpeg'
            })

    return pages


def get_issue_pages(issue_id: int) -> List[Dict[str, Any]]:
    """Return page descriptors for files Kapowarr already links to an issue."""
    return build_pages_for_files(FilesDB.fetch(issue_id=issue_id))
