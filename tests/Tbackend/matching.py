import unittest
from typing import Dict

from backend.base.definitions import FilenameData, VolumeMetadata
from backend.implementations.matching import (
    select_best_volume_result_for_file,
    select_confident_volume_result_for_file)


class confident_volume_matching(unittest.TestCase):
    @staticmethod
    def _group(
        year=2020,
        volume_number=2,
        issue_number=1.0
    ) -> Dict[str, FilenameData]:
        return {
            'Batman 001.cbz': {
                'series': 'Batman',
                'year': year,
                'volume_number': volume_number,
                'special_version': None,
                'issue_number': issue_number,
                'annual': False
            }
        }

    @staticmethod
    def _volume(
        comicvine_id: int,
        year=2020,
        volume_number=2,
        issue_count=1
    ) -> VolumeMetadata:
        return {
            'comicvine_id': comicvine_id,
            'title': 'Batman',
            'year': year,
            'volume_number': volume_number,
            'cover_link': '',
            'cover': None,
            'description': '',
            'site_url': f'https://comicvine.example/{comicvine_id}',
            'aliases': [],
            'publisher': None,
            'issue_count': issue_count,
            'translated': False,
            'already_added': None,
            'issues': None
        }

    def test_review_scan_still_returns_best_guess_for_tie(self):
        first = self._volume(1)
        second = self._volume(2)

        result = select_best_volume_result_for_file(
            self._group(),
            [first, second],
            only_english=True
        )

        self.assertEqual(result, first)

    def test_confident_match_rejects_exact_tie(self):
        result = select_confident_volume_result_for_file(
            self._group(),
            [self._volume(1), self._volume(2)],
            only_english=True
        )

        self.assertIsNone(result)

    def test_confident_match_rejects_one_point_lead(self):
        winner = self._volume(1, year=2020, volume_number=2, issue_count=1)
        runner_up = self._volume(
            2,
            year=2021,
            volume_number=2,
            issue_count=1
        )

        result = select_confident_volume_result_for_file(
            self._group(),
            [winner, runner_up],
            only_english=True
        )

        self.assertIsNone(result)

    def test_confident_match_rejects_weak_single_candidate(self):
        result = select_confident_volume_result_for_file(
            self._group(year=None, volume_number=2, issue_number=1.0),
            [self._volume(1, year=2020, volume_number=2, issue_count=1)],
            only_english=True
        )

        self.assertIsNone(result)

    def test_confident_match_accepts_strong_single_candidate(self):
        winner = self._volume(1)

        result = select_confident_volume_result_for_file(
            self._group(),
            [winner],
            only_english=True
        )

        self.assertEqual(result, winner)

    def test_confident_match_accepts_clear_winner(self):
        winner = self._volume(1, year=2020, volume_number=2, issue_count=1)
        runner_up = self._volume(
            2,
            year=2010,
            volume_number=2,
            issue_count=5
        )

        result = select_confident_volume_result_for_file(
            self._group(),
            [runner_up, winner],
            only_english=True
        )

        self.assertEqual(result, winner)


if __name__ == '__main__':
    unittest.main()
