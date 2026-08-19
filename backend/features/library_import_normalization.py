# -*- coding: utf-8 -*-

"""Small, import-specific filename normalization helpers.

Existing comic libraries sometimes prefix filenames with a shelf/order number,
for example ``001.) ElfQuest - Hidden Years #6``. The generic filename parser
correctly finds the issue number but historically kept ``001.)`` in the series
name. Continuous import would then treat every ordered file as a separate series
and spend a paced ComicVine search on each one.

Keep this repair local to Library Import rather than broadening the generic
parser for every caller.
"""

from __future__ import annotations

from os.path import basename
from re import compile
from typing import Dict

from backend.base.definitions import FilenameData


_ORDER_PREFIX = compile(r'^\s*\d{1,5}(?:\.\)|\))\s+')
_TRAILING_FOLDER_YEAR = compile(r'\s*\(\d{4}\)\s*$')


def normalize_import_series_name(series: str) -> str:
    """Remove an explicit numeric shelf-order prefix from a parsed series."""
    original = str(series or '').strip()
    normalized = _ORDER_PREFIX.sub('', original).strip()
    return normalized or original


def normalize_import_filename_data(
    files: Dict[str, FilenameData]
) -> Dict[str, FilenameData]:
    """Return filename data with import-only series cleanup applied.

    Values are copied only when a series actually changes so ordinary imports
    keep their existing objects and evidence untouched.
    """
    normalized_files: Dict[str, FilenameData] = {}
    for filepath, file_data in files.items():
        series = normalize_import_series_name(file_data['series'])
        if series == file_data['series']:
            normalized_files[filepath] = file_data
            continue

        cleaned = file_data.copy()
        cleaned['series'] = series
        normalized_files[filepath] = cleaned

    return normalized_files


def folder_search_query(folder: str) -> str:
    """Build a conservative broad-search seed from a folder basename."""
    name = _TRAILING_FOLDER_YEAR.sub('', basename(folder)).strip()
    return normalize_import_series_name(name).lower()
