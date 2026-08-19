import unittest
from datetime import date

from bs4 import BeautifulSoup

from backend.implementations.weekly_releases import (
    _find_latest_weekly_release_article, _parse_mylar_release_data,
    _parse_weekly_release_lines,
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

    def test_parses_live_getcomics_download_suffix(self):
        html = """
        <div class="entry-content">
            <ul>
                <li>
                    Action Comics #1101 :
                    <a href="/download/1">Download</a> |
                    <a href="/read/1">Read Online</a>
                </li>
                <li>D’Orc #7 : <a href="/download/2">Download</a></li>
                <li>The Witcher – The Last Wish : <a href="/download/3">Download</a></li>
            </ul>
        </div>
        """
        soup = BeautifulSoup(html, "html.parser")
        releases = _parse_weekly_release_lines(
            soup, "GetComics", "http://x/weekly-pack"
        )

        self.assertEqual(len(releases), 2)
        self.assertEqual(releases[0]['series'], 'Action Comics')
        self.assertEqual(releases[0]['issue_number'], '1101')
        self.assertEqual(releases[1]['series'], 'D’Orc')
        self.assertEqual(releases[1]['issue_number'], '7')

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
                <li>Batman #123 : Download</li>
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
                <li>X-Men #1AU : Download</li>
            </ul>
        </div>
        """
        soup = BeautifulSoup(html, "html.parser")
        releases = _parse_weekly_release_lines(soup, "GetComics", "http://x/1")

        self.assertEqual(len(releases), 1)
        self.assertEqual(releases[0]['issue_number'], '1AU')


class parse_mylar_release_data(unittest.TestCase):
    def test_maps_publisher_dates_and_comicvine_ids(self):
        releases = _parse_mylar_release_data([{
            'series': 'Absolute Batman',
            'issue': '23',
            'publisher': 'DC Comics',
            'shipdate': '08/19/2026',
            'coverdate': '2026-10-01',
            'comicid': '4050',
            'issueid': '9001',
            'seriesyear': '2024',
            'link': 'https://comicvine.example/issue/9001'
        }], date(2026, 8, 19))

        self.assertEqual(len(releases), 1)
        self.assertEqual(releases[0]['publisher'], 'DC Comics')
        self.assertEqual(releases[0]['release_date'], '2026-08-19')
        self.assertEqual(releases[0]['week_start'], '2026-08-17')
        self.assertEqual(releases[0]['comicvine_volume_id'], 4050)
        self.assertEqual(releases[0]['comicvine_issue_id'], 9001)
        self.assertIsNone(releases[0]['availability_source'])

    def test_invalid_rows_are_skipped(self):
        releases = _parse_mylar_release_data([
            {'publisher': 'DC Comics'},
            'not a mapping'
        ], date(2026, 8, 19))

        self.assertEqual(releases, [])


# =====================
# Fetch helpers, with a fake AsyncSession
# =====================
class _FakeAsyncSession:
    """Returns canned response bodies from a dict keyed by URL."""

    def __init__(self, bodies) -> None:
        self._bodies = bodies
        self.calls = []

    async def get_text(self, url, params={}, headers={}, quiet_fail=False):
        self.calls.append(url)
        return self._bodies.get(url, '')


LISTING_HTML = """
<article class="post">
    <h1 class="post-title">
        <a href="http://getcomics.example/batman">Absolute Batman #23 (2026)</a>
    </h1>
</article>
<article class="post">
    <h1 class="post-title">
        <a href="http://getcomics.example/other-comics/2026-08-12-weekly-pack/">
            2026.08.12 Weekly Pack
        </a>
    </h1>
</article>
"""

ARTICLE_HTML = """
<div class="entry-content">
    <h3>DC COMICS</h3>
    <ul>
        <li>Action Comics #1101 : <a href="/d/1">Download</a> | Read Online</li>
        <li>Absolute Batman #23 : <a href="/d/2">Download</a></li>
    </ul>
</div>
"""

WEEKLY_LINK = 'http://getcomics.example/other-comics/2026-08-12-weekly-pack/'


class find_latest_weekly_release_article(unittest.IsolatedAsyncioTestCase):
    async def test_finds_weekly_pack_after_normal_post(self):
        session = _FakeAsyncSession({
            'https://getcomics.org/tag/dc-week/':
                LISTING_HTML
        })
        link = await _find_latest_weekly_release_article(session)
        self.assertEqual(link, WEEKLY_LINK)

    async def test_weekly_pack_href_is_enough_even_if_title_changes(self):
        html = """
        <article class="post">
            <h1 class="post-title">
                <a href="http://x/2026-08-12-weekly-pack/">New Comics 08/12</a>
            </h1>
        </article>
        """
        session = _FakeAsyncSession({'https://getcomics.org': html})
        link = await _find_latest_weekly_release_article(session)
        self.assertEqual(link, 'http://x/2026-08-12-weekly-pack/')

    async def test_empty_listing_returns_none(self):
        session = _FakeAsyncSession({})
        link = await _find_latest_weekly_release_article(session)
        self.assertIsNone(link)

    async def test_no_article_tag_returns_none(self):
        session = _FakeAsyncSession({
            'https://getcomics.org/tag/dc-week/':
                '<html><body>Nothing here</body></html>'
        })
        link = await _find_latest_weekly_release_article(session)
        self.assertIsNone(link)

    async def test_no_weekly_pack_returns_none(self):
        session = _FakeAsyncSession({
            'https://getcomics.org/tag/dc-week/': (
                '<article class="post"><h1 class="post-title">'
                '<a href="http://x/batman">Batman #1</a>'
                '</h1></article>'
            )
        })
        link = await _find_latest_weekly_release_article(session)
        self.assertIsNone(link)


class getcomics_weekly_releases_fetch(unittest.IsolatedAsyncioTestCase):
    async def test_full_fetch_parses_live_shaped_releases(self):
        session = _FakeAsyncSession({
            'https://getcomics.org/tag/dc-week/':
                LISTING_HTML,
            WEEKLY_LINK: ARTICLE_HTML
        })

        releases = await fetch_getcomics_weekly_releases(session)

        self.assertEqual(len(releases), 2)
        self.assertEqual(releases[0]['series'], 'Action Comics')
        self.assertEqual(releases[0]['issue_number'], '1101')
        self.assertEqual(releases[0]['source'], 'GetComics')
        self.assertEqual(releases[0]['link'], WEEKLY_LINK)

    async def test_no_listing_returns_empty(self):
        session = _FakeAsyncSession({})
        releases = await fetch_getcomics_weekly_releases(session)
        self.assertEqual(releases, [])

    async def test_empty_article_body_returns_empty(self):
        session = _FakeAsyncSession({
            'https://getcomics.org/tag/dc-week/':
                LISTING_HTML
            # No body registered for the Weekly Pack link itself.
        })
        releases = await fetch_getcomics_weekly_releases(session)
        self.assertEqual(releases, [])


if __name__ == '__main__':
    unittest.main()
