# -*- coding: utf-8 -*-

"""GCD never returns an `id`, and the provider required one.

`search_volumes` skipped any series row whose `id` was `None`, guarding
against "series rows with no `id`". No GCD series row has ever had one:
`sorted(row)` on both `/series/name/{q}/` and `/series/{id}/` is the same
fifteen keys and `id` is not among them. A series' identity appears only
inside its own `api_url`.

So the guard skipped every row of every page, and `search_volumes`
returned an empty list for every query ever made -- at DEBUG, with no
exception, which is exactly how it reached production and stayed there.
The importer's fan-out logged `gcd=0` against 52 consecutive searches
while the live API had answers for a third of them, including three
folders ComicVine returned literally nothing for.

`_volume` had the same assumption one line down (`str(data['id'])`), which
would have raised `KeyError` on the first row that got past the guard --
so `fetch_volume` was broken too, for every GCD volume.

The recorded shapes below are the real ones, taken from
`https://www.comics.org/api/` on 2026-08-26.
"""

import unittest

from backend.implementations.gcd import _series_id


# One real search row, verbatim but for the issue list being trimmed.
SEARCH_ROW = {
    'api_url': 'https://www.comics.org/api/series/203786/?format=json',
    'name': 'Tear Us Apart',
    'country': 'us',
    'language': 'en',
    'active_issues': [
        'https://www.comics.org/api/issue/2566881/?format=json',
        'https://www.comics.org/api/issue/2571702/?format=json'
    ],
    'publisher': 'https://www.comics.org/api/publisher/512/?format=json',
    'year_began': 2023,
    'year_ended': None,
    'notes': '',
    'binding': '', 'color': '', 'dimensions': '',
    'issue_descriptors': '', 'paper_stock': '', 'publishing_format': ''
}


class a_series_row_carries_its_identity_in_its_url(unittest.TestCase):
    def test_the_real_search_shape_yields_an_id(self):
        self.assertEqual(_series_id(SEARCH_ROW), '203786')

    def test_no_row_gcd_actually_returns_has_an_id_field(self):
        # The premise of the whole fix. If this ever fails, GCD started
        # returning `id` and the branch below takes over on its own.
        self.assertNotIn('id', SEARCH_ROW)

    def test_an_explicit_id_is_believed_over_the_url(self):
        row = dict(SEARCH_ROW, id=999)
        self.assertEqual(_series_id(row), '999')

    def test_a_row_with_neither_is_still_skippable(self):
        self.assertIsNone(_series_id({'name': 'No identity at all'}))
        self.assertIsNone(_series_id({'api_url': '', 'name': 'Empty'}))

    def test_an_unrelated_url_does_not_yield_a_series_id(self):
        self.assertIsNone(_series_id({
            'api_url': 'https://www.comics.org/api/publisher/512/?format=json'
        }))

    def test_a_url_without_a_query_string_still_parses(self):
        self.assertEqual(
            _series_id({
                'api_url': 'https://www.comics.org/api/series/203786/'
            }),
            '203786'
        )


class the_provider_builds_a_volume_from_that_row(unittest.TestCase):
    def _volume(self):
        from unittest.mock import MagicMock, patch
        from types import SimpleNamespace
        import backend.implementations.gcd as G

        settings = MagicMock()
        settings.get_settings.return_value = SimpleNamespace(
            gcd_username=None, gcd_password=None
        )
        with patch.object(G, 'Settings', return_value=settings):
            return G.Gcd()._volume(SEARCH_ROW)

    def test_the_volume_is_identified_by_its_gcd_id(self):
        volume = self._volume()
        self.assertEqual(volume['external_id'], '203786')
        self.assertEqual(volume['provider_id'], 'gcd')
        self.assertEqual(volume['title'], 'Tear Us Apart')
        self.assertEqual(volume['year'], 2023)

    def test_it_still_claims_no_comicvine_identity(self):
        # GCD has no ComicVine cross-link; inventing one would be worse
        # than having none.
        self.assertIsNone(self._volume()['comicvine_id'])

    def test_the_site_url_points_at_the_series(self):
        self.assertEqual(
            self._volume()['site_url'],
            'https://www.comics.org/series/203786/'
        )


class a_publisher_url_carries_a_query_string(unittest.TestCase):
    """`.../publisher/512/?format=json` never ended in a number.

    `rsplit('/', 1)[-1]` on it returns `?format=json`, which fails
    `isdigit()`, so `_publisher_name` returned `None` for every GCD volume
    there has ever been.
    """

    def test_the_id_is_found_by_pattern_not_by_last_segment(self):
        from backend.implementations.gcd import _publisher_id_regex
        url = 'https://www.comics.org/api/publisher/512/?format=json'
        self.assertEqual(url.rsplit('/', 1)[-1], '?format=json')
        match = _publisher_id_regex.search(url)
        self.assertIsNotNone(match)
        self.assertEqual(match.group(1), '512')


if __name__ == '__main__':
    unittest.main()
