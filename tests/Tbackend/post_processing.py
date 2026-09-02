import unittest
from os import makedirs
from os.path import basename, exists, join
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, call, patch

from backend.base.definitions import BlocklistReason, NotificationEvent
from backend.features import post_processing as pp
from backend.features.post_processing import (PostProcessor,
                                              PostProcessorTorrentsComplete,
                                              PostProcessorTorrentsCopy)


def _make_download(**overrides) -> MagicMock:
    "A stand-in Download with the attributes post_processing.py actually reads"
    dl = MagicMock()
    dl.id = overrides.get('id', 1)
    dl.volume_id = overrides.get('volume_id', 1)
    dl.issue_id = overrides.get('issue_id', None)
    dl.title = overrides.get('title', 'Batman 001')
    dl.web_link = overrides.get('web_link', 'https://example/1')
    dl.web_title = overrides.get('web_title', 'Batman 001')
    dl.web_sub_title = overrides.get('web_sub_title', None)
    dl.download_link = overrides.get('download_link', 'https://example/1/dl')
    dl.filename_body = overrides.get('filename_body', 'Batman 001')
    dl.files = overrides.get('files', [])
    if 'state' in overrides:
        dl.state = overrides['state']
    if 'source_type' in overrides:
        dl.source_type = overrides['source_type']
    return dl


# region Database
class remove_from_queue_fn(unittest.TestCase):
    def test_deletes_the_matching_row(self):
        dl = _make_download(id=42)
        cursor = MagicMock()
        with patch.object(pp, 'get_db', return_value=cursor):
            pp.remove_from_queue(dl)
        cursor.execute.assert_called_once_with(
            "DELETE FROM download_queue WHERE id = ?", (42,)
        )


class add_to_history_fn(unittest.TestCase):
    def test_records_success_flag_from_state(self):
        from backend.base.definitions import DownloadState
        for state, expected_success in (
            (DownloadState.IMPORTING_STATE, True),
            (DownloadState.DOWNLOADING_STATE, True),
            (DownloadState.FAILED_STATE, False),
        ):
            with self.subTest(state=state):
                dl = _make_download(state=state)
                dl.source_type.value = 'gc'
                cursor = MagicMock()
                with patch.object(pp, 'get_db', return_value=cursor), \
                     patch.object(pp, 'time', return_value=1000):
                    pp.add_to_history(dl)
                params = cursor.execute.call_args.args[1]
                self.assertEqual(params['success'], expected_success)
                self.assertEqual(params['downloaded_at'], 1000)
                self.assertEqual(params['file_title'], dl.title)
                self.assertEqual(params['source'], 'gc')


class add_file_to_database_fn(unittest.TestCase):
    def test_scans_the_downloaded_files(self):
        dl = _make_download(volume_id=3, files=['/a', '/b'])
        with patch.object(pp, 'scan_files') as mock_scan:
            pp.add_file_to_database(dl)
        mock_scan.assert_called_once_with(
            3, filepath_filter=['/a', '/b'], update_websocket=True
        )


# region Blocklist
class add_dl_to_blocklist_fn(unittest.TestCase):
    def test_forwards_link_broken_reason(self):
        dl = _make_download(
            web_link='wl', web_title='wt', web_sub_title='ws',
            download_link='dl', volume_id=2, issue_id=5
        )
        with patch.object(pp, 'add_to_blocklist') as mock_add:
            pp.add_dl_to_blocklist(dl)
        mock_add.assert_called_once_with(
            'wl', 'wt', 'ws', 'dl', dl.source_type, 2, 5,
            BlocklistReason.LINK_BROKEN
        )

    def test_a_corrupt_download_blocklists_under_its_own_reason(self):
        # The link worked; what it served did not. Recorded separately
        # from LINK_BROKEN so the blocklist page distinguishes a dead
        # indexer link from a bad rip.
        dl = _make_download(
            web_link='wl', web_title='wt', web_sub_title='ws',
            download_link='dl', volume_id=2, issue_id=5
        )
        with patch.object(pp, 'add_to_blocklist') as mock_add:
            pp.add_corrupt_dl_to_blocklist(dl)
        mock_add.assert_called_once_with(
            'wl', 'wt', 'ws', 'dl', dl.source_type, 2, 5,
            BlocklistReason.DOWNLOAD_CORRUPT
        )


# region Integrity gate
class failed_integrity_check_fn(unittest.TestCase):
    @staticmethod
    def _settings(enabled: bool):
        return patch.object(
            pp, 'Settings',
            return_value=MagicMock(
                sv=MagicMock(verify_downloaded_archives=enabled)
            )
        )

    def test_returns_none_when_every_file_passes(self):
        dl = _make_download(files=['/a.cbz', '/b.cbz'])
        ok = MagicMock(ok=True)
        with self._settings(True), \
             patch.object(pp, 'verify_archive', return_value=ok) as mock_verify:
            self.assertIsNone(pp.failed_integrity_check(dl))
        self.assertEqual(mock_verify.call_count, 2)

    def test_returns_the_first_failing_result(self):
        dl = _make_download(files=['/a.cbz', '/b.cbz', '/c.cbz'])
        bad = MagicMock(ok=False)
        with self._settings(True), \
             patch.object(pp, 'verify_archive', side_effect=[
                 MagicMock(ok=True), bad, MagicMock(ok=True)
             ]) as mock_verify:
            self.assertIs(pp.failed_integrity_check(dl), bad)
        # Stops at the first failure rather than checking the rest.
        self.assertEqual(mock_verify.call_count, 2)

    def test_the_setting_switches_the_check_off_entirely(self):
        dl = _make_download(files=['/a.cbz'])
        with self._settings(False), \
             patch.object(pp, 'verify_archive') as mock_verify:
            self.assertIsNone(pp.failed_integrity_check(dl))
        mock_verify.assert_not_called()

    def test_a_download_with_no_files_passes(self):
        dl = _make_download(files=[])
        with self._settings(True), \
             patch.object(pp, 'verify_archive') as mock_verify:
            self.assertIsNone(pp.failed_integrity_check(dl))
        mock_verify.assert_not_called()


class integrity_failure_pipeline(unittest.TestCase):
    def test_a_failing_download_is_blocklisted_and_never_registered(self):
        """The whole point of the gate: `add_file_to_database` must not
        run, or the corrupt file becomes the issue's file, the issue
        stops being `wanted`, and nothing ever re-searches it."""
        dl = _make_download()
        failure = MagicMock(detail='CRC check failed', **{'status.value': 'corrupt'})
        recorder = MagicMock()
        with patch.object(pp, 'failed_integrity_check', return_value=failure), \
             patch.object(PostProcessor, 'actions_success', [recorder.success]), \
             patch.object(PostProcessor, 'actions_integrity_failed',
                          [recorder.reject]), \
             patch.object(pp, 'send_notification') as mock_notify:
            PostProcessor.success(dl)

        recorder.reject.assert_called_once_with(dl)
        recorder.success.assert_not_called()
        self.assertEqual(
            mock_notify.call_args.args[0], NotificationEvent.IMPORT_FAILED
        )

    def test_the_download_is_marked_failed_before_history_is_written(self):
        # `add_to_history` derives its `success` column from the state,
        # so a rejection filed while the state still says IMPORTING
        # would be recorded as a successful import.
        from backend.base.definitions import DownloadState

        dl = _make_download()
        seen_state = {}

        def _record_state(download):
            seen_state['value'] = download.state

        with patch.object(pp, 'failed_integrity_check',
                          return_value=MagicMock(detail='x')), \
             patch.object(PostProcessor, 'actions_integrity_failed',
                          [_record_state]), \
             patch.object(pp, 'send_notification'):
            PostProcessor.success(dl)

        self.assertEqual(seen_state['value'], DownloadState.FAILED_STATE)

    def test_the_rejection_pipeline_blocklists_and_deletes(self):
        self.assertEqual(PostProcessor.actions_integrity_failed, [
            pp.remove_from_queue, pp.add_to_history,
            pp.add_corrupt_dl_to_blocklist, pp.delete_file
        ])
        # It must not contain the step that would register the file.
        self.assertNotIn(
            pp.add_file_to_database, PostProcessor.actions_integrity_failed
        )

    def test_torrents_complete_is_gated_like_a_direct_download(self):
        # Its success() still runs before move_torrent_to_dest imports
        # anything, so the gate is in the right place.
        self.assertTrue(PostProcessorTorrentsComplete.verify_integrity_on_success)

    def test_torrents_copy_is_not_gated_on_success(self):
        """copy_file_torrent has already imported the file by the time
        success() runs, so gating there would blocklist and delete the
        seeding copy while the imported one stays in the library."""
        self.assertFalse(PostProcessorTorrentsCopy.verify_integrity_on_success)

        dl = _make_download(files=['/a.cbz'])
        with patch.object(pp, 'failed_integrity_check') as mock_check, \
             patch.object(PostProcessorTorrentsCopy, 'actions_success', []), \
             patch.object(pp, 'send_notification'):
            PostProcessorTorrentsCopy.success(dl)

        mock_check.assert_not_called()


# region Moving
class move_to_dest_fn(unittest.TestCase):
    def test_missing_source_file_is_a_noop(self):
        dl = _make_download(files=['/does/not/exist/x.cbz'])
        with patch.object(pp, 'Volume') as MockVolume:
            pp.move_to_dest(dl)
        MockVolume.assert_not_called()
        self.assertEqual(dl.files, ['/does/not/exist/x.cbz'])

    def test_moves_file_into_the_volume_folder(self):
        with TemporaryDirectory() as tmp:
            src = join(tmp, 'src.cbz')
            open(src, 'w').close()
            dest_folder = join(tmp, 'dest')
            makedirs(dest_folder)
            dl = _make_download(files=[src], filename_body='Batman 001')
            with patch.object(pp, 'Volume') as MockVolume, \
                 patch.object(pp, 'commit'):
                MockVolume.return_value.vd.folder = dest_folder
                pp.move_to_dest(dl)
            expected = join(dest_folder, 'Batman 001.cbz')
            self.assertTrue(exists(expected))
            self.assertFalse(exists(src))
            self.assertEqual(dl.files, [expected])

    def test_existing_destination_file_is_replaced_not_merged(self):
        with TemporaryDirectory() as tmp:
            src = join(tmp, 'src.cbz')
            with open(src, 'w') as f:
                f.write('new')
            dest_folder = join(tmp, 'dest')
            makedirs(dest_folder)
            dest_path = join(dest_folder, 'Batman 001.cbz')
            with open(dest_path, 'w') as f:
                f.write('old')
            dl = _make_download(files=[src], filename_body='Batman 001')
            with patch.object(pp, 'Volume') as MockVolume, \
                 patch.object(pp, 'commit'):
                MockVolume.return_value.vd.folder = dest_folder
                pp.move_to_dest(dl)
            with open(dest_path) as f:
                self.assertEqual(f.read(), 'new')

    def test_non_scannable_extension_is_dropped_from_the_filename(self):
        with TemporaryDirectory() as tmp:
            # .torrent is a real, permanent extension -- never part of
            # FileConstants.SCANNABLE_EXTENSIONS -- so this exercises the
            # branch without mocking the constants table.
            src = join(tmp, 'src.torrent')
            open(src, 'w').close()
            dest_folder = join(tmp, 'dest')
            makedirs(dest_folder)
            dl = _make_download(files=[src], filename_body='Batman 001')
            with patch.object(pp, 'Volume') as MockVolume, \
                 patch.object(pp, 'commit'):
                MockVolume.return_value.vd.folder = dest_folder
                pp.move_to_dest(dl)
            expected = join(dest_folder, 'Batman 001')
            self.assertTrue(exists(expected))


class move_torrent_to_dest_fn(unittest.TestCase):
    def test_missing_source_is_a_noop(self):
        dl = _make_download(files=['/missing'])
        with patch.object(pp, 'move_to_dest') as mock_move:
            pp.move_torrent_to_dest(dl)
        mock_move.assert_not_called()

    def test_extracts_scans_and_optionally_renames(self):
        with TemporaryDirectory() as tmp:
            folder = join(tmp, 'downloaded')
            makedirs(folder)
            dl = _make_download(files=[folder], volume_id=9)
            with patch.object(pp, 'move_to_dest') as mock_move, \
                 patch.object(pp, 'extract_files_from_folder',
                              return_value=['/x.cbz']) as mock_extract, \
                 patch.object(pp, 'scan_files') as mock_scan, \
                 patch.object(pp, 'Settings') as MockSettings, \
                 patch.object(pp, 'mass_rename',
                              return_value=['/x-renamed.cbz']) as mock_rename:
                MockSettings.return_value.sv.rename_downloaded_files = True
                pp.move_torrent_to_dest(dl)
            mock_move.assert_called_once_with(dl)
            mock_extract.assert_called_once_with(folder, 9)
            mock_scan.assert_called_once_with(
                9, filepath_filter=['/x.cbz'], update_websocket=True
            )
            mock_rename.assert_called_once_with(
                9, filepath_filter=['/x.cbz'], process_individual_files=False
            )
            self.assertEqual(dl.files, ['/x-renamed.cbz'])

    def test_no_extracted_files_skips_scan_and_rename(self):
        with TemporaryDirectory() as tmp:
            folder = join(tmp, 'downloaded')
            makedirs(folder)
            dl = _make_download(files=[folder])
            with patch.object(pp, 'move_to_dest'), \
                 patch.object(pp, 'extract_files_from_folder', return_value=[]), \
                 patch.object(pp, 'scan_files') as mock_scan, \
                 patch.object(pp, 'mass_rename') as mock_rename:
                pp.move_torrent_to_dest(dl)
            mock_scan.assert_not_called()
            mock_rename.assert_not_called()

    def test_rename_disabled_leaves_extracted_files_as_is(self):
        with TemporaryDirectory() as tmp:
            folder = join(tmp, 'downloaded')
            makedirs(folder)
            dl = _make_download(files=[folder])
            with patch.object(pp, 'move_to_dest'), \
                 patch.object(pp, 'extract_files_from_folder',
                              return_value=['/x.cbz']), \
                 patch.object(pp, 'scan_files'), \
                 patch.object(pp, 'Settings') as MockSettings, \
                 patch.object(pp, 'mass_rename') as mock_rename:
                MockSettings.return_value.sv.rename_downloaded_files = False
                pp.move_torrent_to_dest(dl)
            mock_rename.assert_not_called()
            self.assertEqual(dl.files, ['/x.cbz'])


class copy_file_torrent_fn(unittest.TestCase):
    def test_missing_source_still_records_original_files(self):
        dl = _make_download(files=['/missing'])
        pp.copy_file_torrent(dl)
        self.assertEqual(dl._original_files, ['/missing'])

    def test_copies_extracts_scans_and_optionally_renames(self):
        with TemporaryDirectory() as tmp:
            src = join(tmp, 'downloaded')
            makedirs(src)
            dest_folder = join(tmp, 'dest')
            makedirs(dest_folder)
            dl = _make_download(files=[src], volume_id=4)
            with patch.object(pp, 'Volume') as MockVolume, \
                 patch.object(pp, 'commit'), \
                 patch.object(pp, 'copy_directory') as mock_copy, \
                 patch.object(pp, 'extract_files_from_folder',
                              return_value=['/y.cbz']) as mock_extract, \
                 patch.object(pp, 'scan_files') as mock_scan, \
                 patch.object(pp, 'Settings') as MockSettings:
                MockVolume.return_value.vd.folder = dest_folder
                MockSettings.return_value.sv.rename_downloaded_files = False
                pp.copy_file_torrent(dl)
            self.assertEqual(dl._original_files, [src])
            expected_dest = join(dest_folder, basename(src))
            mock_copy.assert_called_once_with(src, expected_dest)
            mock_extract.assert_called_once_with(expected_dest, 4)
            mock_scan.assert_called_once_with(
                4, filepath_filter=['/y.cbz'], update_websocket=True
            )
            self.assertEqual(dl.files, ['/y.cbz'])

    def test_existing_destination_is_replaced(self):
        with TemporaryDirectory() as tmp:
            src = join(tmp, 'downloaded')
            makedirs(src)
            dest_folder = join(tmp, 'dest')
            existing = join(dest_folder, 'downloaded')
            makedirs(existing)
            with open(join(existing, 'stale.txt'), 'w') as f:
                f.write('stale')
            dl = _make_download(files=[src])
            with patch.object(pp, 'Volume') as MockVolume, \
                 patch.object(pp, 'commit'), \
                 patch.object(pp, 'extract_files_from_folder', return_value=[]), \
                 patch.object(pp, 'scan_files'):
                MockVolume.return_value.vd.folder = dest_folder
                pp.copy_file_torrent(dl)
            self.assertFalse(exists(join(existing, 'stale.txt')))


# region Extras
class delete_file_fn(unittest.TestCase):
    def test_deletes_every_file(self):
        dl = _make_download(files=['/a', '/b'])
        with patch.object(pp, 'delete_file_folder') as mock_delete:
            pp.delete_file(dl)
        mock_delete.assert_has_calls([call('/a'), call('/b')])


class rename_with_proper_extension_fn(unittest.TestCase):
    def test_renames_files_whose_detected_extension_changed(self):
        with TemporaryDirectory() as tmp:
            f1 = join(tmp, 'a.tmp')
            open(f1, 'w').close()
            new_name = join(tmp, 'a.cbz')
            dl = _make_download(files=[f1])
            with patch.object(pp, 'set_detected_extension', return_value=new_name), \
                 patch.object(pp, 'rename_file') as mock_rename, \
                 patch.object(pp, 'FilesDB') as MockFilesDB, \
                 patch.object(pp, 'commit') as mock_commit:
                pp.rename_with_proper_extension(dl)
            mock_rename.assert_called_once_with(f1, new_name)
            self.assertEqual(dl.files, [new_name])
            MockFilesDB.update_filepaths.assert_called_once_with({f1: new_name})
            mock_commit.assert_called_once()

    def test_noop_when_detected_extension_is_unchanged(self):
        with TemporaryDirectory() as tmp:
            f1 = join(tmp, 'a.cbz')
            open(f1, 'w').close()
            dl = _make_download(files=[f1])
            with patch.object(pp, 'set_detected_extension', return_value=f1), \
                 patch.object(pp, 'rename_file') as mock_rename, \
                 patch.object(pp, 'FilesDB') as MockFilesDB:
                pp.rename_with_proper_extension(dl)
            mock_rename.assert_not_called()
            MockFilesDB.update_filepaths.assert_not_called()

    def test_skips_files_that_no_longer_exist(self):
        dl = _make_download(files=['/does/not/exist'])
        with patch.object(pp, 'set_detected_extension') as mock_detect:
            pp.rename_with_proper_extension(dl)
        mock_detect.assert_not_called()


class convert_file_fn(unittest.TestCase):
    def test_noop_when_conversion_disabled(self):
        dl = _make_download(files=['/a'])
        with patch.object(pp, 'Settings') as MockSettings, \
             patch.object(pp, 'mass_convert') as mock_convert:
            MockSettings.return_value.sv.convert = False
            pp.convert_file(dl)
        mock_convert.assert_not_called()
        self.assertEqual(dl.files, ['/a'])

    def test_appends_converted_files_when_enabled(self):
        # download.files += mass_convert(...) mutates the same list object
        # that was passed in as filepath_filter, so call_args can't be
        # trusted after the fact -- capture a copy at call time instead.
        dl = _make_download(files=['/a'], volume_id=7, issue_id=3)
        captured = {}

        def _convert(volume_id, issue_id, filepath_filter,
                     update_websocket_files, process_individual_files):
            captured['args'] = (
                volume_id, issue_id, list(filepath_filter),
                update_websocket_files, process_individual_files
            )
            return ['/a.cbz']

        with patch.object(pp, 'Settings') as MockSettings, \
             patch.object(pp, 'mass_convert', side_effect=_convert):
            MockSettings.return_value.sv.convert = True
            pp.convert_file(dl)
        self.assertEqual(captured['args'], (7, 3, ['/a'], True, False))
        self.assertEqual(dl.files, ['/a', '/a.cbz'])


class set_file_properties_fn(unittest.TestCase):
    def test_processes_files_for_the_download_issue(self):
        dl = _make_download(volume_id=1, issue_id=2)
        with patch.object(pp, 'mass_process_files') as mock_process:
            pp.set_file_properties(dl)
        mock_process.assert_called_once_with(1, 2)


# region Post-Processor pipelines
class post_processor_pipeline_composition(unittest.TestCase):
    "Locks in exactly which actions run for each terminal download state"

    def test_success_pipeline(self):
        self.assertEqual(PostProcessor.actions_success, [
            pp.move_to_dest, pp.rename_with_proper_extension,
            pp.add_file_to_database, pp.remove_from_queue, pp.add_to_history,
            pp.convert_file, pp.record_download_file_provenance,
            pp.set_file_properties
        ])

    def test_the_download_leaves_the_queue_only_once_it_is_in_the_library(self):
        """Dequeuing first lost twenty-three finished downloads on
        2026-09-01: out of the queue, never into the library, no record
        anywhere that they existed."""
        order = PostProcessor.actions_success
        self.assertLess(
            order.index(pp.move_to_dest), order.index(pp.remove_from_queue))
        self.assertLess(
            order.index(pp.add_file_to_database),
            order.index(pp.remove_from_queue))

    def test_seeding_pipeline_is_empty_by_default(self):
        self.assertEqual(PostProcessor.actions_seeding, [])

    def test_canceled_pipeline_cleans_up_without_history(self):
        self.assertEqual(
            PostProcessor.actions_canceled,
            [pp.delete_file, pp.remove_from_queue]
        )

    def test_shutdown_pipeline_only_deletes_the_file(self):
        self.assertEqual(PostProcessor.actions_shutdown, [pp.delete_file])

    def test_failed_pipeline_records_history_and_cleans_up(self):
        self.assertEqual(PostProcessor.actions_failed, [
            pp.remove_from_queue, pp.add_to_history, pp.delete_file
        ])

    def test_perm_failed_pipeline_also_blocklists(self):
        self.assertEqual(PostProcessor.actions_perm_failed, [
            pp.remove_from_queue, pp.add_to_history,
            pp.add_dl_to_blocklist, pp.delete_file
        ])

    def test_torrents_complete_extracts_and_moves_instead_of_the_direct_move(self):
        self.assertIn(pp.move_torrent_to_dest,
                       PostProcessorTorrentsComplete.actions_success)
        self.assertNotIn(pp.move_to_dest,
                          PostProcessorTorrentsComplete.actions_success)

    def test_torrents_copy_seeds_before_the_success_cleanup(self):
        self.assertEqual(
            PostProcessorTorrentsCopy.actions_success,
            [pp.remove_from_queue, pp.delete_file]
        )
        self.assertEqual(PostProcessorTorrentsCopy.actions_seeding, [
            pp.add_to_history, pp.copy_file_torrent, pp.convert_file,
            pp.record_download_file_provenance, pp.set_file_properties,
            pp.reset_file_link
        ])


class post_processor_run_actions(unittest.TestCase):
    def test_success_runs_every_action_in_order_and_notifies(self):
        dl = _make_download()
        recorder = MagicMock()
        with patch.object(PostProcessor, 'actions_success',
                           [recorder.a, recorder.b]), \
             patch.object(pp, 'failed_integrity_check', return_value=None), \
             patch.object(pp, 'send_notification') as mock_notify:
            PostProcessor.success(dl)
        recorder.assert_has_calls([call.a(dl), call.b(dl)])
        mock_notify.assert_called_once_with(
            NotificationEvent.DOWNLOAD_COMPLETED, 'Download completed', dl.title
        )

    def test_perm_failed_sends_import_failed_notification(self):
        dl = _make_download()
        with patch.object(PostProcessor, 'actions_perm_failed', []), \
             patch.object(pp, 'send_notification') as mock_notify:
            PostProcessor.perm_failed(dl)
        mock_notify.assert_called_once_with(
            NotificationEvent.IMPORT_FAILED, 'Import failed', dl.title
        )

    def test_canceled_failed_shutdown_and_seeding_never_notify(self):
        dl = _make_download()
        with patch.object(PostProcessor, 'actions_canceled', []), \
             patch.object(PostProcessor, 'actions_failed', []), \
             patch.object(PostProcessor, 'actions_shutdown', []), \
             patch.object(PostProcessor, 'actions_seeding', []), \
             patch.object(pp, 'send_notification') as mock_notify:
            PostProcessor.canceled(dl)
            PostProcessor.failed(dl)
            PostProcessor.shutdown(dl)
            PostProcessor.seeding(dl)
        mock_notify.assert_not_called()

    def test_reset_file_link_restores_the_original_files(self):
        dl = _make_download(files=['/copy/path'])
        dl._original_files = ['/original/path']
        pp.reset_file_link(dl)
        self.assertEqual(dl.files, ['/original/path'])


if __name__ == '__main__':
    unittest.main()