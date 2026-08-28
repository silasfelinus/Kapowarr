# -*- coding: utf-8 -*-

"""A run-context winner must keep the identity the matcher gave it.

`apply_series_run_context` promotes one whole-run winner across a
folder's publication-year groups, and it does that by *replacing* the
match each group already had with a dict `_format_context_match` builds
from scratch. That dict carried `id` and nothing else.

`id` is the ComicVine ID, and GCD has none by design. So a GCD volume
promoted here arrived at the import gate with `id: None` and no identity
beside it, failed `match_identifies_a_volume`, and -- because a winning
match dict carries no `review_reason` -- was held as `no-candidate`: "no
database in the world has this", about a volume GCD had just named.

Third time for this same identity: #140 taught `Library.add` to take a
provider's own ID, #150 taught the review gate to accept one, and this
path threw both away afterwards. It only bites a folder whose files split
into two or more groups, which is why it outlived those fixes -- the
single-group folders they were found on never reach this code.

Live example, 2026-08-27 job 15: `/content/Adult/AstroWitch` split into
three groups, GCD's `Astrowitch` (2023) was the only viable candidate at
policy score 4, and all three groups were held as `no-candidate`.
"""

import unittest

from backend.features.library_import import match_identifies_a_volume
from backend.features.library_import_context import _format_context_match


def _result(comicvine_id, provider_id=None, external_id=None):
    result = {
        'comicvine_id': comicvine_id,
        'title': 'Astrowitch',
        'year': 2023,
        'issue_count': 89,
        'site_url': 'https://www.comics.org/series/205378/'
    }
    if provider_id is not None:
        result['provider_id'] = provider_id
    if external_id is not None:
        result['external_id'] = external_id
    return result


class a_promoted_winner_is_still_importable(unittest.TestCase):
    def test_a_gcd_winner_survives_the_promotion(self):
        match = _format_context_match(
            _result(None, 'gcd', '205378')
        )
        self.assertTrue(match_identifies_a_volume(match))
        self.assertEqual(match['provider_id'], 'gcd')
        self.assertEqual(match['external_id'], '205378')

    def test_a_comicvine_winner_is_unchanged(self):
        match = _format_context_match(
            _result(4050, 'comicvine', 4050)
        )
        self.assertTrue(match_identifies_a_volume(match))
        self.assertEqual(match['id'], 4050)
        self.assertEqual(match['provider_id'], 'comicvine')

    def test_a_result_predating_provider_identity_reads_as_comicvine(self):
        # Absent has always meant ComicVine; a bare result must not
        # suddenly acquire a different provider.
        match = _format_context_match(_result(4050))
        self.assertEqual(match['provider_id'], 'comicvine')
        self.assertEqual(match['external_id'], 4050)
        self.assertTrue(match_identifies_a_volume(match))

    def test_a_metron_winner_without_a_cross_reference_survives(self):
        # Metron carries a ComicVine ID only when the series happens to
        # have one.
        match = _format_context_match(_result(None, 'metron', '4050-1'))
        self.assertTrue(match_identifies_a_volume(match))
        self.assertEqual(match['external_id'], '4050-1')

    def test_the_rest_of_the_match_is_untouched(self):
        match = _format_context_match(_result(None, 'gcd', '205378'))
        self.assertEqual(match['title'], 'Astrowitch (2023)')
        self.assertEqual(match['issue_count'], 89)
        self.assertTrue(match['series_context'])

    def test_it_carries_no_review_reason(self):
        # A winner is not a hold. If this ever gains one, the gate is
        # being asked the wrong question again.
        self.assertNotIn(
            'review_reason', _format_context_match(_result(None, 'gcd', '1'))
        )


if __name__ == '__main__':
    unittest.main()
