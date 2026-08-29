# -*- coding: utf-8 -*-

"""Corroboration was being read as ambiguity.

Continuous import searches every configured provider and ranks the combined
pool -- that is the whole point of having Metron and GCD behind ComicVine.
It also means a series more than one of them carries arrives more than
once, and under the margin rule each copy became a rival to the others.

`/content/Fight Club 2 (2015)` in Silas's library was held for human review
across five tied candidates: "Fight Club 2", 2015, volume 1, from ComicVine
twice, GCD twice and Metron once. Three databases independently naming the
same series is the strongest corroboration anywhere in this pipeline, and
it produced the same verdict as no evidence at all -- on job 16, job 17 and
job 18 alike, because a hold is not a decision.

The rule stays narrow deliberately. Two rows inside one database with the
same title, year and volume number are two rows that database chose to keep
apart, and merging them is not this function's call.
"""

import unittest

from backend.features.library_import_policy import (REVIEW_REASON_TIE,
                                                    select_auto_import_volume_result)


def _volume(
    external_id,
    provider_id='comicvine',
    title='Fight Club 2',
    year=2015,
    volume_number=1,
    issue_count=10
):
    return {
        'comicvine_id': external_id if provider_id == 'comicvine' else None,
        'external_id': external_id,
        'provider_id': provider_id,
        'title': title,
        'year': year,
        'volume_number': volume_number,
        'cover_link': '',
        'cover': None,
        'description': '',
        'site_url': 'https://example.test/%s' % external_id,
        'aliases': [],
        'publisher': None,
        'issue_count': issue_count,
        'translated': False,
        'already_added': None,
        'issues': None
    }


def _group():
    return {
        '/content/Fight Club 2 (2015)/Fight Club 2 001.cbz': {
            'series': 'Fight Club 2',
            'year': 2015,
            'volume_number': 1,
            'special_version': None,
            'issue_number': 1.0,
            'annual': False
        }
    }


class the_shape_that_held_fight_club(unittest.TestCase):
    RESULTS = [
        _volume(1, 'comicvine', issue_count=10),
        _volume(2, 'comicvine', issue_count=2),
        _volume('3', 'gcd', issue_count=29),
        _volume('4', 'gcd', issue_count=2),
        _volume('5', 'metron', issue_count=10)
    ]

    def test_it_imports_instead_of_holding(self):
        result, reason = select_auto_import_volume_result(
            _group(), list(self.RESULTS), only_english=True
        )

        self.assertIsNotNone(result)
        self.assertIsNone(reason)

    def test_the_users_own_provider_wins(self):
        # The library is keyed on the default provider, so a GCD row with a
        # larger issue count must not displace it.
        result, _ = select_auto_import_volume_result(
            _group(), list(self.RESULTS), only_english=True
        )

        self.assertEqual(result['provider_id'], 'comicvine')

    def test_and_then_the_fullest_record(self):
        # Within that provider the two rows differ only in how much of the
        # series each knows about.
        result, _ = select_auto_import_volume_result(
            _group(), list(self.RESULTS), only_english=True
        )

        self.assertEqual(result['issue_count'], 10)

    def test_the_answer_does_not_move_between_passes(self):
        # A hold that resolves differently each pass is its own bug.
        chosen = {
            select_auto_import_volume_result(
                _group(), list(reversed(self.RESULTS)), only_english=True
            )[0]['external_id']
            for _ in range(3)
        }
        chosen |= {
            select_auto_import_volume_result(
                _group(), list(self.RESULTS), only_english=True
            )[0]['external_id']
        }

        self.assertEqual(len(chosen), 1)


class what_is_still_a_tie(unittest.TestCase):
    def test_two_rows_in_one_database(self):
        # ComicVine keeps these apart; this does not presume to merge them.
        result, reason = select_auto_import_volume_result(
            _group(),
            [
                _volume(1, 'comicvine', issue_count=10),
                _volume(2, 'comicvine', issue_count=10)
            ],
            only_english=True
        )

        self.assertIsNone(result)
        self.assertEqual(reason, REVIEW_REASON_TIE)

    def test_two_genuinely_different_volumes(self):
        # Same title, different years: that is the ambiguity a human is for,
        # and it is exactly what this must not swallow.
        result, reason = select_auto_import_volume_result(
            {
                '/content/Neverwhere/Neverwhere.cbz': {
                    'series': 'Neverwhere', 'year': None, 'volume_number': 1,
                    'special_version': None, 'issue_number': None,
                    'annual': False
                }
            },
            [
                _volume('a', 'gcd', title='Neverwhere', year=1978, issue_count=2),
                _volume('b', 'metron', title='Neverwhere', year=2015, issue_count=1)
            ],
            only_english=True
        )

        self.assertIsNone(result)
        self.assertEqual(reason, REVIEW_REASON_TIE)

    def test_two_different_series_from_two_databases(self):
        # `/content/Future State` tied across 35 candidates spanning 14
        # distinct series. Providers disagreeing about which series this is
        # must stay a hold no matter how many of them answered.
        group = {
            '/content/Future State/Future State 001.cbz': {
                'series': 'Future State', 'year': 2021, 'volume_number': 1,
                'special_version': None, 'issue_number': 1.0, 'annual': False
            }
        }
        result, reason = select_auto_import_volume_result(
            group,
            [
                _volume(1, 'comicvine', title='Future State: Gotham',
                        year=2021, issue_count=18),
                _volume('2', 'gcd', title='Future State: Teen Titans',
                        year=2021, issue_count=18)
            ],
            only_english=True
        )

        self.assertIsNone(result)
        self.assertEqual(reason, REVIEW_REASON_TIE)


if __name__ == '__main__':
    unittest.main()
