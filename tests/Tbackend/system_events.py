import unittest

from backend.base.custom_exceptions import CVRateLimitReached
from backend.features.metadata import MetadataProviderRegistry
from backend.features.system_events import (
    begin_comicvine_operation,
    finish_comicvine_operation,
    get_comicvine_operation_stats,
    reset_comicvine_operation_stats,
)


class comicvine_operation_telemetry(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        reset_comicvine_operation_stats()

    def tearDown(self):
        reset_comicvine_operation_stats()

    def test_direct_operation_counters_are_json_safe(self):
        key = begin_comicvine_operation('search_volumes')
        finish_comicvine_operation(key, 'success')

        stats = get_comicvine_operation_stats()

        self.assertEqual(stats['measurement'], 'provider_operations')
        self.assertEqual(stats['total_operations'], 1)
        self.assertEqual(len(stats['operations']), 1)
        self.assertEqual(stats['operations'][0]['operation'], 'search_volumes')
        self.assertEqual(stats['operations'][0]['success'], 1)
        self.assertEqual(stats['operations'][0]['rate_limit'], 0)

    async def test_registry_wrapper_records_success_and_rate_limit(self):
        class FakeComicVine:
            operation_outcomes = {CVRateLimitReached: 'rate_limit'}

            async def search_volumes(self, query):
                return [query]

            async def fetch_volume(self, external_id):
                raise CVRateLimitReached

            async def fetch_volumes(self, external_ids):
                return list(external_ids)

            async def fetch_issues(self, volume_external_ids):
                return list(volume_external_ids)

        MetadataProviderRegistry._instrument_operations(FakeComicVine)
        provider = FakeComicVine()

        self.assertEqual(await provider.search_volumes('Batman'), ['Batman'])
        with self.assertRaises(CVRateLimitReached):
            await provider.fetch_volume(123)

        stats = {
            item['operation']: item
            for item in get_comicvine_operation_stats()['operations']
        }
        self.assertEqual(stats['search_volumes']['operations'], 1)
        self.assertEqual(stats['search_volumes']['success'], 1)
        self.assertEqual(stats['fetch_volume']['operations'], 1)
        self.assertEqual(stats['fetch_volume']['rate_limit'], 1)

    async def test_instrumentation_is_idempotent(self):
        class FakeComicVine:
            async def search_volumes(self, query):
                return []

            async def fetch_volume(self, external_id):
                return {}

            async def fetch_volumes(self, external_ids):
                return []

            async def fetch_issues(self, volume_external_ids):
                return []

        MetadataProviderRegistry._instrument_operations(FakeComicVine)
        MetadataProviderRegistry._instrument_operations(FakeComicVine)
        await FakeComicVine().search_volumes('Batman')

        stats = get_comicvine_operation_stats()
        self.assertEqual(stats['total_operations'], 1)

    async def test_unmapped_exception_falls_to_other_error(self):
        class Boom(Exception):
            pass

        class FakeProvider:
            operation_outcomes = {CVRateLimitReached: 'rate_limit'}

            async def search_volumes(self, query):
                raise Boom

            async def fetch_volume(self, external_id):
                return {}

            async def fetch_volumes(self, external_ids):
                return []

            async def fetch_issues(self, volume_external_ids):
                return []

        MetadataProviderRegistry._instrument_operations(FakeProvider)
        with self.assertRaises(Boom):
            await FakeProvider().search_volumes('Batman')

        stats = {
            item['operation']: item
            for item in get_comicvine_operation_stats()['operations']
        }
        self.assertEqual(stats['search_volumes']['other_error'], 1)

    async def test_register_only_instruments_opted_in_providers(self):
        class PlainProvider:
            provider_id = 'plain-test-provider'

            async def search_volumes(self, query):
                return []

            async def fetch_volume(self, external_id):
                return {}

            async def fetch_volumes(self, external_ids):
                return []

            async def fetch_issues(self, volume_external_ids):
                return []

        original = dict(MetadataProviderRegistry._providers)
        try:
            registered = MetadataProviderRegistry.register(PlainProvider)
            await registered().search_volumes('Batman')

            stats = get_comicvine_operation_stats()
            self.assertEqual(stats['total_operations'], 0)
        finally:
            MetadataProviderRegistry._providers = original


if __name__ == '__main__':
    unittest.main()
