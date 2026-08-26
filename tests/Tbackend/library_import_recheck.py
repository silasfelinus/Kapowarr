import unittest
from unittest.mock import call, patch

from backend.features.library_import_maintenance import (
    RecheckContinuousLibraryImport,
    RescanContinuousLibraryImport,
    register_library_import_maintenance_tasks,
)
from backend.features.tasks import _task_lane, task_library


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

    def setUp(self):
        register_library_import_maintenance_tasks()

    def test_maintenance_tasks_are_registered_separately(self):
        self.assertIs(
            task_library['recheck_continuous_library_import'],
            RecheckContinuousLibraryImport,
        )
        self.assertIs(
            task_library['rescan_continuous_library_import'],
            RescanContinuousLibraryImport,
        )

    def test_maintenance_tasks_share_continuous_import_lane(self):
        self.assertEqual(_task_lane(RecheckContinuousLibraryImport()), 'continuous_import')
        self.assertEqual(_task_lane(RescanContinuousLibraryImport()), 'continuous_import')

    def test_recheck_retires_paused_jobs_and_uses_only_live_review_holds(self):
        task = RecheckContinuousLibraryImport()
        review_items = [
            {'folder': '/library/Held One', 'filepath': '/library/Held One/1.cbz'},
            {'folder': '/library/Held One', 'filepath': '/library/Held One/2.cbz'},
            {'folder': '/library/Held Two', 'filepath': '/library/Held Two/1.cbz'},
        ]

        with patch(
            'backend.features.library_import_maintenance.get_paused_job',
            side_effect=[{'id': 12}, {'id': 8}, None],
        ), patch(
            'backend.features.library_import_maintenance.mark_job_complete',
        ) as mark_complete, patch(
            'backend.features.library_import_maintenance.get_outstanding_review_items',
            return_value=review_items,
        ), patch(
            'backend.features.library_import_maintenance._collect_unimported_files',
        ) as collect_unimported, patch(
            'backend.features.library_import_maintenance.create_job',
            return_value=31,
        ) as create_job, patch(
            'backend.features.library_import_maintenance.mark_job_paused',
        ) as mark_paused, patch(
            'backend.features.library_import_maintenance.WebSocket',
        ):
            task.run()

        self.assertEqual(mark_complete.call_args_list, [call(12), call(8)])
        self.assertEqual(
            list(create_job.call_args.args[0]),
            ['/library/Held One', '/library/Held Two'],
        )
        collect_unimported.assert_not_called()
        mark_paused.assert_called_once_with(31)
        self.assertIn('2 review-held folders', task.message)

    def test_recheck_stages_empty_pass_when_no_review_holds_remain(self):
        task = RecheckContinuousLibraryImport()

        with patch(
            'backend.features.library_import_maintenance.get_paused_job',
            return_value=None,
        ), patch(
            'backend.features.library_import_maintenance.get_outstanding_review_items',
            return_value=[],
        ), patch(
            'backend.features.library_import_maintenance.create_job',
            return_value=44,
        ) as create_job, patch(
            'backend.features.library_import_maintenance.mark_job_paused',
        ) as mark_paused, patch(
            'backend.features.library_import_maintenance.WebSocket',
        ):
            task.run()

        self.assertEqual(list(create_job.call_args.args[0]), [])
        mark_paused.assert_called_once_with(44)
        self.assertIn('0 review-held folders', task.message)

    def test_rescan_rebuilds_from_every_current_untracked_folder(self):
        task = RescanContinuousLibraryImport()
        comic_a = '/library/Batman Renamed (2020)/Batman 001.cbz'
        comic_b = '/library/Superman (2021)/Superman 001.cbz'
        cover = '/library/Batman Renamed (2020)/cover.jpg'
        collected = {
            comic_a: self._file_data(),
            comic_b: self._file_data(series='Superman'),
            cover: self._file_data(),
        }
        file_to_folder = {
            comic_a: '/library/Batman Renamed (2020)',
            comic_b: '/library/Superman (2021)',
            cover: '/library/Batman Renamed (2020)',
        }

        with patch(
            'backend.features.library_import_maintenance.get_paused_job',
            return_value=None,
        ), patch(
            'backend.features.library_import_maintenance._collect_unimported_files',
            return_value=(collected, file_to_folder),
        ), patch(
            'backend.features.library_import_maintenance.is_library_import_artifact',
            side_effect=lambda path: path.endswith('cover.jpg'),
        ), patch(
            'backend.features.library_import_maintenance.get_outstanding_review_items',
        ) as review_items, patch(
            'backend.features.library_import_maintenance.create_job',
            return_value=52,
        ) as create_job, patch(
            'backend.features.library_import_maintenance.mark_job_paused',
        ) as mark_paused, patch(
            'backend.features.library_import_maintenance.WebSocket',
        ):
            task.run()

        self.assertEqual(
            list(create_job.call_args.args[0]),
            ['/library/Batman Renamed (2020)', '/library/Superman (2021)'],
        )
        review_items.assert_not_called()
        mark_paused.assert_called_once_with(52)
        self.assertIn('2 current unimported folders', task.message)


if __name__ == '__main__':
    unittest.main()
