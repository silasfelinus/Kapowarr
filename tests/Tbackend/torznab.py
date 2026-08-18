import sqlite3
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from bencoding import bencode

from backend.base.definitions import (DownloadType, SpecialVersion,
                                      VolumeData)
from backend.features import search
from backend.implementations import torznab as tz
from backend.implementations.query_builders import QueryBuilders
from backend.internals.db import KapowarrCursor


class torznab_link_tags(unittest.TestCase):
    def test_round_trip_preserves_link_and_metadata(self):
        original = 'magnet:?xt=urn:btih:abc&dn=Batman'
        tagged = tz.tag_torznab_link(original, 17, 'Batman #1')
        clean, indexer_id, title = tz.strip_torznab_tag(tagged)
        self.assertEqual(clean, original)
        self.assertEqual(indexer_id, 17)
        self.assertEqual(title, 'Batman #1')
        self.assertTrue(tz.is_torznab_link(tagged))

    def test_http_query_is_unchanged_by_fragment_tag(self):
        original = 'https://prowlarr.example/1/api?t=get&id=abc&apikey=secret'
        tagged = tz.tag_torznab_link(original, 3, 'Batman 001')
        clean, _, _ = tz.strip_torznab_tag(tagged)
        self.assertEqual(clean, original)


class torznab_registry(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(':memory:')
        self.connection.row_factory = sqlite3.Row
        self.patch = patch.object(
            tz,
            'get_db',
            side_effect=lambda *a, **k: self._cursor()
        )
        self.patch.start()

    def _cursor(self):
        cursor = KapowarrCursor(self.connection)
        cursor.row_factory = sqlite3.Row
        return cursor

    def tearDown(self):
        self.patch.stop()
        self.connection.close()

    def test_table_is_created_idempotently_and_adds_default_comics_category(self):
        first = tz.TorznabIndexers.add(
            'Prowlarr',
            'prowlarr.local/1/api',
            'key'
        )
        second = tz.TorznabIndexers.get_one(first.id)
        self.assertEqual(second.base_url, 'http://prowlarr.local/1/api')
        self.assertEqual(second.categories, '7030')
        self.assertEqual(second.get_data()['protocol'], 'torznab')

    def test_multiple_enabled_feeds_are_supported(self):
        tz.TorznabIndexers.add('Prowlarr', 'http://p/1/api', 'k')
        tz.TorznabIndexers.add('Jackett', 'http://j/api', 'k')
        tz.TorznabIndexers.add('Off', 'http://off/api', 'k', enabled=False)
        self.assertEqual(
            [i.title for i in tz.TorznabIndexers.get_enabled()],
            ['Jackett', 'Prowlarr']
        )


class _FakeSession:
    def __init__(self, body):
        self.body = body
        self.calls = []

    async def get_text(self, url, params={}, headers={}, quiet_fail=False):
        self.calls.append((url, params, quiet_fail))
        return self.body


def _indexer(categories='7030'):
    result = tz.TorznabIndexer.__new__(tz.TorznabIndexer)
    result._id = 4
    result._title = 'Prowlarr'
    result._base_url = 'https://prowlarr.example/1/api'
    result._api_key = 'secret'
    result._categories = categories
    result._enabled = True
    return result


class torznab_search(unittest.IsolatedAsyncioTestCase):
    async def test_parses_namespaced_attributes_and_preserves_source(self):
        body = '''<?xml version="1.0"?>
        <rss xmlns:torznab="http://torznab.com/schemas/2015/feed">
          <channel>
            <item>
              <title>Batman (2016) 042</title>
              <link>https://prowlarr.example/details/42</link>
              <enclosure url="https://prowlarr.example/1/api?t=get&amp;id=42" type="application/x-bittorrent" />
              <torznab:attr name="infohash" value="ABCDEF" />
              <torznab:attr name="seeders" value="12" />
            </item>
          </channel>
        </rss>'''
        session = _FakeSession(body)
        results = await tz.search_torznab_indexer(
            session, _indexer(), 'Batman #42'
        )
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['source'], 'Prowlarr')
        self.assertEqual(results[0]['issue_number'], 42.0)
        clean, source_id, title = tz.strip_torznab_tag(results[0]['link'])
        self.assertEqual(
            clean,
            'https://prowlarr.example/1/api?t=get&id=42'
        )
        self.assertEqual(source_id, 4)
        self.assertEqual(title, 'Batman (2016) 042')
        self.assertEqual(session.calls[0][1]['cat'], '7030')

    async def test_infohash_without_enclosure_builds_magnet(self):
        body = '''<rss xmlns:torznab="http://torznab.com/schemas/2015/feed">
          <channel><item><title>Batman 001</title>
          <torznab:attr name="infohash" value="ABCDEF1234" />
          </item></channel></rss>'''
        session = _FakeSession(body)
        results = await tz.search_torznab_indexer(
            session, _indexer(''), 'Batman'
        )
        clean, _, _ = tz.strip_torznab_tag(results[0]['link'])
        self.assertTrue(clean.startswith('magnet:?'))
        self.assertNotIn('cat', session.calls[0][1])

    async def test_malformed_xml_isolated_to_empty_result(self):
        results = await tz.search_torznab_indexer(
            _FakeSession('<rss><broken>'), _indexer(), 'Batman'
        )
        self.assertEqual(results, [])


class torrent_metadata_conversion(unittest.TestCase):
    def test_torrent_bytes_produce_magnet_and_real_root_name(self):
        payload = bencode({
            b'announce': b'https://tracker.example/announce',
            b'info': {
                b'name': b'Batman Pack',
                b'piece length': 16384,
                b'pieces': b'12345678901234567890',
                b'length': 123
            }
        })
        magnet, name = tz.torrent_bytes_to_magnet(payload, 'fallback')
        self.assertEqual(name, 'Batman Pack')
        self.assertIn('xt=', magnet)
        self.assertIn('dn=Batman+Pack', magnet)


class torznab_search_registration(unittest.TestCase):
    def test_torrent_protocol_is_registered_for_search_and_queries(self):
        self.assertIn(
            search.SearchTorznab,
            search.SearchSources.sources[DownloadType.TORRENT]
        )
        self.assertIsNotNone(QueryBuilders.get(DownloadType.TORRENT))


def _volume_mock():
    volume = MagicMock()
    volume.get_data.return_value = VolumeData(
        id=1, comicvine_id=1, title='Batman', alt_title=None,
        year=2016, volume_number=1, description='', site_url='',
        publisher=None, monitored=True, monitor_new_issues=True,
        root_folder=1, folder='/Batman', custom_folder=False,
        special_version=SpecialVersion.NORMAL,
        special_version_locked=False, last_cv_fetch=0
    )
    volume.get_issues.return_value = []
    return volume


class create_torznab_download_test(unittest.IsolatedAsyncioTestCase):
    async def test_forced_magnet_download_keeps_indexer_provenance(self):
        tagged = tz.tag_torznab_link(
            'magnet:?xt=urn:btih:ABC&dn=Batman+001',
            8,
            'Batman (2016) 001'
        )
        indexer = SimpleNamespace(id=8, title='Prowlarr')
        fake_client = MagicMock()
        fake_client.id = 2

        with patch.object(
            tz.TorznabIndexers, 'get_one', return_value=indexer
        ), patch.object(
            tz, 'Volume', return_value=_volume_mock()
        ), patch.object(
            tz.ExternalClients,
            'get_least_used_client',
            return_value=fake_client
        ), patch.object(
            tz,
            'Settings',
            return_value=SimpleNamespace(
                sv=SimpleNamespace(
                    download_folder='/downloads',
                    rename_downloaded_files=False
                )
            )
        ):
            download = await tz.create_torznab_download(
                tagged, 1, None, force_match=True
            )

        self.assertIsInstance(download, tz.IndexerTorrentDownload)
        self.assertEqual(download.source_name, 'Prowlarr')
        self.assertEqual(download.web_link, tagged)
        self.assertEqual(download.title, 'Batman 001')


if __name__ == '__main__':
    unittest.main()
