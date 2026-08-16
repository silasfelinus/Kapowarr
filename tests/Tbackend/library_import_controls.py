import unittest
from unittest.mock import Mock, patch

from backend.features.library_import import (
    ContinuousLibraryImport,
    _match_file_groups,
)
from backend.features.library_import_policy import REVIEW_REASON_TIE
from backend.features.tasks import TaskHandler


class continuous_import_review_snapshot(unittest.IsolatedAsyncioTestCase):
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

    async def test_held_match_keeps_best_guess_without_another_search(self):
        candidate = {
            'comicvine_id': 123,
            'title': 'Batman',
            'year': 2020,
            'issue_count': 12,
            'site_url': 'https://example.test/volume/123'
        }
        comicvine = Mock()
        comicvine.search_volumes = Mock()

        with patch(
            'backend.features.library_import.ComicVine',
            return_value=comicvine
        ), patch(
            'backend.features.library_import.select_auto_import_volume_result',
            return_value=(None, REVIEW_REASON_TIE)
        ), patch(
            'backend.features.library_import.select_best_volume_result_for_file',
            return_value=candidate
        ):
            matches = await _match_file_groups(
                self._group(),
                only_english=True,
                search_cache={'batman': [candidate]},
                require_confident_match=True
            )

        self.assertIsNone(matches[1]['id'])
        self.assertEqual(matches[1]['review_reason'], REVIEW_REASON_TIE)
        self.assertEqual(matches[1]['review_candidate'], {
            'id': 123,
            'title': 'Batman (2020)',
            'issue_count': 12,
            'link': 'https://example.test/volume/123'
        })
        comicvine.search_volumes.assert_not_called()

    def test_review_rows_have_unique_group_ids_and_stop_is_cooperative(self):
        task = ContinuousLibraryImport()
        task._add_review_group(
            '/library/Batman',
            self._group()[1],
            {
                'id': None,
                'review_reason': REVIEW_REASON_TIE,
                'review_candidate': {
                    'id': 123,
                    'title': 'Batman (2020)',
                    'issue_count': 12,
                    'link': 'https://example.test/volume/123'
                }
            }
        )
        task._add_review_group(
            '/library/Batman Again',
            self._group()[1],
            {
                'id': None,
                'review_reason': REVIEW_REASON_TIE,
                'review_candidate': None
            }
        )

        details = task.get_task_details()
        self.assertEqual(len(details['review_items']), 2)
        self.assertNotEqual(
            details['review_items'][0]['group_number'],
            details['review_items'][1]['group_number']
        )
        self.assertFalse(task.stop)
        self.assertFalse(details['stop_requested'])

        task.request_stop()
        self.assertTrue(task.stop_requested)
        self.assertFalse(task.stop)


class task_handler_cooperative_stop(unittest.TestCase):
    def test_running_task_can_opt_into_request_stop(self):
        handler = object.__new__(TaskHandler)
        task = Mock()
        task.display_title = 'Continuous Library Import'
        task.request_stop = Mock()
        entry = {
            'task': task,
            'id': 7,
            'status': 'running',
            'thread': Mock()
        }
        handler.queue = [entry]

        handler.remove(7)

        task.request_stop.assert_called_once_with()
        self.assertEqual(handler.queue, [entry])


if __name__ == '__main__':
    unittest.main()
