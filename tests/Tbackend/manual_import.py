import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from backend.base.custom_exceptions import IssueNotFound, VolumeNotFound
from backend.features import manual_import as manual_import_module
from backend.features.manual_import import manual_import_files


class _FakeVolume:
    """Stands in for `backend.implementations.volumes.Volume`, just enough
    for `manual_import_files()`: a folder to move files into, and issues
    that either belong to it or don't.
    """

    def __init__(self, folder, valid_issue_ids=(1,)):
        self._folder = folder
        self._valid_issue_ids = set(valid_issue_ids)

    def get_data(self):
        return SimpleNamespace(folder=self._folder)

    def get_issue(self, issue_id):
        if issue_id not in self._valid_issue_ids:
            raise IssueNotFound(issue_id)
        return SimpleNamespace(id=issue_id)


class manual_import_of_externally_acquired_files(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)

        self.volume_folder = os.path.join(self.tmpdir.name, 'volume')
        self.downloads_folder = os.path.join(self.tmpdir.name, 'downloads')
        os.makedirs(self.volume_folder)
        os.makedirs(self.downloads_folder)

        self.volume = _FakeVolume(self.volume_folder)

        self.get_volume_patch = patch.object(
            manual_import_module.Library,
            'get_volume',
            return_value=self.volume
        )
        self.get_volume_patch.start()
        self.addCleanup(self.get_volume_patch.stop)

        self.scan_files_patch = patch.object(
            manual_import_module, 'scan_files'
        )
        self.mock_scan_files = self.scan_files_patch.start()
        self.addCleanup(self.scan_files_patch.stop)

        self.set_file_matching_patch = patch.object(
            manual_import_module, 'set_file_matching'
        )
        self.mock_set_file_matching = self.set_file_matching_patch.start()
        self.addCleanup(self.set_file_matching_patch.stop)

    def _make_file(self, folder, name, content=b'x'):
        path = os.path.join(folder, name)
        with open(path, 'wb') as f:
            f.write(content)
        return path

    def test_file_outside_volume_folder_is_moved_in_and_scanned(self):
        src = self._make_file(self.downloads_folder, 'Batman 001.cbz')

        result = manual_import_files(1, [src])

        expected_dest = os.path.join(self.volume_folder, 'Batman 001.cbz')
        self.assertFalse(os.path.isfile(src))
        self.assertTrue(os.path.isfile(expected_dest))

        self.assertEqual(len(result['imported']), 1)
        self.assertEqual(result['imported'][0]['filepath'], src)
        self.assertEqual(result['imported'][0]['moved_to'], expected_dest)
        self.assertEqual(result['skipped'], [])

        self.mock_scan_files.assert_called_once_with(
            1, filepath_filter=[expected_dest], update_websocket=True
        )
        self.mock_set_file_matching.assert_not_called()

    def test_file_already_in_volume_folder_is_not_moved(self):
        src = self._make_file(self.volume_folder, 'Batman 002.cbz')

        result = manual_import_files(1, [src])

        self.assertTrue(os.path.isfile(src))
        self.assertEqual(result['imported'][0]['moved_to'], None)
        self.mock_scan_files.assert_called_once_with(
            1, filepath_filter=[src], update_websocket=True
        )

    def test_missing_file_is_skipped_with_a_reason(self):
        missing = os.path.join(self.downloads_folder, 'does-not-exist.cbz')

        result = manual_import_files(1, [missing])

        self.assertEqual(result['imported'], [])
        self.assertEqual(len(result['skipped']), 1)
        self.assertEqual(result['skipped'][0]['filepath'], missing)
        self.assertEqual(result['skipped'][0]['status'], 'skipped')
        self.assertIn('not found', result['skipped'][0]['reason'])
        self.mock_scan_files.assert_not_called()
        self.mock_set_file_matching.assert_not_called()

    def test_name_collision_in_destination_is_skipped(self):
        self._make_file(self.volume_folder, 'Batman 003.cbz', b'existing')
        src = self._make_file(self.downloads_folder, 'Batman 003.cbz', b'new')

        result = manual_import_files(1, [src])

        self.assertEqual(result['imported'], [])
        self.assertEqual(len(result['skipped']), 1)
        self.assertIn('already exists', result['skipped'][0]['reason'])
        # The pre-existing file in the volume folder was left untouched.
        with open(
            os.path.join(self.volume_folder, 'Batman 003.cbz'), 'rb'
        ) as f:
            self.assertEqual(f.read(), b'existing')
        # And the source file was never moved/removed.
        self.assertTrue(os.path.isfile(src))

    def test_mixed_batch_reports_both_imported_and_skipped(self):
        good = self._make_file(self.downloads_folder, 'good.cbz')
        missing = os.path.join(self.downloads_folder, 'missing.cbz')

        result = manual_import_files(1, [good, missing])

        self.assertEqual(len(result['imported']), 1)
        self.assertEqual(len(result['skipped']), 1)
        self.mock_scan_files.assert_called_once()
        scanned_paths = self.mock_scan_files.call_args.kwargs[
            'filepath_filter'
        ]
        self.assertEqual(
            scanned_paths,
            [os.path.join(self.volume_folder, 'good.cbz')]
        )

    def test_issue_id_forces_a_match_instead_of_scanning(self):
        src = self._make_file(self.downloads_folder, 'Batman 001.cbz')

        result = manual_import_files(1, [src], issue_id=1)

        expected_dest = os.path.join(self.volume_folder, 'Batman 001.cbz')
        self.assertEqual(result['imported'][0]['moved_to'], expected_dest)

        self.mock_scan_files.assert_not_called()
        self.mock_set_file_matching.assert_called_once_with(1, [{
            'filepath': expected_dest,
            'issue_ids': [1],
            'general_file': False,
            'forced_match': True
        }])

    def test_issue_id_not_belonging_to_volume_raises(self):
        src = self._make_file(self.downloads_folder, 'Batman 001.cbz')

        with self.assertRaises(IssueNotFound):
            manual_import_files(1, [src], issue_id=999)

        # Nothing should have been moved.
        self.assertTrue(os.path.isfile(src))
        self.mock_scan_files.assert_not_called()
        self.mock_set_file_matching.assert_not_called()

    def test_unknown_volume_raises(self):
        with patch.object(
            manual_import_module.Library,
            'get_volume',
            side_effect=VolumeNotFound(404)
        ):
            with self.assertRaises(VolumeNotFound):
                manual_import_files(404, ['/tmp/whatever.cbz'])

    def test_no_importable_files_returns_early_without_touching_matchers(self):
        missing = os.path.join(self.downloads_folder, 'ghost.cbz')

        result = manual_import_files(1, [missing])

        self.assertEqual(result['imported'], [])
        self.assertEqual(len(result['skipped']), 1)
        self.mock_scan_files.assert_not_called()
        self.mock_set_file_matching.assert_not_called()


if __name__ == '__main__':
    unittest.main()
