# -*- coding: utf-8 -*-

"""Publisher-aware weekly release calendar and automation rules."""

from asyncio import TimeoutError as AsyncTimeoutError
from asyncio import gather, run, wait_for
from datetime import date, datetime, timedelta
from time import time
from typing import Any, Dict, List, Tuple, Type, Union

from backend.base.definitions import (MonitorScheme, WeeklyReleaseData,
                                      WeeklyReleaseSource)
from backend.base.file_extraction import extract_issue_number
from backend.base.helpers import AsyncSession, force_range
from backend.base.logging import LOGGER
from backend.features.search import auto_search
from backend.implementations.matching import match_title
from backend.implementations.volumes import Library, Volume
from backend.implementations.weekly_releases import (
    fetch_getcomics_weekly_releases, fetch_mylar_weekly_releases)
from backend.internals.db import get_db

DownloadTuple = Tuple[str, int, Union[int, None]]
WEEKLY_RELEASE_FETCH_TIMEOUT = 45.0


def _monday(value: date) -> date:
    return value - timedelta(days=value.weekday())


class WeeklyReleaseSources:
    """Registry for independent release metadata providers."""

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
class MylarWeeklyReleases(WeeklyReleaseSource):
    """Publisher-aware calendar feed used by Mylar's weekly pull list."""

    async def fetch(
        self,
        session: AsyncSession,
        requested_date: Union[date, None] = None
    ) -> List[WeeklyReleaseData]:
        return await fetch_mylar_weekly_releases(
            session, requested_date or date.today()
        )


@WeeklyReleaseSources.register
class GetComicsWeeklyReleases(WeeklyReleaseSource):
    """GetComics availability overlay for the current release week."""

    async def fetch(
        self,
        session: AsyncSession,
        requested_date: Union[date, None] = None
    ) -> List[WeeklyReleaseData]:
        if (
            requested_date is not None
            and _monday(requested_date) != _monday(date.today())
        ):
            return []
        return await fetch_getcomics_weekly_releases(session)


def _release_key(release: Dict[str, Any]) -> str:
    issue_id = release.get('comicvine_issue_id')
    if issue_id:
        return f'comicvine:{issue_id}'
    return '|'.join((
        str(release.get('publisher') or '').lower(),
        str(
            release.get('release_title') or release.get('series') or ''
        ).lower(),
        str(release.get('issue_number') or '').lower(),
        str(release.get('release_date') or release.get('week_start') or '')
    ))[:255]


def _merge_release_sources(
    responses: List[List[WeeklyReleaseData]]
) -> List[WeeklyReleaseData]:
    """Merge catalogue rows and attach GetComics as availability metadata."""
    releases: List[WeeklyReleaseData] = []
    releases_by_title: Dict[
        Tuple[str, Union[str, None]], List[WeeklyReleaseData]
    ] = {}
    availability: List[WeeklyReleaseData] = []
    seen_catalogue = set()

    for response in responses:
        for release in response:
            if release.get('availability_source'):
                availability.append(release)
                continue
            key = (
                release.get('comicvine_issue_id'),
                release['series'].strip().lower(),
                release['issue_number'],
                release.get('year')
            )
            if key in seen_catalogue:
                continue
            seen_catalogue.add(key)
            releases.append(release)
            title_key = (
                release['series'].strip().lower(), release['issue_number']
            )
            releases_by_title.setdefault(title_key, []).append(release)

    for available in availability:
        key = (available['series'].strip().lower(), available['issue_number'])
        candidates = releases_by_title.get(key, [])
        match = next((
            release for release in candidates
            if release['week_start'] == available['week_start']
        ), candidates[0] if candidates else None)
        if match is None:
            releases.append(available)
            continue
        match['availability_source'] = available['availability_source']
        match['availability_link'] = available['availability_link']

    return releases


async def _fetch_release_source(
    source: WeeklyReleaseSource,
    session: AsyncSession,
    week: date
) -> List[WeeklyReleaseData]:
    """Bound one release-source request so one host cannot spin forever."""
    try:
        return await wait_for(
            source.fetch(session, week),
            timeout=WEEKLY_RELEASE_FETCH_TIMEOUT
        )
    except AsyncTimeoutError:
        LOGGER.warning(
            'Weekly release source %s timed out for week %s',
            type(source).__name__, week.isoformat()
        )
        return []


async def _fetch_all_weekly_releases() -> List[WeeklyReleaseData]:
    """Fetch nine navigable weeks, including four past and four future."""
    start = _monday(date.today())
    weeks = [start + timedelta(weeks=offset) for offset in range(-4, 5)]
    sources = WeeklyReleaseSources.get_active()

    async with AsyncSession() as session:
        responses = await gather(*(
            _fetch_release_source(source, session, week)
            for source in sources
            for week in weeks
        ))
    return _merge_release_sources(responses)


def match_releases_to_library(
    releases: List[WeeklyReleaseData],
    volumes: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """Attach a library volume when ComicVine ID or title/year matches."""
    entries: List[Dict[str, Any]] = []
    for release in releases:
        matched_volume = None
        comicvine_id = release.get('comicvine_volume_id')
        if comicvine_id:
            matched_volume = next((
                volume for volume in volumes
                if volume.get('comicvine_id') == comicvine_id
            ), None)

        if matched_volume is None:
            matched_volume = next((
                volume for volume in volumes
                if match_title(volume['title'], release['series'])
                and (
                    release.get('year') is None
                    or volume.get('year') is None
                    or volume.get('year') == release.get('year')
                )
            ), None)

        entries.append({
            'volume_id': matched_volume['id'] if matched_volume else None,
            'volume_title': (
                matched_volume['title'] if matched_volume else None
            ),
            'volume_monitored': bool(
                matched_volume.get('monitored')
            ) if matched_volume else False,
            'issue_id': None,
            'issue_number': release['issue_number'],
            'release_title': release['series'],
            'publisher': release.get('publisher'),
            'release_date': release.get('release_date'),
            'cover_date': release.get('cover_date'),
            'week_start': release['week_start'],
            'year': release['year'],
            'comicvine_volume_id': release.get('comicvine_volume_id'),
            'comicvine_issue_id': release.get('comicvine_issue_id'),
            'source': release['source'],
            'link': release['link'],
            'availability_source': release.get('availability_source'),
            'availability_link': release.get('availability_link')
        })
    return entries


def _find_issue_id(entry: Dict[str, Any]) -> Union[int, None]:
    if not entry.get('volume_id'):
        return None
    cursor = get_db()
    comicvine_issue_id = entry.get('comicvine_issue_id')
    if comicvine_issue_id:
        issue_id = cursor.execute(
            "SELECT id FROM issues WHERE volume_id = ? AND comicvine_id = ?;",
            (entry['volume_id'], comicvine_issue_id)
        ).exists()
        if issue_id:
            return issue_id

    calculated = extract_issue_number(str(entry.get('issue_number') or ''))
    if calculated is None:
        return None
    return cursor.execute(
        """
        SELECT id FROM issues
        WHERE volume_id = ? AND calculated_issue_number = ?
        LIMIT 1;
        """,
        (entry['volume_id'], force_range(calculated)[0])
    ).exists()


def check_weekly_pull_list() -> List[Dict[str, Any]]:
    """Refresh the full catalogue while preserving it on source failure."""
    releases = run(_fetch_all_weekly_releases())
    current_week = _monday(date.today()).isoformat()
    current_catalogue = [
        release
        for release in releases
        if release.get('week_start') == current_week
        and release.get('publisher')
    ]
    if not current_catalogue:
        raise RuntimeError(
            'No current-week publisher releases were returned; '
            'the previous pull list was kept'
        )

    entries = match_releases_to_library(
        releases, Library.get_public_volumes()
    )
    checked_at = round(time())
    for entry in entries:
        entry['issue_id'] = _find_issue_id(entry)
        entry['checked_at'] = checked_at

    cursor = get_db()
    with cursor:
        cursor.execute('DELETE FROM pull_list_entries;')
        cursor.executemany(
            """
            INSERT INTO pull_list_entries(
                volume_id, issue_id, comicvine_volume_id, comicvine_issue_id,
                issue_number, release_title, publisher, release_date,
                cover_date, week_start, year, source, link,
                availability_source, availability_link, checked_at
            ) VALUES (
                :volume_id, :issue_id, :comicvine_volume_id,
                :comicvine_issue_id, :issue_number, :release_title,
                :publisher, :release_date, :cover_date, :week_start, :year,
                :source, :link, :availability_source, :availability_link,
                :checked_at
            );
            """,
            entries
        )

    LOGGER.info('Weekly release calendar stored %d release(s)', len(entries))
    return entries


def get_pull_list(week_start: Union[str, None] = None) -> List[Dict[str, Any]]:
    """Return one release week, defaulting to the current Monday."""
    selected_week = week_start or _monday(date.today()).isoformat()
    try:
        datetime.strptime(selected_week, '%Y-%m-%d')
    except ValueError:
        selected_week = _monday(date.today()).isoformat()

    return get_db().execute(
        """
        SELECT
            p.*, v.title AS volume_title, v.monitored AS volume_monitored,
            h.success AS automation_success,
            h.message AS automation_message
        FROM pull_list_entries p
        LEFT JOIN volumes v ON p.volume_id = v.id
        LEFT JOIN publisher_automation_history h
          ON h.release_key = CASE
            WHEN p.comicvine_issue_id IS NOT NULL
              THEN 'comicvine:' || p.comicvine_issue_id
            ELSE lower(COALESCE(p.publisher, '')) || '|' ||
                 lower(p.release_title) || '|' ||
                 lower(COALESCE(p.issue_number, '')) || '|' ||
                 COALESCE(p.release_date, p.week_start)
          END
          AND h.action = 'auto_search'
        WHERE p.week_start = ?
        ORDER BY COALESCE(p.release_date, p.week_start),
                 COALESCE(p.publisher, ''), p.release_title, p.id;
        """,
        (selected_week,)
    ).fetchalldict()


def get_publishers() -> List[Dict[str, Any]]:
    """Return known publishers with global and per-week release counts."""
    cursor = get_db()
    publishers = cursor.execute(
        """
        SELECT
            p.publisher,
            s.root_folder_id,
            COALESCE(s.auto_search, 0) AS auto_search,
            COUNT(*) AS release_count
        FROM pull_list_entries p
        LEFT JOIN publisher_subscriptions s
          ON p.publisher = s.publisher COLLATE NOCASE
        WHERE p.publisher IS NOT NULL AND p.publisher != ''
        GROUP BY p.publisher
        ORDER BY p.publisher COLLATE NOCASE;
        """
    ).fetchalldict()
    counts_by_publisher: Dict[str, Dict[str, int]] = {}
    for row in cursor.execute(
        """
        SELECT publisher, week_start, COUNT(*) AS release_count
        FROM pull_list_entries
        WHERE publisher IS NOT NULL AND publisher != ''
        GROUP BY publisher, week_start;
        """
    ).fetchalldict():
        counts_by_publisher.setdefault(row['publisher'], {})[
            row['week_start']
        ] = row['release_count']
    for publisher in publishers:
        publisher['release_counts'] = counts_by_publisher.get(
            publisher['publisher'], {}
        )
    return publishers


def set_publisher_subscription(
    publisher: str,
    root_folder_id: int,
    auto_search_enabled: bool
) -> Dict[str, Any]:
    """Create or replace an auto-add/auto-grab publisher rule."""
    get_db().execute(
        """
        INSERT INTO publisher_subscriptions(
            publisher, root_folder_id, auto_search
        ) VALUES (?, ?, ?)
        ON CONFLICT(publisher) DO UPDATE SET
            root_folder_id = excluded.root_folder_id,
            auto_search = excluded.auto_search;
        """,
        (publisher.strip(), root_folder_id, auto_search_enabled)
    )
    return {
        'publisher': publisher.strip(),
        'root_folder_id': root_folder_id,
        'auto_search': auto_search_enabled
    }


def delete_publisher_subscription(publisher: str) -> None:
    get_db().execute(
        """
        DELETE FROM publisher_subscriptions
        WHERE publisher = ? COLLATE NOCASE;
        """,
        (publisher,)
    )


def _add_or_monitor_entry(
    entry: Dict[str, Any],
    root_folder_id: int
) -> Tuple[int, Union[int, None]]:
    volume_id = entry.get('volume_id')
    if volume_id is None:
        comicvine_id = entry.get('comicvine_volume_id')
        if not comicvine_id:
            raise RuntimeError('Release has no ComicVine volume ID')
        volume_id = get_db().execute(
            'SELECT id FROM volumes WHERE comicvine_id = ? LIMIT 1;',
            (comicvine_id,)
        ).exists()
        if volume_id is None:
            volume_id = Library.add(
                comicvine_id, root_folder_id, True, MonitorScheme.ALL,
                True, auto_search=False
            )
        entry['volume_id'] = volume_id
    Volume(volume_id).update({'monitored': True})

    issue_id = _find_issue_id(entry)
    if issue_id:
        Library.get_issue(issue_id).update({'monitored': True})
    cursor = get_db()
    if entry.get('id') is not None:
        cursor.execute(
            """
            UPDATE pull_list_entries
            SET volume_id = ?, issue_id = ?
            WHERE id = ?;
            """,
            (volume_id, issue_id, entry['id'])
        )
    elif entry.get('comicvine_issue_id') is not None:
        cursor.execute(
            """
            UPDATE pull_list_entries
            SET volume_id = ?, issue_id = ?
            WHERE comicvine_issue_id = ?;
            """,
            (volume_id, issue_id, entry['comicvine_issue_id'])
        )
    else:
        cursor.execute(
            """
            UPDATE pull_list_entries
            SET volume_id = ?, issue_id = ?
            WHERE release_title = ? AND issue_number IS ? AND week_start = ?;
            """,
            (
                volume_id, issue_id, entry['release_title'],
                entry.get('issue_number'), entry['week_start']
            )
        )
    return volume_id, issue_id


def act_on_release(
    entry_id: int,
    action: str,
    root_folder_id: Union[int, None] = None
) -> Tuple[int, Union[int, None]]:
    """Apply an explicit add/monitor/grab action to one calendar row."""
    entry = get_db().execute(
        'SELECT * FROM pull_list_entries WHERE id = ?;', (entry_id,)
    ).fetchonedict()
    if not entry:
        raise RuntimeError('Release entry not found')
    if action not in ('monitor', 'grab'):
        raise RuntimeError('Unsupported release action')
    if entry.get('volume_id') is None and root_folder_id is None:
        raise RuntimeError('A root folder is required to add this series')

    volume_id, issue_id = _add_or_monitor_entry(
        entry, root_folder_id or 0
    )
    if action == 'grab' and issue_id is None:
        raise RuntimeError('The released issue is not in ComicVine yet')
    return volume_id, issue_id


def process_publisher_subscriptions(
    entries: List[Dict[str, Any]]
) -> List[DownloadTuple]:
    """Apply opt-in publisher rules and return downloads for the task queue."""
    subscriptions = {
        row['publisher'].lower(): row
        for row in get_db().execute(
            'SELECT * FROM publisher_subscriptions;'
        ).fetchalldict()
    }
    downloads: List[DownloadTuple] = []
    cursor = get_db()
    current_week = _monday(date.today()).isoformat()
    for entry in entries:
        if entry.get('week_start') != current_week:
            continue
        publisher = str(entry.get('publisher') or '').lower()
        subscription = subscriptions.get(publisher)
        if not subscription:
            continue
        action = 'auto_search' if subscription['auto_search'] else 'auto_add'
        release_key = _release_key(entry)
        completed = cursor.execute(
            """
            SELECT success FROM publisher_automation_history
            WHERE release_key = ? AND action = ?;
            """,
            (release_key, action)
        ).exists()
        if completed:
            continue

        try:
            volume_id, issue_id = _add_or_monitor_entry(
                entry, subscription['root_folder_id']
            )
            if subscription['auto_search']:
                if issue_id is None:
                    raise RuntimeError(
                        'The released issue is not in ComicVine yet'
                    )
                results = auto_search(volume_id, issue_id)
                if not results:
                    raise RuntimeError('No matching download was found yet')
                downloads.extend((
                    result['link'], volume_id, issue_id
                ) for result in results)
            success, message = True, None
        except Exception as error:
            LOGGER.exception(
                'Publisher automation failed for %s', release_key
            )
            success, message = False, str(error)[:240]

        cursor.execute(
            """
            INSERT INTO publisher_automation_history(
                release_key, action, success, message, attempted_at
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(release_key, action) DO UPDATE SET
                success = excluded.success,
                message = excluded.message,
                attempted_at = excluded.attempted_at;
            """,
            (release_key, action, success, message, round(time()))
        )
    return downloads
