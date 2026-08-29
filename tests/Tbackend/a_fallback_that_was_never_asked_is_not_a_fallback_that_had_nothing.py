# -*- coding: utf-8 -*-

"""Two very different answers used to look identical in a review record.

`search_volumes_everywhere` stops at the first provider whose results
contain an exact title match; the providers after it are never asked. A
provider that answers with nothing leaves nothing behind either. So a hold
listing only ComicVine meant one of two things -- the fallbacks were asked
and had nothing, or they were never reached -- and only the first is a
reason to stop looking.

Reading job 18 of Silas's library, 38 of the 45 no-candidate holds listed
ComicVine alone. Establishing that 36 of those had in fact exhausted GCD
and Metron, and only two (`/content/Coaraptor (2020)` and
`/content/Doonesbury`) stopped early on an exact ComicVine title the ranker
then rejected, meant re-deriving the stop condition by hand against the
captured sample. The record should say it.
"""

import unittest
from asyncio import run
from unittest.mock import patch

from backend.features import metadata as MD
from backend.features.library_import_diagnostics import _provider_breakdown


class _Provider:
    def __init__(self, results):
        self._results = results

    async def search_volumes(self, title):
        return self._results


def _volume(title, provider_id, external_id=1):
    return {
        'comicvine_id': external_id if provider_id == 'comicvine' else None,
        'external_id': external_id,
        'provider_id': provider_id,
        'title': title,
        'year': 2020,
        'volume_number': 1,
        'issue_count': 1,
        'publisher': None,
        'aliases': [],
        'translated': False,
        'already_added': None,
        'site_url': '',
        'cover_link': '',
        'cover': None,
        'description': '',
        'issues': None
    }


def _search(title, providers):
    with patch.object(
        MD, 'configured_metadata_provider_ids',
        return_value=list(providers)
    ), patch.object(
        MD, 'get_metadata_provider',
        side_effect=lambda pid: _Provider(providers[pid])
    ):
        return run(MD.search_volumes_everywhere(title))


def _by_provider(breakdown):
    return {entry['provider_id']: entry for entry in breakdown}


class the_search_records_who_it_asked(unittest.TestCase):
    def test_a_fallback_that_answered_with_nothing_still_appears(self):
        results = _search('danger jane', {
            'comicvine': [_volume('Something Else', 'comicvine')],
            'gcd': [],
            'metron': []
        })

        asked = _by_provider(_provider_breakdown(results, []))

        self.assertEqual(sorted(asked), ['comicvine', 'gcd', 'metron'])
        for provider in ('gcd', 'metron'):
            self.assertTrue(asked[provider]['asked'])
            self.assertEqual(asked[provider]['result_count'], 0)

    def test_a_fallback_that_was_never_reached_says_so(self):
        # ComicVine has an exact title, so the search stops -- even though
        # the ranker may reject that row moments later. This is the
        # `/content/Doonesbury` shape.
        results = _search('doonesbury', {
            'comicvine': [_volume('Doonesbury', 'comicvine')],
            'gcd': [_volume('Doonesbury', 'gcd')],
            'metron': []
        })

        asked = _by_provider(_provider_breakdown(results, []))

        self.assertTrue(asked['comicvine']['asked'])
        self.assertTrue(asked['comicvine']['recognised_title'])
        self.assertFalse(asked['gcd']['asked'])
        self.assertFalse(asked['metron']['asked'])

    def test_the_two_cases_are_now_distinguishable(self):
        exhausted = _by_provider(_provider_breakdown(_search('danger jane', {
            'comicvine': [_volume('Something Else', 'comicvine')],
            'gcd': []
        }), []))
        stopped = _by_provider(_provider_breakdown(_search('doonesbury', {
            'comicvine': [_volume('Doonesbury', 'comicvine')],
            'gcd': []
        }), []))

        # Identical from the results alone: GCD contributed no rows either way.
        self.assertEqual(exhausted['gcd']['result_count'], 0)
        self.assertEqual(stopped['gcd']['result_count'], 0)
        # Not identical any more.
        self.assertTrue(exhausted['gcd']['asked'])
        self.assertFalse(stopped['gcd']['asked'])

    def test_a_provider_that_failed_is_not_a_provider_that_had_nothing(self):
        class _Broken:
            async def search_volumes(self, title):
                raise RuntimeError('down')

        with patch.object(
            MD, 'configured_metadata_provider_ids',
            return_value=['comicvine', 'gcd']
        ), patch.object(
            MD, 'get_metadata_provider',
            side_effect=lambda pid: (
                _Broken() if pid == 'gcd'
                else _Provider([_volume('Something Else', 'comicvine')])
            )
        ):
            results = run(MD.search_volumes_everywhere('danger jane'))

        asked = _by_provider(_provider_breakdown(results, []))

        self.assertTrue(asked['gcd']['asked'])
        self.assertTrue(asked['gcd']['failed'])


class nothing_downstream_notices_the_change(unittest.TestCase):
    def test_the_results_are_still_just_a_list(self):
        results = _search('danger jane', {
            'comicvine': [_volume('Danger Jane', 'comicvine')]
        })

        self.assertIsInstance(results, list)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['title'], 'Danger Jane')

    def test_a_plain_list_still_produces_a_breakdown(self):
        # Every test in the suite that patches the search with a plain list
        # keeps working; it simply carries no consultation.
        breakdown = _provider_breakdown(
            [_volume('Danger Jane', 'comicvine')], []
        )

        self.assertEqual(len(breakdown), 1)
        self.assertEqual(breakdown[0]['result_count'], 1)


if __name__ == '__main__':
    unittest.main()
