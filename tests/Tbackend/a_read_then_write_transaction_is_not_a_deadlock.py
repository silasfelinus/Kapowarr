# -*- coding: utf-8 -*-

"""Two tasks died of "database is locked" during one import run.

`Search All` in `grab_size_limits._ensure_defaults`, and `refresh_and_scan`
in `scan_files` (2026-08-24). Both read before they write, and both were
killed outright while the library import held the writer.

The connection timeout was already 10 seconds and the database was already
in WAL mode, so neither is the explanation. A DEFERRED transaction takes no
lock until its first statement: read first and it holds a read lock, then
has to upgrade to write. SQLite will not make that upgrade wait -- the other
writer may be waiting on this connection's read lock, so waiting could
deadlock -- and returns SQLITE_BUSY immediately, without ever calling the
busy handler that `timeout` installs.

IMMEDIATE takes the write lock up front, which the busy handler *can* wait
on. These tests pin that difference down against real SQLite rather than
trusting the reasoning.
"""

import os
import sqlite3
import tempfile
import unittest

from backend.internals.db import WRITE_TRANSACTION_MODE


class the_connection_asks_for_the_write_lock_up_front(unittest.TestCase):
    def test_kapowarr_does_not_use_the_sqlite3_default(self):
        self.assertEqual(WRITE_TRANSACTION_MODE, 'IMMEDIATE')


class against_real_sqlite(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = os.path.join(self.temp.name, 'Kapowarr.db')

        setup = sqlite3.connect(self.path)
        setup.execute('PRAGMA journal_mode = wal;')
        setup.execute('CREATE TABLE config(key TEXT PRIMARY KEY, value TEXT);')
        setup.execute('INSERT INTO config VALUES ("seed", "1");')
        setup.commit()
        setup.close()

    def _connect(self, isolation_level):
        connection = sqlite3.connect(self.path, timeout=0.5)
        connection.isolation_level = isolation_level
        self.addCleanup(connection.close)
        return connection

    def _holding_the_writer(self):
        """A second connection mid-write, as a running import would be."""
        writer = self._connect(None)
        writer.execute('BEGIN IMMEDIATE;')
        writer.execute('INSERT INTO config VALUES ("writer", "1");')
        self.addCleanup(
            lambda: writer.in_transaction and writer.execute('ROLLBACK;')
        )
        return writer

    def _read_then_write(self, connection):
        """The shape both dead tasks had: SELECT, then INSERT."""
        connection.execute('SELECT value FROM config;').fetchall()
        connection.execute('INSERT INTO config VALUES ("task", "1");')

    def test_deferred_fails_instantly_and_ignores_the_timeout(self):
        """The old behaviour, kept as the thing being prevented."""
        self._holding_the_writer()

        with self.assertRaises(sqlite3.OperationalError) as caught:
            self._read_then_write(self._connect('DEFERRED'))

        self.assertIn('locked', str(caught.exception))

    def test_immediate_waits_on_the_busy_handler_instead(self):
        """It still times out here -- the writer never lets go -- but it
        waits for the timeout rather than failing on contact, which is what
        lets a real writer finish and the task go through."""
        self._holding_the_writer()

        from time import monotonic
        started = monotonic()
        with self.assertRaises(sqlite3.OperationalError):
            self._read_then_write(self._connect(WRITE_TRANSACTION_MODE))
        waited = monotonic() - started

        self.assertGreaterEqual(waited, 0.4)

    def test_and_goes_through_once_the_writer_commits(self):
        writer = self._holding_the_writer()
        writer.execute('COMMIT;')

        connection = self._connect(WRITE_TRANSACTION_MODE)
        self._read_then_write(connection)
        connection.commit()

        rows = connection.execute(
            'SELECT key FROM config ORDER BY key;'
        ).fetchall()
        self.assertEqual(
            [row[0] for row in rows], ['seed', 'task', 'writer']
        )

    def test_a_plain_read_never_takes_the_write_lock(self):
        """IMMEDIATE is only used for the implicit transaction sqlite3 opens
        before a write, so readers are unaffected by any of this."""
        self._holding_the_writer()

        connection = self._connect(WRITE_TRANSACTION_MODE)
        rows = connection.execute('SELECT key FROM config;').fetchall()

        self.assertEqual([row[0] for row in rows], ['seed'])
