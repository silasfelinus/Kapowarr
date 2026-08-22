import sqlite3
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from backend.features import pull_list as pull_list_module
from backend.features.pull_list import (_add_or_monitor_entry,
                                        _resolve_release_metadata,
                                        get_pull_list)
from backend.internals.db import KapowarrCursor


class pull_list_metadata_resolution(unittest.TestCase):
    @staticmethod
    def _entry():
        return {
            'id': 7,
            'volume_id': None,
            'issue_id': None,
            'comicvine_volume_id': None,
            'comicvine_issue_id': None,
            'issue_number': '3',
            'release_title': 'Batman',
            'publisher': 'DC Comics',
            'release_date': '2026-08-12',
            'week_start': '2026-08-10',
            'year': 2024,
        }

    def test_resolver_prefers_configured_metron_and_stops(self):
        provider = MagicMock()
        provider.search_volumes = AsyncMock(return_value=[{
            'provider_id': 'metron',
            'external_id': '123',
            'comicvine_id': 4050,
            'title': 'Batman',
            'year': 2024,
            'publisher': 'DC Comics',
            'already_added': None,
        }])
        provider.is_unavailable_error.return_value = False

        with patch.object(
            pull_list_module,
            'configured_metadata_provider_ids',
            return_value=['comicvine', 'gcd', 'metron']
        ), patch.object(
            pull_list_module,
            'get_metadata_provider',
            return_value=provider
        ) as get_provider:
            result = _resolve_release_metadata(self._entry())

        self.assertEqual(result['provider_id'], 'metron')
        self.assertEqual(result['external_id'], '123')
        self.assertEqual(result['comicvine_id'], 4050)
        get_provider.assert_called_once_with('metron')
        provider.search_volumes.assert_awaited_once_with('Batman')

    def test_add_without_comicvine_id_uses_resolved_metadata_identity(self):
        entry = self._entry()
        cursor = MagicMock()
        with patch.object(
            pull_list_module,
            '_resolve_release_metadata',
            return_value={
                'provider_id': 'metron',
                'external_id': '123',
                'comicvine_id': None,
                'volume_id': None,
            }
        ), patch.object(
            pull_list_module.Library, 'add', return_value=42
        ) as add, patch.object(
            pull_list_module, 'Volume'
        ), patch.object(
            pull_list_module, '_find_issue_id', return_value=None
        ), patch.object(
            pull_list_module, 'get_db', return_value=cursor
        ):
            volume_id, issue_id = _add_or_monitor_entry(entry, 9)

        self.assertEqual(volume_id, 42)
        self.assertIsNone(issue_id)
        add.assert_called_once_with(
            None, 9, True, pull_list_module.MonitorScheme.ALL,
            True, auto_search=False,
            metadata_provider_id='metron', metadata_external_id='123'
        )
        self.assertEqual(entry['volume_id'], 42)


class pull_list_auto_add_visibility(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(':memory:')
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript("""
            CREATE TABLE volumes(
                id INTEGER PRIMARY KEY,
                title TEXT,
                monitored BOOL
            );
            CREATE TABLE pull_list_entries(
                id INTEGER PRIMARY KEY,
                volume_id INTEGER,
                issue_id INTEGER,
                comicvine_volume_id INTEGER,
                comicvine_issue_id INTEGER,
                issue_number TEXT,
                release_title TEXT NOT NULL,
                publisher TEXT,
                release_date DATE,
                cover_date DATE,
                week_start DATE NOT NULL,
                year INTEGER,
                source TEXT NOT NULL,
                link TEXT NOT NULL,
                availability_source TEXT,
                availability_link TEXT,
                checked_at INTEGER NOT NULL
            );
            CREATE TABLE publisher_subscriptions(
                publisher TEXT PRIMARY KEY COLLATE NOCASE,
                root_folder_id INTEGER NOT NULL,
                auto_search BOOL NOT NULL DEFAULT 0
            );
            CREATE TABLE publisher_automation_history(
                release_key TEXT NOT NULL,
                action TEXT NOT NULL,
                success BOOL NOT NULL,
                message TEXT,
                attempted_at INTEGER NOT NULL,
                PRIMARY KEY (release_key, action)
            );
        """)
        self.connection.execute(
            """
            INSERT INTO pull_list_entries(
                id, volume_id, issue_id, comicvine_volume_id,
                comicvine_issue_id, issue_number, release_title, publisher,
                release_date, cover_date, week_start, year, source, link,
                availability_source, availability_link, checked_at
            ) VALUES (
                1, NULL, NULL, NULL, NULL, '3', 'The Adequates', 'Comixology',
                '2026-08-11', NULL, '2026-08-10', 2026,
                'Mylar Release Provider', 'https://example.invalid/release',
                NULL, NULL, 1
            );
            """
        )
        self.connection.execute(
            "INSERT INTO publisher_subscriptions VALUES ('Comixology', 1, 0);"
        )
        self.connection.execute(
            """
            INSERT INTO publisher_automation_history
                (release_key, action, success, message, attempted_at)
            VALUES (
                'comixology|the adequates|3|2026-08-11',
                'auto_add', 0, 'No metadata match found; will retry later', 2
            );
            """
        )
        self.connection.commit()

    def tearDown(self):
        self.connection.close()

    def _cursor(self):
        cursor = KapowarrCursor(self.connection)
        cursor.row_factory = sqlite3.Row
        return cursor

    def test_auto_add_failure_is_returned_to_calendar(self):
        with patch.object(
            pull_list_module, 'get_db', side_effect=lambda *a, **k: self._cursor()
        ):
            rows = get_pull_list('2026-08-10')

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['automation_success'], 0)
        self.assertEqual(rows[0]['automation_action'], 'auto_add')
        self.assertIn('No metadata match', rows[0]['automation_message'])


if __name__ == '__main__':
    unittest.main()
