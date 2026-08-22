import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from backend.implementations import indexers as nz
from backend.implementations import torznab as tz


class _ConcurrentSession:
    def __init__(self):
        self.active = 0
        self.max_active = 0
        self.calls = []

    async def get_text(self, url, params={}, headers={}, quiet_fail=False):
        self.calls.append((url, params.get('q')))
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        await asyncio.sleep(0.02)
        self.active -= 1
        return '<rss><channel /></rss>'


def _indexer(indexer_id=39, base_url='https://prowlarr.example/39/api'):
    return SimpleNamespace(
        id=indexer_id,
        title='L337x',
        base_url=base_url,
        api_key='secret',
        categories='7030',
        category_filter_enabled=True,
        enabled=True
    )


def _newznab_indexer(indexer_id=33, base_url='https://prowlarr.example/33/api'):
    result = nz.Indexer.__new__(nz.Indexer)
    result._id = indexer_id
    result._title = 'NZB.SU'
    result._base_url = base_url
    result._api_key = 'secret'
    result._enabled = True
    return result


class torznab_request_pacing(unittest.IsolatedAsyncioTestCase):
    async def test_query_variants_do_not_overlap_on_same_feed(self):
        session = _ConcurrentSession()
        indexer = _indexer()

        with patch.object(tz, 'TORZNAB_REQUEST_MIN_INTERVAL', 0):
            await asyncio.gather(
                tz.search_torznab_indexer(session, indexer, 'Batman 1'),
                tz.search_torznab_indexer(session, indexer, 'Batman #1'),
                tz.search_torznab_indexer(session, indexer, 'Batman 001')
            )

        self.assertEqual(len(session.calls), 3)
        self.assertEqual(session.max_active, 1)

    async def test_different_feeds_can_still_search_in_parallel(self):
        session = _ConcurrentSession()

        with patch.object(tz, 'TORZNAB_REQUEST_MIN_INTERVAL', 0):
            await asyncio.gather(
                tz.search_torznab_indexer(
                    session,
                    _indexer(39, 'https://prowlarr.example/39/api'),
                    'Batman'
                ),
                tz.search_torznab_indexer(
                    session,
                    _indexer(40, 'https://prowlarr.example/40/api'),
                    'Batman'
                )
            )

        self.assertEqual(session.max_active, 2)


class torznab_feed_ownership(unittest.TestCase):
    def test_untagged_configured_feed_url_is_recognised(self):
        indexer = _indexer()
        with patch.object(
            tz.TorznabIndexers, 'get_enabled', return_value=[indexer]
        ):
            found = tz.TorznabIndexers.find_by_link(
                'https://prowlarr.example/39/api?t=get&id=abc'
            )
        self.assertIs(found, indexer)

    def test_shared_host_is_not_enough_to_claim_other_feed(self):
        indexer = _indexer()
        with patch.object(
            tz.TorznabIndexers, 'get_enabled', return_value=[indexer]
        ):
            found = tz.TorznabIndexers.find_by_link(
                'https://prowlarr.example/33/api?t=get&id=nzb'
            )
        self.assertIsNone(found)


class newznab_same_host_guard(unittest.TestCase):
    def test_newznab_does_not_claim_sibling_torznab_feed(self):
        indexer = _newznab_indexer()
        with patch.object(nz.Indexers, 'get_enabled', return_value=[indexer]):
            found = nz.Indexers.find_by_link(
                'https://prowlarr.example/39/api?t=get&id=torrent'
            )
        self.assertIsNone(found)

    def test_host_level_prowlarr_download_compatibility_remains(self):
        indexer = _newznab_indexer()
        with patch.object(nz.Indexers, 'get_enabled', return_value=[indexer]):
            found = nz.Indexers.find_by_link(
                'https://prowlarr.example/download/nzb-123'
            )
        self.assertIs(found, indexer)


if __name__ == '__main__':
    unittest.main()
