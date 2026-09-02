# -*- coding: utf-8 -*-

"""A file `scan_files` declines to record must at least say so.

`files` is the table library import reads to decide whether a folder still
has anything untracked in it. A file that fails `file_importing_filter`
never reaches `add_file`, so it never enters `files`, its folder is
therefore still untracked, and the folder comes back in full on the next
Rescan Untracked Library -- forever, with nothing in the log saying which
file held it there or why.

That is the same failure the `UNMATCHED_ISSUE` branch was added to fix,
one level up and still unaddressed. It is not fixed here: a file that
fails this filter has not been established as belonging to the volume the
way an unmatched issue number has, and may well belong to a different
one. It is only no longer invisible.
"""

import logging
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from backend.base.definitions import SpecialVersion
from backend.implementations import file_matching as FM


class scan_files_reports_what_it_declines(unittest.TestCase):
    FOLDER = '/content/Death of Power'

    def _scan(self, filename):
        volume_data = SimpleNamespace(
            special_version=SpecialVersion.NORMAL,
            volume_number=1,
            title='Death of Power',
            year=2023,
            folder=self.FOLDER
        )
        issues = [
            SimpleNamespace(
                id=100 + n,
                calculated_issue_number=float(n),
                date='2023-01-01',
                title=None
            )
            for n in (1, 2)
        ]
        fake_volume = MagicMock()
        fake_volume.get_data.return_value = volume_data
        fake_volume.get_issues.return_value = issues
        fake_volume.get_all_files.return_value = []
        fake_volume.get_general_files.return_value = []

        cursor = MagicMock()
        cursor.execute.return_value = []

        # A plain handler rather than `assertLogs`, which fails a test that
        # produces no records at all -- and a clean bind produces none,
        # which is exactly what one of these tests asserts.
        records = []
        handler = logging.Handler()
        handler.emit = lambda record: records.append(record.getMessage())
        FM.LOGGER.addHandler(handler)
        previous_level = FM.LOGGER.level
        FM.LOGGER.setLevel(logging.INFO)
        self.addCleanup(FM.LOGGER.setLevel, previous_level)
        self.addCleanup(FM.LOGGER.removeHandler, handler)

        # `scan_files` registers in one batch now, so what matters is which
        # files it decided to register, not whether it called at all.
        registered = []

        def register(filepaths):
            registered.extend(filepaths)
            return {f: 7 for f in registered}

        with patch.object(FM, 'isdir', return_value=True), \
                patch.object(FM, 'list_files', return_value=[filename]), \
                patch.object(FM, 'get_db', return_value=cursor), \
                patch.object(FM, 'Settings'), \
                patch.object(FM, 'WebSocket'), \
                patch.object(FM, 'RootFolders'), \
                patch.object(FM, 'delete_empty_child_folders'), \
                patch.object(FM.FilesDB, 'add_files',
                             side_effect=register) as add, \
                patch.object(FM.FilesDB, 'delete_unmatched_files'):
            with patch('backend.implementations.volumes.Volume',
                       return_value=fake_volume):
                FM.scan_files(1)
        return registered, '\n'.join(records)

    def test_a_file_of_another_series_is_named(self):
        registered, output = self._scan(
            f'{self.FOLDER}/Totally Unrelated Manga v02 005 (1998).cbz'
        )
        self.assertEqual(registered, [])
        self.assertIn('Totally Unrelated Manga v02 005 (1998).cbz', output)
        self.assertIn('does not match the volume', output)

    def test_a_file_of_this_series_the_volume_will_not_take_is_named(self):
        # The series is right and the volume still declines it: a
        # one-shot offered to a normal volume.
        registered, output = self._scan(
            f'{self.FOLDER}/Death of Power One-Shot (2023).cbz'
        )
        self.assertEqual(registered, [])
        self.assertIn('Death of Power One-Shot', output)
        self.assertIn('does not match the volume', output)

    def test_a_one_shot_against_a_normal_volume_is_named(self):
        registered, output = self._scan(
            f'{self.FOLDER}/Death of Power One-Shot (2023).cbz'
        )
        self.assertEqual(registered, [])
        self.assertIn('does not match the volume', output)

    def test_a_file_the_volume_does_take_is_not_reported_as_declined(self):
        registered, output = self._scan(
            f'{self.FOLDER}/Death of Power 002 (2023).cbz'
        )
        self.assertEqual(len(registered), 1)
        self.assertNotIn('does not match the volume', output)


if __name__ == '__main__':
    unittest.main()
