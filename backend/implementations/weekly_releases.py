# -*- coding: utf-8 -*-

"""
Fetching publisher-aware release metadata plus GetComics availability.

The raw fetch/parse logic lives here (mirroring how `getcomics.py` and
`indexers.py` hold the raw fetch/parse logic for the search feature); the
pluggable `WeeklyReleaseSource` adapter and registry that this plugs into
live in `backend.features.pull_list`, mirroring how `search_getcomics()`/
`search_indexer()` here plug into `SearchGetComics`/`SearchIndexers` in
`backend.features.search`.

The calendar feed is the community release provider used by Mylar. GetComics
weekly-pack posts remain a distinct availability overlay: their presence says
a download page exists, not that they are the authoritative release catalogue.
"""

from datetime import date, datetime, timedelta
from json import JSONDecodeError, loads as json_loads
from re import compile as re_compile
from typing import Any, Dict, List, Union

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


def _week_start(value: date) -> str:
    return (value - timedelta(days=value.weekday())).isoformat()


def _normalise_date(value: Any) -> Union[str, None]:
    """Return a provider date as ISO-8601, accepting Mylar's known shapes."""
    if not value:
        return None

    text = str(value).strip()
    for date_format in ('%Y-%m-%d', '%m/%d/%Y', '%Y%m%d'):
        try:
            return datetime.strptime(text[:10], date_format).date().isoformat()
        except ValueError:
            continue
    return None


def _optional_int(value: Any) -> Union[int, None]:
    try:
        return int(value) if value not in (None, '') else None
    except (TypeError, ValueError):
        return None


def _parse_mylar_release_data(
    payload: Any,
    requested_date: date
) -> List[WeeklyReleaseData]:
    """Convert Mylar's release-provider JSON into Kapowarr release rows."""
    if isinstance(payload, dict):
        payload = payload.get('results', payload.get('releases', []))
    if not isinstance(payload, list):
        return []

    releases: List[WeeklyReleaseData] = []
    seen = set()
    requested_week = _week_start(requested_date)
    for raw in payload:
        if not isinstance(raw, dict):
            continue
        series = str(raw.get('series') or '').strip()
        if not series:
            continue
        issue_number = str(raw.get('issue') or '').strip() or None
        release_date = _normalise_date(raw.get('shipdate'))
        key = (
            _optional_int(raw.get('issueid')),
            series.lower(), issue_number, release_date
        )
        if key in seen:
            continue
        seen.add(key)

        releases.append(WeeklyReleaseData(
            series=series,
            issue_number=issue_number,
            year=_optional_int(raw.get('seriesyear') or raw.get('year')),
            link=str(raw.get('link') or ''),
            source='Mylar Release Provider',
            publisher=(str(raw.get('publisher')).strip()
                       if raw.get('publisher') else None),
            release_date=release_date,
            cover_date=_normalise_date(raw.get('coverdate')),
            week_start=(
                _week_start(datetime.strptime(release_date, '%Y-%m-%d').date())
                if release_date else requested_week
            ),
            comicvine_volume_id=_optional_int(raw.get('comicid')),
            comicvine_issue_id=_optional_int(raw.get('issueid')),
            availability_source=None,
            availability_link=None
        ))

    return releases


async def fetch_mylar_weekly_releases(
    session: AsyncSession,
    requested_date: date
) -> List[WeeklyReleaseData]:
    """Fetch one publisher-aware week from the provider used by Mylar."""
    body = await session.get_text(
        Constants.MYLAR_RELEASES_URL,
        params={
            'week': requested_date.strftime('%U'),
            'year': requested_date.year
        },
        quiet_fail=True
    )
    if not body:
        LOGGER.warning(
            'Could not fetch release week %s from the Mylar provider',
            _week_start(requested_date)
        )
        return []

    try:
        payload: Union[List[Any], Dict[str, Any]] = json_loads(body)
    except JSONDecodeError:
        LOGGER.warning('Mylar release provider returned invalid JSON')
        return []
    return _parse_mylar_release_data(payload, requested_date)


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
            lines.extend(
                line.strip()
                for line in text.split("\n")
                if line.strip()
            )

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
            source=source_name,
            publisher=None,
            release_date=None,
            cover_date=None,
            week_start=_week_start(date.today()),
            comicvine_volume_id=None,
            comicvine_issue_id=None,
            availability_source=source_name,
            availability_link=link
        ))

    return releases


async def _find_latest_weekly_release_article(
    session: AsyncSession
) -> Union[str, None]:
    """Find the newest Weekly Pack article from GetComics.

    GetComics does not currently expose the previously assumed
    ``/category/weekly-comic-book-releases/`` listing. Check the live
    ``dc-week`` tag archive first and then the home page, choosing the first
    title/link that clearly identifies a Weekly Pack.

    Args:
        session (AsyncSession): The session to make the request with.

    Returns:
        Union[str, None]: The newest Weekly Pack link, or ``None`` when the
            page cannot be fetched or no Weekly Pack is visible.
    """
    for listing_url in (
        Constants.GC_WEEKLY_RELEASES_URL,
        Constants.GC_SITE_URL
    ):
        listing_html = await session.get_text(listing_url, quiet_fail=True)
        if not listing_html:
            continue

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
