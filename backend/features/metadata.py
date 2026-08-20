# -*- coding: utf-8 -*-

"""Metadata-provider contracts, registration and persistent identities.

Provider IDs are stable, lowercase storage keys.  Display names and credentials
are deliberately kept out of library identity so providers can be reconfigured
without orphaning volumes or issues.
"""

from abc import ABC, abstractmethod
from asyncio import gather
from enum import Enum
from functools import wraps
from importlib import import_module
from time import time
from typing import Dict, List, Sequence, Tuple, Union

from backend.base.definitions import IssueMetadata, VolumeMetadata
from backend.internals.db import get_db

#: The provider every ComicVine-ID-shaped code path still assumes.
DEFAULT_METADATA_PROVIDER_ID = 'comicvine'


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

    #: Errors that mean "this provider cannot answer right now" rather than
    #: "something is broken".  A fan-out across several providers tolerates
    #: these as long as another provider answered; anything else propagates.
    unavailable_errors: Tuple[type, ...] = ()

    #: Opt in to have every operation wrapped with System Events counters.
    #: False by default so nothing changes for providers that do not ask.
    instrument_operations: bool = False

    #: Exception type -> outcome bucket, consulted in declaration order by
    #: the instrumentation wrapper. An exception matching none of these is
    #: counted as `other_error`. Unused when `instrument_operations` is False.
    operation_outcomes: Dict[type, str] = {}

    @classmethod
    def is_configured(cls) -> bool:
        """Whether this provider holds the credentials to take part in a
        fan-out.  Providers that need no configuration inherit ``True``."""
        return True

    @classmethod
    def is_unavailable_error(cls, error: BaseException) -> bool:
        """Whether `error` is this provider being unavailable, not a bug."""
        return isinstance(error, cls.unavailable_errors)

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

    #: Built-in providers and the module whose import registers them.  Adding a
    #: provider here is the only change the registry needs; nothing else in
    #: this module names a provider.  Imports stay lazy to avoid
    #: settings/database import cycles.
    _builtin_modules: Dict[str, str] = {
        'comicvine': 'backend.implementations.comicvine',
        'metron': 'backend.implementations.metron'
    }

    @staticmethod
    def _instrument_operations(provider: type) -> type:
        """Measure provider operations without changing provider semantics.

        The outcome bucket for a raised exception comes from the provider's
        own `operation_outcomes` table (first matching type wins); anything
        unmapped is counted as `other_error`. This keeps the wrapper generic
        while letting each provider that opts in via `instrument_operations`
        classify its own exception types.
        """
        from backend.features.system_events import (
            begin_comicvine_operation,
            finish_comicvine_operation,
        )

        outcome_map = getattr(provider, 'operation_outcomes', {})

        for operation in (
            'search_volumes',
            'fetch_volume',
            'fetch_volumes',
            'fetch_issues',
        ):
            original = getattr(provider, operation)
            if getattr(original, '_kapowarr_events_instrumented', False):
                continue

            def instrument(method, operation_name):
                @wraps(method)
                async def wrapped(self, *args, **kwargs):
                    operation_key = begin_comicvine_operation(operation_name)
                    try:
                        result = await method(self, *args, **kwargs)
                    except Exception as error:
                        outcome = 'other_error'
                        for error_type, mapped_outcome in outcome_map.items():
                            if isinstance(error, error_type):
                                outcome = mapped_outcome
                                break
                        finish_comicvine_operation(operation_key, outcome)
                        raise

                    finish_comicvine_operation(operation_key, 'success')
                    return result

                wrapped._kapowarr_events_instrumented = True
                return wrapped

            setattr(provider, operation, instrument(original, operation))

        return provider

    @classmethod
    def register(cls, provider: type) -> type:
        provider_id = getattr(provider, 'provider_id', '')
        if not provider_id or provider_id != provider_id.lower():
            raise ValueError('Metadata provider IDs must be stable lowercase keys')
        if provider_id in cls._providers and cls._providers[provider_id] is not provider:
            raise ValueError(f'Metadata provider already registered: {provider_id}')
        if getattr(provider, 'instrument_operations', False):
            provider = cls._instrument_operations(provider)
        cls._providers[provider_id] = provider
        return provider

    @classmethod
    def _load_builtin(cls, provider_id: str) -> None:
        """Import the module that registers `provider_id`, if it is built in."""
        if provider_id in cls._providers:
            return
        module = cls._builtin_modules.get(provider_id)
        if module is not None:
            import_module(module)

    @classmethod
    def _load_builtins(cls) -> None:
        for provider_id in cls._builtin_modules:
            cls._load_builtin(provider_id)

    @classmethod
    def provider_class(cls, provider_id: str) -> type:
        """The registered class for `provider_id`, without constructing it."""
        cls._load_builtin(provider_id)
        try:
            return cls._providers[provider_id]
        except KeyError:
            raise KeyError(f'Unknown metadata provider: {provider_id}')

    @classmethod
    def get(
        cls, provider_id: str = DEFAULT_METADATA_PROVIDER_ID, **kwargs
    ) -> MetadataProvider:
        return cls.provider_class(provider_id)(**kwargs)

    @classmethod
    def capabilities(cls) -> Dict[str, Tuple[MetadataCapability, ...]]:
        cls._load_builtins()
        return {
            provider_id: provider.capabilities
            for provider_id, provider in cls._providers.items()
        }

    @classmethod
    def configured_provider_ids(
        cls, capability: Union[MetadataCapability, None] = None
    ) -> List[str]:
        """Every registered provider that has the credentials to be used.

        `capability` narrows the list to providers that can actually perform
        the operation in question, so a provider that does not search is never
        included in a search fan-out.

        Ordered deterministically — the default provider first, then the rest
        alphabetically — so a fan-out's result order never depends on which
        module happened to be imported first.
        """
        cls._load_builtins()
        return sorted(
            (
                provider_id
                for provider_id, provider in cls._providers.items()
                if provider.is_configured()
                and (capability is None or capability in provider.capabilities)
            ),
            key=lambda provider_id: (
                provider_id != DEFAULT_METADATA_PROVIDER_ID, provider_id
            )
        )


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
    provider_id: str = DEFAULT_METADATA_PROVIDER_ID, **kwargs
) -> MetadataProvider:
    return MetadataProviderRegistry.get(provider_id, **kwargs)


def configured_metadata_provider_ids(
    capability: Union[MetadataCapability, None] = None
) -> List[str]:
    """Registered providers that hold the credentials to be used."""
    return MetadataProviderRegistry.configured_provider_ids(capability)


def is_metadata_provider_configured(provider_id: str) -> bool:
    """Whether `provider_id` is registered and configured."""
    try:
        return MetadataProviderRegistry.provider_class(
            provider_id
        ).is_configured()
    except KeyError:
        return False


async def search_metadata_with_fallback(query: str) -> List[VolumeMetadata]:
    """Search every configured provider while keeping identities explicit.

    A provider that reports itself unavailable (see
    :attr:`MetadataProvider.unavailable_errors`) is skipped as long as another
    provider answered; if every provider is unavailable, the first such error
    is raised.  Anything else propagates immediately — an unavailable provider
    is a degraded search, an unexpected error is a bug.
    """
    async def search_provider(provider_id: str) -> List[VolumeMetadata]:
        # Constructing inside the coroutine keeps a provider whose credentials
        # are rejected at construction time an error of *that* provider's
        # search, not of the whole fan-out.
        return await get_metadata_provider(provider_id).search_volumes(query)

    provider_ids = configured_metadata_provider_ids(
        MetadataCapability.SEARCH_VOLUMES
    )
    if len(provider_ids) <= 1:
        # A lone provider owns the outcome, so its errors are the search's
        # errors — there is no second opinion to fall back to.
        return await search_provider(
            provider_ids[0] if provider_ids else DEFAULT_METADATA_PROVIDER_ID
        )

    outcomes = await gather(
        *(search_provider(provider_id) for provider_id in provider_ids),
        return_exceptions=True
    )

    results: List[VolumeMetadata] = []
    answered = False
    first_error: Union[BaseException, None] = None
    for provider_id, outcome in zip(provider_ids, outcomes):
        if not isinstance(outcome, BaseException):
            answered = True
            results.extend(outcome)
            continue

        if not MetadataProviderRegistry.provider_class(
            provider_id
        ).is_unavailable_error(outcome):
            raise outcome
        if first_error is None:
            first_error = outcome

    if first_error is not None and not answered:
        raise first_error

    return results


async def fetch_volume_with_fallback(cv_id: int) -> VolumeMetadata:
    """Fetch a CV-linked volume from Metron if ComicVine is unavailable."""
    from backend.base.custom_exceptions import (CVRateLimitReached,
                                                InvalidComicVineApiKey)
    try:
        return await get_metadata_provider().fetch_volume(cv_id)
    except (CVRateLimitReached, InvalidComicVineApiKey):
        if not is_metadata_provider_configured('metron'):
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
        if not is_metadata_provider_configured('metron'):
            raise
        metron = get_metadata_provider('metron')
        return await metron.fetch_volumes_by_comicvine_ids(cv_ids)  # type: ignore

    if not is_metadata_provider_configured('metron'):
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
