import unittest
from unittest.mock import call, patch

from backend.features.library_import_persistent import (
    RecheckContinuousLibraryImport,
)
from backend.features.tasks import task_library


class continuous_import_review_recheck(unittest.TestCase):
    @staticmethod
    def _file_data(series='Batman', issue_number=1.0):
        return {
            'series': series,
            'year': 2020,
            'volume_number': 1,
            'special_version': None,
            'issue_number': issue_number,
            'annual': False,
        }

    def test_recheck_task_is_registered(self):
        self.assertIs(
            task_library['recheck_continuous_library_import'],
            RecheckContinuousLibraryImport,
        )

    def test_recheck_retires_old_paused_jobs_and_uses_current_paths(self):
        task = RecheckContinuousLibraryImport()
        current_comic = '/library/Batman Renamed (2020)/Batman 001.cbz'
        current_cover = '/library/Batman Renamed (2020)/cover.jpg'
        collected = {
            current_comic: self._file_data(),
            current_cover: self._file_data(),
        }
        file_to_folder = {
            current_comic: '/library/Batman Renamed (2020)',
            current_cover: '/library/Batman Renamed (2020)',
        }

        with patch(
            'backend.features.library_import_persistent.get_paused_job',
            side_effect=[{'id': 12}, {'id': 8}, None],
        ), patch(
            'backend.features.library_import_persistent.mark_job_complete',
        ) as mark_complete, patch(
            'backend.features.library_import_persistent._collect_unimported_files',
            return_value=(collected, file_to_folder),
        ), patch(
            'backend.features.library_import_persistent.create_job',
            return_value=31,
        ) as create_job, patch(
            'backend.features.library_import_persistent.mark_job_paused',
        ) as mark_paused, patch(
            'backend.features.library_import_persistent.WebSocket',
        ):
            task.run()

        self.assertEqual(
            mark_complete.call_args_list,
            [call(12), call(8)],
        )
        self.assertEqual(
            list(create_job.call_args.args[0]),
            ['/library/Batman Renamed (2020)'],
        )
        mark_paused.assert_called_once_with(31)
        self.assertIn('1 current unimported folders', task.message)

    def test_recheck_stages_empty_fresh_pass_when_nothing_is_unimported(self):
        task = RecheckContinuousLibraryImport()

        with patch(
            'backend.features.library_import_persistent.get_paused_job',
            return_value=None,
        ), patch(
            'backend.features.library_import_persistent._collect_unimported_files',
            return_value=({}, {}),
        ), patch(
            'backend.features.library_import_persistent.create_job',
            return_value=44,
        ) as create_job, patch(
            'backend.features.library_import_persistent.mark_job_paused',
        ) as mark_paused, patch(
            'backend.features.library_import_persistent.WebSocket',
        ):
            task.run()

        self.assertEqual(list(create_job.call_args.args[0]), [])
        mark_paused.assert_called_once_with(44)
        self.assertIn('0 current unimported folders', task.message)


if __name__ == '__main__':
    unittest.main()
