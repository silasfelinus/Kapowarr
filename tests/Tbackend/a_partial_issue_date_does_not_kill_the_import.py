# -*- coding: utf-8 -*-

"""A date a provider only half knows must not end the whole import task.

GCD records unknown month and day components as `00` -- `"1959-00-00"` is
a real returned value -- and Kapowarr's GCD provider truncates those to
whatever precision is real (`"1959"`, `"1959-05"`) rather than throw the
whole date away over one unknown component.

Two places parsed a date rather than a year and assumed `%Y-%m-%d`:
`determine_special_version`, which every single volume added goes
through, and `set_file_date`. Neither could be reached with a partial
date while ComicVine was effectively the only provider ever consulted --
it always sends a full date. The moment GCD started returning results,
the first GCD volume to reach `Library.add` raised `ValueError` out
through `import_library` and killed the entire Continuous Library Import
task. The 2026-08-27 log shows it happening three times, on the same
folder (`/content/Adult/Ignominia`, GCD series 168939, issue date
`'2015-05'`), at 05:30, 10:45 and 15:14 -- every retry, all day, with no
progress in between.
"""

import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from backend.base.definitions import SpecialVersion
from backend.base.helpers import parse_issue_date
from backend.implementations import volumes as V


class dates_a_provider_only_half_knows(unittest.TestCase):
    def test_a_full_date_is_unchanged(self):
        self.assertEqual(
            parse_issue_date('2015-05-14'), datetime(2015, 5, 14)
        )

    def test_a_year_and_month_resolve_to_the_first(self):
        self.assertEqual(parse_issue_date('2015-05'), datetime(2015, 5, 1))

    def test_a_bare_year_resolves_to_january_the_first(self):
        self.assertEqual(parse_issue_date('2015'), datetime(2015, 1, 1))

    def test_a_year_is_not_allowed_to_swallow_a_fuller_date(self):
        # `%Y` would happily match the leading `2015` of `2015-05-14` if
        # the formats were tried shortest-first.
        self.assertEqual(
            parse_issue_date('2015-05-14').month, 5
        )

    def test_what_cannot_be_read_reads_as_nothing(self):
        for value in ('1959-00-00', '', '   ', 'nonsense', None):
            with self.subTest(value=value):
                self.assertIsNone(parse_issue_date(value))


class determining_a_special_version_survives_one(unittest.TestCase):
    def _determine(self, date):
        issue = SimpleNamespace(
            id=1, calculated_issue_number=1.0, date=date, title=None
        )
        volume = MagicMock()
        volume.get_data.return_value = SimpleNamespace(
            title='Ignominia', year=2015, description='', volume_number=1,
            special_version=None
        )
        volume.get_issues.return_value = [issue]
        with patch.object(V, 'Volume', return_value=volume):
            return V.determine_special_version(1)

    def test_the_exact_value_that_killed_the_task(self):
        # GCD series 168939, the first GCD volume ever to reach Library.add.
        self.assertEqual(self._determine('2015-05'), SpecialVersion.TPB)

    def test_a_bare_year_is_read_as_an_old_single_issue(self):
        self.assertEqual(self._determine('1959'), SpecialVersion.TPB)

    def test_a_full_date_still_behaves_as_it_did(self):
        self.assertEqual(self._determine('2015-05-14'), SpecialVersion.TPB)

    def test_a_recent_single_issue_is_still_a_normal_volume(self):
        recent = datetime.now().strftime('%Y-%m-%d')
        self.assertEqual(self._determine(recent), SpecialVersion.NORMAL)

    def test_an_unreadable_date_falls_through_rather_than_raising(self):
        for date in ('1959-00-00', '', None):
            with self.subTest(date=date):
                self.assertEqual(
                    self._determine(date), SpecialVersion.NORMAL
                )


class setting_a_file_date_survives_one(unittest.TestCase):
    def test_an_unreadable_date_skips_the_file_instead_of_raising(self):
        from backend.base import files as F
        with patch.object(F, 'get_os_type') as os_type, \
                patch.object(F, 'utime') as utime:
            F.set_file_date('/content/x.cbz', '1959-00-00')
            os_type.assert_not_called()
            utime.assert_not_called()

    def test_a_partial_date_is_still_applied(self):
        from backend.base import files as F
        from backend.base.definitions import OSType
        with patch.object(F, 'get_os_type', return_value=OSType.LINUX), \
                patch.object(F, 'utime') as utime:
            F.set_file_date('/content/x.cbz', '2015-05')
            utime.assert_called_once()
            expected = datetime(2015, 5, 1).timestamp()
            self.assertEqual(utime.call_args.kwargs['times'],
                             (expected, expected))


if __name__ == '__main__':
    unittest.main()
