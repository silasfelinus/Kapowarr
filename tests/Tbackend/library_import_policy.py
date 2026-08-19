import unittest
from typing import Dict

from backend.base.definitions import FilenameData, VolumeMetadata
from backend.features.library_import_policy import (
    REVIEW_REASON_NO_CANDIDATE, REVIEW_REASON_TIE, REVIEW_REASON_WEAK_SCORE,
    select_auto_import_volume_result)


class continuous_import_policy(unittest.TestCase):
    @staticmethod
    def _group(
        series='Batman',
        year=2020,
        volume_number=2,
        issue_number=1.0
    ) -> Dict[str, FilenameData]:
        return {
            f'{series} 001.cbz': {
                'series': series,
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
        title='Batman',
        year=2020,
        volume_number=2,
        issue_count=1
    ) -> VolumeMetadata:
        return {
            'comicvine_id': comicvine_id,
            'title': title,
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

    def test_exact_tie_still_requires_review(self):
        result, reason = select_auto_import_volume_result(
            self._group(),
            [self._volume(1), self._volume(2)],
            only_english=True
        )

        self.assertIsNone(result)
        self.assertEqual(reason, REVIEW_REASON_TIE)

    def test_one_point_lead_is_accepted(self):
        winner = self._volume(1, year=2020, volume_number=2, issue_count=1)
        runner_up = self._volume(
            2,
            year=2021,
            volume_number=2,
            issue_count=1
        )

        result, reason = select_auto_import_volume_result(
            self._group(),
            [winner, runner_up],
            only_english=True
        )

        self.assertEqual(result, winner)
        self.assertIsNone(reason)

    def test_unique_viable_candidate_with_sparse_evidence_is_accepted(self):
        winner = self._volume(
            1,
            year=2020,
            volume_number=1,
            issue_count=12
        )

        result, reason = select_auto_import_volume_result(
            self._group(year=None, volume_number=None, issue_number=None),
            [winner],
            only_english=True
        )

        self.assertEqual(result, winner)
        self.assertIsNone(reason)

    def test_far_year_rerelease_is_accepted_when_it_is_the_unique_viable_title(self):
        winner = self._volume(
            1,
            title='Lost Girls',
            year=2026,
            volume_number=1,
            issue_count=3
        )

        result, reason = select_auto_import_volume_result(
            self._group(
                series='Lost Girls',
                year=2006,
                volume_number=None,
                issue_number=1.0
            ),
            [winner],
            only_english=True
        )

        self.assertEqual(result, winner)
        self.assertIsNone(reason)

    def test_explicit_issue_count_contradiction_still_requires_review(self):
        result, reason = select_auto_import_volume_result(
            self._group(
                year=None,
                volume_number=None,
                issue_number=10.0
            ),
            [self._volume(
                1,
                year=2020,
                volume_number=1,
                issue_count=9
            )],
            only_english=True
        )

        self.assertIsNone(result)
        self.assertEqual(reason, REVIEW_REASON_WEAK_SCORE)

    def test_no_viable_candidate_reports_reason(self):
        result, reason = select_auto_import_volume_result(
            self._group(),
            [],
            only_english=True
        )

        self.assertIsNone(result)
        self.assertEqual(reason, REVIEW_REASON_NO_CANDIDATE)

    def test_strong_single_candidate_is_accepted(self):
        winner = self._volume(1)

        result, reason = select_auto_import_volume_result(
            self._group(),
            [winner],
            only_english=True
        )

        self.assertEqual(result, winner)
        self.assertIsNone(reason)


if __name__ == '__main__':
    unittest.main()
