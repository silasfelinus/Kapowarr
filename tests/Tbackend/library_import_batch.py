import unittest
from types import SimpleNamespace
from unittest.mock import patch

from backend.features.library_import import import_library


class resumable_library_import(unittest.TestCase):
    def _patches(self, add_side_effect):
        root = SimpleNamespace(id=1, folder='/library/')
        return (
            patch(
                'backend.features.library_import.RootFolders.get_all',
                return_value=[root]
            ),
            patch(
                'backend.features.library_import.Library.add',
                side_effect=add_side_effect
            ),
            patch('backend.features.library_import.scan_files'),
            patch('backend.features.library_import.commit')
        )

    def test_manual_batch_keeps_working_after_one_volume_fails(self):
        patches = self._patches([RuntimeError('stale folder'), 22])
        with patches[0], patches[1], patches[2] as scan, patches[3]:
            result = import_library([
                {'id': 101, 'filepath': '/library/Bad/Bad 001.cbz'},
                {'id': 202, 'filepath': '/library/Good/Good 001.cbz'}
            ], continue_on_error=True)

        self.assertEqual([row['id'] for row in result['failed']], [101])
        self.assertEqual(result['failed'][0]['reason'], 'stale folder')
        self.assertEqual([row['id'] for row in result['imported']], [202])
        scan.assert_called_once_with(
            22, filepath_filter=['/library/Good/Good 001.cbz']
        )

    def test_background_import_still_raises_for_task_retry(self):
        patches = self._patches(RuntimeError('temporary failure'))
        with patches[0], patches[1], patches[2], patches[3]:
            with self.assertRaisesRegex(RuntimeError, 'temporary failure'):
                import_library([
                    {'id': 101, 'filepath': '/library/Bad/Bad 001.cbz'}
                ])

    def test_outside_root_is_reported_instead_of_silently_dropped(self):
        patches = self._patches(22)
        with patches[0], patches[1] as add, patches[2], patches[3]:
            result = import_library([
                {'id': 101, 'filepath': '/downloads/Bad 001.cbz'}
            ], continue_on_error=True)

        self.assertEqual(result['imported'], [])
        self.assertEqual(result['failed'], [])
        self.assertEqual(result['skipped'][0]['id'], 101)
        self.assertIn('outside', result['skipped'][0]['reason'])
        add.assert_not_called()


if __name__ == '__main__':
    unittest.main()
