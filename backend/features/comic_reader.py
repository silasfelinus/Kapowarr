# -*- coding: utf-8 -*-

"""Page discovery for Kapowarr's lightweight comic reader.

The reader supports formats that can be served without extracting a comic into
a public directory: loose browser-readable images, ZIP/CBZ archives, and linked
PDF documents. CBR/RAR can build on the same reader contract later.
"""

from __future__ import annotations

from mimetypes import guess_type
from os.path import splitext
from re import split
from typing import Any, Dict, List, Union
from zipfile import BadZipFile, ZipFile

from backend.base.definitions import FileConstants, FileData
from backend.base.logging import LOGGER
from backend.internals.db_models import FilesDB

READER_ARCHIVE_EXTENSIONS = ('.cbz', '.zip')
READER_PDF_EXTENSIONS = ('.pdf',)
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
    """Return whether the built-in reader can display ``filepath``."""
    extension = splitext(filepath)[1].lower()
    return (
        extension in READER_IMAGE_EXTENSIONS
        or extension in READER_ARCHIVE_EXTENSIONS
        or extension in READER_PDF_EXTENSIONS
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
    """Build ordered image-page descriptors for issue-linked files.

    Loose images become one page each. ZIP/CBZ files contribute each contained
    image without extracting it to disk. PDF files are deliberately handled as
    documents by :func:`find_pdf_file` rather than pretending each PDF is one
    image page.
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


def find_pdf_file(files: List[FileData]) -> Union[FileData, None]:
    """Return the first naturally sorted issue-linked PDF, if one exists."""
    pdf_files = [
        file_data
        for file_data in files
        if splitext(file_data['filepath'])[1].lower() in READER_PDF_EXTENSIONS
    ]
    if not pdf_files:
        return None

    return min(
        pdf_files,
        key=lambda item: natural_sort_key(item['filepath'])
    )


def get_issue_files(issue_id: int) -> List[FileData]:
    """Return files Kapowarr already links to an issue."""
    return FilesDB.fetch(issue_id=issue_id)


def get_issue_pages(issue_id: int) -> List[Dict[str, Any]]:
    """Return image-page descriptors for files linked to an issue."""
    return build_pages_for_files(get_issue_files(issue_id))


def get_issue_pdf(issue_id: int) -> Union[FileData, None]:
    """Return a linked PDF descriptor for an issue, if one exists."""
    return find_pdf_file(get_issue_files(issue_id))
