# -*- coding: utf-8 -*-

"""A folder that names a series and holds no comics is asking for one.

Library import has only ever seen a folder as the parent of a file it
found: `_collect_unimported_files` lists content files, and every folder
in a pass is `file_to_folder[filepath]` for one of them. A folder with
nothing in it produces no entry, so it is never grouped, never searched,
and never becomes a volume -- an empty `Blood Train (2025)` was invisible
rather than a request.

It is broader than "no comics": a folder holding only `cover.jpg` and
`series.json` also collapses to nothing, because the image is an artifact
and `.json` is not a content extension. A folder that looks populated in
a file browser could still be invisible to the importer.

Such a folder is now searched by name and held for review. Never
auto-imported: with no files there is no file evidence, and the
confidence policy has nothing to weigh but the title.
"""

import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from backend.features import library_import as LI
from backend.features.library_import import (collect_content_less_folders,
                                              is_content_less_series_folder)


class finding_the_folders_that_are_asking(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.root = os.path.join(self.tmp, 'content')

        def make(*parts, files=()):
            path = os.path.join(self.root, *parts)
            os.makedirs(path, exist_ok=True)
            for name in files:
                open(os.path.join(path, name), 'w').close()
            return path

        self.empty = make('Blood Train (2025)')
        self.cover_only = make('Ignominia', files=('cover.jpg',))
        self.sidecar_only = make('Sidecar Only', files=('series.json',))
        self.organizer = make('Batman')
        self.empty_child = make('Batman', 'Batman (2011)')
        self.has_comics = make('Has Comics (2020)', files=('x 01.cbz',))
        self.no_letters = make('2020')
        self.dot_dir = make('.yacreaderlibrary', 'covers')
        self.page_comic = make('Page Comic', files=('page 001.jpg',))

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _found(self, owned=()):
        roots = MagicMock()
        roots.return_value.get_folder_list.return_value = [self.root]
        with patch.object(LI, 'RootFolders', roots), \
                patch.object(
                    LI, '_volume_owned_folders', return_value=set(owned)
                ):
            return set(collect_content_less_folders())

    def test_an_empty_series_folder_is_found(self):
        self.assertIn(self.empty, self._found())

    def test_so_is_one_holding_only_cover_art(self):
        self.assertIn(self.cover_only, self._found())

    def test_so_is_one_holding_only_a_sidecar(self):
        self.assertIn(self.sidecar_only, self._found())

    def test_an_organizer_is_not_the_series_folder(self):
        # `/content/Batman` names no series of its own; its child does.
        found = self._found()
        self.assertNotIn(self.organizer, found)
        self.assertIn(self.empty_child, found)

    def test_a_folder_with_comics_is_left_to_the_ordinary_path(self):
        self.assertNotIn(self.has_comics, self._found())

    def test_a_page_image_comic_is_not_an_empty_folder(self):
        # Kapowarr supports these; they are content, not an empty shelf.
        self.assertNotIn(self.page_comic, self._found())

    def test_a_name_with_no_letters_is_a_shelf_not_a_series(self):
        self.assertNotIn(self.no_letters, self._found())

    def test_a_dot_directory_is_never_offered(self):
        found = self._found()
        self.assertFalse(any('.yacreaderlibrary' in f for f in found))

    def test_the_root_itself_is_never_offered(self):
        self.assertNotIn(self.root, self._found())

    def test_a_folder_a_volume_already_owns_is_never_offered(self):
        """Kapowarr makes these itself.

        `create_empty_volume_folders` gives every volume added a folder
        whether or not anything has been downloaded into it, so a library
        monitoring volumes it has not filled has an empty folder for each
        one -- every one of them naming a series already in the library.
        Asking which series they mean is a paced provider request and a
        review hold to answer a question nobody had.
        """
        self.assertNotIn(self.empty, self._found(owned=[self.empty]))
        self.assertIn(self.empty, self._found())

    def test_a_folder_beneath_one_a_volume_owns_is_never_offered(self):
        self.assertNotIn(
            self.empty_child, self._found(owned=[self.organizer])
        )

    def test_owning_one_folder_does_not_hide_the_others(self):
        found = self._found(owned=[self.empty])
        self.assertIn(self.cover_only, found)
        self.assertIn(self.sidecar_only, found)


class asking_about_one_folder_on_its_own(unittest.TestCase):
    """The importer re-decides when it reaches the folder.

    A paused pass resumes without the seeding scan's state, so the same
    question has to be answerable from the folder alone.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.root = os.path.join(self.tmp, 'content')
        self.empty = os.path.join(self.root, 'Blood Train (2025)')
        os.makedirs(self.empty)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_it_agrees_with_the_walk(self):
        self.assertTrue(
            is_content_less_series_folder(self.empty, {self.root})
        )

    def test_a_folder_that_gained_a_comic_is_no_longer_one(self):
        open(os.path.join(self.empty, 'Blood Train 01.cbz'), 'w').close()
        self.assertFalse(
            is_content_less_series_folder(self.empty, {self.root})
        )

    def test_a_folder_that_vanished_is_not_one(self):
        import shutil
        shutil.rmtree(self.empty)
        self.assertFalse(
            is_content_less_series_folder(self.empty, {self.root})
        )

    def test_a_root_folder_is_not_one(self):
        self.assertFalse(
            is_content_less_series_folder(self.root, {self.root})
        )


class what_the_importer_does_when_it_gets_there(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.root = os.path.join(self.tmp, 'content')
        self.empty = os.path.join(self.root, 'Blood Train (2025)')
        os.makedirs(self.empty)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _review(self, results, owned=frozenset()):
        from backend.features import library_import_persistent as P

        task = P.PersistentContinuousLibraryImport.__new__(
            P.PersistentContinuousLibraryImport
        )
        task.job_id = 7
        task.search_cache = {}
        task._wait_for_resource_slot = lambda _key: True
        task._emit_persistent_status = lambda *a, **k: None

        roots = MagicMock()
        roots.return_value.get_folder_list.return_value = [self.root]
        with patch.object(LI, 'RootFolders', roots), \
                patch.object(P, '_volume_owned_folders', return_value=owned), \
                patch.object(P, 'asyncio_run', return_value=results), \
                patch.object(P, 'search_volumes_everywhere', lambda q: q):
            return task._review_content_less_folder(self.empty, 3)

    @staticmethod
    def _candidate(title, year, comicvine_id=1):
        return {
            'comicvine_id': comicvine_id, 'title': title, 'year': year,
            'volume_number': 1, 'cover_link': '', 'cover': None,
            'description': '', 'site_url': f'https://cv.test/{comicvine_id}',
            'aliases': [], 'publisher': 'Example', 'issue_count': 4,
            'translated': False, 'already_added': None, 'issues': None
        }

    def test_a_hold_is_produced_for_the_folder_itself(self):
        rows = self._review([self._candidate('Blood Train', 2025)])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['filepath'], self.empty)
        self.assertEqual(rows[0]['file_title'], 'Blood Train (2025)')

    def test_it_is_held_and_never_imported(self):
        rows = self._review([self._candidate('Blood Train', 2025)])
        self.assertEqual(rows[0]['review_reason'], 'empty-folder')

    def test_the_match_it_found_is_offered_as_a_suggestion(self):
        rows = self._review([self._candidate('Blood Train', 2025)])
        self.assertEqual(rows[0]['cv']['title'], 'Blood Train (2025)')
        self.assertEqual(rows[0]['cv']['id'], 1)

    def test_a_folder_nothing_matched_is_still_held_for_a_human(self):
        rows = self._review([self._candidate('Something Else', 1998, 9)])
        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0]['cv']['id'])

    def test_a_folder_that_gained_a_comic_produces_no_hold(self):
        open(os.path.join(self.empty, 'Blood Train 01.cbz'), 'w').close()
        self.assertEqual(self._review([]), [])

    def test_a_folder_a_volume_gained_while_paused_produces_no_hold(self):
        # The pass can be paused for hours; the library moves on.
        self.assertEqual(
            self._review([], owned={self.empty}), []
        )


if __name__ == '__main__':
    unittest.main()
