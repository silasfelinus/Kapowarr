# -*- coding: utf-8 -*-

"""Torznab indexers and torrent-result preparation.

Torznab is kept as a protocol peer to the fork's existing Newznab support.
Prowlarr and Jackett expose Torznab feeds, so Kapowarr only needs the common
protocol rather than product-specific integrations.
"""

from __future__ import annotations

from concurrent.futures import (Future, ThreadPoolExecutor,
                                TimeoutError as FutureTimeoutError)
from hashlib import sha1
from os.path import basename, join, splitext
from threading import Event
from typing import Any, Dict, List, Mapping, Tuple, Union
from urllib.parse import (parse_qs, quote_plus, unquote_plus, urlencode,
                          urlsplit, urlunsplit)
from xml.etree import ElementTree

from aiohttp import ClientError
from bencoding import bdecode, bencode
from requests import RequestException

from backend.base.custom_exceptions import (EnqueuingDownloadFailure,
                                            IndexerNotFound, IssueNotFound,
                                            KeyNotFound)
from backend.base.definitions import (Download, DownloadSource, DownloadState,
                                      DownloadType,
                                      EnqueuingDownloadFailureReason,
                                      ExternalDownloadClient, SearchResultData)
from backend.base.file_extraction import (extract_filename_data,
                                          refine_special_version)
from backend.base.helpers import (AsyncSession, Session,
                                  extract_year_from_date, normalise_base_url)
from backend.base.logging import LOGGER
from backend.implementations.download_clients import TorrentDownload
from backend.implementations.external_clients import ExternalClients
from backend.implementations.matching import check_search_result_match
from backend.implementations.naming import generate_issue_name
from backend.implementations.volumes import Volume
from backend.internals.db import get_db
from backend.internals.settings import Settings

TORZNAB_TEST_TIMEOUT = 10.0
DEFAULT_COMIC_CATEGORIES = '7030'
TORZNAB_TAG_KEY = 'kapowarr-torznab'
TORZNAB_TITLE_KEY = 'kapowarr-title'


def _ensure_table() -> None:
    """Create Torznab storage idempotently for fresh and upgraded installs."""
    get_db().execute("""
        CREATE TABLE IF NOT EXISTS torznab_indexers(
            id INTEGER PRIMARY KEY,
            title VARCHAR(255) NOT NULL,
            base_url TEXT NOT NULL,
            api_key VARCHAR(255) NOT NULL,
            categories VARCHAR(255) NOT NULL DEFAULT '7030',
            category_filter_enabled BOOL NOT NULL DEFAULT 0,
            enabled BOOL NOT NULL DEFAULT 1
        );
    """)

    columns = {
        row['name']
        for row in get_db().execute(
            'PRAGMA table_info(torznab_indexers);'
        ).fetchalldict()
    }
    if 'category_filter_enabled' not in columns:
        # Before this flag existed, every feed was silently given 7030. Treat
        # that legacy built-in value as automatic/unfiltered, while retaining
        # clearly customized category lists as explicit filters.
        get_db().executescript("""
            ALTER TABLE torznab_indexers ADD COLUMN
                category_filter_enabled BOOL NOT NULL DEFAULT 0;
            UPDATE torznab_indexers
            SET category_filter_enabled = 1
            WHERE TRIM(categories) NOT IN ('', '7030');
        """)
    return


def tag_torznab_link(link: str, indexer_id: int, title: str) -> str:
    """Attach local provenance to a result link using a URL fragment.

    Fragments are never sent to the remote HTTP server, and the tag also works
    on magnet URIs. This lets auto-search keep source identity even though its
    historical task contract passes only a link into the queue.
    """
    parsed = urlsplit(link)
    parts = [part for part in parsed.fragment.split('&') if part]
    parts.extend((
        f'{TORZNAB_TAG_KEY}={indexer_id}',
        f'{TORZNAB_TITLE_KEY}={quote_plus(title)}'
    ))
    return urlunsplit((
        parsed.scheme,
        parsed.netloc,
        parsed.path,
        parsed.query,
        '&'.join(parts)
    ))


def strip_torznab_tag(
    link: str
) -> Tuple[str, Union[int, None], Union[str, None]]:
    """Remove Kapowarr's provenance fragment and return its metadata."""
    parsed = urlsplit(link)
    indexer_id = None
    title = None
    kept = []
    for part in parsed.fragment.split('&'):
        if not part:
            continue
        key, separator, value = part.partition('=')
        if key == TORZNAB_TAG_KEY and separator:
            try:
                indexer_id = int(value)
            except ValueError:
                pass
        elif key == TORZNAB_TITLE_KEY and separator:
            title = unquote_plus(value)
        else:
            kept.append(part)

    clean = urlunsplit((
        parsed.scheme,
        parsed.netloc,
        parsed.path,
        parsed.query,
        '&'.join(kept)
    ))
    return clean, indexer_id, title


def is_torznab_link(link: str) -> bool:
    return strip_torznab_tag(link)[1] is not None


def _xml_local_name(tag: str) -> str:
    return tag.rsplit('}', 1)[-1].lower()


def _item_attributes(item) -> Dict[str, str]:
    result: Dict[str, str] = {}
    for child in list(item):
        if _xml_local_name(child.tag) != 'attr':
            continue
        name = child.attrib.get('name')
        value = child.attrib.get('value')
        if name and value is not None:
            result[name.lower()] = value
    return result


def _item_text(item, name: str) -> Union[str, None]:
    for child in list(item):
        if _xml_local_name(child.tag) == name:
            return child.text
    return None


def _item_enclosure(item) -> Union[str, None]:
    for child in list(item):
        if _xml_local_name(child.tag) == 'enclosure':
            return child.attrib.get('url')
    return None


def _build_magnet(
    infohash: str,
    title: str,
    trackers: List[str] = []
) -> str:
    params = [
        ('xt', f'urn:btih:{infohash}'),
        ('dn', title)
    ]
    params.extend(('tr', tracker) for tracker in trackers if tracker)
    return 'magnet:?' + urlencode(params)


def torrent_bytes_to_magnet(
    content: bytes,
    fallback_title: str
) -> Tuple[str, str]:
    """Convert a .torrent payload to a magnet and its real root name."""
    try:
        metadata = bdecode(content)
        info = metadata[b'info']
        encoded_info = bencode(info)
        infohash = sha1(encoded_info).hexdigest()
        raw_name = info.get(b'name') or fallback_title.encode('utf-8')
        if isinstance(raw_name, bytes):
            torrent_name = raw_name.decode('utf-8', errors='replace')
        else:
            torrent_name = str(raw_name)

        trackers: List[str] = []
        announce = metadata.get(b'announce')
        if isinstance(announce, bytes):
            trackers.append(announce.decode('utf-8', errors='replace'))

        announce_list = metadata.get(b'announce-list') or []
        for tier in announce_list:
            values = tier if isinstance(tier, list) else [tier]
            for tracker in values:
                if isinstance(tracker, bytes):
                    value = tracker.decode('utf-8', errors='replace')
                    if value not in trackers:
                        trackers.append(value)

        return _build_magnet(infohash, torrent_name, trackers), torrent_name

    except (KeyError, TypeError, ValueError):
        raise EnqueuingDownloadFailure(
            EnqueuingDownloadFailureReason.LINK_BROKEN
        )


class TorznabIndexer:
    required_fields = ('title', 'base_url', 'api_key')

    @property
    def id(self) -> int:
        return self._id

    @property
    def title(self) -> str:
        return self._title

    @property
    def base_url(self) -> str:
        return self._base_url

    @property
    def api_key(self) -> str:
        return self._api_key

    @property
    def categories(self) -> str:
        return self._categories

    @property
    def category_filter_enabled(self) -> bool:
        return self._category_filter_enabled

    @property
    def enabled(self) -> bool:
        return self._enabled

    def __init__(self, indexer_id: int) -> None:
        _ensure_table()
        data = get_db().execute("""
            SELECT id, title, base_url, api_key, categories,
                   category_filter_enabled, enabled
            FROM torznab_indexers
            WHERE id = ?
            LIMIT 1;
        """, (indexer_id,)).fetchonedict()
        if data is None:
            raise IndexerNotFound(indexer_id)

        self._id = data['id']
        self._title = data['title']
        self._base_url = data['base_url']
        self._api_key = data['api_key']
        self._categories = data['categories']
        self._category_filter_enabled = bool(data['category_filter_enabled'])
        self._enabled = bool(data['enabled'])

    def get_data(self) -> Dict[str, Any]:
        return {
            'id': self.id,
            'title': self.title,
            'base_url': self.base_url,
            'api_key': self.api_key,
            'categories': self.categories,
            'category_filter_enabled': self.category_filter_enabled,
            'enabled': self.enabled,
            'protocol': 'torznab'
        }

    def update(self, data: Mapping[str, Any]) -> None:
        formatted = TorznabIndexers._format_data(data)
        get_db().execute("""
            UPDATE torznab_indexers
            SET title = :title,
                base_url = :base_url,
                api_key = :api_key,
                categories = :categories,
                category_filter_enabled = :category_filter_enabled,
                enabled = :enabled
            WHERE id = :id;
        """, {**formatted, 'id': self.id})
        self._title = formatted['title']
        self._base_url = formatted['base_url']
        self._api_key = formatted['api_key']
        self._categories = formatted['categories']
        self._category_filter_enabled = formatted['category_filter_enabled']
        self._enabled = formatted['enabled']

    def delete(self) -> None:
        get_db().execute(
            'DELETE FROM torznab_indexers WHERE id = ?;',
            (self.id,)
        )


class TorznabIndexers:
    @staticmethod
    def _format_data(data: Mapping[str, Any]) -> Dict[str, Any]:
        for key in TorznabIndexer.required_fields:
            if not data.get(key):
                raise KeyNotFound(key)

        categories = data.get('categories', DEFAULT_COMIC_CATEGORIES)
        if categories is None:
            categories = ''
        if not isinstance(categories, str):
            categories = str(categories)

        return {
            'title': data['title'],
            'base_url': normalise_base_url(data['base_url']),
            'api_key': data['api_key'],
            'categories': categories.strip(),
            'category_filter_enabled': bool(
                data.get('category_filter_enabled', False)
            ),
            'enabled': bool(data.get('enabled', True))
        }

    @staticmethod
    def test(base_url: str, api_key: str) -> bool:
        base_url = normalise_base_url(base_url)

        def _run() -> bool:
            with Session() as session:
                response = session.get(
                    base_url,
                    params={'t': 'caps', 'apikey': api_key},
                    timeout=TORZNAB_TEST_TIMEOUT
                )
            if not response.ok:
                return False
            try:
                root = ElementTree.fromstring(response.text)
            except ElementTree.ParseError:
                return False
            return _xml_local_name(root.tag) == 'caps'

        executor = ThreadPoolExecutor(max_workers=1)
        future: Future = executor.submit(_run)
        try:
            return future.result(timeout=TORZNAB_TEST_TIMEOUT)
        except (FutureTimeoutError, RequestException):
            return False
        finally:
            executor.shutdown(wait=False)

    @staticmethod
    def add(
        title: str,
        base_url: str,
        api_key: str,
        categories: str = DEFAULT_COMIC_CATEGORIES,
        enabled: bool = True,
        category_filter_enabled: bool = False
    ) -> TorznabIndexer:
        _ensure_table()
        data = TorznabIndexers._format_data({
            'title': title,
            'base_url': base_url,
            'api_key': api_key,
            'categories': categories,
            'category_filter_enabled': category_filter_enabled,
            'enabled': enabled
        })
        indexer_id = get_db().execute("""
            INSERT INTO torznab_indexers(
                title, base_url, api_key, categories,
                category_filter_enabled, enabled
            ) VALUES (
                :title, :base_url, :api_key, :categories,
                :category_filter_enabled, :enabled
            );
        """, data).lastrowid
        return TorznabIndexer(indexer_id)

    @staticmethod
    def get_all() -> List[Dict[str, Any]]:
        _ensure_table()
        return [
            TorznabIndexer(row['id']).get_data()
            for row in get_db().execute(
                'SELECT id FROM torznab_indexers ORDER BY title, id;'
            ).fetchalldict()
        ]

    @staticmethod
    def get_one(indexer_id: int) -> TorznabIndexer:
        return TorznabIndexer(indexer_id)

    @staticmethod
    def get_enabled() -> List[TorznabIndexer]:
        _ensure_table()
        return [
            TorznabIndexer(row['id'])
            for row in get_db().execute("""
                SELECT id FROM torznab_indexers
                WHERE enabled = 1
                ORDER BY title, id;
            """).fetchalldict()
        ]


async def search_torznab_indexer(
    session: AsyncSession,
    indexer: TorznabIndexer,
    query: str
) -> List[SearchResultData]:
    params = {
        't': 'search',
        'q': query,
        'apikey': indexer.api_key,
        'extended': '1'
    }
    if indexer.category_filter_enabled and indexer.categories:
        params['cat'] = indexer.categories

    body = await session.get_text(
        indexer.base_url,
        params=params,
        quiet_fail=True
    )
    if not body:
        return []

    try:
        root = ElementTree.fromstring(body)
    except ElementTree.ParseError:
        LOGGER.warning('Torznab indexer %s returned malformed XML', indexer.title)
        return []

    results: List[SearchResultData] = []
    for item in root.iter():
        if _xml_local_name(item.tag) != 'item':
            continue

        title = _item_text(item, 'title')
        if not title:
            continue

        attrs = _item_attributes(item)
        enclosure = _item_enclosure(item)
        magnet = attrs.get('magneturl')
        infohash = attrs.get('infohash')
        link = enclosure or magnet or _item_text(item, 'link')
        if not link and infohash:
            link = _build_magnet(infohash, title)
        if not link:
            continue

        results.append({
            **extract_filename_data(
                title,
                assume_volume_number=False,
                fix_year=True
            ),
            'link': tag_torznab_link(link, indexer.id, title),
            'display_title': title,
            'source': indexer.title
        })

    return results


class IndexerTorrentDownload(TorrentDownload):
    """Torrent download whose metadata was already resolved by Torznab."""

    identifier = 'indexer_torrent'

    def __init__(
        self,
        download_link: str,
        volume_id: int,
        covered_issues: Union[float, Tuple[float, float], None],
        source_type: DownloadSource,
        source_name: str,
        web_link: Union[str, None],
        web_title: Union[str, None],
        web_sub_title: Union[str, None],
        forced_match: bool = False,
        external_client: Union[ExternalDownloadClient, None] = None
    ) -> None:
        settings = Settings().sv
        volume = Volume(volume_id)

        self._download_link = self._pure_link = download_link
        self._volume_id = volume_id
        self._issue_id = None
        self._covered_issues = covered_issues
        self._source_type = source_type
        self._source_name = source_name
        self._web_link = web_link
        self._web_title = web_title
        self._web_sub_title = web_sub_title

        self._id = None
        self._state = DownloadState.QUEUED_STATE
        self._progress = 0.0
        self._speed = 0.0
        self._size = -1
        self._download_thread = None
        self._download_folder = settings.download_folder
        self._sleep_event = Event()
        self._original_files: List[str] = []
        self._external_id = None
        self._external_client = external_client or ExternalClients.get_least_used_client(
            DownloadType.TORRENT
        )

        try:
            if isinstance(covered_issues, float):
                self._issue_id = volume.get_issue_from_number(covered_issues).id
        except IssueNotFound as error:
            if not forced_match:
                raise error

        query = parse_qs(urlsplit(download_link).query)
        torrent_name = (query.get('dn') or [web_title or 'torrent'])[0]
        torrent_name = basename(torrent_name)

        self._filename_body = ''
        if settings.rename_downloaded_files:
            try:
                self._filename_body = generate_issue_name(
                    volume.get_data(),
                    covered_issues
                )
            except IssueNotFound as error:
                if not forced_match:
                    raise error

        if not self._filename_body:
            self._filename_body = splitext(torrent_name)[0]

        self._title = basename(self._filename_body)
        self._files = [join(self._download_folder, torrent_name)]


async def _normalise_torrent_link(
    link: str,
    title: str
) -> str:
    if link.startswith('magnet:'):
        return link

    async with AsyncSession() as session:
        try:
            content = await session.get_content(link, quiet_fail=True)
        except ClientError:
            content = b''
    if not content:
        raise EnqueuingDownloadFailure(
            EnqueuingDownloadFailureReason.LINK_BROKEN
        )

    magnet, _ = torrent_bytes_to_magnet(content, title)
    return magnet


async def create_torznab_download(
    tagged_link: str,
    volume_id: int,
    issue_id: Union[int, None],
    force_match: bool = False
) -> Download:
    link, indexer_id, tagged_title = strip_torznab_tag(tagged_link)
    if indexer_id is None:
        raise EnqueuingDownloadFailure(
            EnqueuingDownloadFailureReason.LINK_BROKEN
        )

    try:
        indexer = TorznabIndexers.get_one(indexer_id)
    except IndexerNotFound:
        raise EnqueuingDownloadFailure(
            EnqueuingDownloadFailureReason.LINK_BROKEN
        )

    title = tagged_title or 'unknown torrent release'
    volume = Volume(volume_id)
    volume_data = volume.get_data()
    info = refine_special_version(
        volume_data,
        extract_filename_data(
            splitext(title)[0],
            assume_volume_number=False,
            fix_year=True
        )
    )
    covered_issues = info['issue_number']

    if not force_match:
        volume_issues = volume.get_issues()
        number_to_year = {
            issue.calculated_issue_number: extract_year_from_date(issue.date)
            for issue in volume_issues
        }
        calculated_issue_number = None
        if issue_id is not None:
            calculated_issue_number = volume.get_issue(issue_id).get_data().calculated_issue_number

        match = check_search_result_match(
            {
                **info,
                'link': tagged_link,
                'display_title': title,
                'source': indexer.title
            },
            volume_data,
            volume_issues,
            number_to_year,
            calculated_issue_number
        )
        if not match['match']:
            raise EnqueuingDownloadFailure(
                EnqueuingDownloadFailureReason.NO_MATCHES
            )

    magnet = await _normalise_torrent_link(link, title)
    return IndexerTorrentDownload(
        download_link=magnet,
        volume_id=volume_id,
        covered_issues=covered_issues,
        # Generic torrent-indexer enum can be added without changing queue
        # behavior; source_name below preserves the actual indexer provenance.
        source_type=DownloadSource.GETCOMICS_TORRENT,
        source_name=indexer.title,
        web_link=tagged_link,
        web_title=title,
        web_sub_title='Torznab',
        forced_match=force_match
    )
