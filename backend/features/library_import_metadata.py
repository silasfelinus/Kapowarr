# -*- coding: utf-8 -*-

"""Local filesystem evidence for Continuous Library Import.

An organized library often already contains stronger identity evidence than a
filename search can recover. Mylar-style ``series.json``/``cvinfo`` files and
standard ``ComicInfo.xml`` metadata can carry a ComicVine volume URL beside or
inside the files they describe. This module keeps that evidence local and
conservative: metadata is only trusted for the folder/group that owns it, and
never blindly inherited by child folders or unrelated titles.
"""

from __future__ import annotations

from json import load
from os.path import basename, join, normpath, sep, splitext
from re import fullmatch, search
from tempfile import TemporaryDirectory
from typing import Any, Dict, List, Optional
from xml.etree.ElementTree import ParseError, fromstring
from zipfile import BadZipFile, ZipFile

from backend.base.definitions import FileConstants, FilenameData, SpecialVersion
from backend.base.file_extraction import extract_filename_data
from backend.base.files import list_files
from backend.base.helpers import run_rar
from backend.base.logging import LOGGER
from backend.implementations.matching import match_title


LOCAL_METADATA_FILENAMES = ('series.json', 'metadata.json')
CVINFO_FILENAMES = ('cvinfo', 'cvinfo.txt', 'cvinfo.xml')
COMICINFO_FILENAME = 'ComicInfo.xml'
MAX_COMICINFO_BYTES = 64 * 1024
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

    # ComicVine uses ``4050-N`` for volume resources. Older Mylar libraries can
    # still contain the historical ``49-N`` volume form, which Mylar itself
    # continues to accept while scanning cvinfo files.
    for prefix in ('4050-', '49-'):
        if value.startswith(prefix) and value[len(prefix):].isdigit():
            result = int(value[len(prefix):])
            return result if result > 0 else None

    return None


def _comicvine_id_from_cvinfo(value: str) -> Optional[int]:
    """Read a ComicVine *volume* id from sidecar text or a URL.

    Mylar normally writes the full ComicVine volume URL, while older libraries
    may contain the historical ``49-NNNN`` form. ``4050-NNNN`` and bare numeric
    ids are accepted too. Issue URLs (``4000``) and story-arc URLs (``4045``)
    are intentionally rejected. XML delimiters are tolerated so legacy
    ``cvinfo.xml`` can be inspected without pretending it is ComicInfo.
    """
    value = value.strip()
    direct = _comicvine_id(value)
    if direct is not None:
        return direct

    match = search(
        r'(?:^|/|>)(?:4050|49)-(\d+)(?=/|<|$)',
        value,
    )
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


def _optional_positive_int(value: Any) -> Optional[int]:
    try:
        result = int(value)
    except (TypeError, ValueError):
        return None
    return result if result > 0 else None


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

        year = _optional_positive_int(metadata.get('year'))
        volume_number = _optional_positive_int(metadata.get('volume'))
        issue_count = _optional_positive_int(metadata.get('total_issues'))

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
    """Load Mylar/ComicRack ``cvinfo`` and derive the local series label."""
    for filename in CVINFO_FILENAMES:
        path = join(folder, filename)
        try:
            with open(path, 'r', encoding='utf-8-sig') as cvinfo_file:
                value = cvinfo_file.read(MAX_COMICINFO_BYTES + 1)
        except (OSError, UnicodeError):
            continue

        if len(value) > MAX_COMICINFO_BYTES:
            LOGGER.warning('Ignoring oversized local metadata file: %s', path)
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


def _parse_comicinfo(
    content: bytes,
    source_path: str,
) -> Optional[Dict[str, Any]]:
    """Return exact volume identity from standard ComicInfo metadata.

    ComicInfo has no dedicated ComicVine ID field. The standard ``Web`` field is
    therefore trusted only when it contains an actual ComicVine *volume* URL.
    Series/year/volume/count remain supporting metadata, never a synthesized ID.
    """
    if len(content) > MAX_COMICINFO_BYTES:
        return None
    try:
        root = fromstring(content)
    except (ParseError, ValueError):
        return None

    if root.tag.rsplit('}', 1)[-1] != 'ComicInfo':
        return None

    fields = {
        child.tag.rsplit('}', 1)[-1]: (child.text or '').strip()
        for child in root
    }
    name = fields.get('Series', '').strip()
    web = fields.get('Web', '').strip()
    comicvine_id = _comicvine_id_from_cvinfo(web)
    if not name or comicvine_id is None:
        return None

    return {
        'comicvine_id': comicvine_id,
        'name': name,
        'year': _optional_positive_int(fields.get('Year')),
        'volume_number': _optional_positive_int(fields.get('Volume')),
        'issue_count': _optional_positive_int(fields.get('Count')),
        'source': COMICINFO_FILENAME,
        'path': source_path,
    }


def _load_sidecar_comicinfo(folder: str) -> Optional[Dict[str, Any]]:
    path = join(folder, COMICINFO_FILENAME)
    try:
        with open(path, 'rb') as comicinfo_file:
            content = comicinfo_file.read(MAX_COMICINFO_BYTES + 1)
    except OSError:
        return None
    if len(content) > MAX_COMICINFO_BYTES:
        LOGGER.warning('Ignoring oversized ComicInfo metadata file: %s', path)
        return None
    return _parse_comicinfo(content, path)


def _archive_member_basename(member: str) -> str:
    """Return an archive member basename independent of archive path syntax."""
    return member.replace('\\', '/').rsplit('/', 1)[-1]


def _read_rar_comicinfo(filepath: str) -> Optional[Dict[str, Any]]:
    """Read one ComicInfo member from a CBR/RAR without modifying the archive.

    Kapowarr's bundled RAR executable first lists members. Only the selected
    ComicInfo member is extracted into an isolated temporary directory. The
    extracted metadata is still subject to the same 64 KiB read ceiling used by
    ZIP/CBZ and sidecar metadata.
    """
    try:
        listing = run_rar(['lb', filepath])
    except (KeyError, OSError):
        return None
    if listing.returncode != 0:
        return None

    member = next((
        name.strip()
        for name in listing.stdout.splitlines()
        if (
            name.strip()
            and _archive_member_basename(name.strip()).casefold()
            == COMICINFO_FILENAME.casefold()
        )
    ), None)
    if member is None:
        return None

    with TemporaryDirectory() as temp_folder:
        try:
            extraction = run_rar([
                'e',
                '-inul',
                '-o+',
                filepath,
                member,
                temp_folder + sep,
            ])
        except (KeyError, OSError):
            return None
        if extraction.returncode != 0:
            return None

        extracted = next((
            path
            for path in list_files(temp_folder)
            if basename(path).casefold() == COMICINFO_FILENAME.casefold()
        ), None)
        if extracted is None:
            return None

        try:
            with open(extracted, 'rb') as comicinfo_file:
                content = comicinfo_file.read(MAX_COMICINFO_BYTES + 1)
        except OSError:
            return None
        if len(content) > MAX_COMICINFO_BYTES:
            LOGGER.warning(
                'Ignoring oversized embedded ComicInfo metadata: %s!%s',
                filepath,
                member,
            )
            return None

    return _parse_comicinfo(content, f'{filepath}!{member}')


def _load_embedded_comicinfo(
    group: Optional[Dict[str, FilenameData]],
) -> Optional[Dict[str, Any]]:
    """Inspect supported comic archives for exact ComicInfo volume identity."""
    if not group:
        return None

    candidates: List[Dict[str, Any]] = []
    for filepath in group:
        extension = splitext(filepath)[1].lower()
        if extension in ('.cbz', '.zip'):
            try:
                with ZipFile(filepath, 'r') as archive:
                    info = next((
                        entry
                        for entry in archive.infolist()
                        if _archive_member_basename(entry.filename).casefold()
                        == COMICINFO_FILENAME.casefold()
                    ), None)
                    if info is None:
                        continue
                    if info.file_size > MAX_COMICINFO_BYTES:
                        LOGGER.warning(
                            'Ignoring oversized embedded ComicInfo metadata: %s!%s',
                            filepath,
                            info.filename,
                        )
                        continue
                    content = archive.read(info)
            except (BadZipFile, OSError, RuntimeError, ValueError):
                continue

            metadata = _parse_comicinfo(
                content,
                f'{filepath}!{info.filename}',
            )
        elif extension in ('.cbr', '.rar'):
            metadata = _read_rar_comicinfo(filepath)
        else:
            continue

        if metadata is not None:
            candidates.append(metadata)

    if not candidates:
        return None

    ids = {candidate['comicvine_id'] for candidate in candidates}
    if len(ids) > 1:
        LOGGER.warning(
            'Conflicting embedded ComicInfo volume identities: %s',
            ', '.join(
                f"{candidate['path']} -> {candidate['comicvine_id']}"
                for candidate in candidates
            ),
        )
        return None

    result = dict(candidates[0])
    if len(candidates) > 1:
        result['corroborated_by'] = f'{len(candidates) - 1} other ComicInfo file(s)'
    return result


def load_local_series_metadata(
    folder: str,
    group: Optional[Dict[str, FilenameData]] = None,
) -> Optional[Dict[str, Any]]:
    """Load local metadata that explicitly identifies a ComicVine volume.

    Mylar JSON/cvinfo and standard ComicInfo evidence are accepted only when
    they identify an exact ComicVine volume. If independent local sources
    disagree on that ID, none are trusted for unattended import.
    """
    candidates = [
        metadata
        for metadata in (
            _load_json_series_metadata(folder),
            _load_sidecar_comicinfo(folder),
            _load_embedded_comicinfo(group),
            _load_cvinfo_metadata(folder),
        )
        if metadata is not None
    ]
    if not candidates:
        return None

    ids = {metadata['comicvine_id'] for metadata in candidates}
    if len(ids) > 1:
        LOGGER.warning(
            'Conflicting local ComicVine metadata in %s: %s',
            folder,
            ', '.join(
                f"{metadata['source']} -> {metadata['comicvine_id']}"
                for metadata in candidates
            ),
        )
        return None

    # Prefer richer series-level JSON, then ComicInfo, then bare cvinfo. All
    # candidates have already agreed on the exact external identity.
    result = dict(candidates[0])
    corroborators = [
        metadata['source']
        for metadata in candidates[1:]
    ]
    if corroborators:
        existing = result.get('corroborated_by')
        if existing:
            corroborators.insert(0, str(existing))
        result['corroborated_by'] = ', '.join(corroborators)
    return result


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

    A sidecar/archive metadata record belongs only to its own folder/group. We
    accept it when the parsed group title agrees with the local metadata title.
    For low-information filenames such as ``1970_04.pdf``, the folder title may
    establish that agreement instead. File years are *not* required to equal a
    series start year because issue publication years legitimately drift.
    """
    metadata = load_local_series_metadata(folder, group)
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
        LOGGER.info(
            'Library import found local %s ComicVine ID %s in %s but did not '
            'apply it to parsed series %r because it did not safely match %r',
            metadata.get('source', 'metadata'),
            metadata['comicvine_id'],
            folder,
            group_series,
            metadata['name'],
        )
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
