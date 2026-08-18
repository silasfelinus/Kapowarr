import unittest
from unittest.mock import MagicMock

from backend.features.download_queue import download_type_to_class
from backend.implementations.download_preppers import (
    DownloadPreppers,
    TorznabDownloadPrepper,
)
from backend.implementations.torrent_clients.qBittorrent import qBittorrent
from backend.implementations.torznab import (IndexerTorrentDownload,
                                            tag_torznab_link)


class TorznabQueueIntegrationTest(unittest.TestCase):
    def test_torznab_download_type_can_be_restored_after_restart(self):
        self.assertIs(
            download_type_to_class['indexer_torrent'],
            IndexerTorrentDownload
        )

    def test_torznab_link_resolves_to_torznab_prepper(self):
        link = tag_torznab_link(
            'magnet:?xt=urn:btih:ABC&dn=Batman',
            4,
            'Batman 001'
        )
        self.assertIs(
            DownloadPreppers.get_for_link(link),
            TorznabDownloadPrepper
        )

    def test_qbittorrent_accepts_percent_encoded_xt(self):
        client = qBittorrent.__new__(qBittorrent)
        client._base_url = 'http://qbittorrent.local'
        client._username = None
        client._password = None
        client.ssn = MagicMock()
        client.torrent_hashes = {}

        result = client.add_download(
            'magnet:?xt=urn%3Abtih%3AABCDEF&dn=Batman',
            '/downloads',
            None
        )

        self.assertEqual(result, 'ABCDEF')
        self.assertIn('ABCDEF', client.torrent_hashes)
        client.ssn.post.assert_called_once()


if __name__ == '__main__':
    unittest.main()
