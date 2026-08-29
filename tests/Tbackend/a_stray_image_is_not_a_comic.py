# -*- coding: utf-8 -*-

"""One loose image must not promote its folder to a volume.

`_collect_unimported_files` represents an unpacked page-image comic by its
directory, because that is what the comic is. It decided a directory was
one on the strength of the first loose image it saw in it, whatever that
image was -- a series banner, a poster, a page left behind beside its
archive, another reader's `_thumb.jpg`. The folder was then searched as
though it were a single comic.

Job 18 of Silas's library shows what that costs. `/content/Creepy`,
`/content/Doctor Who`, `/content/Invincible` and `/content/Future State`
are container folders: their real volumes live in subfolders and were
imported long ago. Each held one stray image, so each came back as a work
item, was searched by its bare series name, and tied across every
same-titled volume the provider has -- six of the nine ties in that pass,
unchanged across three consecutive passes because nothing about them can
ever resolve.

A comic has pages. A folder holding one image, or holding images beside
the archive they came out of, is a folder with something in it -- not an
unpacked comic. Neither the folder nor the stray image is worth offering:
a search for one jpg's filename has nothing to match either.
"""

import unittest
from unittest.mock import patch

from backend.features.library_import import (MIN_UNPACKED_COMIC_IMAGES,
                                             _collect_unimported_files)


def _parsed(series, special_version=None):
    return {
        'series': series, 'year': 2020, 'volume_number': 1,
        'special_version': special_version, 'issue_number': None,
        'annual': False
    }


class collecting(unittest.TestCase):
    def _collect(self, listing, series_of=None, artifacts=()):
        def parse(path, prefer_folder_year=False):
            name = series_of(path) if series_of else 'Whatever'
            return _parsed(name)

        with patch(
            'backend.features.library_import.RootFolders.get_folder_list',
            return_value=['/content']
        ), patch(
            'backend.features.library_import.list_files',
            return_value=list(listing)
        ), patch(
            'backend.features.library_import.FilesDB.fetch',
            return_value=[]
        ), patch(
            'backend.features.library_import.extract_filename_data',
            side_effect=parse
        ), patch(
            'backend.features.library_import.is_library_import_artifact',
            side_effect=lambda p: p in set(artifacts)
        ):
            return _collect_unimported_files()


class a_container_folder_is_not_a_comic(collecting):
    """The shape that produced six of job 18's nine ties."""

    def test_one_banner_does_not_make_a_work_item(self):
        files, file_to_folder = self._collect(['/content/Creepy/folder.jpg'])

        self.assertEqual(files, {})
        self.assertEqual(file_to_folder, {})

    def test_nor_does_the_image_stand_in_for_itself(self):
        # Offering the jpg on its own would only move the dead end: there is
        # nothing for `folder.jpg` to match either.
        files, _ = self._collect(['/content/Creepy/folder.jpg'])

        self.assertNotIn('/content/Creepy/folder.jpg', files)

    def test_the_real_volumes_underneath_are_untouched(self):
        files, file_to_folder = self._collect([
            '/content/Creepy/folder.jpg',
            '/content/Creepy/Creepy (1964)/Creepy 001.cbz'
        ])

        self.assertEqual(list(files), ['/content/Creepy/Creepy (1964)/Creepy 001.cbz'])
        self.assertEqual(
            file_to_folder['/content/Creepy/Creepy (1964)/Creepy 001.cbz'],
            '/content/Creepy/Creepy (1964)'
        )


class pages_beside_their_archive(collecting):
    def test_an_archive_in_the_folder_settles_it(self):
        # However many images are lying around: a folder that contains the
        # .cbz is a folder with leftovers, not an unpacked comic.
        listing = ['/content/Hulk/Hulk 001.cbz'] + [
            '/content/Hulk/%03d.jpg' % n
            for n in range(1, MIN_UNPACKED_COMIC_IMAGES + 5)
        ]

        files, _ = self._collect(listing)

        self.assertEqual(list(files), ['/content/Hulk/Hulk 001.cbz'])
        self.assertNotIn('/content/Hulk', files)


class a_real_unpacked_comic_still_imports(collecting):
    def test_a_folder_of_pages_is_still_its_directory(self):
        listing = [
            '/content/Animosity/%03d.jpg' % n
            for n in range(1, MIN_UNPACKED_COMIC_IMAGES + 1)
        ]

        files, file_to_folder = self._collect(listing)

        self.assertEqual(list(files), ['/content/Animosity'])
        self.assertEqual(file_to_folder['/content/Animosity'], '/content/Animosity')

    def test_the_threshold_is_the_line(self):
        below = [
            '/content/Animosity/%03d.jpg' % n
            for n in range(1, MIN_UNPACKED_COMIC_IMAGES)
        ]

        files, _ = self._collect(below)

        self.assertEqual(files, {})


class what_does_not_count_as_a_page(collecting):
    THUMB = '/content/Creepy/Creepy 001_thumb.jpg'

    def test_another_readers_cache_never_counts(self):
        # `is_library_import_artifact` already knows a thumbnail is not
        # content, but it was consulted only after the folder had been
        # promoted -- and a promoted folder is a directory path, which that
        # check passes every time. Consulting it first is the fix.
        pages = [self.THUMB] + [
            '/content/Creepy/%03d.jpg' % n
            for n in range(1, MIN_UNPACKED_COMIC_IMAGES)
        ]

        files, _ = self._collect(pages, artifacts=[self.THUMB])

        self.assertEqual(files, {})

    def test_but_real_pages_beside_a_cache_file_still_do(self):
        pages = [self.THUMB] + [
            '/content/Creepy/%03d.jpg' % n
            for n in range(1, MIN_UNPACKED_COMIC_IMAGES + 1)
        ]

        files, _ = self._collect(pages, artifacts=[self.THUMB])

        self.assertEqual(list(files), ['/content/Creepy'])


class the_record_says_it_is_a_folder(unittest.TestCase):
    """Telling a phantom from a real unpacked comic took reading
    `files[0].filepath == folder` out of the JSON by eye. The record now
    says so, and says how many pages were behind the decision."""

    def test_a_directory_work_item_is_labelled(self):
        import os
        import tempfile

        from backend.features.library_import_diagnostics import (
            _unpacked_comic_snapshot)

        with tempfile.TemporaryDirectory() as folder:
            for name in ('001.jpg', '002.jpg', 'Creepy 001.cbz'):
                open(os.path.join(folder, name), 'w').close()
            os.mkdir(os.path.join(folder, 'Creepy (1964)'))

            snapshot = _unpacked_comic_snapshot(folder)

        self.assertEqual(snapshot, {
            'is_directory': True, 'page_images': 2, 'archives': 1
        })

    def test_a_real_file_carries_nothing_extra(self):
        from backend.features.library_import_diagnostics import (
            _unpacked_comic_snapshot)

        self.assertIsNone(
            _unpacked_comic_snapshot('/content/Creepy/Creepy 001.cbz')
        )


if __name__ == '__main__':
    unittest.main()
