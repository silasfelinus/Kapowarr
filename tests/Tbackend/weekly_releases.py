import unittest

from bs4 import BeautifulSoup

from backend.implementations.weekly_releases import (
    _find_latest_weekly_release_article, _parse_weekly_release_lines,
    fetch_getcomics_weekly_releases)


# =====================
# _parse_weekly_release_lines()
# =====================
class parse_weekly_release_lines(unittest.TestCase):
    def test_parses_list_items(self):
        html = """
        <div class="entry-content">
            <ul>
                <li>Batman #123</li>
                <li>Amazing Spider-Man #45 (2024)</li>
            </ul>
        </div>
        """
        soup = BeautifulSoup(html, "html.parser")
        releases = _parse_weekly_release_lines(soup, "GetComics", "http://x/1")

        self.assertEqual(len(releases), 2)
        self.assertEqual(releases[0]['series'], 'Batman')
        self.assertEqual(releases[0]['issue_number'], '123')
        self.assertIsNone(releases[0]['year'])
        self.assertEqual(releases[0]['source'], 'GetComics')
        self.assertEqual(releases[0]['link'], 'http://x/1')

        self.assertEqual(releases[1]['series'], 'Amazing Spider-Man')
        self.assertEqual(releases[1]['issue_number'], '45')
        self.assertEqual(releases[1]['year'], 2024)

    def test_falls_back_to_paragraph_lines_when_no_list_items(self):
        html = """
        <div class="entry-content">
            <p>Batman #123
            Daredevil #10</p>
        </div>
        """
        soup = BeautifulSoup(html, "html.parser")
        releases = _parse_weekly_release_lines(soup, "GetComics", "http://x/1")

        self.assertEqual(len(releases), 2)
        self.assertEqual(
            {r['series'] for r in releases}, {'Batman', 'Daredevil'}
        )

    def test_non_release_lines_are_skipped(self):
        html = """
        <div class="entry-content">
            <ul>
                <li>Welcome to this week's releases!</li>
                <li>Batman #123</li>
                <li>Thanks for reading</li>
            </ul>
        </div>
        """
        soup = BeautifulSoup(html, "html.parser")
        releases = _parse_weekly_release_lines(soup, "GetComics", "http://x/1")

        self.assertEqual(len(releases), 1)
        self.assertEqual(releases[0]['series'], 'Batman')

    def test_duplicate_release_lines_are_deduped(self):
        html = """
        <div class="entry-content">
            <ul>
                <li>Batman #123</li>
                <li>Batman #123</li>
            </ul>
        </div>
        """
        soup = BeautifulSoup(html, "html.parser")
        releases = _parse_weekly_release_lines(soup, "GetComics", "http://x/1")

        self.assertEqual(len(releases), 1)

    def test_no_entry_content_falls_back_to_whole_soup(self):
        html = "<html><body><ul><li>Batman #1</li></ul></body></html>"
        soup = BeautifulSoup(html, "html.parser")
        releases = _parse_weekly_release_lines(soup, "GetComics", "http://x/1")

        self.assertEqual(len(releases), 1)
        self.assertEqual(releases[0]['series'], 'Batman')

    def test_no_matching_lines_returns_empty(self):
        html = """
        <div class="entry-content">
            <ul>
                <li>Nothing to see here</li>
            </ul>
        </div>
        """
        soup = BeautifulSoup(html, "html.parser")
        releases = _parse_weekly_release_lines(soup, "GetComics", "http://x/1")

        self.assertEqual(releases, [])

    def test_non_numeric_issue_number_is_kept(self):
        html = """
        <div class="entry-content">
            <ul>
                <li>X-Men #1AU</li>
            </ul>
        </div>
        """
        soup = BeautifulSoup(html, "html.parser")
        releases = _parse_weekly_release_lines(soup, "GetComics", "http://x/1")

        self.assertEqual(len(releases), 1)
        self.assertEqual(releases[0]['issue_number'], '1AU')


# =====================
# Fetch helpers, with a fake AsyncSession
# =====================
class _FakeAsyncSession:
    """Returns canned response bodies from a dict keyed by URL, mirroring
    `indexers.py` test suite's `_FakeAsyncSession` but supporting multiple
    distinct URLs since fetching weekly releases is a two-step process
    (listing page, then the latest article on it).
    """

    def __init__(self, bodies) -> None:
        self._bodies = bodies
        self.calls = []

    async def get_text(self, url, params={}, headers={}, quiet_fail=False):
        self.calls.append(url)
        return self._bodies.get(url, '')


LISTING_HTML = """
<article class="post">
    <h1 class="post-title"><a href="http://getcomics.example/weekly-1">This Week</a></h1>
</article>
"""

ARTICLE_HTML = """
<div class="entry-content">
    <ul>
        <li>Batman #123</li>
        <li>Amazing Spider-Man #45 (2024)</li>
    </ul>
</div>
"""


class find_latest_weekly_release_article(unittest.IsolatedAsyncioTestCase):
    async def test_finds_link(self):
        session = _FakeAsyncSession({
            'https://getcomics.org/category/weekly-comic-book-releases/':
                LISTING_HTML
        })
        link = await _find_latest_weekly_release_article(session)
        self.assertEqual(link, 'http://getcomics.example/weekly-1')

    async def test_empty_listing_returns_none(self):
        session = _FakeAsyncSession({})
        link = await _find_latest_weekly_release_article(session)
        self.assertIsNone(link)

    async def test_no_article_tag_returns_none(self):
        session = _FakeAsyncSession({
            'https://getcomics.org/category/weekly-comic-book-releases/':
                '<html><body>Nothing here</body></html>'
        })
        link = await _find_latest_weekly_release_article(session)
        self.assertIsNone(link)

    async def test_no_anchor_returns_none(self):
        session = _FakeAsyncSession({
            'https://getcomics.org/category/weekly-comic-book-releases/':
                '<article class="post"><h1 class="post-title"></h1></article>'
        })
        link = await _find_latest_weekly_release_article(session)
        self.assertIsNone(link)


class getcomics_weekly_releases_fetch(unittest.IsolatedAsyncioTestCase):
    async def test_full_fetch_parses_releases(self):
        session = _FakeAsyncSession({
            'https://getcomics.org/category/weekly-comic-book-releases/':
                LISTING_HTML,
            'http://getcomics.example/weekly-1': ARTICLE_HTML
        })

        releases = await fetch_getcomics_weekly_releases(session)

        self.assertEqual(len(releases), 2)
        self.assertEqual(releases[0]['series'], 'Batman')
        self.assertEqual(releases[0]['source'], 'GetComics')
        self.assertEqual(
            releases[0]['link'], 'http://getcomics.example/weekly-1'
        )

    async def test_no_listing_returns_empty(self):
        session = _FakeAsyncSession({})
        releases = await fetch_getcomics_weekly_releases(session)
        self.assertEqual(releases, [])

    async def test_empty_article_body_returns_empty(self):
        session = _FakeAsyncSession({
            'https://getcomics.org/category/weekly-comic-book-releases/':
                LISTING_HTML
            # No body registered for the article link itself.
        })
        releases = await fetch_getcomics_weekly_releases(session)
        self.assertEqual(releases, [])


if __name__ == '__main__':
    unittest.main()
