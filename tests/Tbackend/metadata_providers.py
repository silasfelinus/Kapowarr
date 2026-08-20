import sqlite3
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from backend.base.custom_exceptions import InvalidComicVineApiKey
from backend.features.metadata import (MetadataCapability,
                                       MetadataIdentityStore, MetadataProvider,
                                       MetadataProviderRegistry,
                                       get_metadata_provider,
                                       search_metadata_with_fallback)
from backend.internals.db import KapowarrCursor


class DummyUnavailable(RuntimeError):
    """This dummy provider cannot answer right now."""


class DummyProvider(MetadataProvider):
    provider_id = 'test-provider'
    display_name = 'Test Provider'
    capabilities = (MetadataCapability.SEARCH_VOLUMES,)
    unavailable_errors = (DummyUnavailable,)

    def test_key(self):
        return True

    async def search_volumes(self, query):
        return []

    async def fetch_volume(self, external_id):
        raise NotImplementedError

    async def fetch_volumes(self, external_ids):
        return []

    async def fetch_issues(self, volume_external_ids):
        return []


class metadata_provider_registry(unittest.TestCase):
    def test_comicvine_is_available_through_default_boundary(self):
        with patch(
            'backend.implementations.comicvine.Settings'
        ) as settings, patch(
            'backend.implementations.comicvine.Session'
        ):
            settings.return_value.get_settings.return_value.date_type.value = (
                'cover_date'
            )
            settings.return_value.get_settings.return_value.comicvine_api_key = (
                'key'
            )
            provider = get_metadata_provider()

        self.assertEqual(provider.provider_id, 'comicvine')
        self.assertIn(
            MetadataCapability.FETCH_VOLUME, provider.capabilities
        )

    def test_registry_keeps_provider_ids_stable_and_lazy(self):
        original = dict(MetadataProviderRegistry._providers)
        try:
            MetadataProviderRegistry.register(DummyProvider)
            self.assertIsInstance(
                get_metadata_provider('test-provider'), DummyProvider
            )
            with self.assertRaises(ValueError):
                MetadataProviderRegistry.register(type(
                    'UnstableProvider', (DummyProvider,),
                    {'provider_id': 'MixedCase'}
                ))
        finally:
            MetadataProviderRegistry._providers = original

    def test_configured_provider_ids_skip_the_unconfigured_and_order_stably(
        self
    ):
        original = dict(MetadataProviderRegistry._providers)
        try:
            MetadataProviderRegistry._providers = {
                'metron': type('M', (DummyProvider,), {'provider_id': 'metron'}),
                'unconfigured': type('U', (DummyProvider,), {
                    'provider_id': 'unconfigured',
                    'is_configured': classmethod(lambda cls: False)
                }),
                'comicvine': type('C', (DummyProvider,), {
                    'provider_id': 'comicvine'
                })
            }
            with patch.object(
                MetadataProviderRegistry, '_load_builtins'
            ):
                self.assertEqual(
                    MetadataProviderRegistry.configured_provider_ids(),
                    ['comicvine', 'metron']
                )
        finally:
            MetadataProviderRegistry._providers = original

    def test_configured_provider_ids_filter_on_capability(self):
        original = dict(MetadataProviderRegistry._providers)
        try:
            MetadataProviderRegistry._providers = {
                'comicvine': type('C', (DummyProvider,), {
                    'provider_id': 'comicvine'
                }),
                'covers-only': type('K', (DummyProvider,), {
                    'provider_id': 'covers-only',
                    'capabilities': (MetadataCapability.COVERS,)
                })
            }
            with patch.object(MetadataProviderRegistry, '_load_builtins'):
                self.assertEqual(
                    MetadataProviderRegistry.configured_provider_ids(
                        MetadataCapability.SEARCH_VOLUMES
                    ),
                    ['comicvine']
                )
                self.assertEqual(
                    MetadataProviderRegistry.configured_provider_ids(
                        MetadataCapability.COVERS
                    ),
                    ['covers-only']
                )
        finally:
            MetadataProviderRegistry._providers = original


class metadata_provider_search(unittest.IsolatedAsyncioTestCase):
    async def test_configured_metron_results_join_comicvine_results(self):
        comicvine = MagicMock()
        comicvine.search_volumes = AsyncMock(return_value=[{
            'provider_id': 'comicvine', 'external_id': '4050'
        }])
        metron = MagicMock()
        metron.search_volumes = AsyncMock(return_value=[{
            'provider_id': 'metron', 'external_id': 'native-9'
        }])

        with patch(
            'backend.features.metadata.configured_metadata_provider_ids',
            return_value=['comicvine', 'metron']
        ), patch(
            'backend.features.metadata.get_metadata_provider',
            side_effect=lambda provider_id='comicvine': (
                comicvine if provider_id == 'comicvine' else metron
            )
        ):
            results = await search_metadata_with_fallback('Saga')

        self.assertEqual(
            [result['provider_id'] for result in results],
            ['comicvine', 'metron']
        )
        comicvine.search_volumes.assert_awaited_once_with('Saga')
        metron.search_volumes.assert_awaited_once_with('Saga')

    async def test_metron_works_without_a_comicvine_key(self):
        metron = MagicMock()
        metron.search_volumes = AsyncMock(return_value=[{
            'provider_id': 'metron', 'external_id': 'native-9'
        }])

        def provider(provider_id='comicvine'):
            if provider_id == 'comicvine':
                raise InvalidComicVineApiKey
            return metron

        with patch(
            'backend.features.metadata.configured_metadata_provider_ids',
            return_value=['comicvine', 'metron']
        ), patch(
            'backend.features.metadata.get_metadata_provider',
            side_effect=provider
        ):
            results = await search_metadata_with_fallback('Saga')

        self.assertEqual(results[0]['provider_id'], 'metron')

    async def test_a_third_provider_joins_the_fan_out_without_special_casing(
        self
    ):
        providers = {}
        for provider_id in ('comicvine', 'metron', 'test-provider'):
            provider = MagicMock()
            provider.search_volumes = AsyncMock(return_value=[{
                'provider_id': provider_id, 'external_id': f'{provider_id}-1'
            }])
            providers[provider_id] = provider

        with patch(
            'backend.features.metadata.configured_metadata_provider_ids',
            return_value=['comicvine', 'metron', 'test-provider']
        ), patch(
            'backend.features.metadata.get_metadata_provider',
            side_effect=lambda provider_id='comicvine': providers[provider_id]
        ):
            results = await search_metadata_with_fallback('Saga')

        self.assertEqual(
            [result['provider_id'] for result in results],
            ['comicvine', 'metron', 'test-provider']
        )
        for provider in providers.values():
            provider.search_volumes.assert_awaited_once_with('Saga')

    async def test_an_unavailable_provider_is_skipped_not_fatal(self):
        comicvine = MagicMock()
        comicvine.search_volumes = AsyncMock(return_value=[{
            'provider_id': 'comicvine', 'external_id': '4050'
        }])
        broken = MagicMock()
        broken.search_volumes = AsyncMock(side_effect=DummyUnavailable)

        with patch(
            'backend.features.metadata.configured_metadata_provider_ids',
            return_value=['comicvine', 'test-provider']
        ), patch(
            'backend.features.metadata.get_metadata_provider',
            side_effect=lambda provider_id='comicvine': (
                comicvine if provider_id == 'comicvine' else broken
            )
        ):
            MetadataProviderRegistry.register(DummyProvider)
            try:
                results = await search_metadata_with_fallback('Saga')
            finally:
                MetadataProviderRegistry._providers.pop('test-provider', None)

        self.assertEqual(
            [result['provider_id'] for result in results], ['comicvine']
        )

    async def test_an_unexpected_provider_error_still_propagates(self):
        comicvine = MagicMock()
        comicvine.search_volumes = AsyncMock(return_value=[])
        broken = MagicMock()
        broken.search_volumes = AsyncMock(side_effect=RuntimeError('boom'))

        with patch(
            'backend.features.metadata.configured_metadata_provider_ids',
            return_value=['comicvine', 'test-provider']
        ), patch(
            'backend.features.metadata.get_metadata_provider',
            side_effect=lambda provider_id='comicvine': (
                comicvine if provider_id == 'comicvine' else broken
            )
        ):
            MetadataProviderRegistry.register(DummyProvider)
            try:
                with self.assertRaises(RuntimeError):
                    await search_metadata_with_fallback('Saga')
            finally:
                MetadataProviderRegistry._providers.pop('test-provider', None)

    async def test_a_single_configured_provider_owns_its_own_errors(self):
        metron = MagicMock()
        metron.search_volumes = AsyncMock(side_effect=RuntimeError('boom'))

        with patch(
            'backend.features.metadata.configured_metadata_provider_ids',
            return_value=['metron']
        ), patch(
            'backend.features.metadata.get_metadata_provider',
            return_value=metron
        ):
            with self.assertRaises(RuntimeError):
                await search_metadata_with_fallback('Saga')


class metadata_identity_store(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(':memory:')
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript("""
            CREATE TABLE volume_external_ids(
                volume_id INTEGER NOT NULL,
                provider_id TEXT NOT NULL,
                external_id TEXT NOT NULL,
                source_url TEXT,
                updated_at INTEGER NOT NULL,
                PRIMARY KEY (volume_id, provider_id),
                UNIQUE (provider_id, external_id)
            );
            CREATE TABLE issue_external_ids(
                issue_id INTEGER NOT NULL,
                provider_id TEXT NOT NULL,
                external_id TEXT NOT NULL,
                source_url TEXT,
                updated_at INTEGER NOT NULL,
                PRIMARY KEY (issue_id, provider_id),
                UNIQUE (provider_id, external_id)
            );
        """)
        self.patch = patch(
            'backend.features.metadata.get_db', side_effect=self._cursor
        )
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.connection.close()

    def _cursor(self):
        cursor = KapowarrCursor(self.connection)
        cursor.row_factory = sqlite3.Row
        return cursor

    def test_multiple_providers_coexist_and_resolve(self):
        MetadataIdentityStore.set('volume', 7, 'comicvine', 4050)
        MetadataIdentityStore.set('volume', 7, 'metron', 'abc-12')

        self.assertEqual(MetadataIdentityStore.get('volume', 7), {
            'comicvine': '4050', 'metron': 'abc-12'
        })
        self.assertEqual(
            MetadataIdentityStore.resolve('volume', 'metron', 'abc-12'), 7
        )

    def test_provider_identity_cannot_silently_merge_entities(self):
        MetadataIdentityStore.set('issue', 2, 'metron', 'issue-9')
        with self.assertRaises(sqlite3.IntegrityError):
            MetadataIdentityStore.set('issue', 3, 'metron', 'issue-9')


if __name__ == '__main__':
    unittest.main()
