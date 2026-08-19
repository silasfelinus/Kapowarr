import unittest

from backend.features.library_import import create_groups
from backend.features.library_import_normalization import (
    folder_search_query,
    normalize_import_filename_data,
    normalize_import_series_name,
)


class library_import_normalization(unittest.TestCase):
    def test_ordered_shelf_prefix_is_removed(self):
        self.assertEqual(
            normalize_import_series_name('001.) ElfQuest Hidden Years'),
            'ElfQuest Hidden Years'
        )
        self.assertEqual(
            normalize_import_series_name('42) ElfQuest New Blood'),
            'ElfQuest New Blood'
        )

    def test_numeric_title_without_order_marker_is_preserved(self):
        self.assertEqual(
            normalize_import_series_name('100 Bullets'),
            '100 Bullets'
        )

    def test_ordered_files_collapse_into_one_series_group(self):
        files = normalize_import_filename_data({
            '/content/ElfQuest/001.) Hidden Years 006.cbr': {
                'series': '001.) ElfQuest Hidden Years',
                'year': None,
                'volume_number': 1,
                'special_version': None,
                'issue_number': 6.0,
                'annual': False
            },
            '/content/ElfQuest/002.) Hidden Years 007.cbr': {
                'series': '002.) ElfQuest Hidden Years',
                'year': None,
                'volume_number': 1,
                'special_version': None,
                'issue_number': 7.0,
                'annual': False
            }
        })

        groups = create_groups(files)
        self.assertEqual(len(groups), 1)
        self.assertEqual(len(groups[1]), 2)

    def test_folder_search_query_strips_trailing_year_only(self):
        self.assertEqual(
            folder_search_query('/content/Alien Paradiso (2025)'),
            'alien paradiso'
        )
        self.assertEqual(
            folder_search_query('/content/100 Bullets'),
            '100 bullets'
        )


if __name__ == '__main__':
    unittest.main()
