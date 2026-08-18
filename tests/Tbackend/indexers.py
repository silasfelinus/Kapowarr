import sqlite3
import unittest
from unittest.mock import MagicMock, patch

from aiohttp import ClientError

from backend.base.custom_exceptions import (EnqueuingDownloadFailure,
                                            IndexerNotFound, KeyNotFound)
from backend.base.definitions import (DownloadSource,
                                      EnqueuingDownloadFailureReason,
                                      SpecialVersion, VolumeData)
from backend.implementations import indexers as indexers_module
from backend.implementations.indexers import (
    Indexer, Indexers, _extract_item_link, _parse_content_disposition_filename,
    create_nzb_download, search_indexer)
from backend.internals.db import KapowarrCursor


# =====================
# Pure helper functions
# =====================
class content_disposition_parsing(unittest.TestCase):
    def test_plain_filename(self):
        self.assertEqual(
            _parse_content_disposition_filename(
                'attachment; filename="Batman (2020) 001"'
            ),
            'Batman (2020) 001'
        )

    def test_rfc5987_filename_star(self):
        self.assertEqual(
            _parse_content_disposition_filename(
                "attachment; filename*=UTF-8''Batman%20001.nzb"
            ),
            'Batman%20001.nzb'
        )

    def test_missing_header(self):
        self.assertIsNone(_parse_content_disposition_filename(''))

    def test_unparseable_header(self):
        self.assertIsNone(_parse_content_disposition_filename('attachment'))


class newznab_item_link_extraction(unittest.TestCase):
    def test_enclosure_url_preferred(self):
        item = {
            "enclosure": {"@attributes": {"url": "http://idx/get/1"}},
            "link": "http://idx/details/1"
        }
        self.assertEqual(_extract_item_link(item), "http://idx/get/1")

    def test_falls_back_to_link(self):
        item = {"link": "http://idx/get/1"}
        self.assertEqual(_extract_item_link(item), "http://idx/get/1")

    def test_falls_back_to_permalink_guid(self):
        item = {
            "guid": {
                "#text": "http://idx/get/1",
                "@attributes": {"isPermaLink": "true"}
            }
        }
        self.assertEqual(_extract_item_link(item), "http://idx/get/1")

    def test_non_permalink_guid_is_not_used(self):
        item = {
            "guid": {
                "#text": "abc-123",
                "@attributes": {"isPermaLink": "false"}
            }
        }
        self.assertIsNone(_extract_item_link(item))

    def test_nothing_found(self):
        self.assertIsNone(_extract_item_link({}))

    def test_null_enclosure_attributes_falls_back_to_link(self):
        # `.get("@attributes", {})`'s default only applies when the key is
        # *absent* -- a present-but-null value used to raise AttributeError
        # on the chained `.get("url")` (kapowarr/t-024).
        item = {
            "enclosure": {"@attributes": None},
            "link": "http://idx/details/1"
        }
        self.assertEqual(_extract_item_link(item), "http://idx/details/1")

    def test_null_guid_attributes_does_not_raise(self):
        item = {"guid": {"#text": "abc-123", "@attributes": None}}
        self.assertIsNone(_extract_item_link(item))


# =====================
# Registry (Indexer/Indexers)
# =====================
class indexer_registry(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(':memory:')
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("""
            CREATE TABLE indexers(
                id INTEGER PRIMARY KEY,
                title VARCHAR(255) NOT NULL,
                base_url TEXT NOT NULL,
                api_key VARCHAR(255) NOT NULL,
                enabled BOOL NOT NULL DEFAULT 1
            );
        """)
        self.get_db_patch = patch.object(
            indexers_module,
            'get_db',
            side_effect=lambda *a, **k: self._cursor()
        )
        self.get_db_patch.start()

    def _cursor(self) -> KapowarrCursor:
        c = KapowarrCursor(self.connection)
        c.row_factory = sqlite3.Row
        return c

    def tearDown(self):
        self.get_db_patch.stop()
        self.connection.close()

    def test_add_and_get_one(self):
        indexer = Indexers.add('NZBgeek', 'https://api.nzbgeek.info', 'key123')
        self.assertIsInstance(indexer, Indexer)
        self.assertEqual(indexer.title, 'NZBgeek')
        self.assertEqual(indexer.base_url, 'https://api.nzbgeek.info')
        self.assertTrue(indexer.enabled)

        fetched = Indexers.get_one(indexer.id)
        self.assertEqual(fetched.get_data(), indexer.get_data())

    def test_add_normalises_base_url(self):
        indexer = Indexers.add('Local', 'api.example.com/', 'key')
        self.assertEqual(indexer.base_url, 'http://api.example.com')

    def test_add_missing_required_field_raises(self):
        with self.assertRaises(KeyNotFound):
            Indexers.add('', 'https://api.example.com', 'key')

    def test_get_all_ordered_by_title(self):
        Indexers.add('Zeta', 'https://z.example.com', 'k')
        Indexers.add('Alpha', 'https://a.example.com', 'k')
        titles = [i['title'] for i in Indexers.get_all()]
        self.assertEqual(titles, ['Alpha', 'Zeta'])

    def test_get_enabled_excludes_disabled(self):
        Indexers.add('On', 'https://on.example.com', 'k', enabled=True)
        Indexers.add('Off', 'https://off.example.com', 'k', enabled=False)
        enabled_titles = [i.title for i in Indexers.get_enabled()]
        self.assertEqual(enabled_titles, ['On'])

    def test_get_one_missing_raises(self):
        with self.assertRaises(IndexerNotFound):
            Indexers.get_one(999)

    def test_update(self):
        indexer = Indexers.add('Old', 'https://old.example.com', 'k')
        indexer.update({
            'title': 'New',
            'base_url': 'https://new.example.com',
            'api_key': 'k2',
            'enabled': False
        })
        self.assertEqual(indexer.title, 'New')
        self.assertFalse(indexer.enabled)
        self.assertEqual(Indexers.get_one(indexer.id).title, 'New')

    def test_delete(self):
        indexer = Indexers.add('Gone', 'https://gone.example.com', 'k')
        indexer.delete()
        with self.assertRaises(IndexerNotFound):
            Indexers.get_one(indexer.id)

    def test_find_by_link_matches_enabled_indexer(self):
        Indexers.add('NZBgeek', 'https://api.nzbgeek.info', 'key')
        found = Indexers.find_by_link(
            'https://api.nzbgeek.info/api?t=get&id=1&apikey=key'
        )
        self.assertIsNotNone(found)
        self.assertEqual(found.title, 'NZBgeek')

    def test_find_by_link_ignores_disabled_indexer(self):
        Indexers.add('Off', 'https://off.example.com', 'k', enabled=False)
        self.assertIsNone(
            Indexers.find_by_link('https://off.example.com/api?t=get&id=1')
        )

    def test_find_by_link_no_match(self):
        Indexers.add('NZBgeek', 'https://api.nzbgeek.info', 'key')
        self.assertIsNone(
            Indexers.find_by_link('https://getcomics.org/some-article')
        )


# =====================
# search_indexer()
# =====================
class _FakeAsyncSession:
    def __init__(self, body: str) -> None:
        self._body = body
        self.calls = []

    async def get_text(self, url, params={}, headers={}, quiet_fail=False):
        self.calls.append(url)
        return self._body


def _fake_indexer() -> Indexer:
    indexer = Indexer.__new__(Indexer)
    indexer._id = 1
    indexer._title = 'NZBgeek'
    indexer._base_url = 'https://api.nzbgeek.info'
    indexer._api_key = 'key123'
    indexer._enabled = True
    return indexer


class search_indexer_parsing(unittest.IsolatedAsyncioTestCase):
    async def test_parses_list_of_items(self):
        body = (
            '{"channel": {"item": ['
            '{"title": "Batman (2020) 001", '
            '"link": "https://api.nzbgeek.info/get/1"},'
            '{"title": "Batman (2020) 002", '
            '"enclosure": {"@attributes": {"url": "https://api.nzbgeek.info/get/2"}}}'
            ']}}'
        )
        session = _FakeAsyncSession(body)
        results = await search_indexer(session, _fake_indexer(), 'Batman')

        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]['link'], 'https://api.nzbgeek.info/get/1')
        self.assertEqual(results[0]['series'], 'Batman')
        self.assertEqual(results[0]['issue_number'], 1.0)
        self.assertEqual(results[0]['source'], 'NZBgeek')
        self.assertEqual(results[1]['link'], 'https://api.nzbgeek.info/get/2')

    async def test_single_result_dict_quirk_is_normalised(self):
        # Newznab's well-known quirk: `item` is a bare object, not a
        # one-element list, when there's exactly one result.
        body = (
            '{"channel": {"item": '
            '{"title": "Batman (2020) 001", "link": "https://idx/get/1"}'
            '}}'
        )
        session = _FakeAsyncSession(body)
        results = await search_indexer(session, _fake_indexer(), 'Batman')
        self.assertEqual(len(results), 1)

    async def test_empty_body_returns_empty(self):
        session = _FakeAsyncSession('')
        results = await search_indexer(session, _fake_indexer(), 'Batman')
        self.assertEqual(results, [])

    async def test_non_json_body_returns_empty(self):
        session = _FakeAsyncSession('<html>not json</html>')
        results = await search_indexer(session, _fake_indexer(), 'Batman')
        self.assertEqual(results, [])

    async def test_error_response_returns_empty(self):
        body = '{"error": {"code": "100", "description": "Incorrect API key"}}'
        session = _FakeAsyncSession(body)
        results = await search_indexer(session, _fake_indexer(), 'Batman')
        self.assertEqual(results, [])

    async def test_item_without_title_or_link_is_skipped(self):
        body = '{"channel": {"item": [{"title": "No link here"}]}}'
        session = _FakeAsyncSession(body)
        results = await search_indexer(session, _fake_indexer(), 'Batman')
        self.assertEqual(results, [])

    # -- kapowarr/t-024: malformed/unexpected response shapes must return
    # [] rather than raise, same as the non-JSON/error-key cases above --
    # `SearchIndexers.search()` awaits every indexer through a plain
    # `asyncio.gather()` with no `return_exceptions=True`, so a raised
    # exception here would fail the whole combined search, not just this
    # one indexer.
    async def test_top_level_non_dict_returns_empty(self):
        session = _FakeAsyncSession('[1, 2, 3]')
        results = await search_indexer(session, _fake_indexer(), 'Batman')
        self.assertEqual(results, [])

    async def test_channel_is_null_returns_empty(self):
        session = _FakeAsyncSession('{"channel": null}')
        results = await search_indexer(session, _fake_indexer(), 'Batman')
        self.assertEqual(results, [])

    async def test_channel_is_not_a_dict_returns_empty(self):
        session = _FakeAsyncSession('{"channel": "unexpected"}')
        results = await search_indexer(session, _fake_indexer(), 'Batman')
        self.assertEqual(results, [])

    async def test_item_is_not_a_list_or_dict_returns_empty(self):
        session = _FakeAsyncSession('{"channel": {"item": "unexpected"}}')
        results = await search_indexer(session, _fake_indexer(), 'Batman')
        self.assertEqual(results, [])

    async def test_non_dict_item_in_list_is_skipped(self):
        body = (
            '{"channel": {"item": ['
            '"not-a-dict",'
            '{"title": "Batman (2020) 001", "link": "https://idx/get/1"}'
            ']}}'
        )
        session = _FakeAsyncSession(body)
        results = await search_indexer(session, _fake_indexer(), 'Batman')
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['link'], 'https://idx/get/1')


# =====================
# create_nzb_download()
# =====================
class _FakeResponse:
    def __init__(self, ok=True, headers=None):
        self.ok = ok
        self.headers = headers or {}


class _FakeGetCM:
    def __init__(self, response=None, raise_error=False):
        self._response = response
        self._raise_error = raise_error

    async def __aenter__(self):
        if self._raise_error:
            raise ClientError()
        return self._response

    async def __aexit__(self, *a):
        return False


class _FakeResolveSession:
    def __init__(self, response=None, raise_error=False):
        self._response = response
        self._raise_error = raise_error

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def get(self, link):
        return _FakeGetCM(self._response, self._raise_error)


def _volume_mock(title='Batman', volume_number=1) -> MagicMock:
    volume = MagicMock()
    volume.get_data.return_value = VolumeData(
        id=1, comicvine_id=1, title=title, alt_title=None,
        year=2020, volume_number=volume_number, description='',
        site_url='', publisher=None, monitored=True,
        monitor_new_issues=True, root_folder=1, folder='/Batman',
        custom_folder=False, special_version=SpecialVersion.NORMAL,
        special_version_locked=False, last_cv_fetch=0
    )
    volume.get_issues.return_value = []
    return volume


class create_nzb_download_link_resolution(unittest.IsolatedAsyncioTestCase):
    async def test_link_broken_on_client_error(self):
        with patch.object(
            indexers_module, 'AsyncSession',
            return_value=_FakeResolveSession(raise_error=True)
        ), patch.object(
            indexers_module.Indexers, 'find_by_link', return_value=None
        ):
            with self.assertRaises(EnqueuingDownloadFailure) as ctx:
                await create_nzb_download(
                    'https://idx/get/1', 1, None, force_match=True
                )
        self.assertEqual(
            ctx.exception.reason, EnqueuingDownloadFailureReason.LINK_BROKEN
        )

    async def test_link_broken_on_non_ok_response(self):
        with patch.object(
            indexers_module, 'AsyncSession',
            return_value=_FakeResolveSession(_FakeResponse(ok=False))
        ), patch.object(
            indexers_module.Indexers, 'find_by_link', return_value=None
        ):
            with self.assertRaises(EnqueuingDownloadFailure) as ctx:
                await create_nzb_download(
                    'https://idx/get/1', 1, None, force_match=True
                )
        self.assertEqual(
            ctx.exception.reason, EnqueuingDownloadFailureReason.LINK_BROKEN
        )

    async def test_force_match_skips_matching_and_builds_download(self):
        response = _FakeResponse(headers={
            'Content-Disposition': 'attachment; filename="Batman (2020) 001"'
        })
        fake_download = MagicMock()
        with patch.object(
            indexers_module, 'AsyncSession',
            return_value=_FakeResolveSession(response)
        ), patch.object(
            indexers_module, 'Volume', return_value=_volume_mock()
        ), patch.object(
            indexers_module, 'Indexers'
        ) as indexers_cls, patch.object(
            indexers_module, 'NZBDownload', return_value=fake_download
        ) as nzb_cls:
            indexers_cls.find_by_link.return_value = _fake_indexer()

            result = await create_nzb_download(
                'https://api.nzbgeek.info/get/1', 1, None, force_match=True
            )

        self.assertIs(result, fake_download)
        nzb_cls.assert_called_once()
        kwargs = nzb_cls.call_args.kwargs
        self.assertEqual(kwargs['download_link'], 'https://api.nzbgeek.info/get/1')
        self.assertEqual(kwargs['volume_id'], 1)
        self.assertEqual(kwargs['covered_issues'], 1.0)
        self.assertEqual(kwargs['source_type'], DownloadSource.USENET_INDEXER)
        self.assertEqual(kwargs['source_name'], 'NZBgeek')
        self.assertEqual(kwargs['web_title'], 'Batman (2020) 001')
        self.assertTrue(kwargs['forced_match'])

    async def test_no_match_raises_when_not_forced(self):
        response = _FakeResponse(headers={
            'Content-Disposition': 'attachment; filename="Spider-Man 001.nzb"'
        })
        with patch.object(
            indexers_module, 'AsyncSession',
            return_value=_FakeResolveSession(response)
        ), patch.object(
            indexers_module, 'Volume', return_value=_volume_mock(title='Batman')
        ), patch.object(
            indexers_module.Indexers, 'find_by_link', return_value=None
        ), patch.object(
            indexers_module, 'check_search_result_match',
            return_value={'match': False, 'match_issue': "Titles don't match"}
        ):
            with self.assertRaises(EnqueuingDownloadFailure) as ctx:
                await create_nzb_download(
                    'https://api.nzbgeek.info/get/1', 1, None, force_match=False
                )
        self.assertEqual(
            ctx.exception.reason, EnqueuingDownloadFailureReason.NO_MATCHES
        )

    async def test_match_builds_download_when_not_forced(self):
        response = _FakeResponse(headers={
            'Content-Disposition': 'attachment; filename="Batman (2020) 001"'
        })
        fake_download = MagicMock()
        with patch.object(
            indexers_module, 'AsyncSession',
            return_value=_FakeResolveSession(response)
        ), patch.object(
            indexers_module, 'Volume', return_value=_volume_mock()
        ), patch.object(
            indexers_module, 'check_search_result_match',
            return_value={'match': True, 'match_issue': None}
        ), patch.object(
            indexers_module, 'Indexers'
        ) as indexers_cls, patch.object(
            indexers_module, 'NZBDownload', return_value=fake_download
        ) as nzb_cls:
            indexers_cls.find_by_link.return_value = None

            result = await create_nzb_download(
                'https://api.nzbgeek.info/get/1', 1, None, force_match=False
            )

        self.assertIs(result, fake_download)
        kwargs = nzb_cls.call_args.kwargs
        self.assertEqual(kwargs['source_name'], 'Usenet indexer')

    async def test_missing_content_disposition_falls_back_to_url_basename(self):
        response = _FakeResponse(headers={})
        fake_download = MagicMock()
        with patch.object(
            indexers_module, 'AsyncSession',
            return_value=_FakeResolveSession(response)
        ), patch.object(
            indexers_module, 'Volume', return_value=_volume_mock()
        ), patch.object(
            indexers_module.Indexers, 'find_by_link', return_value=None
        ), patch.object(
            indexers_module, 'NZBDownload', return_value=fake_download
        ) as nzb_cls:
            await create_nzb_download(
                'https://api.nzbgeek.info/get/1', 1, None, force_match=True
            )
        kwargs = nzb_cls.call_args.kwargs
        self.assertEqual(kwargs['web_title'], '1')


if __name__ == '__main__':
    unittest.main()
