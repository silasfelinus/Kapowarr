# -*- coding: utf-8 -*-

"""A download client reports paths in its own filesystem, not Kapowarr's."""

import unittest
from unittest.mock import MagicMock, patch

from backend.base.definitions import DownloadState


class the_clients_storage_path_is_translated(unittest.TestCase):
    """`run()` already translates the other direction.

    Telling the client where to download to goes through `local_to_remote`;
    the return trip was missing entirely -- `remote_to_local` existed and was
    called from nowhere. A SABnzbd in another container reports a path
    Kapowarr cannot open, so the download completed, was recorded as a
    success, and then vanished: post-processing raised FileNotFoundError on a
    path that only exists on the client's side, and the issue stayed unfiled.
    """

    def _download(self):
        from backend.implementations.download_clients import NZBDownload

        download = NZBDownload.__new__(NZBDownload)
        download._external_id = 'nzo_1'
        download._state = DownloadState.DOWNLOADING_STATE
        download._files = []
        # Nothing exists under here, so the download-folder fallback in
        # `_locate_completed_download` finds nothing and these tests stay
        # about the mapping itself.
        download._download_folder = '/nonexistent-download-folder'
        download._title = 'Batman 001'
        client = MagicMock()
        client.id = 7
        download._external_client = client
        return download, client

    def _update(self, download, client, status, mapping):
        from backend.implementations import download_clients as dc

        client.get_download.return_value = status
        with patch.object(
            dc.RemoteMappings, 'remote_to_local',
            side_effect=lambda client_id, path: mapping.get(path, path)
        ):
            download.update_status()
        return download._files

    def _status(self, storage):
        return {
            'progress': 100.0, 'speed': 0.0, 'size': 1,
            'state': DownloadState.IMPORTING_STATE, 'storage': storage
        }

    def test_the_reported_path_is_mapped_into_kapowarrs_filesystem(self):
        download, client = self._download()

        files = self._update(
            download, client,
            self._status('/pc/miss_kitty/prowlarr/A Year of Marvels'),
            {'/pc/miss_kitty/prowlarr/A Year of Marvels': '/downloads/A Year of Marvels'}
        )

        self.assertEqual(files, ['/downloads/A Year of Marvels'])

    def test_an_unmapped_path_is_used_as_is(self):
        """Same-host setups have no mapping, and must keep working."""
        download, client = self._download()

        files = self._update(
            download, client, self._status('/downloads/Batman 001'), {}
        )

        self.assertEqual(files, ['/downloads/Batman 001'])

    def test_the_client_id_decides_the_mapping(self):
        download, client = self._download()
        seen = []

        from backend.implementations import download_clients as dc

        client.get_download.return_value = self._status('/remote/x')
        with patch.object(
            dc.RemoteMappings, 'remote_to_local',
            side_effect=lambda client_id, path: seen.append(client_id) or path
        ):
            download.update_status()

        self.assertEqual(seen, [7], 'mappings are per download client')

    def test_nothing_is_recorded_before_the_download_finishes(self):
        download, client = self._download()
        status = self._status('/remote/x')
        status['state'] = DownloadState.DOWNLOADING_STATE

        files = self._update(download, client, status, {})

        self.assertEqual(files, [], 'the path is not final until importing')


if __name__ == '__main__':
    unittest.main()
