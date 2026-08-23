# -*- coding: utf-8 -*-

"""Grand Comics Database (GCD) metadata provider.

Built against GCD's public REST API (`https://www.comics.org/api/`), not the
bulk database dump: the dump ships no image URLs at all (its reference
consumer, `comictagger/gcd_talker`, has to scrape `comics.org` HTML and
detect Cloudflare challenge pages to get covers), is regenerated bi-weekly
behind an account login, and is roughly 6 GB. The API returns cover URLs
directly and is measured to be fast enough for ordinary library operations
without a login (see `projects/kapowarr/docs/t-059-gcd-metadata-provider.md`
in the conductor repo for the full evaluation this provider implements).

GCD identities remain GCD identities: `comicvine_id` is always `None` here,
the same way Metron never invents a ComicVine ID for a series it has no
cross-link for. GCD has no ComicVine cross-link at all.

Three measured traps this module exists specifically to avoid:

1. **Search pagination is unbounded and cannot be capped by the caller.**
   `/api/series/name/{name}/` ignores `page_size`/`limit` entirely and
   always returns exactly 50 results per page; a one-character query is
   3,571 pages. Unlike Metron's `_all()`, `search_volumes()` here reads only
   the first page and never follows `next`.
2. **The API is DRF with a browsable HTML renderer.** Without
   `Accept: application/json` (or `?format=json`), a request succeeds with
   HTTP 200 and an HTML body instead of JSON. Both are sent on every
   request, belt-and-suspenders.
3. **A `/` in a series name 404s even percent-encoded** (Apache's
   `AllowEncodedSlashes off`, upstream of Django) and returns an Apache HTML
   error page, not a JSON error body -- `search_volumes()` strips `/` from
   the query before building the request so ordinary slash-containing
   titles stay searchable under their surrounding words, and `_get()`
   guards JSON parsing the way Metron's does for whatever reaches it anyway.

Two more differences from Metron worth knowing before touching this file:

- GCD has no series-level volume number (`volume_number` always defaults to
  `1`, never adopted from `year_began` -- that would silently change folder
  naming and matching for anyone who later enables it).
- `publisher` comes back as a URL, not an inline name; it is resolved once
  per `fetch_volume` call (never per search hit) through a small id->name
  cache on the instance, the same reasoning Metron's `fetch_volume` already
  documents for skipping a per-issue detail request.
"""

from typing import Any, Dict, List, Sequence, Union
from urllib.parse import quote

from aiohttp import BasicAuth, ClientError
from asyncio import run

from backend.base.logging import LOGGER
from backend.base.definitions import Constants, IssueMetadata, VolumeMetadata
from backend.base.file_extraction import extract_issue_number
from backend.base.helpers import AsyncSession, force_range, normalise_string
from backend.features.metadata import (MetadataCapability,
                                       MetadataIdentityStore, MetadataProvider,
                                       MetadataProviderRegistry)
from backend.internals.settings import Settings


class GcdError(RuntimeError):
    """GCD was unavailable or rejected the request."""


@MetadataProviderRegistry.register
class Gcd(MetadataProvider):
    provider_id = 'gcd'
    display_name = 'Grand Comics Database'
    capabilities = (
        MetadataCapability.SEARCH_VOLUMES,
        MetadataCapability.FETCH_VOLUME,
        MetadataCapability.FETCH_VOLUMES,
        MetadataCapability.FETCH_ISSUES,
        MetadataCapability.COVERS
    )
    unavailable_errors = (GcdError,)

    # No `is_configured()` override: GCD's API is usable anonymously (20
    # sequential requests in 11.5s drew no 429 in evaluation), unlike
    # ComicVine/Metron which require a key/token to do anything at all.
    # `gcd_username`/`gcd_password` below are an optional account for GCD's
    # higher authenticated rate limit, not a precondition for the provider
    # to take part in a fan-out. The base class's default `True` is correct
    # as-is.

    def __init__(
        self,
        gcd_username: Union[str, None] = None,
        gcd_password: Union[str, None] = None
    ) -> None:
        settings = Settings().get_settings()
        self.username = gcd_username or settings.gcd_username
        self.password = gcd_password or settings.gcd_password
        self.auth = (
            BasicAuth(self.username, self.password)
            if self.username and self.password else None
        )
        # Publisher id -> name, populated lazily by `_publisher_name()`.
        # Deliberately per-instance rather than module-level: a provider
        # instance is already short-lived (constructed per call by the
        # registry), so this only saves the repeat lookups within one
        # `fetch_volume`/`fetch_volumes` call, which is exactly where they
        # occur (many issues, few distinct publishers).
        self._publisher_cache: Dict[str, str] = {}

    async def _get(
        self, path: str, params: Union[Dict[str, Any], None] = None
    ) -> Dict[str, Any]:
        try:
            async with AsyncSession() as session:
                response = await session.get(
                    f'{Constants.GCD_API_URL}/{path.strip("/")}/',
                    params={**(params or {}), 'format': 'json'},
                    auth=self.auth,
                    headers={'Accept': 'application/json'}
                )
                if response.status in (401, 403, 429):
                    raise GcdError(f'GCD returned HTTP {response.status}')
                # Covers the Apache-HTML 404 case too (trap 3 above):
                # `raise_for_status()` raises on any 4xx/5xx regardless of
                # the body, and the `ValueError` guard below catches a `200`
                # that is HTML anyway, in case `format=json` is ever dropped.
                response.raise_for_status()
                result: Dict[str, Any] = await response.json(
                    content_type=None
                )
                return result
        except (ClientError, ValueError) as exc:
            raise GcdError('Unable to read GCD metadata') from exc

    def _volume(self, data: Dict[str, Any]) -> VolumeMetadata:
        gcd_id = str(data['id'])
        issue_ids = data.get('active_issues') or []
        result: VolumeMetadata = {
            'provider_id': self.provider_id,
            'external_id': gcd_id,
            # GCD has no ComicVine cross-link, unlike Metron's `cv_id`.
            'comicvine_id': None,
            'title': normalise_string(data.get('name') or ''),
            'year': data.get('year_began'),
            # No series-level volume number in GCD; see module docstring.
            'volume_number': 1,
            'cover_link': '',
            'cover_source': {
                'provider_id': self.provider_id,
                'external_id': gcd_id,
                'source_url': ''
            },
            'cover': None,
            'description': normalise_string(data.get('notes') or ''),
            'site_url': f'{Constants.GCD_SITE_URL}/series/{gcd_id}/',
            'aliases': [],
            # Resolved in `fetch_volume` only; a search result keeps this
            # `None` rather than paying for 50 publisher requests per page.
            'publisher': None,
            'issue_count': len(issue_ids),
            'translated': False,
            'already_added': None,
            'issues': None
        }
        return result

    def _issue(
        self, data: Dict[str, Any], volume_external_id: str
    ) -> IssueMetadata:
        number = str(data.get('number') or '').replace('/', '-').strip()
        calculated = force_range(extract_issue_number(number))[0]
        title = self._issue_title(data)
        return {
            'provider_id': self.provider_id,
            'external_id': str(data['issue_id']),
            'volume_external_id': volume_external_id,
            'comicvine_id': None,
            'volume_id': None,
            'issue_number': number,
            'calculated_issue_number': calculated or 0.0,
            'title': normalise_string(title) if title else None,
            'date': self._normalise_date(data),
            'description': ((data.get('longest_story') or {}).get('synopsis')
                            or '')
        }

    @staticmethod
    def _issue_title(data: Dict[str, Any]) -> Union[str, None]:
        """Best-effort issue title from an `overview` row.

        `overview` rows carry no standalone `title` field (only the full
        per-issue detail record does, which this provider deliberately never
        requests -- see the module docstring). `descriptor` is
        `"<number> - <title>"`; strip the redundant leading number and fall
        back to the longest story's title when the descriptor has none.
        """
        descriptor = data.get('descriptor') or ''
        _, _, remainder = descriptor.partition(' - ')
        remainder = remainder.strip()
        if remainder:
            return remainder
        story = data.get('longest_story') or {}
        return story.get('title') or None

    @staticmethod
    def _normalise_date(data: Dict[str, Any]) -> Union[str, None]:
        """`on_sale_date` when present; otherwise `key_date` with its zero
        placeholders truncated rather than emitted.

        `key_date` uses `00` for unknown month/day components --
        `"1959-00-00"` is a real returned value, and it is not a valid date.
        `on_sale_date` is a proper ISO date when GCD has one and empty
        otherwise. Truncating (`"1959-00-00"` -> `"1959"`,
        `"1959-05-00"` -> `"1959-05"`) keeps whatever precision is real
        instead of discarding the whole value for one unknown component.
        """
        on_sale = (data.get('on_sale_date') or '').strip()
        if on_sale:
            return on_sale
        key_date = (data.get('key_date') or '').strip()
        if not key_date:
            return None
        year, _, rest = key_date.partition('-')
        month, _, day = rest.partition('-')
        if not year or year == '0000':
            return None
        if not month or month == '00':
            return year
        if not day or day == '00':
            return f'{year}-{month}'
        return key_date

    async def _issue_overview(
        self, external_id: Union[str, int]
    ) -> List[Dict[str, Any]]:
        """Walk `/series/{id}/overview/` to completion.

        Unlike search (trap 1 in the module docstring), this pagination is
        bounded by the series' real issue count -- `ceil(n / 50)` requests,
        measured as 20 requests for a 973-issue series -- so following
        `next` to completion is safe here.
        """
        page = await self._get(f'series/{external_id}/overview')
        results = list(page.get('results') or [])
        page_number = 1
        while page.get('next'):
            page_number += 1
            page = await self._get(
                f'series/{external_id}/overview', {'page': page_number}
            )
            results.extend(page.get('results') or [])
        return results

    async def _publisher_name(
        self, publisher_url: Union[str, None]
    ) -> Union[str, None]:
        """Resolve a series' `publisher` URL to a name, cached per-instance.

        The id is extracted from the URL and re-requested through the
        configured API origin rather than following the URL verbatim -- the
        same reasoning Metron's `_all()` documents for not following its
        `next` URL directly.
        """
        if not publisher_url:
            return None
        publisher_id = publisher_url.rstrip('/').rsplit('/', 1)[-1]
        if not publisher_id.isdigit():
            return None
        if publisher_id in self._publisher_cache:
            return self._publisher_cache[publisher_id]
        try:
            data = await self._get(f'publisher/{publisher_id}')
        except GcdError:
            return None
        name = data.get('name')
        if name:
            self._publisher_cache[publisher_id] = name
        return name

    def test_key(self) -> bool:
        async def test() -> bool:
            try:
                await self._get('series/1')
            except GcdError:
                return False
            return True
        return run(test())

    async def search_volumes(self, query: str) -> List[VolumeMetadata]:
        # `/` is rejected by Apache upstream of Django even percent-encoded
        # (trap 3); stripping it keeps the surrounding words searchable
        # instead of failing the whole query outright.
        sanitised = query.replace('/', ' ').strip()
        if not sanitised:
            return []
        page = await self._get(f'series/name/{quote(sanitised, safe="")}')
        # One unusable entry must not cost the whole search. GCD has returned
        # series rows with no `id`, and indexing it unconditionally raised
        # KeyError out through the search endpoint -- so a query that matched
        # twenty series returned nothing at all and looked like a total
        # failure. An entry with no id could not be added anyway: there is
        # nothing to link it to.
        results = []
        for item in page.get('results') or []:
            if not isinstance(item, dict) or item.get('id') is None:
                LOGGER.debug(
                    'Skipping unidentifiable GCD series in results for %r',
                    sanitised
                )
                continue
            results.append(self._volume(item))
        for item in results:
            item['already_added'] = MetadataIdentityStore.resolve(
                'volume', self.provider_id, item['external_id']
            )
        return results

    async def fetch_volume(self, external_id: Union[str, int]) -> VolumeMetadata:
        raw = await self._get(f'series/{external_id}')
        volume = self._volume(raw)
        volume['publisher'] = await self._publisher_name(raw.get('publisher'))

        overview_rows = await self._issue_overview(external_id)
        volume['issues'] = [
            self._issue(row, volume['external_id']) for row in overview_rows
        ]
        # Series summaries carry no cover of their own; fall back to the
        # first issue with one, the same reasoning Metron's fetch_volume
        # documents (metron.py:173-175).
        cover_url = next(
            (row.get('cover_url') for row in overview_rows
             if row.get('cover_url')),
            ''
        )
        if cover_url:
            volume['cover_link'] = cover_url
            volume['cover_source']['source_url'] = cover_url
            try:
                async with AsyncSession() as session:
                    volume['cover'] = await session.get_content(
                        cover_url, quiet_fail=True
                    ) or None
            except ClientError:
                pass
        return volume

    async def fetch_volumes(
        self, external_ids: Sequence[Union[str, int]]
    ) -> List[VolumeMetadata]:
        return [await self.fetch_volume(external_id) for external_id in external_ids]

    async def fetch_issues(
        self, volume_external_ids: Sequence[Union[str, int]]
    ) -> List[IssueMetadata]:
        results: List[IssueMetadata] = []
        for external_id in volume_external_ids:
            overview_rows = await self._issue_overview(external_id)
            results.extend(
                self._issue(row, str(external_id)) for row in overview_rows
            )
        return results
