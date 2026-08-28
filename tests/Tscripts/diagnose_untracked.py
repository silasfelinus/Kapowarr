# -*- coding: utf-8 -*-

"""The diagnostic has to agree with the importer, and change nothing.

`diagnose_untracked.py` exists to tell one kind of stuck folder from
another, and it is only worth trusting if its verdicts come from
Kapowarr's own predicates rather than a private reimplementation. These
pin the two verdicts that matter -- the ones that separate "the library
is behaving correctly" from "a comic will never import until something
changes" -- plus the rule that it never writes.
"""

import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(
    0,
    os.path.join(os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))), 'scripts')
)

from diagnose_untracked import diagnose, read_library  # noqa: E402


class a_library_with_one_of_everything(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.root = os.path.join(self.tmp, 'content')
        self.db = os.path.join(self.tmp, 'db.db')

        def make(folder, *files):
            path = os.path.join(self.root, folder)
            os.makedirs(path, exist_ok=True)
            for name in files:
                open(os.path.join(path, name), 'w').close()
            return path

        # A volume the user has locked as a TPB, holding an issue 2 that
        # it therefore still refuses. Locked rather than inferred on
        # purpose: an inferred single-issue classification no longer
        # refuses its own series' files, so only a locked one still
        # exercises this verdict.
        self.witch = make(
            'Witch Hammer (2018)',
            'Witch Hammer 01 (2018).cbz', 'Witch Hammer 02 (2023).cbz'
        )
        # A folder of loose images beside an archive, a reader's
        # thumbnail, and cover art.
        self.moon = make(
            'Moon Knight (2006)',
            'Moon Knight 26 (2009).cbz', 'Moon Knight 26 (2009).jpg',
            'Moon Knight 26 (2009)_thumb.jpg', 'cover.jpg'
        )
        self.pages = make('Page Comic (2019)', 'page 001.jpg')
        self.unowned = make('Unowned (2024)', 'Unowned 01 (2024).cbz')
        self.empty = make('Blood Train (2025)')

        connection = sqlite3.connect(self.db)
        connection.executescript('''
            CREATE TABLE root_folders (id INTEGER PRIMARY KEY, folder TEXT);
            CREATE TABLE files (id INTEGER PRIMARY KEY, filepath TEXT,
                                size INT);
            CREATE TABLE volumes (id INTEGER PRIMARY KEY, title TEXT,
                year INT, volume_number INT, folder TEXT,
                special_version TEXT, special_version_locked BOOL
                NOT NULL DEFAULT 0);
            CREATE TABLE issues (id INTEGER PRIMARY KEY, volume_id INT,
                calculated_issue_number FLOAT, date TEXT, title TEXT);
        ''')
        connection.execute(
            'INSERT INTO root_folders (folder) VALUES (?);', (self.root,)
        )
        connection.execute(
            "INSERT INTO volumes VALUES "
            "(1,'Witch Hammer',2018,1,?,'tpb',1);",
            (self.witch,)
        )
        connection.execute(
            'INSERT INTO issues VALUES (1,1,1.0,?,NULL);', ('2018-05-01',)
        )
        connection.execute(
            "INSERT INTO volumes VALUES "
            "(2,'Moon Knight',2006,1,?,NULL,0);",
            (self.moon,)
        )
        connection.execute(
            'INSERT INTO issues VALUES (2,2,26.0,?,NULL);', ('2009-02-01',)
        )
        for tracked in (
            os.path.join(self.witch, 'Witch Hammer 01 (2018).cbz'),
            os.path.join(self.moon, 'Moon Knight 26 (2009).cbz')
        ):
            connection.execute(
                'INSERT INTO files (filepath, size) VALUES (?, 0);',
                (tracked,)
            )
        connection.commit()
        connection.close()

        with open(self.db, 'rb') as handle:
            self.before = handle.read()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _rows(self):
        tracked, volumes, _roots = read_library(self.db)
        return {
            (r['file'] or os.path.basename(r['folder'])): r
            for r in diagnose(self.root, tracked, volumes)
        }

    def test_a_volume_refusing_its_own_series_is_named_as_such(self):
        # The one that means a real bug rather than a misfiled folder.
        row = self._rows()['Witch Hammer 02 (2023).cbz']
        self.assertEqual(row['verdict'], 'volume-refused')
        self.assertIn('SAME-SERIES', row['detail'])
        self.assertIn('special-version', row['detail'])

    def test_a_leftover_issue_number_is_not_a_different_series(self):
        """`match_title` alone calls this a different comic.

        The parser leaves an issue number in the series often enough --
        "Hell Her Way 001" against the volume "Hell Her Way" -- that
        strict equality would report a folder as misfiled when the file
        plainly belongs to the volume sitting on it.
        """
        from diagnose_untracked import why_refused
        from backend.base.file_extraction import extract_filename_data
        from types import SimpleNamespace
        from backend.base.definitions import SpecialVersion

        file_data = extract_filename_data(
            '/content/Hell Her Way/Hell Her Way 001 (2023) (One Shot).cbz',
            prefer_folder_year=True
        )
        volume = {
            'data': SimpleNamespace(
                title='Hell Her Way', year=2024, volume_number=1,
                special_version=SpecialVersion.NORMAL, folder='/x'
            ),
            'issues': []
        }
        kind, _gates = why_refused(file_data, volume)
        self.assertEqual(kind, 'SAME-SERIES')

    def test_a_genuinely_different_series_is_still_called_out(self):
        from diagnose_untracked import why_refused
        from backend.base.file_extraction import extract_filename_data
        from types import SimpleNamespace
        from backend.base.definitions import SpecialVersion

        file_data = extract_filename_data(
            '/content/ElfQuest/Rogues Curse 01 (1999).cbz',
            prefer_folder_year=True
        )
        volume = {
            'data': SimpleNamespace(
                title='Detective Comics', year=1937, volume_number=1,
                special_version=SpecialVersion.NORMAL, folder='/x'
            ),
            'issues': []
        }
        kind, _gates = why_refused(file_data, volume)
        self.assertEqual(kind, 'WRONG-VOLUME')

    def test_a_readers_thumbnail_is_not_reported_as_a_problem(self):
        self.assertEqual(
            self._rows()['Moon Knight 26 (2009)_thumb.jpg']['verdict'],
            'reader-cache'
        )

    def test_cover_art_is_not_reported_as_a_problem(self):
        self.assertEqual(self._rows()['cover.jpg']['verdict'], 'cover-art')

    def test_a_page_image_comic_is_not_reported_as_leftovers(self):
        self.assertEqual(
            self._rows()['page 001.jpg']['verdict'], 'page-image-comic'
        )

    def test_a_redundant_page_image_is(self):
        self.assertEqual(
            self._rows()['Moon Knight 26 (2009).jpg']['verdict'],
            'loose-page-image'
        )

    def test_a_folder_no_volume_claims_is_an_import_job(self):
        self.assertEqual(
            self._rows()['Unowned 01 (2024).cbz']['verdict'],
            'no-volume-owns'
        )

    def test_an_empty_folder_is_reported(self):
        self.assertEqual(
            self._rows()['Blood Train (2025)']['verdict'], 'empty-folder'
        )

    def test_a_tracked_file_is_not_reported_at_all(self):
        self.assertNotIn('Witch Hammer 01 (2018).cbz', self._rows())
        self.assertNotIn('Moon Knight 26 (2009).cbz', self._rows())

    def test_it_does_not_write_to_the_database(self):
        self._rows()
        with open(self.db, 'rb') as handle:
            self.assertEqual(handle.read(), self.before)

    def test_the_database_is_opened_read_only(self):
        read_library(self.db)
        connection = sqlite3.connect(f'file:{self.db}?mode=ro', uri=True)
        with self.assertRaises(sqlite3.OperationalError):
            connection.execute('DELETE FROM files;')
        connection.close()

    def test_it_does_not_touch_the_library(self):
        before = sorted(
            os.path.join(d, f)
            for d, _s, fs in os.walk(self.root) for f in fs
        )
        self._rows()
        after = sorted(
            os.path.join(d, f)
            for d, _s, fs in os.walk(self.root) for f in fs
        )
        self.assertEqual(before, after)


if __name__ == '__main__':
    unittest.main()
