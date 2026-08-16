import unittest
from unittest.mock import AsyncMock, patch

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

    async def test_shared_clock_waits_only_remaining_interval(self):
        clock = {'last_started': 100.0}

        with patch(
            'backend.features.library_import.monotonic',
            side_effect=[112.0, 120.0]
        ), patch(
            'backend.features.library_import.async_sleep',
            new_callable=AsyncMock
        ) as sleep_mock, patch(
            'backend.features.library_import.ComicVine.search_volumes',
            new_callable=AsyncMock,
            return_value=[]
        ) as search_mock:
            await _match_file_groups(
                self._group(),
                only_english=True,
                request_delay=20.0,
                search_cache={},
                request_clock=clock
            )

        sleep_mock.assert_awaited_once_with(8.0)
        search_mock.assert_awaited_once_with('batman')
        self.assertEqual(clock['last_started'], 120.0)

    async def test_first_search_does_not_sleep(self):
        clock = {}

        with patch(
            'backend.features.library_import.monotonic',
            return_value=50.0
        ), patch(
            'backend.features.library_import.async_sleep',
            new_callable=AsyncMock
        ) as sleep_mock, patch(
            'backend.features.library_import.ComicVine.search_volumes',
            new_callable=AsyncMock,
            return_value=[]
        ):
            await _match_file_groups(
                self._group(),
                only_english=True,
                request_delay=20.0,
                search_cache={},
                request_clock=clock
            )

        sleep_mock.assert_not_awaited()
        self.assertEqual(clock['last_started'], 50.0)


if __name__ == '__main__':
    unittest.main()
