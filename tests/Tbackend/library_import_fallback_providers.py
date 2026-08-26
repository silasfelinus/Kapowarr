# -*- coding: utf-8 -*-

"""A fallback provider's answer must survive to the import, and to the record.

#139 asked GCD and Metron when ComicVine did not recognise a title and
#140 carried the provider's own identity into `Library.add`. The branch
deciding whether to import at all still asked only whether the ComicVine
ID was set, so a GCD rescue -- which has no ComicVine ID by design -- was
routed to review and, having no `review_reason` of its own, stamped
`no-candidate`. The postmortem then recorded neither which provider a
candidate came from nor which providers had been asked, so the resulting
record could not be told apart from one where no database had the title.
"""

import unittest

from backend.base.definitions import SpecialVersion
from backend.features.library_import import match_identifies_a_volume
from backend.features.library_import_diagnostics import (
    RAW_SEARCH_CAPTURE_LIMIT,
    build_review_diagnostics,
)


def _candidate(
    title='Arclight',
    comicvine_id=None,
    provider_id='metron',
    external_id='4050-1',
    year=2016,
    volume_number=1,
    issue_count=2
):
    return {
        'comicvine_id': comicvine_id,
        'provider_id': provider_id,
        'external_id': external_id,
        'title': title,
        'year': year,
        'volume_number': volume_number,
        'cover_link': '',
        'cover': None,
        'description': '',
        'site_url': '',
        'aliases': [],
        'publisher': None,
        'issue_count': issue_count,
        'translated': False,
        'already_added': None,
        'issues': None
    }


def _group(series='Arclight', year=2016):
    return {
        '/content/Arclight/Arclight 01.cbz': {
            'series': series,
            'year': year,
            'volume_number': 1,
            'issue_number': 1.0,
            'special_version': SpecialVersion.NORMAL,
            'annual': False
        }
    }


class a_provider_match_without_a_comicvine_id_is_importable(
    unittest.TestCase
):
    def test_a_gcd_match_is_not_held_for_review(self):
        # GCD has no ComicVine cross-link, so `id` is None by design.
        self.assertTrue(match_identifies_a_volume({
            'id': None,
            'provider_id': 'gcd',
            'external_id': 'gcd-12345'
        }))

    def test_a_comicvine_match_is_still_importable(self):
        self.assertTrue(match_identifies_a_volume({
            'id': 4050,
            'provider_id': 'comicvine',
            'external_id': 4050
        }))

    def test_a_genuine_miss_is_still_held(self):
        # The shape `_match_file_groups` builds when nothing was found.
        self.assertFalse(match_identifies_a_volume({
            'id': None,
            'title': None,
            'issue_count': None,
            'link': None,
            'review_reason': 'no-candidate',
            'review_candidate': None
        }))

    def test_a_match_dict_predating_provider_identity_is_unchanged(self):
        # Absent means ComicVine; a bare `id` must still decide.
        self.assertTrue(match_identifies_a_volume({'id': 4050}))
        self.assertFalse(match_identifies_a_volume({'id': None}))


class the_record_says_which_database_answered(unittest.TestCase):
    def test_every_candidate_names_its_provider(self):
        diagnostics = build_review_diagnostics(
            _group(),
            [_candidate()],
            only_english=True,
            review_reason='tie'
        )
        raw = diagnostics['raw_search_results'][0]
        self.assertEqual(raw['provider_id'], 'metron')
        self.assertEqual(raw['external_id'], '4050-1')

    def test_a_result_without_provider_identity_reads_as_comicvine(self):
        bare = _candidate(comicvine_id=101)
        del bare['provider_id']
        del bare['external_id']
        diagnostics = build_review_diagnostics(
            _group(),
            [bare],
            only_english=True,
            review_reason='tie'
        )
        self.assertEqual(
            diagnostics['raw_search_results'][0]['provider_id'],
            'comicvine'
        )

    def test_candidates_sharing_a_null_comicvine_id_keep_their_own_scores(self):
        # Keyed on `comicvine_id`, every GCD row landed in one `None`
        # bucket and was handed the last one's score.
        candidates = [
            _candidate(external_id='gcd-1', provider_id='gcd', year=2016),
            _candidate(external_id='gcd-2', provider_id='gcd', year=1999)
        ]
        diagnostics = build_review_diagnostics(
            _group(year=2016),
            candidates,
            only_english=True,
            review_reason='tie'
        )
        scores = {
            row['external_id']: row['viable_score']
            for row in diagnostics['raw_search_results']
        }
        self.assertNotEqual(scores['gcd-1'], scores['gcd-2'])

    def test_providers_that_answered_are_counted_over_the_whole_response(self):
        # ComicVine's fifty unrelated rows, then the fallback that had it.
        results = [
            _candidate(
                title=f'Unrelated {index}',
                comicvine_id=index,
                provider_id='comicvine',
                external_id=index
            )
            for index in range(50)
        ] + [_candidate(provider_id='gcd', external_id='gcd-1')]

        diagnostics = build_review_diagnostics(
            _group(),
            results,
            only_english=True,
            review_reason='tie'
        )
        breakdown = {
            entry['provider_id']: entry
            for entry in diagnostics['providers']
        }
        self.assertEqual(breakdown['comicvine']['result_count'], 50)
        self.assertEqual(breakdown['comicvine']['viable_count'], 0)
        self.assertEqual(breakdown['gcd']['result_count'], 1)
        self.assertEqual(breakdown['gcd']['viable_count'], 1)

    def test_a_fallback_is_captured_from_behind_a_full_comicvine_page(self):
        results = [
            _candidate(
                title=f'Unrelated {index}',
                comicvine_id=index,
                provider_id='comicvine',
                external_id=index
            )
            for index in range(50)
        ] + [_candidate(provider_id='gcd', external_id='gcd-1')]

        diagnostics = build_review_diagnostics(
            _group(),
            results,
            only_english=True,
            review_reason='tie'
        )
        captured = diagnostics['raw_search_results']
        providers = [row['provider_id'] for row in captured]
        self.assertEqual(providers.count('comicvine'), RAW_SEARCH_CAPTURE_LIMIT)
        self.assertIn('gcd', providers)


if __name__ == '__main__':
    unittest.main()
