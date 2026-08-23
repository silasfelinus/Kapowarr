# -*- coding: utf-8 -*-

"""Publisher-aware weekly release calendar and automation rules."""

from asyncio import TimeoutError as AsyncTimeoutError
from asyncio import gather, run, wait_for
from datetime import date, datetime, timedelta
from threading import Lock, Thread
from time import time
from typing import Any, Dict, List, Tuple, Type, Union

from flask import current_app, has_app_context

from backend.base.definitions import (MonitorScheme, WeeklyReleaseData,
                                      WeeklyReleaseSource)
from backend.base.file_extraction import extract_issue_number
from backend.base.helpers import AsyncSession, force_range
from backend.base.logging import LOGGER
from backend.features.metadata import (MetadataCapability,
                                       configured_metadata_provider_ids,
                                       get_metadata_provider)
from backend.features.search import auto_search
from backend.implementations.matching import match_title
from backend.implementations.volumes import Library, Volume
from backend.implementations.weekly_releases import (
    fetch_getcomics_weekly_releases, fetch_mylar_weekly_releases)
from backend.internals.db import get_db

DownloadTuple = Tuple[str, int, Union[int, None]]
WEEKLY_RELEASE_FETCH_TIMEOUT = 45.0
_PUBLISHER_AUTOMATION_LOCK = Lock()

PUBLISHER_RETRY_INTERVAL = 24 * 60 * 60
"""
How long to leave a failed publisher automation attempt alone before
trying it again. A comic that no indexer is carrying on release morning
is very often carried by one a day later, so giving up after a single
attempt loses the release outright -- but retrying every pull list check
would hammer the indexers for every release that is simply not out.
Daily is the compromise.
"""


class _NotAvailableYet(Exception):
    """The release is real, it just has not turned up anywhere yet.

    A comic that no indexer is carrying on release morning is the ordinary
    case, not a fault, and neither is one ComicVine has not indexed issues
    for. Both are still recorded as unsuccessful so the pull list can show
    them as pending, but they do not belong in the error log: a single
    check produced 139 ERROR tracebacks for releases that were simply not
    out, which buried the failures that actually needed reading.
    """



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


async def _fetch_all_weekly_releases(
    requested_week: Union[date, None] = None
) -> List[WeeklyReleaseData]:
    """Fetch one selected week or the normal nearby nine-week window."""
    if requested_week is None:
        start = _monday(date.today())
        weeks = [start + timedelta(weeks=offset) for offset in range(-4, 5)]
    else:
        weeks = [_monday(requested_week)]

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


def check_weekly_pull_list(
    requested_week: Union[date, None] = None
) -> List[Dict[str, Any]]:
    """Refresh selected/nearby weeks while retaining the accumulated archive."""
    requested_week = _monday(requested_week) if requested_week else None
    releases = run(_fetch_all_weekly_releases(requested_week))
    validation_week = (
        requested_week or _monday(date.today())
    ).isoformat()
    validation_catalogue = [
        release
        for release in releases
        if release.get('week_start') == validation_week
        and release.get('publisher')
    ]
    if not validation_catalogue:
        if requested_week is None:
            detail = 'No current-week publisher releases were returned'
        else:
            detail = (
                'No publisher releases were returned for week '
                f'{validation_week}'
            )
        raise RuntimeError(f'{detail}; the previous pull list was kept')

    entries = match_releases_to_library(
        releases, Library.get_public_volumes()
    )
    checked_at = round(time())
    for entry in entries:
        entry['issue_id'] = _find_issue_id(entry)
        entry['checked_at'] = checked_at

    refreshed_weeks = sorted({
        str(entry['week_start'])
        for entry in entries
        if entry.get('week_start')
    })
    cursor = get_db()
    with cursor:
        if refreshed_weeks:
            placeholders = ','.join('?' for _ in refreshed_weeks)
            cursor.execute(
                'DELETE FROM pull_list_entries '
                f'WHERE week_start IN ({placeholders});',
                tuple(refreshed_weeks)
            )
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

    archive_count = cursor.execute(
        'SELECT COUNT(*) FROM pull_list_entries;'
    ).fetchone()[0]
    archive_weeks = cursor.execute(
        'SELECT COUNT(DISTINCT week_start) FROM pull_list_entries;'
    ).fetchone()[0]
    LOGGER.info(
        'Weekly release calendar refreshed %d release(s) across %d week(s); '
        'archive retains %d release(s) across %d week(s)',
        len(entries), len(refreshed_weeks), archive_count, archive_weeks
    )
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
            h.message AS automation_message,
            h.action AS automation_action
        FROM pull_list_entries p
        LEFT JOIN volumes v ON p.volume_id = v.id
        LEFT JOIN publisher_subscriptions s
          ON p.publisher = s.publisher COLLATE NOCASE
        LEFT JOIN publisher_automation_history h
          ON h.release_key = CASE
            WHEN p.comicvine_issue_id IS NOT NULL
              THEN 'comicvine:' || p.comicvine_issue_id
            ELSE lower(COALESCE(p.publisher, '')) || '|' ||
                 lower(p.release_title) || '|' ||
                 lower(COALESCE(p.issue_number, '')) || '|' ||
                 COALESCE(p.release_date, p.week_start)
          END
          AND s.publisher IS NOT NULL
          AND h.action = CASE
            WHEN s.auto_search = 1 THEN 'auto_search'
            ELSE 'auto_add'
          END
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
        week_start = row['week_start']
        if hasattr(week_start, 'isoformat'):
            week_start = week_start.isoformat()
        else:
            week_start = str(week_start)
        counts_by_publisher.setdefault(row['publisher'], {})[
            week_start
        ] = row['release_count']
    for publisher in publishers:
        publisher['release_counts'] = counts_by_publisher.get(
            publisher['publisher'], {}
        )
    return publishers


def _schedule_publisher_subscription_apply(publisher: str) -> None:
    """Apply a saved rule to stored releases without blocking the API request."""
    if not has_app_context():
        return

    app = current_app._get_current_object()

    def apply_saved_rule() -> None:
        with app.app_context():
            try:
                downloads = process_publisher_subscriptions(
                    [], publisher_filter=publisher
                )
                if downloads:
                    from backend.features.download_queue import DownloadHandler
                    DownloadHandler().add_multiple(
                        (link, volume_id, issue_id, False)
                        for link, volume_id, issue_id in downloads
                    )
            except Exception:
                LOGGER.exception(
                    'Failed to apply saved Pull List rule for publisher %s',
                    publisher
                )

    Thread(
        target=apply_saved_rule,
        name=f'PublisherRule-{publisher[:40]}',
        daemon=True
    ).start()


def set_publisher_subscription(
    publisher: str,
    root_folder_id: int,
    auto_search_enabled: bool
) -> Dict[str, Any]:
    """Create or replace an auto-add/auto-grab publisher rule."""
    publisher = publisher.strip()
    cursor = get_db()
    cursor.execute(
        """
        INSERT INTO publisher_subscriptions(
            publisher, root_folder_id, auto_search
        ) VALUES (?, ?, ?)
        ON CONFLICT(publisher) DO UPDATE SET
            root_folder_id = excluded.root_folder_id,
            auto_search = excluded.auto_search;
        """,
        (publisher, root_folder_id, auto_search_enabled)
    )
    # The backfill worker uses another DB connection, so make the saved rule
    # visible before starting it instead of depending on request teardown.
    cursor.connection.commit()
    _schedule_publisher_subscription_apply(publisher)
    return {
        'publisher': publisher,
        'root_folder_id': root_folder_id,
        'auto_search': auto_search_enabled
    }


def delete_publisher_subscription(publisher: str) -> None:
    # Removing a rule is deliberately prospective. Existing series/issues stay
    # monitored; only future Pull List automation for this publisher stops.
    get_db().execute(
        """
        DELETE FROM publisher_subscriptions
        WHERE publisher = ? COLLATE NOCASE;
        """,
        (publisher,)
    )


def _normalise_publisher(value: Any) -> str:
    """Normalise publisher labels enough for metadata tie-breaking."""
    return ''.join(
        char for char in str(value or '').lower()
        if char.isalnum()
    )


def _publisher_names_match(left: Any, right: Any) -> bool:
    left_name = _normalise_publisher(left)
    right_name = _normalise_publisher(right)
    if not left_name or not right_name:
        return False
    return (
        left_name == right_name
        or left_name in right_name
        or right_name in left_name
    )


def _metadata_resolution_key(entry: Dict[str, Any]) -> Tuple[str, Any, str]:
    return (
        str(entry.get('release_title') or '').strip().lower(),
        entry.get('year'),
        _normalise_publisher(entry.get('publisher'))
    )


def _narrow_metadata_candidates(
    results: List[Dict[str, Any]],
    entry: Dict[str, Any]
) -> List[Dict[str, Any]]:
    title = str(entry.get('release_title') or '').strip()
    candidates = [
        result for result in results
        if match_title(result.get('title', ''), title)
    ]
    if not candidates:
        return []

    release_year = entry.get('year')
    if release_year is not None:
        year_matches = [
            result for result in candidates
            if result.get('year') == release_year
        ]
        if year_matches:
            candidates = year_matches

    publisher = entry.get('publisher')
    if publisher:
        publisher_matches = [
            result for result in candidates
            if _publisher_names_match(result.get('publisher'), publisher)
        ]
        if publisher_matches:
            candidates = publisher_matches

    unique: Dict[str, Dict[str, Any]] = {}
    for result in candidates:
        external_id = result.get('external_id') or result.get('comicvine_id')
        if external_id is None:
            continue
        unique[str(external_id)] = result
    return list(unique.values())


def _resolve_release_metadata(
    entry: Dict[str, Any],
    cache: Union[Dict[Tuple[str, Any, str], Dict[str, Any]], None] = None
) -> Dict[str, Any]:
    """Resolve a release without a ComicVine ID through configured metadata."""
    cache_key = _metadata_resolution_key(entry)
    if cache is not None and cache_key in cache:
        return cache[cache_key]

    title = str(entry.get('release_title') or '').strip()
    if not title:
        raise RuntimeError('Release has no title to resolve through metadata')

    provider_ids = configured_metadata_provider_ids(
        MetadataCapability.SEARCH_VOLUMES
    )
    if not provider_ids:
        provider_ids = ['comicvine']
    provider_priority = {'metron': 0, 'comicvine': 1, 'gcd': 2}
    provider_ids = sorted(
        provider_ids,
        key=lambda provider_id: provider_priority.get(provider_id, 10)
    )

    ambiguous = []
    unavailable = []
    for provider_id in provider_ids:
        provider = get_metadata_provider(provider_id)
        try:
            results = run(provider.search_volumes(title))
        except Exception as error:
            if provider.is_unavailable_error(error):
                unavailable.append(provider_id)
                LOGGER.warning(
                    'Metadata provider %s unavailable while resolving %s: %s',
                    provider_id, title, error
                )
            else:
                unavailable.append(provider_id)
                LOGGER.exception(
                    'Metadata provider %s failed while resolving %s',
                    provider_id, title
                )
            continue

        candidates = _narrow_metadata_candidates(results, entry)
        if not candidates:
            continue
        if len(candidates) > 1:
            ambiguous.append(provider_id)
            continue

        match = candidates[0]
        external_id = match.get('external_id') or match.get('comicvine_id')
        if external_id is None:
            continue
        resolution = {
            'provider_id': str(match.get('provider_id') or provider_id),
            'external_id': external_id,
            'comicvine_id': match.get('comicvine_id'),
            'volume_id': match.get('already_added')
        }
        if cache is not None:
            cache[cache_key] = resolution
        LOGGER.info(
            'Resolved Pull List series %s through %s ID %s',
            title, resolution['provider_id'], resolution['external_id']
        )
        return resolution

    if ambiguous:
        raise RuntimeError(
            f'Ambiguous metadata match for "{title}" from '
            f'{", ".join(ambiguous)}; will retry later'
        )
    detail = (
        f' (unavailable: {", ".join(unavailable)})'
        if unavailable else ''
    )
    raise RuntimeError(
        f'No metadata match found for "{title}"{detail}; will retry later'
    )


def _add_or_monitor_entry(
    entry: Dict[str, Any],
    root_folder_id: int,
    metadata_cache: Union[
        Dict[Tuple[str, Any, str], Dict[str, Any]], None
    ] = None
) -> Tuple[int, Union[int, None]]:
    volume_id = entry.get('volume_id')
    if volume_id is None:
        comicvine_id = entry.get('comicvine_volume_id')
        if comicvine_id:
            volume_id = get_db().execute(
                'SELECT id FROM volumes WHERE comicvine_id = ? LIMIT 1;',
                (comicvine_id,)
            ).exists()
            if volume_id is None:
                volume_id = Library.add(
                    comicvine_id, root_folder_id, True, MonitorScheme.ALL,
                    True, auto_search=False
                )
        else:
            resolution = _resolve_release_metadata(entry, metadata_cache)
            volume_id = resolution.get('volume_id')
            if volume_id is None:
                volume_id = Library.add(
                    resolution.get('comicvine_id'),
                    root_folder_id,
                    True,
                    MonitorScheme.ALL,
                    True,
                    auto_search=False,
                    metadata_provider_id=resolution['provider_id'],
                    metadata_external_id=resolution['external_id']
                )
                resolution['volume_id'] = volume_id
            if resolution.get('comicvine_id') is not None:
                entry['comicvine_volume_id'] = resolution['comicvine_id']
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
            SET volume_id = ?, issue_id = ?,
                comicvine_volume_id = COALESCE(comicvine_volume_id, ?)
            WHERE id = ?;
            """,
            (
                volume_id, issue_id, entry.get('comicvine_volume_id'),
                entry['id']
            )
        )
    elif entry.get('comicvine_issue_id') is not None:
        cursor.execute(
            """
            UPDATE pull_list_entries
            SET volume_id = ?, issue_id = ?,
                comicvine_volume_id = COALESCE(comicvine_volume_id, ?)
            WHERE comicvine_issue_id = ?;
            """,
            (
                volume_id, issue_id, entry.get('comicvine_volume_id'),
                entry['comicvine_issue_id']
            )
        )
    else:
        cursor.execute(
            """
            UPDATE pull_list_entries
            SET volume_id = ?, issue_id = ?,
                comicvine_volume_id = COALESCE(comicvine_volume_id, ?)
            WHERE release_title = ? AND issue_number IS ? AND week_start = ?;
            """,
            (
                volume_id, issue_id, entry.get('comicvine_volume_id'),
                entry['release_title'], entry.get('issue_number'),
                entry['week_start']
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
        raise RuntimeError('The released issue is not in metadata yet')
    return volume_id, issue_id


def _process_publisher_subscriptions_unlocked(
    entries: List[Dict[str, Any]],
    publisher_filter: Union[str, None] = None
) -> List[DownloadTuple]:
    """Apply publisher rules to current and all retained past releases."""
    publisher_filter = (
        publisher_filter.strip().lower() if publisher_filter else None
    )
    subscriptions = {
        row['publisher'].lower(): row
        for row in get_db().execute(
            'SELECT * FROM publisher_subscriptions;'
        ).fetchalldict()
    }
    downloads: List[DownloadTuple] = []
    cursor = get_db()
    current_week = _monday(date.today()).isoformat()

    # Production checks pass only the freshly fetched nearby window. Prefer
    # the durable archive so retroactive publisher rules reach every retained
    # past week. Direct callers without the archive table keep their input.
    has_archive = cursor.execute(
        """
        SELECT 1 FROM sqlite_master
        WHERE type = 'table' AND name = 'pull_list_entries'
        LIMIT 1;
        """
    ).exists()
    if has_archive:
        archived_entries = cursor.execute(
            """
            SELECT * FROM pull_list_entries
            WHERE week_start <= ?
            ORDER BY week_start, id;
            """,
            (current_week,)
        ).fetchalldict()
        if archived_entries:
            entries = archived_entries

    metadata_cache: Dict[Tuple[str, Any, str], Dict[str, Any]] = {}
    attempted = 0
    succeeded = 0
    failed = 0
    skipped = 0

    today = date.today().isoformat()
    now = round(time())

    for entry in entries:
        week_start = str(entry.get('week_start') or '')
        if not week_start or week_start > current_week:
            continue

        # The week filter alone is not enough. A week starts on Monday, so
        # from Monday morning it admits every release in it -- including
        # Friday's, three days before anyone could have it. Those were
        # attempted, failed for the only possible reason, and recorded.
        release_date = str(entry.get('release_date') or '')
        if release_date and release_date > today:
            continue
        publisher = str(entry.get('publisher') or '').lower()
        if publisher_filter is not None and publisher != publisher_filter:
            continue
        subscription = subscriptions.get(publisher)
        if not subscription:
            continue
        action = 'auto_search' if subscription['auto_search'] else 'auto_add'
        release_key = _release_key(entry)
        history = cursor.execute(
            """
            SELECT success, attempted_at FROM publisher_automation_history
            WHERE release_key = ? AND action = ?;
            """,
            (release_key, action)
        ).fetchone()
        # A row used to mean "done", whatever it said. `.exists()` matches a
        # failed attempt just as happily as a successful one, so a release
        # that was merely not out yet was attempted once and then never
        # again -- while the summary below counted it as `pending retry`.
        # A success is final; a failure waits out the retry interval.
        if history is not None and (
            history[0] or now - history[1] < PUBLISHER_RETRY_INTERVAL
        ):
            skipped += 1
            continue

        attempted += 1
        try:
            volume_id, issue_id = _add_or_monitor_entry(
                entry, subscription['root_folder_id'], metadata_cache
            )
            if subscription['auto_search']:
                if issue_id is None:
                    raise _NotAvailableYet(
                        'The released issue is not in metadata yet'
                    )
                results = auto_search(volume_id, issue_id)
                if not results:
                    raise _NotAvailableYet('No matching download was found yet')
                downloads.extend((
                    result['link'], volume_id, issue_id
                ) for result in results)
            success, message = True, None
            succeeded += 1
        except _NotAvailableYet as pending:
            LOGGER.info(
                'Publisher automation has nothing yet for %s: %s',
                release_key, pending
            )
            success, message = False, str(pending)[:240]
            failed += 1

        except Exception as error:
            LOGGER.exception(
                'Publisher automation failed for %s', release_key
            )
            success, message = False, str(error)[:240]
            failed += 1

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
            (release_key, action, success, message, now)
        )

    LOGGER.info(
        'Publisher automation processed %d release(s): %d succeeded, '
        '%d pending retry, %d already complete',
        attempted, succeeded, failed, skipped
    )
    return downloads


def process_publisher_subscriptions(
    entries: List[Dict[str, Any]],
    publisher_filter: Union[str, None] = None
) -> List[DownloadTuple]:
    """Serialize publisher automation and optionally limit it to one rule."""
    with _PUBLISHER_AUTOMATION_LOCK:
        return _process_publisher_subscriptions_unlocked(
            entries, publisher_filter
        )
