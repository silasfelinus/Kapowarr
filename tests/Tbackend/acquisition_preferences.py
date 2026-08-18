# -*- coding: utf-8 -*-

from unittest import TestCase
from unittest.mock import patch

from backend.base.definitions import DownloadType
from backend.features import acquisition_preferences as preferences
from backend.features.search import _rank_search_result


def group(title):
    return {
        'web_sub_title': title,
        'info': {
            'series': 'Batman',
            'year': 2020,
            'volume_number': 1,
            'special_version': None,
            'issue_number': 1.0,
            'annual': False
        },
        'links': {}
    }


def result(issue_number, match=True):
    return {
        'series': 'batman',
        'year': 2020,
        'volume_number': 1,
        'special_version': None,
        'issue_number': issue_number,
        'annual': False,
        'link': 'https://example.invalid/release',
        'display_title': 'Batman',
        'source': 'test',
        'match': match,
        'match_issue': None
    }


class acquisition_source_order(TestCase):
    def test_default_preserves_historical_protocol_order(self):
        with patch.object(
            preferences,
            'get_acquisition_preferences',
            return_value={
                'acquisition_source_preference': ['direct', 'torrent', 'usenet'],
                'getcomics_quality_preference': 'any',
                'pack_preference': 'neutral'
            }
        ):
            ordered = preferences.ordered_download_types([
                DownloadType.DIRECT,
                DownloadType.USENET,
                DownloadType.TORRENT
            ])

        self.assertEqual(ordered, [
            DownloadType.DIRECT,
            DownloadType.TORRENT,
            DownloadType.USENET
        ])

    def test_user_order_is_respected(self):
        with patch.object(
            preferences,
            'get_acquisition_preferences',
            return_value={
                'acquisition_source_preference': ['usenet', 'direct', 'torrent'],
                'getcomics_quality_preference': 'any',
                'pack_preference': 'neutral'
            }
        ):
            ordered = preferences.ordered_download_types([
                DownloadType.TORRENT,
                DownloadType.DIRECT,
                DownloadType.USENET
            ])

        self.assertEqual(ordered, [
            DownloadType.USENET,
            DownloadType.DIRECT,
            DownloadType.TORRENT
        ])


class pack_policy(TestCase):
    def test_neutral_does_not_change_rank_component(self):
        with patch.object(
            preferences,
            'get_acquisition_preferences',
            return_value={
                'acquisition_source_preference': ['direct', 'torrent', 'usenet'],
                'getcomics_quality_preference': 'any',
                'pack_preference': 'neutral'
            }
        ):
            self.assertEqual(preferences.pack_preference_rank((1.0, 5.0)), 0)
            self.assertEqual(preferences.pack_preference_rank(3.0), 0)

    def test_prefer_and_avoid_reverse_range_tiebreak(self):
        base = {
            'acquisition_source_preference': ['direct', 'torrent', 'usenet'],
            'getcomics_quality_preference': 'any'
        }
        with patch.object(
            preferences,
            'get_acquisition_preferences',
            return_value={**base, 'pack_preference': 'prefer'}
        ):
            self.assertLess(
                preferences.pack_preference_rank((1.0, 5.0)),
                preferences.pack_preference_rank(3.0)
            )

        with patch.object(
            preferences,
            'get_acquisition_preferences',
            return_value={**base, 'pack_preference': 'avoid'}
        ):
            self.assertGreater(
                preferences.pack_preference_rank((1.0, 5.0)),
                preferences.pack_preference_rank(3.0)
            )

    @patch('backend.features.search.pack_preference_rank')
    def test_pack_policy_never_outranks_match_correctness(self, pack_rank):
        pack_rank.side_effect = lambda issue: 0 if isinstance(issue, tuple) else 1
        matched_single = _rank_search_result(
            result(3.0, match=True), 'batman', 1, (2020, 2020), 3.0
        )
        mismatched_pack = _rank_search_result(
            result((1.0, 5.0), match=False), 'batman', 1, (2020, 2020), 3.0
        )
        self.assertLess(matched_single, mismatched_pack)


class getcomics_quality(TestCase):
    def test_quality_labels_use_token_boundaries(self):
        self.assertEqual(preferences.getcomics_quality_label('Batman 001 (HD)'), 'hd')
        self.assertEqual(preferences.getcomics_quality_label('Batman 001 - SD'), 'sd')
        self.assertEqual(preferences.getcomics_quality_label('Shadowman 001'), 'unknown')
        self.assertEqual(preferences.getcomics_quality_label('Batman HDCover'), 'unknown')

    def test_hd_preference_orders_hd_unknown_sd(self):
        with patch.object(
            preferences,
            'get_acquisition_preferences',
            return_value={
                'acquisition_source_preference': ['direct', 'torrent', 'usenet'],
                'getcomics_quality_preference': 'hd',
                'pack_preference': 'neutral'
            }
        ):
            ordered = preferences.order_getcomics_groups([
                group('Batman 001 SD'),
                group('Batman 001'),
                group('Batman 001 HD')
            ])

        self.assertEqual(
            [entry['web_sub_title'] for entry in ordered],
            ['Batman 001 HD', 'Batman 001', 'Batman 001 SD']
        )

    def test_sd_preference_reverses_explicit_variants(self):
        with patch.object(
            preferences,
            'get_acquisition_preferences',
            return_value={
                'acquisition_source_preference': ['direct', 'torrent', 'usenet'],
                'getcomics_quality_preference': 'sd',
                'pack_preference': 'neutral'
            }
        ):
            ordered = preferences.order_getcomics_groups([
                group('Batman 001 HD'),
                group('Batman 001 SD')
            ])

        self.assertEqual(
            [entry['web_sub_title'] for entry in ordered],
            ['Batman 001 SD', 'Batman 001 HD']
        )

    def test_any_preference_preserves_page_order(self):
        original = [
            group('Batman 001 SD'),
            group('Batman 001'),
            group('Batman 001 HD')
        ]
        with patch.object(
            preferences,
            'get_acquisition_preferences',
            return_value={
                'acquisition_source_preference': ['direct', 'torrent', 'usenet'],
                'getcomics_quality_preference': 'any',
                'pack_preference': 'neutral'
            }
        ):
            ordered = preferences.order_getcomics_groups(original)

        self.assertEqual(
            [entry['web_sub_title'] for entry in ordered],
            [entry['web_sub_title'] for entry in original]
        )
