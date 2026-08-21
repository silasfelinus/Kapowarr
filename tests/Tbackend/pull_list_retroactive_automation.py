import sqlite3
import unittest
from datetime import date, timedelta
from unittest.mock import patch

from backend.features import pull_list
from backend.internals.db import KapowarrCursor


def _monday(value):
    return value - timedelta(days=value.weekday())


class publisher_retroactive_automation(unittest.TestCase):
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
            pull_list,
            'get_db',
            side_effect=lambda *a, **k: self._cursor()
        )
        self.get_db_patch.start()

    def tearDown(self):
        self.get_db_patch.stop()
        self.connection.close()

    def _cursor(self):
        cursor = KapowarrCursor(self.connection)
        cursor.row_factory = sqlite3.Row
        return cursor

    def _entry(self, week_start, issue_id):
        return {
            'id': issue_id,
            'publisher': 'DC Comics',
            'week_start': week_start.isoformat(),
            'release_date': week_start.isoformat(),
            'release_title': f'Backfill Series {issue_id}',
            'issue_number': str(issue_id),
            'comicvine_issue_id': issue_id,
            'comicvine_volume_id': 1000 + issue_id,
            'volume_id': None,
            'issue_id': None
        }

    def test_autograb_processes_stored_past_and_current_weeks(self):
        current = _monday(date.today())
        entries = [
            self._entry(current - timedelta(weeks=3), 11),
            self._entry(current, 12)
        ]

        with patch.object(
            pull_list, '_add_or_monitor_entry', side_effect=[(1, 101), (2, 102)]
        ), patch.object(
            pull_list, 'auto_search',
            side_effect=[
                [{'link': 'https://example.test/old'}],
                [{'link': 'https://example.test/current'}]
            ]
        ):
            downloads = pull_list.process_publisher_subscriptions(entries)

        self.assertEqual(downloads, [
            ('https://example.test/old', 1, 101),
            ('https://example.test/current', 2, 102)
        ])
        history = self.connection.execute(
            "SELECT release_key, success FROM publisher_automation_history "
            "ORDER BY release_key;"
        ).fetchall()
        self.assertEqual(len(history), 2)
        self.assertTrue(all(row['success'] == 1 for row in history))

    def test_autograb_does_not_process_future_pull_lists(self):
        future = _monday(date.today()) + timedelta(weeks=1)
        entry = self._entry(future, 13)

        with patch.object(pull_list, '_add_or_monitor_entry') as add_entry, \
                patch.object(pull_list, 'auto_search') as search:
            downloads = pull_list.process_publisher_subscriptions([entry])

        self.assertEqual(downloads, [])
        add_entry.assert_not_called()
        search.assert_not_called()
        self.assertEqual(
            self.connection.execute(
                'SELECT COUNT(*) FROM publisher_automation_history;'
            ).fetchone()[0],
            0
        )

    def test_successful_historical_grab_is_not_repeated(self):
        past = _monday(date.today()) - timedelta(weeks=2)
        entry = self._entry(past, 14)

        with patch.object(
            pull_list, '_add_or_monitor_entry', return_value=(3, 103)
        ) as add_entry, patch.object(
            pull_list, 'auto_search',
            return_value=[{'link': 'https://example.test/once'}]
        ) as search:
            first = pull_list.process_publisher_subscriptions([entry])
            second = pull_list.process_publisher_subscriptions([entry])

        self.assertEqual(first, [('https://example.test/once', 3, 103)])
        self.assertEqual(second, [])
        self.assertEqual(add_entry.call_count, 1)
        self.assertEqual(search.call_count, 1)


if __name__ == '__main__':
    unittest.main()
