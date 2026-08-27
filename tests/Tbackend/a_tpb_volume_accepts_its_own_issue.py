# -*- coding: utf-8 -*-

"""A one-issue volume called a TPB refused its own file.

`determine_special_version` labels any volume with exactly one issue
released over a month ago a `TPB`. That is explicitly a guess -- "we'll
assume it's a TPB" -- and it is the branch every one-shot, special and
graphic novel in a real library lands in.

Such a volume's own file is ordinarily named `<Series> 01 (year)`, which
`extract_filename_data` reads as issue 1 with no special version.
`match_special_version` has a branch for exactly this: issue 1 against a
single-issue volume classification. It listed HARD_COVER, ONE_SHOT and
OMNIBUS, and not TPB.

So the importer added the volume, `scan_files` refused the file that
caused it to be added, nothing entered `files`, and library import -- which
reads `files` to decide whether a folder still holds anything untracked --
handed the folder straight back on the next Rescan Untracked Library.

In the 2026-08-26 run this accounted for 359 of the 922 refusals the new
scan logging recorded, the largest single group in it.
"""

import unittest
from types import SimpleNamespace

from backend.base.definitions import SpecialVersion
from backend.base.file_extraction import extract_filename_data
from backend.implementations.matching import (file_importing_filter,
                                              match_special_version)


def _volume(special_version, title='7174AD', year=2024):
    return SimpleNamespace(
        special_version=special_version,
        volume_number=1,
        title=title,
        year=year,
        folder=f'/content/{title}'
    )


def _one_issue(year=2024):
    return [SimpleNamespace(
        id=1, calculated_issue_number=1.0, date=f'{year}-03-01', title=None
    )]


class every_single_issue_classification_takes_issue_one(unittest.TestCase):
    SINGLE_ISSUE_VERSIONS = (
        SpecialVersion.HARD_COVER,
        SpecialVersion.ONE_SHOT,
        SpecialVersion.OMNIBUS,
        SpecialVersion.TPB
    )

    def test_a_bare_issue_one_matches_all_of_them(self):
        for version in self.SINGLE_ISSUE_VERSIONS:
            with self.subTest(special_version=version):
                self.assertTrue(match_special_version(
                    version, None, '7174AD', 1.0
                ))

    def test_the_real_file_reaches_the_volume_that_caused_its_import(self):
        # The exact pair from the 2026-08-26 log: volume 183 was created
        # for this folder one second before it refused this file.
        file_data = extract_filename_data(
            '/content/7174AD/7174AD 01 (2024).cbz', prefer_folder_year=True
        )
        for version in self.SINGLE_ISSUE_VERSIONS:
            with self.subTest(special_version=version):
                self.assertTrue(file_importing_filter(
                    file_data,
                    _volume(version),
                    _one_issue(),
                    {1.0: 2024}
                ))

    def test_a_normal_volume_is_unaffected(self):
        file_data = extract_filename_data(
            '/content/7174AD/7174AD 01 (2024).cbz', prefer_folder_year=True
        )
        self.assertTrue(file_importing_filter(
            file_data, _volume(SpecialVersion.NORMAL), _one_issue(), {1.0: 2024}
        ))


class the_branch_is_still_about_issue_one(unittest.TestCase):
    """Widening it must not turn TPB into a catch-all."""

    def test_issue_seven_does_not_ride_in_on_it(self):
        self.assertFalse(match_special_version(
            SpecialVersion.TPB, None, '7174AD', 7.0
        ))

    def test_a_file_with_no_issue_number_does_not_either(self):
        self.assertFalse(match_special_version(
            SpecialVersion.TPB, None, '7174AD', None
        ))

    def test_tpb_now_behaves_exactly_like_its_peers(self):
        """No new asymmetry: whatever the branch does, it does for all four.

        The branch deliberately does not consult the file's own special
        version once the issue number is 1 -- that is how it has always
        worked for HARD_COVER, ONE_SHOT and OMNIBUS. TPB joining them must
        not introduce a case where it answers differently from the other
        three.
        """
        for check in (None, SpecialVersion.TPB, SpecialVersion.VOLUME_AS_ISSUE,
                      SpecialVersion.COVER, SpecialVersion.NORMAL):
            with self.subTest(file_special_version=check):
                answers = {
                    match_special_version(reference, check, '7174AD', 1.0)
                    for reference in (
                        SpecialVersion.HARD_COVER,
                        SpecialVersion.ONE_SHOT,
                        SpecialVersion.OMNIBUS,
                        SpecialVersion.TPB
                    )
                }
                self.assertEqual(len(answers), 1, answers)


if __name__ == '__main__':
    unittest.main()
