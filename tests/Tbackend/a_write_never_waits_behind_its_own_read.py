# -*- coding: utf-8 -*-

"""A cursor left open is a write that can never succeed.

An unfinished `SELECT` holds a read transaction on its connection, and SQLite
will not let a connection upgrade a read transaction into a write one: that
would be a deadlock with itself, so it returns "database is locked" AT ONCE,
without consulting the busy handler and without waiting. No amount of
retrying helps, because waiting is not what the write is short of.

`SearchAll.run` walked its volume list that way and stamped a row per volume
as it went. On 2026-09-01 every one of those stamps failed instantly, twenty
retries deep, fifty seconds a volume, and the rotation stopped advancing --
while the log said only "database is locked", which reads like contention and
was not.

The first test is the real behaviour, on a real database. The second is the
guard: no loop in the backend may write while a cursor it is walking is still
open.
"""

import ast
import pathlib
import re
import sqlite3
import threading
import time
import unittest

from Tbackend.a_test_database_behaves_like_the_real_one import (
    connect as connect_test_db, cursor as test_db_cursor)


class an_open_cursor_makes_a_write_unretryable(unittest.TestCase):
    """Not a mock: the point is what SQLite itself does."""

    def setUp(self):
        import os
        import tempfile
        self.path = os.path.join(tempfile.mkdtemp(), 'k.db')
        setup = sqlite3.connect(self.path)
        setup.execute('PRAGMA journal_mode = WAL;')
        setup.execute('CREATE TABLE volumes(id INTEGER PRIMARY KEY, stamp INT);')
        setup.executemany('INSERT INTO volumes(id, stamp) VALUES (?, 0);',
                          [(i,) for i in range(1, 10)])
        setup.commit()
        setup.close()
        return

    def _write_while_a_writer_holds_the_lock(self, keep_select_open):
        """Try one write while another connection holds the write lock, with
        this connection's own `SELECT` either still open or drained.

        Returns:
            Tuple[Union[str, None], float]: The error, if any, and how long
                the write took.
        """
        holding = threading.Event()
        release = 1.5

        def hog():
            conn = connect_test_db(self.path)
            conn.execute('BEGIN IMMEDIATE;')
            conn.execute('UPDATE volumes SET stamp = 1 WHERE id = 9;')
            holding.set()
            time.sleep(release)
            conn.execute('COMMIT;')
            conn.close()

        writer = threading.Thread(target=hog)
        writer.start()
        try:
            holding.wait(timeout=5)
            connection = connect_test_db(self.path)
            sweep = test_db_cursor(connection)
            reader = sweep.execute('SELECT id FROM volumes ORDER BY id;')
            if keep_select_open:
                next(iter(reader))
            else:
                reader.fetchall()

            started = time.time()
            try:
                # Straight to sqlite3, under the retrying wrapper, so the
                # measurement is of SQLite's own behaviour.
                connection.execute(
                    'UPDATE volumes SET stamp = 2 WHERE id = 1;')
                return None, time.time() - started
            except sqlite3.OperationalError as error:
                return str(error), time.time() - started

        finally:
            writer.join()

    def test_it_fails_at_once_rather_than_waiting_for_the_lock(self):
        error, waited = self._write_while_a_writer_holds_the_lock(
            keep_select_open=True)

        self.assertIsNotNone(error)
        self.assertIn('locked', str(error))
        # The point: it did not wait. A retry would meet the same answer.
        self.assertLess(waited, 0.5)
        return

    def test_draining_the_cursor_first_turns_it_back_into_a_wait(self):
        error, waited = self._write_while_a_writer_holds_the_lock(
            keep_select_open=False)

        self.assertIsNone(error)
        # It waited for the holder instead of being refused outright.
        self.assertGreater(waited, 0.5)
        return


class no_loop_writes_while_walking_a_cursor(unittest.TestCase):
    DML = re.compile(r'^\s*(INSERT|UPDATE|DELETE|REPLACE)\b', re.I | re.M)
    WRITE_HELPERS = (
        'add_file', 'add_files', 'delete_file', 'delete_filepath',
        'delete_filepaths', 'update_filepaths', 'add_multiple',
        '_mark_searched'
    )

    def _writes(self, node):
        for n in ast.walk(node):
            if not (isinstance(n, ast.Call)
                    and isinstance(n.func, ast.Attribute)):
                continue
            if n.func.attr in ('execute', 'executemany'):
                for a in n.args:
                    if (isinstance(a, ast.Constant)
                            and isinstance(a.value, str)
                            and self.DML.search(a.value)):
                        return True
            if n.func.attr in self.WRITE_HELPERS:
                return True
        return False

    @staticmethod
    def _is_live_cursor(iterated):
        "Whether the loop walks a cursor rather than rows already read."
        if isinstance(iterated, ast.Name):
            return 'cursor' in iterated.id.lower()
        if (isinstance(iterated, ast.Call)
                and isinstance(iterated.func, ast.Attribute)):
            # `.fetchall()` and friends have already read everything.
            return iterated.func.attr in ('execute', 'executemany')
        return False

    def test_the_backend_has_none(self):
        offenders = []
        root = pathlib.Path(__file__).resolve().parents[2] / 'backend'
        for path in sorted(root.rglob('*.py')):
            tree = ast.parse(path.read_text(), str(path))
            for node in ast.walk(tree):
                if not isinstance(node, (ast.For, ast.AsyncFor)):
                    continue
                if (self._is_live_cursor(node.iter)
                        and self._writes(node)):
                    offenders.append(
                        f'{path.relative_to(root.parent)}:{node.lineno}')

        self.assertEqual(offenders, [], msg=(
            'These loops write while a cursor they are walking is still '
            'open, so the write is refused instantly and no retry can save '
            'it. Read the rows first (.fetchall()).'
        ))
        return
