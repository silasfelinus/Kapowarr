# -*- coding: utf-8 -*-

"""
GetComics Discover: browse GetComics' recent releases inside Kapowarr,
cross-referenced against the library so already-added series can be told
apart from ones that still need to be searched for and added.

Mirrors `backend.features.pull_list`'s relationship with
`backend.implementations.weekly_releases` (itself mirroring
`backend.features.search`'s `SearchSources` registry): a discover source
registers itself with `DiscoverSources.register()`, so a second source
could be added later without this module's orchestration --
`get_discover_feed()`, `match_discover_items_to_library()` -- needing to
change. The item data itself stays replaceable the same way search/weekly-
release sources are: this module never imports `discover.py`'s fetch
function directly except from inside the one adapter class registered
below.

Unlike the weekly pull list, Discover is a live "browse" view, not a
scheduled/persisted check: there's no library of monitored titles it's
matching a fixed weekly result set against, just "what's on GetComics
right now, page by page" -- so nothing here is written to the database,
and every call re-fetches the requested page live.

Adding a matched-but-not-yet-added item to the library deliberately reuses
the existing add-volume flow (`POST /api/volumes/search` +
`POST /api/volumes`, see `frontend/api.py`'s "Library + Volumes" section)
rather than this module growing a second one -- the frontend Discover page
links a not-yet-added item straight into the existing Add Volume page,
pre-filled with the item's series title as the search query.
"""

from asyncio import gather, run
from typing import Any, Dict, List, Tuple, Type

from backend.base.definitions import (DiscoverItemData,
                                      DiscoverMatchData, DiscoverSource)
from backend.base.helpers import AsyncSession
from backend.implementations.discover import fetch_getcomics_discover_page
from backend.implementations.matching import match_title
from backend.implementations.volumes import Library


class DiscoverSources:
    """Registry of discover-source implementations, mirroring
    `backend.features.pull_list.WeeklyReleaseSources`.
    """

    sources: List[Type[DiscoverSource]] = []

    @classmethod
    def register(
        cls,
        source: Type[DiscoverSource]
    ) -> Type[DiscoverSource]:
        cls.sources.append(source)
        return source

    @classmethod
    def get_active(cls) -> List[DiscoverSource]:
        return [source() for source in cls.sources]


@DiscoverSources.register
class GetComicsDiscover(DiscoverSource):
    """GetComics' own recent-posts listing. GetComics is already
    Kapowarr's built-in, configuration-free download source (see
    `getcomics.py`) -- it doubles as a configuration-free discover source
    too, so Discover works with no extra setup out of the box.
    """

    async def fetch(
        self,
        session: AsyncSession,
        page: int = 1
    ) -> Tuple[List[DiscoverItemData], int]:
        return await fetch_getcomics_discover_page(session, page)


async def _fetch_discover_page(page: int) -> Tuple[List[DiscoverItemData], int]:
    """Fetch the given page from every registered discover source.

    Args:
        page (int): The page to fetch.

    Returns:
        Tuple[List[DiscoverItemData], int]: The combined items, deduplicated
            by link across sources, and the highest page count any source
            reported (so paging keeps working as far as the deepest
            source, even once shallower sources run dry).
    """
    async with AsyncSession() as session:
        responses = await gather(*(
            source.fetch(session, page)
            for source in DiscoverSources.get_active()
        ))

    seen = set()
    items: List[DiscoverItemData] = []
    max_page = 1
    for page_items, source_max_page in responses:
        max_page = max(max_page, source_max_page)
        for item in page_items:
            if item['link'] in seen:
                continue
            seen.add(item['link'])
            items.append(item)

    return items, max_page


def match_discover_items_to_library(
    items: List[DiscoverItemData],
    volumes: List[Dict[str, Any]]
) -> List[DiscoverMatchData]:
    """Cross-reference discovered items against library volumes, matching
    on title, so the Discover page can show "already in library" vs
    "not yet added" per item.

    Kept as a pure function of its two arguments (no DB access, no network)
    so it can be unit tested in isolation, mirroring
    `pull_list.match_releases_to_library()`.

    Args:
        items (List[DiscoverItemData]): The discovered items, e.g. from
            `_fetch_discover_page()`.
        volumes (List[Dict[str, Any]]): The library's volumes, as returned
            by `Library.get_public_volumes()`. Unlike the weekly pull list
            (which only cares about monitored volumes), Discover checks
            against the whole library -- an unmonitored volume that's
            already added is still "already in library", not "not yet
            added".

    Returns:
        List[DiscoverMatchData]: One entry per discovered item, each
            carrying the matched volume's id/title if one was found, or
            `None` for both if the item isn't in the library yet.
    """
    matches: List[DiscoverMatchData] = []
    for item in items:
        matched_volume = next(
            (
                volume for volume in volumes
                if item['series'] and match_title(volume['title'], item['series'])
            ),
            None
        )

        matches.append({
            **item,
            'volume_id': matched_volume['id'] if matched_volume else None,
            'volume_title': matched_volume['title'] if matched_volume else None
        })

    return matches


def get_discover_feed(page: int = 1) -> Tuple[List[DiscoverMatchData], int]:
    """Fetch one page of GetComics' recent releases and cross-reference it
    against the library.

    Args:
        page (int, optional): The page to fetch (1-indexed). Defaults to 1.

    Returns:
        Tuple[List[DiscoverMatchData], int]: The matched items for this
            page, and the total number of pages available.
    """
    items, max_page = run(_fetch_discover_page(page))
    volumes = Library.get_public_volumes()
    matches = match_discover_items_to_library(items, volumes)
    return matches, max_page
