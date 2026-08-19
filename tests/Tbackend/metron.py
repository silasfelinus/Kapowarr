# -*- coding: utf-8 -*-

from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import AsyncMock, MagicMock, patch

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


class TestMetronSearchVolumes(IsolatedAsyncioTestCase):
    """t-056: Metron-native search results (no ComicVine cross-link) must
    preview without being filtered out, and their 'already_added' dedup
    must check volume_external_ids, not just the legacy comicvine_id
    column.
    """

    async def test_search_includes_metron_native_results_without_cv_link(self):
        provider = object.__new__(Metron)
        provider._get = AsyncMock(side_effect=(
            {'results': [{'id': 92}], 'next': None},
            {
                'id': 92, 'series': 'Native Series',
                'year_began': 2026, 'issue_count': 1
            }
        ))

        cursor = MagicMock()
        cursor.execute.return_value = []

        with patch('backend.implementations.metron.get_db', return_value=cursor):
            results = await provider.search_volumes('Native')

        self.assertEqual(len(results), 1)
        self.assertIsNone(results[0]['comicvine_id'])
        self.assertEqual(results[0]['external_id'], '92')
        self.assertIsNone(results[0]['already_added'])

    async def test_search_flags_already_added_via_either_identity(self):
        provider = object.__new__(Metron)
        provider._get = AsyncMock(side_effect=(
            {'results': [{'id': 91}, {'id': 92}], 'next': None},
            {
                'id': 91, 'name': 'Saga', 'year_began': 2012,
                'issue_count': 72, 'cv_id': 4050
            },
            {
                'id': 92, 'series': 'Native Series',
                'year_began': 2026, 'issue_count': 1
            }
        ))

        cursor = MagicMock()
        # First call: the ComicVine cross-link dedup query. Second call:
        # the Metron-identity dedup query (against volume_external_ids).
        cursor.execute.side_effect = [
            [(4050, 10)],
            [('92', 20)]
        ]

        with patch('backend.implementations.metron.get_db', return_value=cursor):
            results = await provider.search_volumes('test')

        by_external_id = {r['external_id']: r for r in results}
        self.assertEqual(by_external_id['91']['already_added'], 10)
        self.assertEqual(by_external_id['92']['already_added'], 20)


class TestMetronFetchIssues(IsolatedAsyncioTestCase):
    """t-056: a Metron-native issue (no ComicVine cross-link) must be
    included so it can be added; its durable identity lives in
    issue_external_ids, not comicvine_id.
    """

    async def test_fetch_issues_includes_metron_native_issues(self):
        provider = object.__new__(Metron)
        provider.date_type = 'store_date'
        provider._get = AsyncMock(side_effect=(
            {'results': [{'id': 501}], 'next': None},
            {
                'id': 501, 'number': '1', 'series': {'id': 10},
                'store_date': '2026-01-01', 'desc': ''
            }
        ))

        issues = await provider.fetch_issues((10,))

        self.assertEqual(len(issues), 1)
        self.assertIsNone(issues[0]['comicvine_id'])
        self.assertEqual(issues[0]['external_id'], '501')
        self.assertEqual(issues[0]['provider_id'], 'metron')


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
