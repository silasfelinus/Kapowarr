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

    def test_issue_capacity_breaks_same_score_tie(self):
        too_short = self._volume(
            1,
            title='Alien Paradiso',
            year=2025,
            volume_number=1,
            issue_count=1
        )
        full_run = self._volume(
            2,
            title='Alien Paradiso',
            year=2025,
            volume_number=1,
            issue_count=5
        )

        result, reason = select_auto_import_volume_result(
            self._group(
                series='Alien Paradiso',
                year=2025,
                volume_number=1,
                issue_number=4.0
            ),
            [too_short, full_run],
            only_english=True
        )

        self.assertEqual(result, full_run)
        self.assertIsNone(reason)

    def test_exact_year_beats_default_volume_number_tie(self):
        exact_year = self._volume(
            1,
            title='Batman Beyond',
            year=2015,
            volume_number=6,
            issue_count=16
        )
        default_volume = self._volume(
            2,
            title='Batman Beyond',
            year=1999,
            volume_number=1,
            issue_count=6
        )

        result, reason = select_auto_import_volume_result(
            self._group(
                series='Batman Beyond',
                year=2015,
                volume_number=1,
                issue_number=5.0
            ),
            [default_volume, exact_year],
            only_english=True
        )

        self.assertEqual(result, exact_year)
        self.assertIsNone(reason)

    def test_issue_capacity_can_outweigh_implicit_volume_one(self):
        tiny_volume_one = self._volume(
            1,
            title='Savage Dragon',
            year=1992,
            volume_number=1,
            issue_count=3
        )
        long_volume_two = self._volume(
            2,
            title='Savage Dragon',
            year=1993,
            volume_number=2,
            issue_count=284
        )

        result, reason = select_auto_import_volume_result(
            self._group(
                series='Savage Dragon',
                year=2011,
                volume_number=1,
                issue_number=172.0
            ),
            [tiny_volume_one, long_volume_two],
            only_english=True
        )

        self.assertEqual(result, long_volume_two)
        self.assertIsNone(reason)

    def test_ambiguous_single_issue_editions_remain_tied(self):
        result, reason = select_auto_import_volume_result(
            self._group(
                series='Indian Summer',
                year=None,
                volume_number=1,
                issue_number=1.0
            ),
            [
                self._volume(
                    1,
                    title='Indian Summer',
                    year=1994,
                    volume_number=1,
                    issue_count=1
                ),
                self._volume(
                    2,
                    title='Indian Summer',
                    year=1986,
                    volume_number=1,
                    issue_count=1
                )
            ],
            only_english=True
        )

        self.assertIsNone(result)
        self.assertEqual(reason, REVIEW_REASON_TIE)

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
