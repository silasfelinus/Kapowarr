# -*- coding: utf-8 -*-

"""
Fetching and parsing comic search results from the Internet Archive.

The Internet Archive genuinely serves some items as unrestricted, directly
downloadable files (kapowarr/t-041), which is what makes it different in
kind from Anna's Archive (`backend.implementations.annas_archive`,
kapowarr/t-040): that source is a shadow library Kapowarr never downloads
from at all. Here, the boundary is per-item rather than per-source:

- The majority of the Internet Archive's comics are controlled-lending
  items (borrow-for-an-hour, behind their BookReader). Those are never
  resolved, never have their lending/access controls bypassed, and never
  get a real download link built for them -- they only ever appear via
  `fetch_internet_archive_discover_page()`, browse/link-out only, exactly
  like every other Discover entry.
- A minority of items are genuinely public domain / openly licensed and the
  Internet Archive itself serves their files as plain, unauthenticated
  downloads. Only those items are eligible to appear from
  `search_internet_archive()`, Kapowarr's real acquisition search
  (`backend.features.search`) -- and only after `_is_publicly_downloadable()`
  confirms the item's own metadata says so.

This is Silas's standing default-recommendation policy applied literally:
automate only assets that are directly and publicly downloadable;
controlled-lending/borrow-only items may surface for discovery but are
never acquired automatically or have their access controls bypassed. See
the task note on kapowarr/t-041 for the exact decision this implements.

Uses the Internet Archive's own JSON APIs (`advancedsearch.php` for search,
`/metadata/<identifier>` for the per-item restriction flag and file list)
rather than scraping HTML -- both are long-standing, documented, public
endpoints, so there is no fragile markup dependency the way GetComics' and
Anna's Archive's HTML scrapers have.

Not live-verified against the real Internet Archive in this sandbox (no
network egress available here) -- same documented caveat as this fork's
other scraped/API integrations (t-005, t-007, t-024, t-027, t-040).
"""

from asyncio import gather
from json import JSONDecodeError, loads
from typing import Any, Dict, List, Tuple, Union
from urllib.parse import quote

from backend.base.definitions import (Constants, DiscoverItemData,
                                      SearchResultData)
from backend.base.file_extraction import extract_filename_data
from backend.base.helpers import AsyncSession
from backend.base.logging import LOGGER

# How many pages deep Internet Archive Discover browsing is allowed to page
# into, mirroring the cap `fetch_annas_archive_discover_page()` applies.
MAX_INTERNET_ARCHIVE_DISCOVER_PAGES = 10

INTERNET_ARCHIVE_DISCOVER_ROWS_PER_PAGE = 50
"How many Discover results are requested per page."

INTERNET_ARCHIVE_SEARCH_ROWS = 50
"How many raw search hits `search_internet_archive()` considers per query."

INTERNET_ARCHIVE_DIRECT_CANDIDATE_LIMIT = 25
"""
How many of those raw hits get an item-metadata lookup (the only way to
learn whether an item is access-restricted) before giving up on the rest.
Bounded because each candidate costs its own request, unlike a normal
single-page search.
"""

# File extensions Kapowarr can use directly, in preference order. Anything
# else on an item (OCR text layers, thumbnails, torrent/metadata sidecar
# files the API also lists) is never picked as the download.
_COMIC_FILE_EXTENSIONS = ("cbz", "cbr", "pdf", "epub")


def _build_search_query(query: str) -> str:
    """Build an `advancedsearch.php` query string restricted to texts/books
    (the Internet Archive mediatype that comics are catalogued under).
    """
    terms = ["mediatype:(texts)"]
    if query:
        terms.append(f"({query})")
    return " AND ".join(terms)


async def _advanced_search(
    session: AsyncSession,
    query: str,
    page: int,
    rows: int
) -> Tuple[List[Dict[str, Any]], int]:
    """Run one `advancedsearch.php` query and return its docs + page count.

    Empty results and a page count of `1` on any request/parse failure,
    same "empty rather than raising" contract as the other Discover/Search
    source implementations in this fork.
    """
    params = {
        "q": _build_search_query(query),
        "fl[]": ["identifier", "title", "year"],
        "sort[]": ["addeddate desc"],
        "rows": str(rows),
        "page": str(max(1, page)),
        "output": "json"
    }
    body = await session.get_text(
        f"{Constants.IA_SITE_URL}/advancedsearch.php",
        params=params,
        quiet_fail=True
    )
    if not body:
        return [], 1

    try:
        data = loads(body)
    except JSONDecodeError:
        LOGGER.debug("Internet Archive search returned unparsable JSON")
        return [], 1

    response = data.get("response") if isinstance(data, dict) else None
    if not isinstance(response, dict):
        return [], 1

    docs = response.get("docs")
    if not isinstance(docs, list):
        docs = []

    total = response.get("numFound") or 0
    max_page = max(1, -(-int(total) // rows)) if rows else 1

    return docs, max_page


async def fetch_internet_archive_discover_page(
    session: AsyncSession,
    page: int = 1
) -> Tuple[List[DiscoverItemData], int]:
    """Fetch and parse one page of the Internet Archive's most recently
    added texts, for the Discover page.

    Every item is included regardless of lending/access restriction --
    Discover is browse/link-out only (see this module's docstring), never
    acquisition, so the restriction check `search_internet_archive()` does
    is not needed here.

    Args:
        session (AsyncSession): The session to make the request with.
        page (int, optional): The page to fetch (1-indexed). Defaults to 1.

    Returns:
        Tuple[List[DiscoverItemData], int]: The items found on this page,
            and the total number of pages available (capped at
            `MAX_INTERNET_ARCHIVE_DISCOVER_PAGES`).
    """
    docs, max_page = await _advanced_search(
        session, "", page, INTERNET_ARCHIVE_DISCOVER_ROWS_PER_PAGE
    )
    max_page = min(max_page, MAX_INTERNET_ARCHIVE_DISCOVER_PAGES)

    items: List[DiscoverItemData] = []
    for doc in docs:
        identifier = doc.get("identifier")
        title = doc.get("title")
        if not identifier or not title:
            continue

        items.append({
            **extract_filename_data(
                str(title),
                assume_volume_number=False,
                fix_year=True
            ),
            "link": f"{Constants.IA_SITE_URL}/details/{identifier}",
            "display_title": str(title),
            "source": Constants.IA_SOURCE_TERM,
            "cover": f"{Constants.IA_SITE_URL}/services/img/{identifier}"
        })

    if not items:
        LOGGER.debug(
            "No parsable results found on Internet Archive discover page %d",
            page
        )

    return items, max_page


async def _fetch_item_metadata(
    session: AsyncSession,
    identifier: str
) -> Union[Dict[str, Any], None]:
    """Fetch an item's `/metadata/<identifier>` record.

    Returns `None` (rather than raising) on any request/parse failure, so
    one unreachable item never fails the whole search.
    """
    body = await session.get_text(
        f"{Constants.IA_SITE_URL}/metadata/{identifier}",
        quiet_fail=True
    )
    if not body:
        return None

    try:
        data = loads(body)
    except JSONDecodeError:
        return None

    return data if isinstance(data, dict) else None


def _is_publicly_downloadable(metadata: Dict[str, Any]) -> bool:
    """Decide whether an Internet Archive item is safe to treat as a real
    acquisition source, from its own `/metadata` record.

    Conservative on purpose: any signal that the item is lending/access
    controlled excludes it, even if another signal is ambiguous. This is
    the one function that stands between "browse only" and "Kapowarr
    downloads this automatically" -- see this module's docstring.
    """
    item_metadata = metadata.get("metadata")
    if not isinstance(item_metadata, dict):
        # No metadata block at all is not evidence of being unrestricted.
        return False

    if str(item_metadata.get("access-restricted-item", "")).lower() == "true":
        return False

    collections = item_metadata.get("collection", [])
    if isinstance(collections, str):
        collections = [collections]
    if not isinstance(collections, list):
        collections = []
    restricted_collections = {"inlibrary", "lendinglibrary", "printdisabled"}
    if restricted_collections.intersection(collections):
        return False

    return True


def _pick_downloadable_filename(metadata: Dict[str, Any]) -> Union[str, None]:
    """Pick the single best file to download from an item's file list.

    Prefers `_COMIC_FILE_EXTENSIONS`, in order, and only ever a top-level
    file (an item's own file list can include OCR/text-layer and thumbnail
    sidecar files alongside the actual comic; those are never picked).
    """
    files = metadata.get("files")
    if not isinstance(files, list):
        return None

    by_extension: Dict[str, str] = {}
    for file in files:
        if not isinstance(file, dict):
            continue

        name = file.get("name")
        if not isinstance(name, str) or not name or "/" in name:
            continue

        extension = name.rsplit(".", 1)[-1].lower() if "." in name else ""
        if extension in _COMIC_FILE_EXTENSIONS and extension not in by_extension:
            by_extension[extension] = name

    for extension in _COMIC_FILE_EXTENSIONS:
        if extension in by_extension:
            return by_extension[extension]

    return None


async def search_internet_archive(
    session: AsyncSession,
    query: str
) -> List[SearchResultData]:
    """Give the search results from the Internet Archive for the query --
    real acquisition results only, never a controlled-lending item.

    Every result's `link` is already the exact, publicly-downloadable file
    URL (`{IA_SITE_URL}/download/<identifier>/<filename>`), confirmed via
    `_is_publicly_downloadable()` against the item's own metadata before it
    is ever built. An item that fails that check, or has no file this fork
    recognises as a comic (`_pick_downloadable_filename()`), is silently
    excluded -- not returned with a placeholder link, not returned and
    filtered later. It can still be found by browsing Discover.

    Args:
        session (AsyncSession): The session to make the requests with.
        query (str): The query to use.

    Returns:
        List[SearchResultData]: The search results.
    """
    if not query:
        return []

    docs, _ = await _advanced_search(
        session, query, 1, INTERNET_ARCHIVE_SEARCH_ROWS
    )
    candidates = [
        doc for doc in docs
        if doc.get("identifier") and doc.get("title")
    ][:INTERNET_ARCHIVE_DIRECT_CANDIDATE_LIMIT]

    if not candidates:
        return []

    metadatas = await gather(*(
        _fetch_item_metadata(session, doc["identifier"])
        for doc in candidates
    ))

    results: List[SearchResultData] = []
    for doc, metadata in zip(candidates, metadatas):
        if not metadata or not _is_publicly_downloadable(metadata):
            continue

        filename = _pick_downloadable_filename(metadata)
        if not filename:
            continue

        identifier = doc["identifier"]
        title = str(doc["title"])

        results.append({
            **extract_filename_data(
                title,
                assume_volume_number=False,
                fix_year=True
            ),
            "link": (
                f"{Constants.IA_SITE_URL}/download/{identifier}/"
                f"{quote(filename)}"
            ),
            "display_title": title,
            "source": Constants.IA_SOURCE_TERM
        })

    return results
