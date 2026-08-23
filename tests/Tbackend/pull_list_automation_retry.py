# -*- coding: utf-8 -*-

"""A release that was not out yet gets another chance tomorrow."""

import sqlite3
import unittest
from datetime import date, timedelta
from unittest.mock import patch

from backend.features import pull_list
from backend.internals.db import KapowarrCursor


def _monday(value):
    return value - timedelta(days=value.weekday())


class _Base(unittest.TestCase):
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
            "INSERT INTO publisher_subscriptions("
            "publisher, root_folder_id, auto_search"
            ") VALUES ('DC Comics', 7, 1);"
        )
        self.connection.commit()
        patcher = patch.object(
            pull_list, 'get_db', side_effect=lambda *a, **k: self._cursor()
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def _cursor(self):
        cursor = KapowarrCursor(self.connection)
        cursor.row_factory = sqlite3.Row
        return cursor

    def _entry(self, issue_id, release_date=None):
        week = _monday(date.today())
        return {
            'id': issue_id, 'publisher': 'DC Comics',
            'week_start': week.isoformat(),
            'release_date': (release_date or week).isoformat(),
            'release_title': f'Series {issue_id}',
            'issue_number': str(issue_id), 'comicvine_issue_id': issue_id,
            'comicvine_volume_id': 1000 + issue_id,
            'volume_id': None, 'issue_id': None
        }

    def _run(self, entry, results):
        with patch.object(
            pull_list, '_add_or_monitor_entry', return_value=(1, 101)
        ), patch.object(pull_list, 'auto_search', return_value=results) as s:
            pull_list.process_publisher_subscriptions([entry])
        return s

    def _age_history(self, seconds):
        self.connection.execute(
            'UPDATE publisher_automation_history SET attempted_at = ?;',
            (self.connection.execute(
                'SELECT attempted_at FROM publisher_automation_history;'
            ).fetchone()[0] - seconds,)
        )
        self.connection.commit()


class a_failure_is_not_the_end_of_it(_Base):
    """`.exists()` matched a failed row as happily as a successful one.

    A comic no indexer was carrying on release morning was attempted
    once and then never again -- while the summary line counted it as
    `pending retry`. Very often one is carrying it a day later.
    """

    def test_a_failed_attempt_is_retried_the_next_day(self):
        entry = self._entry(11)
        self._run(entry, [])
        self._age_history(pull_list.PUBLISHER_RETRY_INTERVAL + 60)

        searched = self._run(entry, [{'link': 'https://example.test/late'}])

        self.assertEqual(searched.call_count, 1, 'it should try again')
        row = self.connection.execute(
            'SELECT success FROM publisher_automation_history;'
        ).fetchone()
        self.assertEqual(row['success'], 1)

    def test_but_not_again_within_the_day(self):
        """Retrying every check would hammer the indexers."""
        entry = self._entry(12)
        self._run(entry, [])

        searched = self._run(entry, [])

        self.assertEqual(searched.call_count, 0)

    def test_a_success_is_final(self):
        entry = self._entry(13)
        self._run(entry, [{'link': 'https://example.test/once'}])
        self._age_history(pull_list.PUBLISHER_RETRY_INTERVAL * 30)

        searched = self._run(entry, [{'link': 'https://example.test/again'}])

        self.assertEqual(
            searched.call_count, 0, 'a grabbed release is not re-grabbed'
        )


class a_release_that_is_not_out_yet_is_not_attempted(_Base):
    """A week starts on Monday and admits every release in it.

    From Monday morning that includes Friday's, three days before anyone
    could have it -- attempted, failed for the only possible reason, and
    recorded.
    """

    def test_later_this_week_is_left_alone(self):
        entry = self._entry(14, release_date=date.today() + timedelta(days=3))

        with patch.object(pull_list, '_add_or_monitor_entry') as add, \
                patch.object(pull_list, 'auto_search') as searched:
            pull_list.process_publisher_subscriptions([entry])

        add.assert_not_called()
        searched.assert_not_called()
        self.assertEqual(
            self.connection.execute(
                'SELECT COUNT(*) FROM publisher_automation_history;'
            ).fetchone()[0],
            0,
            'and nothing is recorded, so it is clean when it does arrive'
        )

    def test_today_is_attempted(self):
        entry = self._entry(15, release_date=date.today())

        searched = self._run(entry, [{'link': 'https://example.test/out'}])

        self.assertEqual(searched.call_count, 1)


if __name__ == '__main__':
    unittest.main()
