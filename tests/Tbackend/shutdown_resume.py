# -*- coding: utf-8 -*-

"""A restart must not cost the continuous import its progress."""

import unittest
from threading import Event, Thread
from unittest.mock import MagicMock, patch

from backend.features import library_import_state as state
import backend.features.tasks as tasks_module
from backend.features.tasks import TaskHandler
from Tbackend.a_test_database_behaves_like_the_real_one import (
    connect as connect_test_db, cursor as test_db_cursor)


class stopping_reaches_every_lane(unittest.TestCase):
    """Lanes run concurrently, so more than one task can be running.

    Signalling only queue[0] left every other lane's task running through
    interpreter shutdown. For the continuous import that meant being killed
    mid-folder instead of stopping at a boundary -- and an error raised on the
    way down marked its job paused, so it did not auto-resume. Every restart
    cost the import.
    """

    def _entry(self, task_id, lane, started, honours_stop=True):
        """A queue entry whose task exposes request_stop, like the importer."""
        task = MagicMock()
        task.stop = False
        task.stop_requested = False
        task.display_title = f'Task {task_id}'
        task.request_stop = lambda: setattr(task, 'stop_requested', True)

        def _run():
            started.set()
            while not task.stop:
                if not honours_stop and task.stop_requested:
                    pass
                Event().wait(0.01)

        thread = Thread(target=_run, daemon=True)
        return {
            'id': task_id, 'task': task, 'status': 'running',
            'lane': lane, 'thread': thread
        }

    def _handler(self, queue, grace=None):
        handler = TaskHandler.__new__(TaskHandler)
        handler.task_interval_waiter = None
        handler.queue = queue
        if grace is not None:
            tasks_module.SHUTDOWN_GRACE_SECONDS = grace
            self.addCleanup(
                setattr, tasks_module, 'SHUTDOWN_GRACE_SECONDS', 30.0
            )
        return handler

    def test_shutdown_sets_stop_not_stop_requested(self):
        """The two mean different things to a task that persists its state.

        `stop_requested` is the user's Stop button, which the continuous
        import records as a *paused* job -- and a paused job does not
        auto-resume. `stop` is process shutdown, recorded as still *running*,
        which does. Calling request_stop() here told every restart it was a
        human pausing the import.
        """
        started = Event()
        entry = self._entry(1, 'continuous_import', started)
        handler = self._handler([entry])
        entry['thread'].start()
        self.assertTrue(started.wait(2))

        handler.stop_handle()

        self.assertTrue(entry['task'].stop, 'shutdown must set stop')
        self.assertFalse(
            entry['task'].stop_requested,
            'shutdown must not look like the user pressing Stop'
        )

    def test_every_running_lane_is_stopped(self):
        first_started, second_started = Event(), Event()
        first = self._entry(1, 'default', first_started)
        second = self._entry(2, 'continuous_import', second_started)
        handler = self._handler([first, second])

        first['thread'].start()
        second['thread'].start()
        self.assertTrue(first_started.wait(2))
        self.assertTrue(second_started.wait(2))

        handler.stop_handle()

        self.assertTrue(first['task'].stop)
        self.assertTrue(second['task'].stop)
        self.assertFalse(first['thread'].is_alive())
        self.assertFalse(second['thread'].is_alive())

    def test_a_queued_task_is_left_alone(self):
        """It never started, so there is nothing to wind down."""
        queued = self._entry(1, 'default', Event())
        queued['status'] = 'queued'
        handler = self._handler([queued])

        handler.stop_handle()

        self.assertFalse(queued['task'].stop)

    def test_shutdown_does_not_hang_on_a_task_that_ignores_stop(self):
        started, stubborn = Event(), Event()
        entry = self._entry(1, 'default', started)
        entry['thread'] = Thread(
            target=lambda: (started.set(), stubborn.wait(30)), daemon=True
        )
        handler = self._handler([entry], grace=0.2)
        entry['thread'].start()
        self.assertTrue(started.wait(2))

        handler.stop_handle()  # returns rather than blocking for 30s
        stubborn.set()

    def test_an_empty_queue_is_fine(self):
        self._handler([]).stop_handle()


class a_shutdown_is_not_a_failure(unittest.TestCase):
    """The job row is already `running`, which is what startup resumes from.

    Marking it paused because the worker raised while the process was going
    down is what turned every restart -- an update, a config change, a crash
    fix -- into a stalled import needing a manual Resume.
    """

    def setUp(self):
        self.connection = connect_test_db()
        self.connection.execute(
            'CREATE TABLE files(id INTEGER PRIMARY KEY, filepath TEXT UNIQUE);'
        )
        for target in (
            patch.object(
                state, 'get_db',
                side_effect=lambda *a, **k: test_db_cursor(self.connection)
            ),
            patch.object(state, 'commit', side_effect=self.connection.commit),
            patch.object(state, 'exists', return_value=True),
        ):
            target.start()
            self.addCleanup(target.stop)
        self.addCleanup(self.connection.close)

    def _worker(self, job_id, stopping):
        from backend.features.library_import_persistent import (
            PersistentContinuousLibraryImport)

        worker = PersistentContinuousLibraryImport.__new__(
            PersistentContinuousLibraryImport
        )
        worker.job_id = job_id
        worker.stop = stopping
        worker.stop_requested = False
        return worker

    def _raise_through_run(self, worker):
        from backend.features import library_import_persistent as lip

        with patch.object(
            lip, 'get_pending_folders', side_effect=RuntimeError('torn down')
        ), patch.object(
            lip.PersistentContinuousLibraryImport, '_start_or_resume_job',
            return_value=({}, True)
        ), patch.object(
            lip.PersistentContinuousLibraryImport, '_emit_persistent_status'
        ):
            with self.assertRaises(RuntimeError):
                worker.run()

    def test_a_failure_during_shutdown_leaves_the_job_resumable(self):
        job_id = state.create_job(['/a', '/b'])

        self._raise_through_run(self._worker(job_id, stopping=True))

        self.assertIsNotNone(
            state.get_running_job(),
            'a job left running is what startup picks back up'
        )
        self.assertIsNone(state.get_paused_job())

    def test_a_failure_while_running_normally_still_pauses(self):
        """A real worker error must not spin on every page load."""
        job_id = state.create_job(['/a', '/b'])

        self._raise_through_run(self._worker(job_id, stopping=False))

        paused = state.get_paused_job()
        self.assertIsNotNone(paused)
        self.assertEqual(paused['id'], job_id)
        self.assertIn('RuntimeError', paused['last_error'])


if __name__ == '__main__':
    unittest.main()
