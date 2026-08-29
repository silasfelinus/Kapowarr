# -*- coding: utf-8 -*-

"""Postmortem diagnostics for Continuous Library Import review holds.

The importer already has the full ComicVine search response and its ranked
viable candidates in memory when it decides to hold a group for review. Capture
that evidence once, without making another API request, so live-library tuning
can be based on real ambiguous and false-review cases.
"""

from __future__ import annotations

from enum import Enum
from json import dumps
from os import listdir
from os.path import dirname, isdir, isfile, join
from time import time
from typing import Any, Dict, List, Optional

from backend.base.definitions import (FileConstants, FilenameData,
                                      VolumeMetadata)
from backend.base.logging import LOGGER
from backend.features.library_import_metadata import (
    is_library_import_artifact)
from backend.features.library_import_policy import (
    AUTO_IMPORT_MIN_MATCH_SCORE,
    AUTO_IMPORT_MIN_SCORE_MARGIN,
    _policy_score,
)
from backend.implementations.matching import _rank_volume_results_for_file
from backend.internals.db import DBConnection


POSTMORTEM_FILENAME = 'library_import_review_postmortem.jsonl'

# Per provider, not overall. `search_volumes_everywhere` returns ComicVine's
# results first and appends each fallback's after them, and ComicVine answers
# almost any query with fifty rows. A flat cap of 25 therefore truncated the
# record at ComicVine every time a fallback was reached, so the postmortem
# could never show what GCD or Metron returned -- the exact case the fan-out
# exists for was the one case it could not describe. Keeping a budget per
# provider means every provider that answered is represented.
RAW_SEARCH_CAPTURE_LIMIT = 25


def _json_safe(value: Any) -> Any:
    """Convert filename/parser values to JSON-safe primitives."""
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    return value


def _unpacked_comic_snapshot(filepath: str) -> Optional[Dict[str, Any]]:
    """For a work item that is a directory, say what made it one.

    An unpacked page-image comic is represented by its own directory, so a
    record's only "file" is a folder path. Nothing said so, and nothing said
    how many images were behind the decision -- which is how a single stray
    banner sitting in a folder of subfolders could promote that folder to a
    volume search and produce a tie across every same-titled volume a
    provider has, on pass after pass, while the record looked exactly like a
    record for a real comic. Eight of job 18's nine ties were this, and
    telling them apart meant noticing that `files[0].filepath` equalled
    `folder`.
    """
    if not isdir(filepath):
        return None

    images = 0
    archives = 0
    try:
        for name in listdir(filepath):
            if not isfile(join(filepath, name)):
                continue
            if name.lower().endswith(FileConstants.IMAGE_EXTENSIONS):
                if not is_library_import_artifact(join(filepath, name)):
                    images += 1
            elif name.lower().endswith(FileConstants.CONTAINER_EXTENSIONS):
                archives += 1
    except OSError:
        return None

    return {
        'is_directory': True,
        'page_images': images,
        'archives': archives
    }


def _candidate_identity(result: VolumeMetadata) -> Any:
    """Identify a candidate the way the importer does.

    `comicvine_id` is `None` for every GCD result and for any Metron
    result without a cross-reference, so a score map keyed on it put all
    of them in one `None` bucket and handed each the last one's score.
    Every non-ComicVine candidate in the postmortem carried a number
    belonging to some other volume.
    """
    external_id = result.get('external_id')
    if external_id is None:
        external_id = result['comicvine_id']
    return (result.get('provider_id') or 'comicvine', external_id)


def _candidate_snapshot(
    result: VolumeMetadata,
    score: Optional[int] = None
) -> Dict[str, Any]:
    """Keep useful provider metadata while excluding covers/issue payloads."""
    snapshot: Dict[str, Any] = {
        'comicvine_id': result['comicvine_id'],
        # Which database actually returned this row. Without it a reader
        # cannot tell a ComicVine miss from a fallback that was never
        # consulted, and both look like "nobody had it".
        'provider_id': result.get('provider_id') or 'comicvine',
        'external_id': result.get('external_id'),
        'title': result['title'],
        'year': result['year'],
        'volume_number': result['volume_number'],
        'issue_count': result['issue_count'],
        'publisher': result['publisher'],
        'translated': result['translated'],
        'already_added': result['already_added'],
        'site_url': result['site_url'],
        'aliases': list(result['aliases'])
    }
    if score is not None:
        snapshot['score'] = score
    return snapshot


def _capture_per_provider(
    results: List[VolumeMetadata]
) -> List[VolumeMetadata]:
    """Take up to the capture limit from each provider that answered."""
    seen: Dict[str, int] = {}
    captured: List[VolumeMetadata] = []
    for result in results:
        provider_id = result.get('provider_id') or 'comicvine'
        taken = seen.get(provider_id, 0)
        if taken >= RAW_SEARCH_CAPTURE_LIMIT:
            continue
        seen[provider_id] = taken + 1
        captured.append(result)
    return captured


def _provider_breakdown(
    search_results: List[VolumeMetadata],
    ranked_results: List[Any]
) -> List[Dict[str, Any]]:
    """Say what each provider returned, over the whole response.

    The point of the fan-out is that ComicVine is thinnest exactly where
    a personal library runs deepest. Reading a hold, the first question
    is which databases were asked and what each said -- and a record
    that lists only candidates cannot answer it, because a provider that
    returned nothing leaves no candidates to list. Counted over the full
    response, not the captured sample, so truncation cannot turn a
    fallback that answered into one that appears never to have run.
    """
    viable = {_candidate_identity(result) for result, _ in ranked_results}
    breakdown: Dict[str, Dict[str, Any]] = {}
    for result in search_results:
        provider_id = result.get('provider_id') or 'comicvine'
        entry = breakdown.setdefault(
            provider_id,
            {'provider_id': provider_id, 'result_count': 0, 'viable_count': 0}
        )
        entry['result_count'] += 1
        if _candidate_identity(result) in viable:
            entry['viable_count'] += 1
    return sorted(breakdown.values(), key=lambda entry: entry['provider_id'])


def build_review_diagnostics(
    group: Dict[str, FilenameData],
    search_results: List[VolumeMetadata],
    only_english: bool,
    review_reason: str
) -> Dict[str, Any]:
    """Describe exactly what the unattended matcher saw and why it held.

    This performs only local ranking work. ``search_results`` are the response
    already cached by the continuous importer, so capturing a postmortem record
    spends zero additional ComicVine requests.
    """
    ranked_results = _rank_volume_results_for_file(
        group,
        search_results,
        only_english
    )

    # The decision is made on the policy score, not the base ranking score:
    # continuous import adds an exact-year bonus and an issue-capacity
    # bonus/penalty, and re-sorts on the result. Recording the base score
    # beside `thresholds.minimum_score` meant a record could not explain its
    # own verdict -- a hold could show a best score above the minimum it was
    # supposedly held for, or below one it passed. Both are recorded now, and
    # the decision fields are the ones the matcher actually compared.
    policy_ranked = sorted(
        (
            (result, base_score, _policy_score(group, result, base_score))
            for result, base_score in ranked_results
        ),
        key=lambda item: item[2],
        reverse=True
    )
    viable_scores = {
        _candidate_identity(result): score
        for result, score in ranked_results
    }
    policy_scores = {
        _candidate_identity(result): policy
        for result, _, policy in policy_ranked
    }

    best_score = policy_ranked[0][2] if policy_ranked else None
    runner_up_score = policy_ranked[1][2] if len(policy_ranked) > 1 else None
    score_margin = (
        best_score - runner_up_score
        if best_score is not None and runner_up_score is not None
        else None
    )
    best_base_score = policy_ranked[0][1] if policy_ranked else None

    first_file = next(iter(group.values()))
    return {
        'search_query': first_file['series'].lower(),
        'review_reason': review_reason,
        'only_english': only_english,
        'thresholds': {
            'minimum_score': AUTO_IMPORT_MIN_MATCH_SCORE,
            'minimum_margin': AUTO_IMPORT_MIN_SCORE_MARGIN
        },
        'decision': {
            'best_score': best_score,
            'runner_up_score': runner_up_score,
            'score_margin': score_margin,
            'raw_result_count': len(search_results),
            'viable_candidate_count': len(ranked_results),
            # What the base ranker gave the same winner, for comparison with
            # the policy score the decision used.
            'best_base_score': best_base_score
        },
        # Which databases answered, and whether any of them offered
        # something the ranker could use.
        'providers': _provider_breakdown(search_results, ranked_results),
        'files': [
            {
                'filepath': filepath,
                'parsed': _json_safe(file_data),
                # Present only when the work item is a folder standing in for
                # an unpacked comic, which is not otherwise distinguishable
                # from a record about a real file.
                **({'folder': snapshot} if snapshot else {})
            }
            for filepath, file_data in group.items()
            for snapshot in (_unpacked_comic_snapshot(filepath),)
        ],
        'viable_candidates': [
            _candidate_snapshot(result, score)
            for result, score in ranked_results[:RAW_SEARCH_CAPTURE_LIMIT]
        ],
        'raw_search_results': [
            {
                **_candidate_snapshot(result),
                'viable_score': viable_scores.get(_candidate_identity(result)),
                'policy_score': policy_scores.get(_candidate_identity(result))
            }
            for result in _capture_per_provider(search_results)
        ]
    }


def get_postmortem_path() -> str:
    """Return the JSONL output path beside Kapowarr's SQLite database."""
    return join(dirname(DBConnection.file), POSTMORTEM_FILENAME)


def append_review_postmortem(
    job_id: int,
    folder: str,
    folder_position: int,
    group_number: int,
    diagnostics: Dict[str, Any]
) -> Optional[str]:
    """Append one stable-id review-group record to the postmortem JSONL file.

    A crash can replay one in-flight folder, so ``record_id`` is deterministic;
    downstream analysis can cheaply deduplicate repeated records.
    """
    path = get_postmortem_path()
    record = {
        'record_id': f'{job_id}:{folder_position}:{group_number}',
        'captured_at': round(time()),
        'job_id': job_id,
        'folder': folder,
        'folder_position': folder_position,
        'group_number': group_number,
        **diagnostics
    }

    try:
        with open(path, 'a', encoding='utf-8') as postmortem_file:
            postmortem_file.write(dumps(record, sort_keys=True) + '\n')
    except OSError as error:
        # Diagnostics must never be able to stop or pause the actual importer.
        LOGGER.warning(
            'Unable to write library import postmortem %s: %s',
            path,
            error
        )
        return None

    return path
