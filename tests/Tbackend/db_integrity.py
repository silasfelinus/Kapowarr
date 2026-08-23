# -*- coding: utf-8 -*-

import os
import sqlite3
import tempfile
import unittest

from backend.internals.db_integrity import (
    DatabaseIntegrityError,
    verify_database_integrity,
)


class DatabaseIntegrityTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

    def path(self, name='Kapowarr.db'):
        return os.path.join(self.temp.name, name)

    def test_missing_database_is_left_for_setup_db_to_create(self):
        verify_database_integrity(self.path())

    def test_empty_database_is_left_for_setup_db_to_create(self):
        open(self.path(), 'wb').close()
        verify_database_integrity(self.path())

    def test_valid_database_passes(self):
        path = self.path()
        connection = sqlite3.connect(path)
        try:
            connection.execute('CREATE TABLE marker(value TEXT);')
            connection.execute('INSERT INTO marker VALUES ("ok");')
            connection.commit()
        finally:
            connection.close()

        verify_database_integrity(path)

    def test_malformed_database_fails_before_startup_writes(self):
        path = self.path()
        with open(path, 'wb') as database:
            database.write(b'not a sqlite database')

        with self.assertRaises(DatabaseIntegrityError) as raised:
            verify_database_integrity(path)

        self.assertEqual(raised.exception.filepath, path)
        self.assertIn('will not start writers', str(raised.exception))
        with open(path, 'rb') as database:
            self.assertEqual(database.read(), b'not a sqlite database')


if __name__ == '__main__':
    unittest.main()
