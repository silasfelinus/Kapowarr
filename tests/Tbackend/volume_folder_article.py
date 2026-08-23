from unittest import TestCase

from backend.base.definitions import VolumeNamingKeys


class VolumeFolderArticleNamingTest(TestCase):
    @staticmethod
    def _keys(series_name: str) -> VolumeNamingKeys:
        return VolumeNamingKeys(
            series_name=series_name,
            clean_series_name=series_name,
            volume_number='1',
            comicvine_id=None,
            year=2024,
            publisher=None,
            special_version=None
        )

    def test_removes_the_from_articleless_series_name(self) -> None:
        keys = self._keys('The Rocketfellers')
        self.assertEqual(keys.series_name_no_article, 'Rocketfellers')
        self.assertEqual(
            keys.todict()['series_name_no_article'],
            'Rocketfellers'
        )

    def test_removes_a_from_articleless_series_name(self) -> None:
        self.assertEqual(
            self._keys('A Righteous Thirst for Vengeance').series_name_no_article,
            'Righteous Thirst for Vengeance'
        )

    def test_leaves_titles_without_leading_article_alone(self) -> None:
        self.assertEqual(
            self._keys('Rocketfellers').series_name_no_article,
            'Rocketfellers'
        )
