# -*- coding: utf-8 -*-

"""A shared stand-in for the database in tests.

Tests used to patch `get_db` with a bare `sqlite3.Cursor`. That was close
enough for as long as the backend only ever called `execute` on it, but the
backend now also opens transactions with `with cursor:` and asks the
connection how deep it is in one -- neither of which a bare cursor has. Rather
than grow a hand-rolled double per test file, hand the tests the real
`KapowarrCursor` over an in-memory database, so what they exercise is the same
transaction behaviour production gets.
"""

import sqlite3
import unittest

from backend.internals.db import AUTOCOMMIT, KapowarrCursor


class TestDBConnection(sqlite3.Connection):
    """A connection with the one attribute `KapowarrCursor` needs of it."""

    transaction_depth = 0


def connect(path: str = ':memory:') -> TestDBConnection:
    """Open a database that behaves the way Kapowarr's own does.

    Args:
        path (str, optional): The database to open. Defaults to in-memory.

    Returns:
        TestDBConnection: The connection.
    """
    connection = sqlite3.connect(path, factory=TestDBConnection)
    connection.isolation_level = AUTOCOMMIT
    connection.row_factory = sqlite3.Row
    connection.execute('PRAGMA foreign_keys = ON;')
    return connection


def cursor(connection: sqlite3.Connection) -> KapowarrCursor:
    """Get a cursor of the same class the backend is handed in production.

    Args:
        connection (sqlite3.Connection): The connection to get one from.

    Returns:
        KapowarrCursor: The cursor.
    """
    c = KapowarrCursor(connection) # type: ignore
    c.row_factory = sqlite3.Row
    return c


class the_double_is_the_real_cursor(unittest.TestCase):
    def setUp(self):
        self.connection = connect()
        self.connection.execute('CREATE TABLE t(id INTEGER PRIMARY KEY);')
        self.cursor = cursor(self.connection)
        return

    def test_a_lone_write_commits_on_its_own(self):
        "Nothing is left holding the write lock after a single statement."
        self.cursor.execute('INSERT INTO t(id) VALUES (1);')
        self.assertFalse(self.connection.in_transaction)
        return

    def test_a_block_commits_at_the_end(self):
        with self.cursor:
            self.cursor.execute('INSERT INTO t(id) VALUES (2);')
            self.assertTrue(self.connection.in_transaction)

        self.assertFalse(self.connection.in_transaction)
        self.assertEqual(
            self.cursor.execute('SELECT id FROM t;').exists(), 2)
        return

    def test_a_block_rolls_back_when_it_fails(self):
        with self.assertRaises(RuntimeError):
            with self.cursor:
                self.cursor.execute('INSERT INTO t(id) VALUES (3);')
                raise RuntimeError('no')

        self.assertIsNone(self.cursor.execute('SELECT id FROM t;').exists())
        return

    def test_blocks_nest(self):
        "The outermost block owns the transaction; the inner ones ride along."
        with self.cursor:
            with self.cursor:
                self.cursor.execute('INSERT INTO t(id) VALUES (4);')

            # The inner block ending must not have committed anything.
            self.assertTrue(self.connection.in_transaction)
            self.assertEqual(self.connection.transaction_depth, 1)

        self.assertFalse(self.connection.in_transaction)
        self.assertEqual(self.connection.transaction_depth, 0)
        self.assertEqual(
            self.cursor.execute('SELECT id FROM t;').exists(), 4)
        return

    def test_a_failure_inside_a_nested_block_rolls_the_whole_thing_back(self):
        with self.assertRaises(RuntimeError):
            with self.cursor:
                self.cursor.execute('INSERT INTO t(id) VALUES (5);')
                with self.cursor:
                    self.cursor.execute('INSERT INTO t(id) VALUES (6);')
                    raise RuntimeError('no')

        self.assertIsNone(self.cursor.execute('SELECT id FROM t;').exists())
        self.assertEqual(self.connection.transaction_depth, 0)
        return
