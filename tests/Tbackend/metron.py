# -*- coding: utf-8 -*-

from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import AsyncMock

from backend.implementations.metron import Metron


class TestMetronFormatting(TestCase):
    def test_volume_keeps_metron_and_comicvine_ids_distinct(self):
        volume = Metron._volume({
            'id': 91,
            'name': 'Saga',
            'year_began': 2012,
            'volume': 1,
            'issue_count': 72,
            'cv_id': 4050,
            'publisher': {'name': 'Image Comics'},
            'image': 'https://example.test/cover.jpg',
            'resource_url': 'https://metron.cloud/series/saga/'
        })

        self.assertEqual(volume['provider_id'], 'metron')
        self.assertEqual(volume['external_id'], '91')
        self.assertEqual(volume['comicvine_id'], 4050)
        self.assertEqual(volume['cover_source']['provider_id'], 'metron')

    def test_metron_native_volume_does_not_invent_comicvine_id(self):
        volume = Metron._volume({
            'id': 92,
            'series': 'Native Series',
            'year_began': 2026,
            'issue_count': 1
        })

        self.assertIsNone(volume['comicvine_id'])
        self.assertEqual(volume['external_id'], '92')


class TestMetronRequests(IsolatedAsyncioTestCase):
    async def test_pagination_uses_api_path_instead_of_untrusted_next_url(self):
        provider = object.__new__(Metron)
        provider._get = AsyncMock(side_effect=(
            {
                'results': [{'id': 1}],
                'next': 'https://untrusted.example/steal-credentials'
            },
            {'results': [{'id': 2}], 'next': None}
        ))

        results = await provider._all('series', {'q': 'Saga'})

        self.assertEqual(results, [{'id': 1}, {'id': 2}])
        provider._get.assert_awaited_with(
            'series', {'q': 'Saga', 'page': 2}
        )
