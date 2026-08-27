# -*- coding: utf-8 -*-

"""YACReader Server's thumbnails are its files, not Kapowarr's content.

YACReader Server writes `<comic>_thumb.jpg` beside the comic itself,
rather than inside the `.yacreaderlibrary/` directory the dot-prefix rule
already covers. They are image files, and `.jpg` is in
`FileConstants.CONTENT_EXTENSIONS`, so Kapowarr counted every one of them
as content it had not imported.

That is enough to keep a folder untracked forever: `_collect_unimported_files`
asks whether anything in the folder lacks a row in `files`, a thumbnail
never gets one, and so Rescan Untracked Library hands the folder back on
every pass no matter how many times its comics import. 164 of them turned
up in a single day's scan log, and each one was also refused by
`scan_files` once per volume that scanned the folder it sat in.

Deleting them would be the wrong fix twice over: they belong to another
application, which will regenerate them, and Kapowarr has no business
reaching into another program's cache. Recognising them is the fix.
"""

import unittest

from backend.base.file_extraction import is_reader_cache_file
from backend.features.library_import_metadata import (
    filter_library_import_files, is_library_import_artifact)


class recognising_a_thumbnail(unittest.TestCase):
    def test_the_shape_yacreader_server_writes(self):
        self.assertTrue(is_reader_cache_file(
            '/content/Moon Knight/Moon Knight 26 (2009)_thumb.jpg'
        ))

    def test_case_and_image_type_do_not_matter(self):
        for path in (
            '/content/x_THUMB.JPG',
            '/content/x_Thumb.png',
            '/content/x_thumb.webp'
        ):
            with self.subTest(path=path):
                self.assertTrue(is_reader_cache_file(path))

    def test_the_comic_beside_it_is_not_one(self):
        self.assertFalse(is_reader_cache_file(
            '/content/Moon Knight/Moon Knight 26 (2009).jpg'
        ))
        self.assertFalse(is_reader_cache_file(
            '/content/Moon Knight/Moon Knight 26 (2009).cbz'
        ))

    def test_a_comic_that_merely_says_thumb_is_safe(self):
        # The reason this matches a stem suffix and not a substring.
        for path in (
            '/content/The Thumbscrew 01 (2019).cbz',
            '/content/Thumbelina 01.jpg',
            '/content/Tom Thumb (1965).cbz'
        ):
            with self.subTest(path=path):
                self.assertFalse(is_reader_cache_file(path))

    def test_only_images_can_be_a_thumbnail_cache(self):
        # A comic archive is never somebody's thumbnail, whatever it is
        # called.
        self.assertFalse(is_reader_cache_file('/content/Batman_thumb.cbz'))


class what_that_means_for_the_untracked_scan(unittest.TestCase):
    THUMB = '/content/Moon Knight/Moon Knight 26 (2009)_thumb.jpg'
    PAGE = '/content/Moon Knight/Moon Knight 26 (2009).jpg'

    def test_a_thumbnail_is_an_import_artifact(self):
        self.assertTrue(is_library_import_artifact(self.THUMB))

    def test_a_loose_page_image_still_is_not(self):
        # Page-image comics are real and must keep reaching the importer.
        self.assertFalse(is_library_import_artifact(self.PAGE))

    def test_cover_art_is_still_an_artifact(self):
        self.assertTrue(
            is_library_import_artifact('/content/Moon Knight/cover.jpg')
        )

    def test_the_filter_drops_the_thumbnail_and_keeps_the_page(self):
        files = {self.THUMB: {}, self.PAGE: {}}
        self.assertEqual(
            sorted(filter_library_import_files(files)),
            [self.PAGE]
        )


if __name__ == '__main__':
    unittest.main()
