# -*- coding: utf-8 -*-

"""
Weekly pull list: cross-reference this week's new comic releases against
the volumes already monitored in the library, and surface the matches
("wanted additions") for a quick manual look/download decision.

Mirrors `backend.features.search`'s `SearchSources` registry: a weekly-
release source registers itself with `WeeklyReleaseSources.register()`, so
a second source (e.g. a ComicVine weekly-issues source) can be added later
without this module's orchestration -- `check_weekly_pull_list()`,
`match_releases_to_library()` -- needing to change. The release data
itself stays replaceable the same way search sources are: this module
never imports `weekly_releases.py`'s fetch function directly except from
inside the one adapter class registered below.
"""

from asyncio import gather, run
from time import time
from typing import Any, Dict, List, Type

from backend.base.definitions import (LibraryFilter, WeeklyReleaseData,
                                      WeeklyReleaseSource)
from backend.base.helpers import AsyncSession
from backend.base.logging import LOGGER
from backend.implementations.matching import match_title
from backend.implementations.volumes import Library
from backend.implementations.weekly_releases import \
    fetch_getcomics_weekly_releases
from backend.internals.db import get_db


class WeeklyReleaseSources:
    """Registry of weekly-release-source implementations, mirroring
    `backend.features.search.SearchSources`.
    """

    sources: List[Type[WeeklyReleaseSource]] = []

    @classmethod
    def register(
        cls,
        source: Type[WeeklyReleaseSource]
    ) -> Type[WeeklyReleaseSource]:
        cls.sources.append(source)
        return source

    @classmethod
    def get_active(cls) -> List[WeeklyReleaseSource]:
        return [source() for source in cls.sources]


@WeeklyReleaseSources.register
class GetComicsWeeklyReleases(WeeklyReleaseSource):
    """GetComics' own "weekly pull list" post. GetComics is already
    Kapowarr's built-in, configuration-free download source (see
    `getcomics.py`) -- it doubles as a configuration-free release source
    too, so a pull list works with no extra setup out of the box.
    """

    async def fetch(self, session: AsyncSession) -> List[WeeklyReleaseData]:
        return await fetch_getcomics_weekly_releases(session)


async def _fetch_all_weekly_releases() -> List[WeeklyReleaseData]:
    """Fetch this week's releases from every registered source.

    Returns:
        List[WeeklyReleaseData]: The combined releases, deduplicated by
            (series, issue number) across sources.
    """
    async with AsyncSession() as session:
        responses = await gather(*(
            source.fetch(session)
            for source in WeeklyReleaseSources.get_active()
        ))

    seen = set()
    releases: List[WeeklyReleaseData] = []
    for response in responses:
        for release in response:
            key = (release['series'].lower(), release['issue_number'])
            if key in seen:
                continue
            seen.add(key)
            releases.append(release)

    return releases


def match_releases_to_library(
    releases: List[WeeklyReleaseData],
    monitored_volumes: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """Cross-reference weekly releases against monitored library volumes,
    matching on volume title.

    Kept as a pure function of its two arguments (no DB access) so it can
    be unit tested without a database, and so the two lookups it combines
    -- "what released this week" and "what's monitored" -- can each be
    swapped/extended independently later.

    Args:
        releases (List[WeeklyReleaseData]): This week's releases, e.g. from
            `_fetch_all_weekly_releases()`.
        monitored_volumes (List[Dict[str, Any]]): Monitored volumes, as
            returned by
            `Library.get_public_volumes(filter=LibraryFilter.MONITORED)`.

    Returns:
        List[Dict[str, Any]]: One entry per release that matched a
            monitored volume -- the "wanted additions" for this week.
    """
    matches: List[Dict[str, Any]] = []
    for release in releases:
        for volume in monitored_volumes:
            if not match_title(volume['title'], release['series']):
                continue

            matches.append({
                'volume_id': volume['id'],
                'volume_title': volume['title'],
                'issue_number': release['issue_number'],
                'release_title': release['series'],
                'year': release['year'],
                'source': release['source'],
                'link': release['link']
            })
            # A release is credited to the first monitored volume it
            # matches; volume titles are unique enough in practice that a
            # release matching two different monitored volumes would be a
            # false positive worth investigating, not a legitimate
            # double-match.
            break

    return matches


def check_weekly_pull_list() -> List[Dict[str, Any]]:
    """Fetch this week's releases from every registered source, cross-
    reference them against the monitored library, and persist the matches
    as the current pull list (replacing whatever was stored by the
    previous check).

    Returns:
        List[Dict[str, Any]]: The matches that were stored.
    """
    releases = run(_fetch_all_weekly_releases())
    monitored_volumes = Library.get_public_volumes(filter=LibraryFilter.MONITORED)
    matches = match_releases_to_library(releases, monitored_volumes)

    checked_at = round(time())
    cursor = get_db()
    cursor.execute("DELETE FROM pull_list_entries;")
    cursor.executemany(
        """
        INSERT INTO pull_list_entries(
            volume_id, issue_number, release_title, year,
            source, link, checked_at
        )
        VALUES (
            :volume_id, :issue_number, :release_title, :year,
            :source, :link, :checked_at
        );
        """,
        (
            {**match, 'checked_at': checked_at}
            for match in matches
        )
    )

    LOGGER.info(
        "Weekly pull-list check found %d matched release(s)", len(matches)
    )
    return matches


def get_pull_list() -> List[Dict[str, Any]]:
    """Get the currently stored pull list -- the result of the most recent
    weekly check.

    Returns:
        List[Dict[str, Any]]: The pull-list entries, joined with the
            matched volume's current title.
    """
    return get_db().execute(
        """
        SELECT
            p.id, p.volume_id, v.title AS volume_title,
            p.issue_number, p.release_title, p.year,
            p.source, p.link, p.checked_at
        FROM pull_list_entries p
        INNER JOIN volumes v
            ON p.volume_id = v.id
        ORDER BY p.checked_at DESC, v.title, p.id;
        """
    ).fetchalldict()
