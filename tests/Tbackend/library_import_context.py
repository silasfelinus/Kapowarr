import unittest

from backend.features.library_import_context import apply_series_run_context


class series_run_import_context(unittest.TestCase):
    @staticmethod
    def _file_data(year, issue_number):
        return {
            'series': 'Penthouse Comix',
            'year': year,
            'volume_number': 1,
            'special_version': None,
            'issue_number': float(issue_number),
            'annual': False,
        }

    @classmethod
    def _group(cls, year, issues):
        return {
            f'/library/Penthouse Comix ({year or 1994})/Penthouse Comix {issue:03d}.cbr':
                cls._file_data(year, issue)
            for issue in issues
        }

    @staticmethod
    def _candidate(comicvine_id, year, issue_count, publisher):
        return {
            'comicvine_id': comicvine_id,
            'title': 'Penthouse Comix',
            'year': year,
            'volume_number': 1,
            'issue_count': issue_count,
            'translated': False,
            'site_url': f'https://example.test/4050-{comicvine_id}/',
            'publisher': publisher,
            'aliases': [],
            'already_added': None,
        }

    def test_publication_year_groups_use_whole_run_evidence(self):
        groups = {
            1: self._group(None, (1, 2, 4, 17, 18, 19, 20, 24, 25, 27, 31, 32, 33)),
            2: self._group(1994, (3,)),
            3: self._group(1995, (5, 6, 7, 8, 9, 10)),
            4: self._group(1996, (11, 12, 13, 14, 15, 16)),
            5: self._group(1998, (28, 29, 30)),
        }
        candidates = [
            self._candidate(47952, 1997, 58, 'Voordewindkracht 10'),
            self._candidate(19793, 1994, 33, 'Penthouse Comics'),
            self._candidate(39546, 1994, 108, 'Blue Sky'),
        ]
        original_matches = {
            1: {'id': None, 'review_reason': 'weak-score'},
            2: {'id': None, 'review_reason': 'tie'},
            3: {'id': None, 'review_reason': 'weak-score'},
            4: {
                'id': 47952,
                'title': 'Penthouse Comix (1997)',
                'issue_count': 58,
                'link': 'https://example.test/4050-47952/',
            },
            5: {'id': None, 'review_reason': 'weak-score'},
        }

        matches = apply_series_run_context(
            groups,
            original_matches,
            {'penthouse comix': candidates},
            only_english=True,
        )

        self.assertEqual({match['id'] for match in matches.values()}, {19793})
        self.assertTrue(all(match['series_context'] for match in matches.values()))
        self.assertEqual(matches[1]['issue_count'], 33)

    def test_two_same_title_runs_with_restarted_issue_numbers_are_not_fused(self):
        groups = {
            1: self._group(1994, (1, 2, 3)),
            2: self._group(1997, (1, 2, 3)),
        }
        original_matches = {
            1: {'id': None, 'review_reason': 'tie'},
            2: {'id': None, 'review_reason': 'tie'},
        }

        matches = apply_series_run_context(
            groups,
            original_matches,
            {
                'penthouse comix': [
                    self._candidate(19793, 1994, 33, 'Penthouse Comics'),
                    self._candidate(47952, 1997, 58, 'Voordewindkracht 10'),
                ]
            },
            only_english=True,
        )

        self.assertEqual(matches, original_matches)

    def test_missing_issue_numbers_disable_unattended_run_context(self):
        groups = {
            1: self._group(1994, (1, 2)),
            2: self._group(1995, (3,)),
        }
        first_file = next(iter(groups[2].values()))
        first_file['issue_number'] = None
        original_matches = {
            1: {'id': None, 'review_reason': 'weak-score'},
            2: {'id': None, 'review_reason': 'weak-score'},
        }

        matches = apply_series_run_context(
            groups,
            original_matches,
            {'penthouse comix': [self._candidate(19793, 1994, 33, 'Penthouse Comics')]},
            only_english=True,
        )

        self.assertEqual(matches, original_matches)

    def test_whole_run_must_still_pass_normal_continuous_confidence(self):
        groups = {
            1: self._group(1994, (1, 2)),
            2: self._group(1995, (3,)),
        }
        original_matches = {
            1: {'id': None, 'review_reason': 'weak-score'},
            2: {'id': None, 'review_reason': 'weak-score'},
        }

        # Both candidates have the same title/year/volume and neither has the
        # exact three-issue run size, so the combined evidence remains tied.
        matches = apply_series_run_context(
            groups,
            original_matches,
            {
                'penthouse comix': [
                    self._candidate(100, 1994, 10, 'A'),
                    self._candidate(200, 1994, 20, 'B'),
                ]
            },
            only_english=True,
        )

        self.assertEqual(matches, original_matches)


if __name__ == '__main__':
    unittest.main()
