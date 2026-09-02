# -*- coding: utf-8 -*-

"""SQLite has one writer for the whole database.

`scan_files` asked for a file's ID the moment it decided to keep it, which
meant one write per file. That runs in a pool of up to one process per core,
so a scan of a large library became a storm of write-lock acquisitions, and
everything else in the application queued behind it -- measurably: with
sixteen scan workers, a small write elsewhere got through four times in eight
seconds, against twenty-three once the registrations were batched.

The loop now decides, and one write registers whatever it decided on.
"""

import unittest
from unittest.mock import patch

from Tbackend.a_test_database_behaves_like_the_real_one import (
    connect as connect_test_db, cursor as test_db_cursor)
from backend.internals import db_models
from backend.internals.db_models import FilesDB


class add_files(unittest.TestCase):
    def setUp(self):
        self.connection = connect_test_db()
        self.connection.execute(
            'CREATE TABLE files('
            'id INTEGER PRIMARY KEY, filepath TEXT UNIQUE NOT NULL, '
            'size INTEGER);'
        )
        self.cursor = test_db_cursor(self.connection)
        patcher = patch.object(db_models, 'get_db', return_value=self.cursor)
        patcher.start()
        self.addCleanup(patcher.stop)
        # Every file is one byte; the sizes are not what is under test.
        sizer = patch.object(db_models, 'stat',
                             side_effect=lambda p: type('s', (), {'st_size': 1}))
        sizer.start()
        self.addCleanup(sizer.stop)
        return

    def test_it_registers_every_file_and_returns_their_ids(self):
        paths = [f'/comics/v/{i}.cbz' for i in range(5)]

        ids = FilesDB.add_files(paths)

        self.assertEqual(sorted(ids), sorted(paths))
        self.assertEqual(
            self.cursor.execute('SELECT COUNT(*) FROM files;').exists(), 5)
        return

    def test_a_file_already_known_keeps_the_id_it_had(self):
        first = FilesDB.add_files(['/comics/v/1.cbz'])
        again = FilesDB.add_files(['/comics/v/1.cbz', '/comics/v/2.cbz'])

        self.assertEqual(again['/comics/v/1.cbz'], first['/comics/v/1.cbz'])
        self.assertEqual(
            self.cursor.execute('SELECT COUNT(*) FROM files;').exists(), 2)
        return

    def test_the_whole_batch_is_one_write(self):
        "The point of the change: N files, one acquisition of the writer."
        writes = []
        real = type(self.cursor).executemany

        def count(cursor, sql, *args, **kwargs):
            writes.append(sql)
            return real(cursor, sql, *args, **kwargs)

        with patch.object(type(self.cursor), 'executemany', count):
            FilesDB.add_files([f'/comics/v/{i}.cbz' for i in range(50)])

        self.assertEqual(len(writes), 1)
        return

    def test_a_file_that_went_away_does_not_fail_the_batch(self):
        def sometimes(path):
            if path.endswith('gone.cbz'):
                raise OSError('no such file')
            return type('s', (), {'st_size': 1})

        with patch.object(db_models, 'stat', side_effect=sometimes):
            ids = FilesDB.add_files(['/comics/v/here.cbz', '/comics/v/gone.cbz'])

        self.assertEqual(list(ids), ['/comics/v/here.cbz'])
        return

    def test_nothing_to_register_is_not_a_write(self):
        with patch.object(type(self.cursor), 'executemany') as write:
            self.assertEqual(FilesDB.add_files([]), {})

        write.assert_not_called()
        return
