# -*- coding: utf-8 -*-

"""Normalize downloaded multi-issue packs into their useful issue files.

A search result such as ``Series 001-100`` should preserve its range through
queueing, but a ZIP/RAR that contains individual CBZ/CBR/PDF issue containers
should not remain one giant file linked to every issue. This module detects that
specific shape and extracts only issue files that can fill currently missing
issues. Monolithic comic archives containing page images are left untouched.
"""

from __future__ import annotations

from os.path import exists, isfile, splitext
from typing import List, Set, Tuple, Union
from zipfile import BadZipFile, ZipFile

from backend.base.definitions import Constants, FileConstants
from backend.base.file_extraction import extract_filename_data
from backend.base.files import (create_folder, delete_file_folder,
                                generate_archive_folder, list_files)
from backend.base.helpers import force_range, run_rar
from backend.base.logging import LOGGER
from backend.implementations.converters import extract_files_from_folder
from backend.implementations.volumes import Volume

ZIP_PACK_EXTENSIONS = ('.zip', '.cbz')
RAR_PACK_EXTENSIONS = ('.rar', '.cbr')
PACK_EXTENSIONS = ZIP_PACK_EXTENSIONS + RAR_PACK_EXTENSIONS

IssueNumber = Union[float, Tuple[float, float], None]


def _archive_members(filepath: str) -> Union[List[str], None]:
    """List archive members when the pack can be inspected safely."""
    extension = splitext(filepath)[1].lower()

    if extension in ZIP_PACK_EXTENSIONS:
        try:
            with ZipFile(filepath, 'r') as archive:
                entries = archive.infolist()
                # Do not auto-normalize encrypted packs. Listing can succeed
                # while extraction later requires a password.
                if any(entry.flag_bits & 0x1 for entry in entries):
                    return None
                return [
                    entry.filename
                    for entry in entries
                    if not entry.is_dir()
                ]
        except (BadZipFile, OSError):
            return None

    if extension in RAR_PACK_EXTENSIONS:
        try:
            result = run_rar([
                'lb',
                '-inul',
                filepath
            ])
        except (KeyError, OSError):
            return None

        if result.returncode != 0:
            return None
        return [member for member in result.stdout.splitlines() if member]

    return None


def archive_contains_complete_issues(filepath: str) -> bool:
    """Return whether an archive contains nested comic issue containers."""
    members = _archive_members(filepath)
    if members is None:
        return False

    return any(
        splitext(member)[1].lower() in FileConstants.CONTAINER_EXTENSIONS
        for member in members
    )


def issue_number_overlaps_missing(
    issue_number: IssueNumber,
    missing_issue_numbers: Set[float]
) -> bool:
    """Return whether a parsed issue number/range can fill a missing issue."""
    if issue_number is None:
        return True

    start, end = force_range(issue_number)
    return any(start <= number <= end for number in missing_issue_numbers)


def _missing_issue_numbers(download) -> Set[float]:
    volume = Volume(download.volume_id)
    return {
        issue.calculated_issue_number
        for issue in volume.get_issues()
        if not issue.files
    }


def prune_downloaded_range_files(download) -> int:
    """Discard clearly already-owned individual issue files from a range.

    This complements outer-pack extraction. Torrent/NZB releases often arrive
    as a directory containing issue CBZ/CBR/PDF files directly, without a
    ``1-100.zip`` wrapper. For a download whose search metadata is a range,
    remove individual containers that cannot fill any currently missing issue.
    Ambiguous files and monolithic range archives are retained so normal
    volume-aware matching remains the final authority.

    Returns:
        int: Number of files removed.
    """
    if not isinstance(getattr(download, 'covered_issues', None), tuple):
        return 0

    missing_issue_numbers = _missing_issue_numbers(download)
    kept: List[str] = []
    removed = 0

    for filepath in download.files:
        if (
            not isfile(filepath)
            or splitext(filepath)[1].lower()
            not in FileConstants.CONTAINER_EXTENSIONS
        ):
            kept.append(filepath)
            continue

        file_data = extract_filename_data(
            filepath,
            assume_volume_number=False
        )
        issue_number = file_data['issue_number']
        if issue_number is None or issue_number_overlaps_missing(
            issue_number,
            missing_issue_numbers
        ):
            kept.append(filepath)
            continue

        delete_file_folder(filepath)
        removed += 1
        LOGGER.info(
            'Discarded already-owned issue file from downloaded range: %s',
            filepath
        )

    if removed:
        download.files = kept
    return removed


def _extract_archive(filepath: str, target_folder: str) -> bool:
    """Extract an archive without deleting the source when extraction fails."""
    extension = splitext(filepath)[1].lower()
    if exists(target_folder):
        delete_file_folder(target_folder)

    if extension in ZIP_PACK_EXTENSIONS:
        try:
            with ZipFile(filepath, 'r') as archive:
                archive.extractall(target_folder)
        except (BadZipFile, OSError, RuntimeError):
            delete_file_folder(target_folder)
            return False
        return True

    if extension in RAR_PACK_EXTENSIONS:
        create_folder(target_folder)
        try:
            result = run_rar([
                'x',
                '-inul',
                filepath,
                target_folder
            ])
        except (KeyError, OSError):
            delete_file_folder(target_folder)
            return False

        if result.returncode != 0:
            delete_file_folder(target_folder)
            return False
        return True

    return False


def _remove_already_owned_issues(
    extraction_folder: str,
    missing_issue_numbers: Set[float]
) -> int:
    """Delete clearly already-owned issue containers from an extracted pack.

    Unknown/ambiguous filenames are retained so Kapowarr's normal volume-aware
    extraction filter gets the final say. The return value is the count of
    candidate issue containers left after pruning.
    """
    candidates = 0
    for filepath in list_files(
        extraction_folder,
        FileConstants.SCANNABLE_EXTENSIONS
    ):
        if splitext(filepath)[1].lower() not in FileConstants.CONTAINER_EXTENSIONS:
            continue

        file_data = extract_filename_data(
            filepath.replace(Constants.ARCHIVE_EXTRACT_FOLDER + '_', ''),
            assume_volume_number=False
        )
        issue_number = file_data['issue_number']

        if issue_number is not None and not issue_number_overlaps_missing(
            issue_number,
            missing_issue_numbers
        ):
            delete_file_folder(filepath)
            continue

        candidates += 1

    return candidates


def normalize_downloaded_range_pack(download) -> bool:
    """Split a downloaded range pack when it contains complete issue files.

    Only downloads whose search metadata carried a real issue range are
    considered. Existing library issues are checked before the new pack is
    scanned, so issue containers already owned are discarded rather than
    duplicated. A page-image CBZ/CBR, unreadable archive, or ambiguous
    non-container archive is kept unchanged.

    Returns:
        bool: Whether at least one outer pack archive was normalized/removed.
    """
    if not isinstance(getattr(download, 'covered_issues', None), tuple):
        return False

    volume = Volume(download.volume_id)
    missing_issue_numbers = _missing_issue_numbers(download)

    normalized_files: List[str] = []
    changed = False

    for filepath in download.files:
        if (
            not isfile(filepath)
            or splitext(filepath)[1].lower() not in PACK_EXTENSIONS
            or not archive_contains_complete_issues(filepath)
        ):
            normalized_files.append(filepath)
            continue

        extraction_folder = generate_archive_folder(volume.vd.folder, filepath)
        if not _extract_archive(filepath, extraction_folder):
            LOGGER.warning(
                'Could not safely extract downloaded issue pack: %s',
                filepath
            )
            normalized_files.append(filepath)
            continue

        candidate_count = _remove_already_owned_issues(
            extraction_folder,
            missing_issue_numbers
        )
        if candidate_count:
            extracted_files = extract_files_from_folder(
                extraction_folder,
                download.volume_id
            )
            normalized_files.extend(extracted_files)
        else:
            # Nothing in this pack can improve the current library. Avoid the
            # normal extraction helper's intentional "keep everything" fallback.
            delete_file_folder(extraction_folder)

        delete_file_folder(filepath)
        changed = True
        LOGGER.info(
            'Normalized downloaded issue pack %s into %d useful file(s)',
            filepath,
            len(normalized_files)
        )

    if changed:
        download.files = normalized_files
        download._normalized_range_pack = True

    return changed
