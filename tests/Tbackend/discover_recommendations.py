import unittest
from unittest.mock import AsyncMock, patch

from backend.features import discover as discover_feature_module
from backend.features.discover import (_recommendation_tokens,
                                       _score_related_title,
                                       get_recommended_discover_feed,
                                       recommend_discover_items)


def _item(series, link=None):
    return {
        'series': series,
        'year': None,
        'volume_number': None,
        'special_version': None,
        'issue_number': None,
        'annual': False,
        'link': link or f'http://x/{series}',
        'display_title': series,
        'source': 'GetComics',
        'cover': None
    }


def _volume(id, title):
    return {'id': id, 'title': title}


class recommendation_tokens(unittest.TestCase):
    def test_drops_generic_comic_words(self):
        self.assertEqual(
            _recommendation_tokens('The Batman Comics Vol. 2'),
            ('batman',)
        )

    def test_keeps_numeric_franchise_tokens(self):
        self.assertEqual(
            _recommendation_tokens('100 Bullets: Brother Lono'),
            ('100', 'bullets', 'brother', 'lono')
        )


class related_title_scoring(unittest.TestCase):
    def test_one_word_franchise_matches_longer_title(self):
        self.assertGreaterEqual(
            _score_related_title('Batman and Robin', 'Batman'),
            5
        )

    def test_multi_token_franchise_matches_spinoff(self):
        self.assertGreaterEqual(
            _score_related_title('100 Bullets: Brother Lono', '100 Bullets'),
            5
        )

    def test_unrelated_title_is_zero(self):
        self.assertEqual(
            _score_related_title('Saga', 'Batman'),
            0
        )

    def test_single_weak_shared_word_stays_below_threshold(self):
        self.assertLess(
            _score_related_title('Dark Crisis', 'Dark Horse Presents'),
            discover_feature_module.RECOMMENDATION_MIN_SCORE
        )


class recommend_items(unittest.TestCase):
    def test_recommends_unowned_related_release_with_reason(self):
        recommendations = recommend_discover_items(
            [_item('Batman and Robin')],
            [_volume(7, 'Batman')]
        )

        self.assertEqual(len(recommendations), 1)
        self.assertEqual(recommendations[0]['related_volume_id'], 7)
        self.assertEqual(
            recommendations[0]['recommendation_reason'],
            'Because you collect Batman'
        )

    def test_does_not_recommend_already_owned_release(self):
        recommendations = recommend_discover_items(
            [_item('Batman')],
            [_volume(7, 'Batman')]
        )
        self.assertEqual(recommendations, [])

    def test_does_not_recommend_weak_overlap(self):
        recommendations = recommend_discover_items(
            [_item('Dark Crisis')],
            [_volume(7, 'Dark Horse Presents')]
        )
        self.assertEqual(recommendations, [])

    def test_best_library_match_drives_reason(self):
        recommendations = recommend_discover_items(
            [_item('Batman and Robin')],
            [_volume(1, 'Robin'), _volume(2, 'Batman')]
        )
        self.assertEqual(recommendations[0]['related_volume_id'], 2)

    def test_stronger_recommendations_sort_first(self):
        recommendations = recommend_discover_items(
            [_item('New Avengers'), _item('Batman and Robin')],
            [_volume(1, 'Avengers'), _volume(2, 'Batman')]
        )
        self.assertGreaterEqual(
            recommendations[0]['recommendation_score'],
            recommendations[1]['recommendation_score']
        )


class recommended_feed(unittest.TestCase):
    def test_uses_recent_window_and_library(self):
        items = [_item('Batman and Robin')]
        with patch.object(
            discover_feature_module, '_fetch_recent_discover_items',
            new=AsyncMock(return_value=(items, 3))
        ), patch.object(
            discover_feature_module.Library, 'get_public_volumes',
            return_value=[_volume(2, 'Batman')]
        ):
            recommendations, pages_scanned = get_recommended_discover_feed()

        self.assertEqual(pages_scanned, 3)
        self.assertEqual(len(recommendations), 1)
        self.assertEqual(recommendations[0]['related_volume_title'], 'Batman')


if __name__ == '__main__':
    unittest.main()
