# -*- coding: utf-8 -*-

"""
Fetching and parsing "what's releasing this week" from GetComics.

The raw fetch/parse logic lives here (mirroring how `getcomics.py` and
`indexers.py` hold the raw fetch/parse logic for the search feature); the
pluggable `WeeklyReleaseSource` adapter and registry that this plugs into
live in `backend.features.pull_list`, mirroring how `search_getcomics()`/
`search_indexer()` here plug into `SearchGetComics`/`SearchIndexers` in
`backend.features.search`.

The live GetComics layout was verified on 2026-08-18. Weekly Pack posts are
surfaced on the normal GetComics home page, and issue rows currently look
like ``Action Comics #1101 : Download | Read Online``. The parser remains
intentionally defensive so harmless markup changes do not crash the weekly
task.
"""

from re import compile as re_compile
from typing import List, Union

from bs4 import BeautifulSoup, Tag

from backend.base.definitions import Constants, WeeklyReleaseData
from backend.base.helpers import AsyncSession
from backend.base.logging import LOGGER

# Matches release lines like "Batman #123", "Batman Vol. 3 #123 (2024)" or
# the live GetComics form "Batman #123 : Download | Read Online". The tail
# after ':' is presentation/download-link text and is deliberately ignored.
_RELEASE_LINE_REGEX = re_compile(
    r"^(?P<series>.+?)\s*#\s*(?P<issue>[\w.]+)\s*"
    r"(?:\((?P<year>\d{4})\))?\s*(?::.*)?$"
)


def _parse_weekly_release_lines(
    soup: BeautifulSoup,
    source_name: str,
    link: str
) -> List[WeeklyReleaseData]:
    """Parse "Series #Issue" style release lines out of a Weekly Pack post.

    GetComics currently lists each release in an ``<li>`` with download and
    read-online links after a colon. We read the whole list-item text and let
    `_RELEASE_LINE_REGEX` ignore that trailing UI text. For older/different
    post layouts, paragraph/div text remains a fallback.

    Args:
        soup (BeautifulSoup): The soup of the Weekly Pack article page.
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
    """Find the newest Weekly Pack article from the GetComics home page.

    GetComics does not currently expose the previously assumed
    ``/category/weekly-comic-book-releases/`` listing. Weekly Pack posts are
    regular ``article.post`` entries on the home page, so scan the available
    posts and choose the first title/link that clearly identifies a Weekly
    Pack. This is more tolerant of the site's tag/category reshuffling.

    Args:
        session (AsyncSession): The session to make the request with.

    Returns:
        Union[str, None]: The newest Weekly Pack link, or ``None`` when the
            page cannot be fetched or no Weekly Pack is visible.
    """
    listing_html = await session.get_text(
        Constants.GC_SITE_URL, quiet_fail=True
    )
    if not listing_html:
        return None

    soup = BeautifulSoup(listing_html, "html.parser")
    for article in soup.find_all("article", {"class": "post"}):
        if not isinstance(article, Tag):
            continue

        title_el = article.find("h1", {"class": "post-title"})
        anchor = title_el.find("a") if isinstance(title_el, Tag) else None
        if not isinstance(anchor, Tag):
            continue

        link = anchor.get("href")
        title = anchor.get_text(" ", strip=True)
        if not isinstance(link, str) or not link:
            continue

        if "weekly pack" in title.lower() or "weekly-pack" in link.lower():
            return link

    return None


async def fetch_getcomics_weekly_releases(
    session: AsyncSession
) -> List[WeeklyReleaseData]:
    """Fetch and parse the most recent Weekly Pack from GetComics.

    Returns an empty list rather than raising on request/parse failure, so a
    broken release source cannot crash a future multi-source weekly check.
    Failures are logged at warning level instead of disappearing silently.
    """
    link = await _find_latest_weekly_release_article(session)
    if not link:
        LOGGER.warning("Could not find a current Weekly Pack post on GetComics")
        return []

    article_html = await session.get_text(link, quiet_fail=True)
    if not article_html:
        LOGGER.warning("Could not fetch GetComics Weekly Pack post %s", link)
        return []

    article_soup = BeautifulSoup(article_html, "html.parser")
    releases = _parse_weekly_release_lines(
        article_soup, Constants.GC_SOURCE_TERM, link
    )
    if not releases:
        LOGGER.warning(
            "No parsable release lines found on GetComics Weekly Pack %s", link
        )

    return releases
