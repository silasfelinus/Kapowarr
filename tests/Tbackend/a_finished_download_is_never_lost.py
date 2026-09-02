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
