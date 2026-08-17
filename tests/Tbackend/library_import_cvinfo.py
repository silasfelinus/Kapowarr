import json
import os
import tempfile
import unittest

from backend.features.library_import_metadata import (
    load_local_series_metadata,
    select_local_series_metadata,
)
from backend.features.library_import_persistent import (
    PersistentContinuousLibraryImport,
)


class mylar_cvinfo_import(unittest.TestCase):
    @staticmethod
    def _file_data(series, year=None, issue_number=1.0):
        return {
            'series': series,
            'year': year,
            'volume_number': 1,
            'special_version': None,
            'issue_number': issue_number,
            'annual': False,
        }

    @staticmethod
    def _write_cvinfo(folder, value):
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, 'cvinfo'), 'w', encoding='utf-8') as handle:
            handle.write(value)

    def test_mylar_cvinfo_url_reads_volume_id_and_folder_identity(self):
        with tempfile.TemporaryDirectory() as root:
            folder = os.path.join(root, 'Penthouse Comix (1994)')
            self._write_cvinfo(
                folder,
                'https://comicvine.gamespot.com/penthouse-comix/4050-19793/',
            )

            metadata = load_local_series_metadata(folder)

        self.assertIsNotNone(metadata)
        self.assertEqual(metadata['comicvine_id'], 19793)
        self.assertEqual(metadata['name'], 'Penthouse Comix')
        self.assertEqual(metadata['year'], 1994)
        self.assertEqual(metadata['source'], 'cvinfo')

    def test_cvinfo_accepts_full_resource_or_bare_numeric_id(self):
        for value in ('4050-19793', '19793'):
            with self.subTest(value=value), tempfile.TemporaryDirectory() as root:
                folder = os.path.join(root, 'Penthouse Comix (1994)')
                self._write_cvinfo(folder, value)
                metadata = load_local_series_metadata(folder)
                self.assertIsNotNone(metadata)
                self.assertEqual(metadata['comicvine_id'], 19793)

    def test_cvinfo_rejects_issue_and_story_arc_ids(self):
        for value in (
            'https://comicvine.gamespot.com/example/4000-12345/',
            'https://comicvine.gamespot.com/example/4045-12345/',
        ):
            with self.subTest(value=value), tempfile.TemporaryDirectory() as root:
                folder = os.path.join(root, 'Some Series (2020)')
                self._write_cvinfo(folder, value)
                self.assertIsNone(load_local_series_metadata(folder))

    def test_publication_year_does_not_override_exact_cvinfo_volume(self):
        with tempfile.TemporaryDirectory() as root:
            folder = os.path.join(root, 'Penthouse Comix (1994)')
            self._write_cvinfo(
                folder,
                'https://comicvine.gamespot.com/penthouse-comix/4050-19793/',
            )
            group_1998 = {
                os.path.join(folder, 'Penthouse Comix 030 (1998).cbr'):
                    self._file_data('Penthouse Comix', 1998, 30.0)
            }

            metadata = select_local_series_metadata(folder, group_1998)

        self.assertIsNotNone(metadata)
        self.assertEqual(metadata['comicvine_id'], 19793)

    def test_cvinfo_is_not_inherited_by_different_series_in_organizer_folder(self):
        with tempfile.TemporaryDirectory() as root:
            folder = os.path.join(root, 'Avengers (2018)')
            self._write_cvinfo(folder, '4050-1234')
            avengers = {
                os.path.join(folder, 'Avengers 001.cbz'):
                    self._file_data('Avengers', 2018)
            }
            academy = {
                os.path.join(folder, 'Avengers Academy 001.cbz'):
                    self._file_data('Avengers Academy', 2010)
            }

            self.assertEqual(
                select_local_series_metadata(folder, avengers)['comicvine_id'],
                1234,
            )
            self.assertIsNone(select_local_series_metadata(folder, academy))

    def test_conflicting_json_and_cvinfo_are_not_auto_trusted(self):
        with tempfile.TemporaryDirectory() as root:
            folder = os.path.join(root, 'Batman (2011)')
            self._write_cvinfo(folder, '4050-111')
            with open(
                os.path.join(folder, 'series.json'),
                'w',
                encoding='utf-8',
            ) as handle:
                json.dump({
                    'metadata': {
                        'name': 'Batman',
                        'comicid': 222,
                        'year': 2011,
                    }
                }, handle)

            self.assertIsNone(load_local_series_metadata(folder))

    def test_persistent_import_skips_search_when_cvinfo_matches_folder(self):
        with tempfile.TemporaryDirectory() as root:
            folder = os.path.join(root, 'Penthouse Comix (1994)')
            self._write_cvinfo(folder, '4050-19793')
            group_to_files = {
                1: {
                    os.path.join(folder, 'Penthouse Comix 001 (1994).cbr'):
                        self._file_data('Penthouse Comix', 1994, 1.0)
                },
                2: {
                    os.path.join(folder, 'Penthouse Comix 030 (1998).cbr'):
                        self._file_data('Penthouse Comix', 1998, 30.0)
                },
            }

            local, search = (
                PersistentContinuousLibraryImport._match_groups_with_local_metadata(
                    folder,
                    group_to_files,
                )
            )

        self.assertEqual(search, {})
        self.assertEqual(local[1]['id'], 19793)
        self.assertEqual(local[2]['id'], 19793)
        self.assertEqual(local[1]['local_metadata'], 'cvinfo')


if __name__ == '__main__':
    unittest.main()
