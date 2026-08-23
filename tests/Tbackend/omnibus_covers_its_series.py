# -*- coding: utf-8 -*-

"""An omnibus is the run it collects, not issue one of it."""

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from backend.base.definitions import SpecialVersion
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


class which_issues_it_covers(unittest.TestCase):
    def test_an_omnibus_covers_the_whole_run(self):
        self.assertTrue(FM.collected_edition_covers_volume(
            _file('Black Hammer', 'omnibus'), _volume()
        ))

    def test_a_named_part_of_a_longer_run_does_not(self):
        """"Batman Volume 3" collects part of Batman; it is not Batman."""
        self.assertFalse(FM.collected_edition_covers_volume(
            _file('Batman', 'tpb', volume_number=3), _volume(title='Batman')
        ))

    def test_a_file_with_an_issue_number_does_not(self):
        self.assertFalse(FM.collected_edition_covers_volume(
            _file('Black Hammer', 'tpb', issue_number=4.0), _volume()
        ))

    def test_a_volume_that_is_itself_a_special_version_does_not(self):
        """Those already bind through the existing special-version branch."""
        self.assertFalse(FM.collected_edition_covers_volume(
            _file('Black Hammer', 'omnibus'),
            _volume(sv=SpecialVersion.OMNIBUS)
        ))

    def test_an_ordinary_issue_file_does_not(self):
        self.assertFalse(FM.collected_edition_covers_volume(
            _file('Black Hammer', None, issue_number=4.0), _volume()
        ))


class scanning_binds_it_to_every_issue(unittest.TestCase):
    """The whole point: never issue one with the rest left missing.

    Bound to issue one alone, Kapowarr would count issues 2-50 as missing
    and go download comics that are already on disk inside this very file.
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

        # The bindings are written through executemany on the cursor.
        written = set()
        for call in cursor.executemany.call_args_list:
            sql, rows = call.args[0], call.args[1]
            if 'issues_files' in sql and 'INSERT' in sql.upper():
                written.update(tuple(r) for r in rows)
        return written, issues

    def test_every_issue_gets_the_omnibus(self):
        written, issues = self._scan('/content/Black Hammer/Black Hammer Omnibus (2022).cbz')

        self.assertEqual(
            written, {(7, issue.id) for issue in issues},
            'the omnibus must cover the run, not just its first issue'
        )

    def test_a_named_part_of_the_run_is_left_alone(self):
        """"Batman Volume 3" must not mark all of Batman as had."""
        written, _ = self._scan(
            '/content/Batman/Batman Volume 3 (2022).cbz',
            volume=_volume(title='Batman')
        )

        self.assertEqual(written, set())

    def test_it_is_not_bound_to_issue_one_alone(self):
        written, issues = self._scan('/content/Black Hammer/Black Hammer Omnibus (2022).cbz')

        self.assertNotEqual(written, {(7, issues[0].id)})


if __name__ == '__main__':
    unittest.main()
