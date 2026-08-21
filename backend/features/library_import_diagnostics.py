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
from os.path import dirname, join
from time import time
from typing import Any, Dict, List, Optional

from backend.base.definitions import FilenameData, VolumeMetadata
from backend.base.logging import LOGGER
from backend.features.library_import_policy import (
    AUTO_IMPORT_MIN_MATCH_SCORE,
    AUTO_IMPORT_MIN_SCORE_MARGIN,
    _policy_score,
)
from backend.implementations.matching import _rank_volume_results_for_file
from backend.internals.db import DBConnection


POSTMORTEM_FILENAME = 'library_import_review_postmortem.jsonl'
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


def _candidate_snapshot(
    result: VolumeMetadata,
    score: Optional[int] = None
) -> Dict[str, Any]:
    """Keep useful ComicVine metadata while excluding covers/issue payloads."""
    snapshot: Dict[str, Any] = {
        'comicvine_id': result['comicvine_id'],
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
        result['comicvine_id']: score
        for result, score in ranked_results
    }
    policy_scores = {
        result['comicvine_id']: policy
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
        'files': [
            {
                'filepath': filepath,
                'parsed': _json_safe(file_data)
            }
            for filepath, file_data in group.items()
        ],
        'viable_candidates': [
            _candidate_snapshot(result, score)
            for result, score in ranked_results[:RAW_SEARCH_CAPTURE_LIMIT]
        ],
        'raw_search_results': [
            {
                **_candidate_snapshot(result),
                'viable_score': viable_scores.get(result['comicvine_id']),
                'policy_score': policy_scores.get(result['comicvine_id'])
            }
            for result in search_results[:RAW_SEARCH_CAPTURE_LIMIT]
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
