# -*- coding: utf-8 -*-

"""The invented volume number that handed files to the wrong entry.

`extract_filename_data` fills in `volume_number = 1` when a filename does not
state one, and almost every filename does not. Almost every volume in a comics
library is also volume 1, so `match_volume_number` said yes to those and no to
every other entry of the same series -- on the strength of a number the file
never carried.

Where the name states a year as well, `match_year` still had something real to
say. Where it states neither, that invented 1 was the *only* thing deciding,
and it decided by catalogue accident: the file went to whichever entries happen
to be volume 1, and could not reach the rest however plainly the issue belonged
to them.

The fixtures here are Silas's library on 2026-09-05, from the orphan recovery
pass that could not place `Detective.Comics.962.(two.covers).cbz`:

    Detective Comics (1937)  volume 1  issues 1..881
    Detective Comics (2017)  volume 1  issues 1..30
    Detective Comics (2016)  volume 3  issues 934..1112

Issue 962 exists in exactly one of those. It is the one the file could not
reach, and the pass reported the two that cannot contain it as competing for
it. Kapowarr's own download history agrees: eight other issues of that run,
942 through 961, are all recorded against Detective Comics (2016).
"""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from backend.base.file_extraction import extract_filename_data
from backend.features import watched_folder_import as wfi
from backend.features.watched_folder_import import LibraryIndex
from backend.implementations.matching import file_importing_filter


class _FakeVolume:
    def __init__(self, volume_id, title, year, volume_number, issues,
                 dated=False):
        self.id = volume_id
        self._issues = list(issues)
        # A catalogue entry that dates its issues, monthly from the volume's
        # own year. Off by default because most of these cases turn on the
        # issue list alone, and on by request where the year check is the
        # thing under test.
        self._dated = dated
        self._year = year
        self._data = SimpleNamespace(
            title=title, year=year, volume_number=volume_number,
            special_version=None, folder=f'/content/{title} ({year})'
        )

    def get_data(self):
        return self._data

    def _date(self, n, first):
        if not self._dated:
            return None
        return f'{self._year + (n - first) // 12:04d}-01-01'

    def get_issues(self, _skip_files=False):
        first = self._issues[0]
        return [
            SimpleNamespace(
                calculated_issue_number=float(n), date=self._date(n, first)
            )
            for n in self._issues
        ]


DETECTIVE_COMICS = {
    774: _FakeVolume(774, 'Detective Comics', 1937, 1, range(1, 882)),
    1671: _FakeVolume(1671, 'Detective Comics', 2017, 1, range(1, 31)),
    1757: _FakeVolume(1757, 'Detective Comics', 2016, 3, range(934, 1113))
}


def _accepts(filename, volume):
    issues = volume.get_issues()
    return file_importing_filter(
        extract_filename_data(filename),
        volume.get_data(),
        issues,
        {
            i.calculated_issue_number:
                int(i.date[:4]) if i.date else None
            for i in issues
        }
    )


class a_name_that_states_no_volume_reaches_every_volume(unittest.TestCase):
    def test_the_volume_that_has_the_issue_is_a_candidate(self):
        """The bug, at its narrowest. Detective Comics (2016) is volume 3, so
        a file that never said "volume 1" was refused by the only entry that
        holds issue 962."""
        self.assertTrue(
            _accepts('Detective.Comics.962.(two.covers).cbz',
                     DETECTIVE_COMICS[1757])
        )

    def test_a_stated_volume_number_still_rules_entries_out(self):
        """Widening applies to silence, not to a name that says otherwise.
        "v1" is a claim, and volume 3 must still refuse it."""
        self.assertFalse(
            _accepts('Detective Comics v1 962.cbz', DETECTIVE_COMICS[1757])
        )

    def test_a_stated_year_is_still_judged(self):
        """A name carrying a year has something real to go on, so this leaves
        that path exactly as it was: a dated run ending in the 1990s does not
        take a 2026 file, and it did not need the volume number to say so."""
        dated_1937 = _FakeVolume(
            774, 'Detective Comics', 1937, 1, range(1, 882), dated=True)

        self.assertFalse(_accepts('Detective Comics 962 (2026).cbz',
                                  dated_1937))
        self.assertTrue(_accepts('Detective Comics 962.cbz', dated_1937))


class the_issue_list_decides_between_them(unittest.TestCase):
    """Widening the filter is only half of it: three candidates where there
    was one is progress only if something then picks the right one. That is
    `_settle`, and it picks on which volume actually lists the issue."""

    def _match(self, filename, volumes, ambiguous=None):
        with patch.object(wfi, 'Volume', side_effect=lambda vid: volumes[vid]):
            return wfi.match_file_to_library_volume(
                filename, LibraryIndex(list(volumes)), ambiguous
            )

    def test_issue_962_goes_to_the_run_that_has_it(self):
        self.assertEqual(
            self._match('/downloads/Detective.Comics.962.(two.covers).cbz',
                        DETECTIVE_COMICS),
            1757
        )

    def test_an_issue_two_runs_share_is_still_left_alone(self):
        """No false confidence. Issue 12 is in Detective Comics (1937) and
        Detective Comics (2017) alike, the name carries no year, and nothing
        separates them -- so it stays a human's decision."""
        ambiguous = {}
        self.assertIsNone(
            self._match('/downloads/Detective.Comics.012.cbz',
                        DETECTIVE_COMICS, ambiguous)
        )
        self.assertTrue(ambiguous)

    def test_adam_strange_stays_ambiguous(self):
        """The other half of the same pass, and the honest outcome. Both runs
        of Adam Strange have a #1, the filename carries no year, and Kapowarr
        recorded fetching this same release for both volumes. There is no
        evidence to decide on and the file is left where it is."""
        adam_strange = {
            208: _FakeVolume(208, 'Adam Strange', 2004, 1, range(1, 9)),
            908: _FakeVolume(908, 'Adam Strange', 1990, 1, range(1, 4))
        }

        self.assertIsNone(
            self._match('/downloads/Adam Strange #01.cbr', adam_strange)
        )
