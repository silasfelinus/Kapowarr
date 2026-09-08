"""Tests for `backend.implementations.internet_archive`.

Covers the JSON parsing (search + per-item metadata), the Discover browse
path (unfiltered), the real DIRECT acquisition path (filtered), and -- the
one thing that actually matters for kapowarr/t-041 -- an explicit boundary
test proving a controlled-lending/access-restricted item can never come
back from `search_internet_archive()` with a download link, regardless of
what else about it looks eligible.
"""

import unittest
from json import dumps

from backend.base.definitions import Constants, DownloadType
from backend.features.discover import DiscoverSources, InternetArchiveDiscover
from backend.features.search import SearchInternetArchive, SearchSources
from backend.implementations.internet_archive import (
    _build_search_query, _is_publicly_downloadable,
    _pick_downloadable_filename, fetch_internet_archive_discover_page,
    search_internet_archive)


# =====================
# _build_search_query()
# =====================
class build_search_query(unittest.TestCase):
    def test_restricts_to_texts_mediatype(self):
        self.assertIn('mediatype:(texts)', _build_search_query('batman'))

    def test_includes_the_query_term(self):
        self.assertIn('(batman)', _build_search_query('batman'))

    def test_empty_query_still_restricts_to_texts(self):
        self.assertEqual(_build_search_query(''), 'mediatype:(texts)')


# =====================
# _is_publicly_downloadable()
# =====================
class is_publicly_downloadable(unittest.TestCase):
    def test_no_metadata_block_is_not_downloadable(self):
        self.assertFalse(_is_publicly_downloadable({}))

    def test_explicit_access_restricted_flag_excludes_it(self):
        metadata = {'metadata': {'access-restricted-item': 'true'}}
        self.assertFalse(_is_publicly_downloadable(metadata))

    def test_access_restricted_flag_is_case_insensitive(self):
        metadata = {'metadata': {'access-restricted-item': 'True'}}
        self.assertFalse(_is_publicly_downloadable(metadata))

    def test_inlibrary_collection_excludes_it(self):
        metadata = {'metadata': {'collection': ['inlibrary', 'americana']}}
        self.assertFalse(_is_publicly_downloadable(metadata))

    def test_lendinglibrary_collection_excludes_it(self):
        metadata = {'metadata': {'collection': 'lendinglibrary'}}
        self.assertFalse(_is_publicly_downloadable(metadata))

    def test_printdisabled_collection_excludes_it(self):
        metadata = {'metadata': {'collection': ['printdisabled']}}
        self.assertFalse(_is_publicly_downloadable(metadata))

    def test_open_item_with_no_restriction_signals_is_downloadable(self):
        metadata = {
            'metadata': {
                'access-restricted-item': 'false',
                'collection': ['comics', 'americana']
            }
        }
        self.assertTrue(_is_publicly_downloadable(metadata))

    def test_missing_flag_and_ordinary_collection_is_downloadable(self):
        metadata = {'metadata': {'collection': ['comics']}}
        self.assertTrue(_is_publicly_downloadable(metadata))


# =====================
# _pick_downloadable_filename()
# =====================
class pick_downloadable_filename(unittest.TestCase):
    def test_prefers_cbz_over_pdf(self):
        metadata = {'files': [
            {'name': 'batman-001.pdf'},
            {'name': 'batman-001.cbz'}
        ]}
        self.assertEqual(_pick_downloadable_filename(metadata), 'batman-001.cbz')

    def test_falls_back_to_pdf_when_no_cbz_or_cbr(self):
        metadata = {'files': [
            {'name': 'batman-001_meta.xml'},
            {'name': 'batman-001.pdf'}
        ]}
        self.assertEqual(_pick_downloadable_filename(metadata), 'batman-001.pdf')

    def test_ignores_unrecognised_extensions(self):
        metadata = {'files': [
            {'name': 'batman-001_djvu.txt'},
            {'name': 'batman-001_meta.xml'}
        ]}
        self.assertIsNone(_pick_downloadable_filename(metadata))

    def test_ignores_files_reported_with_a_path_separator(self):
        metadata = {'files': [{'name': 'sub/batman-001.cbz'}]}
        self.assertIsNone(_pick_downloadable_filename(metadata))

    def test_no_files_list_returns_none(self):
        self.assertIsNone(_pick_downloadable_filename({}))


# =====================
# fetch_internet_archive_discover_page(), with a fake AsyncSession
# =====================
class _FakeInternetArchiveSearchSession:
    """Returns a canned `advancedsearch.php` JSON body for any URL, and
    records each call's (url, params) so the query/page params can be
    asserted on.
    """

    def __init__(self, body: str) -> None:
        self._body = body
        self.calls = []

    async def get_text(self, url, params={}, headers={}, quiet_fail=False):
        self.calls.append((url, dict(params)))
        return self._body


def _search_response(docs, num_found=None):
    return dumps({
        'response': {
            'numFound': num_found if num_found is not None else len(docs),
            'docs': docs
        }
    })


class internet_archive_discover_page_fetch(unittest.IsolatedAsyncioTestCase):
    async def test_parses_items_and_max_page(self):
        body = _search_response(
            [
                {'identifier': 'batman-001', 'title': 'Batman 001 (2024)'},
                {'identifier': 'daredevil-010', 'title': 'Daredevil #10'}
            ],
            num_found=120
        )
        session = _FakeInternetArchiveSearchSession(body)

        items, max_page = await fetch_internet_archive_discover_page(session, page=1)

        self.assertEqual(len(items), 2)
        self.assertEqual(
            items[0]['link'], f'{Constants.IA_SITE_URL}/details/batman-001'
        )
        self.assertEqual(items[0]['source'], Constants.IA_SOURCE_TERM)
        self.assertIsNotNone(items[0]['cover'])
        # numFound=120 at 50/page -> 3 pages, well under the cap.
        self.assertEqual(max_page, 3)

    async def test_page_count_is_capped(self):
        body = _search_response([], num_found=100000)
        session = _FakeInternetArchiveSearchSession(body)

        _, max_page = await fetch_internet_archive_discover_page(session, page=1)

        self.assertEqual(max_page, 10)

    async def test_empty_response_returns_no_items(self):
        session = _FakeInternetArchiveSearchSession('')

        items, max_page = await fetch_internet_archive_discover_page(session, page=1)

        self.assertEqual(items, [])
        self.assertEqual(max_page, 1)

    async def test_unparsable_json_degrades_to_empty(self):
        session = _FakeInternetArchiveSearchSession('not json')

        items, max_page = await fetch_internet_archive_discover_page(session, page=1)

        self.assertEqual(items, [])
        self.assertEqual(max_page, 1)

    async def test_never_filters_by_restriction(self):
        # Discover is browse/link-out only -- an access-restricted item is
        # still a valid Discover entry. This module never even looks at
        # per-item metadata for the Discover path, so there is nothing to
        # assert on *besides* the item showing up unfiltered.
        body = _search_response(
            [{'identifier': 'restricted-item', 'title': 'Restricted Comic (2024)'}]
        )
        session = _FakeInternetArchiveSearchSession(body)

        items, _ = await fetch_internet_archive_discover_page(session, page=1)

        self.assertEqual(len(items), 1)


# =====================
# search_internet_archive(), with a fake AsyncSession
# =====================
class _FakeInternetArchiveSearchAndMetadataSession:
    """Routes `advancedsearch.php` to a canned search body, and
    `/metadata/<identifier>` to a per-identifier canned metadata body.
    """

    def __init__(self, search_body: str, metadata_bodies: dict) -> None:
        self._search_body = search_body
        self._metadata_bodies = metadata_bodies
        self.calls = []

    async def get_text(self, url, params={}, headers={}, quiet_fail=False):
        self.calls.append(url)
        if url.endswith('/advancedsearch.php'):
            return self._search_body

        for identifier, body in self._metadata_bodies.items():
            if url.endswith(f'/metadata/{identifier}'):
                return body

        return ''


class internet_archive_search(unittest.IsolatedAsyncioTestCase):
    async def test_empty_query_returns_no_results(self):
        session = _FakeInternetArchiveSearchAndMetadataSession('', {})
        self.assertEqual(await search_internet_archive(session, ''), [])

    async def test_eligible_item_gets_a_real_download_link(self):
        search_body = _search_response(
            [{'identifier': 'open-comic', 'title': 'Open Comic 001 (2024)'}]
        )
        metadata_body = dumps({
            'metadata': {
                'access-restricted-item': 'false',
                'collection': ['comics']
            },
            'files': [{'name': 'open-comic-001.cbz'}]
        })
        session = _FakeInternetArchiveSearchAndMetadataSession(
            search_body, {'open-comic': metadata_body}
        )

        results = await search_internet_archive(session, 'open comic')

        self.assertEqual(len(results), 1)
        self.assertEqual(
            results[0]['link'],
            f'{Constants.IA_SITE_URL}/download/open-comic/open-comic-001.cbz'
        )
        self.assertEqual(results[0]['source'], Constants.IA_SOURCE_TERM)

    async def test_access_restricted_item_is_never_returned(self):
        """The boundary test: kapowarr/t-041's whole point. A
        controlled-lending item must never come back from the real
        acquisition search, no matter how clean everything else about it
        looks (a comic-shaped file genuinely present in its file list
        included) -- only `_is_publicly_downloadable()` decides this, and
        it must win.
        """
        search_body = _search_response(
            [{'identifier': 'lending-comic', 'title': 'Lending Comic 001 (2024)'}]
        )
        metadata_body = dumps({
            'metadata': {
                'access-restricted-item': 'true',
                'collection': ['inlibrary', 'lendinglibrary']
            },
            'files': [{'name': 'lending-comic-001.cbz'}]
        })
        session = _FakeInternetArchiveSearchAndMetadataSession(
            search_body, {'lending-comic': metadata_body}
        )

        results = await search_internet_archive(session, 'lending comic')

        self.assertEqual(results, [])

    async def test_item_with_no_recognised_file_is_excluded(self):
        search_body = _search_response(
            [{'identifier': 'text-only', 'title': 'Text Only Item (2024)'}]
        )
        metadata_body = dumps({
            'metadata': {'access-restricted-item': 'false'},
            'files': [{'name': 'text-only_djvu.txt'}]
        })
        session = _FakeInternetArchiveSearchAndMetadataSession(
            search_body, {'text-only': metadata_body}
        )

        results = await search_internet_archive(session, 'text only')

        self.assertEqual(results, [])

    async def test_item_whose_metadata_could_not_be_fetched_is_excluded(self):
        search_body = _search_response(
            [{'identifier': 'unreachable', 'title': 'Unreachable Item (2024)'}]
        )
        session = _FakeInternetArchiveSearchAndMetadataSession(search_body, {})

        results = await search_internet_archive(session, 'unreachable')

        self.assertEqual(results, [])

    async def test_mixed_results_keep_only_the_eligible_item(self):
        search_body = _search_response([
            {'identifier': 'open-comic', 'title': 'Open Comic 001 (2024)'},
            {'identifier': 'lending-comic', 'title': 'Lending Comic 001 (2024)'}
        ])
        session = _FakeInternetArchiveSearchAndMetadataSession(search_body, {
            'open-comic': dumps({
                'metadata': {'access-restricted-item': 'false'},
                'files': [{'name': 'open-comic-001.cbz'}]
            }),
            'lending-comic': dumps({
                'metadata': {'access-restricted-item': 'true'},
                'files': [{'name': 'lending-comic-001.cbz'}]
            })
        })

        results = await search_internet_archive(session, 'comic')

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['display_title'], 'Open Comic 001 (2024)')


# =====================
# Registry wiring
# =====================
class internet_archive_registry(unittest.TestCase):
    def test_discover_source_is_registered_by_default(self):
        self.assertIn(InternetArchiveDiscover, DiscoverSources.sources)

    def test_discover_source_is_active(self):
        active = DiscoverSources.get_active()
        self.assertTrue(
            any(isinstance(source, InternetArchiveDiscover) for source in active)
        )

    def test_search_source_is_registered_under_direct(self):
        self.assertIn(
            SearchInternetArchive, SearchSources.sources[DownloadType.DIRECT]
        )


if __name__ == '__main__':
    unittest.main()
