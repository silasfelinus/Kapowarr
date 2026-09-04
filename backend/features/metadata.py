# -*- coding: utf-8 -*-

"""Metadata-provider contracts, registration and persistent identities.

Provider IDs are stable, lowercase storage keys.  Display names and credentials
are deliberately kept out of library identity so providers can be reconfigured
without orphaning volumes or issues.
"""

from abc import ABC, abstractmethod
from asyncio import TimeoutError as AsyncioTimeoutError, gather, wait_for
from enum import Enum
from functools import wraps
from importlib import import_module
from time import time
from typing import (Any, Callable, Dict, List, Optional, Sequence, Tuple,
                    Union)

from backend.base.definitions import IssueMetadata, VolumeMetadata
from backend.base.logging import LOGGER
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
        'metron': 'backend.implementations.metron',
        'gcd': 'backend.implementations.gcd'
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


class ProviderSearchResults(list):
    """The combined search results, plus which providers produced them.

    A provider that answers with nothing leaves nothing behind, so a
    breakdown computed from the results alone cannot tell "GCD had nothing
    either" from "GCD was never asked" -- the fan-out returns as soon as one
    provider recognises the title. Both read as "no database in the world
    has this", and only one of them is a reason to stop looking. Working out
    which had happened for a given hold meant re-deriving the stop condition
    by hand against the captured sample.

    A list subclass rather than a new return type: every caller treats this
    as the list of results it always was, and a test that patches the search
    with a plain list simply carries no consultation.
    """

    consulted: List[Dict[str, Any]]

    def __init__(self, results=(), consulted=()):
        super().__init__(results)
        self.consulted = list(consulted)


async def search_volumes_everywhere(
    title: str,
    accepts: Optional[Callable[[List[VolumeMetadata]], bool]] = None
) -> List[VolumeMetadata]:
    """Ask each configured provider for `title` until one recognises it.

    ComicVine is a huge database but far from a complete one, and it is
    thinnest in exactly the places a personal library runs deepest:
    small-press, indie and adult material. The Grand Comics Database and
    Metron carry a great deal it never indexed. Both were configurable
    and neither was ever asked -- library import went straight to the
    default provider, so a folder ComicVine had not heard of was held
    for review as though no database in the world had it.

    "Recognises" means a result whose title actually matches, not merely
    a non-empty response. ComicVine answers almost anything with fifty
    rows; a search for an obscure title comes back full of unrelated
    series, and stopping there would keep every fallback permanently out
    of reach. Checking the titles costs nothing and is the same
    comparison the ranker applies moments later.

    A title the default provider knows therefore costs the single
    request it always did. The extra requests only happen for titles
    that were going to be held for review anyway, and they go to
    different services, so no one provider's rate limit sees more
    traffic than before.

    Returns every result gathered when nobody recognises the title, so
    the review queue records what was actually considered.

    `accepts` decides what "recognises" means. Title matching is the
    default and is the cheapest question worth asking, but it is a
    weaker test than the one applied moments later: a provider can
    return a row whose title is exactly right and which the ranker then
    refuses on language, type or issue coverage. The search stopped
    there anyway, so the folder was held for review having never asked
    the databases that might have had it -- four of job 21's
    thirty-eight, among them `/content/Doonesbury`, where ComicVine
    offered a "Doonesbury" the ranker would not take and neither GCD nor
    Metron was consulted. A caller that knows what it will accept can
    say so, and the fan-out keeps going until somebody offers it.
    """
    from backend.implementations.matching import match_title

    provider_ids = configured_metadata_provider_ids(
        MetadataCapability.SEARCH_VOLUMES
    ) or [DEFAULT_METADATA_PROVIDER_ID]

    everything: List[VolumeMetadata] = []
    # Every provider asked, in order, and what it gave back -- including the
    # ones that gave back nothing, and the ones never reached because an
    # earlier provider recognised the title.
    consultation: List[Dict[str, Any]] = []
    # A provider that returns nothing leaves no candidate behind, so a
    # review record built from candidates alone cannot distinguish "the
    # fallbacks had nothing either" from "the fallbacks were never
    # asked". Both read as "no database in the world had this", and only
    # one of them is a reason to stop looking. Say what each provider was
    # asked and what it gave back, so the answer is in the log even when
    # the answer is nothing.
    consulted: List[str] = []
    for provider_id in provider_ids:
        provider = get_metadata_provider(provider_id)
        try:
            results = await provider.search_volumes(title)
        except Exception as error:
            # One provider being down is not a reason to abandon an import
            # that the others can still serve.
            LOGGER.warning(
                'Metadata provider %s failed searching for %r: %s',
                provider_id, title, error
            )
            consulted.append(f'{provider_id}=failed')
            consultation.append({
                'provider_id': provider_id,
                'asked': True,
                'result_count': 0,
                'failed': True,
                'recognised': False
            })
            continue

        consulted.append(f'{provider_id}={len(results)}')
        recognised = (
            accepts(results) if accepts is not None else
            any(match_title(title, result['title']) for result in results)
        )
        consultation.append({
            'provider_id': provider_id,
            'asked': True,
            'result_count': len(results),
            'failed': False,
            'recognised': recognised
        })

        if recognised:
            if everything:
                LOGGER.info(
                    'Found %r through %s, which the earlier provider(s) '
                    'did not recognise', title, provider_id
                )
            # The providers after this one are deliberately not asked. Say so
            # rather than leaving them absent, which is indistinguishable
            # from having asked and got nothing.
            consultation.extend({
                'provider_id': later,
                'asked': False,
                'result_count': 0,
                'failed': False,
                'recognised': False
            } for later in provider_ids[provider_ids.index(provider_id) + 1:])
            return ProviderSearchResults(results, consultation)

        everything.extend(results)

    LOGGER.info(
        'No configured provider recognised %r; asked %s',
        title, ', '.join(consulted) or 'nobody'
    )
    return ProviderSearchResults(everything, consultation)


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


# How long one metadata provider gets to answer a search before the others
# go on without it. A search normally takes a second or two; twenty is
# generous for a provider having a bad day and short enough that the dialog
# comes back rather than sitting there.
METADATA_SEARCH_TIMEOUT = 20.0


def searchable_metadata_providers() -> List[Dict[str, str]]:
    """The providers a search can be pointed at, named for a person.

    Args:
        None.

    Returns:
        List[Dict[str, str]]: `id` and `name` per configured provider, in
        the order a fan-out would ask them.
    """
    return [
        {
            'id': provider_id,
            'name': getattr(
                MetadataProviderRegistry.provider_class(provider_id),
                'display_name',
                provider_id
            )
        }
        for provider_id in configured_metadata_provider_ids(
            MetadataCapability.SEARCH_VOLUMES
        )
    ]


async def search_metadata_with_fallback(
    query: str,
    provider_ids: Union[Sequence[str], None] = None
) -> List[VolumeMetadata]:
    """Search every configured provider while keeping identities explicit.

    A provider that reports itself unavailable (see
    :attr:`MetadataProvider.unavailable_errors`) is skipped as long as another
    provider answered; if every provider is unavailable, the first such error
    is raised.  Anything else propagates immediately — an unavailable provider
    is a degraded search, an unexpected error is a bug.

    Args:
        query (str): What to search for.

        provider_ids (Union[Sequence[str], None], optional): Ask only these
            providers. Silas, on the Edit Metadata Match dialog: "It would
            be better in that particular screen case if we had toggles to
            choose which provider(s) to search." A search is only as quick
            as its slowest provider, and the person looking at the dialog
            knows which one they want. Unknown or unconfigured ids are
            ignored; asking for none of them falls back to all, because a
            search that asks nobody is not a search. Defaults to None,
            meaning every configured provider.
    """
    async def search_provider(provider_id: str) -> List[VolumeMetadata]:
        # Constructing inside the coroutine keeps a provider whose credentials
        # are rejected at construction time an error of *that* provider's
        # search, not of the whole fan-out.
        #
        # Bounded, because nothing else bounds it. `AsyncSession` sets
        # `connect` and `sock_read` but no `total`, so a provider that
        # answers slowly enough never trips either, and five retries with
        # backoff on top can hold one search for minutes. `gather` below
        # then waits for the slowest of them.
        #
        # That is what a frozen Kapowarr looks like from a browser: the
        # Edit Metadata Match dialog sits on "Searching metadata
        # providers…", and because a browser opens only about six
        # connections per host, a few such requests are enough to make
        # every other page on the site unreachable too. Silas, 2026-09-03.
        return await wait_for(
            get_metadata_provider(provider_id).search_volumes(query),
            timeout=METADATA_SEARCH_TIMEOUT
        )

    configured = configured_metadata_provider_ids(
        MetadataCapability.SEARCH_VOLUMES
    )
    if provider_ids is not None:
        wanted = set(provider_ids)
        chosen = [p for p in configured if p in wanted]
        # Every choice filtered away leaves nothing to ask. Falling back to
        # the full set beats returning an empty search that looks like "no
        # results" -- a stale toggle should not silently answer for the
        # provider it names.
        configured = chosen or configured

    provider_ids = configured
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

        if isinstance(outcome, AsyncioTimeoutError):
            # Not answering is a way of being unavailable. The others'
            # results are still worth having, and saying which one went
            # quiet is worth more than a search that never returns.
            LOGGER.warning(
                'Metadata provider %s did not answer within %.0fs; '
                'searching without it',
                provider_id, METADATA_SEARCH_TIMEOUT
            )
        elif not MetadataProviderRegistry.provider_class(
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
    from backend.implementations.metron import MetronError

    try:
        return await get_metadata_provider().fetch_volume(cv_id)
    except (CVRateLimitReached, InvalidComicVineApiKey) as primary_error:
        if not is_metadata_provider_configured('metron'):
            raise
        metron = get_metadata_provider('metron')
        try:
            return await metron.fetch_volume_by_comicvine_id(cv_id)  # type: ignore
        except MetronError:
            # Fallback is opportunistic. If it cannot rescue this CV-linked
            # volume, preserve the primary provider's transient/configuration
            # signal so callers can apply their existing retry/cooldown policy.
            # In particular, a missing Metron CV cross-link must not turn a
            # ComicVine rate limit into a fatal background-task error.
            raise primary_error from None


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
