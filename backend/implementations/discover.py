# -*- coding: utf-8 -*-

"""
Fetching and parsing "recent releases" from GetComics for the Discover page.

The raw fetch/parse logic lives here (mirroring how `getcomics.py` holds the
raw fetch/parse logic for the search feature, and how `weekly_releases.py`
holds it for the weekly pull list); the pluggable `DiscoverSource` adapter
and registry that this plugs into live in `backend.features.discover`,
mirroring how `search_getcomics()` plugs into `SearchGetComics` in
`backend.features.search` and how `fetch_getcomics_weekly_releases()` plugs
into `GetComicsWeeklyReleases` in `backend.features.pull_list`.

Reuses `getcomics.py`'s own `_get_max_page()` for pagination -- a Discover
page is just GetComics' regular post listing (the same paged
"article.post > h1.post-title > a" markup `_get_articles()` and
`search_getcomics()` already rely on), browsed unfiltered instead of
filtered by a search query. Article extraction is a "moral equivalent" of
`_get_articles()` rather than that exact function, since Discover also
wants a best-effort cover image per post, which `_get_articles()` doesn't
extract (`search_getcomics()` has never needed one).

Not live-verified against a real GetComics listing page in this sandbox (no
network egress available here) -- same caveat already flagged on
`weekly_releases.py` and this fork's other scraped/API integrations (t-005,
t-007, t-024, t-027). The cover-image lookup in particular is a best-effort
guess at common WordPress "featured image" markup (falls back to `None`
rather than raising) and should be checked against the live site before
relying on it; the pagination and title extraction reuse `getcomics.py`'s
already-relied-upon markup assumptions, so they're on firmer footing.
"""

from typing import List, Tuple, Union

from bs4 import BeautifulSoup, Tag

from backend.base.definitions import Constants, DiscoverItemData
from backend.base.file_extraction import extract_filename_data
from backend.base.helpers import AsyncSession, first_of_range
from backend.base.logging import LOGGER
from backend.implementations.getcomics import _get_max_page

# How many pages deep "recent releases" is allowed to page into, mirroring
# the cap `search_getcomics()` applies to its own paginated fetch.
MAX_DISCOVER_PAGES = 10


def _get_discover_articles(
    soup: BeautifulSoup
) -> List[Tuple[str, str, Union[str, None]]]:
    """From a GetComics post-listing page (the homepage or a `/page/N`
    thereof), extract each post's link, title and -- best-effort -- a
    cover/thumbnail image.

    Moral equivalent of `getcomics.py`'s `_get_articles()`, reusing the same
    `article.post > h1.post-title > a` markup for link/title, plus a
    best-effort look for an `<img>` inside the article for the cover.

    Args:
        soup (BeautifulSoup): The soup of the GC post-listing page.

    Returns:
        List[Tuple[str, str, Union[str, None]]]: One tuple per post found:
            (link, title, cover). `cover` is `None` when no image could be
            found, rather than raising.
    """
    result: List[Tuple[str, str, Union[str, None]]] = []
    for article in soup.find_all("article", {"class": "post"}):
        title_el = article.find("h1", {"class": "post-title"})
        if not isinstance(title_el, Tag):
            continue

        anchor = title_el.find('a')
        if not isinstance(anchor, Tag):
            continue

        link: str = first_of_range(anchor.get('href') or '')
        if not link:
            continue
        title = title_el.get_text(strip=True)

        cover: Union[str, None] = None
        img = article.find('img')
        if isinstance(img, Tag):
            # WordPress listings commonly lazy-load thumbnails, putting the
            # real URL in a `data-src`/`data-lazy-src` attribute instead of
            # `src` (which holds a placeholder) -- prefer those if present.
            cover = (
                img.get('data-src')
                or img.get('data-lazy-src')
                or img.get('src')
            )
            if not isinstance(cover, str) or not cover:
                cover = None

        result.append((link, title, cover))

    return result


async def fetch_getcomics_discover_page(
    session: AsyncSession,
    page: int = 1
) -> Tuple[List[DiscoverItemData], int]:
    """Fetch and parse one page of GetComics' recent releases.

    Args:
        session (AsyncSession): The session to make the request with.
        page (int, optional): The page to fetch (1-indexed). Defaults to 1.

    Returns:
        Tuple[List[DiscoverItemData], int]: The items found on this page,
            and the total number of pages available (capped at
            `MAX_DISCOVER_PAGES`). Empty items and a page count of `1` on
            any request/parse failure, same "empty rather than raising"
            contract as `search_getcomics()` / `fetch_getcomics_weekly_releases()`.
    """
    page = max(1, page)
    url = (
        Constants.GC_SITE_URL if page == 1
        else f"{Constants.GC_SITE_URL}/page/{page}"
    )

    html = await session.get_text(url, quiet_fail=True)
    if not html:
        return [], 1

    soup = BeautifulSoup(html, "html.parser")
    max_page = min(_get_max_page(soup), MAX_DISCOVER_PAGES)

    items: List[DiscoverItemData] = [
        {
            **extract_filename_data(
                title,
                assume_volume_number=False,
                fix_year=True
            ),
            "link": link,
            "display_title": title,
            "source": Constants.GC_SOURCE_TERM,
            "cover": cover
        }
        for link, title, cover in _get_discover_articles(soup)
    ]

    if not items:
        LOGGER.debug("No parsable posts found on GetComics discover page %d", page)

    return items, max_page
