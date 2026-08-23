# -*- coding: utf-8 -*-

from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import AsyncMock, patch

from backend.implementations.gcd import Gcd


def _provider() -> Gcd:
    """A Gcd instance that never touches real Settings/DB state."""
    provider = object.__new__(Gcd)
    provider.provider_id = 'gcd'
    provider.username = None
    provider.password = None
    provider.auth = None
    provider._publisher_cache = {}
    return provider


class TestGcdFormatting(TestCase):
    def test_volume_keeps_comicvine_id_none(self):
        volume = _provider()._volume({
            'id': 550,
            'name': 'Tintin',
            'year_began': 1959,
            'active_issues': [1, 2, 3],
            'notes': ''
        })

        self.assertEqual(volume['provider_id'], 'gcd')
        self.assertEqual(volume['external_id'], '550')
        self.assertIsNone(volume['comicvine_id'])
        self.assertEqual(volume['cover_source']['provider_id'], 'gcd')

    def test_volume_number_always_defaults_to_one(self):
        # GCD has no series-level volume number; the year must never be
        # silently adopted as one, because it would change folder naming.
        volume = _provider()._volume({
            'id': 9,
            'name': 'Astérix',
            'year_began': 1959,
            'active_issues': list(range(188))
        })

        self.assertEqual(volume['volume_number'], 1)
        self.assertEqual(volume['issue_count'], 188)

    def test_site_url_is_constructed_not_taken_from_api_url(self):
        volume = _provider()._volume({
            'id': 550,
            'name': 'Tintin',
            'year_began': 1959,
            'api_url': 'https://www.comics.org/api/series/550/'
        })

        self.assertEqual(
            volume['site_url'], 'https://www.comics.org/series/550/'
        )


class TestGcdDateNormalisation(TestCase):
    def test_zero_placeholder_date_is_truncated_to_year(self):
        date = Gcd._normalise_date({'key_date': '1959-00-00'})
        self.assertEqual(date, '1959')

    def test_zero_day_is_truncated_to_year_month(self):
        date = Gcd._normalise_date({'key_date': '1959-05-00'})
        self.assertEqual(date, '1959-05')

    def test_full_key_date_is_kept_verbatim(self):
        date = Gcd._normalise_date({'key_date': '1959-05-12'})
        self.assertEqual(date, '1959-05-12')

    def test_on_sale_date_wins_over_key_date(self):
        date = Gcd._normalise_date({
            'on_sale_date': '1959-05-12',
            'key_date': '1959-00-00'
        })
        self.assertEqual(date, '1959-05-12')

    def test_no_usable_date_is_none(self):
        self.assertIsNone(Gcd._normalise_date({}))
        self.assertIsNone(Gcd._normalise_date({'key_date': '0000-00-00'}))


class TestGcdIssueTitle(TestCase):
    def test_title_parsed_from_descriptor(self):
        title = Gcd._issue_title({
            'descriptor': "368 - King Ottokar's Sceptre"
        })
        self.assertEqual(title, "King Ottokar's Sceptre")

    def test_falls_back_to_longest_story_title(self):
        title = Gcd._issue_title({
            'descriptor': '1',
            'longest_story': {'title': 'A New Beginning'}
        })
        self.assertEqual(title, 'A New Beginning')

    def test_no_title_available_is_none(self):
        self.assertIsNone(Gcd._issue_title({'descriptor': '1'}))


class TestGcdIssueMapping(TestCase):
    def test_fetch_issues_shape(self):
        issue = _provider()._issue({
            'issue_id': 566399,
            'descriptor': "368 - King Ottokar's Sceptre",
            'number': '368',
            'on_sale_date': '',
            'key_date': '1959-00-00',
            'cover_url': 'https://files1.comics.org//img/gcd/covers_by_id/550/w400/550394.jpg',
            'longest_story': {'title': "King Ottokar's Sceptre"}
        }, volume_external_id='550')

        self.assertEqual(issue['provider_id'], 'gcd')
        self.assertEqual(issue['external_id'], '566399')
        self.assertEqual(issue['volume_external_id'], '550')
        self.assertIsNone(issue['comicvine_id'])
        self.assertEqual(issue['issue_number'], '368')
        self.assertEqual(issue['date'], '1959')
        self.assertEqual(issue['title'], "King Ottokar's Sceptre")


class TestGcdAuthentication(TestCase):
    def test_anonymous_by_default(self):
        settings = type('Settings', (), {
            'gcd_username': '', 'gcd_password': ''
        })()
        with patch('backend.implementations.gcd.Settings') as settings_factory:
            settings_factory.return_value.get_settings.return_value = settings
            provider = Gcd()

        self.assertIsNone(provider.auth)

    def test_configured_without_credentials(self):
        # Unlike Metron/ComicVine, GCD must remain usable with no account at
        # all -- anonymous access is the documented default, not a degraded
        # mode.
        self.assertTrue(Gcd.is_configured())

    def test_basic_auth_used_when_credentials_given(self):
        settings = type('Settings', (), {
            'gcd_username': '', 'gcd_password': ''
        })()
        with patch('backend.implementations.gcd.Settings') as settings_factory:
            settings_factory.return_value.get_settings.return_value = settings
            provider = Gcd('someone', 'secret')

        self.assertIsNotNone(provider.auth)
        self.assertEqual(provider.auth.login, 'someone')


class TestGcdRequests(IsolatedAsyncioTestCase):
    async def test_search_never_follows_next(self):
        # A one-character query is 3,571 pages on GCD; only the first page
        # may ever be requested.
        provider = _provider()
        provider._get = AsyncMock(return_value={
            'results': [{
                'id': 145, 'name': 'Tintin', 'year_began': 1959,
                'active_issues': [1]
            }],
            'next': 'https://www.comics.org/api/series/name/tintin/?page=2'
        })

        with patch(
            'backend.implementations.gcd.MetadataIdentityStore.resolve',
            return_value=None
        ):
            results = await provider.search_volumes('Tintin')

        self.assertEqual(len(results), 1)
        provider._get.assert_awaited_once()

    async def test_search_strips_slash_from_query(self):
        provider = _provider()
        provider._get = AsyncMock(return_value={'results': []})

        await provider.search_volumes('Batman/Superman')

        path = provider._get.await_args.args[0]
        self.assertNotIn('/', path.split('series/name/', 1)[1])

    async def test_overview_pagination_follows_next_to_completion(self):
        # Unlike search, overview pagination IS bounded by the real issue
        # count and must be followed to completion.
        provider = _provider()
        provider._get = AsyncMock(side_effect=(
            {'results': [{'issue_id': 1}], 'next': 'page-2'},
            {'results': [{'issue_id': 2}], 'next': None}
        ))

        results = await provider._issue_overview(550)

        self.assertEqual([r['issue_id'] for r in results], [1, 2])
        self.assertEqual(provider._get.await_count, 2)

    async def test_publisher_name_is_cached_across_calls(self):
        provider = _provider()
        provider._get = AsyncMock(return_value={'name': 'Le Lombard'})

        first = await provider._publisher_name(
            'https://www.comics.org/api/publisher/17/'
        )
        second = await provider._publisher_name(
            'https://www.comics.org/api/publisher/17/'
        )

        self.assertEqual(first, 'Le Lombard')
        self.assertEqual(second, 'Le Lombard')
        provider._get.assert_awaited_once()

    async def test_publisher_name_none_when_url_missing(self):
        provider = _provider()
        provider._get = AsyncMock()

        name = await provider._publisher_name(None)

        self.assertIsNone(name)
        provider._get.assert_not_called()


if __name__ == '__main__':
    import unittest
    unittest.main()


class GcdSearchSurvivesAnUnusableResult(IsolatedAsyncioTestCase):
    """One bad entry must not cost the whole search.

    GCD has returned series rows with no `id`. Indexing it unconditionally
    raised KeyError out through /api/volumes/search, so a query that matched
    twenty series returned nothing at all and looked like a total failure. An
    entry with no id cannot be added anyway -- there is nothing to link to.
    """

    async def _search(self, results):
        provider = _provider()
        with patch.object(
            provider, '_get', AsyncMock(return_value={'results': results})
        ), patch(
            'backend.implementations.gcd.MetadataIdentityStore.resolve',
            return_value=None
        ):
            return await provider.search_volumes('rocketfellers')

    async def test_an_entry_with_no_id_is_skipped_not_fatal(self):
        found = await self._search([
            {'name': 'Broken Row'},
            {'id': 42, 'name': 'The Rocketfellers', 'year_began': 2003},
        ])

        self.assertEqual([v['title'] for v in found], ['The Rocketfellers'])

    async def test_a_null_id_is_treated_the_same(self):
        found = await self._search([
            {'id': None, 'name': 'Broken Row'},
            {'id': 7, 'name': 'Keeper', 'year_began': 1999},
        ])

        self.assertEqual([v['external_id'] for v in found], ['7'])

    async def test_a_page_of_only_bad_rows_returns_empty_rather_than_raising(self):
        self.assertEqual(
            await self._search([{'name': 'a'}, {'name': 'b'}]), []
        )

    async def test_a_normal_page_is_unaffected(self):
        found = await self._search([
            {'id': 1, 'name': 'One', 'year_began': 2001},
            {'id': 2, 'name': 'Two', 'year_began': 2002},
        ])

        self.assertEqual([v['external_id'] for v in found], ['1', '2'])
