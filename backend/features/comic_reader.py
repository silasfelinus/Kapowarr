# -*- coding: utf-8 -*-

"""Page discovery for Kapowarr's lightweight comic reader.

The reader serves loose browser-readable images, ZIP/CBZ, RAR/CBR and
TAR/CBT archive members, plus linked PDF documents without permanently
extracting comics into a public directory.

`.cb7`/`.7z` is deliberately still absent. Kapowarr bundles a RAR
executable (`backend/lib/rar_*`) and gets ZIP and tar from the standard
library, but nothing in this codebase -- and no runtime dependency in
`requirements.txt` -- can open a 7z container. Adding it would mean either
a new bundled binary or a new dependency, which is a packaging decision
rather than a reader one. `is_reader_supported_file` reports 7z as
unsupported instead of listing it and then failing per page.
"""

from __future__ import annotations

from mimetypes import guess_type
from os.path import splitext
from re import split
from subprocess import run
from tarfile import ReadError, TarInfo
from tarfile import open as tar_open
from typing import Any, Dict, List, Union
from zipfile import BadZipFile, ZipFile

from backend.base.definitions import FileConstants, FileData, RAR_EXECUTABLES
from backend.base.files import folder_path
from backend.base.helpers import get_os_type, run_rar
from backend.base.logging import LOGGER
from backend.internals.db_models import FilesDB

READER_ZIP_ARCHIVE_EXTENSIONS = ('.cbz', '.zip')
READER_RAR_ARCHIVE_EXTENSIONS = ('.cbr', '.rar')
# Matched against the whole lowercased filename, not `splitext`, because
# the compressed forms are double extensions: `splitext('x.tar.gz')[1]` is
# `.gz`, which on its own says nothing about tar. `tarfile` sniffs the
# compression itself, so every suffix here opens through the same call.
# Shared with `backend.features.archive_integrity` via `FileConstants` so
# the set the reader can display and the set the verifier inspects cannot
# drift apart.
READER_TAR_ARCHIVE_SUFFIXES = FileConstants.TAR_ARCHIVE_SUFFIXES
READER_ARCHIVE_EXTENSIONS = (
    READER_ZIP_ARCHIVE_EXTENSIONS + READER_RAR_ARCHIVE_EXTENSIONS
)
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


def is_tar_archive_file(filepath: str) -> bool:
    """Return whether ``filepath`` names a TAR-family container.

    Matches the full name rather than the last extension so the
    double-extension forms (`.tar.gz`, `.tar.xz`) are recognised and a bare
    `.gz`/`.xz` -- which is a single compressed file, not an archive of
    pages -- is not.
    """
    return filepath.lower().endswith(READER_TAR_ARCHIVE_SUFFIXES)


def is_reader_supported_file(filepath: str) -> bool:
    """Return whether the built-in reader can display ``filepath``."""
    extension = splitext(filepath)[1].lower()
    return (
        extension in READER_IMAGE_EXTENSIONS
        or extension in READER_ARCHIVE_EXTENSIONS
        or extension in READER_PDF_EXTENSIONS
        or is_tar_archive_file(filepath)
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


def list_rar_pages(filepath: str) -> List[str]:
    """Return naturally sorted browser-readable image members from RAR/CBR."""
    try:
        result = run_rar([
            'lb',       # Bare file list, one archive member per line.
            '-inul',    # Suppress informational output.
            filepath
        ])
    except (KeyError, OSError):
        LOGGER.warning('Comic reader could not inspect RAR archive: %s', filepath)
        return []

    if result.returncode != 0:
        LOGGER.warning('Comic reader could not inspect RAR archive: %s', filepath)
        return []

    pages = [
        member.strip()
        for member in result.stdout.splitlines()
        if (
            member.strip()
            and splitext(member.strip())[1].lower()
            in READER_IMAGE_EXTENSIONS
        )
    ]
    pages.sort(key=natural_sort_key)
    return pages


def _is_readable_tar_page(member: TarInfo) -> bool:
    """Return whether one ``TarInfo`` is a real image member worth serving.

    Only regular files qualify. A tar can carry symlinks, hardlinks, device
    nodes and FIFOs, and `TarFile.extractfile` either returns ``None`` for
    them or resolves a link somewhere else on disk -- which for an
    attacker-supplied archive means an absolute path or a `../` escape out
    of the library. The reader never writes these to disk, but it does
    stream the bytes back over an authenticated endpoint, so a link member
    pointing at `/etc/passwd` would be a genuine read primitive. Rejecting
    everything but `isfile()` closes that without needing to reason about
    the link target at all.
    """
    if not member.isfile():
        return False

    return splitext(member.name)[1].lower() in READER_IMAGE_EXTENSIONS


def list_tar_pages(filepath: str) -> List[str]:
    """Return naturally sorted browser-readable image members from TAR/CBT."""
    try:
        with tar_open(filepath, 'r:*') as archive:
            pages = [
                member.name
                for member in archive.getmembers()
                if _is_readable_tar_page(member)
            ]
    except (ReadError, EOFError, OSError):
        LOGGER.warning('Comic reader could not inspect tar archive: %s', filepath)
        return []

    pages.sort(key=natural_sort_key)
    return pages


def read_tar_member(filepath: str, member: str) -> Union[bytes, None]:
    """Read one TAR/CBT member into memory without extracting it to disk."""
    try:
        with tar_open(filepath, 'r:*') as archive:
            info = archive.getmember(member)

            # Re-check on the way out as well as in `list_tar_pages`. The
            # member name arrives from a page descriptor built earlier, and
            # the file on disk may have been replaced in between.
            if not _is_readable_tar_page(info):
                LOGGER.warning(
                    'Comic reader refused non-file tar member %s in %s',
                    member,
                    filepath
                )
                return None

            extracted = archive.extractfile(info)
            if extracted is None:
                return None

            with extracted:
                return extracted.read()

    except (ReadError, EOFError, KeyError, OSError):
        LOGGER.warning(
            'Comic reader could not read tar member %s from %s',
            member,
            filepath
        )
        return None


def read_rar_member(filepath: str, member: str) -> Union[bytes, None]:
    """Read one RAR/CBR member into memory without extracting it permanently."""
    try:
        exe = folder_path(
            'backend',
            'lib',
            RAR_EXECUTABLES[get_os_type()]
        )
        result = run(
            [
                exe,
                'p',       # Print the selected member to stdout.
                '-inul',
                filepath,
                member
            ],
            capture_output=True
        )
    except (KeyError, OSError):
        LOGGER.warning(
            'Comic reader could not read RAR member %s from %s',
            member,
            filepath
        )
        return None

    if result.returncode != 0:
        LOGGER.warning(
            'Comic reader could not read RAR member %s from %s',
            member,
            filepath
        )
        return None

    return result.stdout


def build_pages_for_files(files: List[FileData]) -> List[Dict[str, Any]]:
    """Build ordered image-page descriptors for issue-linked files.

    Loose images become one page each. ZIP/CBZ, RAR/CBR and TAR/CBT files
    contribute browser-readable image members without permanent extraction.
    PDF files are deliberately handled as documents by :func:`find_pdf_file`
    rather than pretending each PDF is one image page.
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
                'archive_type': None,
                'mimetype': guess_type(filepath)[0] or 'image/jpeg'
            })
            continue

        if extension in READER_ZIP_ARCHIVE_EXTENSIONS:
            archive_type = 'zip'
            members = list_archive_pages(filepath)
        elif extension in READER_RAR_ARCHIVE_EXTENSIONS:
            archive_type = 'rar'
            members = list_rar_pages(filepath)
        elif is_tar_archive_file(filepath):
            archive_type = 'tar'
            members = list_tar_pages(filepath)
        else:
            continue

        for member in members:
            pages.append({
                'file_id': file_data['id'],
                'filepath': filepath,
                'member': member,
                'archive_type': archive_type,
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
