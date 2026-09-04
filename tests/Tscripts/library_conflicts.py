# -*- coding: utf-8 -*-

"""Two volumes of one series is normal; two volumes in one folder is not.

The first version of this script grouped the library by title and offered
to delete any entry holding no files. Run against Silas's real library on
2026-09-04 that listed roughly 250 "duplicate" sets -- Batman's four runs,
Daredevil's nine -- and among the deletions it would have offered were
`Batman (1940)` (0 of 716 imported), `Detective Comics (2017)`,
`Doom Patrol (1964)` and `Fantastic Four (1982)`. An empty volume is not a
duplicate. It is usually a series nobody has imported yet.

What that same dump did show is two things no judgement is needed for:
four volumes sharing `/content/Batman/Batman (2016)`, and `One Piece
(1997)` pointing at `/content/WildC.A.T.S/WildCATS Covert Action Teams`.
These pin those, and pin that nothing here can delete a volume.
"""

import os
import sys
import unittest

sys.path.insert(
    0,
    os.path.join(os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))), 'scripts')
)

from library_conflicts import (folder_names_the_volume,  # noqa: E402
                               misfiled, shared_folders)


def _volume(volume_id, title, year, folder, downloaded=0, issues=10):
    return {
        'id': volume_id, 'title': title, 'year': year, 'folder': folder,
        'issue_count': issues, 'issues_downloaded': downloaded
    }


class a_folder_that_names_a_different_series(unittest.TestCase):
    def test_a_release_folder_under_another_series_is_misfiled(self):
        self.assertFalse(folder_names_the_volume(
            'Grimm Tales of Terror',
            '/content/WildC.A.T.S/WildCATS Covert Action Teams/'
            'Grimm.Tales.of.Terror.2018.Halloween.Special.2018.digital.'
            'The.Seeker-Empire'
        ))

    def test_one_piece_in_the_wildcats_folder_is_misfiled(self):
        self.assertFalse(folder_names_the_volume(
            'One Piece', '/content/WildC.A.T.S/WildCATS Covert Action Teams'
        ))

    def test_a_longer_title_that_merely_starts_the_same_is_misfiled(self):
        """`Red` is not `Red Book`, and `Blue` is not `Blue Book`."""
        self.assertFalse(
            folder_names_the_volume('Red', '/content/Red Book (2025)'))
        self.assertFalse(
            folder_names_the_volume('Blue', '/content/Blue Book'))

    def test_a_franchise_folder_above_the_volume_is_fine(self):
        self.assertTrue(folder_names_the_volume(
            'Detective Comics', '/content/Batman/Detective Comics (1937)'
        ))
        self.assertTrue(folder_names_the_volume(
            'Moon Knight',
            '/content/Moon Knight Omnibus (2022)/Moon Knight (1980)'
        ))

    def test_the_naming_scheme_is_read_the_way_the_importer_reads_it(self):
        """`Amazing Spider-Man, The (1963)` is the folder Kapowarr's own
        naming scheme generates for The Amazing Spider-Man."""
        self.assertTrue(folder_names_the_volume(
            'The Amazing Spider-Man',
            '/content/Spider-Man/Amazing Spider-Man, The (1963)'
        ))
        self.assertTrue(folder_names_the_volume(
            'Howard the Duck', '/content/Howard the Duck v4 (2015)'
        ))

    def test_a_title_the_parser_mangles_is_mangled_on_both_sides(self):
        """`Gen 13` loses its 13 to the issue-number rule. That is fine as
        long as the folder loses it too, which is why the volume's title
        goes through the same parse."""
        self.assertTrue(
            folder_names_the_volume('Gen 13', '/content/Gen 13/Gen 13 (1994)')
        )

    def test_a_volume_with_no_folder_is_not_judged(self):
        self.assertFalse(folder_names_the_volume('Batman', ''))


class two_volumes_in_one_directory(unittest.TestCase):
    def test_the_shared_directory_is_reported_once_with_both_sides(self):
        groups = shared_folders([
            _volume(1652, 'Batman', 1940, '/content/Batman/Batman (2016)'),
            _volume(1653, 'Batman Annual', 1958,
                    '/content/Batman/Batman (2016)'),
            _volume(1754, 'Batman Annual', 2016,
                    '/content/Batman/Batman (2016)', downloaded=5),
            _volume(611, 'Batman', 2025, '/content/Batman/Batman (2025)')
        ])

        self.assertEqual(len(groups), 1)
        self.assertEqual(len(groups[0]), 3)

    def test_a_library_where_every_volume_has_its_own_folder(self):
        self.assertEqual(shared_folders([
            _volume(1, 'Batman', 1940, '/content/Batman (1940)'),
            _volume(2, 'Batman', 2016, '/content/Batman (2016)')
        ]), [])

    def test_volumes_with_no_folder_are_not_a_collision(self):
        self.assertEqual(shared_folders([
            _volume(1, 'Batman', 1940, ''),
            _volume(2, 'Superman', 1939, '')
        ]), [])


class four_runs_of_a_series_are_not_a_finding(unittest.TestCase):
    """The whole reason the title-grouping version had to go."""

    def test_batmans_runs_are_left_alone(self):
        volumes = [
            _volume(1652, 'Batman', 1940, '/content/Batman/Batman (1940)'),
            _volume(1753, 'Batman', 2016, '/content/Batman/Batman (2016)',
                    downloaded=162),
            _volume(611, 'Batman', 2025, '/content/Batman/Batman (2025)',
                    downloaded=12)
        ]

        self.assertEqual(shared_folders(volumes), [])
        self.assertEqual(misfiled(volumes), [])

    def test_an_empty_volume_is_not_a_finding_either(self):
        """`Batman (1940)` has 0 of 716 issues imported. Nothing is wrong
        with it."""
        self.assertEqual(misfiled([
            _volume(1652, 'Batman', 1940, '/content/Batman/Batman (1940)',
                    downloaded=0, issues=716)
        ]), [])


class the_worst_misfiling_is_named_first(unittest.TestCase):
    def test_a_volume_with_comics_in_the_wrong_place_leads(self):
        found = misfiled([
            _volume(5304, 'Grimm Tales of Terror', 2018,
                    '/content/WildC.A.T.S/WildCATS Covert Action Teams/x',
                    downloaded=1),
            _volume(5456, 'One Piece', 1997,
                    '/content/WildC.A.T.S/WildCATS Covert Action Teams',
                    downloaded=6)
        ])

        self.assertEqual([v['id'] for v in found], [5456, 5304])


class it_cannot_delete_a_volume(unittest.TestCase):
    def test_nothing_in_it_issues_a_delete(self):
        import inspect

        import library_conflicts

        source = inspect.getsource(library_conflicts)
        self.assertNotIn("'DELETE'", source)
        self.assertNotIn('delete_folder', source)

    def test_folders_are_only_moved_when_asked(self):
        import inspect

        import library_conflicts

        source = inspect.getsource(library_conflicts.main)
        self.assertIn('if not args.fix_folders:', source)
        # And only the misfiled ones: a shared folder can be two entries
        # that both belong there.
        self.assertIn('for volume in wrong:', source)


if __name__ == '__main__':
    unittest.main()
