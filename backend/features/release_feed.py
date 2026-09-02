# -*- coding: utf-8 -*-

"""Watching what the indexers publish, instead of asking about every issue.

Kapowarr only ever asked indexers questions: one search per volume, then one
per missing issue. That is the expensive direction. An hour of it on
2026-09-02 reached eight volumes out of thousands, spent every indexer's
daily quota doing so, and found five things.

Newznab and Torznab both answer `t=search` with no `q` by handing back their
most recent releases. One request per indexer therefore covers the *entire*
library -- every volume, every missing issue -- for the cost of one search.
At a quarter-hourly poll that is 96 requests a day per indexer, against the
thousands a targeted sweep of the same library needs, and it sees a new
release within fifteen minutes of it being posted rather than whenever the
rotation next comes round.

This is the standing "RSS sync" of the rest of the *arr suite, and Kapowarr
had no equivalent -- not removed, never built: the application began around
GetComics, which has no feed to sync from, and the Newznab and Torznab
support that does came later without anyone revisiting the model. Silas, on
being told: "Omg, we don't have rss sync!!!!!! I'm honestly flabbergasted."

Deliberate boundaries
---------------------

**It never widens the library.** A release is only interesting if it belongs
to a volume already in the library, is missing, and is monitored. Finding new
series is Discover's job.

**It keeps no state.** Nothing is remembered between polls, because nothing
needs to be: an issue that gets downloaded has a file and stops matching, a
link already queued is refused by the queue, and a link that failed is on the
blocklist. A "seen" table would be a second thing to keep correct for no gain.

**It decides nothing for itself.** Whether a release matches a volume is
`match_parsed_to_library_volume`'s answer -- the same one the watched-folder
importer uses -- and whether it is worth downloading is the ordinary search
matcher's, reached through `manual_search` with the release already in hand.
"""

from __future__ import annotations

from asyncio import gather, run
from typing import Any, Callable, Dict, List, Set, Tuple, Union

from backend.base.definitions import SearchResultData
from backend.base.helpers import AsyncSession, check_overlapping_issues
from backend.base.logging import LOGGER
from backend.features.watched_folder_import import (
    LibraryIndex, match_parsed_to_library_volume)
from backend.implementations.indexers_core import Indexers, search_indexer
from backend.implementations.torznab import (TorznabIndexers,
                                             search_torznab_indexer)

FEED_SYNC_INTERVAL_SECONDS = 900
"The interval the task runs at, seeded into `task_intervals`."


class FeedSyncSummary(dict):
    """What one poll did. A plain dict so it stays trivially serialisable for
    the task message and the tests."""


def _empty_summary() -> FeedSyncSummary:
    return FeedSyncSummary(
        indexers=0, releases=0, matched=0, queued=0, unknown=0, ambiguous=0,
        already_held=0
    )


async def _fetch_all(session: AsyncSession) -> List[SearchResultData]:
    """Ask every enabled indexer for what it has published lately.

    Args:
        session (AsyncSession): The session to ask through.

    Returns:
        List[SearchResultData]: Every release returned, deduplicated by link.
    """
    calls = []
    for indexer in Indexers.get_enabled():
        calls.append(search_indexer(session, indexer, ''))
    for indexer in TorznabIndexers.get_enabled():
        calls.append(search_torznab_indexer(session, indexer, ''))

    if not calls:
        return []

    responses = await gather(*calls, return_exceptions=True)

    releases: List[SearchResultData] = []
    seen: Set[str] = set()
    for response in responses:
        if isinstance(response, BaseException):
            # One indexer being down, or out of quota, must not cost the poll
            # the others' answers.
            LOGGER.warning('An indexer feed could not be read: %s', response)
            continue

        for release in response:
            link = release.get('link')
            if link and link not in seen:
                seen.add(link)
                releases.append(release)

    return releases


def fetch_recent_releases() -> Tuple[List[SearchResultData], int]:
    """Read every enabled indexer's feed.

    Returns:
        Tuple[List[SearchResultData], int]: The releases, and how many
            indexers were asked.
    """
    indexer_count = (
        len(Indexers.get_enabled()) + len(TorznabIndexers.get_enabled())
    )
    if not indexer_count:
        return [], 0

    async def fetch():
        async with AsyncSession() as session:
            return await _fetch_all(session)

    return run(fetch()), indexer_count


def wanted_issues_of(volume_id: int) -> List[Tuple[int, float]]:
    """The issues of a volume that are monitored and have no file.

    Args:
        volume_id (int): The volume to ask about.

    Returns:
        List[Tuple[int, float]]: Issue ID and calculated issue number.
    """
    from backend.implementations.volumes import Volume

    volume = Volume(volume_id)
    if not volume.get_data().monitored:
        return []
    return volume.get_open_issues()


def releases_worth_grabbing(
    releases: List[SearchResultData],
    index: Union[LibraryIndex, None] = None,
    counts: Union[Dict[str, int], None] = None
) -> List[Tuple[str, int, None]]:
    """Work out which of these releases the library actually wants.

    Args:
        releases (List[SearchResultData]): What the feeds returned.

        index (Union[LibraryIndex, None], optional): A prepared library index,
            built once for the whole poll. Defaults to None, meaning build one.

        counts (Union[Dict[str, int], None], optional): Filled in with why
            releases were passed over, for the summary. Defaults to None.

    Returns:
        List[Tuple[str, int, None]]: `(link, volume id, None)` for each
            release to queue, in feed order.
    """
    from backend.features.search import manual_search

    if index is None:
        index = LibraryIndex()

    if counts is None:
        counts = {}
    for key in ('unknown', 'ambiguous', 'already_held'):
        counts.setdefault(key, 0)

    wanted: Dict[int, List[Tuple[int, float]]] = {}
    to_queue: List[Tuple[str, int, None]] = []
    already: Set[str] = set()

    for release in releases:
        # The release arrives already parsed -- `SearchResultData` extends
        # `FilenameData` -- so match those fields rather than re-deriving
        # them from the display title, which is a different string.
        volume_id = match_parsed_to_library_volume(
            release, index, release.get('display_title', ''), quiet=True)
        if volume_id is None:
            counts['unknown'] += 1
            continue

        if volume_id not in wanted:
            wanted[volume_id] = wanted_issues_of(volume_id)
        open_issues = wanted[volume_id]
        if not open_issues:
            counts['already_held'] += 1
            continue

        # The ordinary matcher decides, with the release already in hand, so
        # no indexer is asked anything here.
        matched = [
            r
            for r in manual_search(volume_id, None, already_fetched=[release])
            if r['match']
        ]
        if not matched:
            continue

        covers = matched[0].get('issue_number')
        if covers is not None and not any(
            check_overlapping_issues(number, covers)
            for _, number in open_issues
        ):
            # It matches the volume but only issues that are already here.
            counts['already_held'] += 1
            continue

        link = matched[0]['link']
        if link in already:
            continue
        already.add(link)
        to_queue.append((link, volume_id, None))

    return to_queue


def poll_release_feeds(
    should_stop: Union[Callable[[], bool], None] = None
) -> FeedSyncSummary:
    """Do one pass: read the feeds, and queue whatever the library wants.

    Args:
        should_stop (Union[Callable[[], bool], None], optional): Polled before
            queueing, so a stop takes effect without leaving half a pass
            enqueued. Defaults to None.

    Returns:
        FeedSyncSummary: What the pass did.
    """
    summary = _empty_summary()

    releases, summary['indexers'] = fetch_recent_releases()
    summary['releases'] = len(releases)
    if not releases:
        # Logged like any other outcome. A feed that comes back empty is the
        # one most worth saying out loud -- it is what four hours of silent
        # polls looked like on 2026-09-02 -- so it must not be the one case
        # that returns before the summary.
        LOGGER.info('%s', describe_sync(summary))
        return summary

    to_queue = releases_worth_grabbing(releases, counts=summary)
    summary['matched'] = len(to_queue)

    if should_stop is not None and should_stop():
        LOGGER.info('Feed sync stopped before queueing')
        return summary

    if to_queue:
        from backend.features.download_queue import DownloadHandler

        try:
            DownloadHandler().add_multiple(
                (link, volume_id, issue_id, False)
                for link, volume_id, issue_id in to_queue
            )
            summary['queued'] = len(to_queue)
        except Exception:
            LOGGER.exception('Could not queue what the feeds turned up: ')

    LOGGER.info('%s', describe_sync(summary))
    return summary


def describe_sync(summary: FeedSyncSummary) -> str:
    """A one-line, user-facing description of a pass, for the task message.

    Args:
        summary (FeedSyncSummary): What the pass did.

    Returns:
        str: The description.
    """
    if not summary['indexers']:
        return 'No indexers to read a feed from'

    described = (
        f"Read {summary['releases']} recent release(s) from "
        f"{summary['indexers']} indexer(s) · {summary['queued']} queued"
    )

    # Why the rest were passed over, so a poll that queues nothing says
    # whether that is because the library is complete or because nothing it
    # saw belongs to the library at all.
    passed_over = [
        (summary.get('already_held', 0), 'already held'),
        (summary.get('unknown', 0), 'not in the library'),
    ]
    detail = ', '.join(
        f'{count} {reason}' for count, reason in passed_over if count
    )
    if detail:
        described += f' ({detail})'

    return described
