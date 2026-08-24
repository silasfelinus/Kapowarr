# -*- coding: utf-8 -*-

"""ComicVine is not the only database, and was the only one asked."""

import unittest
from asyncio import run
from unittest.mock import AsyncMock, patch

from backend.base.custom_exceptions import CVRateLimitReached
from backend.features import metadata as MD
from backend.implementations.metron import MetronError


def _result(title, provider_id='comicvine'):
    return {
        'comicvine_id': 1, 'title': title, 'year': 2020,
        'volume_number': 1, 'translated': False, 'issue_count': 5,
        'publisher': 'Someone', 'aliases': [], 'site_url': 'https://e.test',
        'provider_id': provider_id
    }


class asking_the_others_when_the_first_does_not_know(unittest.TestCase):
    """A folder ComicVine had not heard of was held for review as though
    no database in the world had it. Metron and the Grand Comics
    Database were configurable and neither was ever asked."""

    def _search(self, provider_results):
        providers = {}
        for provider_id, results in provider_results.items():
            provider = AsyncMock()
            provider.search_volumes = AsyncMock(return_value=results)
            providers[provider_id] = provider

        with patch.object(
            MD, 'configured_metadata_provider_ids',
            return_value=list(provider_results)
        ), patch.object(
            MD, 'get_metadata_provider', side_effect=providers.get
        ):
            found = run(MD.search_volumes_everywhere('Danger Jane'))
        return found, providers

    def test_the_default_provider_alone_when_it_knows_the_title(self):
        found, providers = self._search({
            'comicvine': [_result('Danger Jane')],
            'gcd': [_result('Danger Jane', 'gcd')]
        })

        self.assertEqual(found, [_result('Danger Jane')])
        providers['gcd'].search_volumes.assert_not_awaited()

    def test_a_later_provider_rescues_a_title_the_first_does_not_have(self):
        found, _ = self._search({
            'comicvine': [_result('Danger Zone'), _result('Jane Eyre')],
            'gcd': [_result('Danger Jane', 'gcd')]
        })

        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]['provider_id'], 'gcd')

    def test_fifty_unrelated_rows_do_not_count_as_knowing_it(self):
        """ComicVine answers almost anything with a full page.

        Stopping at the first non-empty response would keep every
        fallback permanently out of reach.
        """
        found, providers = self._search({
            'comicvine': [_result(f'Something Else {n}') for n in range(50)],
            'gcd': [_result('Danger Jane', 'gcd')]
        })

        providers['gcd'].search_volumes.assert_awaited_once()
        self.assertEqual(found[0]['provider_id'], 'gcd')

    def test_everything_gathered_is_returned_when_nobody_knows_it(self):
        """So the review queue records what was actually considered."""
        found, _ = self._search({
            'comicvine': [_result('Nope One')],
            'gcd': [_result('Nope Two', 'gcd')]
        })

        self.assertEqual(
            [r['title'] for r in found], ['Nope One', 'Nope Two']
        )

    def test_a_provider_that_is_down_does_not_take_the_import_with_it(self):
        broken = AsyncMock()
        broken.search_volumes = AsyncMock(side_effect=OSError('unreachable'))
        working = AsyncMock()
        working.search_volumes = AsyncMock(
            return_value=[_result('Danger Jane', 'gcd')]
        )

        with patch.object(
            MD, 'configured_metadata_provider_ids',
            return_value=['comicvine', 'gcd']
        ), patch.object(
            MD, 'get_metadata_provider',
            side_effect={'comicvine': broken, 'gcd': working}.get
        ):
            found = run(MD.search_volumes_everywhere('Danger Jane'))

        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]['provider_id'], 'gcd')

    def test_it_falls_back_to_the_default_when_none_are_configured(self):
        provider = AsyncMock()
        provider.search_volumes = AsyncMock(return_value=[])

        with patch.object(
            MD, 'configured_metadata_provider_ids', return_value=[]
        ), patch.object(
            MD, 'get_metadata_provider', return_value=provider
        ) as get:
            run(MD.search_volumes_everywhere('Danger Jane'))

        get.assert_called_once_with(MD.DEFAULT_METADATA_PROVIDER_ID)


class fallback_does_not_relabel_primary_outages(unittest.TestCase):
    """A fallback miss must not turn a retryable ComicVine throttle fatal."""

    def _providers(self, fallback_error):
        comicvine = AsyncMock()
        comicvine.fetch_volume = AsyncMock(side_effect=CVRateLimitReached)
        metron = AsyncMock()
        metron.fetch_volume_by_comicvine_id = AsyncMock(
            side_effect=fallback_error
        )

        def provider(provider_id=MD.DEFAULT_METADATA_PROVIDER_ID):
            return metron if provider_id == 'metron' else comicvine

        return provider, comicvine, metron

    def test_metron_crosslink_miss_preserves_the_comicvine_rate_limit(self):
        provider, comicvine, metron = self._providers(
            MetronError('Metron has no unique series for ComicVine ID 139594')
        )

        with patch.object(
            MD, 'is_metadata_provider_configured', return_value=True
        ), patch.object(MD, 'get_metadata_provider', side_effect=provider):
            with self.assertRaises(CVRateLimitReached):
                run(MD.fetch_volume_with_fallback(139594))

        comicvine.fetch_volume.assert_awaited_once_with(139594)
        metron.fetch_volume_by_comicvine_id.assert_awaited_once_with(139594)

    def test_unexpected_fallback_bugs_are_still_real_errors(self):
        provider, _, _ = self._providers(RuntimeError('fallback bug'))

        with patch.object(
            MD, 'is_metadata_provider_configured', return_value=True
        ), patch.object(MD, 'get_metadata_provider', side_effect=provider):
            with self.assertRaisesRegex(RuntimeError, 'fallback bug'):
                run(MD.fetch_volume_with_fallback(139594))


class library_import_uses_it(unittest.TestCase):
    """Both search sites went straight to the default provider."""

    def test_neither_import_path_calls_a_single_provider_any_more(self):
        import inspect

        from backend.features import library_import, library_import_persistent

        for module in (library_import, library_import_persistent):
            source = inspect.getsource(module)
            self.assertNotIn(
                'get_metadata_provider', source,
                f'{module.__name__} still searches one provider only'
            )
            self.assertIn('search_volumes_everywhere', source)


if __name__ == '__main__':
    unittest.main()
