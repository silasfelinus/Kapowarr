# -*- coding: utf-8 -*-

"""`already_added` was carried on every search result and never consulted.

Two of the survivors from a whole-library import were held for reasons the
library itself could have settled:

- "Druuna 09 Came from the Wind.cbz" carries no year and no volume number,
  so the 1986 Druuna and the 2016 Druuna scored identically and the group
  was held as a tie. Eight of that folder's nine files had already been
  imported into the 1986 volume, and Kapowarr had recorded that volume as
  owning that exact folder.

- "Death of Power" #3-#5 was held as 'no-candidate' with fifty raw results,
  because a hard filter dropped every candidate whose issue count was below
  the number of issues the group covers. ComicVine records two issues; the
  three files sit in the folder of the volume the user already added.

Both are the same mistake: the provider's record was treated as the only
source of truth about a series, when the user's own library had already
answered.
"""

import unittest
from typing import Dict
from unittest.mock import patch

from backend.base.definitions import FilenameData, VolumeMetadata
from backend.features import library_import_policy as policy
from backend.features.library_import_policy import (
    REVIEW_REASON_TIE, select_auto_import_volume_result)
from backend.implementations.matching import _rank_volume_results_for_file


def _files(folder: str, *names) -> Dict[str, FilenameData]:
    return {f'{folder}/{name}': data for name, data in names}


def _parsed(issue_number, year=None, volume_number=1) -> FilenameData:
    return {
        'series': 'Druuna',
        'year': year,
        'volume_number': volume_number,
        'special_version': None,
        'issue_number': issue_number,
        'annual': False
    }


def _candidate(
    comicvine_id: int,
    title: str,
    issue_count: int,
    year: int,
    already_added=None,
    volume_number=1
) -> VolumeMetadata:
    return {
        'comicvine_id': comicvine_id,
        'title': title,
        'year': year,
        'volume_number': volume_number,
        'cover_link': '',
        'cover': None,
        'description': '',
        'site_url': f'https://comicvine.example/{comicvine_id}',
        'aliases': [],
        'publisher': None,
        'issue_count': issue_count,
        'translated': False,
        'already_added': already_added,
        'issues': None
    }


DRUUNA_1986 = _candidate(41409, 'Druuna', 8, 1986, already_added=1621)
DRUUNA_2016 = _candidate(96418, 'Druuna', 6, 2016, already_added=231)


class a_tie_is_broken_by_who_owns_the_folder(unittest.TestCase):
    """Only a tie, and only when exactly one candidate owns the folder."""

    GROUP = _files(
        '/content/Adult/Druuna',
        ('Druuna 09 Came from the Wind.cbz', _parsed(9.0))
    )

    def _select(self, candidates, folders):
        with patch.object(
            policy, '_library_volume_folders', return_value=folders
        ):
            return select_auto_import_volume_result(
                self.GROUP, candidates, only_english=True
            )

    def test_the_volume_that_holds_the_rest_of_the_folder_wins(self):
        result, reason = self._select(
            [DRUUNA_1986, DRUUNA_2016],
            {1621: '/content/Adult/Druuna', 231: '/content/Comics/Druuna'}
        )

        self.assertIsNone(reason)
        self.assertEqual(result['comicvine_id'], 41409)

    def test_a_file_in_a_subfolder_of_the_volume_counts_as_owned(self):
        result, reason = self._select(
            [DRUUNA_1986, DRUUNA_2016],
            {1621: '/content/Adult', 231: '/content/Comics/Druuna'}
        )

        self.assertIsNone(reason)
        self.assertEqual(result['comicvine_id'], 41409)

    def test_two_owners_are_still_ambiguous_and_go_to_a_human(self):
        result, reason = self._select(
            [DRUUNA_1986, DRUUNA_2016],
            {1621: '/content/Adult/Druuna', 231: '/content/Adult/Druuna'}
        )

        self.assertIsNone(result)
        self.assertEqual(reason, REVIEW_REASON_TIE)

    def test_no_owner_is_still_ambiguous(self):
        result, reason = self._select(
            [DRUUNA_1986, DRUUNA_2016],
            {1621: '/content/Comics/A', 231: '/content/Comics/B'}
        )

        self.assertIsNone(result)
        self.assertEqual(reason, REVIEW_REASON_TIE)

    def test_a_candidate_in_no_library_at_all_cannot_be_promoted(self):
        """`already_added` is the whole signal; without it there is none."""
        result, reason = self._select(
            [
                _candidate(41409, 'Druuna', 8, 1986),
                _candidate(96418, 'Druuna', 6, 2016)
            ],
            {}
        )

        self.assertIsNone(result)
        self.assertEqual(reason, REVIEW_REASON_TIE)

    def test_the_tiebreak_never_overrules_filename_evidence(self):
        """A candidate that lost on score is not resurrected by ownership.

        The 2016 volume owns the folder here, but the 1986 volume wins the
        year outright, so there is no tie to break and ownership is never
        consulted.
        """
        result, reason = select_auto_import_volume_result(
            _files(
                '/content/Adult/Druuna',
                ('Druuna 01 (1986).cbz', _parsed(1.0, year=1986))
            ),
            [
                _candidate(41409, 'Druuna', 8, 1986),
                _candidate(96418, 'Druuna', 6, 2016, already_added=231)
            ],
            only_english=True
        )

        self.assertIsNone(reason)
        self.assertEqual(result['comicvine_id'], 41409)


class a_volume_you_own_survives_a_stale_issue_count(unittest.TestCase):
    """The hard filter erased the answer before anything could weigh it."""

    GROUP = {
        f'/content/Adult/Death of Power/Death of Power 00{n} (2024).cbz': {
            'series': 'Death of Power',
            'year': 2024,
            'volume_number': 1,
            'special_version': None,
            'issue_number': float(n),
            'annual': False
        }
        for n in (3, 4, 5)
    }

    def test_three_files_against_a_two_issue_record_now_rank(self):
        ranked = _rank_volume_results_for_file(
            self.GROUP,
            [_candidate(
                154211, 'Death of Power', 2, 2023, already_added=230
            )],
            only_english=True
        )

        self.assertEqual(len(ranked), 1)
        self.assertEqual(ranked[0][0]['comicvine_id'], 154211)

    def test_a_namesake_you_do_not_own_is_still_filtered_out(self):
        """The gate is intact for everything the user has not vouched for."""
        ranked = _rank_volume_results_for_file(
            self.GROUP,
            [_candidate(154211, 'Death of Power', 2, 2023)],
            only_english=True
        )

        self.assertEqual(ranked, [])

    def test_being_owned_is_not_the_same_as_being_preferred(self):
        """It survives to be scored; it does not win by surviving.

        The 30-issue series can hold #3-#5 and is rated above the owned
        two-issue record, exactly as it should be.
        """
        ranked = _rank_volume_results_for_file(
            self.GROUP,
            [
                _candidate(
                    154211, 'Death of Power', 2, 2023, already_added=230
                ),
                _candidate(999, 'Death of Power', 30, 2024)
            ],
            only_english=True
        )

        self.assertEqual(ranked[0][0]['comicvine_id'], 999)
        self.assertGreater(ranked[0][1], ranked[1][1])
