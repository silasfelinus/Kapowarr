# -*- coding: utf-8 -*-

"""Confidence policy for unattended library import.

The core matching module owns the reusable filename-evidence ranking. Continuous
import keeps its decision threshold here so live-library calibration can evolve
without changing the more conservative generic confident-match helper.
"""

from typing import Dict, List, Optional, Tuple

from backend.base.definitions import FilenameData, VolumeMetadata
from backend.implementations.matching import _rank_volume_results_for_file


# Ranking already applies the important hard safety gates before assigning a
# score: title compatibility, language, special-version compatibility and enough
# issues to contain the files being imported. A zero score therefore means "the
# candidate is viable, but the filename supplied no extra year/volume/count
# corroboration", not "bad match". Negative scores currently represent an
# explicit contradiction (for example a file issue number beyond the candidate's
# reported issue count), so keep those for review.
AUTO_IMPORT_MIN_MATCH_SCORE = 0
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

    The reusable ranker has already removed title/language/type candidates that
    cannot describe the files. Continuous import accepts the remaining unique
    best result when its score is non-negative, even if year or volume-number
    evidence is absent or disagrees. This deliberately accommodates re-releases
    and organizer filenames whose year is not the series' original start year.
    Exact ties and explicit negative contradictions remain human-reviewed.
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
