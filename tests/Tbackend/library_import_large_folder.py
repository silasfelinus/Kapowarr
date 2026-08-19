import unittest
from unittest.mock import AsyncMock, Mock, patch

from backend.features.library_import_persistent import (
    LARGE_FOLDER_SHARED_SEARCH_MIN_TITLES,
    PersistentContinuousLibraryImport,
)


class continuous_import_large_folder(unittest.TestCase):
    @staticmethod
    def _file_data(series: str, issue_number: float):
        return {
            'series': series,
            'year': None,
            'volume_number': 1,
            'special_version': None,
            'issue_number': issue_number,
            'annual': False
        }

    @staticmethod
    def _volume(comicvine_id: int, title: str):
        return {
            'comicvine_id': comicvine_id,
            'title': title,
            'year': 1995,
            'volume_number': 1,
            'cover_link': '',
            'cover': None,
            'description': '',
            'site_url': f'https://comicvine.example/{comicvine_id}',
            'aliases': [],
            'publisher': 'Warp Graphics',
            'issue_count': 50,
            'translated': False,
            'already_added': None,
            'issues': None
        }

    def test_large_folder_reuses_one_broad_search_when_pool_resolves_titles(self):
        importer = PersistentContinuousLibraryImport()
        groups = {}
        broad_results = []
        for idx in range(1, LARGE_FOLDER_SHARED_SEARCH_MIN_TITLES + 1):
            title = f'ElfQuest Arc {idx}'
            groups[idx] = {
                f'/content/ElfQuest/{idx:03d}.) {title} #{idx}.cbr':
                    self._file_data(title, float(idx))
            }
            broad_results.append(self._volume(idx, title))

        provider = Mock()
        provider.search_volumes = AsyncMock(return_value=broad_results)

        # Persistent import owns the broad search, while the shared matcher
        # constructs its provider through backend.features.library_import even
        # when every title is already present in its temporary cache. Stub both
        # call sites so this unit test stays independent of Flask/Settings.
        with patch(
            'backend.features.library_import_persistent.get_metadata_provider',
            return_value=provider
        ), patch(
            'backend.features.library_import.get_metadata_provider',
            return_value=provider
        ):
            result = importer._match_search_groups(groups, '/content/ElfQuest')

        self.assertIsNotNone(result)
        matches, context_cache = result
        self.assertEqual(len(matches), len(groups))
        self.assertTrue(all(match['id'] is not None for match in matches.values()))
        self.assertEqual(set(context_cache), {
            f'elfquest arc {idx}'
            for idx in range(1, LARGE_FOLDER_SHARED_SEARCH_MIN_TITLES + 1)
        })
        provider.search_volumes.assert_awaited_once_with('elfquest')
        # Broad results are retained only under the query that actually produced
        # them; unrelated exact-title cache keys are folder-local.
        self.assertEqual(set(importer.search_cache), {'elfquest'})


if __name__ == '__main__':
    unittest.main()
