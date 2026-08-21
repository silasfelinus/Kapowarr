import unittest
from datetime import date
from unittest.mock import patch

from backend.features import pull_list_parallel as parallel


class _Rows:
    def __init__(self, rows):
        self.rows = rows

    def fetchalldict(self):
        return [dict(row) for row in self.rows]


class _Cursor:
    def __init__(self, rows):
        self.rows = rows

    def execute(self, *args, **kwargs):
        return _Rows(self.rows)


class pull_list_week_summary(unittest.TestCase):
    def test_stored_weeks_are_returned_with_json_safe_dates(self):
        with patch.object(
            parallel, 'get_db',
            return_value=_Cursor([{
                'week_start': date(2026, 8, 17),
                'release_count': 129,
                'checked_at': 123
            }])
        ):
            weeks = parallel.get_pull_list_weeks()

        self.assertEqual(weeks[0]['week_start'], '2026-08-17')
        self.assertEqual(weeks[0]['release_count'], 129)


class pull_list_parallel_runner(unittest.TestCase):
    def _runner(self):
        runner = parallel.PullListCheckRunner()
        runner._checks[1] = {
            'id': 1,
            'status': 'queued',
            'message': '',
            'error': None,
            'release_count': None,
            'started_at': 0,
            'finished_at': None,
        }
        return runner

    def test_run_refreshes_without_task_handler_queue(self):
        runner = self._runner()
        releases = [{'release_title': 'Batman'}]
        with patch.object(
            parallel, 'check_weekly_pull_list', return_value=releases
        ), patch.object(
            parallel, 'process_publisher_subscriptions', return_value=[]
        ), patch.object(runner, '_record_history'):
            runner._run(1)

        check = runner.get(1)
        self.assertEqual(check['status'], 'completed')
        self.assertEqual(check['release_count'], 1)
        self.assertEqual(check['message'], 'Release calendar updated.')

    def test_run_surfaces_refresh_failure(self):
        runner = self._runner()
        with patch.object(
            parallel, 'check_weekly_pull_list',
            side_effect=RuntimeError('provider unavailable')
        ), patch.object(runner, '_record_history'):
            runner._run(1)

        check = runner.get(1)
        self.assertEqual(check['status'], 'failed')
        self.assertEqual(check['error'], 'provider unavailable')
        self.assertIn('provider unavailable', check['message'])


if __name__ == '__main__':
    unittest.main()
