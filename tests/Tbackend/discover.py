import unittest
from unittest.mock import AsyncMock, patch

from bs4 import BeautifulSoup

from backend.base.definitions import DiscoverSource
from backend.features import discover as discover_feature_module
from backend.features.discover import (DiscoverSources, GetComicsDiscover,
                                       get_discover_feed,
                                       match_discover_items_to_library)
from backend.implementations.discover import (MAX_DISCOVER_PAGES,
                                              _get_discover_articles,
                                              fetch_getcomics_discover_page)


# =====================
# _get_discover_articles()
# =====================
class get_discover_articles(unittest.TestCase):
    def test_extracts_link_title_and_cover(self):
        html = """
        <article class="post">
            <h1 class="post-title"><a href="http://x/batman-1">Batman #123 (2024)</a></h1>
            <img src="http://x/batman-1.jpg">
        </article>
        """
        soup = BeautifulSoup(html, "html.parser")
        articles = _get_discover_articles(soup)

        self.assertEqual(len(articles), 1)
        link, title, cover = articles[0]
        self.assertEqual(link, 'http://x/batman-1')
        self.assertEqual(title, 'Batman #123 (2024)')
        self.assertEqual(cover, 'http://x/batman-1.jpg')

    def test_prefers_lazy_load_src_over_placeholder_src(self):
        html = """
        <article class="post">
            <h1 class="post-title"><a href="http://x/1">Title</a></h1>
            <img src="placeholder.gif" data-src="http://x/real.jpg">
        </article>
        """
        soup = BeautifulSoup(html, "html.parser")
        _, _, cover = _get_discover_articles(soup)[0]
        self.assertEqual(cover, 'http://x/real.jpg')

    def test_falls_back_to_data_lazy_src(self):
        html = """
        <article class="post">
            <h1 class="post-title"><a href="http://x/1">Title</a></h1>
            <img src="placeholder.gif" data-lazy-src="http://x/lazy.jpg">
        </article>
        """
        soup = BeautifulSoup(html, "html.parser")
        _, _, cover = _get_discover_articles(soup)[0]
        self.assertEqual(cover, 'http://x/lazy.jpg')

    def test_no_image_gives_none_cover(self):
        html = """
        <article class="post">
            <h1 class="post-title"><a href="http://x/1">Title</a></h1>
        </article>
        """
        soup = BeautifulSoup(html, "html.parser")
        _, _, cover = _get_discover_articles(soup)[0]
        self.assertIsNone(cover)

    def test_article_without_title_is_skipped(self):
        html = '<article class="post"></article>'
        soup = BeautifulSoup(html, "html.parser")
        self.assertEqual(_get_discover_articles(soup), [])

    def test_title_without_anchor_is_skipped(self):
        html = """
        <article class="post">
            <h1 class="post-title">No link here</h1>
        </article>
        """
        soup = BeautifulSoup(html, "html.parser")
        self.assertEqual(_get_discover_articles(soup), [])

    def test_multiple_articles_in_page_order(self):
        html = """
        <article class="post">
            <h1 class="post-title"><a href="http://x/1">First</a></h1>
        </article>
        <article class="post">
            <h1 class="post-title"><a href="http://x/2">Second</a></h1>
        </article>
        """
        soup = BeautifulSoup(html, "html.parser")
        articles = _get_discover_articles(soup)
        self.assertEqual([a[1] for a in articles], ['First', 'Second'])


# =====================
# fetch_getcomics_discover_page(), with a fake AsyncSession
# =====================
class _FakeAsyncSession:
    """Returns a canned response body for any URL, mirroring
    `weekly_releases.py`'s test suite's `_FakeAsyncSession`.
    """

    def __init__(self, bodies) -> None:
        self._bodies = bodies
        self.calls = []

    async def get_text(self, url, params={}, headers={}, quiet_fail=False):
        self.calls.append(url)
        return self._bodies.get(url, '')


LISTING_HTML = """
<article class="post">
    <h1 class="post-title"><a href="http://getcomics.example/batman-123">Batman 001 (2024)</a></h1>
    <img src="http://getcomics.example/batman-123.jpg">
</article>
<article class="post">
    <h1 class="post-title"><a href="http://getcomics.example/daredevil-10">Daredevil #10</a></h1>
</article>
<span class="page-numbers">1</span>
<a class="page-numbers" href="?page=2">2</a>
<span class="page-numbers">3</span>
"""


class getcomics_discover_page_fetch(unittest.IsolatedAsyncioTestCase):
    async def test_parses_items_and_max_page(self):
        session = _FakeAsyncSession({
            'https://getcomics.org': LISTING_HTML
        })

        items, max_page = await fetch_getcomics_discover_page(session, page=1)

        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]['series'], 'Batman')
        self.assertEqual(items[0]['link'], 'http://getcomics.example/batman-123')
        self.assertEqual(items[0]['cover'], 'http://getcomics.example/batman-123.jpg')
        self.assertEqual(items[0]['source'], 'GetComics')
        self.assertIsNone(items[1]['cover'])
        self.assertEqual(max_page, 3)

    async def test_requests_paged_url_for_page_above_one(self):
        session = _FakeAsyncSession({
            'https://getcomics.org/page/2': LISTING_HTML
        })

        await fetch_getcomics_discover_page(session, page=2)

        self.assertIn('https://getcomics.org/page/2', session.calls)

    async def test_page_below_one_is_clamped_to_first_page(self):
        session = _FakeAsyncSession({
            'https://getcomics.org': LISTING_HTML
        })

        await fetch_getcomics_discover_page(session, page=0)

        self.assertIn('https://getcomics.org', session.calls)

    async def test_empty_response_returns_empty(self):
        session = _FakeAsyncSession({})

        items, max_page = await fetch_getcomics_discover_page(session, page=1)

        self.assertEqual(items, [])
        self.assertEqual(max_page, 1)

    async def test_max_page_is_capped(self):
        html = '<span class="page-numbers">999</span>'
        session = _FakeAsyncSession({'https://getcomics.org': html})

        _, max_page = await fetch_getcomics_discover_page(session, page=1)

        self.assertEqual(max_page, MAX_DISCOVER_PAGES)


# =====================
# match_discover_items_to_library()
# =====================
def _item(series, year=None, link='http://x/1'):
    return {
        'series': series,
        'year': year,
        'volume_number': None,
        'special_version': None,
        'issue_number': None,
        'annual': False,
        'link': link,
        'display_title': series,
        'source': 'GetComics',
        'cover': None
    }


def _volume(id, title):
    return {'id': id, 'title': title}


class discover_to_library_matching(unittest.TestCase):
    def test_matched_item_carries_volume_id_and_title(self):
        items = [_item('Batman')]
        volumes = [_volume(1, 'Batman')]

        matches = match_discover_items_to_library(items, volumes)

        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]['volume_id'], 1)
        self.assertEqual(matches[0]['volume_title'], 'Batman')
        self.assertEqual(matches[0]['series'], 'Batman')

    def test_unmatched_item_has_none_volume_fields(self):
        items = [_item('Batman')]
        volumes = [_volume(1, 'Daredevil')]

        matches = match_discover_items_to_library(items, volumes)

        self.assertEqual(len(matches), 1)
        self.assertIsNone(matches[0]['volume_id'])
        self.assertIsNone(matches[0]['volume_title'])

    def test_matches_unmonitored_volumes_too(self):
        # Discover checks the whole library. Even an unmonitored volume is
        # already owned and therefore should later be suppressed by the feed.
        items = [_item('Batman')]
        volumes = [{'id': 1, 'title': 'Batman', 'monitored': False}]

        matches = match_discover_items_to_library(items, volumes)

        self.assertEqual(matches[0]['volume_id'], 1)

    def test_every_item_gets_one_output_entry_regardless_of_match(self):
        items = [_item('Batman'), _item('Nothing In Library', link='http://x/2')]
        volumes = [_volume(1, 'Batman')]

        matches = match_discover_items_to_library(items, volumes)

        self.assertEqual(len(matches), 2)
        self.assertIsNone(matches[1]['volume_id'])


# =====================
# DiscoverSources registry
# =====================
class discover_sources_registry(unittest.TestCase):
    def test_getcomics_is_registered_by_default(self):
        self.assertIn(GetComicsDiscover, DiscoverSources.sources)

    def test_get_active_returns_instances(self):
        active = DiscoverSources.get_active()
        self.assertTrue(any(isinstance(s, GetComicsDiscover) for s in active))

    def test_register_adds_a_new_source(self):
        class _DummySource(DiscoverSource):
            async def fetch(self, session, page=1):
                return [], 1

        original_sources = list(DiscoverSources.sources)
        try:
            DiscoverSources.register(_DummySource)
            self.assertIn(_DummySource, DiscoverSources.sources)
        finally:
            DiscoverSources.sources = original_sources


# =====================
# get_discover_feed()
# =====================
class discover_feed(unittest.TestCase):
    def test_suppresses_items_already_in_library(self):
        items = [
            _item('Batman', link='http://x/batman'),
            _item('Something New', link='http://x/new')
        ]
        with patch.object(
            discover_feature_module, '_fetch_discover_page',
            new=AsyncMock(return_value=(items, 4))
        ), patch.object(
            discover_feature_module.Library, 'get_public_volumes',
            return_value=[_volume(1, 'Batman')]
        ):
            matches, max_page = get_discover_feed(page=2)

        self.assertEqual(max_page, 4)
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]['series'], 'Something New')
        self.assertIsNone(matches[0]['volume_id'])

    def test_empty_fetch_returns_no_matches(self):
        with patch.object(
            discover_feature_module, '_fetch_discover_page',
            new=AsyncMock(return_value=([], 1))
        ), patch.object(
            discover_feature_module.Library, 'get_public_volumes',
            return_value=[]
        ):
            matches, max_page = get_discover_feed()

        self.assertEqual(matches, [])
        self.assertEqual(max_page, 1)


if __name__ == '__main__':
    unittest.main()
