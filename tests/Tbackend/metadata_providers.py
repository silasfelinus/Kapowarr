import sqlite3
import unittest
from unittest.mock import patch

from backend.features.metadata import (MetadataCapability,
                                       MetadataIdentityStore, MetadataProvider,
                                       MetadataProviderRegistry,
                                       get_metadata_provider)
from backend.internals.db import KapowarrCursor


class DummyProvider(MetadataProvider):
    provider_id = 'test-provider'
    display_name = 'Test Provider'
    capabilities = (MetadataCapability.SEARCH_VOLUMES,)

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
