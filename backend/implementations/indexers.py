# -*- coding: utf-8 -*-

"""Compatibility wrapper around the original Newznab implementation.

The original implementation lives in ``indexers_core`` so this module can keep
its public/patchable surface while adding production compatibility for RSS/XML
Newznab feeds, Prowlarr download URLs, and request pacing.
"""

from asyncio import Lock as AsyncLock, get_running_loop, sleep as async_sleep
from json import loads as json_loads
from threading import Lock
from time import monotonic
from urllib.parse import urlsplit
from weakref import WeakKeyDictionary
from xml.etree import ElementTree

from backend.base.custom_exceptions import EnqueuingDownloadFailure, IssueNotFound
from backend.base.definitions import (DownloadSource,
                                      EnqueuingDownloadFailureReason,
                                      SearchResultData)
from backend.base.file_extraction import extract_filename_data, refine_special_version
from backend.base.helpers import AsyncSession, extract_year_from_date
from backend.base.logging import LOGGER
from backend.implementations import indexers_core as _core
from backend.implementations.indexers_core import *
from backend.implementations.matching import check_search_result_match
from backend.implementations.volumes import Volume
from backend.implementations.download_clients import NZBDownload
from backend.internals.db import get_db as _real_get_db

# Keep private helpers that existing callers/tests import from this module.
_extract_item_link = _core._extract_item_link
_parse_content_disposition_filename = _core._parse_content_disposition_filename

# Core Indexer/Indexers methods resolve get_db in their defining module. Route
# that lookup back through this wrapper so the long-standing test/plugin patch
# point ``backend.implementations.indexers.get_db`` remains effective.
get_db = _real_get_db

def _forward_get_db(*args, **kwargs):
    return globals()['get_db'](*args, **kwargs)

_core.get_db = _forward_get_db

# One Newznab feed gets one in-flight request per asyncio loop. Query planning
# can produce four variants at once; sending those as a burst is hostile to
# Prowlarr/indexer rate limits and was producing repeated HTTP 429s.
_REQUEST_LOCKS_GUARD = Lock()
_REQUEST_LOCKS = WeakKeyDictionary()
_REQUEST_STARTS = WeakKeyDictionary()
NEWZNAB_REQUEST_MIN_INTERVAL = 0.6


def _request_key(indexer) -> str:
    return newznab_api_url(indexer.base_url).lower()


def _request_state(indexer):
    loop = get_running_loop()
    key = _request_key(indexer)
    with _REQUEST_LOCKS_GUARD:
        locks = _REQUEST_LOCKS.setdefault(loop, {})
        starts = _REQUEST_STARTS.setdefault(loop, {})
        lock = locks.setdefault(key, AsyncLock())
    return lock, starts, key


def _strip_feed_suffix(path: str) -> str:
    path = path.rstrip('/')
    lowered = path.lower()
    if lowered.endswith('/newznab'):
        return path[:-len('/newznab')].rstrip('/')
    if lowered.endswith('/api'):
        return path[:-len('/api')].rstrip('/')
    return path


def _link_belongs_to_indexer(indexer, link: str) -> bool:
    """Recognise native and Prowlarr Newznab result/download URLs."""
    try:
        base = urlsplit(indexer.base_url)
        target = urlsplit(link)
    except ValueError:
        return False
    if not target.scheme or not target.netloc:
        return False
    if (base.scheme.lower(), base.netloc.lower()) != (
        target.scheme.lower(), target.netloc.lower()
    ):
        return False

    prefix = _strip_feed_suffix(base.path)
    target_path = target.path.rstrip('/')
    if not prefix:
        return True
    return target_path == prefix or target_path.startswith(prefix + '/')


def _deduped_get_enabled():
    original = _core.Indexers.get_enabled.__func__ if isinstance(
        _core.Indexers.__dict__.get('get_enabled'), staticmethod
    ) else None
    # This function is installed once below, so preserve the captured original
    # instead of introspecting after replacement.
    return [] if original is None else original()


_ORIGINAL_GET_ENABLED = Indexers.get_enabled


def _get_enabled_unique():
    result = []
    seen = set()
    for indexer in _ORIGINAL_GET_ENABLED():
        key = (newznab_api_url(indexer.base_url).lower(), indexer.api_key)
        if key in seen:
            LOGGER.debug('Skipping duplicate Newznab feed %s', indexer.title)
            continue
        seen.add(key)
        result.append(indexer)
    return result


def _find_by_link_compatible(link: str):
    enabled = Indexers.get_enabled()
    # Prefer a path-specific match, which preserves the correct publisher name
    # for legacy /39/api and modern /api/v1/indexer/39/newznab feeds.
    for indexer in enabled:
        if _link_belongs_to_indexer(indexer, link):
            return indexer

    # Some Prowlarr versions return a host-level /download/... URL rather than
    # a sibling of the configured per-indexer feed. Accept that narrow shape,
    # but never treat an arbitrary sibling path as Newznab merely because it is
    # on the same Prowlarr hostname. A host can simultaneously expose /33/api
    # as Newznab and /39/api as Torznab.
    try:
        target = urlsplit(link)
    except ValueError:
        return None
    target_path = target.path.rstrip('/').lower()
    if not (target_path == '/download' or target_path.startswith('/download/')):
        return None

    for indexer in enabled:
        base = urlsplit(indexer.base_url)
        if (base.scheme.lower(), base.netloc.lower()) == (
            target.scheme.lower(), target.netloc.lower()
        ):
            return indexer
    return None


Indexers.get_enabled = staticmethod(_get_enabled_unique)
Indexers.find_by_link = staticmethod(_find_by_link_compatible)


def _local_name(tag: str) -> str:
    return tag.rsplit('}', 1)[-1].lower()


def _xml_child_text(element, name: str):
    for child in element:
        if _local_name(child.tag) == name:
            return (child.text or '').strip() or None
    return None


def _parse_newznab_xml(body: str, indexer) -> list:
    """Parse canonical Newznab RSS/XML into the normal search-result shape."""
    try:
        root = ElementTree.fromstring(body)
    except ElementTree.ParseError:
        LOGGER.warning(
            'Indexer %s returned neither valid JSON nor Newznab XML',
            indexer.title
        )
        return []

    errors = [element for element in root.iter() if _local_name(element.tag) == 'error']
    if errors:
        error = errors[0]
        detail = error.attrib.get('description') or error.attrib.get('code') or 'unknown error'
        LOGGER.warning('Indexer %s returned an error: %s', indexer.title, detail)
        return []

    results = []
    for item in root.iter():
        if _local_name(item.tag) != 'item':
            continue
        title = _xml_child_text(item, 'title')
        if not title:
            continue
        link = None
        for child in item:
            name = _local_name(child.tag)
            if name == 'enclosure' and child.attrib.get('url'):
                link = child.attrib['url']
                break
            if name == 'link' and (child.text or '').strip():
                link = child.text.strip()
            elif name == 'guid' and not link:
                permalink = str(child.attrib.get('isPermaLink', '')).lower()
                if permalink == 'true' and (child.text or '').strip():
                    link = child.text.strip()
        if not link:
            continue
        results.append({
            **extract_filename_data(title, assume_volume_number=False, fix_year=True),
            'link': link,
            'display_title': title,
            'source': indexer.title
        })
    return results


def _parse_newznab_json(data, indexer) -> list:
    if not isinstance(data, dict):
        LOGGER.warning(
            'Indexer %s returned an unexpected response shape (%s)',
            indexer.title, type(data).__name__
        )
        return []
    if 'error' in data:
        LOGGER.warning('Indexer %s returned an error: %s', indexer.title, data.get('error'))
        return []

    channel = data.get('channel')
    items = channel.get('item', []) if isinstance(channel, dict) else []
    if isinstance(items, dict):
        items = [items]
    elif not isinstance(items, list):
        items = []

    results = []
    for item in items:
        if not isinstance(item, dict):
            continue
        title = item.get('title')
        link = _extract_item_link(item) if title else None
        if not title or not link:
            continue
        results.append({
            **extract_filename_data(title, assume_volume_number=False, fix_year=True),
            'link': link,
            'display_title': title,
            'source': indexer.title
        })
    return results


async def search_indexer(session: AsyncSession, indexer: Indexer, query: str) -> list:
    """Search one Newznab feed, accepting JSON or canonical RSS/XML."""
    lock, starts, key = _request_state(indexer)
    async with lock:
        elapsed = monotonic() - starts.get(key, 0.0)
        if elapsed < NEWZNAB_REQUEST_MIN_INTERVAL:
            await async_sleep(NEWZNAB_REQUEST_MIN_INTERVAL - elapsed)
        starts[key] = monotonic()
        body = await session.get_text(
            newznab_api_url(indexer.base_url),
            params={
                't': 'search', 'q': query, 'apikey': indexer.api_key,
                'o': 'json', 'extended': '1'
            },
            quiet_fail=True
        )

    if not body:
        return []
    try:
        return _parse_newznab_json(json_loads(body), indexer)
    except ValueError:
        return _parse_newznab_xml(body, indexer)


async def create_nzb_download(
    link: str,
    volume_id: int,
    issue_id,
    force_match: bool = False
):
    """Turn an indexer result URL into a queue-ready NZB download."""
    indexer = Indexers.find_by_link(link)
    source_name = indexer.title if indexer else 'Usenet indexer'

    try:
        async with AsyncSession() as session:
            async with session.get(link) as response:
                if not response.ok:
                    raise EnqueuingDownloadFailure(
                        EnqueuingDownloadFailureReason.LINK_BROKEN
                    )
                title = _parse_content_disposition_filename(
                    response.headers.get('Content-Disposition', '')
                )
    except ClientError:
        raise EnqueuingDownloadFailure(
            EnqueuingDownloadFailureReason.LINK_BROKEN
        )

    if not title:
        title = link.rsplit('/', 1)[-1] or 'unknown release'

    volume = Volume(volume_id)
    volume_data = volume.get_data()
    info = extract_filename_data(
        splitext(title)[0], assume_volume_number=False, fix_year=True
    )
    info = refine_special_version(volume_data, info)
    covered_issues = info['issue_number']

    if not force_match:
        volume_issues = volume.get_issues()
        number_to_year = {
            issue.calculated_issue_number: extract_year_from_date(issue.date)
            for issue in volume_issues
        }
        calculated_issue_number = None
        if issue_id is not None:
            try:
                calculated_issue_number = volume.get_issue(
                    issue_id
                ).get_data().calculated_issue_number
            except IssueNotFound:
                pass
        result: SearchResultData = {
            **info,
            'link': link,
            'display_title': title,
            'source': source_name
        }
        match = check_search_result_match(
            result, volume_data, volume_issues,
            number_to_year, calculated_issue_number
        )
        if not match['match']:
            raise EnqueuingDownloadFailure(
                EnqueuingDownloadFailureReason.NO_MATCHES
            )

    return NZBDownload(
        download_link=link,
        volume_id=volume_id,
        covered_issues=covered_issues,
        source_type=DownloadSource.USENET_INDEXER,
        source_name=source_name,
        web_link=None,
        web_title=title,
        web_sub_title=None,
        forced_match=force_match
    )