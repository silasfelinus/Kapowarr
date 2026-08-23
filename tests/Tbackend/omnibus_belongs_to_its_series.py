# -*- coding: utf-8 -*-

"""An omnibus belongs to its series without claiming to be it."""

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from backend.base.definitions import GeneralFileType, SpecialVersion
from backend.implementations import file_matching as FM
from backend.implementations.matching import (_rank_volume_results_for_file,
                                              match_special_version)


def _file(series, sv, volume_number=1, issue_number=None, year=2022):
    return {
        'series': series, 'year': year, 'volume_number': volume_number,
        'special_version': sv, 'issue_number': issue_number, 'annual': False
    }


def _result(title, issue_count, year=2022, volume_number=1):
    return {
        'comicvine_id': 1, 'title': title, 'year': year,
        'volume_number': volume_number, 'translated': False,
        'issue_count': issue_count, 'publisher': 'Dark Horse',
        'aliases': [], 'site_url': 'https://example.test/1'
    }


def _volume(sv=SpecialVersion.NORMAL, volume_number=1, title='Black Hammer'):
    return SimpleNamespace(
        special_version=sv, volume_number=volume_number,
        title=title, year=2022, folder='/content/Black Hammer'
    )


class an_omnibus_can_match_the_series_it_collects(unittest.TestCase):
    """"Black Hammer Omnibus" could only ever match a one-issue namesake.

    A file parsed as a collected edition was restricted to search results
    with exactly one issue, so the real 50-issue Black Hammer was filtered
    out before anything was scored and the folder was held as
    'no-candidate' -- with 50 raw results sitting right there.
    """

    def test_the_multi_issue_series_is_now_viable(self):
        ranked = _rank_volume_results_for_file(
            {'/c/Black Hammer Omnibus.cbz': _file('Black Hammer', 'omnibus')},
            [_result('Black Hammer', 50)],
            only_english=True
        )

        self.assertEqual(len(ranked), 1)
        self.assertEqual(ranked[0][0]['title'], 'Black Hammer')

    def test_a_tpb_may_too(self):
        ranked = _rank_volume_results_for_file(
            {'/c/Daredevil (2015).cbz': _file('Daredevil', 'tpb', year=2015)},
            [_result('Daredevil', 381, year=2015)],
            only_english=True
        )

        self.assertEqual(len(ranked), 1)

    def test_a_one_shot_still_may_not(self):
        """A one-shot is a single standalone issue, not a collection."""
        ranked = _rank_volume_results_for_file(
            {'/c/Some One-Shot.cbz': _file('Some Story', 'one-shot')},
            [_result('Some Story', 50)],
            only_english=True
        )

        self.assertEqual(ranked, [])


class the_file_belongs_to_that_volume(unittest.TestCase):
    """match_special_version gates the file before it can ever be bound."""

    def test_a_collected_edition_matches_a_normal_volume(self):
        self.assertTrue(
            match_special_version(
                SpecialVersion.NORMAL, SpecialVersion.OMNIBUS, 'Black Hammer'
            )
        )

    def test_including_one_the_extractor_only_guessed_was_a_tpb(self):
        self.assertTrue(
            match_special_version(
                SpecialVersion.NORMAL, SpecialVersion.TPB, 'Daredevil'
            )
        )

    def test_but_not_a_one_shot(self):
        self.assertFalse(
            match_special_version(
                SpecialVersion.NORMAL, SpecialVersion.ONE_SHOT, 'Black Hammer'
            )
        )

    def test_and_not_a_file_that_names_an_issue(self):
        """That is an ordinary issue and is bound as one."""
        self.assertFalse(
            match_special_version(
                SpecialVersion.NORMAL, SpecialVersion.OMNIBUS,
                'Black Hammer', issue_number=4.0
            )
        )


class which_volume_it_belongs_to(unittest.TestCase):
    def test_an_omnibus_belongs_to_the_series(self):
        self.assertTrue(FM.collected_edition_of_volume(
            _file('Black Hammer', 'omnibus'), _volume()
        ))

    def test_so_does_a_named_part_of_a_longer_run(self):
        """It is a real file in the folder; Kapowarr should know about it.

        Nothing is claimed about coverage either way now, so there is no
        reason to leave it unmatched and invisible.
        """
        self.assertTrue(FM.collected_edition_of_volume(
            _file('Batman', 'tpb', volume_number=3), _volume(title='Batman')
        ))

    def test_a_file_with_an_issue_number_does_not(self):
        """That is an ordinary issue and is bound as one."""
        self.assertFalse(FM.collected_edition_of_volume(
            _file('Black Hammer', 'tpb', issue_number=4.0), _volume()
        ))

    def test_a_volume_that_is_itself_a_special_version_does_not(self):
        """Those already bind through the existing special-version branch."""
        self.assertFalse(FM.collected_edition_of_volume(
            _file('Black Hammer', 'omnibus'),
            _volume(sv=SpecialVersion.OMNIBUS)
        ))

    def test_an_ordinary_issue_file_does_not(self):
        self.assertFalse(FM.collected_edition_of_volume(
            _file('Black Hammer', None, issue_number=4.0), _volume()
        ))


class scanning_files_it_to_the_volume(unittest.TestCase):
    """It lands in the folder, and claims none of the issues.

    Bound to issue one, Kapowarr would count 2-50 as missing. Bound to
    all of them, a partial collection would strand every issue it does
    not actually contain. So it is neither: a volume file.
    """

    def _scan(self, filename, volume=None):
        volume_data = volume or _volume()
        issues = [
            SimpleNamespace(id=100 + n, calculated_issue_number=float(n),
                            date='2022-01-01', title=None)
            for n in range(1, 6)
        ]
        fake_volume = MagicMock()
        fake_volume.get_data.return_value = volume_data
        fake_volume.get_issues.return_value = issues
        fake_volume.get_all_files.return_value = []

        cursor = MagicMock()
        cursor.execute.return_value = []

        with patch.object(FM, 'isdir', return_value=True), \
                patch.object(FM, 'list_files', return_value=[filename]), \
                patch.object(FM, 'get_db', return_value=cursor), \
                patch.object(FM, 'Settings'), \
                patch.object(FM, 'commit'), \
                patch.object(FM, 'WebSocket'), \
                patch.object(FM, 'RootFolders'), \
                patch.object(FM, 'delete_empty_child_folders'), \
                patch.object(FM.FilesDB, 'add_file', return_value=7), \
                patch.object(FM.FilesDB, 'delete_unmatched_files'), \
                patch('backend.implementations.volumes.Volume',
                      return_value=fake_volume):
            FM.scan_files(1)

        # Bindings are written through executemany on the cursor.
        issue_bindings, volume_bindings = set(), set()
        for call in cursor.executemany.call_args_list:
            sql, rows = call.args[0], call.args[1]
            if 'INSERT' not in sql.upper():
                continue
            if 'issues_files' in sql:
                issue_bindings.update(tuple(r) for r in rows)
            elif 'volume_files' in sql:
                volume_bindings.update(tuple(r) for r in rows)
        return issue_bindings, volume_bindings, issues

    def test_it_is_filed_against_the_volume(self):
        _, volume_bindings, _ = self._scan(
            '/content/Black Hammer/Black Hammer Omnibus (2022).cbz'
        )

        self.assertEqual(len(volume_bindings), 1)
        self.assertIn(
            GeneralFileType.COLLECTED.value, volume_bindings.pop(),
            'the omnibus belongs to the volume, as a collected edition'
        )

    def test_no_issue_is_marked_as_had(self):
        """A partial collection must not strand what it does not contain."""
        issue_bindings, _, _ = self._scan(
            '/content/Black Hammer/Black Hammer Omnibus (2022).cbz'
        )

        self.assertEqual(
            issue_bindings, set(),
            'the individual issues stay wanted and are fetched normally'
        )

    def test_a_named_part_of_the_run_is_filed_the_same_way(self):
        issue_bindings, volume_bindings, _ = self._scan(
            '/content/Batman/Batman Volume 3 (2022).cbz',
            volume=_volume(title='Batman')
        )

        self.assertEqual(issue_bindings, set())
        self.assertEqual(len(volume_bindings), 1)

    def test_it_is_not_bound_to_issue_one(self):
        """The original bug: the rest of the run reads as missing."""
        issue_bindings, _, issues = self._scan(
            '/content/Black Hammer/Black Hammer Omnibus (2022).cbz'
        )

        self.assertNotIn((7, issues[0].id), issue_bindings)


if __name__ == '__main__':
    unittest.main()
