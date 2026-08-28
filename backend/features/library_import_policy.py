# -*- coding: utf-8 -*-

"""Confidence policy for unattended library import.

The core matching module owns the reusable filename-evidence ranking. Continuous
import keeps its decision threshold here so live-library calibration can evolve
without changing the more conservative generic confident-match helper.
"""

from typing import Dict, List, Optional, Tuple

from backend.base.definitions import FilenameData, VolumeMetadata
from backend.base.files import common_folder, folder_is_inside_folder
from backend.base.logging import LOGGER
from backend.implementations.matching import (
    ISSUE_CAPACITY_RATING_PENALTY, _rank_volume_results_for_file)


# Ranking already applies the important hard safety gates before assigning a
# score: title compatibility, language, special-version compatibility and enough
# files covered by the candidate's issue count. A zero score therefore means
# "the candidate is viable, but the filename supplied no extra year/volume/count
# corroboration", not "bad match". Continuous import adds two secondary pieces
# of evidence that are particularly useful in real libraries: an exact series
# year and whether the candidate's issue count can plausibly reach the highest
# issue number present in the files. Those are deliberately policy-local so the
# generic interactive matcher keeps its historical ordering.
AUTO_IMPORT_MIN_MATCH_SCORE = 0
AUTO_IMPORT_MIN_SCORE_MARGIN = 1
AUTO_IMPORT_EXACT_YEAR_BONUS = 1
AUTO_IMPORT_ISSUE_CAPACITY_BONUS = 2
AUTO_IMPORT_ISSUE_CAPACITY_PENALTY = 2

REVIEW_REASON_NO_CANDIDATE = 'no-candidate'
REVIEW_REASON_WEAK_SCORE = 'weak-score'
REVIEW_REASON_TIE = 'tie'
# A folder that names a series and holds no comics. Never auto-imported:
# with no files there is no file evidence, and the score/margin rules have
# nothing to weigh but the title.
REVIEW_REASON_EMPTY_FOLDER = 'empty-folder'


def _highest_issue_number(group: Dict[str, FilenameData]) -> Optional[float]:
    highest: Optional[float] = None
    for file_data in group.values():
        issue_number = file_data['issue_number']
        if issue_number is None:
            continue

        if isinstance(issue_number, tuple):
            candidate = max(issue_number)
        else:
            candidate = issue_number

        if highest is None or candidate > highest:
            highest = candidate

    return highest


def _policy_score(
    group: Dict[str, FilenameData],
    result: VolumeMetadata,
    base_score: int
) -> int:
    """Add import-specific corroboration without changing generic matching."""
    score = base_score
    years = {
        file_data['year']
        for file_data in group.values()
        if file_data['year'] is not None
    }
    if result['year'] is not None and result['year'] in years:
        # The reusable ranker already rewards exact/fuzzy year agreement. This
        # small extra point lets an explicit folder/filename year beat the very
        # common implicit/default volume-number=1 tie.
        score += AUTO_IMPORT_EXACT_YEAR_BONUS

    highest_issue = _highest_issue_number(group)
    if highest_issue is not None:
        if result['issue_count'] >= highest_issue:
            # A candidate that can actually contain #172 is materially more
            # plausible than a three-issue namesake, even when the parser's
            # default volume number would otherwise favor the tiny series.
            score += AUTO_IMPORT_ISSUE_CAPACITY_BONUS
        else:
            # Keep this as a score penalty rather than a hard filter because
            # legacy numbering can legitimately start above one -- and because
            # a provider issue count is a claim about the provider's records,
            # not about the series. Ongoing and self-published runs sit ahead
            # of ComicVine constantly.
            #
            # The ranker already docked this exact candidate for this exact
            # fact, so `base_score` is short by that much before policy adds
            # anything. Charging the full policy penalty on top made one piece
            # of evidence cost three points on a scale whose realistic ceiling
            # is five, and put a 4-point gap between "can hold #6" (+2) and
            # "cannot" (-2). Give the ranker's penalty back and take this one
            # instead, so the axis is priced once.
            #
            # Concretely: "Death of Power" #6 against the 2-issue ComicVine
            # record of the very series it belongs to -- exact title, matching
            # volume number, the only viable candidate, and the rest of the
            # folder already imported into it -- scored 1 on the base scale
            # and -1 after policy, landing under the floor and back in the
            # review queue on every pass.
            score += ISSUE_CAPACITY_RATING_PENALTY
            score -= AUTO_IMPORT_ISSUE_CAPACITY_PENALTY

    return score


def _library_volume_folders(volume_ids: List[int]) -> Dict[int, str]:
    """Map local volume IDs to the folder each one occupies on disk.

    Imported lazily: this module is otherwise pure scoring, and the tests
    that cover the scoring do not need a database to exist.
    """
    if not volume_ids:
        return {}

    from backend.internals.db import get_db

    placeholders = ','.join('?' * len(volume_ids))
    return {
        int(row['id']): str(row['folder'])
        for row in get_db().execute(
            f"SELECT id, folder FROM volumes WHERE id IN ({placeholders});",
            volume_ids
        ).fetchall()
        if row['folder']
    }


def _folder_owning_candidate(
    group: Dict[str, FilenameData],
    tied: List[VolumeMetadata]
) -> Optional[VolumeMetadata]:
    """Break a tie with the one thing the library already knows.

    Filename evidence genuinely runs out sometimes. "Druuna 09 Came from the
    Wind.cbz" carries no year and no volume number, so the 1986 Druuna and
    the 2016 Druuna score identically off the filename and the group is held
    as a tie -- on every pass, forever, because a hold is not a decision.

    But the files are not nowhere. Eight of that folder's nine files were
    already imported into the 1986 volume, and Kapowarr recorded that volume
    as owning that exact folder. `already_added` carries the local volume ID
    on every search result and was never consulted; the answer was sitting in
    the library the whole time.

    Only a tie is resolved this way, and only when exactly one of the tied
    candidates owns the folder. A candidate that lost on filename evidence is
    never promoted by this, and an ambiguous folder still goes to a human.
    """
    if len(tied) < 2:
        return None

    added_ids = {
        candidate['comicvine_id']: candidate['already_added']
        for candidate in tied
        if candidate.get('already_added') is not None
    }
    if not added_ids:
        return None

    group_folder = common_folder(list(group))
    if not group_folder:
        return None

    folders = _library_volume_folders(sorted(set(added_ids.values())))

    owners = [
        candidate
        for candidate in tied
        if candidate.get('already_added') in folders
        and folder_is_inside_folder(
            folders[candidate['already_added']],
            group_folder
        )
    ]
    if len(owners) != 1:
        return None

    LOGGER.info(
        'Breaking a %d-way tie for %s in favour of %s (%s): volume %s '
        'already owns this folder',
        len(tied), group_folder, owners[0]['title'], owners[0]['year'],
        owners[0]['already_added']
    )
    return owners[0]


def select_auto_import_volume_result(
    group: Dict[str, FilenameData],
    search_results: List[VolumeMetadata],
    only_english: bool
) -> Tuple[Optional[VolumeMetadata], Optional[str]]:
    """Return a safe unattended winner and, when held, the review reason.

    The reusable ranker has already removed title/language/type candidates that
    cannot describe the files. Continuous import accepts a non-negative winner
    when it leads the runner-up after import-specific corroboration. Exact ties
    remain human-reviewed. Far-year re-releases are still allowed when they are
    the only viable title; the extra year point only resolves otherwise-close
    candidates, it is never a hard year gate.
    """
    ranked_results = _rank_volume_results_for_file(
        group,
        search_results,
        only_english
    )
    if not ranked_results:
        return None, REVIEW_REASON_NO_CANDIDATE

    policy_ranked = [
        (result, _policy_score(group, result, base_score))
        for result, base_score in ranked_results
    ]
    policy_ranked.sort(key=lambda item: item[1], reverse=True)

    best_result, best_score = policy_ranked[0]
    if best_score < AUTO_IMPORT_MIN_MATCH_SCORE:
        return None, REVIEW_REASON_WEAK_SCORE

    if len(policy_ranked) > 1:
        runner_up_score = policy_ranked[1][1]
        if best_score - runner_up_score < AUTO_IMPORT_MIN_SCORE_MARGIN:
            owner = _folder_owning_candidate(
                group,
                [
                    result
                    for result, score in policy_ranked
                    if score == best_score
                ]
            )
            if owner is not None:
                return owner, None

            return None, REVIEW_REASON_TIE

    return best_result, None
