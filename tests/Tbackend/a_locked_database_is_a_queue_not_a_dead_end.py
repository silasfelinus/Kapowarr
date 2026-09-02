# -*- coding: utf-8 -*-

"""A locked database used to be fatal.

On 2026-09-01 three threads died of `sqlite3.OperationalError: database is
locked` in one run: two `DownloadThread`s inside `remove_from_queue`, and the
`TaskIntervalThread` inside `__check_intervals`. The last of those is the
worst kind of failure, because that method is the only thing that schedules
the next run of itself: losing it meant no task ran on an interval again
until the container was restarted.

Three things have to hold for that not to repeat:

  1. Contention is waited out, not surrendered to.
  2. A write that nobody wrapped in a transaction commits immediately, so it
     can't sit on the write lock through the caller's next 80 seconds of
     indexer traffic.
  3. When the database does defeat a thread, the thread cleans up after
     itself and carries on.
"""

import logging
import sqlite3
import unittest
from unittest.mock import MagicMock, patch

from Tbackend.a_test_database_behaves_like_the_real_one import (
    connect as connect_test_db, cursor as test_db_cursor)
from backend.base.definitions import Constants, DownloadState
from backend.internals import db
from backend.internals.db import KapowarrCursor


class WatchedCursor(KapowarrCursor):
    """Records the statements it is given, so a test can see the transaction
    the cursor opened around them."""

    def __init__(self, connection, /):
        super().__init__(connection)
        self.seen = []
        return

    def execute(self, *args, **kwargs):
        self.seen.append(args[0] if args else '')
        return super().execute(*args, **kwargs)


def locked():
    return sqlite3.OperationalError('database is locked')


class a_locked_statement_is_retried(unittest.TestCase):
    def setUp(self):
        self.connection = connect_test_db()
        self.connection.execute('CREATE TABLE t(id INTEGER PRIMARY KEY);')
        self.cursor = test_db_cursor(self.connection)
        # Nothing here should actually sleep.
        self.slept = []
        patcher = patch.object(db, 'sleep', self.slept.append)
        patcher.start()
        self.addCleanup(patcher.stop)
        return

    def test_a_lock_is_waited_out_and_the_answer_still_comes_back(self):
        failures = [locked(), locked()]

        def flaky(value):
            if failures:
                raise failures.pop()
            return value

        self.assertEqual(
            KapowarrCursor._run_waiting_for_lock(flaky, 'written'),
            'written'
        )
        self.assertEqual(len(self.slept), 2)
        return

    def test_the_wait_backs_off_instead_of_hammering(self):
        attempts = []

        def flaky():
            attempts.append(1)
            if len(attempts) <= 6:
                raise locked()
            return None

        KapowarrCursor._run_waiting_for_lock(flaky)

        self.assertEqual(len(self.slept), 6)
        # Jitter means no two waits are predictable individually, but the
        # trend has to be upwards and it has to stop growing at the cap.
        self.assertGreater(self.slept[-1], self.slept[0])
        self.assertLessEqual(
            max(self.slept),
            Constants.DB_LOCK_RETRY_MAX_WAIT * 1.5
        )
        return

    def test_a_real_error_is_not_retried(self):
        "A typo in a query is a bug, and has to arrive as one."
        with self.assertRaises(sqlite3.OperationalError):
            self.cursor.execute('SELECT nonexistent FROM t;')

        self.assertEqual(self.slept, [])
        return

    def test_it_gives_up_eventually_rather_than_retrying_forever(self):
        clock = [0.0]

        def creep():
            # Every look at the clock is a second later than the last, so the
            # deadline arrives however short the (patched out) sleeps are.
            clock[0] += 1.0
            return clock[0]

        def always_locked():
            raise locked()

        with patch.object(db, 'monotonic', creep):
            with self.assertRaises(sqlite3.OperationalError):
                KapowarrCursor._run_waiting_for_lock(always_locked)

        self.assertLessEqual(
            len(self.slept),
            Constants.DB_LOCK_RETRY_TIMEOUT
        )
        return

    def test_a_write_goes_through_the_waiting(self):
        "The retry is on the path every statement in the backend takes."
        with patch.object(
            KapowarrCursor, '_run_waiting_for_lock',
            return_value=None
        ) as waiting:
            self.cursor.execute('INSERT INTO t(id) VALUES (1);')

        waiting.assert_called_once()
        return

    def test_a_batch_is_one_transaction_so_a_retry_cannot_double_it(self):
        """`executemany` under autocommit would otherwise land row by row,
        and a retry halfway through would re-apply the rows that already
        went in."""
        cursor = WatchedCursor(self.connection)
        cursor.executemany(
            'INSERT INTO t(id) VALUES (?);',
            ((10,), (11,), (12,))
        )

        self.assertEqual(cursor.seen, ['BEGIN IMMEDIATE;', 'COMMIT;'])
        self.assertEqual(
            [r[0] for r in self.cursor.execute(
                'SELECT id FROM t ORDER BY id;').fetchall()],
            [10, 11, 12]
        )
        return


class a_write_does_not_sit_on_the_lock(unittest.TestCase):
    def test_the_connection_is_in_autocommit(self):
        """Legacy `isolation_level` is what let a single uncommitted write
        hold the writer for the rest of the thread's life."""
        self.assertIsNone(db.AUTOCOMMIT)
        return

    def test_commit_stays_out_of_the_way_of_a_transaction_block(self):
        """A `commit()` inside a `with cursor:` block would cut it in half,
        leaving the rest of the block unprotected."""
        connection = connect_test_db()
        connection.execute('CREATE TABLE t(id INTEGER PRIMARY KEY);')
        cursor = test_db_cursor(connection)

        with patch.object(db, 'get_db', return_value=cursor):
            with cursor:
                cursor.execute('INSERT INTO t(id) VALUES (1);')
                db.commit()
                self.assertTrue(connection.in_transaction)

            self.assertFalse(connection.in_transaction)

            # Outside a block it commits as it always did.
            cursor.execute('INSERT INTO t(id) VALUES (2);')
            db.commit()

        self.assertEqual(
            [r[0] for r in cursor.execute(
                'SELECT id FROM t ORDER BY id;').fetchall()],
            [1, 2]
        )
        return


class a_thread_the_database_defeats_carries_on(unittest.TestCase):
    def setUp(self):
        # These tests provoke the failures on purpose; their tracebacks are
        # the expected result, not something to print over the test run.
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, logging.NOTSET)
        return

    def test_the_scheduler_books_its_next_run_even_when_the_check_fails(self):
        from backend.features.tasks_core import TaskHandler

        handler = TaskHandler.__new__(TaskHandler)
        handler.context = MagicMock(
            side_effect=sqlite3.OperationalError('database is locked'))

        with patch.object(TaskHandler, 'handle_intervals') as reschedule:
            handler._TaskHandler__check_intervals()

        reschedule.assert_called_once()
        return

    def test_it_still_sets_an_alarm_when_it_cannot_work_out_the_time(self):
        from backend.features import tasks_core
        from backend.features.tasks_core import TaskHandler

        handler = TaskHandler.__new__(TaskHandler)
        handler.context = MagicMock(
            side_effect=sqlite3.OperationalError('database is locked'))

        timers = []

        def record(delay, target):
            timers.append(delay)
            return MagicMock()

        with patch.object(tasks_core, 'Timer', record):
            handler.handle_intervals()

        self.assertEqual(timers, [TaskHandler.INTERVAL_FALLBACK_DELAY])
        return

    def test_a_download_leaves_the_queue_even_if_post_processing_fails(self):
        from backend.features import download_queue as dq

        handler = dq.DownloadHandler.__new__(dq.DownloadHandler)
        download = MagicMock()
        download.id = 4
        download.state = DownloadState.DOWNLOADING_STATE
        handler.queue = [download]

        with patch.object(dq, 'WebSocket'), \
                patch.object(dq.DownloadHandler, '_process_queue'), \
                patch.object(
                    dq.PostProcessor, 'success',
                    side_effect=sqlite3.OperationalError(
                        'database is locked')):
            handler._DownloadHandler__run_download(download)

        self.assertEqual(handler.queue, [])
        return
