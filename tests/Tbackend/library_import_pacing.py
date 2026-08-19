import unittest
from unittest.mock import AsyncMock, Mock, call, patch

from backend.features.library_import import _match_file_groups
from backend.features.library_import_persistent import (
    CONTINUOUS_IMPORT_CV_RESOURCE_DELAY,
    PersistentContinuousLibraryImport,
)


class continuous_import_pacing(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _group():
        return {
            1: {
                'Batman 001.cbz': {
                    'series': 'Batman',
                    'year': 2020,
                    'volume_number': 2,
                    'special_version': None,
                    'issue_number': 1.0,
                    'annual': False
                }
            }
        }

    @staticmethod
    def _comicvine_stub():
        comicvine = Mock()
        comicvine.search_volumes = AsyncMock(return_value=[])
        return comicvine

    async def test_shared_clock_waits_only_remaining_interval(self):
        clock = {'last_started': 100.0}
        comicvine = self._comicvine_stub()

        with patch(
            'backend.features.library_import.monotonic',
            side_effect=[112.0, 120.0]
        ), patch(
            'backend.features.library_import.async_sleep',
            new_callable=AsyncMock
        ) as sleep_mock, patch(
            'backend.features.library_import.get_metadata_provider',
            return_value=comicvine
        ):
            await _match_file_groups(
                self._group(),
                only_english=True,
                request_delay=20.0,
                search_cache={},
                request_clock=clock
            )

        sleep_mock.assert_awaited_once_with(8.0)
        comicvine.search_volumes.assert_awaited_once_with('batman')
        self.assertEqual(clock['last_started'], 120.0)

    async def test_first_search_does_not_sleep(self):
        clock = {}
        comicvine = self._comicvine_stub()

        with patch(
            'backend.features.library_import.monotonic',
            return_value=50.0
        ), patch(
            'backend.features.library_import.async_sleep',
            new_callable=AsyncMock
        ) as sleep_mock, patch(
            'backend.features.library_import.get_metadata_provider',
            return_value=comicvine
        ):
            await _match_file_groups(
                self._group(),
                only_english=True,
                request_delay=20.0,
                search_cache={},
                request_clock=clock
            )

        sleep_mock.assert_not_awaited()
        comicvine.search_volumes.assert_awaited_once_with('batman')
        self.assertEqual(clock['last_started'], 50.0)

    def test_metadata_fetch_clock_paces_cvinfo_fast_path_in_short_slices(self):
        importer = PersistentContinuousLibraryImport()
        importer.cv_request_clock['last_metadata_started'] = 100.0

        with patch(
            'backend.features.library_import_persistent.monotonic',
            side_effect=[112.0, 130.0]
        ), patch(
            'backend.features.library_import_persistent.sleep'
        ) as sleep_mock:
            allowed = importer._wait_for_metadata_slot()

        self.assertTrue(allowed)
        expected_delay = CONTINUOUS_IMPORT_CV_RESOURCE_DELAY - 12.0
        self.assertEqual(sleep_mock.call_count, int(expected_delay))
        self.assertEqual(
            sleep_mock.call_args_list,
            [call(1.0)] * int(expected_delay)
        )
        self.assertEqual(
            importer.cv_request_clock['last_metadata_started'],
            130.0
        )

    def test_metadata_fetch_clock_does_not_wait_before_first_add(self):
        importer = PersistentContinuousLibraryImport()

        with patch(
            'backend.features.library_import_persistent.monotonic',
            return_value=50.0
        ), patch(
            'backend.features.library_import_persistent.sleep'
        ) as sleep_mock:
            allowed = importer._wait_for_metadata_slot()

        self.assertTrue(allowed)
        sleep_mock.assert_not_called()
        self.assertEqual(
            importer.cv_request_clock['last_metadata_started'],
            50.0
        )

    def test_stop_requested_during_metadata_wait_is_seen_within_one_slice(self):
        importer = PersistentContinuousLibraryImport()
        importer.cv_request_clock['last_metadata_started'] = 100.0

        def request_stop(_seconds):
            importer.stop_requested = True

        with patch(
            'backend.features.library_import_persistent.monotonic',
            return_value=112.0
        ), patch(
            'backend.features.library_import_persistent.sleep',
            side_effect=request_stop
        ) as sleep_mock:
            allowed = importer._wait_for_metadata_slot()

        self.assertFalse(allowed)
        sleep_mock.assert_called_once_with(1.0)
        self.assertEqual(
            importer.cv_request_clock['last_metadata_started'],
            100.0
        )

    def test_stop_requested_before_metadata_wait_never_sleeps_or_starts_request(self):
        importer = PersistentContinuousLibraryImport()
        importer.cv_request_clock['last_metadata_started'] = 100.0
        importer.stop_requested = True

        with patch(
            'backend.features.library_import_persistent.monotonic',
            return_value=112.0
        ), patch(
            'backend.features.library_import_persistent.sleep'
        ) as sleep_mock:
            allowed = importer._wait_for_metadata_slot()

        self.assertFalse(allowed)
        sleep_mock.assert_not_called()
        self.assertEqual(
            importer.cv_request_clock['last_metadata_started'],
            100.0
        )

    def test_stop_during_search_pacing_does_not_start_another_provider_search(self):
        importer = PersistentContinuousLibraryImport()
        importer.cv_request_clock['last_started'] = 100.0

        def request_stop(_seconds):
            importer.stop_requested = True

        with patch(
            'backend.features.library_import_persistent.monotonic',
            return_value=112.0
        ), patch(
            'backend.features.library_import_persistent.sleep',
            side_effect=request_stop
        ) as sleep_mock, patch(
            'backend.features.library_import_persistent._match_file_groups',
            new_callable=AsyncMock
        ) as match_mock:
            result = importer._match_search_groups(self._group())

        self.assertIsNone(result)
        sleep_mock.assert_called_once_with(1.0)
        match_mock.assert_not_awaited()
        self.assertEqual(
            importer.cv_request_clock['last_started'],
            100.0
        )

    def test_cached_search_title_skips_pacing_but_still_honors_stop(self):
        importer = PersistentContinuousLibraryImport()
        importer.search_cache['batman'] = []
        importer.stop_requested = True

        with patch(
            'backend.features.library_import_persistent.sleep'
        ) as sleep_mock, patch(
            'backend.features.library_import_persistent._match_file_groups',
            new_callable=AsyncMock
        ) as match_mock:
            result = importer._match_search_groups(self._group())

        self.assertIsNone(result)
        sleep_mock.assert_not_called()
        match_mock.assert_not_awaited()


if __name__ == '__main__':
    unittest.main()
