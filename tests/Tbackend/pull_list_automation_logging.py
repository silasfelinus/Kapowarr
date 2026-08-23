# -*- coding: utf-8 -*-

"""A comic that is not out yet is not an error."""

import sqlite3
import unittest
from datetime import date, timedelta
from unittest.mock import patch

from backend.features import pull_list
from backend.internals.db import KapowarrCursor


def _monday(value):
    return value - timedelta(days=value.weekday())


class not_out_yet_is_not_an_error(unittest.TestCase):
    """One pull list check produced 139 ERROR tracebacks.

    Every one of them said 'No matching download was found yet' -- the
    ordinary state of a comic on the morning it is solicited. Raising
    RuntimeError for it and handing that to LOGGER.exception put a full
    stack trace in the error log per release, which buried the failures
    that were genuinely worth reading. The pull list already shows these
    as pending in its own column, so the error log was not even where the
    information belonged.
    """

    def setUp(self):
        self.connection = sqlite3.connect(':memory:')
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript("""
            CREATE TABLE publisher_subscriptions(
                publisher VARCHAR(255) PRIMARY KEY COLLATE NOCASE,
                root_folder_id INTEGER NOT NULL,
                auto_search BOOL NOT NULL DEFAULT 0
            );
            CREATE TABLE publisher_automation_history(
                release_key VARCHAR(255) NOT NULL,
                action VARCHAR(20) NOT NULL,
                success BOOL NOT NULL,
                message TEXT,
                attempted_at INTEGER NOT NULL,
                PRIMARY KEY (release_key, action)
            );
        """)
        self.connection.execute(
            """
            INSERT INTO publisher_subscriptions(
                publisher, root_folder_id, auto_search
            ) VALUES ('DC Comics', 7, 1);
            """
        )
        self.connection.commit()
        self.get_db_patch = patch.object(
            pull_list, 'get_db', side_effect=lambda *a, **k: self._cursor()
        )
        self.get_db_patch.start()

    def tearDown(self):
        self.get_db_patch.stop()
        self.connection.close()

    def _cursor(self):
        cursor = KapowarrCursor(self.connection)
        cursor.row_factory = sqlite3.Row
        return cursor

    def _entry(self, issue_id):
        week = _monday(date.today()).isoformat()
        return {
            'id': issue_id, 'publisher': 'DC Comics', 'week_start': week,
            'release_date': week, 'release_title': f'Series {issue_id}',
            'issue_number': str(issue_id), 'comicvine_issue_id': issue_id,
            'comicvine_volume_id': 1000 + issue_id,
            'volume_id': None, 'issue_id': None
        }

    def _run(self, entry, add_result, search_result):
        """Returns (records the error log kept, records it did not)."""
        with patch.object(
            pull_list, '_add_or_monitor_entry', **add_result
        ), patch.object(
            pull_list, 'auto_search', **search_result
        ), self.assertLogs(pull_list.LOGGER, level='INFO') as logs:
            pull_list.process_publisher_subscriptions([entry])

        errors = [r for r in logs.records if r.levelname == 'ERROR']
        return errors, logs.records

    def test_nothing_found_yet_stays_out_of_the_error_log(self):
        errors, records = self._run(
            self._entry(21),
            {'return_value': (1, 101)}, {'return_value': []}
        )

        self.assertEqual(
            [r.getMessage() for r in errors], [],
            'a release no indexer is carrying yet is not a failure'
        )
        self.assertTrue(
            any('nothing yet' in r.getMessage() for r in records),
            'it is still worth one line, just not a traceback'
        )

    def test_an_issue_missing_from_metadata_stays_out_too(self):
        errors, _ = self._run(
            self._entry(22),
            {'return_value': (1, None)}, {'return_value': []}
        )

        self.assertEqual([r.getMessage() for r in errors], [])

    def test_it_is_still_recorded_as_pending_for_the_pull_list(self):
        """Quiet in the log, but the pull list column must still show it."""
        self._run(
            self._entry(23), {'return_value': (1, 103)}, {'return_value': []}
        )

        row = self.connection.execute(
            'SELECT success, message FROM publisher_automation_history;'
        ).fetchone()
        self.assertEqual(row['success'], 0)
        self.assertIn('yet', row['message'])

    def test_a_real_failure_still_gets_its_traceback(self):
        """The point is to make these readable, not to hide them."""
        errors, _ = self._run(
            self._entry(24),
            {'side_effect': ValueError('the root folder is gone')},
            {'return_value': []}
        )

        self.assertEqual(len(errors), 1)
        self.assertIn('failed', errors[0].getMessage())
        self.assertIsNotNone(
            errors[0].exc_info, 'a genuine fault still needs its stack'
        )


if __name__ == '__main__':
    unittest.main()
