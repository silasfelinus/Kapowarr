import inspect
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

    def __init__(self, volume_id, title, year, volume_number, folder,
                 issues=range(1, 6), dates=None):
        self.id = volume_id
        self._issues = list(issues)
        # `{issue number: 'YYYY-MM-DD'}`, for the volumes whose catalogue
        # entry actually dates its issues. ComicVine's usually does; the
        # fixtures that predate this one did not care, and still do not.
        self._dates = dates or {}
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
            SimpleNamespace(
                calculated_issue_number=float(n),
                date=self._dates.get(n)
            )
            for n in self._issues
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

    def test_an_ambiguous_file_says_which_volumes_are_competing(self):
        """"More than one" is not something anyone can act on. Silas's
        2026-09-03 log was 4,155 lines of that message covering 997 stuck
        files, and not one of them named the volumes -- so why they were
        stuck could not be worked out from the log at all.
        """
        self.volumes[2] = _FakeVolume(2, 'Batman', 1940, 1, '/library/Batman2')
        collected = {}

        matched = match_file_to_library_volume(
            '/inbound/Batman (1940) Volume 1 Issue 3.cbz', self._index(),
            collected
        )

        self.assertIsNone(matched)
        competing = collected['/inbound/Batman (1940) Volume 1 Issue 3.cbz']
        self.assertIn('Batman (1940)', competing)
        # Findable in the UI, which is where the fixing happens.
        self.assertIn('id 1', competing)
        self.assertIn('id 2', competing)

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
            side_effect=lambda path, ids=None, ambiguous=None: mapping.get(
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


class a_folder_of_stuck_files_is_reported_once(unittest.TestCase):
    """A file that matches two volumes is left alone, correctly -- and then
    found again on the next pass, and the next.

    Silas's 2026-09-03 log: 4,291 lines, of which 4,155 were this one
    message, covering 997 files. 97% of a log saying the same thing about
    the same files, hourly, since they got stuck. And 667 of those files
    carried a `.1`/`.2` suffix from the download client, because the issue
    stayed wanted and the same release was fetched again -- Wonder Woman
    033 twelve times over.
    """

    def test_the_scan_says_it_once_with_a_count(self):
        ambiguous = {
            f'/downloads/Wonder Woman {n:03d}.cbz': 'Wonder Woman (2026) '
            f'[id 1]; Wonder Woman (2026) [id 2]'
            for n in range(1, 21)
        }

        with self.assertLogs(wfi.LOGGER, level='WARNING') as captured:
            wfi._report_ambiguous('Orphans', ambiguous)

        joined = '\n'.join(captured.output)
        self.assertIn('20 file(s) could not be imported', joined)
        # Twenty files, but one duplicate volume behind all of them: one
        # decision, said once, with a file named so it can be found.
        self.assertIn('across 1 set(s) of competing volumes', joined)
        self.assertIn(
            '20 file(s): Wonder Woman (2026) [id 1]; Wonder Woman (2026) '
            '[id 2]',
            joined
        )
        self.assertIn('e.g. /downloads/Wonder Woman 001.cbz', joined)
        self.assertNotIn('...and', joined)
        self.assertEqual(len(captured.output), 3)

    def test_the_biggest_pile_is_named_first(self):
        ambiguous = {'/downloads/small.cbz': 'C; D'}
        ambiguous.update({
            f'/downloads/big {n}.cbz': 'A; B' for n in range(10)
        })

        with self.assertLogs(wfi.LOGGER, level='WARNING') as captured:
            wfi._report_ambiguous('Orphans', ambiguous)

        joined = '\n'.join(captured.output)
        self.assertIn('11 file(s) could not be imported', joined)
        self.assertIn('across 2 set(s)', joined)
        self.assertLess(joined.index('10 file(s): A; B'),
                        joined.index('1 file(s): C; D'))

    def test_too_many_sets_are_counted_rather_than_listed(self):
        ambiguous = {
            f'/downloads/{n}.cbz': f'Series {n} (2000) [id {n}]; '
            f'Series {n} (2001) [id {n + 100}]'
            for n in range(wfi.AMBIGUOUS_SAMPLE + 3)
        }

        with self.assertLogs(wfi.LOGGER, level='WARNING') as captured:
            wfi._report_ambiguous('Orphans', ambiguous)

        joined = '\n'.join(captured.output)
        self.assertIn('...and 3 more set(s) covering 3 file(s)', joined)
        # Summary + two lines per named set + the tail.
        self.assertEqual(
            len(captured.output), wfi.AMBIGUOUS_SAMPLE * 2 + 2
        )

    def test_a_short_list_is_given_in_full(self):
        with self.assertLogs(wfi.LOGGER, level='WARNING') as captured:
            wfi._report_ambiguous('Orphans', {'/downloads/a.cbz': 'A; B'})

        joined = '\n'.join(captured.output)
        self.assertIn('1 file(s) could not be imported', joined)
        self.assertIn('1 file(s): A; B', joined)
        self.assertIn('e.g. /downloads/a.cbz', joined)
        self.assertNotIn('...and', joined)
        self.assertEqual(len(captured.output), 3)

    def test_nothing_stuck_says_nothing(self):
        with patch.object(wfi.LOGGER, 'warning') as warn:
            wfi._report_ambiguous('Orphans', {})

        warn.assert_not_called()

    def test_a_scan_collects_instead_of_logging_per_file(self):
        """The per-file line is still there for a one-off someone drops in
        the watched folder; a folder scan takes the collector instead.
        """
        source = inspect.getsource(wfi.import_loose_files)
        self.assertIn('ambiguous: Dict[str, str] = {}', source)
        self.assertIn('_report_ambiguous(description, ambiguous)', source)


class the_volume_that_has_the_issue_wins(unittest.TestCase):
    """Two runs of a series can both survive the year check for a file whose
    number only one of them ever published. Nightwing (2021) and Nightwing
    (2016) both fit a file dated 2021 -- it is one volume's first year and
    the other's eighty-seventh issue -- but only one of them has an issue
    87.

    So when several volumes match, one that lists the issue beats one that
    does not. Only when it settles the question outright: two volumes both
    listing it is still a choice nobody can make from a filename, and
    guessing puts a comic in the wrong folder, which is what this path
    exists to avoid.
    """

    def _match(self, filename, volumes, ambiguous=None):
        with patch.object(wfi, 'Volume', side_effect=lambda vid: volumes[vid]):
            return wfi.match_file_to_library_volume(
                filename, LibraryIndex(list(volumes)), ambiguous
            )

    def test_the_one_that_published_it_takes_it(self):
        volumes = {
            1: _FakeVolume(1, 'Nightwing', 2016, 1, '/l/1', range(1, 101)),
            2: _FakeVolume(2, 'Nightwing', 2021, 1, '/l/2', range(1, 21))
        }

        self.assertEqual(
            self._match('/inbound/Nightwing 087 (2021).cbz', volumes), 1
        )

    def test_both_publishing_it_is_still_nobody_to_choose(self):
        volumes = {
            1: _FakeVolume(1, 'Nightwing', 2016, 1, '/l/1', range(1, 101)),
            2: _FakeVolume(2, 'Nightwing', 2021, 1, '/l/2', range(1, 101))
        }
        ambiguous = {}

        self.assertIsNone(self._match(
            '/inbound/Nightwing 087 (2021).cbz', volumes, ambiguous
        ))
        self.assertEqual(len(ambiguous), 1)

    def test_neither_publishing_it_is_left_alone_too(self):
        volumes = {
            1: _FakeVolume(1, 'Nightwing', 2016, 1, '/l/1', range(1, 6)),
            2: _FakeVolume(2, 'Nightwing', 2021, 1, '/l/2', range(1, 6))
        }

        self.assertIsNone(
            self._match('/inbound/Nightwing 087 (2021).cbz', volumes)
        )

    def test_one_match_is_unaffected(self):
        volumes = {1: _FakeVolume(1, 'Batman', 1940, 1, '/l/1', range(1, 101))}

        self.assertEqual(
            self._match('/inbound/Batman 087 (1946).cbz', volumes), 1
        )


class the_run_that_dates_the_issue_takes_it(unittest.TestCase):
    """Two runs of a series can both list the issue number a file names, and
    then no amount of reading the filename separates them -- unless the two
    catalogue entries disagree about *when* they published it, which they
    almost always do.

    Silas's 2026-09-04 log, after the year check had already cleared 299 of
    997 stuck files: "Captain America 015 (2026)" was still competing
    between Captain America (2023) and Captain America (2025). Both have a
    #15. Only one of them dates it 2026 -- and the year in these release
    names is the issue's cover year, which is exactly the number to compare
    it against.
    """

    def _match(self, filename, volumes, ambiguous=None):
        with patch.object(wfi, 'Volume', side_effect=lambda vid: volumes[vid]):
            return wfi.match_file_to_library_volume(
                filename, LibraryIndex(list(volumes)), ambiguous
            )

    def test_the_run_that_published_it_that_year_takes_it(self):
        volumes = {
            1: _FakeVolume(
                1, 'Captain America', 2023, 1, '/l/1', range(1, 21),
                dates={15: '2024-06-01'}
            ),
            2: _FakeVolume(
                2, 'Captain America', 2025, 1, '/l/2', range(1, 21),
                dates={15: '2026-07-01'}
            )
        }

        self.assertEqual(
            self._match(
                '/inbound/Captain America 015 (2026) (Digital).cbz', volumes
            ),
            2
        )

    def test_a_cover_date_a_year_out_still_counts(self):
        """A December issue is cover-dated into the next year as often as
        not, so the nearest run wins rather than only an exact one."""
        volumes = {
            1: _FakeVolume(
                1, 'Hellverine', 2024, 1, '/l/1', range(1, 6),
                dates={4: '2025-01-01'}
            ),
            2: _FakeVolume(
                2, 'Hellverine', 2025, 1, '/l/2', range(1, 6),
                dates={4: '2026-03-01'}
            )
        }

        self.assertEqual(
            self._match('/inbound/Hellverine 004 (2024).cbz', volumes), 1
        )

    def test_the_same_year_from_both_settles_nothing(self):
        volumes = {
            1: _FakeVolume(
                1, 'Hellverine', 2024, 1, '/l/1', range(1, 6),
                dates={4: '2025-02-01'}
            ),
            2: _FakeVolume(
                2, 'Hellverine', 2025, 1, '/l/2', range(1, 6),
                dates={4: '2025-11-01'}
            )
        }
        ambiguous = {}

        self.assertIsNone(
            self._match('/inbound/Hellverine 004 (2025).cbz', volumes,
                        ambiguous)
        )
        self.assertEqual(len(ambiguous), 1)

    def test_a_run_that_names_no_date_does_not_outvote_one_that_does(self):
        volumes = {
            1: _FakeVolume(
                1, 'Catwoman', 2011, 1, '/l/1', range(1, 6),
                dates={3: '2011-11-01'}
            ),
            2: _FakeVolume(2, 'Catwoman', 2012, 1, '/l/2', range(1, 6))
        }

        self.assertEqual(
            self._match('/inbound/Catwoman 003 (2011).cbz', volumes), 1
        )

    def test_neither_naming_a_date_stays_ambiguous(self):
        """The Nightwing case: two undated catalogue entries say nothing to
        compare, and a guess would file the comic in the wrong folder."""
        volumes = {
            1: _FakeVolume(1, 'Nightwing', 2016, 1, '/l/1', range(1, 101)),
            2: _FakeVolume(2, 'Nightwing', 2021, 1, '/l/2', range(1, 101))
        }

        self.assertIsNone(
            self._match('/inbound/Nightwing 087 (2021).cbz', volumes)
        )

    def test_a_file_with_no_year_settles_nothing(self):
        """Detective Comics 949 names no year, and the library holds two
        entries covering the same run. That is library data to dedupe, not
        something the matcher can read out of a filename."""
        volumes = {
            1: _FakeVolume(
                1, 'Detective Comics', 1937, 1, '/l/1', range(940, 960),
                dates={949: '2021-11-01'}
            ),
            2: _FakeVolume(
                2, 'Detective Comics', 2017, 1, '/l/2', range(940, 960),
                dates={949: '2021-11-01'}
            )
        }
        ambiguous = {}

        self.assertIsNone(
            self._match('/inbound/Detective.Comics.949.cbz', volumes,
                        ambiguous)
        )
        self.assertEqual(len(ambiguous), 1)

    def test_a_year_nowhere_near_either_run_is_no_evidence(self):
        volumes = {
            1: _FakeVolume(
                1, 'Green Lantern', 2005, 1, '/l/1', range(1, 40),
                dates={24: '2007-12-01'}
            ),
            2: _FakeVolume(
                2, 'Green Lantern', 2021, 1, '/l/2', range(1, 40),
                dates={24: '2023-04-01'}
            )
        }

        self.assertIsNone(
            self._match('/inbound/Green Lantern 024 (2015).cbz', volumes)
        )


class knowing_whether_a_volume_lists_an_issue(unittest.TestCase):
    def test_a_number_it_has(self):
        self.assertTrue(wfi._lists_issue({1.0: 2020, 2.0: 2020}, 2.0))

    def test_a_number_it_does_not(self):
        self.assertFalse(wfi._lists_issue({1.0: 2020}, 87.0))

    def test_a_range_counts_if_either_end_is_there(self):
        "A collected edition names a span; owning part of it is enough."
        self.assertTrue(wfi._lists_issue({5.0: 2020}, (1.0, 5.0)))
        self.assertFalse(wfi._lists_issue({9.0: 2020}, (1.0, 5.0)))

    def test_a_file_naming_no_issue_says_nothing_either_way(self):
        self.assertFalse(wfi._lists_issue({1.0: 2020}, None))
