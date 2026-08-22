import threading
import time
import unittest
from unittest.mock import MagicMock, patch

from backend.features import tasks as tasks_module
from backend.features.tasks import Task, TaskHandler
from backend.implementations import indexers as indexers_module
from backend.implementations.indexers import Indexer, Indexers, search_indexer


class _FakeAsyncSession:
    def __init__(self, body):
        self.body = body
        self.calls = []

    async def get_text(self, url, params={}, headers={}, quiet_fail=False):
        self.calls.append((url, params, quiet_fail))
        return self.body


def _indexer(base_url='https://prowlarr.example/39/api'):
    result = Indexer.__new__(Indexer)
    result._id = 39
    result._title = 'NZB Geek'
    result._base_url = base_url
    result._api_key = 'secret'
    result._enabled = True
    return result


class newznab_xml_compatibility(unittest.IsolatedAsyncioTestCase):
    async def test_parses_canonical_rss_xml_results(self):
        body = '''<?xml version="1.0" encoding="UTF-8"?>
        <rss version="2.0"><channel><item>
          <title>Batman (2020) 001</title>
          <link>https://prowlarr.example/39/download/abc</link>
          <enclosure url="https://prowlarr.example/39/download/abc" type="application/x-nzb" />
        </item></channel></rss>'''
        results = await search_indexer(_FakeAsyncSession(body), _indexer(), 'Batman')
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['display_title'], 'Batman (2020) 001')
        self.assertEqual(results[0]['issue_number'], 1.0)
        self.assertEqual(
            results[0]['link'], 'https://prowlarr.example/39/download/abc'
        )

    async def test_xml_error_is_not_misreported_as_non_json(self):
        body = '<error code="100" description="Incorrect API key" />'
        with self.assertLogs('Kapowarr', level='WARNING') as logs:
            results = await search_indexer(
                _FakeAsyncSession(body), _indexer(), 'Batman'
            )
        self.assertEqual(results, [])
        self.assertTrue(any('Incorrect API key' in line for line in logs.output))
        self.assertFalse(any('non-JSON' in line for line in logs.output))


class prowlarr_download_link_ownership(unittest.TestCase):
    def test_legacy_feed_owns_sibling_download_url(self):
        indexer = _indexer('https://prowlarr.example/39/api')
        with patch.object(Indexers, 'get_enabled', return_value=[indexer]):
            found = Indexers.find_by_link(
                'https://prowlarr.example/39/download/abc'
            )
        self.assertIs(found, indexer)

    def test_modern_feed_accepts_host_level_download_url(self):
        indexer = _indexer(
            'https://prowlarr.example/api/v1/indexer/39/newznab'
        )
        with patch.object(Indexers, 'get_enabled', return_value=[indexer]):
            found = Indexers.find_by_link(
                'https://prowlarr.example/download/abc'
            )
        self.assertIs(found, indexer)


class _LaneTask(Task):
    stop = False
    message = ''
    category = ''
    display_title = 'Lane task'

    @property
    def volume_id(self):
        return None

    @property
    def issue_id(self):
        return None

    def __init__(self, action, started, release, finished):
        self.action = action
        self.started = started
        self.release = release
        self.finished = finished
        self.stop = False

    def run(self):
        self.started.set()
        self.release.wait(timeout=3)
        self.finished.set()


class task_handler_lanes(unittest.TestCase):
    def setUp(self):
        TaskHandler.queue.clear()
        self.handler = TaskHandler()

    def tearDown(self):
        for entry in list(TaskHandler.queue):
            task = entry['task']
            if hasattr(task, 'release'):
                task.release.set()
        deadline = time.time() + 3
        while TaskHandler.queue and time.time() < deadline:
            time.sleep(0.01)
        TaskHandler.queue.clear()

    def test_continuous_import_does_not_block_default_lane(self):
        import_started = threading.Event()
        import_release = threading.Event()
        import_finished = threading.Event()
        default_started = threading.Event()
        default_release = threading.Event()
        default_release.set()
        default_finished = threading.Event()

        importer = _LaneTask(
            'continuous_library_import', import_started,
            import_release, import_finished
        )
        normal = _LaneTask(
            'auto_search_issue', default_started,
            default_release, default_finished
        )

        fake_cursor = MagicMock()
        fake_cursor.execute.return_value = fake_cursor
        with patch.object(tasks_module, 'get_db', return_value=fake_cursor), \
             patch.object(tasks_module, 'commit'), \
             patch.object(tasks_module, 'WebSocket', return_value=MagicMock()):
            import_id = self.handler.add(importer)
            self.assertTrue(import_started.wait(1))
            normal_id = self.handler.add(normal)
            self.assertTrue(default_finished.wait(1))

            queue = self.handler.get_all()
            importer_row = next(row for row in queue if row['id'] == import_id)
            self.assertEqual(importer_row['status'], 'running')
            self.assertEqual(importer_row['queue_lane'], 'continuous_import')
            self.assertNotEqual(import_id, normal_id)

            import_release.set()
            self.assertTrue(import_finished.wait(1))

    def test_duplicate_interval_style_task_reuses_existing_queue_entry(self):
        started = threading.Event()
        release = threading.Event()
        finished = threading.Event()
        first = _LaneTask('watched_folder_import', started, release, finished)
        second = _LaneTask(
            'watched_folder_import', threading.Event(),
            threading.Event(), threading.Event()
        )
        fake_cursor = MagicMock()
        fake_cursor.execute.return_value = fake_cursor
        with patch.object(tasks_module, 'get_db', return_value=fake_cursor), \
             patch.object(tasks_module, 'commit'), \
             patch.object(tasks_module, 'WebSocket', return_value=MagicMock()):
            first_id = self.handler.add(first)
            self.assertTrue(started.wait(1))
            second_id = self.handler.add(second)
            self.assertEqual(first_id, second_id)
            self.assertEqual(len(TaskHandler.queue), 1)
            release.set()
            self.assertTrue(finished.wait(1))


if __name__ == '__main__':
    unittest.main()
