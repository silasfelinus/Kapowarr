# -*- coding: utf-8 -*-

"""
Fetching and parsing "what's releasing this week" from GetComics.

The raw fetch/parse logic lives here (mirroring how `getcomics.py` and
`indexers.py` hold the raw fetch/parse logic for the search feature); the
pluggable `WeeklyReleaseSource` adapter and registry that this plugs into
live in `backend.features.pull_list`, mirroring how `search_getcomics()`/
`search_indexer()` here plug into `SearchGetComics`/`SearchIndexers` in
`backend.features.search`.

Not live-verified against a real GetComics weekly-releases page in this
sandbox (no network egress available here) -- same caveat already flagged
on this fork's other scraped/API integrations (t-005, t-007, t-024,
`indexers.py`'s Newznab support). Parsing is deliberately defensive about
page shape (see `_parse_weekly_release_lines`) and covered by fixture-
based unit tests using saved sample HTML rather than a live fetch. The
exact markup of GetComics' weekly-releases posts should be checked against
the live site before relying on this in production; the category URL and
line-parsing regex in this module are a best-effort match for GetComics'
existing "one release per list item" post style, not a verified scrape.
"""

from re import compile as re_compile
from typing import List, Union

from bs4 import BeautifulSoup, Tag

from backend.base.definitions import Constants, WeeklyReleaseData
from backend.base.helpers import AsyncSession
from backend.base.logging import LOGGER

# Matches release lines like "Batman #123", "Batman Vol. 3 #123 (2024)" or
# "Batman Annual #1AU" -- a series title, a '#', an issue number (allowing
# non-purely-numeric forms like "1AU" or "12.1"), and an optional trailing
# year in parentheses.
_RELEASE_LINE_REGEX = re_compile(
    r"^(?P<series>.+?)\s*#\s*(?P<issue>[\w.]+)\s*(?:\((?P<year>\d{4})\))?\s*$"
)


def _parse_weekly_release_lines(
    soup: BeautifulSoup,
    source_name: str,
    link: str
) -> List[WeeklyReleaseData]:
    """Parse "Series #Issue" style release lines out of a weekly-releases
    article body.

    GetComics' weekly pull-list posts list one release per line -- this
    looks for `<li>` elements first (the common case for a bulleted pull
    list) and falls back to paragraph/div text lines, rather than assuming
    one specific markup shape. Lines that don't look like a release (no
    recognisable "Title #Number" pattern) are silently skipped instead of
    raising, since a post's intro/outro paragraphs and category blurbs are
    expected to not match.

    Args:
        soup (BeautifulSoup): The soup of the weekly-releases article page.
        source_name (str): The display name to tag every release with.
        link (str): The article link to tag every release with.

    Returns:
        List[WeeklyReleaseData]: The releases found, in page order and
            deduplicated by (series, issue number).
    """
    content = soup.find("div", {"class": "entry-content"})
    if not isinstance(content, Tag):
        content = soup

    lines: List[str] = [
        li.get_text(" ", strip=True)
        for li in content.find_all("li")
    ]

    if not lines:
        # Fall back to plain lines of paragraph/div text, for posts that
        # list releases without a <ul>/<li> structure.
        for block in content.find_all(["p", "div"]):
            text = block.get_text("\n", strip=True)
            lines.extend(line.strip() for line in text.split("\n") if line.strip())

    releases: List[WeeklyReleaseData] = []
    seen = set()
    for line in lines:
        match = _RELEASE_LINE_REGEX.match(line)
        if not match:
            continue

        series = match.group("series").strip(" -–—")
        issue_number = match.group("issue")
        if not series:
            continue

        key = (series.lower(), issue_number)
        if key in seen:
            continue
        seen.add(key)

        year_str = match.group("year")
        releases.append(WeeklyReleaseData(
            series=series,
            issue_number=issue_number,
            year=int(year_str) if year_str else None,
            link=link,
            source=source_name
        ))

    return releases


async def _find_latest_weekly_release_article(
    session: AsyncSession
) -> Union[str, None]:
    """Find the link to the most recent post in GetComics' weekly-releases
    category, reusing the same "article.post > h1.post-title > a" markup
    `getcomics.py`'s own search-result parsing (`_get_articles`) relies on
    for every other GetComics page.

    Args:
        session (AsyncSession): The session to make the request with.

    Returns:
        Union[str, None]: The link, or `None` if it couldn't be found.
    """
    listing_html = await session.get_text(
        Constants.GC_WEEKLY_RELEASES_URL, quiet_fail=True
    )
    if not listing_html:
        return None

    soup = BeautifulSoup(listing_html, "html.parser")
    article = soup.find("article", {"class": "post"})
    if not isinstance(article, Tag):
        return None

    title_el = article.find("h1", {"class": "post-title"})
    anchor = title_el.find("a") if isinstance(title_el, Tag) else None
    if not isinstance(anchor, Tag):
        return None

    link = anchor.get("href")
    return link if isinstance(link, str) and link else None


async def fetch_getcomics_weekly_releases(
    session: AsyncSession
) -> List[WeeklyReleaseData]:
    """Fetch and parse the most recent "weekly releases" post from
    GetComics.

    Args:
        session (AsyncSession): The session to make the requests with.

    Returns:
        List[WeeklyReleaseData]: The releases found. Empty (rather than
            raising) on any request/parse failure, same "empty rather than
            raising" contract as `search_getcomics()`/`search_indexer()` --
            a broken/unreachable release source shouldn't fail a check
            that could combine several sources.
    """
    link = await _find_latest_weekly_release_article(session)
    if not link:
        return []

    article_html = await session.get_text(link, quiet_fail=True)
    if not article_html:
        return []

    article_soup = BeautifulSoup(article_html, "html.parser")
    releases = _parse_weekly_release_lines(
        article_soup, Constants.GC_SOURCE_TERM, link
    )
    if not releases:
        LOGGER.debug(
            "No parsable release lines found on weekly-releases post %s", link
        )

    return releases
