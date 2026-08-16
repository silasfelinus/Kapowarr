# -*- coding: utf-8 -*-

"""Confidence policy for unattended library import.

The core matching module owns the reusable filename-evidence ranking. Continuous
import keeps its decision threshold here so live-library calibration can evolve
without changing the more conservative generic confident-match helper.
"""

from typing import Dict, List, Optional, Tuple

from backend.base.definitions import FilenameData, VolumeMetadata
from backend.implementations.matching import _rank_volume_results_for_file


AUTO_IMPORT_MIN_MATCH_SCORE = 4
AUTO_IMPORT_MIN_SCORE_MARGIN = 1

REVIEW_REASON_NO_CANDIDATE = 'no-candidate'
REVIEW_REASON_WEAK_SCORE = 'weak-score'
REVIEW_REASON_TIE = 'tie'


def select_auto_import_volume_result(
    group: Dict[str, FilenameData],
    search_results: List[VolumeMetadata],
    only_english: bool
) -> Tuple[Optional[VolumeMetadata], Optional[str]]:
    """Return a safe unattended winner and, when held, the review reason.

    A candidate still needs at least 4/5 filename evidence. Live-library data
    showed that requiring a two-point lead was sending too many folders to
    review, so continuous import now accepts a one-point lead while exact ties
    remain human-reviewed.
    """
    ranked_results = _rank_volume_results_for_file(
        group,
        search_results,
        only_english
    )
    if not ranked_results:
        return None, REVIEW_REASON_NO_CANDIDATE

    best_result, best_score = ranked_results[0]
    if best_score < AUTO_IMPORT_MIN_MATCH_SCORE:
        return None, REVIEW_REASON_WEAK_SCORE

    if len(ranked_results) > 1:
        runner_up_score = ranked_results[1][1]
        if best_score - runner_up_score < AUTO_IMPORT_MIN_SCORE_MARGIN:
            return None, REVIEW_REASON_TIE

    return best_result, None
