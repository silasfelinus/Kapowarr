# -*- coding: utf-8 -*-

"""Metron metadata provider.

Metron identities remain Metron identities. ComicVine IDs returned by Metron
are compatibility links only; callers may use them to enrich an existing
library entity, but this provider never substitutes one identifier for the
other.
"""

from asyncio import run
from typing import Any, Dict, List, Sequence, Union

from aiohttp import BasicAuth, ClientError

from backend.base.definitions import Constants, IssueMetadata, VolumeMetadata
from backend.base.file_extraction import extract_issue_number
from backend.base.helpers import AsyncSession, force_range, normalise_string
from backend.features.metadata import (MetadataCapability,
                                       MetadataIdentityStore, MetadataProvider,
                                       MetadataProviderRegistry)
from backend.internals.settings import Settings


class MetronError(RuntimeError):
    """Metron was unavailable or rejected the configured credentials."""


@MetadataProviderRegistry.register
class Metron(MetadataProvider):
    provider_id = 'metron'
    display_name = 'Metron'
    capabilities = (
        MetadataCapability.SEARCH_VOLUMES,
        MetadataCapability.FETCH_VOLUME,
        MetadataCapability.FETCH_VOLUMES,
        MetadataCapability.FETCH_ISSUES,
        MetadataCapability.COVERS
    )
    unavailable_errors = (MetronError,)

    @classmethod
    def is_configured(cls) -> bool:
        settings = Settings().get_settings()
        return bool(
            settings.metron_api_token
            or (settings.metron_username and settings.metron_password)
        )

    def __init__(
        self,
        metron_api_token: Union[str, None] = None,
        metron_username: Union[str, None] = None,
        metron_password: Union[str, None] = None
    ) -> None:
        settings = Settings().get_settings()
        self.token = metron_api_token or settings.metron_api_token
        self.username = metron_username or settings.metron_username
        self.password = metron_password or settings.metron_password
        if not self.token and (not self.username or not self.password):
            raise MetronError(
                'A Metron API token or username and password are required'
            )
        if self.token:
            self.auth = None
            self.headers = {'Authorization': f'Bearer {self.token}'}
        else:
            assert self.username and self.password
            self.auth = BasicAuth(self.username, self.password)
            self.headers = {}
        self.date_type = settings.date_type.value

    async def _get(
        self, path: str, params: Union[Dict[str, Any], None] = None
    ) -> Dict[str, Any]:
        try:
            async with AsyncSession() as session:
                response = await session.get(
                    f'{Constants.METRON_API_URL}/{path.strip("/")}/',
                    params=params or {}, auth=self.auth, headers=self.headers
                )
                if response.status in (401, 403, 429):
                    raise MetronError(f'Metron returned HTTP {response.status}')
                response.raise_for_status()
                result: Dict[str, Any] = await response.json()
                return result
        except (ClientError, ValueError) as exc:
            raise MetronError('Unable to read Metron metadata') from exc

    @staticmethod
    def _volume(data: Dict[str, Any]) -> VolumeMetadata:
        image = data.get('image') or ''
        metron_id = str(data['id'])
        cv_id = data.get('cv_id')
        year = data.get('year_began')
        title = data.get('name') or data.get('series') or ''
        year_suffix = f' ({year})'
        if not data.get('name') and year and title.endswith(year_suffix):
            title = title[:-len(year_suffix)]
        result: VolumeMetadata = {
            'provider_id': 'metron',
            'external_id': metron_id,
            'comicvine_id': int(cv_id) if cv_id else None,
            'title': normalise_string(title),
            'year': year,
            'volume_number': int(data.get('volume') or 1),
            'cover_link': image,
            'cover_source': {
                'provider_id': 'metron',
                'external_id': metron_id,
                'source_url': image
            },
            'cover': None,
            'description': data.get('desc') or '',
            'site_url': data.get('resource_url') or '',
            'aliases': list(data.get('alt_names') or []),
            'publisher': (data.get('publisher') or {}).get('name'),
            'issue_count': int(data.get('issue_count') or 0),
            'translated': False,
            'already_added': None,
            'issues': None
        }
        return result

    def _issue(self, data: Dict[str, Any]) -> IssueMetadata:
        number = str(data.get('number') or '').replace('/', '-').strip()
        calculated = force_range(extract_issue_number(number))[0]
        series = data.get('series') or {}
        cv_id = data.get('cv_id')
        return {
            'provider_id': self.provider_id,
            'external_id': str(data['id']),
            'volume_external_id': str(series.get('id') or ''),
            'comicvine_id': int(cv_id) if cv_id else None,
            'volume_id': None,
            'issue_number': number,
            'calculated_issue_number': calculated or 0.0,
            'title': normalise_string(data.get('title') or '') or None,
            'date': data.get(self.date_type) or None,
            'description': data.get('desc') or ''
        }

    async def _all(self, path: str, params: Dict[str, Any]) -> List[Dict[str, Any]]:
        page = await self._get(path, params)
        results = list(page.get('results') or [])
        while page.get('next'):
            # Metron's next URL is deliberately not followed verbatim: keeping
            # requests on the configured API origin prevents credential leaks.
            params = {**params, 'page': int(params.get('page', 1)) + 1}
            page = await self._get(path, params)
            results.extend(page.get('results') or [])
        return results

    def test_key(self) -> bool:
        async def test() -> bool:
            try:
                await self._get('series', {'page_size': 1})
            except MetronError:
                return False
            return True
        return run(test())

    async def search_volumes(self, query: str) -> List[VolumeMetadata]:
        params: Dict[str, Any]
        if query.startswith(('4050-', 'cv:')):
            params = {'cv_id': query.split('-', 1)[-1].split(':')[-1]}
        else:
            params = {'q': query, 'page_size': 50}
        results = [
            self._volume(item)
            for item in await self._all('series', params)
        ]
        for item in results:
            item['already_added'] = MetadataIdentityStore.resolve(
                'volume', self.provider_id, item['external_id']
            )
        return results

    async def fetch_volume(self, external_id: Union[str, int]) -> VolumeMetadata:
        volume = self._volume(await self._get(f'series/{external_id}'))
        summaries = await self._all('issue', {'series_id': external_id})
        if not volume['cover_link'] and summaries:
            volume['cover_link'] = summaries[0].get('image') or ''
            volume['cover_source']['source_url'] = volume['cover_link']
        # List results contain every field Kapowarr needs to establish an issue
        # identity. Avoid a detail request per issue: a long-running series can
        # otherwise exhaust Metron's burst quota during one add or refresh.
        volume['issues'] = [self._issue(summary) for summary in summaries]
        if volume['cover_link']:
            try:
                async with AsyncSession() as session:
                    volume['cover'] = await session.get_content(
                        volume['cover_link'], quiet_fail=True
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
            summaries = await self._all('issue', {'series_id': external_id})
            results.extend(self._issue(summary) for summary in summaries)
        return results

    async def fetch_volume_by_comicvine_id(self, cv_id: int) -> VolumeMetadata:
        """Resolve a Metron series through its explicit ComicVine cross-link."""
        matches = await self._all('series', {'cv_id': cv_id})
        if len(matches) != 1:
            raise MetronError(f'Metron has no unique series for ComicVine ID {cv_id}')
        return await self.fetch_volume(matches[0]['id'])

    async def fetch_volumes_by_comicvine_ids(
        self, cv_ids: Sequence[int]
    ) -> List[VolumeMetadata]:
        """Fetch linked series without confusing cross-links with Metron IDs."""
        volumes: List[VolumeMetadata] = []
        for cv_id in cv_ids:
            matches = await self._all('series', {'cv_id': cv_id})
            if len(matches) == 1:
                volumes.append(await self.fetch_volume(matches[0]['id']))
        return volumes
