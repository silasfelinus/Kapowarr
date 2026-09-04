# -*- coding: utf-8 -*-

"""Twenty-three downloads finished and none of them reached the library.

On 2026-09-01 every download that completed in one run died in
post-processing on a locked write, inside `remove_from_queue` -- which was
the FIRST action in the success chain. So the queue row was gone before
anything had moved the file anywhere, and the download vanished: out of the
queue, never into the library, no record anywhere that it had happened. Two
Kaya issues and twenty-one X-Statix sat in the download folder with nothing
coming back for them, and the next sweep went looking for the same issues
again.

Two things follow from that, and this file covers both: the queue row has to
outlive a failed import, and something has to come back for what is already
on disk.
"""

import logging
import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from backend.features import orphaned_downloads as od
from backend.features import post_processing as pp
from backend.features.post_processing import PostProcessor


class the_queue_row_outlives_a_failed_import(unittest.TestCase):
    def test_nothing_is_dequeued_before_the_file_is_in_the_library(self):
        order = PostProcessor.actions_success
        for landed in (pp.move_to_dest, pp.add_file_to_database):
            self.assertLess(
                order.index(landed), order.index(pp.remove_from_queue),
                msg=f'{landed.__name__} must run before the download leaves '
                    'the queue, or a failure between them loses it silently'
            )
        return

    def test_a_terminal_failure_still_dequeues(self):
        "Nothing to protect when there is no import to lose."
        for actions in (PostProcessor.actions_failed,
                        PostProcessor.actions_perm_failed,
                        PostProcessor.actions_canceled):
            self.assertIn(pp.remove_from_queue, actions)
        return


class recovering_what_is_already_on_disk(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.download_folder = self.tmpdir.name

        settings = MagicMock()
        settings.sv.download_folder = self.download_folder
        patcher = patch.object(od, 'Settings', return_value=settings)
        patcher.start()
        self.addCleanup(patcher.stop)
        return

    def _run(self, queue=(), **import_kwargs):
        handler = MagicMock()
        handler.queue = list(queue)
        with patch('backend.features.download_queue.DownloadHandler',
                   return_value=handler), \
                patch.object(od, 'import_loose_files',
                             **import_kwargs) as importer:
            summary = od.recover_orphaned_downloads()
        return importer, summary

    def test_it_never_moves_because_something_may_still_be_seeding(self):
        """`seeding_handling` defaults to copy, so a torrent client goes on
        seeding out of the download folder after Kapowarr has stopped
        tracking the release. Moving that file would break the seed."""
        importer, _ = self._run(return_value=od.WatchedFolderImportSummary(
            imported=0, unmatched=0, unsettled=0, skipped=0, volumes=0,
            errors=0))

        self.assertTrue(importer.call_args.kwargs['leave_original'])
        self.assertEqual(importer.call_args[0][0], self.download_folder)
        return

    def test_a_download_still_in_the_queue_is_left_alone(self):
        live = MagicMock()
        live.files = ['/downloads/in-flight.cbz']
        live._original_files = ['/downloads/seeding-root']

        importer, _ = self._run(
            queue=[live],
            return_value=od.WatchedFolderImportSummary(
                imported=0, unmatched=0, unsettled=0, skipped=0, volumes=0,
                errors=0))

        left_alone = importer.call_args.kwargs['leave_alone']
        self.assertIn('/downloads/in-flight.cbz', left_alone)
        self.assertIn('/downloads/seeding-root', left_alone)
        return

    def test_it_does_nothing_if_it_cannot_tell_what_is_in_flight(self):
        """Better to skip a pass than to touch a file something else is
        using; there will be another pass in an hour."""
        # The traceback is the expected result here, not something to print
        # over the test run.
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, logging.NOTSET)
        with patch.object(od, 'files_in_use',
                          side_effect=RuntimeError('no queue')), \
                patch.object(od, 'import_loose_files') as importer:
            summary = od.recover_orphaned_downloads()

        importer.assert_not_called()
        self.assertEqual(summary['imported'], 0)
        return

    def test_a_quiet_pass_says_so_plainly(self):
        self.assertEqual(
            od.describe_recovery(od.WatchedFolderImportSummary(
                imported=0, unmatched=0, unsettled=0, skipped=0, volumes=0,
                errors=0)),
            'Nothing left behind in the download folder'
        )
        return

    def test_a_pass_that_found_something_says_what(self):
        message = od.describe_recovery(od.WatchedFolderImportSummary(
            imported=23, unmatched=1, unsettled=0, skipped=0, volumes=2,
            errors=0))

        self.assertIn('Recovered 23', message)
        self.assertIn('2 volume(s)', message)
        return


class the_recovery_runs_without_being_asked(unittest.TestCase):
    def test_it_is_enrolled_on_an_interval(self):
        from backend.internals.settings import task_intervals
        self.assertIn('orphaned_download_recovery', task_intervals)
        return

    def test_the_task_is_findable_by_its_action(self):
        "A missing entry would break the whole interval loop, not just this."
        from backend.features.tasks_core import task_library
        self.assertIn('orphaned_download_recovery', task_library)
        return


class linking_leaves_the_original_where_it_was(unittest.TestCase):
    """The property the whole design rests on: after a recovery import, the
    file is in the volume folder AND still in the download folder."""

    def test_manual_import_can_link_instead_of_move(self):
        from backend.features import manual_import as mi

        with tempfile.TemporaryDirectory() as tmp:
            source = os.path.join(tmp, 'seeding')
            volume_folder = os.path.join(tmp, 'library')
            os.makedirs(source)
            os.makedirs(volume_folder)
            filepath = os.path.join(source, 'X-Statix 006.cbz')
            with open(filepath, 'wb') as f:
                f.write(b'comic')

            volume = MagicMock()
            volume.get_data.return_value = MagicMock(folder=volume_folder)

            with patch.object(mi.Library, 'get_volume', return_value=volume), \
                    patch.object(mi, 'scan_files'):
                result = mi.manual_import_files(
                    1, [filepath], leave_original=True)

            self.assertEqual(len(result['imported']), 1)
            # In the library...
            self.assertTrue(os.path.isfile(
                os.path.join(volume_folder, 'X-Statix 006.cbz')))
            # ...and still where the torrent client left it.
            self.assertTrue(os.path.isfile(filepath))
        return


class kapowarr_remembers_what_it_fetched(unittest.TestCase):
    """A release Kapowarr grabbed was grabbed on behalf of one volume, and
    that was written down at the moment it was grabbed. The importer then
    threw it away and re-derived the volume from the filename -- which is
    why "Flash Gordon 017 (2026)" sat in the download folder while Flash
    Gordon (2024) and Flash Gordon (2023) both claimed it, the issue stayed
    wanted, and SABnzbd fetched the same release eight times over
    (`...-Empire]`, then `.1` through `.7`).

    The record is not a guess between two candidates. It is the answer.
    """

    def test_the_job_folder_names_the_volume(self):
        fetched_for = {'Flash.Gordon.017.[2026].[digital].[Empire]': 57}

        self.assertEqual(
            od.volume_it_was_fetched_for(
                '/downloads/Flash.Gordon.017.[2026].[digital].[Empire]/'
                'Flash Gordon 017 (2026).cbr',
                fetched_for
            ),
            57
        )

    def test_a_refetch_suffix_comes_off(self):
        """SABnzbd appends `.1`, `.2` to a job folder whose name is taken,
        which is what a re-download looks like on disk."""
        fetched_for = {'Flash.Gordon.017.[2026].[digital].[Empire]': 57}

        for attempt in range(1, 8):
            self.assertEqual(
                od.volume_it_was_fetched_for(
                    f'/downloads/Flash.Gordon.017.[2026].[digital].'
                    f'[Empire].{attempt}/Flash Gordon 017 (2026).cbr',
                    fetched_for
                ),
                57,
                msg=f'attempt .{attempt} is the same job'
            )

    def test_a_job_name_that_really_ends_in_a_number_still_matches(self):
        fetched_for = {'Morning Glories Vol.2': 3682}

        self.assertEqual(
            od.volume_it_was_fetched_for(
                '/downloads/Morning Glories Vol.2/issue.cbr', fetched_for
            ),
            3682
        )

    def test_a_file_written_straight_into_the_folder_uses_its_own_name(self):
        fetched_for = {'Adam Strange 001 (2004)': 208}

        self.assertEqual(
            od.volume_it_was_fetched_for(
                '/downloads/Adam Strange 001 (2004).cbr', fetched_for
            ),
            208
        )

    def test_a_file_nobody_recorded_is_left_to_the_filename(self):
        self.assertIsNone(
            od.volume_it_was_fetched_for(
                '/downloads/Something Someone Dropped In/issue.cbr', {}
            )
        )

    def test_two_volumes_against_one_job_name_settle_nothing(self):
        """The record is only worth more than the filename where it is
        unambiguous. Where it is not, it is not a tie-break either."""
        rows = [('Detective Comics 949', 774), ('Detective Comics 949', 1671)]

        with patch.object(od, 'LOGGER'):
            with patch(
                'backend.internals.db.get_db',
                return_value=MagicMock(execute=MagicMock(return_value=rows))
            ):
                fetched_for = od.downloads_by_job_name()

        self.assertIsNone(fetched_for['Detective Comics 949'])
        self.assertIsNone(
            od.volume_it_was_fetched_for(
                '/downloads/Detective Comics 949/issue.cbz', fetched_for
            )
        )

    def test_one_volume_against_one_job_name_is_the_answer(self):
        rows = [
            ('Flash.Gordon.017', 57),
            ('Flash.Gordon.017', 57),
            ('Adam Strange 001', 208)
        ]

        with patch(
            'backend.internals.db.get_db',
            return_value=MagicMock(execute=MagicMock(return_value=rows))
        ):
            fetched_for = od.downloads_by_job_name()

        self.assertEqual(fetched_for['Flash.Gordon.017'], 57)
        self.assertEqual(fetched_for['Adam Strange 001'], 208)

    def test_the_recovery_pass_hands_the_record_to_the_importer(self):
        """Orphan recovery supplies it and the watched folder does not: a
        folder someone drops files into has no such record to consult."""
        import inspect

        from backend.features import watched_folder_import as wfi

        self.assertIn(
            'resolve=lambda filepath: volume_it_was_fetched_for',
            inspect.getsource(od.recover_orphaned_downloads)
        )
        self.assertNotIn(
            'resolve=',
            inspect.getsource(wfi.run_watched_folder_import)
        )


class the_narrowing_pass_does_not_repeat_the_report(unittest.TestCase):
    """`still_missing` matches every file, then `import_loose_files` matches
    them all again and reports what it could not place, grouped by the
    volumes competing for it. The narrowing pass logging its own line per
    stuck file put 82 lines back into the 2026-09-04 log that the grouped
    report exists to replace.
    """

    def test_it_matches_quietly(self):
        import inspect

        source = inspect.getsource(od.still_missing)
        self.assertIn('quiet=True', source)
