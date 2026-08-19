# -*- coding: utf-8 -*-

from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import AsyncMock, patch

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

    def test_series_summary_removes_duplicate_year_from_title(self):
        volume = Metron._volume({
            'id': 9,
            'series': 'Saga (2012)',
            'year_began': 2012,
            'volume': 1,
            'issue_count': 1
        })

        self.assertEqual(volume['title'], 'Saga')
        self.assertEqual(volume['year'], 2012)


class TestMetronAuthentication(TestCase):
    def test_token_authentication_is_preferred(self):
        settings = type('Settings', (), {
            'metron_api_token': '',
            'metron_username': '',
            'metron_password': '',
            'date_type': type('DateType', (), {'value': 'cover_date'})()
        })()
        with patch(
            'backend.implementations.metron.Settings'
        ) as settings_factory:
            settings_factory.return_value.get_settings.return_value = settings
            provider = Metron(
                metron_api_token='secret-token',
                metron_username='ignored',
                metron_password='ignored'
            )

        self.assertIsNone(provider.auth)
        self.assertEqual(
            provider.headers, {'Authorization': 'Bearer secret-token'}
        )


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

    async def test_fetch_issues_keeps_metron_native_issue(self):
        provider = object.__new__(Metron)
        provider.provider_id = 'metron'
        provider.date_type = 'cover_date'
        provider._get = AsyncMock()
        provider._all = AsyncMock(return_value=[{
            'id': 44,
            'series': {'id': 9},
            'number': '1',
            'title': 'A New Beginning',
            'cover_date': '2026-08-19',
            'desc': '',
            'cv_id': None
        }])

        issues = await provider.fetch_issues((9,))

        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]['external_id'], '44')
        self.assertIsNone(issues[0]['comicvine_id'])
        provider._get.assert_not_called()
