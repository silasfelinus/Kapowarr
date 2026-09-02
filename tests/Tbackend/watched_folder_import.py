import os
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from backend.base.custom_exceptions import FolderNotFound, InvalidKeyValue
from backend.features import watched_folder_import as wfi
from backend.features.watched_folder_import import (
    WATCHED_FOLDER_SETTLE_SECONDS, LibraryIndex, describe_summary,
    file_has_settled, find_importable_files, match_file_to_library_volume,
    run_watched_folder_import)
from backend.internals.settings import Settings


class _FakeVolume:
    """Just enough of `backend.implementations.volumes.Volume` for matching:
    a title/year/volume-number to match against and a folder to move into.
    """

    def __init__(self, volume_id, title, year, volume_number, folder):
        self.id = volume_id
        self._data = SimpleNamespace(
            title=title,
            year=year,
            volume_number=volume_number,
            special_version=None,
            folder=folder
        )

    def get_data(self):
        return self._data

    def get_issues(self, _skip_files=False):
        return [
            SimpleNamespace(calculated_issue_number=float(n), date=None)
            for n in range(1, 6)
        ]


class file_settling(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)

    def _make_file(self, name, age_seconds=0):
        path = os.path.join(self.tmpdir.name, name)
        with open(path, 'wb') as f:
            f.write(b'x')
        if age_seconds:
            stamp = time.time() - age_seconds
            os.utime(path, (stamp, stamp))
        return path

    def test_a_file_written_just_now_has_not_settled(self):
        path = self._make_file('in-progress.cbz')

        self.assertFalse(file_has_settled(path))

    def test_a_file_untouched_for_long_enough_has_settled(self):
        path = self._make_file(
            'finished.cbz', age_seconds=WATCHED_FOLDER_SETTLE_SECONDS + 10
        )

        self.assertTrue(file_has_settled(path))

    def test_a_file_that_vanished_counts_as_not_settled(self):
        """A stat failure must never read as 'safe to import' -- the whole
        point of the settle check is to refuse to touch a file we can't
        confirm is finished."""
        missing = os.path.join(self.tmpdir.name, 'gone.cbz')

        self.assertFalse(file_has_settled(missing))

    def test_all_files_in_a_scan_are_judged_against_one_clock_reading(self):
        old = self._make_file(
            'old.cbz', age_seconds=WATCHED_FOLDER_SETTLE_SECONDS + 10
        )
        fixed_now = time.time()

        self.assertTrue(file_has_settled(old, now=fixed_now))
        # Same file, a clock reading from before it was even eligible.
        self.assertFalse(
            file_has_settled(
                old, now=fixed_now - WATCHED_FOLDER_SETTLE_SECONDS - 20
            )
        )


class scanning_the_watched_folder(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)

    def _make_file(self, name, age_seconds=0):
        path = os.path.join(self.tmpdir.name, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'wb') as f:
            f.write(b'x')
        if age_seconds:
            stamp = time.time() - age_seconds
            os.utime(path, (stamp, stamp))
        return path

    def test_only_settled_scannable_files_are_returned(self):
        settled = self._make_file(
            'Batman 001.cbz', age_seconds=WATCHED_FOLDER_SETTLE_SECONDS + 10
        )
        self._make_file('Batman 002.cbz')

        ready, unsettled = find_importable_files(self.tmpdir.name)

        self.assertEqual(ready, [settled])
        self.assertEqual(unsettled, 1)

    def test_non_comic_files_are_ignored_entirely(self):
        """A .nfo or .txt sitting next to the comic is neither imported nor
        counted as 'still downloading' -- it just isn't our business."""
        self._make_file(
            'readme.txt', age_seconds=WATCHED_FOLDER_SETTLE_SECONDS + 10
        )
        self._make_file('notes.nfo')

        ready, unsettled = find_importable_files(self.tmpdir.name)

        self.assertEqual(ready, [])
        self.assertEqual(unsettled, 0)

    def test_nested_folders_are_scanned(self):
        nested = self._make_file(
            os.path.join('Batman', 'Batman 001.cbz'),
            age_seconds=WATCHED_FOLDER_SETTLE_SECONDS + 10
        )

        ready, _ = find_importable_files(self.tmpdir.name)

        self.assertEqual(ready, [nested])


class matching_a_file_to_a_library_volume(unittest.TestCase):
    def setUp(self):
        self.volumes = {
            1: _FakeVolume(1, 'Batman', 1940, 1, '/library/Batman'),
            2: _FakeVolume(2, 'Superman', 1939, 1, '/library/Superman')
        }
        self.volume_patch = patch.object(
            wfi, 'Volume', side_effect=lambda vid: self.volumes[vid]
        )
        self.volume_patch.start()
        self.addCleanup(self.volume_patch.stop)

    def _index(self):
        return LibraryIndex(list(self.volumes))

    def test_a_file_naming_a_library_volume_is_matched(self):
        matched = match_file_to_library_volume(
            '/inbound/Batman (1940) Volume 1 Issue 3.cbz', self._index()
        )

        self.assertEqual(matched, 1)

    def test_a_file_for_an_unknown_series_is_left_alone(self):
        matched = match_file_to_library_volume(
            '/inbound/Some Series Nobody Owns (2001) Issue 1.cbz', self._index()
        )

        self.assertIsNone(matched)

    def test_an_ambiguous_file_is_left_alone_rather_than_guessed(self):
        """Two volumes claiming the same file means a human has to decide.
        Picking one moves the file into the wrong folder, which is the exact
        outcome this feature must never produce."""
        self.volumes[2] = _FakeVolume(2, 'Batman', 1940, 1, '/library/Batman2')

        matched = match_file_to_library_volume(
            '/inbound/Batman (1940) Volume 1 Issue 3.cbz', self._index()
        )

        self.assertIsNone(matched)

    def test_an_unparseable_name_is_left_alone(self):
        with patch.object(
            wfi, 'extract_filename_data', return_value={
                'series': '', 'year': None, 'volume_number': None,
                'special_version': None, 'issue_number': None, 'annual': False
            }
        ):
            self.assertIsNone(
                match_file_to_library_volume('/inbound/????.cbz', self._index())
            )

    def test_each_volume_is_read_from_the_database_only_once_per_pass(self):
        """Matching is O(files x volumes) and both volume reads are database
        round-trips, so a big library plus a busy watched folder would
        otherwise issue tens of thousands of queries for one scan."""
        index = self._index()
        for _ in range(5):
            match_file_to_library_volume(
                '/inbound/Batman (1940) Volume 1 Issue 3.cbz', index
            )

        # Two volumes, each constructed once for get_data(); only Batman's
        # title matched, so only Batman's issues were ever loaded.
        self.assertEqual(
            sorted(c.args[0] for c in wfi.Volume.call_args_list), [1, 2]
        )

    def test_issue_lists_are_only_loaded_for_volumes_whose_title_matched(self):
        index = self._index()
        match_file_to_library_volume(
            '/inbound/Batman (1940) Volume 1 Issue 3.cbz', index
        )

        self.assertEqual(list(index._issues), [1])
        self.assertEqual(sorted(index._data), [1, 2])


class running_a_watched_folder_pass(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)

        self.watched = os.path.join(self.tmpdir.name, 'inbound')
        self.volume_folder = os.path.join(self.tmpdir.name, 'library', 'Batman')
        os.makedirs(self.watched)
        os.makedirs(self.volume_folder)

        self._set_watched_folder(self.watched)

        self.get_volumes_patch = patch.object(
            wfi.Library, 'get_volumes', return_value=[1]
        )
        self.get_volumes_patch.start()
        self.addCleanup(self.get_volumes_patch.stop)

        self.post_process_patch = patch.object(wfi, '_post_process_volume')
        self.mock_post_process = self.post_process_patch.start()
        self.addCleanup(self.post_process_patch.stop)

    def _set_watched_folder(self, folder):
        settings = SimpleNamespace(sv=SimpleNamespace(watched_folder=folder))
        patcher = patch.object(wfi, 'Settings', return_value=settings)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _patch_matching(self, mapping):
        """Route filenames to volume ids without exercising the real matcher,
        which has its own tests above."""
        patcher = patch.object(
            wfi,
            'match_file_to_library_volume',
            side_effect=lambda path, ids=None: mapping.get(
                os.path.basename(path)
            )
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def _patch_import(self, imported_paths=None, skipped_paths=None):
        """Stand in for `manual_import_files`, which has its own tests.

        The stub really moves the files it claims to import, because the
        cleanup step downstream reads the filesystem: a stub that reported an
        import without vacating the source folder would make the empty-folder
        assertions meaningless.
        """
        imported_paths = list(imported_paths or [])
        skipped_paths = list(skipped_paths or [])

        def _fake_import(volume_id, filepaths, issue_id=None,
                         leave_original=False):
            imported = []
            for path in filepaths:
                if path not in imported_paths:
                    continue
                dest = os.path.join(
                    self.volume_folder, os.path.basename(path)
                )
                if leave_original:
                    # The recovery pass links and leaves the source alone,
                    # so the source folder is not vacated.
                    os.link(path, dest)
                else:
                    os.replace(path, dest)
                imported.append({
                    'filepath': path, 'status': 'imported', 'reason': None,
                    'moved_to': dest
                })
            return {
                'imported': imported,
                'skipped': [
                    {
                        'filepath': p, 'status': 'skipped',
                        'reason': 'already exists', 'moved_to': None
                    }
                    for p in filepaths if p in skipped_paths
                ]
            }

        patcher = patch.object(
            wfi, 'manual_import_files', side_effect=_fake_import
        )
        mock = patcher.start()
        self.addCleanup(patcher.stop)
        return mock

    def _make_settled(self, name):
        path = os.path.join(self.watched, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'wb') as f:
            f.write(b'x')
        stamp = time.time() - WATCHED_FOLDER_SETTLE_SECONDS - 10
        os.utime(path, (stamp, stamp))
        return path

    def test_an_unconfigured_watched_folder_is_a_no_op_not_an_error(self):
        self._set_watched_folder('')
        mock_import = self._patch_import()

        summary = run_watched_folder_import()

        self.assertEqual(
            summary,
            {'imported': 0, 'unmatched': 0, 'unsettled': 0, 'skipped': 0,
             'volumes': 0, 'errors': 0}
        )
        mock_import.assert_not_called()

    def test_a_configured_but_missing_folder_is_a_no_op_not_an_error(self):
        self._set_watched_folder(os.path.join(self.tmpdir.name, 'nope'))
        mock_import = self._patch_import()

        summary = run_watched_folder_import()

        self.assertEqual(summary['imported'], 0)
        mock_import.assert_not_called()

    def test_a_matched_file_is_imported_and_post_processed(self):
        src = self._make_settled('Batman 001.cbz')
        self._patch_matching({'Batman 001.cbz': 1})
        mock_import = self._patch_import(imported_paths=[src])

        summary = run_watched_folder_import()

        mock_import.assert_called_once_with(1, [src], leave_original=False)
        self.assertEqual(summary['imported'], 1)
        self.assertEqual(summary['volumes'], 1)
        self.mock_post_process.assert_called_once_with(
            1, [os.path.join(self.volume_folder, 'Batman 001.cbz')]
        )

    def test_an_unmatched_file_is_counted_and_left_on_disk(self):
        src = self._make_settled('Mystery Comic 001.cbz')
        self._patch_matching({})
        mock_import = self._patch_import()

        summary = run_watched_folder_import()

        self.assertEqual(summary['unmatched'], 1)
        self.assertEqual(summary['imported'], 0)
        mock_import.assert_not_called()
        self.assertTrue(os.path.isfile(src))

    def test_a_file_still_being_written_is_counted_but_not_imported(self):
        in_progress = os.path.join(self.watched, 'Batman 002.cbz')
        with open(in_progress, 'wb') as f:
            f.write(b'partial')
        self._patch_matching({'Batman 002.cbz': 1})
        mock_import = self._patch_import()

        summary = run_watched_folder_import()

        self.assertEqual(summary['unsettled'], 1)
        self.assertEqual(summary['imported'], 0)
        mock_import.assert_not_called()
        self.assertTrue(os.path.isfile(in_progress))

    def test_files_for_the_same_volume_are_imported_in_one_call(self):
        first = self._make_settled('Batman 001.cbz')
        second = self._make_settled('Batman 002.cbz')
        self._patch_matching({'Batman 001.cbz': 1, 'Batman 002.cbz': 1})
        mock_import = self._patch_import(imported_paths=[first, second])

        summary = run_watched_folder_import()

        self.assertEqual(mock_import.call_count, 1)
        self.assertEqual(
            sorted(mock_import.call_args[0][1]), sorted([first, second])
        )
        self.assertEqual(summary['volumes'], 1)

    def test_a_skipped_file_does_not_count_as_imported(self):
        src = self._make_settled('Batman 003.cbz')
        self._patch_matching({'Batman 003.cbz': 1})
        self._patch_import(skipped_paths=[src])

        summary = run_watched_folder_import()

        self.assertEqual(summary['skipped'], 1)
        self.assertEqual(summary['imported'], 0)
        self.assertEqual(summary['volumes'], 0)
        # Nothing landed, so no post-processing and no folder cleanup.
        self.mock_post_process.assert_not_called()

    def test_one_failing_volume_does_not_abort_the_rest_of_the_pass(self):
        """A volume deleted between the scan and the import raises. The other
        volumes' files must still land, and the failure must be visible rather
        than silently dropped."""
        first = self._make_settled('Batman 001.cbz')
        second = self._make_settled('Superman 001.cbz')
        self._patch_matching({'Batman 001.cbz': 1, 'Superman 001.cbz': 2})

        def _import(volume_id, filepaths, issue_id=None,
                    leave_original=False):
            if volume_id == 1:
                raise RuntimeError('volume vanished')
            dest = os.path.join(
                self.volume_folder, os.path.basename(filepaths[0])
            )
            os.replace(filepaths[0], dest)
            return {
                'imported': [{
                    'filepath': filepaths[0], 'status': 'imported',
                    'reason': None, 'moved_to': dest
                }],
                'skipped': []
            }

        with patch.object(wfi, 'manual_import_files', side_effect=_import):
            with self.assertLogs(level='ERROR'):
                summary = run_watched_folder_import()

        self.assertEqual(summary['errors'], 1)
        self.assertEqual(summary['imported'], 1)
        self.assertEqual(summary['volumes'], 1)
        # The failed volume's file is untouched, so the next pass retries it.
        self.assertTrue(os.path.isfile(first))
        self.assertFalse(os.path.isfile(second))

    def test_a_failure_is_named_in_the_task_message(self):
        message = describe_summary(
            wfi.WatchedFolderImportSummary(
                imported=0, unmatched=0, unsettled=0, skipped=0, volumes=0,
                errors=2
            )
        )

        self.assertIn('2 failed', message)

    def test_a_stop_request_is_honoured_between_volumes(self):
        self._make_settled('Batman 001.cbz')
        self._patch_matching({'Batman 001.cbz': 1})
        mock_import = self._patch_import()

        summary = run_watched_folder_import(should_stop=lambda: True)

        mock_import.assert_not_called()
        self.assertEqual(summary['imported'], 0)

    def test_emptied_subfolders_are_cleaned_up_after_a_real_import(self):
        src = self._make_settled(os.path.join('Batman', 'Batman 001.cbz'))
        self._patch_matching({'Batman 001.cbz': 1})
        self._patch_import(imported_paths=[src])

        run_watched_folder_import()

        self.assertFalse(os.path.isdir(os.path.join(self.watched, 'Batman')))
        self.assertTrue(os.path.isdir(self.watched))

    def test_a_folder_still_holding_an_unmatched_file_is_not_removed(self):
        matched = self._make_settled(os.path.join('Batman', 'Batman 001.cbz'))
        kept = self._make_settled(os.path.join('Batman', 'Mystery 001.cbz'))
        self._patch_matching({'Batman 001.cbz': 1})
        self._patch_import(imported_paths=[matched])

        run_watched_folder_import()

        self.assertTrue(os.path.isfile(kept))
        self.assertTrue(os.path.isdir(os.path.join(self.watched, 'Batman')))


class summary_description(unittest.TestCase):
    def test_every_counter_appears_in_the_task_message(self):
        message = describe_summary(
            wfi.WatchedFolderImportSummary(
                imported=3, unmatched=2, unsettled=1, skipped=4, volumes=2
            )
        )

        for fragment in ('3 imported', '2 volume(s)', '2 unmatched',
                         '1 still downloading', '4 skipped'):
            self.assertIn(fragment, message)


class watched_folder_setting_validation(unittest.TestCase):
    """`Settings.__format_value` decides whether a watched folder is allowed.

    Exercised directly on a bare instance (no DB) with `sv` and `RootFolders`
    stubbed, the same shape the collision rule sees in production.
    """

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)

        self.root = os.path.join(self.tmpdir.name, 'library')
        self.downloads = os.path.join(self.tmpdir.name, 'downloads')
        self.inbound = os.path.join(self.tmpdir.name, 'inbound')
        for folder in (self.root, self.downloads, self.inbound):
            os.makedirs(folder)

        self.settings = Settings.__new__(Settings)
        self._set_current(watched_folder='', download_folder=self.downloads)

        root_folders = SimpleNamespace(get_folder_list=lambda: [self.root])
        patcher = patch(
            'backend.implementations.root_folders.RootFolders',
            return_value=root_folders
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def _set_current(self, **values):
        current = SimpleNamespace(**values)
        patcher = patch.object(
            type(self.settings), 'sv',
            new_callable=lambda: property(lambda _self: current)
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def _format(self, key, value):
        return self.settings._Settings__format_value(key, value, True)

    def test_a_separate_folder_is_accepted_and_normalised(self):
        result = self._format('watched_folder', self.inbound)

        self.assertTrue(result.startswith(self.inbound))

    def test_empty_disables_the_feature_without_needing_a_real_folder(self):
        self.assertEqual(self._format('watched_folder', ''), '')

    def test_a_missing_folder_is_rejected(self):
        with self.assertRaises(FolderNotFound):
            self._format(
                'watched_folder', os.path.join(self.tmpdir.name, 'nope')
            )

    def test_a_folder_inside_a_root_folder_is_rejected(self):
        """Watching inside the library would have this task and Continuous
        Library Import fighting over the same files."""
        nested = os.path.join(self.root, 'inbound')
        os.makedirs(nested)

        with self.assertRaises(InvalidKeyValue):
            self._format('watched_folder', nested)

    def test_a_folder_containing_a_root_folder_is_rejected(self):
        with self.assertRaises(InvalidKeyValue):
            self._format('watched_folder', self.tmpdir.name)

    def test_the_download_folder_itself_is_rejected(self):
        """Overlapping the download folder would grab direct downloads that
        are still being written."""
        with self.assertRaises(InvalidKeyValue):
            self._format('watched_folder', self.downloads)

    def test_the_download_folder_cannot_be_moved_onto_the_watched_folder(self):
        """The collision is checked from both sides, so the pair can't be made
        to overlap by editing whichever setting was saved second."""
        self._set_current(
            watched_folder=self.inbound, download_folder=self.downloads
        )

        with self.assertRaises(InvalidKeyValue):
            self._format('download_folder', self.inbound)

    def test_an_unset_watched_folder_is_never_passed_as_a_path(self):
        """An empty watched folder must be dropped, not passed through: the
        collision check resolves what it's given, and `abspath('')` is the
        working directory -- which would collide with almost anything."""
        with patch(
            'backend.internals.settings.are_folders_colliding',
            return_value=False
        ) as colliding:
            self._format('download_folder', self.downloads)

        checked_against = colliding.call_args[0][1]
        self.assertNotIn('', checked_against)
        self.assertEqual(checked_against, [self.root])


class task_registration(unittest.TestCase):
    def test_the_interval_task_is_resolvable_from_the_task_library(self):
        """`setup_db()` seeds an interval row for every key in
        `task_intervals`, and the interval loop looks each one up in
        `task_library` by name. A missing entry breaks the whole loop, not
        just this task."""
        from backend.features.tasks import task_library
        from backend.internals.settings import task_intervals

        self.assertIn('watched_folder_import', task_intervals)
        self.assertIn('watched_folder_import', task_library)
        self.assertEqual(
            task_library['watched_folder_import'].action,
            'watched_folder_import'
        )

    def test_every_seeded_interval_has_a_task_class(self):
        from backend.features.tasks import task_library
        from backend.internals.settings import task_intervals

        missing = [k for k in task_intervals if k not in task_library]
        self.assertEqual(missing, [])


if __name__ == '__main__':
    unittest.main()
