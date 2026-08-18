# -*- coding: utf-8 -*-

"""
GetComics Discover: browse GetComics' recent releases inside Kapowarr,
cross-referenced against the library so already-added series can be excluded
from the discovery feed.

Mirrors `backend.features.pull_list`'s relationship with
`backend.implementations.weekly_releases` (itself mirroring
`backend.features.search`'s `SearchSources` registry): a discover source
registers itself with `DiscoverSources.register()`, so a second source
could be added later without this module's orchestration needing to change.

Unlike the weekly pull list, Discover is a live browse view rather than a
scheduled/persisted check. The recommendation layer is deliberately
explainable and deterministic: it ranks recent, not-yet-owned releases by
strong title/franchise overlap with the current library. Richer metadata
signals such as creators/characters can be added when those fields are
persisted locally; this first pass never makes extra ComicVine calls just to
manufacture recommendations.
"""

from asyncio import gather, run
from re import sub
from typing import Any, Dict, List, Tuple, Type

from backend.base.definitions import (DiscoverItemData,
                                      DiscoverMatchData, DiscoverSource)
from backend.base.helpers import AsyncSession
from backend.implementations.discover import fetch_getcomics_discover_page
from backend.implementations.matching import match_title
from backend.implementations.volumes import Library

RECOMMENDATION_DISCOVER_PAGES = 3
"How many recent Discover pages are sampled for the For You view."

RECOMMENDATION_MIN_SCORE = 5
"Minimum deterministic title-overlap score required for a recommendation."

_RECOMMENDATION_STOPWORDS = {
    'a', 'an', 'and', 'annual', 'book', 'books', 'collection', 'comic',
    'comics', 'complete', 'edition', 'for', 'in', 'of', 'omnibus', 'on',
    'the', 'to', 'vol', 'volume', 'with'
}


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
    """GetComics' own recent-posts listing."""

    async def fetch(
        self,
        session: AsyncSession,
        page: int = 1
    ) -> Tuple[List[DiscoverItemData], int]:
        return await fetch_getcomics_discover_page(session, page)


async def _fetch_discover_page(page: int) -> Tuple[List[DiscoverItemData], int]:
    """Fetch the given page from every registered discover source."""
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


async def _fetch_recent_discover_items(
    page_limit: int = RECOMMENDATION_DISCOVER_PAGES
) -> Tuple[List[DiscoverItemData], int]:
    """Fetch a bounded recent window for the recommendation view.

    The first page tells us the source's real maximum page count. Remaining
    pages in the bounded window are then fetched in parallel. Results are
    deduplicated by release link while preserving page order.
    """
    first_items, max_page = await _fetch_discover_page(1)
    pages_to_fetch = min(max_page, max(1, page_limit))

    page_results = []
    if pages_to_fetch > 1:
        page_results = await gather(*(
            _fetch_discover_page(page)
            for page in range(2, pages_to_fetch + 1)
        ))

    seen = set()
    items: List[DiscoverItemData] = []
    for page_items in [first_items, *(result[0] for result in page_results)]:
        for item in page_items:
            if item['link'] in seen:
                continue
            seen.add(item['link'])
            items.append(item)

    return items, pages_to_fetch


def match_discover_items_to_library(
    items: List[DiscoverItemData],
    volumes: List[Dict[str, Any]]
) -> List[DiscoverMatchData]:
    """Cross-reference discovered items against all library volumes."""
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


def _recommendation_tokens(title: str) -> Tuple[str, ...]:
    """Return meaningful lowercase title tokens for deterministic matching.

    Strip explicit volume numbering before normal tokenisation so a title
    such as ``Batman Vol. 2`` does not gain a false numeric franchise token,
    while real numeric titles such as ``100 Bullets`` keep their number.
    """
    without_volume_number = sub(
        r'\b(?:vol|volume)\.?\s*\d+\b',
        ' ',
        title.lower()
    )
    cleaned = sub(r'[^a-z0-9]+', ' ', without_volume_number).strip()
    return tuple(
        token for token in cleaned.split()
        if token not in _RECOMMENDATION_STOPWORDS
        and (len(token) >= 3 or token.isdigit())
    )


def _score_related_title(candidate: str, library_title: str) -> int:
    """Score a candidate title against one title already in the library.

    The threshold is intentionally conservative. A single generic word is
    not enough; strong franchise/title stems, shared multi-token phrases, or
    a distinctive one-word series embedded in a longer title are.
    """
    candidate_tokens = _recommendation_tokens(candidate)
    library_tokens = _recommendation_tokens(library_title)
    if not candidate_tokens or not library_tokens:
        return 0

    candidate_set = set(candidate_tokens)
    library_set = set(library_tokens)
    shared = candidate_set & library_set
    if not shared:
        return 0

    score = len(shared) * 3

    # Sharing the opening word is meaningful only when there is already
    # more than one token of evidence. This avoids recommending unrelated
    # multi-word titles such as "Dark Crisis" from "Dark Horse Presents".
    if (
        len(shared) >= 2
        and candidate_tokens[0] == library_tokens[0]
    ):
        score += 2

    candidate_normal = ' '.join(candidate_tokens)
    library_normal = ' '.join(library_tokens)
    if (
        candidate_normal.startswith(f'{library_normal} ')
        or library_normal.startswith(f'{candidate_normal} ')
    ):
        score += 4

    if (
        len(library_tokens) == 1
        and library_tokens[0] in candidate_set
        and len(library_tokens[0]) >= 4
    ):
        score += 3

    return score


def recommend_discover_items(
    items: List[DiscoverItemData],
    volumes: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """Rank recent, not-owned releases using explainable library similarity.

    Every returned row carries the exact library title that caused it to be
    recommended, making the ranking inspectable instead of opaque.
    """
    matched_items = match_discover_items_to_library(items, volumes)
    recommendations: List[Dict[str, Any]] = []

    for item in matched_items:
        if item['volume_id'] is not None or not item['series']:
            continue

        best_score = 0
        best_volume = None
        for volume in volumes:
            score = _score_related_title(item['series'], volume['title'])
            if score > best_score:
                best_score = score
                best_volume = volume

        if best_volume is None or best_score < RECOMMENDATION_MIN_SCORE:
            continue

        recommendations.append({
            **item,
            'recommendation_score': best_score,
            'recommendation_reason': (
                f"Because you collect {best_volume['title']}"
            ),
            'related_volume_id': best_volume['id'],
            'related_volume_title': best_volume['title']
        })

    recommendations.sort(key=lambda item: (
        -item['recommendation_score'],
        (item['series'] or item['display_title']).lower()
    ))
    return recommendations


def get_discover_feed(page: int = 1) -> Tuple[List[DiscoverMatchData], int]:
    """Return one chronological page containing only not-yet-owned releases.

    The cross-reference still happens server-side so ownership is decided by
    Kapowarr's normal title matcher. Already-owned volumes are removed from
    the public Discover feed rather than merely being labelled in the UI.
    """
    items, max_page = run(_fetch_discover_page(page))
    volumes = Library.get_public_volumes()
    matches = match_discover_items_to_library(items, volumes)
    return [item for item in matches if item['volume_id'] is None], max_page


def get_recommended_discover_feed() -> Tuple[List[Dict[str, Any]], int]:
    """Return ranked recommendations from a bounded window of recent posts."""
    items, pages_scanned = run(_fetch_recent_discover_items())
    volumes = Library.get_public_volumes()
    return recommend_discover_items(items, volumes), pages_scanned
