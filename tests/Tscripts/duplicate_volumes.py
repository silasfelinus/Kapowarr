# -*- coding: utf-8 -*-

"""Deleting the wrong half of a duplicate pair loses the library entry that
holds all the comics.

The 2026-09-04 log named seven sets of competing volumes and nothing else:
`Penthouse Comix (1997) [id 244]; Penthouse Comix (1994) [id 1588]`. Two
ids, no indication which of them has the 43 files. This script exists so
that choice is made on the numbers, and so the one case that needs no
judgement -- an entry holding nothing at all -- can be cleared without one.
"""

import os
import sys
import unittest

sys.path.insert(
    0,
    os.path.join(os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))), 'scripts')
)

from duplicate_volumes import (competing_groups, overlapping,  # noqa: E402
                               safe_to_delete)


def _volume(volume_id, title, year, issues=10, downloaded=0):
    return {
        'id': volume_id,
        'title': title,
        'year': year,
        'folder': f'/content/{title}',
        'issue_count': issues,
        'issues_downloaded': downloaded,
        'total_size': downloaded * 40_000_000
    }


class the_groups_are_the_ones_the_importer_would_see(unittest.TestCase):
    def test_two_entries_for_one_run_group_together(self):
        groups = competing_groups([
            _volume(244, 'Penthouse Comix', 1997),
            _volume(1588, 'Penthouse Comix', 1994),
            _volume(9, 'Batman', 1940)
        ])

        self.assertEqual(len(groups), 1)
        self.assertEqual(
            sorted(v['id'] for v in groups[0]), [244, 1588]
        )

    def test_a_library_with_no_duplicates_reports_none(self):
        self.assertEqual(
            competing_groups([
                _volume(1, 'Batman', 1940),
                _volume(2, 'Superman', 1939)
            ]),
            []
        )

    def test_titles_are_matched_the_way_the_importer_matches_them(self):
        """`match_title`, not a private reimplementation -- otherwise this
        would list pairs that never compete and miss pairs that do."""
        groups = competing_groups([
            _volume(4048, 'Project Superpowers', 2008),
            _volume(4050, 'Project: Superpowers Omnibus', 2018)
        ])

        self.assertEqual(len(groups), 1)

    def test_the_biggest_group_comes_first(self):
        groups = competing_groups([
            _volume(1, 'Green Lantern', 1960),
            _volume(2, 'Green Lantern', 1990),
            _volume(3, 'Green Lantern', 2005),
            _volume(4, 'Catwoman', 2011),
            _volume(5, 'Catwoman', 2012)
        ])

        self.assertEqual(len(groups[0]), 3)


class an_entry_with_nothing_cannot_be_the_wrong_one(unittest.TestCase):
    def test_the_empty_side_is_the_safe_delete(self):
        group = [
            _volume(1588, 'Penthouse Comix', 1994, downloaded=43),
            _volume(244, 'Penthouse Comix', 1997, downloaded=0)
        ]

        self.assertEqual(
            [v['id'] for v in safe_to_delete(group)], [244]
        )

    def test_two_sides_that_both_hold_comics_are_left_to_the_user(self):
        group = [
            _volume(208, 'Adam Strange', 2004, downloaded=8),
            _volume(908, 'Adam Strange', 1990, downloaded=4)
        ]

        self.assertEqual(safe_to_delete(group), [])

    def test_two_empty_sides_are_left_alone_too(self):
        """Nothing to lose either way, but nothing to gain from picking
        blind: deleting the wrong one still discards a monitored series."""
        group = [
            _volume(1, 'Flash Gordon', 2023, downloaded=0),
            _volume(2, 'Flash Gordon', 2024, downloaded=0)
        ]

        self.assertEqual(safe_to_delete(group), [])


class a_pair_only_counts_if_both_could_claim_something(unittest.TestCase):
    def test_an_entry_with_no_issues_at_all_is_not_a_competitor(self):
        self.assertFalse(overlapping([
            _volume(1, 'Batman', 1940, issues=1000),
            _volume(2, 'Batman', 2016, issues=0)
        ]))

    def test_two_entries_with_issues_compete(self):
        self.assertTrue(overlapping([
            _volume(1, 'Batman', 1940, issues=1000),
            _volume(2, 'Batman', 2016, issues=90)
        ]))


class it_never_deletes_without_being_told(unittest.TestCase):
    def test_deleting_is_behind_a_flag(self):
        import inspect

        import duplicate_volumes

        source = inspect.getsource(duplicate_volumes.main)
        self.assertIn("if not args.delete_empty:", source)
        # And the folder is never touched, whatever it deletes.
        self.assertIn('delete_folder=false', source)
        self.assertNotIn('delete_folder=true', source)


if __name__ == '__main__':
    unittest.main()
