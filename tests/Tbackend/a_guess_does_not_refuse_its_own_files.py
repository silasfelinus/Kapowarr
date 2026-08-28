# -*- coding: utf-8 -*-

"""Two ways `file_importing_filter` refused files that belong to a volume.

Both were found by running `diagnose_untracked.py` against a real
library, where they are 25 of the 27 files a volume refused from its own
series.

INFERRED SINGLE-ISSUE CLASSIFICATIONS
`determine_special_version` calls any volume with one issue released over
a month ago a TPB -- its own comment says "we'll assume it's a TPB" --
and that assumption then refused the volume's own files. #153 opened it
for issue 1. The library shows the rest of the shape: five volumes whose
single issue is numbered `00` rather than `01` (`Plague Seeker 00`,
`Dread the Halls 00`, `The Light Fantastic 00`, `Milestone Returns
Infinite Edition 00`, `Nacelleverse The Great Garloo 00`), `Doctor
Strange 450` against a volume long since grown past one issue, and
`Witch Hammer` 2 and 3 of a series that outran its catalogue entry.

A guess must not outrank the files it was guessing about. A
classification the user set stays authoritative -- that is an assertion,
not an assumption -- which is what `special_version_locked` is for.

COLLECTED EDITIONS
A collected edition's `v02` is the collection's number and its year is
the collection's year; neither is the series'. Requiring them to equal
the volume's refused `Black Hammer Omnibus v02` from the Black Hammer
folder it sits in, along with `Moon Knight Omnibus v01`, `RASL Omnibus`,
`Die v03` and four `Amory Wars` volumes. `scan_files` has had a branch
waiting for exactly these files that this gate never let it reach.
"""

import unittest
from types import SimpleNamespace

from backend.base.definitions import SpecialVersion
from backend.base.file_extraction import extract_filename_data
from backend.implementations.matching import file_importing_filter


def _volume(title, year, special_version, locked=False, volume_number=1):
    return SimpleNamespace(
        title=title, year=year, volume_number=volume_number,
        special_version=special_version, special_version_locked=locked,
        folder=f'/content/{title}'
    )


def _issues(*numbers, year=2023):
    return [
        SimpleNamespace(
            id=n, calculated_issue_number=float(n),
            date=f'{year}-01-01', title=None
        )
        for n in numbers
    ]


def _imports(filename, volume, issues):
    file_data = extract_filename_data(
        f'{volume.folder}/{filename}', prefer_folder_year=True
    )
    return file_importing_filter(
        file_data, volume, issues,
        {i.calculated_issue_number: year_of(i) for i in issues}
    )


def year_of(issue):
    return int(issue.date[:4])


class an_inferred_single_issue_classification(unittest.TestCase):
    def test_a_volume_takes_its_own_issue_zero(self):
        # Five real volumes, all numbered 00 rather than 01.
        volume = _volume('Plague Seeker', 2023, SpecialVersion.TPB)
        self.assertTrue(
            _imports('Plague Seeker 00 (2023).cbz', volume, _issues(0))
        )

    def test_a_volume_that_outgrew_the_guess_takes_its_issues(self):
        # ComicVine knew one issue when the volume was added.
        volume = _volume('Doctor Strange', 2025, SpecialVersion.TPB)
        self.assertTrue(_imports(
            'Doctor Strange 450 (2025).cbz', volume,
            _issues(450, year=2025)
        ))

    def test_a_series_that_outran_its_catalogue_entry(self):
        volume = _volume('Witch Hammer', 2018, SpecialVersion.TPB)
        for name in (
            'Witch Hammer 02 (2023).cbz', 'Witch Hammer 03 (2024).cbz'
        ):
            with self.subTest(name=name):
                self.assertTrue(
                    _imports(name, volume, _issues(1, year=2018))
                )

    def test_every_inferred_classification_behaves_the_same(self):
        for version in (
            SpecialVersion.TPB, SpecialVersion.ONE_SHOT,
            SpecialVersion.HARD_COVER, SpecialVersion.OMNIBUS
        ):
            with self.subTest(special_version=version):
                self.assertTrue(_imports(
                    'Plague Seeker 02 (2023).cbz',
                    _volume('Plague Seeker', 2023, version),
                    _issues(1)
                ))

    def test_a_classification_the_user_set_still_refuses(self):
        # An assertion, not an assumption. This is the whole distinction.
        volume = _volume(
            'Plague Seeker', 2023, SpecialVersion.TPB, locked=True
        )
        self.assertFalse(
            _imports('Plague Seeker 02 (2023).cbz', volume, _issues(1))
        )

    def test_a_normal_volume_is_unaffected(self):
        volume = _volume('Plague Seeker', 2023, SpecialVersion.NORMAL)
        self.assertTrue(
            _imports('Plague Seeker 02 (2023).cbz', volume, _issues(1, 2))
        )


class a_collected_edition_in_its_series_folder(unittest.TestCase):
    def test_an_omnibus_numbered_past_the_volume_is_taken(self):
        volume = _volume('Black Hammer', 2016, SpecialVersion.NORMAL)
        self.assertTrue(_imports(
            'Black Hammer Omnibus v02 (2023) (digital).cbz',
            volume, _issues(1, 2, 3, year=2016)
        ))

    def test_the_same_for_a_trade_volume(self):
        volume = _volume('Die', 2018, SpecialVersion.NORMAL)
        self.assertTrue(_imports(
            'Die v03 - The Great Game (2021) (Digital).cbz',
            volume, _issues(1, 2, year=2018)
        ))

    def test_it_still_claims_none_of_the_volumes_issues(self):
        # `collected_edition_of_volume` decides how it is filed, and its
        # docstring is explicit that the issues stay wanted. This asserts
        # the file only gets as far as being considered.
        from backend.implementations.matching import (
            collected_edition_of_volume)
        volume = _volume('Black Hammer', 2016, SpecialVersion.NORMAL)
        file_data = extract_filename_data(
            f'{volume.folder}/Black Hammer Omnibus v02 (2023).cbz',
            prefer_folder_year=True
        )
        self.assertTrue(collected_edition_of_volume(file_data, volume))
        self.assertIsNone(file_data['issue_number'])

    def test_an_ordinary_issue_still_needs_the_volume_or_the_year(self):
        # The relaxation is for collected editions only; a numbered issue
        # from another volume is still refused.
        volume = _volume('Death of Power', 2023, SpecialVersion.NORMAL)
        self.assertFalse(_imports(
            'Totally Unrelated Manga v02 005 (1998).cbz',
            volume, _issues(1, 2)
        ))


if __name__ == '__main__':
    unittest.main()
