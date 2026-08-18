import sqlite3
import unittest
from unittest.mock import Mock, patch

from flask import Flask

from backend.features import tasks as tasks_module
from backend.features.tasks import TaskHandler
from backend.internals.db import KapowarrCursor


class _Task:
    stop = False
    message = ''
    category = ''
    volume_id = None
    issue_id = None

    def __init__(self, should_fail=False):
        self.should_fail = should_fail
        self.action = 'test_task'
        self.display_title = 'Test Task'

    def run(self):
        if self.should_fail:
            raise RuntimeError('source returned no releases')


class task_history_outcomes(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(':memory:')
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("""
            CREATE TABLE task_history(
                task_name NOT NULL,
                display_title NOT NULL,
                run_at INTEGER NOT NULL
            );
        """)
        self.handler = object.__new__(TaskHandler)
        self.handler.context = Flask('task-history-test').app_context

    def tearDown(self):
        self.connection.close()

    def _cursor(self):
        cursor = KapowarrCursor(self.connection)
        cursor.row_factory = sqlite3.Row
        return cursor

    def _run(self, task):
        self.handler.queue = [{
            'task': task,
            'id': 1,
            'status': 'running',
            'thread': Mock()
        }]
        with patch.object(
            tasks_module, 'get_db', side_effect=lambda *a, **k: self._cursor()
        ), patch.object(
            tasks_module, 'WebSocket', return_value=Mock()
        ), patch.object(tasks_module, 'sleep'):
            self.handler._TaskHandler__run_task(task)

    def test_successful_task_is_recorded(self):
        self._run(_Task())

        row = self.connection.execute(
            'SELECT task_name, display_title FROM task_history'
        ).fetchone()
        self.assertEqual(tuple(row), ('test_task', 'Test Task'))
        self.assertEqual(self.handler.queue, [])

    def test_failed_task_is_recorded_with_error_and_removed(self):
        self._run(_Task(should_fail=True))

        row = self.connection.execute(
            'SELECT task_name, display_title FROM task_history'
        ).fetchone()
        self.assertEqual(row['task_name'], 'test_task')
        self.assertEqual(
            row['display_title'],
            'Test Task — Failed: source returned no releases'
        )
        self.assertEqual(self.handler.queue, [])


if __name__ == '__main__':
    unittest.main()
