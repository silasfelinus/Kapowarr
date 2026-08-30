# -*- coding: utf-8 -*-

"""Recognising a title is a weaker test than accepting a candidate.

`search_volumes_everywhere` stopped at the first provider whose results
contained an exact title match, and the providers after it were never
asked. That test is cheaper than the one applied moments later: ComicVine
answers almost anything with fifty rows, and a row whose title is exactly
right can still be refused by the ranker on language, type or issue
coverage. The folder was then held for review having never asked the
databases that might have had it.

Job 21 of Silas's library, now that the record says which providers were
asked rather than only which ones answered: of thirty-eight no-candidate
holds, thirty-four had genuinely exhausted every provider, and four had
not. `/content/Doonesbury` and `/content/Coaraptor (2020)` stopped at a
ComicVine title the ranker would not take, with GCD and Metron unasked.
`/content/Adult/Sinner University` and `/content/Adult/The Escort`
stopped at GCD with Metron unasked.

The caller knows what it will accept, so it now says so, and the fan-out
keeps going until somebody offers something usable.
"""

import unittest
from asyncio import run
from unittest.mock import patch

from backend.features import metadata as MD


class _Provider:
    def __init__(self, results):
        self._results = results

    async def search_volumes(self, title):
        return list(self._results)


def _volume(title, provider_id):
    return {
        'comicvine_id': 1 if provider_id == 'comicvine' else None,
        'external_id': provider_id + '-1',
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


def _search(title, providers, accepts=None):
    asked = []

    def provider(pid):
        asked.append(pid)
        return _Provider(providers[pid])

    with patch.object(
        MD, 'configured_metadata_provider_ids', return_value=list(providers)
    ), patch.object(MD, 'get_metadata_provider', side_effect=provider):
        results = run(MD.search_volumes_everywhere(title, accepts=accepts))

    return results, asked


class the_doonesbury_shape(unittest.TestCase):
    """ComicVine has the title. The ranker will not take what it has."""

    PROVIDERS = {
        'comicvine': [_volume('Doonesbury', 'comicvine')],
        'gcd': [_volume('Doonesbury', 'gcd')],
        'metron': [_volume('Doonesbury', 'metron')]
    }

    def test_title_matching_alone_stops_at_the_first_provider(self):
        # The behaviour that cost those four folders their fallbacks.
        _, asked = _search('doonesbury', self.PROVIDERS)

        self.assertEqual(asked, ['comicvine'])

    def test_a_caller_that_refuses_it_gets_the_others_asked(self):
        results, asked = _search(
            'doonesbury', self.PROVIDERS,
            accepts=lambda rows: any(
                r['provider_id'] == 'metron' for r in rows
            )
        )

        self.assertEqual(asked, ['comicvine', 'gcd', 'metron'])
        self.assertEqual(
            [r['provider_id'] for r in results], ['metron'],
            'the provider that answered acceptably is the one returned'
        )

    def test_a_caller_that_accepts_the_first_still_costs_one_request(self):
        # The property the fan-out was built around: a title the default
        # provider can serve costs the single request it always did.
        _, asked = _search(
            'doonesbury', self.PROVIDERS, accepts=lambda rows: bool(rows)
        )

        self.assertEqual(asked, ['comicvine'])


class when_nobody_can_serve_it(unittest.TestCase):
    PROVIDERS = {
        'comicvine': [_volume('Something Else', 'comicvine')],
        'gcd': [_volume('Another Thing', 'gcd')]
    }

    def test_every_provider_is_asked(self):
        _, asked = _search(
            'danger jane', self.PROVIDERS, accepts=lambda rows: False
        )

        self.assertEqual(asked, ['comicvine', 'gcd'])

    def test_and_everything_gathered_comes_back(self):
        # So the review record still says what was actually considered.
        results, _ = _search(
            'danger jane', self.PROVIDERS, accepts=lambda rows: False
        )

        self.assertEqual(
            sorted(r['provider_id'] for r in results), ['comicvine', 'gcd']
        )

    def test_the_record_still_says_who_was_asked(self):
        results, _ = _search(
            'danger jane', self.PROVIDERS, accepts=lambda rows: False
        )

        consulted = {c['provider_id']: c for c in results.consulted}
        self.assertTrue(all(c['asked'] for c in consulted.values()))
        self.assertFalse(any(c['recognised'] for c in consulted.values()))


class what_library_import_asks_for(unittest.TestCase):
    def test_it_asks_whether_the_ranker_can_use_the_results(self):
        # The predicate is the same question the decision asks a few lines
        # later; anything weaker is what let a hold happen with providers
        # left unasked.
        import inspect

        from backend.features import library_import as LI

        source = inspect.getsource(LI._match_file_groups)
        self.assertIn('def usable(', source)
        self.assertIn('_rank_volume_results_for_file(group, results', source)
        self.assertIn('accepts=usable', source)


if __name__ == '__main__':
    unittest.main()
