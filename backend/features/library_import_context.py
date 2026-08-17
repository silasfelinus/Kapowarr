# -*- coding: utf-8 -*-

"""Folder-level context for Continuous Library Import matching.

Filename groups intentionally keep publication years separate. That is useful
for parsing, but a long-running series often has each issue tagged with its own
publication year while ComicVine's volume year is only the series start year.
This module lets an unambiguous single issue run vote as a whole before held
subgroups are sent to review.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, Hashable, List, Optional, Tuple

from backend.base.definitions import FilenameData, VolumeMetadata
from backend.base.helpers import check_overlapping_issues, force_range
from backend.features.library_import_policy import select_auto_import_volume_result


GroupMap = Dict[int, Dict[str, FilenameData]]
MatchMap = Dict[int, Dict[str, Any]]


def _series_run_key(files: Dict[str, FilenameData]) -> Optional[Tuple[Hashable, ...]]:
    """Return the filename dimensions that must agree for one issue run."""
    if not files:
        return None

    first = next(iter(files.values()))
    if first.get('special_version') is not None:
        # Run context is intentionally limited to ordinary numbered issues.
        return None

    series = str(first.get('series') or '').strip().lower()
    if not series:
        return None

    return (
        series,
        first.get('volume_number'),
        first.get('annual'),
    )


def _is_single_issue_run(groups: List[Dict[str, FilenameData]]) -> bool:
    """Require one non-overlapping numbered run with exactly one issue #1.

    Requiring a single issue #1 is a deliberate guard against organizer folders
    that contain two different same-titled volumes whose numbering restarts.
    Missing/ambiguous issue numbers disable the optimization rather than making
    an unattended guess.
    """
    issue_ranges: List[Tuple[float, float]] = []
    issue_one_count = 0

    for files in groups:
        for file_data in files.values():
            issue_number = file_data.get('issue_number')
            if issue_number is None:
                return False

            current = force_range(issue_number)
            current_range = (float(current[0]), float(current[-1]))
            if current_range[0] <= 1.0 <= current_range[1]:
                issue_one_count += 1

            if any(
                check_overlapping_issues(current_range, previous)
                for previous in issue_ranges
            ):
                return False
            issue_ranges.append(current_range)

    return issue_one_count == 1 and len(issue_ranges) >= 3


def _combined_files(groups: List[Dict[str, FilenameData]]) -> Dict[str, FilenameData]:
    result: Dict[str, FilenameData] = {}
    for files in groups:
        result.update(files)
    return result


def _format_context_match(result: VolumeMetadata) -> Dict[str, Any]:
    year = result.get('year')
    title = result['title']
    return {
        'id': result['comicvine_id'],
        'title': f'{title} ({year})' if year is not None else title,
        'issue_count': result['issue_count'],
        'link': result['site_url'],
        'series_context': True,
    }


def apply_series_run_context(
    file_groups: GroupMap,
    group_matches: MatchMap,
    search_cache: Dict[str, List[VolumeMetadata]],
    only_english: bool,
) -> MatchMap:
    """Promote a confident whole-run winner across its publication-year groups.

    The same cached ComicVine search response is reused, so this adds no API
    traffic. A whole-run winner replaces subgroup suggestions only when the
    files themselves form one conservative, non-overlapping issue sequence and
    the existing continuous-import confidence policy accepts the combined
    evidence.
    """
    clusters: Dict[Tuple[Hashable, ...], List[int]] = defaultdict(list)
    for group_number, files in file_groups.items():
        key = _series_run_key(files)
        if key is not None:
            clusters[key].append(group_number)

    result_matches = dict(group_matches)
    for key, group_numbers in clusters.items():
        if len(group_numbers) < 2:
            continue

        groups = [file_groups[group_number] for group_number in group_numbers]
        if not _is_single_issue_run(groups):
            continue

        title = str(key[0])
        search_results = search_cache.get(title, [])
        if not search_results:
            continue

        winner, _ = select_auto_import_volume_result(
            _combined_files(groups),
            search_results,
            only_english=only_english,
        )
        if winner is None:
            continue

        context_match = _format_context_match(winner)
        for group_number in group_numbers:
            result_matches[group_number] = dict(context_match)

    return result_matches
