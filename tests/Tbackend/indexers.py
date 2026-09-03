import sqlite3
import unittest
from unittest.mock import MagicMock, patch

from aiohttp import ClientError

from backend.base.custom_exceptions import (EnqueuingDownloadFailure,
                                            IndexerNotFound, KeyNotFound)
from backend.base.definitions import (Constants, DownloadSource,
                                      EnqueuingDownloadFailureReason,
                                      SpecialVersion, VolumeData)
from backend.implementations import indexers as indexers_module
from backend.implementations.indexers import (
    DEFAULT_COMIC_CATEGORIES, Indexer, Indexers, _extract_item_link,
    _parse_content_disposition_filename, create_nzb_download,
    newznab_api_url, search_indexer)
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


class newznab_endpoint_formatting(unittest.TestCase):
    def test_native_host_gets_api_path(self):
        self.assertEqual(
            newznab_api_url('https://api.nzbgeek.info'),
            'https://api.nzbgeek.info/api'
        )

    def test_prowlarr_legacy_feed_is_used_as_supplied(self):
        self.assertEqual(
            newznab_api_url('https://prowlarr.example/7/api'),
            'https://prowlarr.example/7/api'
        )

    def test_prowlarr_modern_feed_is_used_as_supplied(self):
        self.assertEqual(
            newznab_api_url(
                'https://prowlarr.example/api/v1/indexer/7/newznab'
            ),
            'https://prowlarr.example/api/v1/indexer/7/newznab'
        )


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
                categories VARCHAR(255) NOT NULL DEFAULT '7030,107030',
                category_filter_enabled BOOL NOT NULL DEFAULT 0,
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

    def test_the_list_carries_every_field_the_settings_page_sends_back(self):
        """The settings page holds these rows and sends one straight back
        when its enable toggle is pressed, so a field the list leaves out
        is a field that toggle silently clears.
        """
        Indexers.add(
            'nzb.su', 'https://api.nzb.su', 'k',
            categories='107030', category_filter_enabled=True
        )

        listed = Indexers.get_all()[0]

        # Truthiness rather than identity: this harness connects without
        # the BOOL converter `db.py` registers, so a flag reads back as 1
        # here and as True in the running app.
        self.assertEqual(listed, Indexers.get_one(listed['id']).get_data())
        self.assertEqual(listed['categories'], '107030')
        self.assertTrue(listed['category_filter_enabled'])

    def test_an_indexer_added_without_categories_gets_the_default(self):
        Indexers.add('plain', 'https://plain.example.com', 'k')

        added = Indexers.get_all()[0]

        self.assertEqual(added['categories'], DEFAULT_COMIC_CATEGORIES)
        self.assertFalse(added['category_filter_enabled'])

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
        self.calls.append((url, params, quiet_fail))
        return self._body


def _fake_indexer() -> Indexer:
    indexer = Indexer.__new__(Indexer)
    indexer._id = 1
    indexer._title = 'NZBgeek'
    indexer._base_url = 'https://api.nzbgeek.info'
    indexer._api_key = 'key123'
    indexer._categories = DEFAULT_COMIC_CATEGORIES
    indexer._category_filter_enabled = False
    indexer._enabled = True
    return indexer


class search_indexer_parsing(unittest.IsolatedAsyncioTestCase):
    async def test_queries_full_prowlarr_feed_without_appending_api(self):
        indexer = _fake_indexer()
        indexer._title = 'Prowlarr NZB'
        indexer._base_url = (
            'https://prowlarr.example/api/v1/indexer/7/newznab'
        )
        body = (
            '{"channel": {"item": {'
            '"title": "Gwar - Orgasmageddon (2017) (digital-Empire)", '
            '"link": "https://prowlarr.example/download/1"}}}'
        )
        session = _FakeAsyncSession(body)

        results = await search_indexer(
            session, indexer, 'Gwar Orgasmageddon'
        )

        self.assertEqual(len(results), 1)
        self.assertEqual(
            results[0]['display_title'],
            'Gwar - Orgasmageddon (2017) (digital-Empire)'
        )
        self.assertEqual(session.calls[0][0], indexer.base_url)
        self.assertEqual(session.calls[0][1]['q'], 'Gwar Orgasmageddon')

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
    def __init__(self, ok=True, headers=None, status=None):
        self.ok = ok
        self.headers = headers or {}
        self.status = status if status is not None else (200 if ok else 404)


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
    """What a failed fetch says about the release.

    The caller blocklists `LINK_BROKEN` permanently and never asks about
    that release again, so it has to mean the release is gone -- not that
    the indexer was briefly unwell. Both used to raise it.
    """

    async def _reason_for(self, response=None, raise_error=False):
        with patch.object(
            indexers_module, 'AsyncSession',
            return_value=_FakeResolveSession(response, raise_error)
        ), patch.object(
            indexers_module.Indexers, 'find_by_link', return_value=None
        ):
            with self.assertRaises(EnqueuingDownloadFailure) as ctx:
                await create_nzb_download(
                    'https://idx/get/1', 1, None, force_match=True
                )
        return ctx.exception.reason

    async def test_a_release_the_indexer_says_is_gone_is_broken(self):
        for status in (404, 410):
            with self.subTest(status=status):
                self.assertEqual(
                    await self._reason_for(
                        _FakeResponse(ok=False, status=status)
                    ),
                    EnqueuingDownloadFailureReason.LINK_BROKEN
                )

    async def test_an_indexer_that_is_unwell_does_not_condemn_the_release(self):
        """500 while it restarts, 401 from a stale key, 503 behind a proxy.
        None of those are the release's fault, and blocklisting is forever.
        """
        for status in (500, 502, 503, 401, 403, 400):
            with self.subTest(status=status):
                self.assertEqual(
                    await self._reason_for(
                        _FakeResponse(ok=False, status=status)
                    ),
                    EnqueuingDownloadFailureReason.SOURCE_UNAVAILABLE
                )

    async def test_never_reaching_the_indexer_says_nothing_about_the_link(self):
        self.assertEqual(
            await self._reason_for(raise_error=True),
            EnqueuingDownloadFailureReason.SOURCE_UNAVAILABLE
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
        # A single indexer result was fetched successfully and refused for
        # being a different issue -- not a page that yielded no usable links,
        # which is what NO_MATCHES describes.
        self.assertEqual(
            ctx.exception.reason,
            EnqueuingDownloadFailureReason.RESULT_DOES_NOT_MATCH
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


class an_indexer_files_comics_where_it_likes(unittest.IsolatedAsyncioTestCase):
    """7030 is the standard Newznab comics category and the standard is
    only a suggestion. nzb.su files comics under its own 107030, and
    Silas, of his three: "all our nzb indexers use that category".

    Torznab feeds have had a per-indexer category since they were added.
    Newznab ones had a hardcoded 7030 and no way to say otherwise, so a
    feed poll asked nzb.su for its most recent 7030 -- which is not where
    its comics are -- and a search asked for every category at once, which
    on 2026-09-02 returned anime video offered for Afro Samurai.
    """

    async def _params_for(self, query, **fields):
        indexer = _fake_indexer()
        for name, value in fields.items():
            setattr(indexer, f'_{name}', value)
        session = _FakeAsyncSession('{"channel": {"item": []}}')

        await search_indexer(session, indexer, query)

        return session.calls[0][1]

    async def test_a_feed_poll_asks_for_the_indexers_own_categories(self):
        """It has no query doing the narrowing, so the category is the only
        thing standing between the poll and the site's recent everything.
        """
        params = await self._params_for('', categories='107030')

        self.assertEqual(params['cat'], '107030')
        self.assertNotIn('q', params)

    async def test_a_feed_poll_falls_back_when_none_was_set(self):
        params = await self._params_for('', categories='')

        self.assertEqual(params['cat'], Constants.COMIC_CATEGORY)

    async def test_a_search_is_unconfined_unless_asked(self):
        """Off by default, matching Torznab and Prowlarr's own manual
        search: the query narrows, and a category can only hide a release
        the indexer filed somewhere unexpected.
        """
        params = await self._params_for(
            'Batman', categories='107030', category_filter_enabled=False
        )

        self.assertNotIn('cat', params)
        self.assertEqual(params['q'], 'Batman')

    async def test_a_search_is_confined_when_it_is(self):
        params = await self._params_for(
            'Batman', categories='107030', category_filter_enabled=True
        )

        self.assertEqual(params['cat'], '107030')

    async def test_the_filter_with_nothing_to_filter_by_is_not_a_filter(self):
        params = await self._params_for(
            'Batman', categories='', category_filter_enabled=True
        )

        self.assertNotIn('cat', params)

    def test_the_default_asks_for_both_the_standard_and_the_common_one(self):
        """An indexer ignores category IDs it does not have, so asking for
        both costs nothing -- and picking one leaves every indexer that
        chose the other silently returning nothing.
        """
        self.assertIn(Constants.COMIC_CATEGORY, DEFAULT_COMIC_CATEGORIES)
        self.assertIn('107030', DEFAULT_COMIC_CATEGORIES)


class the_search_path_that_runs_is_the_one_that_was_fixed(
    unittest.IsolatedAsyncioTestCase
):
    """`indexers.py` shadows `indexers_core.py`'s `search_indexer` and is
    the one `search.py` imports, so a fix applied only to the core never
    runs. That has already happened twice: `create_nzb_download`'s
    blocklist classification, and the rate-limit scoping below.

    Without the scope registration a Newznab indexer's quota is keyed at
    the Prowlarr hostname, so one indexer running out silences every other
    indexer behind it.
    """

    async def test_a_newznab_search_rations_the_indexer_not_the_host(self):
        indexer = _fake_indexer()
        indexer._base_url = 'https://prowlarr.example/33/api'
        session = _FakeAsyncSession('{"channel": {"item": []}}')

        with patch.object(
            indexers_module, 'register_rate_limit_scope'
        ) as register:
            await search_indexer(session, indexer, 'Batman')

        register.assert_called_once_with('https://prowlarr.example/33/api')


class an_indexer_that_will_not_serve_is_left_alone(
    unittest.IsolatedAsyncioTestCase
):
    """NZB Planet answered HTTP 510 -- what a Newznab indexer says when the
    day's downloads are spent -- 216 times in twenty minutes on 2026-09-03,
    once for every release the sweep reached.

    Naming the status (which is why the log said so at all) was enough to
    see that the answer was never going to be about the release. Backing
    off is the same courtesy an explicit 429 already gets from the session.
    """

    async def _fetch(self, status):
        with patch.object(
            indexers_module, 'AsyncSession',
            return_value=_FakeResolveSession(
                _FakeResponse(ok=False, status=status)
            )
        ), patch.object(
            indexers_module.Indexers, 'find_by_link', return_value=None
        ), patch.object(
            indexers_module, 'note_rate_limit', return_value=900.0
        ) as noted:
            with self.assertRaises(EnqueuingDownloadFailure):
                await create_nzb_download(
                    'https://idx.example/get/1', 1, None, force_match=True
                )
        return noted

    async def test_a_refusal_puts_the_indexer_in_cooldown(self):
        for status in (510, 500, 403, 429):
            with self.subTest(status=status):
                noted = await self._fetch(status)
                noted.assert_called_once_with('https://idx.example/get/1')

    async def test_a_release_that_is_gone_says_nothing_about_the_indexer(self):
        """404 is about this release only. Standing the whole indexer down
        for one dead NZB would be the opposite mistake.
        """
        for status in (404, 410):
            with self.subTest(status=status):
                noted = await self._fetch(status)
                noted.assert_not_called()
