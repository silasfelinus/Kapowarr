# -*- coding: utf-8 -*-

"""Portable series metadata generation and preservation-aware write-back.

The first write-back format deliberately targets Mylar-compatible ``series.json``
at the volume-folder level. Existing metadata is preserved by default; archive
mutation is intentionally outside this module.
"""

from __future__ import annotations

from html.parser import HTMLParser
from json import dumps
from os import O_CREAT, O_EXCL, O_WRONLY, fdopen, fsync, open as os_open
from os import remove, replace
from os.path import dirname, exists, isdir, join
from tempfile import NamedTemporaryFile
from typing import Any, Dict, Optional

from backend.base.definitions import SpecialVersion
from backend.base.logging import LOGGER
from backend.features.metadata import MetadataIdentityStore
from backend.implementations.file_matching import scan_files
from backend.implementations.volumes import Volume


SERIES_JSON_FILENAME = 'series.json'
SERIES_JSON_VERSION = '1.0.2'


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_data(self, data: str) -> None:
        if data.strip():
            self.parts.append(data.strip())

    def text(self) -> str:
        return ' '.join(self.parts)


def _plain_description(value: str) -> str:
    parser = _TextExtractor()
    parser.feed(value or '')
    parser.close()
    return parser.text()


def _booktype(special_version: SpecialVersion) -> str:
    return {
        SpecialVersion.ONE_SHOT: 'One-Shot',
        SpecialVersion.TPB: 'TPB',
        SpecialVersion.HARD_COVER: 'Hard-Cover',
        SpecialVersion.OMNIBUS: 'Omnibus',
    }.get(special_version, 'Print')


def build_series_json(volume_id: int) -> Dict[str, Any]:
    """Build a conservative Mylar-compatible series metadata payload.

    Only fields Kapowarr actually knows are populated. Lifecycle, imprint,
    age-rating and collection values remain unknown rather than being inferred.
    Provider-neutral identities stay durable in Kapowarr's database; the legacy
    ``comicid`` field is populated only when the volume truly has ComicVine
    identity.
    """
    volume = Volume(volume_id, check_existence=True)
    data = volume.get_data()
    issues = volume.get_issues(_skip_files=True)
    external_ids = MetadataIdentityStore.get('volume', volume_id)
    comicvine_id: Optional[int]
    try:
        comicvine_id = int(external_ids['comicvine'])
    except (KeyError, TypeError, ValueError):
        comicvine_id = data.comicvine_id

    return {
        'version': SERIES_JSON_VERSION,
        'metadata': {
            'type': 'comicSeries',
            'publisher': data.publisher,
            'imprint': None,
            'name': data.title,
            'comicid': comicvine_id,
            'year': data.year,
            'description_text': _plain_description(data.description),
            'description_formatted': data.description or '',
            'volume': data.volume_number or None,
            'booktype': _booktype(data.special_version),
            'age_rating': None,
            'collects': None,
            'comic_image': None,
            'total_issues': len(issues),
            'publication_run': None,
            'status': 'Unknown',
        },
    }


def preview_series_json(volume_id: int) -> Dict[str, Any]:
    """Return generated metadata and filesystem preservation status."""
    volume = Volume(volume_id, check_existence=True)
    path = join(volume.vd.folder, SERIES_JSON_FILENAME)
    return {
        'path': path,
        'folder_exists': isdir(volume.vd.folder),
        'exists': exists(path),
        'payload': build_series_json(volume_id),
    }


def serialized_series_json(volume_id: int) -> str:
    return dumps(
        build_series_json(volume_id),
        indent=4,
        ensure_ascii=False,
    ) + '\n'


def _exclusive_create(path: str, content: str) -> bool:
    """Create ``path`` without ever clobbering a concurrently created file."""
    try:
        fd = os_open(path, O_WRONLY | O_CREAT | O_EXCL, 0o644)
    except FileExistsError:
        return False

    with fdopen(fd, 'w', encoding='utf-8') as handle:
        handle.write(content)
        handle.flush()
        fsync(handle.fileno())
    return True


def _atomic_replace(path: str, content: str) -> None:
    """Explicit overwrite path: write beside the target, then atomically swap."""
    temp_path = ''
    try:
        with NamedTemporaryFile(
            'w',
            encoding='utf-8',
            dir=dirname(path),
            prefix='.series.json.',
            suffix='.tmp',
            delete=False,
        ) as handle:
            temp_path = handle.name
            handle.write(content)
            handle.flush()
            fsync(handle.fileno())
        replace(temp_path, path)
        temp_path = ''
    finally:
        if temp_path and exists(temp_path):
            remove(temp_path)


def write_series_json(volume_id: int, overwrite: bool = False) -> Dict[str, Any]:
    """Write portable series metadata while preserving existing files by default."""
    volume = Volume(volume_id, check_existence=True)
    folder = volume.vd.folder
    path = join(folder, SERIES_JSON_FILENAME)

    if not isdir(folder):
        return {
            'path': path,
            'written': False,
            'reason': 'folder_missing',
            'exists': False,
        }

    existed = exists(path)
    if existed and not overwrite:
        return {
            'path': path,
            'written': False,
            'reason': 'existing_preserved',
            'exists': True,
        }

    content = serialized_series_json(volume_id)

    if overwrite:
        _atomic_replace(path, content)
        reason = 'overwritten' if existed else 'created'
    else:
        if not _exclusive_create(path, content):
            return {
                'path': path,
                'written': False,
                'reason': 'existing_preserved',
                'exists': True,
            }
        reason = 'created'

    # Keep Kapowarr's database view in sync with the newly materialized metadata.
    scan_files(volume_id, filepath_filter=[path])
    LOGGER.info('Portable series metadata %s: %s', reason, path)
    return {
        'path': path,
        'written': True,
        'reason': reason,
        'exists': True,
    }
