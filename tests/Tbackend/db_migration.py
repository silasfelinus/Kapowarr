import sqlite3
import unittest
from unittest.mock import MagicMock, patch

from backend.internals import db_migration
from backend.internals.db import KapowarrCursor


class weekly_calendar_migration(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(':memory:')
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript("""
            CREATE TABLE root_folders(
                id INTEGER PRIMARY KEY,
                folder TEXT NOT NULL
            );
            INSERT INTO root_folders VALUES (1, '/comics');

            CREATE TABLE pull_list_entries(
                id INTEGER PRIMARY KEY,
                volume_id INTEGER NOT NULL,
                issue_number VARCHAR(20),
                release_title VARCHAR(255) NOT NULL,
                year INTEGER(5),
                source VARCHAR(50) NOT NULL,
                link TEXT NOT NULL,
                checked_at INTEGER NOT NULL
            );
            INSERT INTO pull_list_entries VALUES (
                1, 7, '4', 'Gwar: Orgasmageddon #4', 2017,
                'GetComics', 'https://example.test/gwar', 123456
            );

            -- setup_db applies the current base schema before running an
            -- upgrade migration, so these tables already exist in production.
            CREATE TABLE publisher_subscriptions(
                publisher VARCHAR(255) PRIMARY KEY COLLATE NOCASE,
                root_folder_id INTEGER NOT NULL,
                auto_search BOOL NOT NULL DEFAULT 0
            );
            INSERT INTO publisher_subscriptions VALUES ('Dynamite', 1, 1);

            CREATE TABLE publisher_automation_history(
                release_key VARCHAR(255) NOT NULL,
                action VARCHAR(20) NOT NULL,
                success BOOL NOT NULL,
                message TEXT,
                attempted_at INTEGER NOT NULL,
                PRIMARY KEY (release_key, action)
            );
            INSERT INTO publisher_automation_history VALUES (
                'gwar-4', 'search', 1, NULL, 123456
            );
        """)
        self.patch = patch.object(
            db_migration,
            'get_db',
            side_effect=lambda *a, **k: self._cursor()
        )
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.connection.close()

    def _cursor(self):
        cursor = KapowarrCursor(self.connection)
        cursor.row_factory = sqlite3.Row
        return cursor

    def test_existing_current_schema_tables_do_not_block_upgrade(self):
        db_migration._migrate_expand_pull_list_calendar()

        migrated = self.connection.execute(
            'SELECT * FROM pull_list_entries WHERE id = 1;'
        ).fetchone()
        self.assertEqual(migrated['release_title'], 'Gwar: Orgasmageddon #4')
        self.assertEqual(migrated['availability_source'], 'GetComics')
        self.assertEqual(
            migrated['availability_link'],
            'https://example.test/gwar'
        )
        self.assertIsNotNone(migrated['week_start'])

        subscription = self.connection.execute(
            'SELECT * FROM publisher_subscriptions;'
        ).fetchone()
        self.assertEqual(subscription['publisher'], 'Dynamite')
        self.assertEqual(subscription['auto_search'], 1)

        history = self.connection.execute(
            'SELECT * FROM publisher_automation_history;'
        ).fetchone()
        self.assertEqual(history['release_key'], 'gwar-4')
        self.assertEqual(history['success'], 1)


class migration_completion(unittest.TestCase):
    @patch('backend.internals.settings.Settings')
    def test_migration_does_not_vacuum_before_web_start(self, Settings):
        settings = Settings.return_value
        settings.sv.database_version = 48
        cursor = MagicMock()
        handler = MagicMock()

        with patch.object(
            db_migration.DatabaseMigrationHandler,
            'handlers',
            {48: handler}
        ), patch.object(
            db_migration.DatabaseMigrationHandler,
            'latest_db_version',
            return_value=49
        ), patch.object(
            db_migration,
            'iter_commit',
            side_effect=lambda iterable: iterable
        ), patch.object(
            db_migration,
            'get_db',
            return_value=cursor
        ):
            db_migration.DatabaseMigrationHandler.migrate()

        handler.assert_called_once_with()
        settings.update.assert_called_once_with({'database_version': 49})
        settings.clear_cache.assert_called_once_with()
        cursor.execute.assert_not_called()


class metadata_provider_migration(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(':memory:')
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript("""
            CREATE TABLE volumes(
                id INTEGER PRIMARY KEY,
                comicvine_id INTEGER NOT NULL,
                site_url TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE issues(
                id INTEGER PRIMARY KEY,
                volume_id INTEGER NOT NULL,
                comicvine_id INTEGER NOT NULL UNIQUE
            );
            CREATE TABLE volumes_covers(
                volume_id INTEGER UNIQUE NOT NULL,
                cover BLOB
            );
            INSERT INTO volumes VALUES (
                1, 4050, 'https://comicvine.example/volume/4050'
            );
            INSERT INTO issues VALUES (2, 1, 9001);
            INSERT INTO volumes_covers VALUES (1, X'CAFE');
        """)
        self.patch = patch.object(
            db_migration, 'get_db', side_effect=self._cursor
        )
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.connection.close()

    def _cursor(self):
        cursor = KapowarrCursor(self.connection)
        cursor.row_factory = sqlite3.Row
        return cursor

    def test_backfills_legacy_ids_and_cover_provenance_restart_safely(self):
        db_migration._migrate_add_metadata_provider_identities()
        db_migration._migrate_add_metadata_provider_identities()

        volume_identity = self.connection.execute(
            'SELECT * FROM volume_external_ids;'
        ).fetchone()
        issue_identity = self.connection.execute(
            'SELECT * FROM issue_external_ids;'
        ).fetchone()
        cover = self.connection.execute(
            'SELECT * FROM volumes_covers;'
        ).fetchone()

        self.assertEqual(volume_identity['provider_id'], 'comicvine')
        self.assertEqual(volume_identity['external_id'], '4050')
        self.assertEqual(issue_identity['external_id'], '9001')
        self.assertEqual(cover['provider_id'], 'comicvine')
        self.assertEqual(cover['external_id'], '4050')


if __name__ == '__main__':
    unittest.main()
