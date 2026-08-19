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


class provider_native_identity_migration(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(':memory:')
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript("""
            PRAGMA foreign_keys = ON;
            CREATE TABLE root_folders(
                id INTEGER PRIMARY KEY,
                folder TEXT NOT NULL
            );
            INSERT INTO root_folders VALUES (1, '/comics');
            CREATE TABLE volumes(
                id INTEGER PRIMARY KEY,
                comicvine_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                alt_title TEXT,
                year INTEGER,
                publisher TEXT,
                volume_number INTEGER DEFAULT 1,
                description TEXT,
                site_url TEXT NOT NULL DEFAULT '',
                monitored BOOL NOT NULL DEFAULT 0,
                monitor_new_issues BOOL NOT NULL DEFAULT 1,
                root_folder INTEGER NOT NULL,
                folder TEXT,
                custom_folder BOOL NOT NULL DEFAULT 0,
                last_cv_fetch INTEGER DEFAULT 0,
                special_version TEXT,
                special_version_locked BOOL NOT NULL DEFAULT 0,
                FOREIGN KEY (root_folder) REFERENCES root_folders(id)
            );
            CREATE TABLE issues(
                id INTEGER PRIMARY KEY,
                volume_id INTEGER NOT NULL,
                comicvine_id INTEGER NOT NULL UNIQUE,
                issue_number TEXT NOT NULL,
                calculated_issue_number FLOAT NOT NULL,
                title TEXT,
                date TEXT,
                description TEXT,
                monitored BOOL NOT NULL DEFAULT 1,
                FOREIGN KEY (volume_id) REFERENCES volumes(id)
                    ON DELETE CASCADE
            );
            CREATE TABLE issues_files(
                issue_id INTEGER NOT NULL,
                file_id INTEGER NOT NULL,
                FOREIGN KEY (issue_id) REFERENCES issues(id)
            );
            CREATE TABLE volume_external_ids(
                volume_id INTEGER NOT NULL,
                provider_id TEXT NOT NULL,
                external_id TEXT NOT NULL,
                PRIMARY KEY (volume_id, provider_id),
                FOREIGN KEY (volume_id) REFERENCES volumes(id)
                    ON DELETE CASCADE
            );
            CREATE TABLE issue_external_ids(
                issue_id INTEGER NOT NULL,
                provider_id TEXT NOT NULL,
                external_id TEXT NOT NULL,
                PRIMARY KEY (issue_id, provider_id),
                FOREIGN KEY (issue_id) REFERENCES issues(id)
                    ON DELETE CASCADE
            );
            CREATE INDEX issues_volume_number_index
                ON issues(volume_id, calculated_issue_number);
            CREATE INDEX issues_volume_index ON issues(volume_id);
            INSERT INTO volumes VALUES (
                1, 4050, 'Saga', NULL, 2012, 'Image', 1, '', '',
                1, 1, 1, '/comics/Saga', 0, 123, 'normal', 0
            );
            INSERT INTO issues VALUES (
                2, 1, 9001, '1', 1.0, NULL, '2012-03-14', '', 1
            );
            INSERT INTO issues_files VALUES (2, 7);
            INSERT INTO volume_external_ids VALUES (1, 'comicvine', '4050');
            INSERT INTO issue_external_ids VALUES (2, 'comicvine', '9001');
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

    def test_preserves_legacy_rows_and_allows_native_null_ids(self):
        db_migration._migrate_allow_provider_native_identities()

        legacy = self.connection.execute(
            'SELECT * FROM volumes WHERE id = 1;'
        ).fetchone()
        self.assertEqual(legacy['comicvine_id'], 4050)
        self.assertEqual(legacy['folder'], '/comics/Saga')

        self.connection.execute("""
            INSERT INTO volumes(
                id, comicvine_id, title, root_folder
            ) VALUES (3, NULL, 'Metron Native', 1);
        """)
        self.connection.executemany("""
            INSERT INTO issues(
                volume_id, comicvine_id, issue_number,
                calculated_issue_number
            ) VALUES (3, NULL, ?, ?);
        """, (('1', 1.0), ('2', 2.0)))

        # A retry after the DDL completed but before the version setting was
        # persisted must preserve both legacy and provider-native rows.
        db_migration._migrate_allow_provider_native_identities()

        native = self.connection.execute(
            'SELECT comicvine_id FROM volumes WHERE id = 3;'
        ).fetchone()
        native_issue_count = self.connection.execute(
            'SELECT COUNT(*) FROM issues WHERE volume_id = 3;'
        ).fetchone()[0]
        linked_file = self.connection.execute(
            'SELECT * FROM issues_files;'
        ).fetchone()
        volume_identity = self.connection.execute(
            'SELECT * FROM volume_external_ids;'
        ).fetchone()
        issue_identity = self.connection.execute(
            'SELECT * FROM issue_external_ids;'
        ).fetchone()
        self.assertIsNone(native['comicvine_id'])
        self.assertEqual(native_issue_count, 2)
        self.assertEqual(linked_file['issue_id'], 2)
        self.assertEqual(volume_identity['external_id'], '4050')
        self.assertEqual(issue_identity['external_id'], '9001')
        self.assertEqual(
            list(self.connection.execute('PRAGMA foreign_key_check;')), []
        )


if __name__ == '__main__':
    unittest.main()
