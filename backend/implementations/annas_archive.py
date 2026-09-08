# -*- coding: utf-8 -*-

"""
Fetching and parsing comic search results from Anna's Archive for the
Discover page.

This is deliberately a metadata/search discovery integration, not an
acquisition source (see kapowarr/t-040 for the scope decision): Anna's
Archive is a shadow library, so Kapowarr never downloads from it, requests
one of its mirrors, or resolves an item to a grabbable file. Every item this
module returns only ever carries a link to the item's own Anna's Archive
page (`/md5/<hash>`) -- exactly the same "browse, cross-reference against
the library, then link out" contract `backend.implementations.discover`
already applies to GetComics, reused as-is rather than special-cased.

Mirrors `discover.py`'s GetComics fetch/parse pair -- see that module's
docstring for how the raw fetch/parse logic here relates to the pluggable
`DiscoverSource` adapter it plugs into (`backend.features.discover`).

Parsing strategy deliberately avoids relying on Anna's Archive's outer
layout/class names, which are known to reshuffle often and are not
consistently human-readable class names to begin with. Every result on an
Anna's Archive search page links to its own item page at a stable,
long-standing URL shape -- `/md5/<hash>` -- regardless of how the
surrounding markup is styled, so that anchor is the one thing this scraper
depends on being stable.

Not live-verified against a real Anna's Archive search page in this sandbox
(no network egress available here) -- same caveat already flagged on
`discover.py`'s GetComics implementation and this fork's other scraped
integrations (t-005, t-007, t-024, t-027). Should be checked against the
live site before relying on it.
"""

from re import search as re_search
from typing import List, Tuple, Union

from bs4 import BeautifulSoup, Tag

from backend.base.definitions import Constants, DiscoverItemData
from backend.base.file_extraction import extract_filename_data
from backend.base.helpers import AsyncSession, first_of_range
from backend.base.logging import LOGGER

# How many pages deep Anna's Archive Discover browsing is allowed to page
# into, mirroring the cap `fetch_getcomics_discover_page()` applies.
MAX_ANNAS_ARCHIVE_DISCOVER_PAGES = 10

# Anna's Archive's own content-type filter value for the "comic" category,
# and its sort value for "recently added" -- the closest equivalent to
# GetComics' unfiltered recent-posts listing that Discover's "browse recent
# releases" contract needs.
ANNAS_ARCHIVE_CONTENT_FILTER = "book_comic"
ANNAS_ARCHIVE_SORT_NEWEST = "newest"


def _get_annas_archive_items(
    soup: BeautifulSoup
) -> List[Tuple[str, str, Union[str, None]]]:
    """From an Anna's Archive search-result page, extract each result's
    item-page link, title and -- best-effort -- a cover/thumbnail image.

    Deliberately keys off `a[href^="/md5/"]` rather than any styling class:
    every search result links to its own item page at that stable URL
    shape, regardless of how the surrounding listing markup is styled.

    Args:
        soup (BeautifulSoup): The soup of the Anna's Archive search page.

    Returns:
        List[Tuple[str, str, Union[str, None]]]: One tuple per result found:
            (link, title, cover). `cover` is `None` when no image could be
            found, rather than raising. Results with no usable title text
            are skipped.
    """
    result: List[Tuple[str, str, Union[str, None]]] = []
    seen_links: set = set()

    for anchor in soup.find_all("a", href=True):
        href: str = first_of_range(anchor.get('href') or '')
        if not href.startswith('/md5/'):
            continue

        title = anchor.get_text(strip=True)
        if not title:
            continue

        link = f"{Constants.ANNAS_ARCHIVE_SITE_URL}{href}"
        if link in seen_links:
            # Anna's Archive search results sometimes wrap both the cover
            # and the title in separate anchors pointing at the same item;
            # only the first (title) anchor encountered should be kept.
            continue
        seen_links.add(link)

        cover: Union[str, None] = None
        img = anchor.find('img')
        if not isinstance(img, Tag):
            # The cover thumbnail is sometimes a direct sibling of the
            # title anchor rather than nested inside it. Deliberately an
            # immediate-sibling lookup (not `anchor.parent.find('img')`,
            # which would search the whole subtree and could pick up a
            # different result's cover if several results share an outer
            # container).
            sibling = anchor.find_previous_sibling('img') \
                or anchor.find_next_sibling('img')
            if isinstance(sibling, Tag):
                img = sibling
        if isinstance(img, Tag):
            cover = img.get('data-src') or img.get('src')
            if not isinstance(cover, str) or not cover:
                cover = None

        result.append((link, title, cover))

    return result


def _get_annas_archive_max_page(soup: BeautifulSoup) -> int:
    """From an Anna's Archive search-result page, best-effort extract the
    total page count.

    Anna's Archive paginates via a `page=N` query parameter rather than
    GetComics' WordPress-style `page-numbers` markup, so this scans every
    link on the page for that parameter instead of relying on a specific
    pager class. Falls back to `1` (a single page) when no pagination link
    can be found, matching the "don't raise, degrade" contract the rest of
    this module follows.

    Args:
        soup (BeautifulSoup): The soup of the Anna's Archive search page.

    Returns:
        int: The number of pages found, at least `1`.
    """
    max_page = 1
    for anchor in soup.find_all("a", href=True):
        href: str = first_of_range(anchor.get('href') or '')
        match = re_search(r'[?&]page=(\d+)', href)
        if match:
            max_page = max(max_page, int(match.group(1)))

    return max_page


async def fetch_annas_archive_discover_page(
    session: AsyncSession,
    page: int = 1
) -> Tuple[List[DiscoverItemData], int]:
    """Fetch and parse one page of Anna's Archive's comic search results,
    sorted newest-first, for the Discover page.

    Args:
        session (AsyncSession): The session to make the request with.
        page (int, optional): The page to fetch (1-indexed). Defaults to 1.

    Returns:
        Tuple[List[DiscoverItemData], int]: The items found on this page,
            and the total number of pages available (capped at
            `MAX_ANNAS_ARCHIVE_DISCOVER_PAGES`). Empty items and a page
            count of `1` on any request/parse failure, same "empty rather
            than raising" contract as `fetch_getcomics_discover_page()`.
    """
    page = max(1, page)
    params = {
        "q": "",
        "content": ANNAS_ARCHIVE_CONTENT_FILTER,
        "sort": ANNAS_ARCHIVE_SORT_NEWEST,
        "page": str(page)
    }

    html = await session.get_text(
        f"{Constants.ANNAS_ARCHIVE_SITE_URL}/search",
        params=params,
        quiet_fail=True
    )
    if not html:
        return [], 1

    soup = BeautifulSoup(html, "html.parser")
    max_page = min(_get_annas_archive_max_page(soup), MAX_ANNAS_ARCHIVE_DISCOVER_PAGES)

    items: List[DiscoverItemData] = [
        {
            **extract_filename_data(
                title,
                assume_volume_number=False,
                fix_year=True
            ),
            "link": link,
            "display_title": title,
            "source": Constants.ANNAS_ARCHIVE_SOURCE_TERM,
            "cover": cover
        }
        for link, title, cover in _get_annas_archive_items(soup)
    ]

    if not items:
        LOGGER.debug(
            "No parsable results found on Anna's Archive discover page %d",
            page
        )

    return items, max_page
