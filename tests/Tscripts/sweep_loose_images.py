# -*- coding: utf-8 -*-

"""The sweep must never take a comic.

`sweep_loose_images.py` deletes files from a real library, so the tests
that matter are the ones about what it leaves behind. Kapowarr supports
comics stored as loose page images -- `filter_library_import_files` says
so in as many words -- and a folder of images is indistinguishable from a
pile of extractor leavings by filename alone. Only images sitting beside
an archive are redundant by construction.
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

from sweep_loose_images import classify, tracked_filepaths  # noqa: E402


def _touch(*parts):
    path = os.path.join(*parts)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, 'w').close()
    return path


class what_the_sweep_leaves_alone(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.root = os.path.join(self.tmp, 'content')

        # An archive with its extracted pages beside it: the whole point.
        self.redundant = _touch(
            self.root, 'Moon Knight', 'Moon Knight 26 (2009).jpg')
        self.redundant_thumb = _touch(
            self.root, 'Moon Knight', 'Moon Knight 26 (2009)_thumb.jpg')
        _touch(self.root, 'Moon Knight', 'Moon Knight 26 (2009).cbz')

        # Cover decoration Kapowarr already skips.
        self.cover = _touch(self.root, 'Moon Knight', 'cover.jpg')
        self.folder_art = _touch(self.root, 'Moon Knight', 'folder.jpg')

        # A comic that IS loose page images. No archive anywhere near it.
        self.page_comic = [
            _touch(self.root, 'Page Image Comic (2019)', f'page {n}.jpg')
            for n in ('001', '002', '003')
        ]

        # A reader's cache.
        self.cache = _touch(self.root, '.yacreaderlibrary', 'covers', '1.jpg')

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_a_page_image_comic_is_never_a_candidate(self):
        candidates, _ = classify(self.root)
        for page in self.page_comic:
            self.assertNotIn(page, candidates)

    def test_cover_art_is_never_a_candidate(self):
        candidates, _ = classify(self.root)
        self.assertNotIn(self.cover, candidates)
        self.assertNotIn(self.folder_art, candidates)

    def test_a_readers_cache_is_never_a_candidate(self):
        candidates, _ = classify(self.root)
        self.assertNotIn(self.cache, candidates)

    def test_an_archive_is_never_a_candidate(self):
        candidates, _ = classify(self.root)
        for candidate in candidates:
            self.assertFalse(candidate.lower().endswith(
                ('.cbz', '.cbr', '.cb7', '.cbt', '.zip', '.rar')
            ))

    def test_another_readers_thumbnail_is_never_a_candidate(self):
        # YACReader Server's, and it will simply make them again.
        candidates, _ = classify(self.root)
        self.assertNotIn(self.redundant_thumb, candidates)

    def test_pages_beside_an_archive_are(self):
        candidates, _ = classify(self.root)
        self.assertIn(self.redundant, candidates)

    def test_nothing_outside_that_one_is_picked_up(self):
        candidates, _ = classify(self.root)
        self.assertEqual(candidates, [self.redundant])

    def test_an_image_only_folder_stays_whole_even_beneath_one_with_an_archive(
        self
    ):
        # A page-image comic filed as a subfolder of a series that also
        # holds archives. The archive is in the parent, not here.
        nested = [
            _touch(self.root, 'Moon Knight', 'Annual (2010)', f'{n}.jpg')
            for n in ('001', '002')
        ]
        candidates, _ = classify(self.root)
        for page in nested:
            self.assertNotIn(page, candidates)


class what_the_library_already_owns(unittest.TestCase):
    def test_tracked_filepaths_reads_the_files_table(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, 'db.db')
            connection = sqlite3.connect(db)
            connection.execute(
                'CREATE TABLE files (id INTEGER PRIMARY KEY, '
                'filepath TEXT, size INT);'
            )
            connection.execute(
                'INSERT INTO files (filepath, size) VALUES (?, 0);',
                ('/content/kept.jpg',)
            )
            connection.commit()
            connection.close()

            self.assertEqual(tracked_filepaths(db), {'/content/kept.jpg'})

    def test_the_database_is_opened_read_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, 'db.db')
            connection = sqlite3.connect(db)
            connection.execute(
                'CREATE TABLE files (id INTEGER PRIMARY KEY, '
                'filepath TEXT, size INT);'
            )
            connection.commit()
            connection.close()

            tracked_filepaths(db)
            # A sweep has no business writing to the library database.
            read_only = sqlite3.connect(f'file:{db}?mode=ro', uri=True)
            with self.assertRaises(sqlite3.OperationalError):
                read_only.execute(
                    'INSERT INTO files (filepath, size) VALUES (?, 0);',
                    ('/x',)
                )
            read_only.close()


if __name__ == '__main__':
    unittest.main()
