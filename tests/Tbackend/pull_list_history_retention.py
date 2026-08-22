import sqlite3
import unittest
from datetime import date, timedelta
from unittest.mock import AsyncMock, patch

from backend.features import pull_list as pull_list_module
from backend.features.pull_list import (check_weekly_pull_list,
                                        process_publisher_subscriptions)
from backend.internals.db import KapowarrCursor


def _week(offset=0):
    today = date.today()
    monday = today - timedelta(days=today.weekday())
    return (monday + timedelta(weeks=offset)).isoformat()


def _release(title, week_start=None, publisher='DC Comics'):
    week_start = week_start or _week()
    return {
        'series': title,
        'issue_number': '1',
        'year': date.fromisoformat(week_start).year,
        'link': f'https://example.invalid/{title.lower().replace(" ", "-")}',
        'source': 'Mylar Release Provider',
        'publisher': publisher,
        'release_date': week_start,
        'cover_date': None,
        'week_start': week_start,
        'comicvine_volume_id': None,
        'comicvine_issue_id': None,
        'availability_source': None,
        'availability_link': None
    }


class pull_list_history_retention(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(':memory:')
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript("""
            CREATE TABLE pull_list_entries(
                id INTEGER PRIMARY KEY,
                volume_id INTEGER,
                issue_id INTEGER,
                comicvine_volume_id INTEGER,
                comicvine_issue_id INTEGER,
                issue_number VARCHAR(20),
                release_title VARCHAR(255) NOT NULL,
                publisher VARCHAR(255),
                release_date DATE,
                cover_date DATE,
                week_start DATE NOT NULL,
                year INTEGER,
                source VARCHAR(50) NOT NULL,
                link TEXT NOT NULL,
                availability_source VARCHAR(50),
                availability_link TEXT,
                checked_at INTEGER NOT NULL
            );
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
        self.get_db_patch = patch.object(
            pull_list_module,
            'get_db',
            side_effect=lambda *a, **k: self._cursor()
        )
        self.get_db_patch.start()

    def _cursor(self):
        cursor = KapowarrCursor(self.connection)
        cursor.row_factory = sqlite3.Row
        return cursor

    def _insert_entry(self, title, week_start, publisher='DC Comics'):
        self.connection.execute(
            """
            INSERT INTO pull_list_entries(
                volume_id, issue_id, comicvine_volume_id, comicvine_issue_id,
                issue_number, release_title, publisher, release_date,
                cover_date, week_start, year, source, link,
                availability_source, availability_link, checked_at
            ) VALUES (
                NULL, NULL, NULL, NULL, '1', ?, ?, ?, NULL, ?, ?,
                'Mylar Release Provider', ?, NULL, NULL, 1
            );
            """,
            (
                title, publisher, week_start, week_start,
                date.fromisoformat(week_start).year,
                f'https://example.invalid/{title.lower().replace(" ", "-")}'
            )
        )
        self.connection.commit()

    def _check(self, releases, requested_week=None):
        with patch.object(
            pull_list_module,
            '_fetch_all_weekly_releases',
            new=AsyncMock(return_value=releases)
        ) as fetch, patch.object(
            pull_list_module.Library,
            'get_public_volumes',
            return_value=[]
        ):
            result = check_weekly_pull_list(requested_week)
        return result, fetch

    def tearDown(self):
        self.get_db_patch.stop()
        self.connection.close()

    def test_refresh_keeps_weeks_outside_the_fresh_window(self):
        old_week = _week(-12)
        self._insert_entry('Archived Batman', old_week)

        self._check([_release('Current Batman')])

        rows = self.connection.execute(
            'SELECT release_title, week_start FROM pull_list_entries '
            'ORDER BY week_start;'
        ).fetchall()
        self.assertEqual(
            [(row['release_title'], row['week_start']) for row in rows],
            [('Archived Batman', old_week), ('Current Batman', _week())]
        )

    def test_refresh_replaces_a_week_that_was_returned_again(self):
        self._insert_entry('Stale Batman', _week())

        self._check([_release('Fresh Batman')])

        rows = self.connection.execute(
            'SELECT release_title FROM pull_list_entries WHERE week_start = ?;',
            (_week(),)
        ).fetchall()
        self.assertEqual([row['release_title'] for row in rows], ['Fresh Batman'])

    def test_manual_check_fetches_only_the_selected_historical_week(self):
        selected = date.fromisoformat(_week(-20))
        selected_iso = selected.isoformat()
        self._insert_entry('Stale Archive', selected_iso)
        self._insert_entry('Keep Current', _week())

        _, fetch = self._check(
            [_release('Fresh Archive', selected_iso)],
            selected
        )

        fetch.assert_awaited_once_with(selected)
        rows = self.connection.execute(
            'SELECT release_title, week_start FROM pull_list_entries '
            'ORDER BY week_start;'
        ).fetchall()
        self.assertEqual(
            [(row['release_title'], row['week_start']) for row in rows],
            [('Fresh Archive', selected_iso), ('Keep Current', _week())]
        )

    def test_manual_check_keeps_existing_week_when_provider_returns_nothing(self):
        selected = date.fromisoformat(_week(-20))
        selected_iso = selected.isoformat()
        self._insert_entry('Keep Archive', selected_iso)

        with self.assertRaisesRegex(
            RuntimeError,
            f'No publisher releases were returned for week {selected_iso}'
        ):
            self._check([], selected)

        row = self.connection.execute(
            'SELECT release_title FROM pull_list_entries WHERE week_start = ?;',
            (selected_iso,)
        ).fetchone()
        self.assertEqual(row['release_title'], 'Keep Archive')

    def test_publisher_automation_reads_the_entire_retained_archive(self):
        self._insert_entry('Archived Batman', _week(-12))
        self._insert_entry('Current Batman', _week())
        self.connection.execute(
            """
            INSERT INTO publisher_subscriptions(
                publisher, root_folder_id, auto_search
            ) VALUES ('DC Comics', 7, 1);
            """
        )
        self.connection.commit()

        with patch.object(
            pull_list_module,
            '_add_or_monitor_entry',
            return_value=(1, 10)
        ) as add_or_monitor, patch.object(
            pull_list_module,
            'auto_search',
            return_value=[{'link': 'https://example.invalid/download'}]
        ):
            downloads = process_publisher_subscriptions([
                {'week_start': _week(), 'publisher': 'DC Comics'}
            ])

        self.assertEqual(add_or_monitor.call_count, 2)
        self.assertEqual(len(downloads), 2)
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM publisher_automation_history "
                "WHERE action = 'auto_search' AND success = 1;"
            ).fetchone()[0],
            2
        )


if __name__ == '__main__':
    unittest.main()
