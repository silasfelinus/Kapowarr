# -*- coding: utf-8 -*-

"""
Usenet indexer support -- the Newznab-compatible counterpart to GetComics
search (`backend.implementations.getcomics`). An indexer is a
user-configured Newznab API endpoint (NZBgeek, DrunkenSlug, NZBFinder,
a private/self-hosted NZBHydra2 instance, etc.) identified by a base URL
and an API key. Any number of indexers can be configured; every enabled
one is queried on every search, same as GetComics is always queried.

Architecture note (kapowarr/t-008): this deliberately does NOT introduce a
per-indexer "client type" the way `external_clients.py` does for download
clients. Newznab is a single, long-stable shared API spec that the vast
majority of Usenet indexer sites implement identically -- adding a second
indexer is "add a row with a different base_url/api_key", not "write a new
client class". If a genuinely non-Newznab indexer protocol is ever needed,
the natural extension point is `SearchSource` (see `search_indexer()`'s
caller in `backend.features.search`): add another `SearchSource` subclass
for it, same as this module's `SearchIndexers` already does -- no rewrite
of the search orchestration, matching, or download-queue handoff below.

Two Newznab endpoints are used:
- `t=caps` -- a lightweight capability probe, used by `Indexers.test()` for
  the interactive "Test" button. Bounded the same way
  `external_clients.py`'s `_test_client_bounded`/`notifications.py`'s
  `_post_bounded` are, so a dead/slow host fails the button quickly instead
  of hanging on `Session`'s full retry/backoff cycle.
- `t=search` -- the actual query, used by `search_indexer()`. Run through
  `AsyncSession` (async, used by every other search source) rather than the
  synchronous `Session` used for `test()`, matching `search_getcomics()`.

Not live-verified against a real indexer in this sandbox (none reachable
here) -- same caveat prior private-server/Usenet tasks in this project
have flagged (t-005, t-007). The Newznab JSON response shape below follows
the long-stable public spec (https://newznab.readthedocs.io/), including
the well-known quirk where `channel.item` is a bare object instead of a
single-element list when a search returns exactly one result.

`search_indexer()`'s response parsing is deliberately defensive about
shape, not just content: `SearchIndexers.search()` (see
`backend.features.search`) awaits every enabled indexer through a plain
`asyncio.gather()` with no `return_exceptions=True`, so an *unhandled*
exception here would fail the whole combined search (GetComics included),
not just this one indexer's results -- same
"empty rather than raising" contract the docstring below already
promises, just also enforced against a misconfigured/misbehaving endpoint
returning a shape that isn't a well-formed Newznab response, not only
non-JSON or an explicit `error` key (kapowarr/t-024).
"""

from asyncio import Lock as AsyncLock, get_running_loop, sleep as async_sleep
from concurrent.futures import (Future, ThreadPoolExecutor,
                                TimeoutError as FutureTimeoutError)
from json import loads as json_loads
from os.path import splitext
from re import search as re_search
from threading import Lock as ThreadLock
from time import monotonic
from typing import Any, Dict, List, Mapping, Union
from urllib.parse import urlsplit
from weakref import WeakKeyDictionary

from aiohttp import ClientError
from requests import RequestException

from backend.base.custom_exceptions import (EnqueuingDownloadFailure,
                                            IndexerNotFound, IssueNotFound,
                                            KeyNotFound)
from xml.etree import ElementTree

from backend.base.definitions import (Constants, Download, DownloadSource, EnqueuingDownloadFailureReason, SearchResultData)
from backend.base.file_extraction import (extract_filename_data,
                                          refine_special_version)
from backend.base.helpers import (AsyncSession, Session,
                                  extract_year_from_date,
                                  normalise_base_url,
                                  rate_limit_cooldown_remaining,
                                  register_rate_limit_scope)
from backend.base.helpers import redact_url
from backend.base.logging import LOGGER
from backend.features.acquisition_preferences import search_delay
from backend.implementations.download_clients import NZBDownload
from backend.implementations.matching import check_search_result_match
from backend.implementations.volumes import Volume
from backend.internals.db import get_db

# Deliberately shorter than a full `Session` retry cycle, same reasoning as
# `external_clients.py`'s `EXTERNAL_CLIENT_TEST_TIMEOUT` and
# `notifications.py`'s `NOTIFICATION_REQUEST_TIMEOUT`: a user clicking
# "Test" against an unreachable indexer should see a result in roughly ten
# seconds, not wait out the full backoff cycle.
INDEXER_TEST_TIMEOUT = 10.0 # seconds
# `create_nzb_download`'s own one-off fetch (recovering a release's title
# from its download link, see that function's docstring) needs no separate
# bound -- it goes through `AsyncSession`, which already bounds every
# request via `Constants.REQUEST_TIMEOUT`/its own retry cycle, same as
# `getcomics.py`'s `__purify_link` doing the analogous per-link fetch.


def newznab_api_url(base_url: str) -> str:
    """Return the actual Newznab endpoint for a configured URL.

    Native indexers are commonly configured with a host URL and expose their
    API at ``/api``. Prowlarr and similar aggregators instead give users a
    complete per-indexer feed URL, such as ``/1/api`` or
    ``/api/v1/indexer/1/newznab``. Appending another ``/api`` to those URLs
    makes every test and search request miss the real endpoint.
    """
    path = urlsplit(base_url).path.rstrip('/').lower()
    if path.endswith('/api') or path.endswith('/newznab'):
        return base_url
    return f'{base_url}/api'


def _parse_content_disposition_filename(header_value: str) -> Union[str, None]:
    """Extract a filename from a `Content-Disposition` header value, e.g.
    `attachment; filename="Batman (2020) 001.nzb"` or the RFC 5987
    `filename*=UTF-8''...` form. Newznab-compatible indexers set this
    header on their `t=get` download endpoints to communicate the release's
    real name, since the URL itself is usually an opaque id.

    Args:
        header_value (str): The raw `Content-Disposition` header value.

    Returns:
        Union[str, None]: The filename, or `None` if not present/parseable.
    """
    if not header_value:
        return None

    match = re_search(r'filename\*=(?:UTF-8\x27\x27)?([^;]+)', header_value)
    if match:
        return match.group(1).strip('"\' ')

    match = re_search(r'filename="?([^";]+)"?', header_value)
    if match:
        return match.group(1).strip()

    return None


def _test_bounded(base_url: str, api_key: str) -> bool:
    """Run the actual `t=caps` request bounded by INDEXER_TEST_TIMEOUT, no
    matter how many retries/backoff sleeps `Session` attempts internally
    (a plain `timeout=` kwarg only bounds a single attempt, not the sleeps
    between `Session`'s own retries -- see `external_clients.py`'s
    `_test_client_bounded` for the same fix applied to download clients).

    Returns:
        bool: Whether the indexer responded successfully to a caps probe.
    """
    def _run() -> bool:
        with Session() as session:
            r = session.get(
                newznab_api_url(base_url),
                params={"t": "caps", "apikey": api_key, "o": "json"},
                timeout=INDEXER_TEST_TIMEOUT
            )
        if not r.ok:
            return False
        try:
            data = r.json()
        except ValueError:
            # Some indexers only speak XML for `t=caps`; a non-JSON 2xx
            # response still means the host is a real Newznab endpoint.
            return "<caps" in r.text.lower() or "<error" not in r.text.lower()
        return "error" not in data

    executor = ThreadPoolExecutor(max_workers=1)
    future: Future = executor.submit(_run)
    try:
        return future.result(timeout=INDEXER_TEST_TIMEOUT)

    except FutureTimeoutError:
        LOGGER.warning(
            "Indexer test request to %s didn't finish within %ss",
            base_url, INDEXER_TEST_TIMEOUT
        )
        return False

    except RequestException:
        LOGGER.warning("Failed to reach indexer at %s", redact_url(base_url))
        return False

    finally:
        executor.shutdown(wait=False)


# 7030 is the standard Newznab comics category, but the standard is only
# a suggestion: an indexer files its releases wherever it likes, and the
# nzb.su family of sites puts comics under its own 107030. Silas, of his
# three: "all our nzb indexers use that category".
#
# So the default is both. `cat` takes a comma-separated list and an
# indexer ignores the IDs it does not have, which makes asking for both
# free -- and much better than picking one and silently seeing nothing on
# every indexer that chose the other. Per-indexer because no single answer
# is right for everyone; see `Constants.COMIC_CATEGORY` for the fallback
# when an indexer has been given none at all.
DEFAULT_COMIC_CATEGORIES = f"{Constants.COMIC_CATEGORY},107030"


class Indexer:
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
        data = get_db().execute(
            """
            SELECT id, title, base_url, api_key, categories,
                   category_filter_enabled, enabled
            FROM indexers
            WHERE id = ?
            LIMIT 1;
            """,
            (indexer_id,)
        ).fetchonedict()

        if data is None:
            raise IndexerNotFound(indexer_id)

        self._id = data['id']
        self._title = data['title']
        self._base_url = data['base_url']
        self._api_key = data['api_key']
        self._categories = data['categories']
        self._category_filter_enabled = bool(data['category_filter_enabled'])
        self._enabled = bool(data['enabled'])
        return

    def get_data(self) -> Dict[str, Any]:
        return {
            'id': self._id,
            'title': self._title,
            'base_url': self._base_url,
            'api_key': self._api_key,
            'categories': self._categories,
            'category_filter_enabled': self._category_filter_enabled,
            'enabled': self._enabled
        }

    def update(self, data: Mapping[str, Any]) -> None:
        formatted_data = Indexers._format_data(data)

        get_db().execute(
            """
            UPDATE indexers
            SET
                title = :title,
                base_url = :base_url,
                api_key = :api_key,
                categories = :categories,
                category_filter_enabled = :category_filter_enabled,
                enabled = :enabled
            WHERE id = :id;
            """,
            {**formatted_data, "id": self._id}
        )
        self._title = formatted_data['title']
        self._base_url = formatted_data['base_url']
        self._api_key = formatted_data['api_key']
        self._categories = formatted_data['categories']
        self._category_filter_enabled = formatted_data[
            'category_filter_enabled']
        self._enabled = formatted_data['enabled']
        return

    def delete(self) -> None:
        get_db().execute(
            "DELETE FROM indexers WHERE id = ?;",
            (self._id,)
        )
        return

    def __repr__(self) -> str:
        return f'<Indexer(id={self._id}; title={self._title})>'


class Indexers:
    @staticmethod
    def _format_data(data: Mapping[str, Any]) -> Dict[str, Any]:
        """Validate and normalise the incoming title/base_url/api_key/enabled
        fields shared by add() and update().

        Raises:
            KeyNotFound: A required key is missing.
        """
        for key in Indexer.required_fields:
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
        """Test whether an indexer is a reachable Newznab-compatible API.

        Not required before add()/update() (same as
        `notifications.py`'s test-is-separate-from-add convention) -- a
        temporarily-unreachable indexer is still a valid configuration to
        save; the UI exposes "Test" as its own explicit action.

        Returns:
            bool: Whether the caps probe succeeded.
        """
        return _test_bounded(normalise_base_url(base_url), api_key)

    @staticmethod
    def add(
        title: str,
        base_url: str,
        api_key: str,
        enabled: bool = True,
        categories: str = DEFAULT_COMIC_CATEGORIES,
        category_filter_enabled: bool = False
    ) -> Indexer:
        """Add a new indexer.

        Raises:
            KeyNotFound: A required key is missing.
        """
        formatted_data = Indexers._format_data({
            'title': title,
            'base_url': base_url,
            'api_key': api_key,
            'categories': categories,
            'category_filter_enabled': category_filter_enabled,
            'enabled': enabled
        })

        indexer_id = get_db().execute(
            """
            INSERT INTO indexers(
                title, base_url, api_key, categories,
                category_filter_enabled, enabled
            )
            VALUES (
                :title, :base_url, :api_key, :categories,
                :category_filter_enabled, :enabled
            );
            """,
            formatted_data
        ).lastrowid

        return Indexer(indexer_id)

    @staticmethod
    def get_all() -> List[Dict[str, Any]]:
        """Get every configured indexer.

        The same shape `get_data()` returns, columns included: the settings
        page keeps these rows and sends one straight back when a row's
        enable toggle is pressed, so a field missing here is a field wiped
        by that toggle.
        """
        return get_db().execute(
            """
            SELECT id, title, base_url, api_key, categories,
                   category_filter_enabled, enabled
            FROM indexers
            ORDER BY title, id;
            """
        ).fetchalldict()

    @staticmethod
    def get_one(indexer_id: int) -> Indexer:
        return Indexer(indexer_id)

    @staticmethod
    def get_enabled() -> List[Indexer]:
        "Get every indexer that is enabled, for use in a search"
        return [
            Indexer(row['id'])
            for row in get_db().execute(
                "SELECT id FROM indexers WHERE enabled = 1 ORDER BY title, id;"
            ).fetchalldict()
        ]

    @staticmethod
    def find_by_link(link: str) -> Union[Indexer, None]:
        """Find the enabled indexer that a download link belongs to, if any.
        Used by the download queue to recognise an indexer NZB link (see
        `download_queue.py`'s `__determine_link_type`).

        Args:
            link (str): The link to check.

        Returns:
            Union[Indexer, None]: The matching indexer, or `None`.
        """
        for indexer in Indexers.get_enabled():
            if link.startswith(indexer.base_url):
                return indexer
        return None


_REQUEST_LOCKS_GUARD = ThreadLock()
_REQUEST_LOCKS: "WeakKeyDictionary" = WeakKeyDictionary()
_REQUEST_STARTS: "WeakKeyDictionary" = WeakKeyDictionary()


def _request_state(indexer):
    """Return the asyncio-loop-local pacing state for one Newznab feed.

    Keyed by loop as well as by feed: each `asyncio.run` builds a new loop,
    and a lock created in a previous one cannot be awaited in this one.
    """
    loop = get_running_loop()
    key = indexer.base_url.lower()
    with _REQUEST_LOCKS_GUARD:
        locks = _REQUEST_LOCKS.setdefault(loop, {})
        starts = _REQUEST_STARTS.setdefault(loop, {})
        lock = locks.setdefault(key, AsyncLock())
    return lock, starts, key


async def search_indexer(
    session: AsyncSession,
    indexer: Indexer,
    query: str
) -> List[SearchResultData]:
    """Search a single indexer for the query, via its Newznab `t=search`
    endpoint.

    Args:
        session (AsyncSession): The session to make the request with.
        indexer (Indexer): The indexer to search.
        query (str): The query to use.

    Returns:
        List[SearchResultData]: The search results. Empty (rather than
            raising) on any request/parse failure, same as
            `search_getcomics()`'s `quiet_fail=True` -- one broken indexer
            shouldn't fail a search that combines results from several.
    """
    # Newznab had no gate at all, on the theory that Usenet indexers are
    # generous. Most are, and the default is still no delay -- but the
    # coordinator runs several phrasings concurrently against one endpoint,
    # and whether that burst is welcome is the indexer's call rather than
    # Kapowarr's. Settings > Download > Usenet Search Delay.
    # Rationed on its own, not with every other indexer behind the same
    # Prowlarr/Jackett hostname. See `register_rate_limit_scope`.
    register_rate_limit_scope(indexer.base_url)
    interval = search_delay('usenet')
    # Nothing to pace when the request is not going to be made. An indexer
    # that has already reported its quota exhausted is skipped inside the
    # session, so sleeping first spent the delay on a request that never
    # happened -- which on 2026-09-01 turned every issue of a Search All
    # sweep into nine seconds of waiting for nothing, for two hours.
    if interval > 0 and not rate_limit_cooldown_remaining(indexer.base_url):
        lock, starts, key = _request_state(indexer)
        async with lock:
            elapsed = monotonic() - starts.get(key, 0.0)
            if elapsed < interval:
                await async_sleep(interval - elapsed)
            starts[key] = monotonic()

    params = {
        "t": "search",
        "apikey": indexer.api_key,
        "o": "json",
        "extended": "1"
    }
    # An empty query is a feed request, not a search: Newznab and Torznab
    # both answer `t=search` with no `q` by returning the indexer's most
    # recent releases. One request then covers the whole library instead of
    # one volume. See `backend.features.release_feed`.
    if query:
        params["q"] = query
        if indexer.category_filter_enabled and indexer.categories:
            params["cat"] = indexer.categories
    else:
        # Same reasoning as Torznab: a feed request has no query doing the
        # narrowing, so without a category a general indexer's most recent
        # hundred items are films and television and no comic is ever seen.
        params["cat"] = indexer.categories or Constants.COMIC_CATEGORY

    body = await session.get_text(
        newznab_api_url(indexer.base_url),
        params=params,
        quiet_fail=True
    )
    if not body:
        return []

    try:
        data = json_loads(body)
    except ValueError:
        # Newznab's native format is XML; `o=json` is an extension, and
        # indexers that honour it for a search do not necessarily honour it
        # for a query-less feed request. All three of Silas's returned
        # something unparseable to every feed poll on 2026-09-02, so the feed
        # sync found nothing for half a day while reporting that it had run.
        return _results_from_xml(body, indexer)

    if not isinstance(data, dict):
        # A well-formed Newznab response is always a JSON object; a
        # misconfigured/misbehaving endpoint (wrong URL, unrelated JSON
        # API behind the same base_url, etc.) could return anything else.
        # `search_getcomics()`'s `quiet_fail=True` contract -- one broken
        # source shouldn't fail a search combining several -- only holds
        # if this function never raises, so treat any unexpected shape as
        # "no results" rather than letting a `dict`-only call blow up.
        LOGGER.warning(
            "Indexer %s returned an unexpected response shape (%s)",
            indexer.title, type(data).__name__
        )
        return []

    if "error" in data:
        LOGGER.warning(
            "Indexer %s returned an error: %s",
            indexer.title, data.get("error")
        )
        return []

    channel = data.get("channel")
    items = channel.get("item", []) if isinstance(channel, dict) else []
    if isinstance(items, dict):
        # Newznab's well-known single-result JSON quirk: `item` is a bare
        # object instead of a one-element list.
        items = [items]
    elif not isinstance(items, list):
        items = []

    results: List[SearchResultData] = []
    for item in items:
        if not isinstance(item, dict):
            continue

        title = item.get("title")
        if not title:
            continue

        link = _extract_item_link(item)
        if not link:
            continue

        results.append({
            **extract_filename_data(title, assume_volume_number=False, fix_year=True),
            "link": link,
            "display_title": title,
            "source": indexer.title
        })

    return results


def _results_from_xml(
    body: str,
    indexer: Indexer
) -> List[SearchResultData]:
    """Parse a Newznab response that came back as XML rather than JSON.

    Args:
        body (str): The response body.

        indexer (Indexer): Whose response it is, for the log and the source.

    Returns:
        List[SearchResultData]: The releases, or none if the body was neither
            a feed nor something this can make sense of.
    """
    try:
        root = ElementTree.fromstring(body)
    except ElementTree.ParseError:
        LOGGER.warning(
            "Indexer %s returned neither JSON nor XML: %s",
            indexer.title, body[:200].replace('\n', ' ')
        )
        return []

    if _local_name(root.tag) == 'error':
        LOGGER.warning(
            "Indexer %s returned an error: %s (code %s)",
            indexer.title,
            root.get('description') or 'no description',
            root.get('code') or '?'
        )
        return []

    results: List[SearchResultData] = []
    for item in root.iter():
        if _local_name(item.tag) != 'item':
            continue

        title = _child_text(item, 'title')
        if not title:
            continue

        link = _child_enclosure_url(item) or _child_text(item, 'link')
        if not link:
            continue

        results.append({
            **extract_filename_data(
                title, assume_volume_number=False, fix_year=True),
            "link": link,
            "display_title": title,
            "source": indexer.title
        })

    return results


def _local_name(tag: str) -> str:
    "An XML tag without its namespace."
    return tag.rsplit('}', 1)[-1]


def _child_text(item, name: str) -> Union[str, None]:
    "The text of a named child element, if it has one."
    for child in item:
        if _local_name(child.tag) == name and child.text:
            return child.text.strip()
    return None


def _child_enclosure_url(item) -> Union[str, None]:
    "The URL an `<enclosure>` points at, which is the NZB itself."
    for child in item:
        if _local_name(child.tag) == 'enclosure':
            url = child.get('url')
            if url:
                return url
    return None


def _extract_item_link(item: Mapping[str, Any]) -> Union[str, None]:
    """Get the direct NZB download URL out of a parsed Newznab search-result
    item. Different indexer implementations put this in slightly different
    places, so check the well-known spots in order of reliability.
    """
    enclosure = item.get("enclosure")
    if isinstance(enclosure, dict):
        # `.get("@attributes", {})` alone doesn't defend against a
        # present-but-`null` key -- the default only applies when the key
        # is *absent*, and a bare-JSON-object Newznab response can
        # legitimately have `"@attributes": null` on a malformed entry.
        url = (enclosure.get("@attributes") or {}).get("url")
        if url:
            return url

    link = item.get("link")
    if isinstance(link, str) and link:
        return link

    guid = item.get("guid")
    if isinstance(guid, dict):
        text = guid.get("#text")
        is_permalink = (guid.get("@attributes") or {}).get("isPermaLink") == "true"
        if is_permalink and text:
            return text

    return None


async def create_nzb_download(
    link: str,
    volume_id: int,
    issue_id: Union[int, None],
    force_match: bool = False
) -> Download:
    """Turn an indexer NZB link into an `NZBDownload`, for the download
    queue's 'nzb' link-type branch (see `download_queue.py`'s `add()`).
    The counterpart to `getcomics.py`'s `GetComicsPage.create_downloads()`,
    but simpler: unlike a GetComics page (which can bundle several separate
    releases under one webpage link), one indexer search result is already
    exactly one release, so there's no group/path selection to do here --
    just recover its title and check whether it matches what's being
    downloaded for.

    The link itself carries no title -- only whichever opaque id the
    indexer chose -- so the release's real name is recovered the same way
    a browser downloading it would: `Content-Disposition` on the response
    headers (see `_parse_content_disposition_filename`). This is
    Newznab-standard behaviour, not indexer-specific. The response body
    itself is never read; the actual `.nzb` fetch happens later, server-
    side, inside the Usenet client (SABnzbd's `mode=addurl` -- see
    `usenet_clients/SABnzbd.py`), not here.

    Args:
        link (str): The indexer's NZB download link.
        volume_id (int): The volume the download is intended for.
        issue_id (Union[int, None]): The issue the download is intended
            for, if any.
        force_match (bool, optional): Skip matching and accept the release
            as-is. Defaults to False.

    Raises:
        EnqueuingDownloadFailure: The indexer said the release is gone
            (LINK_BROKEN), the indexer could not be reached or answered
            with anything else (SOURCE_UNAVAILABLE), or the release doesn't
            match what's being downloaded for and force_match is False
            (RESULT_DOES_NOT_MATCH). Only the first is blocklisted.

    Returns:
        Download: The download, ready to be queued.
    """
    indexer = Indexers.find_by_link(link)
    source_name = indexer.title if indexer else "Usenet indexer"

    try:
        async with AsyncSession() as session:
            async with session.get(link) as r:
                if not r.ok:
                    # Named, because the two outcomes below are very
                    # different and nothing in the log used to say which
                    # had happened -- a run on 2026-09-02 lost fourteen
                    # releases to the blocklist without recording what the
                    # indexer had actually answered.
                    LOGGER.warning(
                        'Indexer answered HTTP %d for a release of %s',
                        r.status, source_name
                    )
                    raise EnqueuingDownloadFailure(
                        EnqueuingDownloadFailureReason.for_fetch_status(r.status)
                    )
                title = _parse_content_disposition_filename(
                    r.headers.get("Content-Disposition", "")
                )

    except ClientError:
        # Nothing came back at all -- a reset, a DNS failure, a timeout.
        # That is the indexer, not the release.
        raise EnqueuingDownloadFailure(
            EnqueuingDownloadFailureReason.SOURCE_UNAVAILABLE
        )

    if not title:
        title = link.rsplit("/", 1)[-1] or "unknown release"

    volume = Volume(volume_id)
    volume_data = volume.get_data()
    # `title` is the full recovered filename (e.g. "Batman (2020) 001.nzb")
    # -- strip a recognised trailing extension before parsing, same as
    # `NZBDownload.__init__`'s own `splitext(web_title or 'unknown')[0]`
    # does for its `_filename_body`. ".nzb" isn't a comic-archive extension
    # `extract_filename_data` knows to strip on its own, so left attached
    # it corrupts issue-number extraction.
    info = extract_filename_data(
        splitext(title)[0], assume_volume_number=False, fix_year=True
    )
    info = refine_special_version(volume_data, info)

    covered_issues = info["issue_number"]

    if not force_match:
        volume_issues = volume.get_issues()
        number_to_year = {
            i.calculated_issue_number: extract_year_from_date(i.date)
            for i in volume_issues
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
            "link": link,
            "display_title": title,
            "source": source_name
        }
        match = check_search_result_match(
            result, volume_data, volume_issues,
            number_to_year, calculated_issue_number
        )
        if not match["match"]:
            raise EnqueuingDownloadFailure(
                EnqueuingDownloadFailureReason.RESULT_DOES_NOT_MATCH
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
