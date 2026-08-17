# -*- coding: utf-8 -*-

"""Local filesystem evidence for Continuous Library Import.

An organized library often already contains stronger identity evidence than a
filename search can recover. Mylar-style ``series.json`` files, for example,
carry the ComicVine volume id beside the files they describe. This module keeps
that evidence local and conservative: metadata is only trusted for the folder
that owns it, and never blindly inherited by child folders or unrelated titles.
"""

from __future__ import annotations

from json import load
from os.path import basename, join, normpath, sep, splitext
from re import fullmatch
from typing import Any, Dict, Optional

from backend.base.definitions import FileConstants, FilenameData, SpecialVersion
from backend.base.file_extraction import extract_filename_data
from backend.base.logging import LOGGER
from backend.implementations.matching import match_title


LOCAL_METADATA_FILENAMES = ('series.json', 'metadata.json')
LOCAL_COMICVINE_ID_KEYS = (
    'comicid',
    'comicvine_id',
    'comicvineId',
    'comicvine_volume_id',
    'ComicVineId',
)


def _comicvine_id(value: Any) -> Optional[int]:
    """Return a positive ComicVine volume id from common sidecar encodings."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if not isinstance(value, str):
        return None

    value = value.strip()
    if value.isdigit():
        result = int(value)
        return result if result > 0 else None

    # ComicVine object references are often written as ``4050-12345`` where
    # 4050 identifies a volume resource and the trailing number is its id.
    if value.startswith('4050-') and value[5:].isdigit():
        result = int(value[5:])
        return result if result > 0 else None

    return None


def _metadata_mapping(payload: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(payload, dict):
        return None
    metadata = payload.get('metadata')
    if isinstance(metadata, dict):
        return metadata
    return payload


def load_local_series_metadata(folder: str) -> Optional[Dict[str, Any]]:
    """Load a sidecar that explicitly identifies a ComicVine volume.

    ``series.json`` is preferred. ``metadata.json`` is also accepted, but only
    when it exposes an explicit ComicVine id and a series/title name. Unknown
    JSON schemas are ignored rather than guessed.
    """
    for filename in LOCAL_METADATA_FILENAMES:
        path = join(folder, filename)
        try:
            with open(path, 'r', encoding='utf-8-sig') as metadata_file:
                payload = load(metadata_file)
        except (OSError, UnicodeError, ValueError):
            continue

        metadata = _metadata_mapping(payload)
        if metadata is None:
            continue

        comicvine_id = next((
            parsed
            for key in LOCAL_COMICVINE_ID_KEYS
            if (parsed := _comicvine_id(metadata.get(key))) is not None
        ), None)
        name = next((
            value.strip()
            for key in ('name', 'series', 'title')
            if isinstance((value := metadata.get(key)), str) and value.strip()
        ), None)
        if comicvine_id is None or name is None:
            continue

        year = metadata.get('year')
        try:
            year = int(year) if year not in (None, '') else None
        except (TypeError, ValueError):
            year = None

        volume_number = metadata.get('volume')
        try:
            volume_number = (
                int(volume_number)
                if volume_number not in (None, '')
                else None
            )
        except (TypeError, ValueError):
            volume_number = None

        issue_count = metadata.get('total_issues')
        try:
            issue_count = int(issue_count) if issue_count not in (None, '') else None
        except (TypeError, ValueError):
            issue_count = None

        return {
            'comicvine_id': comicvine_id,
            'name': name,
            'year': year,
            'volume_number': volume_number,
            'issue_count': issue_count,
            'source': filename,
            'path': path,
        }

    return None


def _series_is_low_information(series: str) -> bool:
    """Identify filename-derived labels that are mostly dates/issue numbers."""
    compact = series.strip().replace('_', ' ').replace('-', ' ')
    compact = ' '.join(compact.split())
    return bool(fullmatch(r'\d{1,4}(?:\s+\d{1,4})*', compact))


def select_local_series_metadata(
    folder: str,
    group: Dict[str, FilenameData]
) -> Optional[Dict[str, Any]]:
    """Return trusted local volume identity for one filename group.

    A sidecar belongs only to its own folder. We accept it when the parsed group
    title agrees with the sidecar title. For low-information filenames such as
    ``1970_04.pdf``, the folder title may establish that agreement instead. This
    intentionally does *not* map a clearly named, different series in an
    organizer folder to the organizer's sidecar.
    """
    metadata = load_local_series_metadata(folder)
    if metadata is None or not group:
        return None

    first_file = next(iter(group.values()))
    group_series = str(first_file.get('series') or '').strip()
    title_agrees = bool(group_series and match_title(metadata['name'], group_series))

    if not title_agrees and _series_is_low_information(group_series):
        folder_data = extract_filename_data(
            join(folder, 'series.json'),
            assume_volume_number=False,
            prefer_folder_year=True,
        )
        folder_series = str(folder_data.get('series') or '').strip()
        title_agrees = bool(
            folder_series and match_title(metadata['name'], folder_series)
        )

        folder_year = folder_data.get('year')
        if (
            title_agrees
            and folder_year is not None
            and metadata['year'] is not None
            and folder_year != metadata['year']
        ):
            title_agrees = False

    if not title_agrees:
        return None

    return metadata


def is_library_import_artifact(filepath: str) -> bool:
    """Return whether a path is library decoration/cache, not comic content."""
    normalized = normpath(filepath)
    components = [part for part in normalized.split(sep) if part]
    if any(part.startswith('.') and part not in ('.', '..') for part in components):
        return True

    extension = splitext(basename(filepath))[1]
    if extension not in FileConstants.IMAGE_EXTENSIONS:
        return False

    file_data = extract_filename_data(filepath, prefer_folder_year=True)
    return file_data['special_version'] == SpecialVersion.COVER


def filter_library_import_files(
    files: Dict[str, FilenameData]
) -> Dict[str, FilenameData]:
    """Remove decoration/cache paths while preserving real page-image comics."""
    filtered = {
        filepath: file_data
        for filepath, file_data in files.items()
        if not is_library_import_artifact(filepath)
    }
    removed = len(files) - len(filtered)
    if removed:
        LOGGER.debug('Ignored %d library import artwork/cache path(s)', removed)
    return filtered
