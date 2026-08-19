# -*- coding: utf-8 -*-

"""Metadata-provider contracts, registration and persistent identities.

Provider IDs are stable, lowercase storage keys.  Display names and credentials
are deliberately kept out of library identity so providers can be reconfigured
without orphaning volumes or issues.
"""

from abc import ABC, abstractmethod
from enum import Enum
from time import time
from typing import Dict, List, Sequence, Tuple, Union

from backend.base.definitions import IssueMetadata, VolumeMetadata
from backend.internals.db import get_db


class MetadataCapability(Enum):
    SEARCH_VOLUMES = 'search_volumes'
    FETCH_VOLUME = 'fetch_volume'
    FETCH_VOLUMES = 'fetch_volumes'
    FETCH_ISSUES = 'fetch_issues'
    COVERS = 'covers'


class MetadataProvider(ABC):
    """Normalized boundary implemented by every metadata provider."""

    provider_id: str
    display_name: str
    capabilities: Tuple[MetadataCapability, ...]

    @abstractmethod
    def test_key(self) -> bool:
        pass

    @abstractmethod
    async def search_volumes(self, query: str) -> List[VolumeMetadata]:
        pass

    @abstractmethod
    async def fetch_volume(
        self, external_id: Union[str, int]
    ) -> VolumeMetadata:
        pass

    @abstractmethod
    async def fetch_volumes(
        self, external_ids: Sequence[Union[str, int]]
    ) -> List[VolumeMetadata]:
        pass

    @abstractmethod
    async def fetch_issues(
        self, volume_external_ids: Sequence[Union[str, int]]
    ) -> List[IssueMetadata]:
        pass


class MetadataProviderRegistry:
    """Lazy provider registry; registration never constructs API clients."""

    _providers: Dict[str, type] = {}

    @classmethod
    def register(cls, provider: type) -> type:
        provider_id = getattr(provider, 'provider_id', '')
        if not provider_id or provider_id != provider_id.lower():
            raise ValueError('Metadata provider IDs must be stable lowercase keys')
        if provider_id in cls._providers and cls._providers[provider_id] is not provider:
            raise ValueError(f'Metadata provider already registered: {provider_id}')
        cls._providers[provider_id] = provider
        return provider

    @classmethod
    def get(cls, provider_id: str = 'comicvine', **kwargs) -> MetadataProvider:
        if provider_id not in cls._providers:
            # Import the built-in lazily to avoid settings/database import cycles.
            if provider_id == 'comicvine':
                from backend.implementations import comicvine  # noqa: F401
            elif provider_id == 'metron':
                from backend.implementations import metron  # noqa: F401
        try:
            provider = cls._providers[provider_id]
        except KeyError:
            raise KeyError(f'Unknown metadata provider: {provider_id}')
        return provider(**kwargs)

    @classmethod
    def capabilities(cls) -> Dict[str, Tuple[MetadataCapability, ...]]:
        if 'comicvine' not in cls._providers:
            from backend.implementations import comicvine  # noqa: F401
        if 'metron' not in cls._providers:
            from backend.implementations import metron  # noqa: F401
        return {
            provider_id: provider.capabilities
            for provider_id, provider in cls._providers.items()
        }


class MetadataIdentityStore:
    """Cross-provider IDs with deterministic, database-enforced conflicts.

    An entity may have one ID per provider, and a provider ID may identify only
    one local entity.  Adding a second provider enriches identity; it never
    replaces or silently merges the existing primary identity.
    """

    _tables = {
        'volume': ('volume_external_ids', 'volume_id'),
        'issue': ('issue_external_ids', 'issue_id')
    }

    @classmethod
    def set(
        cls,
        entity_type: str,
        entity_id: int,
        provider_id: str,
        external_id: Union[str, int],
        *,
        source_url: Union[str, None] = None
    ) -> None:
        table, id_column = cls._tables[entity_type]
        get_db().execute(f"""
            INSERT INTO {table}(
                {id_column}, provider_id, external_id, source_url, updated_at
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT({id_column}, provider_id) DO UPDATE SET
                external_id = excluded.external_id,
                source_url = COALESCE(excluded.source_url, {table}.source_url),
                updated_at = excluded.updated_at;
            """, (
                entity_id, provider_id, str(external_id), source_url, round(time())
            ))

    @classmethod
    def get(cls, entity_type: str, entity_id: int) -> Dict[str, str]:
        table, id_column = cls._tables[entity_type]
        return dict(get_db().execute(f"""
            SELECT provider_id, external_id
            FROM {table}
            WHERE {id_column} = ?
            ORDER BY provider_id;
            """, (entity_id,)))

    @classmethod
    def resolve(
        cls, entity_type: str, provider_id: str, external_id: Union[str, int]
    ) -> Union[int, None]:
        table, id_column = cls._tables[entity_type]
        row = get_db().execute(f"""
            SELECT {id_column}
            FROM {table}
            WHERE provider_id = ? AND external_id = ?
            LIMIT 1;
            """, (provider_id, str(external_id))).fetchone()
        return row[0] if row else None


def get_metadata_provider(
    provider_id: str = 'comicvine', **kwargs
) -> MetadataProvider:
    return MetadataProviderRegistry.get(provider_id, **kwargs)


def _metron_is_configured() -> bool:
    from backend.internals.settings import Settings
    settings = Settings().get_settings()
    return bool(settings.metron_username and settings.metron_password)


async def search_metadata_with_fallback(query: str) -> List[VolumeMetadata]:
    """Search ComicVine, falling back only after an availability failure."""
    from backend.base.custom_exceptions import (CVRateLimitReached,
                                                InvalidComicVineApiKey)
    try:
        return await get_metadata_provider().search_volumes(query)
    except (CVRateLimitReached, InvalidComicVineApiKey):
        if not _metron_is_configured():
            raise
        return await get_metadata_provider('metron').search_volumes(query)


async def fetch_volume_with_fallback(cv_id: int) -> VolumeMetadata:
    """Fetch a CV-linked volume from Metron if ComicVine is unavailable."""
    from backend.base.custom_exceptions import (CVRateLimitReached,
                                                InvalidComicVineApiKey)
    try:
        return await get_metadata_provider().fetch_volume(cv_id)
    except (CVRateLimitReached, InvalidComicVineApiKey):
        if not _metron_is_configured():
            raise
        metron = get_metadata_provider('metron')
        return await metron.fetch_volume_by_comicvine_id(cv_id)  # type: ignore


async def fetch_volumes_with_fallback(
    cv_ids: Sequence[int]
) -> List[VolumeMetadata]:
    """Bulk variant of :func:`fetch_volume_with_fallback`."""
    from backend.base.custom_exceptions import (CVRateLimitReached,
                                                InvalidComicVineApiKey)
    try:
        volumes = await get_metadata_provider().fetch_volumes(cv_ids)
    except (CVRateLimitReached, InvalidComicVineApiKey):
        if not _metron_is_configured():
            raise
        metron = get_metadata_provider('metron')
        return await metron.fetch_volumes_by_comicvine_ids(cv_ids)  # type: ignore

    if not _metron_is_configured():
        return volumes

    returned_ids = {
        volume['comicvine_id']
        for volume in volumes
        if volume['comicvine_id'] is not None
    }
    missing_ids = [cv_id for cv_id in cv_ids if cv_id not in returned_ids]
    if missing_ids:
        metron = get_metadata_provider('metron')
        volumes.extend(
            await metron.fetch_volumes_by_comicvine_ids(missing_ids)  # type: ignore
        )
    return volumes
