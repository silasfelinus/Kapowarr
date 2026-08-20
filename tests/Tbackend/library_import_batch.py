# -*- coding: utf-8 -*-

"""One bad volume must not take down the rest of an import batch.

These build real files in a temporary root. ``import_library`` moves and
renames files, and decides what it can do by looking at the disk, so a suite
made of paths that do not exist cannot tell a working import from one that
silently does nothing.
"""

import unittest
from os import makedirs
from os.path import dirname, join
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from backend.features.library_import import import_library


class _ImportHarness(unittest.TestCase):
    """Real files under a real root folder, with the database mocked out."""

    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = join(self.temp.name, 'library')
        makedirs(self.root)

    def _file(self, *parts: str) -> str:
        """Create a real file under the root and return its path."""
        path = join(self.root, *parts)
        makedirs(dirname(path), exist_ok=True)
        with open(path, 'wb') as handle:
            handle.write(b'')
        return path

    def _missing(self, *parts: str) -> str:
        """A path under the root that deliberately has no file."""
        return join(self.root, *parts)

    def _patches(self, add_result):
        root = SimpleNamespace(id=1, folder=self.root + '/')
        add = (
            {'side_effect': add_result}
            if isinstance(add_result, (list, BaseException))
            or (isinstance(add_result, type)
                and issubclass(add_result, BaseException))
            else {'return_value': add_result}
        )
        return (
            patch(
                'backend.features.library_import.RootFolders.get_all',
                return_value=[root]
            ),
            patch('backend.features.library_import.Library.add', **add),
            patch('backend.features.library_import.scan_files'),
            patch('backend.features.library_import.commit')
        )


class resumable_library_import(_ImportHarness):
    def test_manual_batch_keeps_working_after_one_volume_fails(self):
        bad = self._file('Bad', 'Bad 001.cbz')
        good = self._file('Good', 'Good 001.cbz')
        patches = self._patches([RuntimeError('stale folder'), 22])
        with patches[0], patches[1], patches[2] as scan, patches[3]:
            result = import_library([
                {'id': 101, 'filepath': bad},
                {'id': 202, 'filepath': good}
            ], continue_on_error=True)

        self.assertEqual([row['id'] for row in result['failed']], [101])
        self.assertEqual(result['failed'][0]['reason'], 'stale folder')
        self.assertEqual([row['id'] for row in result['imported']], [202])
        scan.assert_called_once_with(22, filepath_filter=[good])

    def test_background_import_still_raises_for_task_retry(self):
        bad = self._file('Bad', 'Bad 001.cbz')
        patches = self._patches(RuntimeError('temporary failure'))
        with patches[0], patches[1], patches[2], patches[3]:
            with self.assertRaisesRegex(RuntimeError, 'temporary failure'):
                import_library([{'id': 101, 'filepath': bad}])

    def test_outside_root_is_reported_instead_of_silently_dropped(self):
        outside = join(self.temp.name, 'downloads', 'Bad 001.cbz')
        makedirs(dirname(outside))
        with open(outside, 'wb') as handle:
            handle.write(b'')

        patches = self._patches(22)
        with patches[0], patches[1] as add, patches[2], patches[3]:
            result = import_library([
                {'id': 101, 'filepath': outside}
            ], continue_on_error=True)

        self.assertEqual(result['imported'], [])
        self.assertEqual(result['failed'], [])
        self.assertEqual(result['skipped'][0]['id'], 101)
        self.assertIn('outside', result['skipped'][0]['reason'])
        add.assert_not_called()


class import_of_files_that_moved(_ImportHarness):
    """A proposal records where a file was, not where it will be.

    Importing a volume moves its files into the volume folder and renaming
    rewrites their basenames, so any proposal made before that -- a review
    hold, a re-submitted list, a second browser tab -- can name a path that no
    longer exists. That used to reach ``shutil.move`` and raise
    ``FileNotFoundError``, failing the whole volume including the files that
    were still exactly where the proposal said.
    """

    def test_a_volume_whose_files_all_moved_is_skipped_not_failed(self):
        gone = self._missing('Agent', 'The Agent 02 (2024).cbz')
        patches = self._patches(22)
        with patches[0], patches[1] as add, patches[2], patches[3]:
            result = import_library([
                {'id': 155548, 'filepath': gone}
            ], continue_on_error=True)

        self.assertEqual(result['imported'], [])
        self.assertEqual(
            result['failed'], [],
            'a file that already moved is not an error to retry'
        )
        self.assertEqual(result['skipped'][0]['id'], 155548)
        self.assertIn('no longer', result['skipped'][0]['reason'])
        add.assert_not_called()

    def test_the_files_that_are_still_there_are_imported(self):
        present = self._file('Agent', 'The Agent 03 (2024).cbz')
        gone = self._missing('Agent', 'The Agent 02 (2024).cbz')

        patches = self._patches(22)
        with patches[0], patches[1], patches[2] as scan, patches[3]:
            result = import_library([
                {'id': 155548, 'filepath': gone},
                {'id': 155548, 'filepath': present}
            ], continue_on_error=True)

        self.assertEqual(result['failed'], [])
        self.assertEqual([row['id'] for row in result['imported']], [155548])
        self.assertEqual(
            result['imported'][0]['filepaths'], [present],
            'the result must report what was imported, not what was asked for'
        )
        scan.assert_called_once_with(22, filepath_filter=[present])

    def test_a_missing_file_does_not_fail_its_whole_volume(self):
        present = self._file('Agent', 'The Agent 03 (2024).cbz')
        gone = self._missing('Agent', 'The Agent 02 (2024).cbz')

        patches = self._patches(22)
        with patches[0], patches[1], patches[2], patches[3]:
            result = import_library([
                {'id': 155548, 'filepath': gone},
                {'id': 155548, 'filepath': present}
            ])

        self.assertEqual([row['id'] for row in result['imported']], [155548])


if __name__ == '__main__':
    unittest.main()
