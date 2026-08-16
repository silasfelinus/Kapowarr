import sqlite3
import unittest
from unittest.mock import patch

from backend.features import library_import_state as state
from backend.features.library_import_persistent import (
    PersistentContinuousLibraryImport,
)
from backend.features.tasks import task_library


class durable_library_import_state(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(':memory:')
        self.connection.row_factory = sqlite3.Row
        self.connection.execute(
            "CREATE TABLE files(id INTEGER PRIMARY KEY, filepath TEXT UNIQUE);"
        )
        self.get_db_patch = patch.object(
            state,
            'get_db',
            side_effect=lambda *args, **kwargs: self.connection.cursor()
        )
        self.commit_patch = patch.object(
            state,
            'commit',
            side_effect=self.connection.commit
        )
        self.get_db_patch.start()
        self.commit_patch.start()

    def tearDown(self):
        self.commit_patch.stop()
        self.get_db_patch.stop()
        self.connection.close()

    def test_folder_checkpoints_survive_resume_and_review_reconciliation(self):
        job_id = state.create_job((
            '/library/Batman',
            '/library/Superman'
        ))
        summary = state.get_job_summary(job_id)
        self.assertEqual(summary['total_folders'], 2)
        self.assertEqual(summary['checked_folders'], 0)
        self.assertEqual(summary['status'], state.JOB_RUNNING)

        state.mark_folder_processing(job_id, '/library/Batman')
        state.mark_folder_result(
            job_id,
            '/library/Batman',
            imported_volumes=1,
            review_reason='tie',
            review_items=[{
                'filepath': '/library/Batman/Batman 001.cbz',
                'file_title': 'Batman 001',
                'cv': {
                    'id': 123,
                    'title': 'Batman (2020)',
                    'issue_count': 12,
                    'link': 'https://example.test/123'
                },
                'group_number': 'continuous-review-1-0-1',
                'folder': '/library/Batman',
                'review_reason': 'tie'
            }]
        )
        state.mark_folder_processing(job_id, '/library/Superman')

        # Simulate a process restart. Only the folder that was in-flight is
        # returned to pending; the completed/review checkpoint stays intact.
        state.mark_job_running(job_id)
        self.assertEqual(
            state.get_pending_folders(job_id),
            [(1, '/library/Superman')]
        )
        summary = state.get_job_summary(job_id)
        self.assertEqual(summary['checked_folders'], 1)
        self.assertEqual(summary['review_folders'], 1)
        self.assertEqual(summary['imported_volumes'], 1)

        # A user pause remains discoverable and resumable.
        state.mark_job_paused(job_id)
        paused = state.get_paused_job()
        self.assertIsNotNone(paused)
        self.assertEqual(paused['id'], job_id)
        state.mark_job_running(job_id)

        # Manual review imports through the normal library-import path. The
        # durable review queue reconciles against the canonical files table and
        # removes a row once that file has actually been imported.
        self.connection.execute(
            "INSERT INTO files(filepath) VALUES (?);",
            ('/library/Batman/Batman 001.cbz',)
        )
        self.connection.commit()
        self.assertEqual(state.get_review_items(job_id), [])
        summary = state.get_job_summary(job_id)
        self.assertEqual(summary['review_folders'], 0)
        self.assertEqual(summary['checked_folders'], 1)

        state.mark_folder_processing(job_id, '/library/Superman')
        state.mark_folder_result(
            job_id,
            '/library/Superman',
            imported_volumes=2,
            review_reason=None,
            review_items=[]
        )
        state.mark_job_complete(job_id)

        summary = state.get_job_summary(job_id)
        self.assertEqual(summary['status'], state.JOB_COMPLETE)
        self.assertEqual(summary['checked_folders'], 2)
        self.assertEqual(summary['remaining_folders'], 0)
        self.assertEqual(summary['imported_volumes'], 3)


class durable_continuous_import_task(unittest.TestCase):
    def test_registry_uses_persistent_task(self):
        self.assertIs(
            task_library['continuous_library_import'],
            PersistentContinuousLibraryImport
        )

    def test_running_job_is_recoverable_on_process_start(self):
        with patch(
            'backend.features.library_import_persistent.get_running_job',
            return_value={'id': 41, 'status': state.JOB_RUNNING}
        ):
            task = PersistentContinuousLibraryImport.restore_running_job()

        self.assertIsNotNone(task)
        self.assertEqual(task.job_id, 41)

    def test_explicit_start_resumes_user_paused_job(self):
        task = PersistentContinuousLibraryImport()
        with patch(
            'backend.features.library_import_persistent.get_paused_job',
            return_value={'id': 52, 'status': state.JOB_PAUSED}
        ), patch(
            'backend.features.library_import_persistent.mark_job_running'
        ) as mark_running:
            first_run_cache = task._start_or_resume_job()

        self.assertEqual(first_run_cache, {})
        self.assertEqual(task.job_id, 52)
        mark_running.assert_called_once_with(52)


if __name__ == '__main__':
    unittest.main()
