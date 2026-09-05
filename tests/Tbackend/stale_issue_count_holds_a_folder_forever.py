# -*- coding: utf-8 -*-

"""A file whose issue number runs past the provider's record.

Two folders survived every pass of a whole-library import and came back
identical on each one: "Death of Power" #6 and "Druuna" #9. Both are
ordinary issue files sitting in the folder of a volume that is already in
the library. Both name an issue their provider has never heard of --
ComicVine records two issues of Death of Power and eight of Druuna,
because self-published and long-running series outrun their catalogue
entries.

That single fact was charged twice, in two unrelated places, and the two
charges closed a loop:

- Matching docked the candidate once for it, then continuous import's
  policy docked it again, pushing an otherwise clean match (exact title,
  matching volume number, only viable candidate) under the auto-import
  floor and into the review queue.

- Importing the held row by hand did not clear it. `scan_files` binds a
  file by looking up its issue number among the volume's issues; finding
  none, it dropped the file before `add_file`, so nothing landed in
  `files`. The review queue asks `files` whether a hold was resolved, so
  the hold survived its own import, and the next pass held it again.
"""

import unittest
from types import SimpleNamespace
from typing import Dict
from unittest.mock import MagicMock, patch

from backend.base.definitions import (FilenameData, GeneralFileType,
                                      SpecialVersion, VolumeMetadata)
from backend.features.library_import_policy import (
    REVIEW_REASON_WEAK_SCORE, select_auto_import_volume_result)
from backend.features.library_import_state import _review_item_is_live
from backend.implementations import file_matching as FM


def _group(
    filename: str,
    series: str,
    issue_number: float,
    year=None,
    volume_number=1
) -> Dict[str, FilenameData]:
    return {
        filename: {
            'series': series,
            'year': year,
            'volume_number': volume_number,
            'special_version': None,
            'issue_number': issue_number,
            'annual': False
        }
    }


def _candidate(
    comicvine_id: int,
    title: str,
    issue_count: int,
    year: int,
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
        'publisher': 'self published',
        'issue_count': issue_count,
        'translated': False,
        'already_added': None,
        'issues': None
    }


class the_file_is_filed_against_the_volume(unittest.TestCase):
    """`scan_files` no longer drops what it cannot bind to an issue.

    The file is in the volume's folder and passed every check for
    belonging to it. Bound to no issue, it is still the volume's -- so it
    is a volume file, exactly as a partial collected edition is. The
    issues the volume does know about stay wanted.
    """

    def _scan(self, filename: str, issue_count: int):
        volume_data = SimpleNamespace(
            special_version=SpecialVersion.NORMAL,
            volume_number=1,
            title='Death of Power',
            year=2023,
            folder='/content/Adult/Death of Power'
        )
        issues = [
            SimpleNamespace(
                id=100 + n,
                calculated_issue_number=float(n),
                date='2023-01-01',
                title=None
            )
            for n in range(1, issue_count + 1)
        ]
        fake_volume = MagicMock()
        fake_volume.get_data.return_value = volume_data
        fake_volume.get_issues.return_value = issues
        fake_volume.get_all_files.return_value = []
        fake_volume.get_general_files.return_value = []

        cursor = MagicMock()
        cursor.execute.return_value = []

        with patch.object(FM, 'isdir', return_value=True), \
                patch.object(FM, 'list_files', return_value=[filename]), \
                patch.object(FM, 'get_db', return_value=cursor), \
                patch.object(FM, 'Settings'), \
                patch.object(FM, 'WebSocket'), \
                patch.object(FM, 'RootFolders'), \
                patch.object(FM, 'delete_empty_child_folders'), \
                patch.object(FM.FilesDB, 'add_files',
                             side_effect=lambda fs: {f: 7 for f in fs}) as add, \
                patch.object(FM.FilesDB, 'delete_unmatched_files'):
            with patch('backend.implementations.volumes.Volume',
                       return_value=fake_volume):
                FM.scan_files(1)

        issue_bindings, volume_bindings = set(), set()
        for call in cursor.executemany.call_args_list:
            sql, rows = call.args[0], call.args[1]
            if 'INSERT' not in sql.upper():
                continue
            # Both inserts repeat their values in a `WHERE EXISTS` guard,
            # so a row that vanished mid-scan is skipped rather than
            # killing the whole refresh. The binding is the leading
            # columns; the rest is the guard asking whether they are
            # still there.
            if 'issues_files' in sql:
                issue_bindings.update(tuple(r[:2]) for r in rows)
            elif 'volume_files' in sql:
                volume_bindings.update(tuple(r[:3]) for r in rows)
        return add, issue_bindings, volume_bindings

    def test_an_issue_the_volume_does_not_have_still_enters_the_library(self):
        add, issue_bindings, volume_bindings = self._scan(
            '/content/Adult/Death of Power/'
            'Death of Power 006 (2025) (ADULT) (digital) (DrVink-HD-DCP).cbz',
            issue_count=2
        )

        # This is the part that made the hold unclearable: without a row in
        # `files`, nothing downstream could tell the file had been dealt with.
        add.assert_called_once()
        self.assertEqual(len(volume_bindings), 1)
        self.assertIn(
            GeneralFileType.UNMATCHED_ISSUE.value,
            volume_bindings.pop()
        )

    def test_and_claims_none_of_the_issues_the_volume_does_have(self):
        """#1 and #2 are still missing, and must stay wanted."""
        _, issue_bindings, _ = self._scan(
            '/content/Adult/Death of Power/'
            'Death of Power 006 (2025) (ADULT) (digital) (DrVink-HD-DCP).cbz',
            issue_count=2
        )

        self.assertEqual(issue_bindings, set())

    def test_a_file_that_does_name_a_real_issue_is_bound_to_it(self):
        _, issue_bindings, volume_bindings = self._scan(
            '/content/Adult/Death of Power/Death of Power 002 (2023).cbz',
            issue_count=2
        )

        self.assertEqual(issue_bindings, {(7, 102)})
        self.assertEqual(volume_bindings, set())


class the_review_hold_then_retires(unittest.TestCase):
    """The queue asks `files` whether a held row was resolved."""

    HOLD = {
        'filepath': (
            '/content/Adult/Druuna/Druuna 09 Came from the Wind.cbz'
        )
    }

    def test_a_hold_whose_file_reached_files_is_done(self):
        with patch.object(FM, 'isdir'):
            self.assertFalse(
                _review_item_is_live(self.HOLD, {self.HOLD['filepath']})
            )

    def test_while_one_that_never_did_comes_back_every_pass(self):
        """The old behaviour, kept as the thing being prevented."""
        with patch(
            'backend.features.library_import_state.exists',
            return_value=True
        ):
            self.assertTrue(_review_item_is_live(self.HOLD, set()))


class the_capacity_penalty_is_charged_once(unittest.TestCase):
    """Matching and policy weigh the same evidence; one may bill for it."""

    def test_the_series_the_file_belongs_to_is_now_importable(self):
        result, reason = select_auto_import_volume_result(
            _group(
                '/content/Adult/Death of Power/Death of Power 006 (2025).cbz',
                'Death of Power',
                issue_number=6.0,
                year=2025
            ),
            [_candidate(154211, 'Death of Power', issue_count=2, year=2023)],
            only_english=True
        )

        self.assertIsNone(reason)
        self.assertIsNotNone(result)
        self.assertEqual(result['comicvine_id'], 154211)

    def test_a_candidate_with_nothing_else_going_for_it_is_still_weak(self):
        """Net -1 is a real penalty; it just is not a three-point one.

        No year agreement and no volume number, so capacity is the only
        evidence there is, and it contradicts.
        """
        _, reason = select_auto_import_volume_result(
            _group(
                '/content/Whatever/Whatever 010.cbz',
                'Whatever',
                issue_number=10.0,
                volume_number=None
            ),
            [_candidate(201, 'Whatever', issue_count=9, year=1999)],
            only_english=True
        )

        self.assertEqual(reason, REVIEW_REASON_WEAK_SCORE)

    def test_a_candidate_that_can_hold_the_issue_still_wins(self):
        """The bonus is what separates candidates, and it is untouched."""
        result, reason = select_auto_import_volume_result(
            _group(
                '/content/Nova/Nova 172.cbz',
                'Nova',
                issue_number=172.0,
                year=1976
            ),
            [
                _candidate(1, 'Nova', issue_count=3, year=1976),
                _candidate(2, 'Nova', issue_count=200, year=1976)
            ],
            only_english=True
        )

        self.assertIsNone(reason)
        self.assertEqual(result['comicvine_id'], 2)
