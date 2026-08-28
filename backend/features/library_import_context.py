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
from backend.features.library_import_policy import (
    AUTO_IMPORT_MIN_MATCH_SCORE,
    AUTO_IMPORT_MIN_SCORE_MARGIN,
)
from backend.implementations.matching import _rank_volume_results_for_file


GroupMap = Dict[int, Dict[str, FilenameData]]
MatchMap = Dict[int, Dict[str, Any]]


def _series_run_key(files: Dict[str, FilenameData]) -> Optional[Tuple[Hashable, ...]]:
    """Return the filename dimensions that must agree for one regular issue run."""
    if not files:
        return None

    first = next(iter(files.values()))
    if first.get('special_version') is not None or first.get('annual'):
        # Run context is intentionally limited to ordinary numbered issues.
        # Annuals can legitimately live beside the main run but have their own
        # numbering and ComicVine volume; leave them on the normal matcher path.
        return None

    series = str(first.get('series') or '').strip().lower()
    if not series:
        return None

    return (
        series,
        first.get('volume_number'),
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


def _highest_issue_number(files: Dict[str, FilenameData]) -> Optional[float]:
    values = [
        float(force_range(file_data['issue_number'])[-1])
        for file_data in files.values()
        if file_data.get('issue_number') is not None
    ]
    return max(values, default=None)


def _select_series_run_winner(
    files: Dict[str, FilenameData],
    search_results: List[VolumeMetadata],
    only_english: bool,
) -> Optional[VolumeMetadata]:
    """Choose a whole-run winner with one extra run-boundary evidence point.

    The reusable matcher already scores title, volume number, start year and
    covered issue count. Folder context adds one narrowly scoped signal: if the
    highest known issue number exactly equals a candidate's total issue count,
    that candidate gets one point. This handles incomplete libraries such as a
    #1-33 run with four missing issues without pretending there are 33 files.
    """
    ranked = _rank_volume_results_for_file(files, search_results, only_english)
    if not ranked:
        return None

    highest_issue = _highest_issue_number(files)
    context_ranked: List[Tuple[VolumeMetadata, int]] = []
    for candidate, base_score in ranked:
        boundary_bonus = 0
        if (
            highest_issue is not None
            and highest_issue > 0
            and highest_issue.is_integer()
            and candidate['issue_count'] == int(highest_issue)
        ):
            boundary_bonus = 1
        context_ranked.append((candidate, base_score + boundary_bonus))

    context_ranked.sort(key=lambda item: item[1], reverse=True)
    winner, best_score = context_ranked[0]
    if best_score < AUTO_IMPORT_MIN_MATCH_SCORE:
        return None

    if len(context_ranked) > 1:
        runner_up_score = context_ranked[1][1]
        if best_score - runner_up_score < AUTO_IMPORT_MIN_SCORE_MARGIN:
            return None

    return winner


def _format_context_match(result: VolumeMetadata) -> Dict[str, Any]:
    year = result.get('year')
    title = result['title']
    external_id = result.get('external_id')
    if external_id is None:
        external_id = result['comicvine_id']
    return {
        'id': result['comicvine_id'],
        # The third place this identity has had to be carried, and the
        # only one that rebuilds a match dict from scratch after the
        # matcher has already made one. #140 taught `Library.add` to take
        # a provider's own ID and #150 taught the review gate to accept
        # one, but a run-context winner *replaces* the match those
        # produced -- so a GCD volume promoted here arrived with `id`
        # None and no identity beside it, failed the gate, and was held
        # as `no-candidate`: "no database in the world has this", about a
        # volume GCD had just named and the policy had just scored 4.
        #
        # It only bites a folder whose files split into two or more
        # groups, which is why it survived the earlier fixes: the
        # single-group folders they were found on never reach this code.
        'provider_id': result.get('provider_id', 'comicvine'),
        'external_id': external_id,
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
    files themselves form one conservative, non-overlapping regular issue
    sequence and the normal continuous-import thresholds accept the combined
    evidence plus the run-boundary signal. Annuals are deliberately excluded.
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

        winner = _select_series_run_winner(
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
