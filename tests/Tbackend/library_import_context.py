import unittest
from unittest.mock import patch

from backend.features.library_import_context import apply_series_run_context
from backend.features.library_import_persistent import PersistentContinuousLibraryImport


class series_run_import_context(unittest.TestCase):
    @staticmethod
    def _file_data(year, issue_number, annual=False):
        return {
            'series': 'Penthouse Comix',
            'year': year,
            'volume_number': 1,
            'special_version': None,
            'issue_number': float(issue_number) if issue_number is not None else None,
            'annual': annual,
        }

    @classmethod
    def _group(cls, year, issues, annual=False):
        return {
            f'/library/Penthouse Comix/Penthouse Comix {issue:03d}.cbr':
                cls._file_data(year, issue, annual=annual)
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
        # exact three-issue run boundary, so the combined evidence remains tied.
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

    def test_annuals_in_same_folder_never_use_regular_run_context(self):
        groups = {
            1: self._group(1994, (1, 2, 3)),
            2: self._group(1995, (1, 2, 3), annual=True),
            3: self._group(1996, (4, 5, 6)),
        }
        original_matches = {
            1: {'id': None, 'review_reason': 'weak-score'},
            2: {'id': 999, 'title': 'Penthouse Comix Annual', 'issue_count': 3, 'link': None},
            3: {'id': None, 'review_reason': 'weak-score'},
        }

        matches = apply_series_run_context(
            groups,
            original_matches,
            {'penthouse comix': [self._candidate(19793, 1994, 6, 'Penthouse Comics')]},
            only_english=True,
        )

        self.assertEqual(matches[2], original_matches[2])


class continuous_import_folder_scope(unittest.TestCase):
    def test_parent_folder_processing_excludes_child_volume_files(self):
        parent = '/library/Series'
        parent_file = f'{parent}/Series 001.cbz'
        child_file = f'{parent}/Volume 2/Series 001.cbz'
        parent_data = series_run_import_context._file_data(1994, 1)
        child_data = series_run_import_context._file_data(1997, 1)

        with patch(
            'backend.features.library_import_persistent._collect_unimported_files',
            return_value=(
                {parent_file: parent_data, child_file: child_data},
                {parent_file: parent, child_file: f'{parent}/Volume 2'},
            ),
        ):
            files = PersistentContinuousLibraryImport._load_folder_files(parent)

        self.assertEqual(list(files), [parent_file])


if __name__ == '__main__':
    unittest.main()
