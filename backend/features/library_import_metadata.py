# -*- coding: utf-8 -*-

"""Local filesystem evidence for Continuous Library Import.

An organized library often already contains stronger identity evidence than a
filename search can recover. Mylar-style ``series.json`` and ``cvinfo`` files,
for example, carry the ComicVine volume id beside the files they describe. This
module keeps that evidence local and conservative: metadata is only trusted for
the folder that owns it, and never blindly inherited by child folders or
unrelated titles.
"""

from __future__ import annotations

from json import load
from os.path import basename, join, normpath, sep, splitext
from re import fullmatch, search
from typing import Any, Dict, Optional

from backend.base.definitions import FileConstants, FilenameData, SpecialVersion
from backend.base.file_extraction import extract_filename_data
from backend.base.logging import LOGGER
from backend.implementations.matching import match_title


LOCAL_METADATA_FILENAMES = ('series.json', 'metadata.json')
CVINFO_FILENAMES = ('cvinfo', 'cvinfo.txt')
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


def _comicvine_id_from_cvinfo(value: str) -> Optional[int]:
    """Read a ComicVine *volume* id from Mylar/ComicRack ``cvinfo`` text.

    Mylar normally writes the full ComicVine volume URL, while other tools may
    leave either ``4050-NNNN`` or the bare numeric id. Issue URLs (``4000``) and
    story-arc URLs (``4045``) are intentionally rejected.
    """
    value = value.strip()
    direct = _comicvine_id(value)
    if direct is not None:
        return direct

    match = search(r'(?:^|/)(?:4050-)(\d+)(?:/|$)', value)
    if match is None:
        return None

    result = int(match.group(1))
    return result if result > 0 else None


def _metadata_mapping(payload: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(payload, dict):
        return None
    metadata = payload.get('metadata')
    if isinstance(metadata, dict):
        return metadata
    return payload


def _load_json_series_metadata(folder: str) -> Optional[Dict[str, Any]]:
    """Load the richer JSON sidecars produced by Mylar and similar tools."""
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


def _load_cvinfo_metadata(folder: str) -> Optional[Dict[str, Any]]:
    """Load Mylar/ComicRack ``cvinfo`` and derive the local series label.

    ``cvinfo`` itself intentionally contains little more than a ComicVine id.
    The owning folder supplies the title/year evidence used later to ensure the
    id is not accidentally inherited by a different series in an organizer
    folder.
    """
    for filename in CVINFO_FILENAMES:
        path = join(folder, filename)
        try:
            with open(path, 'r', encoding='utf-8-sig') as cvinfo_file:
                value = cvinfo_file.read(4096)
        except (OSError, UnicodeError):
            continue

        comicvine_id = _comicvine_id_from_cvinfo(value)
        if comicvine_id is None:
            continue

        # Use a filename Kapowarr already recognises as metadata so filename
        # parsing deliberately falls back to this folder's label. ``cvinfo`` has
        # no extension and would otherwise be mistaken for the series title.
        folder_data = extract_filename_data(
            join(folder, 'series.json'),
            assume_volume_number=False,
            prefer_folder_year=True,
        )
        name = str(folder_data.get('series') or '').strip()
        if not name:
            name = basename(normpath(folder)).strip()
        if not name:
            continue

        return {
            'comicvine_id': comicvine_id,
            'name': name,
            'year': folder_data.get('year'),
            'volume_number': folder_data.get('volume_number'),
            'issue_count': None,
            'source': filename,
            'path': path,
        }

    return None


def load_local_series_metadata(folder: str) -> Optional[Dict[str, Any]]:
    """Load local metadata that explicitly identifies a ComicVine volume.

    Rich JSON metadata is preferred when present. A Mylar/ComicRack ``cvinfo``
    file is also accepted. If independent local sidecars disagree on the exact
    ComicVine id, neither is trusted for unattended import.
    """
    json_metadata = _load_json_series_metadata(folder)
    cvinfo_metadata = _load_cvinfo_metadata(folder)

    if json_metadata is not None and cvinfo_metadata is not None:
        if json_metadata['comicvine_id'] != cvinfo_metadata['comicvine_id']:
            LOGGER.warning(
                'Conflicting local ComicVine metadata in %s: %s -> %s, %s -> %s',
                folder,
                json_metadata['source'],
                json_metadata['comicvine_id'],
                cvinfo_metadata['source'],
                cvinfo_metadata['comicvine_id'],
            )
            return None

        # Keep the richer JSON fields, but record that cvinfo corroborated it.
        result = dict(json_metadata)
        result['corroborated_by'] = cvinfo_metadata['source']
        return result

    return json_metadata or cvinfo_metadata


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
    title agrees with the sidecar/folder title. For low-information filenames
    such as ``1970_04.pdf``, the folder title may establish that agreement
    instead. This intentionally does *not* map a clearly named, different
    series in an organizer folder to the organizer's sidecar.

    File years are *not* required to match the series start year. Many organized
    libraries put each issue's publication year in the filename; later issues
    therefore legitimately differ from a volume's ComicVine start year.
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
