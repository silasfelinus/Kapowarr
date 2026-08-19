# -*- coding: utf-8 -*-

"""Explain known file-quality traits without pretending to know more than we do.

This module intentionally avoids a single aggregate score. It reports where a
library file sits in the user's explicit format/source preferences and preserves
explicit GetComics HD/SD labels when provenance supports them. Unknown evidence
stays unknown instead of receiving a made-up penalty or bonus.
"""

from __future__ import annotations

from os.path import splitext
from typing import Any, Dict, List, Optional, Sequence

from backend.features.acquisition_preferences import (
    get_acquisition_preferences,
    getcomics_quality_label,
)
from backend.features.file_provenance import get_volume_file_provenance
from backend.internals.settings import Settings


_DIRECT_SOURCE_TYPES = {
    'Mega',
    'MediaFire',
    'WeTransfer',
    'Pixeldrain',
    'GetComics',
}


def source_protocol(source_type: Optional[str]) -> Optional[str]:
    """Map persisted source labels to the acquisition preference vocabulary."""
    if not source_type:
        return None

    normalized = source_type.strip().casefold()
    if source_type in _DIRECT_SOURCE_TYPES:
        return 'direct'
    if 'torrent' in normalized or 'torznab' in normalized:
        return 'torrent'
    if 'usenet' in normalized or 'newznab' in normalized or 'nzb' in normalized:
        return 'usenet'
    return None


def _preference_position(value: Optional[str], preference: Sequence[str]) -> Optional[int]:
    if value is None:
        return None
    try:
        return list(preference).index(value) + 1
    except ValueError:
        return None


def _file_format(filepath: str) -> Optional[str]:
    extension = splitext(filepath)[1].lower().lstrip('.')
    return extension or None


def explain_file_quality(
    file_data: Dict[str, Any],
    *,
    format_preference: Optional[Sequence[str]] = None,
    source_preference: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """Return only evidence-backed quality/preference traits for one file."""
    if format_preference is None:
        format_preference = list(Settings().sv.format_preference)
    if source_preference is None:
        source_preference = get_acquisition_preferences()[
            'acquisition_source_preference'
        ]

    file_format = _file_format(file_data['filepath'])
    protocol = source_protocol(file_data.get('source_type'))
    explicit_quality = None
    if file_data.get('source_type') in ('GetComics', 'GetComics (torrent)'):
        label = getcomics_quality_label(file_data.get('web_sub_title') or '')
        if label != 'unknown':
            explicit_quality = label

    format_rank = _preference_position(file_format, format_preference)
    source_rank = _preference_position(protocol, source_preference)

    traits: List[str] = []
    if file_format:
        if format_rank is not None:
            traits.append(
                f'{file_format.upper()} is format preference #{format_rank}'
            )
        else:
            traits.append(f'{file_format.upper()} format')

    if protocol:
        if source_rank is not None:
            traits.append(
                f'{protocol.title()} is source preference #{source_rank}'
            )
        else:
            traits.append(f'{protocol.title()} acquisition')

    if explicit_quality is not None:
        traits.append(f'GetComics {explicit_quality.upper()} label')

    if file_data.get('source_name'):
        traits.append(f"Source: {file_data['source_name']}")

    return {
        **file_data,
        'format': file_format,
        'format_preference_rank': format_rank,
        'source_protocol': protocol,
        'source_preference_rank': source_rank,
        'explicit_quality': explicit_quality,
        'traits': traits,
        'comparison_ready': bool(
            format_rank is not None
            or source_rank is not None
            or explicit_quality is not None
        ),
    }


def explain_volume_file_quality(volume_id: int) -> List[Dict[str, Any]]:
    """Explain every registered file in a volume with one settings snapshot."""
    format_preference = list(Settings().sv.format_preference)
    source_preference = get_acquisition_preferences()[
        'acquisition_source_preference'
    ]
    return [
        explain_file_quality(
            file_data,
            format_preference=format_preference,
            source_preference=source_preference,
        )
        for file_data in get_volume_file_provenance(volume_id)
    ]
