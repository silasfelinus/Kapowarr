import unittest
from threading import Event
from unittest.mock import MagicMock, patch

from backend.base.definitions import DownloadState
from backend.features.download_queue import download_type_to_class
from backend.implementations.download_clients import (BaseDirectDownload,
                                                      ExternalDownload,
                                                      NZBDownload)


def _make_download(external_id=None) -> NZBDownload:
    """Build an NZBDownload without going through __init__ (which needs a
    live DB/Settings/Volume) -- sets exactly the private state __init__
    would, so update_status()/run()/remove_from_client() are exercised
    against realistic starting state."""
    dl = NZBDownload.__new__(NZBDownload)
    dl._id = 1
    dl._download_link = 'http://indexer.example/get/1.nzb'
    dl._volume_id = 1
    dl._issue_id = None
    dl._covered_issues = None
    dl._web_link = None
    dl._web_title = 'Batman 001'
    dl._web_sub_title = None
    dl._state = DownloadState.QUEUED_STATE
    dl._progress = 0.0
    dl._speed = 0.0
    dl._size = -1
    dl._download_thread = None
    dl._download_folder = '/downloads'
    dl._sleep_event = Event()
    dl._original_files = []
    dl._external_id = external_id
    dl._external_client = MagicMock()
    dl._filename_body = 'Batman 001'
    dl._title = 'Batman 001'
    dl._files = []
    return dl


class nzb_download_registration(unittest.TestCase):
    def test_registered_under_its_identifier(self):
        self.assertIs(download_type_to_class['nzb'], NZBDownload)

    def test_is_both_external_and_direct_download(self):
        # Same shape as TorrentDownload: ExternalDownload for the
        # client-polling side, BaseDirectDownload for the folder-based
        # file handling (extraction, moving, renaming) side.
        self.assertTrue(issubclass(NZBDownload, ExternalDownload))
        self.assertTrue(issubclass(NZBDownload, BaseDirectDownload))


class nzb_download_run(unittest.TestCase):
    def test_run_submits_via_external_client_and_records_external_id(self):
        dl = _make_download()
        dl._external_client.id = 5
        dl._external_client.add_download.return_value = 'nzo_1'

        with patch(
            'backend.implementations.download_clients.RemoteMappings.local_to_remote',
            return_value='/downloads'
        ) as local_to_remote:
            dl.run()

        dl._external_client.add_download.assert_called_once_with(
            'http://indexer.example/get/1.nzb',
            '/downloads',
            'Batman 001'
        )
        local_to_remote.assert_called_once_with(5, '/downloads')
        self.assertEqual(dl.external_id, 'nzo_1')


class nzb_download_update_status(unittest.TestCase):
    def test_no_external_id_is_a_noop(self):
        dl = _make_download(external_id=None)
        dl.update_status()
        dl._external_client.get_download.assert_not_called()
        self.assertEqual(dl.state, DownloadState.QUEUED_STATE)

    def test_empty_dict_leaves_state_untouched(self):
        dl = _make_download(external_id='nzo_1')
        dl._external_client.get_download.return_value = {}
        dl.update_status()
        self.assertEqual(dl.state, DownloadState.QUEUED_STATE)

    def test_none_marks_canceled(self):
        dl = _make_download(external_id='nzo_1')
        dl._external_client.get_download.return_value = None
        dl.update_status()
        self.assertEqual(dl.state, DownloadState.CANCELED_STATE)

    def test_downloading_updates_progress_without_touching_files(self):
        dl = _make_download(external_id='nzo_1')
        dl._external_client.get_download.return_value = {
            'size': 1000,
            'progress': 42.5,
            'speed': 2048,
            'state': DownloadState.DOWNLOADING_STATE,
            'storage': None
        }
        dl.update_status()

        self.assertEqual(dl.state, DownloadState.DOWNLOADING_STATE)
        self.assertEqual(dl.progress, 42.5)
        self.assertEqual(dl.speed, 2048)
        self.assertEqual(dl.size, 1000)
        self.assertEqual(dl.files, [])

    def test_importing_with_storage_sets_files_for_extraction_pickup(self):
        dl = _make_download(external_id='nzo_1')
        dl._external_client.get_download.return_value = {
            'size': 1000,
            'progress': 100.0,
            'speed': 0,
            'state': DownloadState.IMPORTING_STATE,
            'storage': '/downloads/kapowarr/Batman 001'
        }
        dl.update_status()

        self.assertEqual(dl.state, DownloadState.IMPORTING_STATE)
        self.assertEqual(dl.files, ['/downloads/kapowarr/Batman 001'])

    def test_canceled_state_is_not_overwritten_by_a_late_client_report(self):
        dl = _make_download(external_id='nzo_1')
        dl._state = DownloadState.CANCELED_STATE
        dl._external_client.get_download.return_value = {
            'size': 1000,
            'progress': 50.0,
            'speed': 100,
            'state': DownloadState.DOWNLOADING_STATE,
            'storage': None
        }
        dl.update_status()

        # Mirrors TorrentDownload.update_status()'s identical guard.
        self.assertEqual(dl.state, DownloadState.CANCELED_STATE)


class nzb_download_remove_from_client(unittest.TestCase):
    def test_no_external_id_is_a_noop(self):
        dl = _make_download(external_id=None)
        dl.remove_from_client(delete_files=True)
        dl._external_client.delete_download.assert_not_called()

    def test_delegates_to_external_client(self):
        dl = _make_download(external_id='nzo_1')
        dl.remove_from_client(delete_files=True)
        dl._external_client.delete_download.assert_called_once_with(
            'nzo_1', True
        )


if __name__ == '__main__':
    unittest.main()
