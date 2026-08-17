import unittest
from asyncio import run
from threading import Event
from unittest.mock import MagicMock, patch

from backend.base.custom_exceptions import (ClientNotWorking,
                                            DownloadLimitReached,
                                            DownloadNotFound,
                                            DownloadUnmovable, InvalidKeyValue,
                                            IssueNotFound, LinkBroken)
from backend.base.definitions import (BlocklistReason, BrokenClientReason,
                                      DownloadSource, DownloadState,
                                      ExternalDownload)
from backend.features import download_queue as dq
from backend.features.download_queue import DownloadHandler


def _make_handler(**settings_overrides) -> DownloadHandler:
    """Build a DownloadHandler without going through __init__ (which needs a
    live DB/Settings and creates the download folder on disk) -- sets
    exactly the state the queue-management methods actually read."""
    handler = DownloadHandler.__new__(DownloadHandler)
    handler.queue = []
    settings = MagicMock()
    settings.sv.concurrent_direct_downloads = settings_overrides.get(
        'concurrent_direct_downloads', 1
    )
    settings.sv.delete_completed_downloads = settings_overrides.get(
        'delete_completed_downloads', False
    )
    handler.settings = settings
    return handler


def _make_external_download(states, **overrides) -> MagicMock:
    """An ExternalDownload stand-in whose .state walks through `states`, one
    new value per update_status() call (the last value sticks once the
    sequence is exhausted). `states` must end on a terminal state or the
    polling loop under test will never return."""
    dl = MagicMock(spec=ExternalDownload)
    dl.id = overrides.get('id', 1)
    dl.state = DownloadState.QUEUED_STATE
    dl.sleep_event = Event()
    dl.sleep_event.set()  # wait() returns immediately -- no real delay
    state_iter = iter(states)

    def _advance(*_a, **_k):
        try:
            dl.state = next(state_iter)
        except StopIteration:
            pass

    dl.update_status.side_effect = _advance
    return dl


@patch('backend.features.download_queue.WebSocket')
class run_external_download_terminal_states(unittest.TestCase):
    def test_canceled_removes_from_client_and_stops_polling(self, _mock_ws):
        handler = _make_handler()
        dl = _make_external_download([DownloadState.CANCELED_STATE])
        handler.queue = [dl]
        post_processer = MagicMock()

        handler._DownloadHandler__run_external_download(dl, post_processer)

        dl.remove_from_client.assert_called_once_with(delete_files=True)
        post_processer.canceled.assert_called_once_with(dl)
        post_processer.perm_failed.assert_not_called()
        post_processer.success.assert_not_called()
        self.assertNotIn(dl, handler.queue)
        self.assertEqual(dl.update_status.call_count, 1)

    def test_failed_state_routes_to_perm_failed_not_failed(self, _mock_ws):
        # A failure discovered while polling an external client is always
        # permanent from the queue's point of view -- there's no separate
        # "will retry" failed path here, unlike PostProcessor.failed().
        handler = _make_handler()
        dl = _make_external_download([DownloadState.FAILED_STATE])
        handler.queue = [dl]
        post_processer = MagicMock()

        handler._DownloadHandler__run_external_download(dl, post_processer)

        dl.remove_from_client.assert_called_once_with(delete_files=True)
        post_processer.perm_failed.assert_called_once_with(dl)
        post_processer.failed.assert_not_called()
        self.assertNotIn(dl, handler.queue)

    def test_shutdown_breaks_without_postprocessing_but_still_emits_removed(self, mock_ws_cls):
        handler = _make_handler()
        dl = _make_external_download([DownloadState.SHUTDOWN_STATE])
        handler.queue = [dl]
        post_processer = MagicMock()

        handler._DownloadHandler__run_external_download(dl, post_processer)

        dl.remove_from_client.assert_not_called()
        post_processer.canceled.assert_not_called()
        post_processer.success.assert_not_called()
        # Unlike the other terminal branches, shutdown does NOT remove the
        # download from the in-memory queue itself (stop_handle owns that) --
        # but it still fires the websocket "removed" event unconditionally,
        # after the one QueueStatusEvent from the single poll that saw
        # SHUTDOWN_STATE.
        self.assertIn(dl, handler.queue)
        emitted_events = [
            c.args[0] for c in mock_ws_cls.return_value.emit.call_args_list
        ]
        self.assertEqual(
            [type(e).__name__ for e in emitted_events],
            ['QueueStatusEvent', 'RemovedFromQueueEvent']
        )


@patch('backend.features.download_queue.WebSocket')
class run_external_download_progress(unittest.TestCase):
    def test_stalled_download_keeps_polling_then_succeeds_exactly_once(self, _mock_ws):
        handler = _make_handler(delete_completed_downloads=False)
        dl = _make_external_download([
            DownloadState.DOWNLOADING_STATE,
            DownloadState.DOWNLOADING_STATE,
            DownloadState.DOWNLOADING_STATE,
            DownloadState.IMPORTING_STATE,
        ])
        handler.queue = [dl]
        post_processer = MagicMock()

        handler._DownloadHandler__run_external_download(dl, post_processer)

        self.assertEqual(dl.update_status.call_count, 4)
        post_processer.success.assert_called_once_with(dl)
        dl.remove_from_client.assert_not_called()
        self.assertNotIn(dl, handler.queue)

    def test_importing_removes_from_client_only_when_setting_enabled(self, _mock_ws):
        handler = _make_handler(delete_completed_downloads=True)
        dl = _make_external_download([DownloadState.IMPORTING_STATE])
        handler.queue = [dl]
        post_processer = MagicMock()

        handler._DownloadHandler__run_external_download(dl, post_processer)

        dl.remove_from_client.assert_called_once_with(delete_files=False)
        post_processer.success.assert_called_once_with(dl)

    def test_importing_leaves_client_alone_when_setting_disabled(self, _mock_ws):
        handler = _make_handler(delete_completed_downloads=False)
        dl = _make_external_download([DownloadState.IMPORTING_STATE])
        handler.queue = [dl]
        post_processer = MagicMock()

        handler._DownloadHandler__run_external_download(dl, post_processer)

        dl.remove_from_client.assert_not_called()

    def test_seeding_fires_copy_postprocessor_exactly_once_despite_repeated_polls(self, _mock_ws):
        handler = _make_handler()
        dl = _make_external_download([
            DownloadState.SEEDING_STATE,
            DownloadState.SEEDING_STATE,
            DownloadState.SEEDING_STATE,
            DownloadState.IMPORTING_STATE,
        ])
        handler.queue = [dl]
        post_processer = MagicMock()

        handler._DownloadHandler__run_external_download(dl, post_processer)

        post_processer.seeding.assert_called_once_with(dl)
        post_processer.success.assert_called_once_with(dl)


class determine_link_type(unittest.TestCase):
    def test_getcomics_prefixed_link_is_gc(self):
        handler = _make_handler()
        result = handler._DownloadHandler__determine_link_type(
            dq.Constants.GC_SITE_URL + '/some/page'
        )
        self.assertEqual(result, 'gc')

    def test_link_matching_a_registered_indexer_is_nzb(self):
        handler = _make_handler()
        with patch.object(dq.Indexers, 'find_by_link', return_value=MagicMock()):
            result = handler._DownloadHandler__determine_link_type(
                'http://indexer.example/get/1'
            )
        self.assertEqual(result, 'nzb')

    def test_unrecognised_link_is_none(self):
        handler = _make_handler()
        with patch.object(dq.Indexers, 'find_by_link', return_value=None):
            result = handler._DownloadHandler__determine_link_type(
                'http://random.example/x'
            )
        self.assertIsNone(result)


class link_and_volume_lookups(unittest.TestCase):
    def test_link_in_queue_checks_both_web_and_download_link(self):
        handler = _make_handler()
        dl = MagicMock(web_link='https://web', download_link='https://dl')
        handler.queue = [dl]
        self.assertTrue(handler.link_in_queue('https://web'))
        self.assertTrue(handler.link_in_queue('https://dl'))
        self.assertFalse(handler.link_in_queue('https://other'))

    def test_download_for_volume_queued(self):
        handler = _make_handler()
        handler.queue = [MagicMock(volume_id=1)]
        self.assertTrue(handler.download_for_volume_queued(1))
        self.assertFalse(handler.download_for_volume_queued(2))

    def test_get_one_raises_when_missing(self):
        handler = _make_handler()
        handler.queue = []
        with self.assertRaises(DownloadNotFound):
            handler.get_one(999)


class add_duplicate_link(unittest.TestCase):
    def test_returns_empty_without_touching_any_source_when_already_queued(self):
        handler = _make_handler()
        existing = MagicMock(
            web_link='https://example/1', download_link='https://example/1/dl'
        )
        handler.queue = [existing]

        with patch.object(dq, 'GetComicsPage') as MockGCP, \
             patch.object(dq.Indexers, 'find_by_link') as mock_find:
            result, fail_reason = run(handler.add('https://example/1', volume_id=1))

        self.assertEqual(result, [])
        self.assertIsNone(fail_reason)
        MockGCP.assert_not_called()
        mock_find.assert_not_called()


class set_queue_location(unittest.TestCase):
    def test_reorders_a_queued_download(self):
        handler = _make_handler()
        a = MagicMock(id=1, state=DownloadState.QUEUED_STATE)
        b = MagicMock(id=2, state=DownloadState.QUEUED_STATE)
        handler.queue = [a, b]
        handler.set_queue_location(2, 0)
        self.assertEqual(handler.queue, [b, a])

    def test_rejects_a_download_that_is_not_queued(self):
        handler = _make_handler()
        a = MagicMock(id=1, state=DownloadState.DOWNLOADING_STATE)
        handler.queue = [a]
        with self.assertRaises(DownloadUnmovable):
            handler.set_queue_location(1, 0)

    def test_rejects_an_out_of_range_index(self):
        handler = _make_handler()
        a = MagicMock(id=1, state=DownloadState.QUEUED_STATE)
        handler.queue = [a]
        with self.assertRaises(InvalidKeyValue):
            handler.set_queue_location(1, 5)

    def test_unknown_id_raises_download_not_found(self):
        handler = _make_handler()
        handler.queue = []
        with self.assertRaises(DownloadNotFound):
            handler.set_queue_location(999, 0)


class process_queue(unittest.TestCase):
    def test_starts_queued_direct_downloads_up_to_the_concurrency_limit(self):
        handler = _make_handler(concurrent_direct_downloads=1)
        # Plain MagicMock()s (no spec) are correctly NOT ExternalDownload
        # instances, so they exercise the direct-download branch.
        a = MagicMock(state=DownloadState.QUEUED_STATE, download_thread=MagicMock())
        b = MagicMock(state=DownloadState.QUEUED_STATE, download_thread=MagicMock())
        handler.queue = [a, b]

        handler._process_queue()

        a.download_thread.start.assert_called_once()
        b.download_thread.start.assert_not_called()

    def test_downloading_state_counts_toward_the_limit(self):
        handler = _make_handler(concurrent_direct_downloads=1)
        already_active = MagicMock(state=DownloadState.DOWNLOADING_STATE)
        queued = MagicMock(state=DownloadState.QUEUED_STATE,
                            download_thread=MagicMock())
        handler.queue = [already_active, queued]

        handler._process_queue()

        queued.download_thread.start.assert_not_called()

    def test_external_downloads_are_never_started_from_here(self):
        # ExternalDownload's own thread is started at enqueue time
        # (__prepare_downloads_for_queue) and driven by its own polling
        # loop -- _process_queue only manages the direct-download slots.
        handler = _make_handler(concurrent_direct_downloads=5)
        ext = MagicMock(spec=ExternalDownload, state=DownloadState.QUEUED_STATE)
        ext.download_thread = MagicMock()
        handler.queue = [ext]

        handler._process_queue()

        ext.download_thread.start.assert_not_called()


@patch('backend.features.download_queue.WebSocket')
class remove_download(unittest.TestCase):
    def test_never_started_download_is_a_noop(self, _mock_ws):
        handler = _make_handler()
        dl = MagicMock(id=1, download_thread=None)
        handler.queue = [dl]

        handler.remove(1)

        dl.stop.assert_not_called()
        self.assertIn(dl, handler.queue)

    def test_queued_download_is_stopped_and_postprocessed_as_canceled(self, _mock_ws):
        handler = _make_handler()
        thread = MagicMock()
        thread.is_alive.return_value = True
        dl = MagicMock(id=1, download_thread=thread,
                        state=DownloadState.QUEUED_STATE)
        handler.queue = [dl]

        with patch.object(dq, 'PostProcessor') as MockPP:
            handler.remove(1)

        dl.stop.assert_called_once_with()
        MockPP.canceled.assert_called_once_with(dl)
        self.assertNotIn(dl, handler.queue)

    def test_downloading_thread_that_died_silently_is_cleaned_up(self, _mock_ws):
        # The thread is no longer alive despite the download never reaching
        # a terminal state -- it errored out without going through the
        # normal FAILED_STATE/CANCELED_STATE path. `not was_thread_running`
        # is exactly the signal that catches this and still cleans up.
        handler = _make_handler()
        thread = MagicMock()
        thread.is_alive.return_value = False
        dl = MagicMock(id=1, download_thread=thread,
                        state=DownloadState.DOWNLOADING_STATE)
        handler.queue = [dl]

        with patch.object(dq, 'PostProcessor') as MockPP:
            handler.remove(1)

        MockPP.canceled.assert_called_once_with(dl)
        self.assertNotIn(dl, handler.queue)

    def test_downloading_thread_still_actively_running_is_left_alone(self, _mock_ws):
        handler = _make_handler()
        thread = MagicMock()
        thread.is_alive.return_value = True
        dl = MagicMock(id=1, download_thread=thread,
                        state=DownloadState.DOWNLOADING_STATE)
        handler.queue = [dl]

        with patch.object(dq, 'PostProcessor') as MockPP:
            handler.remove(1)

        MockPP.canceled.assert_not_called()
        self.assertIn(dl, handler.queue)

    def test_external_download_is_never_postprocessed_inline(self, _mock_ws):
        # An ExternalDownload's own polling loop (CANCELED_STATE branch)
        # owns its post-processing once download.stop() flips its state --
        # the guard here exists specifically to avoid double-processing it.
        handler = _make_handler()
        thread = MagicMock()
        thread.is_alive.return_value = True
        dl = MagicMock(spec=ExternalDownload, id=1, download_thread=thread,
                        state=DownloadState.QUEUED_STATE)
        handler.queue = [dl]

        with patch.object(dq, 'PostProcessor') as MockPP:
            handler.remove(1)

        MockPP.canceled.assert_not_called()
        self.assertIn(dl, handler.queue)

    def test_blocklist_flag_adds_the_download_to_the_blocklist(self, _mock_ws):
        handler = _make_handler()
        thread = MagicMock()
        thread.is_alive.return_value = True
        dl = MagicMock(
            id=1, download_thread=thread, state=DownloadState.DOWNLOADING_STATE,
            web_link='wl', web_title='wt', web_sub_title='ws',
            download_link='dl', source_type='src', volume_id=2, issue_id=3
        )
        handler.queue = [dl]

        with patch.object(dq, 'PostProcessor'), \
             patch.object(dq, 'add_to_blocklist') as mock_blocklist:
            handler.remove(1, blocklist=True)

        mock_blocklist.assert_called_once_with(
            web_link='wl', web_title='wt', web_sub_title='ws',
            download_link='dl', source='src', volume_id=2, issue_id=3,
            reason=BlocklistReason.ADDED_BY_USER
        )


@patch('backend.features.download_queue.WebSocket')
@patch('backend.features.download_queue.Server')
class load_downloads_restart(unittest.TestCase):
    "Covers __load_downloads(), the path that repopulates the queue on startup"

    @staticmethod
    def _row(**overrides):
        row = {
            'id': 1, 'volume_id': 1, 'client_type': 'stub',
            'external_client_id': None, 'download_link': 'https://x',
            'covered_issues': None, 'force_original_name': False,
            'source_type': DownloadSource.GETCOMICS.value, 'source_name': 'GetComics',
            'web_link': 'https://x', 'web_title': 'Batman 001',
            'web_sub_title': None,
        }
        row.update(overrides)
        return row

    @staticmethod
    def _cursor_returning(rows):
        cursor = MagicMock()
        cursor.execute.return_value.fetchall.return_value = rows
        return cursor

    @staticmethod
    def _stub_class(raises=None, returns=None):
        """__load_downloads() does `issubclass(download_type_to_class[...],
        ExternalDownload)` before instantiating, so the registry entry must
        be a real class -- a bare MagicMock() instance fails issubclass()
        with a TypeError, not a clean test failure."""
        class _Stub:
            def __new__(cls, **kwargs):
                if raises is not None:
                    raise raises
                return returns
        return _Stub

    def test_broken_link_is_blocklisted_and_the_row_is_dropped(self, _MockServer, _mock_ws):
        # Regression test for a real bug found while writing this coverage:
        # the LinkBroken except-handler read download['source'], but the
        # download_queue SELECT only ever fetches 'source_type' -- on a real
        # sqlite3.Row this raised IndexError (not caught by `except
        # LinkBroken`), which killed __load_downloads mid-loop and silently
        # dropped every row after the broken one from the restored queue on
        # every server restart. Fixed alongside this test.
        handler = _make_handler()
        cursor = self._cursor_returning([self._row()])
        stub_cls = self._stub_class(raises=LinkBroken('https://x'))

        with patch.object(dq, 'get_db', return_value=cursor), \
             patch.object(dq, 'iter_commit', side_effect=lambda rows: rows), \
             patch.dict(dq.download_type_to_class, {'stub': stub_cls}, clear=True), \
             patch.object(dq, 'add_to_blocklist') as mock_blocklist:
            handler._DownloadHandler__load_downloads()

        mock_blocklist.assert_called_once()
        self.assertEqual(mock_blocklist.call_args.kwargs['source'],
                          DownloadSource.GETCOMICS)
        self.assertEqual(mock_blocklist.call_args.kwargs['reason'],
                          BlocklistReason.LINK_BROKEN)
        delete_call = cursor.execute.call_args_list[-1]
        self.assertIn('DELETE FROM download_queue', delete_call.args[0])
        self.assertEqual(delete_call.args[1], (1,))
        self.assertEqual(handler.queue, [])

    def test_startup_client_errors_drop_the_row_without_blocklisting(self, _MockServer, _mock_ws):
        for exc in (
            DownloadLimitReached(DownloadSource.GETCOMICS),
            IssueNotFound(1),
            ClientNotWorking(BrokenClientReason.CONNECTION_ERROR),
        ):
            with self.subTest(exc=type(exc).__name__):
                handler = _make_handler()
                cursor = self._cursor_returning([self._row()])
                stub_cls = self._stub_class(raises=exc)

                with patch.object(dq, 'get_db', return_value=cursor), \
                     patch.object(dq, 'iter_commit', side_effect=lambda rows: rows), \
                     patch.dict(dq.download_type_to_class, {'stub': stub_cls}, clear=True), \
                     patch.object(dq, 'add_to_blocklist') as mock_blocklist:
                    handler._DownloadHandler__load_downloads()

                mock_blocklist.assert_not_called()
                delete_call = cursor.execute.call_args_list[-1]
                self.assertIn('DELETE FROM download_queue', delete_call.args[0])
                self.assertEqual(handler.queue, [])

    def test_successfully_reconstructed_row_is_restored_into_the_live_queue(self, _MockServer, _mock_ws):
        handler = _make_handler()
        cursor = self._cursor_returning([self._row(id=7)])
        restored = MagicMock()
        restored.id = None
        stub_cls = self._stub_class(returns=restored)

        with patch.object(dq, 'get_db', return_value=cursor), \
             patch.object(dq, 'iter_commit', side_effect=lambda rows: rows), \
             patch.dict(dq.download_type_to_class, {'stub': stub_cls}, clear=True):
            handler._DownloadHandler__load_downloads()

        self.assertEqual(restored.id, 7)
        self.assertIn(restored, handler.queue)

    def test_no_rows_leaves_the_queue_empty(self, _MockServer, _mock_ws):
        handler = _make_handler()
        cursor = self._cursor_returning([])

        with patch.object(dq, 'get_db', return_value=cursor), \
             patch.object(dq, 'iter_commit', side_effect=lambda rows: rows):
            handler._DownloadHandler__load_downloads()

        self.assertEqual(handler.queue, [])


if __name__ == '__main__':
    unittest.main()
