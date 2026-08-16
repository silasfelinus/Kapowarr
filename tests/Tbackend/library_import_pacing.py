import unittest
from unittest.mock import AsyncMock, Mock, patch

from backend.features.library_import import _match_file_groups


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
            'backend.features.library_import.ComicVine',
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
            'backend.features.library_import.ComicVine',
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


if __name__ == '__main__':
    unittest.main()
