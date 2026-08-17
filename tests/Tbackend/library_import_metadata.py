import json
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from backend.features import library_import_state as state
from backend.features.library_import_metadata import (
    filter_library_import_files,
    is_library_import_artifact,
    load_local_series_metadata,
    select_local_series_metadata,
)
from backend.features.library_import_persistent import (
    PersistentContinuousLibraryImport,
)


class library_import_local_metadata(unittest.TestCase):
    @staticmethod
    def _file_data(series, year=None, volume_number=1, issue_number=1.0):
        return {
            'series': series,
            'year': year,
            'volume_number': volume_number,
            'special_version': None,
            'issue_number': issue_number,
            'annual': False,
        }

    @staticmethod
    def _write_series_json(folder, **metadata):
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, 'series.json'), 'w', encoding='utf-8') as handle:
            json.dump({'metadata': metadata}, handle)

    def test_mylar_series_json_reads_exact_comicvine_identity(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            self._write_series_json(
                temp_dir,
                name='Aquaman',
                comicid=43022,
                year=2011,
                volume=5,
                total_issues=55,
            )
            metadata = load_local_series_metadata(temp_dir)

        self.assertIsNotNone(metadata)
        self.assertEqual(metadata['comicvine_id'], 43022)
        self.assertEqual(metadata['name'], 'Aquaman')
        self.assertEqual(metadata['year'], 2011)
        self.assertEqual(metadata['volume_number'], 5)
        self.assertEqual(metadata['issue_count'], 55)
        self.assertEqual(metadata['source'], 'series.json')

    def test_comicvine_resource_reference_is_accepted(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            self._write_series_json(
                temp_dir,
                name='Batman',
                comicid='4050-135499',
                year='2021',
            )
            metadata = load_local_series_metadata(temp_dir)

        self.assertEqual(metadata['comicvine_id'], 135499)
        self.assertEqual(metadata['year'], 2021)

    def test_named_group_must_agree_with_sidecar(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            self._write_series_json(
                temp_dir,
                name='Avengers',
                comicid=1234,
                year=2018,
            )
            avengers = {
                os.path.join(temp_dir, 'Avengers 001.cbz'):
                    self._file_data('Avengers', 2018)
            }
            academy = {
                os.path.join(temp_dir, 'Avengers Academy 001.cbz'):
                    self._file_data('Avengers Academy', 2010)
            }

            self.assertEqual(
                select_local_series_metadata(temp_dir, avengers)['comicvine_id'],
                1234,
            )
            self.assertIsNone(select_local_series_metadata(temp_dir, academy))

    def test_numeric_filename_can_use_same_folder_identity(self):
        with tempfile.TemporaryDirectory() as root:
            folder = os.path.join(root, 'National Lampoon')
            self._write_series_json(
                folder,
                name='National Lampoon',
                comicid=5678,
                year=1970,
            )
            group = {
                os.path.join(folder, '1970_04.pdf'):
                    self._file_data('1970', 1970, issue_number=4.0)
            }

            metadata = select_local_series_metadata(folder, group)

        self.assertIsNotNone(metadata)
        self.assertEqual(metadata['comicvine_id'], 5678)

    def test_numeric_filename_rejects_sidecar_when_folder_year_conflicts(self):
        with tempfile.TemporaryDirectory() as root:
            folder = os.path.join(root, 'Some Series (1999)')
            self._write_series_json(
                folder,
                name='Some Series',
                comicid=9012,
                year=2005,
            )
            group = {
                os.path.join(folder, '001.pdf'):
                    self._file_data('001', 1999, issue_number=1.0)
            }

            self.assertIsNone(select_local_series_metadata(folder, group))

    def test_malformed_or_unknown_json_is_ignored(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with open(os.path.join(temp_dir, 'series.json'), 'w', encoding='utf-8') as handle:
                handle.write('{ nope')
            with open(os.path.join(temp_dir, 'metadata.json'), 'w', encoding='utf-8') as handle:
                json.dump({'title': 'Batman'}, handle)

            self.assertIsNone(load_local_series_metadata(temp_dir))

    def test_cover_folder_and_hidden_cache_are_artifacts(self):
        self.assertTrue(is_library_import_artifact('/library/Batman/cover.jpg'))
        self.assertTrue(is_library_import_artifact('/library/Batman/folder.JPG'))
        self.assertTrue(is_library_import_artifact('/library/.yacreaderlibrary/covers'))
        self.assertFalse(is_library_import_artifact('/library/Batman/001.jpg'))
        self.assertFalse(is_library_import_artifact('/library/Batman/Batman 001.cbz'))

    def test_filter_keeps_page_images_but_drops_cover_art(self):
        files = {
            '/library/Batman/cover.jpg': self._file_data('Batman'),
            '/library/Batman/folder.jpg': self._file_data('Batman'),
            '/library/Batman/001.jpg': self._file_data('Batman'),
        }
        filtered = filter_library_import_files(files)
        self.assertEqual(list(filtered), ['/library/Batman/001.jpg'])

    def test_persistent_matcher_skips_search_for_safe_local_identity(self):
        with tempfile.TemporaryDirectory() as root:
            folder = os.path.join(root, 'Avengers (2018)')
            self._write_series_json(
                folder,
                name='Avengers',
                comicid=777,
                year=2018,
                total_issues=50,
            )
            group_to_files = {
                1: {
                    os.path.join(folder, 'Avengers 001.cbz'):
                        self._file_data('Avengers', 2018)
                }
            }

            local, search = (
                PersistentContinuousLibraryImport._match_groups_with_local_metadata(
                    folder,
                    group_to_files,
                )
            )

        self.assertEqual(search, {})
        self.assertEqual(local[1]['id'], 777)
        self.assertEqual(local[1]['issue_count'], 50)
        self.assertEqual(local[1]['local_metadata'], 'series.json')


class library_import_review_artifact_pruning(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(':memory:')
        self.connection.row_factory = sqlite3.Row
        self.connection.execute(
            'CREATE TABLE files(id INTEGER PRIMARY KEY, filepath TEXT UNIQUE);'
        )
        self.get_db_patch = patch.object(
            state,
            'get_db',
            side_effect=lambda *args, **kwargs: self.connection.cursor(),
        )
        self.commit_patch = patch.object(
            state,
            'commit',
            side_effect=self.connection.commit,
        )
        self.get_db_patch.start()
        self.commit_patch.start()

    def tearDown(self):
        self.commit_patch.stop()
        self.get_db_patch.stop()
        self.connection.close()

    def test_existing_cover_only_review_hold_is_pruned_to_done(self):
        job_id = state.create_job(('/library/Batman',))
        state.mark_folder_processing(job_id, '/library/Batman')
        state.mark_folder_result(
            job_id,
            '/library/Batman',
            imported_volumes=0,
            review_reason='weak-score',
            review_items=[
                {'filepath': '/library/Batman/cover.jpg'},
                {'filepath': '/library/Batman/folder.jpg'},
            ],
        )

        details = state.get_job_details(job_id)

        self.assertEqual(details['review_items'], [])
        self.assertEqual(details['review_folders'], 0)
        self.assertEqual(details['checked_folders'], 1)

    def test_mixed_review_hold_prunes_artwork_but_keeps_comic(self):
        job_id = state.create_job(('/library/Batman',))
        state.mark_folder_processing(job_id, '/library/Batman')
        state.mark_folder_result(
            job_id,
            '/library/Batman',
            imported_volumes=0,
            review_reason='tie',
            review_items=[
                {'filepath': '/library/Batman/cover.jpg'},
                {'filepath': '/library/Batman/Batman 001.cbz'},
            ],
        )

        details = state.get_job_details(job_id)

        self.assertEqual(
            [item['filepath'] for item in details['review_items']],
            ['/library/Batman/Batman 001.cbz'],
        )
        self.assertEqual(details['review_folders'], 1)


if __name__ == '__main__':
    unittest.main()
