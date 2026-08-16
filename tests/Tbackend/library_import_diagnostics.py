import json
import os
import tempfile
import unittest
from unittest.mock import patch

from backend.base.definitions import SpecialVersion
from backend.features.library_import_diagnostics import (
    POSTMORTEM_FILENAME,
    append_review_postmortem,
    build_review_diagnostics,
)
from backend.internals.db import DBConnection


class library_import_review_diagnostics(unittest.TestCase):
    @staticmethod
    def _candidate(
        comicvine_id,
        title='Batman',
        year=2020,
        volume_number=3,
        issue_count=12
    ):
        return {
            'comicvine_id': comicvine_id,
            'title': title,
            'year': year,
            'volume_number': volume_number,
            'cover_link': '',
            'cover': None,
            'description': '',
            'site_url': f'https://example.test/{comicvine_id}',
            'aliases': [],
            'publisher': 'Example',
            'issue_count': issue_count,
            'translated': False,
            'already_added': None,
            'issues': None
        }

    @staticmethod
    def _group():
        return {
            '/library/Batman/Batman 001.cbz': {
                'series': 'Batman',
                'year': 2020,
                'volume_number': 3,
                'issue_number': 1.0,
                'special_version': SpecialVersion.NORMAL,
                'annual': False
            }
        }

    def test_tie_diagnostics_keep_ranked_and_raw_candidates(self):
        candidates = [
            self._candidate(101),
            self._candidate(102),
            self._candidate(999, title='Not Batman')
        ]

        diagnostics = build_review_diagnostics(
            self._group(),
            candidates,
            only_english=True,
            review_reason='tie'
        )

        self.assertEqual(diagnostics['search_query'], 'batman')
        self.assertEqual(diagnostics['review_reason'], 'tie')
        self.assertEqual(diagnostics['thresholds']['minimum_score'], 4)
        self.assertEqual(diagnostics['thresholds']['minimum_margin'], 1)
        self.assertEqual(diagnostics['decision']['best_score'], 4)
        self.assertEqual(diagnostics['decision']['runner_up_score'], 4)
        self.assertEqual(diagnostics['decision']['score_margin'], 0)
        self.assertEqual(diagnostics['decision']['raw_result_count'], 3)
        self.assertEqual(diagnostics['decision']['viable_candidate_count'], 2)

        viable = diagnostics['viable_candidates']
        self.assertEqual([candidate['comicvine_id'] for candidate in viable], [101, 102])
        self.assertEqual([candidate['score'] for candidate in viable], [4, 4])

        raw_by_id = {
            candidate['comicvine_id']: candidate
            for candidate in diagnostics['raw_search_results']
        }
        self.assertEqual(raw_by_id[101]['viable_score'], 4)
        self.assertEqual(raw_by_id[102]['viable_score'], 4)
        self.assertIsNone(raw_by_id[999]['viable_score'])

        parsed = diagnostics['files'][0]['parsed']
        self.assertIsNone(parsed['special_version'])

    def test_jsonl_postmortem_is_stable_and_machine_readable(self):
        diagnostics = build_review_diagnostics(
            self._group(),
            [self._candidate(101), self._candidate(102)],
            only_english=True,
            review_reason='tie'
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = os.path.join(temp_dir, 'Kapowarr.db')
            with patch.object(DBConnection, 'file', database_path):
                path = append_review_postmortem(
                    job_id=7,
                    folder='/library/Batman',
                    folder_position=31,
                    group_number=2,
                    diagnostics=diagnostics
                )

            self.assertEqual(
                path,
                os.path.join(temp_dir, POSTMORTEM_FILENAME)
            )
            with open(path, 'r', encoding='utf-8') as postmortem_file:
                rows = [json.loads(line) for line in postmortem_file]

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['record_id'], '7:31:2')
        self.assertEqual(rows[0]['job_id'], 7)
        self.assertEqual(rows[0]['folder'], '/library/Batman')
        self.assertEqual(rows[0]['decision']['score_margin'], 0)
        self.assertEqual(len(rows[0]['viable_candidates']), 2)


if __name__ == '__main__':
    unittest.main()
