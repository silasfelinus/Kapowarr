# -*- coding: utf-8 -*-

"""Continuous import state has to outlive the task that produced it.

The task queue is process-local and drops a task the moment it finishes, so
anything that can only be reached through a running task disappears when the
pass ends, when the process restarts, and on every page reload. The folder
checkpoints and review holds live in SQLite precisely so they do not.
"""

import json
import unittest
from unittest.mock import patch

from flask import Flask

import frontend.api as api_module
import frontend.library_import_status as status_module
from backend.features import library_import_state as state
from Tbackend.a_test_database_behaves_like_the_real_one import (
    connect as connect_test_db, cursor as test_db_cursor)


def _hold(folder: str, reason: str = 'no_candidate', cv_id=None):
    return [{
        'filepath': f'{folder}/issue 1.cbz',
        'file_title': 'issue 1',
        'cv': {
            'id': cv_id,
            'title': None,
            'issue_count': None,
            'link': None
        },
        'group_number': f'continuous-review-{folder}-1',
        'folder': folder,
        'review_reason': reason
    }]


class _StateHarness(unittest.TestCase):
    def setUp(self):
        self.connection = connect_test_db()
        self.connection.execute(
            "CREATE TABLE files(id INTEGER PRIMARY KEY, filepath TEXT UNIQUE);"
        )
        # These cover job bookkeeping, not the filesystem, so held files are
        # taken to exist unless a test says one moved. `moved_paths` is what a
        # file being imported or renamed out from under a hold looks like here.
        self.moved_paths = set()
        patches = (
            patch.object(
                state, 'get_db',
                side_effect=lambda *a, **k: test_db_cursor(self.connection)
            ),
            patch.object(state, 'commit', side_effect=self.connection.commit),
            patch.object(
                state, 'exists',
                side_effect=lambda path: path not in self.moved_paths
            ),
        )
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self.connection.close)

    def _finished_pass(self, folders, held):
        """Run a pass to completion, holding ``held`` folders for review."""
        job_id = state.create_job(folders)
        for folder in folders:
            state.mark_folder_processing(job_id, folder)
            if folder in held:
                state.mark_folder_result(
                    job_id, folder, 0, 'no_candidate', _hold(folder)
                )
            else:
                state.mark_folder_result(job_id, folder, 1, None, [])
        state.mark_job_complete(job_id)
        return job_id


class active_job_lookup(_StateHarness):
    def test_a_finished_pass_is_still_reachable(self):
        job_id = self._finished_pass(
            ['/library/A', '/library/B'], held={'/library/B'}
        )

        # This is the state the UI was blind to: nothing running, nothing
        # paused, and a completed pass holding a folder for review.
        self.assertIsNone(state.get_running_job())
        self.assertIsNone(state.get_paused_job())

        active = state.get_active_job()
        self.assertIsNotNone(active)
        self.assertEqual(active['id'], job_id)
        self.assertEqual(active['status'], state.JOB_COMPLETE)

    def test_a_running_pass_wins_over_older_finished_ones(self):
        self._finished_pass(['/library/A'], held=set())
        running = state.create_job(['/library/B'])

        self.assertEqual(state.get_active_job()['id'], running)

    def test_a_paused_pass_wins_over_older_finished_ones(self):
        self._finished_pass(['/library/A'], held=set())
        paused = state.create_job(['/library/B'])
        state.mark_job_paused(paused)

        self.assertEqual(state.get_active_job()['id'], paused)

    def test_no_pass_at_all_reports_nothing(self):
        state.ensure_schema()
        self.assertIsNone(state.get_active_job())
        self.assertEqual(state.get_outstanding_review_items(), [])


class outstanding_review_queue(_StateHarness):
    def test_holds_survive_the_pass_that_produced_them(self):
        self._finished_pass(
            ['/library/A', '/library/B'], held={'/library/B'}
        )

        items = state.get_outstanding_review_items()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]['folder'], '/library/B')

    def test_holds_survive_a_newly_started_pass(self):
        self._finished_pass(['/library/A'], held={'/library/A'})

        # Nothing imported the held folder, so the next pass finds the same
        # file unimported and queues the same folder again.
        state.create_job(['/library/A'])

        self.assertEqual(len(state.get_outstanding_review_items()), 1)

    def test_the_newest_pass_wins_for_a_folder_it_re_checked(self):
        self._finished_pass(['/library/A'], held={'/library/A'})
        second = state.create_job(['/library/A'])
        state.mark_folder_processing(second, '/library/A')
        state.mark_folder_result(
            second, '/library/A', 0, 'tie', _hold('/library/A', 'tie', 42)
        )

        items = state.get_outstanding_review_items()
        self.assertEqual(len(items), 1, 'a folder must not be listed twice')
        self.assertEqual(items[0]['review_reason'], 'tie')
        self.assertEqual(items[0]['cv']['id'], 42)

    def test_a_hold_resolved_by_hand_is_pruned_and_retired(self):
        job_id = self._finished_pass(['/library/A'], held={'/library/A'})
        self.connection.execute(
            "INSERT INTO files(filepath) VALUES ('/library/A/issue 1.cbz');"
        )
        self.connection.commit()

        self.assertEqual(state.get_outstanding_review_items(), [])
        state_row = self.connection.execute(
            "SELECT state FROM library_import_items WHERE job_id = ?;",
            (job_id,)
        ).fetchone()
        self.assertEqual(state_row['state'], state.ITEM_DONE)

    def test_single_job_review_reader_still_scopes_to_its_job(self):
        first = self._finished_pass(['/library/A'], held={'/library/A'})
        second = self._finished_pass(['/library/B'], held={'/library/B'})

        self.assertEqual(len(state.get_review_items(first)), 1)
        self.assertEqual(len(state.get_review_items(second)), 1)
        self.assertEqual(len(state.get_outstanding_review_items()), 2)


class continuous_status_endpoint(_StateHarness):
    def setUp(self):
        super().setUp()
        app = Flask(__name__)
        app.register_blueprint(api_module.api, url_prefix='/api')
        self.client = app.test_client()

        auth_patches = (
            patch.object(api_module, 'extract_key', return_value='key'),
            patch.object(api_module, 'StartTypeHandlers'),
            patch.object(status_module, 'TaskHandler'),
        )
        for p in auth_patches:
            started = p.start()
            self.addCleanup(p.stop)
            if p.attribute == 'TaskHandler':
                started.return_value.get_all.return_value = []

    def _get(self):
        response = self.client.get(
            '/api/libraryimport/continuous?api_key=key'
        )
        self.assertEqual(response.status_code, 200)
        return json.loads(response.data)['result']

    def test_reports_a_finished_pass_with_no_task_running(self):
        self._finished_pass(
            ['/library/A', '/library/B'], held={'/library/B'}
        )

        result = self._get()

        self.assertEqual(result['job']['status'], state.JOB_COMPLETE)
        self.assertEqual(result['job']['total_folders'], 2)
        self.assertEqual(result['job']['checked_folders'], 2)
        self.assertEqual(result['job']['imported_volumes'], 1)
        self.assertEqual(result['review_folders_outstanding'], 1)
        self.assertEqual(len(result['review_items']), 1)
        self.assertEqual(result['task'], {})

    def test_reports_nothing_on_an_installation_that_never_ran_one(self):
        state.ensure_schema()

        result = self._get()

        self.assertIsNone(result['job'])
        self.assertEqual(result['review_items'], [])
        self.assertEqual(result['review_folders_outstanding'], 0)

    def test_reports_the_live_pass_while_older_holds_stay_visible(self):
        self._finished_pass(['/library/A'], held={'/library/A'})
        running = state.create_job(['/library/A'])

        result = self._get()

        self.assertEqual(result['job']['id'], running)
        self.assertEqual(result['job']['status'], state.JOB_RUNNING)
        self.assertEqual(result['job']['remaining_folders'], 1)
        # The live pass has held nothing yet, but the backlog is still there.
        self.assertEqual(result['job']['review_folders'], 0)
        self.assertEqual(result['review_folders_outstanding'], 1)


class holds_for_files_that_moved(_StateHarness):
    """A hold records a path, and importing or renaming can invalidate it.

    Reconciling only against the ``files`` table cannot see this: the file is
    in ``files`` under its new path, and the recorded one is in neither
    ``files`` nor the filesystem. Such a row survived every read, could not be
    imported -- the move raises ``FileNotFoundError`` -- and could not be
    cleared, so the queue filled with rows whose only outcome was an error.
    """

    def test_a_hold_whose_file_moved_is_dropped(self):
        job_id = self._finished_pass(['/library/A'], held={'/library/A'})
        self.assertEqual(len(state.get_review_items(job_id)), 1)

        self.moved_paths.add('/library/A/issue 1.cbz')

        self.assertEqual(state.get_review_items(job_id), [])
        self.assertEqual(state.get_outstanding_review_items(), [])

    def test_a_folder_left_with_no_live_holds_counts_as_checked(self):
        job_id = self._finished_pass(['/library/A'], held={'/library/A'})
        self.moved_paths.add('/library/A/issue 1.cbz')

        details = state.get_job_details(job_id)

        self.assertEqual(details['review_items'], [])
        self.assertEqual(details['review_folders'], 0)
        self.assertEqual(
            details['checked_folders'], 1,
            'a folder with nothing left to review is finished, not pending'
        )

    def test_a_hold_whose_file_is_still_there_survives(self):
        job_id = self._finished_pass(['/library/A'], held={'/library/A'})
        self.moved_paths.add('/library/B/issue 1.cbz')

        self.assertEqual(len(state.get_review_items(job_id)), 1)


class stopping_a_pass(continuous_status_endpoint):
    """Stop has to work when there is no worker left to ask.

    A pass killed mid-folder leaves its job marked running with nothing in the
    task queue. Stopping was purely a queue operation, so in exactly that state
    it deleted nothing, changed nothing and said nothing -- while the page went
    on reporting a pass in progress, and only a process restart reconciled it.
    """

    def _stop(self):
        response = self.client.post(
            '/api/libraryimport/continuous/stop?api_key=key'
        )
        self.assertEqual(response.status_code, 200)
        return json.loads(response.data)['result']

    def test_a_job_marked_running_with_no_worker_is_reported_as_stalled(self):
        state.create_job(['/library/A'])

        result = self._get()

        self.assertEqual(result['job']['status'], state.JOB_RUNNING)
        self.assertFalse(result['job']['is_live'])
        self.assertTrue(result['job']['is_stalled'])

    def test_a_live_pass_is_not_reported_as_stalled(self):
        state.create_job(['/library/A'])
        status_module.TaskHandler.return_value.get_all.return_value = [{
            'id': 7,
            'action': 'continuous_library_import',
            'status': 'running',
            'message': 'Continuous import: 0/1 folders checked'
        }]

        result = self._get()

        self.assertTrue(result['job']['is_live'])
        self.assertFalse(result['job']['is_stalled'])

    def test_stopping_a_stalled_job_pauses_it_so_it_can_be_resumed(self):
        job_id = state.create_job(['/library/A'])

        self.assertEqual(self._stop()['stopped'], 'stalled_job')

        self.assertIsNone(state.get_running_job())
        paused = state.get_paused_job()
        self.assertIsNotNone(paused)
        self.assertEqual(paused['id'], job_id)

    def test_a_live_pass_is_stopped_through_the_task_queue(self):
        """A running worker owns its folder boundary, so ask it to stop rather
        than editing the job row out from under it."""
        state.create_job(['/library/A'])
        handler = status_module.TaskHandler.return_value
        handler.get_all.return_value = [{
            'id': 7,
            'action': 'continuous_library_import',
            'status': 'running',
            'message': 'Continuous import: 0/1 folders checked'
        }]

        result = self._stop()

        self.assertEqual(result['stopped'], 'task')
        handler.remove.assert_called_once_with(7)
        self.assertIsNotNone(
            state.get_running_job(),
            'the worker marks the job paused when it reaches a safe boundary'
        )

    def test_stopping_an_already_finished_pass_is_not_an_error(self):
        self._finished_pass(['/library/A'], held=set())

        self.assertEqual(self._stop()['stopped'], 'nothing')
