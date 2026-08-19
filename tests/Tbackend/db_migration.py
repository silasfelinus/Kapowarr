import sqlite3
import unittest
from unittest.mock import MagicMock, patch

from backend.internals import db, db_migration
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


class relax_comicvine_id_not_null_migration(unittest.TestCase):
    """Covers migration 50 (t-056): comicvine_id becomes nullable on
    volumes/issues, without a legacy ComicVine ID for every volume/issue
    being required any more.
    """

    def setUp(self):
        self.connection = sqlite3.connect(':memory:')
        self.connection.row_factory = sqlite3.Row
        # The pre-migration-50 (i.e. migration-49) schema: comicvine_id is
        # still NOT NULL on both tables, and both have dependents with a
        # FOREIGN KEY pointing at them -- the exact situation that makes
        # `ALTER TABLE ... RENAME TO` unsafe (see the migration's docstring).
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
                title VARCHAR(255) NOT NULL,
                alt_title VARCHAR(255),
                year INTEGER(5),
                publisher VARCHAR(255),
                volume_number INTEGER(8) DEFAULT 1,
                description TEXT,
                site_url TEXT NOT NULL DEFAULT "",
                monitored BOOL NOT NULL DEFAULT 0,
                monitor_new_issues BOOL NOT NULL DEFAULT 1,
                root_folder INTEGER NOT NULL,
                folder TEXT,
                custom_folder BOOL NOT NULL DEFAULT 0,
                last_cv_fetch INTEGER(8) DEFAULT 0,
                special_version VARCHAR(255),
                special_version_locked BOOL NOT NULL DEFAULT 0,
                FOREIGN KEY (root_folder) REFERENCES root_folders(id)
            );
            INSERT INTO volumes(
                id, comicvine_id, title, site_url, root_folder
            ) VALUES (
                1, 4050, 'Test Volume', 'https://comicvine.example/4050', 1
            );

            CREATE TABLE issues(
                id INTEGER PRIMARY KEY,
                volume_id INTEGER NOT NULL,
                comicvine_id INTEGER NOT NULL UNIQUE,
                issue_number VARCHAR(20) NOT NULL,
                calculated_issue_number FLOAT(20) NOT NULL,
                title VARCHAR(255),
                date VARCHAR(10),
                description TEXT,
                monitored BOOL NOT NULL DEFAULT 1,
                FOREIGN KEY (volume_id) REFERENCES volumes(id)
                    ON DELETE CASCADE
            );
            CREATE INDEX issues_volume_number_index
                ON issues(volume_id, calculated_issue_number);
            CREATE INDEX issues_volume_index
                ON issues(volume_id);
            INSERT INTO issues(
                id, volume_id, comicvine_id, issue_number,
                calculated_issue_number
            ) VALUES (1, 1, 9001, '1', 1.0);

            -- Dependents with a FOREIGN KEY on volumes/issues, to prove the
            -- rebuild doesn't leave their references dangling.
            CREATE TABLE volumes_covers(
                volume_id INTEGER UNIQUE NOT NULL,
                cover BLOB,
                provider_id VARCHAR(50),
                external_id TEXT,
                source_url TEXT,
                FOREIGN KEY (volume_id) REFERENCES volumes(id)
                    ON DELETE CASCADE
            );
            INSERT INTO volumes_covers VALUES (1, X'CAFE', 'comicvine', '4050', NULL);

            CREATE TABLE issues_files(
                file_id INTEGER NOT NULL,
                issue_id INTEGER NOT NULL,
                FOREIGN KEY (issue_id) REFERENCES issues(id)
            );
            INSERT INTO issues_files VALUES (1, 1);
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

    def _notnull(self, table):
        return any(
            row[1] == 'comicvine_id' and row[3]
            for row in self.connection.execute(f"PRAGMA table_info({table});")
        )

    def test_relaxes_not_null_and_preserves_data(self):
        self.assertTrue(self._notnull('volumes'))
        self.assertTrue(self._notnull('issues'))

        db_migration._migrate_relax_comicvine_id_not_null()

        self.assertFalse(self._notnull('volumes'))
        self.assertFalse(self._notnull('issues'))

        volume = self.connection.execute(
            'SELECT * FROM volumes WHERE id = 1;'
        ).fetchone()
        self.assertEqual(volume['comicvine_id'], 4050)
        self.assertEqual(volume['title'], 'Test Volume')

        issue = self.connection.execute(
            'SELECT * FROM issues WHERE id = 1;'
        ).fetchone()
        self.assertEqual(issue['comicvine_id'], 9001)
        self.assertEqual(issue['issue_number'], '1')

    def test_dependent_foreign_keys_still_resolve_after_rebuild(self):
        db_migration._migrate_relax_comicvine_id_not_null()

        # If ALTER TABLE ... RENAME TO had been used, other tables'
        # FOREIGN KEY clauses would have been silently rewritten to point at
        # the temporary name and then left dangling -- PRAGMA
        # foreign_key_check would report them.
        violations = self.connection.execute(
            'PRAGMA foreign_key_check;'
        ).fetchall()
        self.assertEqual(violations, [])

        cover = self.connection.execute(
            'SELECT * FROM volumes_covers WHERE volume_id = 1;'
        ).fetchone()
        self.assertEqual(cover['external_id'], '4050')

        issue_file = self.connection.execute(
            'SELECT * FROM issues_files WHERE issue_id = 1;'
        ).fetchone()
        self.assertEqual(issue_file['file_id'], 1)

    def test_indexes_recreated_on_issues(self):
        db_migration._migrate_relax_comicvine_id_not_null()

        index_names = {
            row['name']
            for row in self.connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'index' AND tbl_name = 'issues';"
            )
        }
        self.assertIn('issues_volume_number_index', index_names)
        self.assertIn('issues_volume_index', index_names)

    def test_allows_multiple_null_comicvine_ids_after_migration(self):
        db_migration._migrate_relax_comicvine_id_not_null()

        self.connection.execute(
            "INSERT INTO volumes(comicvine_id, title, root_folder) "
            "VALUES (NULL, 'Metron Only 1', 1);"
        )
        self.connection.execute(
            "INSERT INTO volumes(comicvine_id, title, root_folder) "
            "VALUES (NULL, 'Metron Only 2', 1);"
        )
        self.connection.execute(
            "INSERT INTO issues("
            "   volume_id, comicvine_id, issue_number, calculated_issue_number"
            ") VALUES (1, NULL, '2', 2.0);"
        )
        self.connection.execute(
            "INSERT INTO issues("
            "   volume_id, comicvine_id, issue_number, calculated_issue_number"
            ") VALUES (1, NULL, '3', 3.0);"
        )
        self.connection.commit()

        null_volumes = self.connection.execute(
            'SELECT title FROM volumes WHERE comicvine_id IS NULL;'
        ).fetchall()
        self.assertEqual(len(null_volumes), 2)

        null_issues = self.connection.execute(
            'SELECT issue_number FROM issues WHERE comicvine_id IS NULL;'
        ).fetchall()
        self.assertEqual(len(null_issues), 2)

    def test_is_idempotent_and_restart_safe(self):
        db_migration._migrate_relax_comicvine_id_not_null()
        # Re-running after "completion" (e.g. a process restart before the
        # database version was persisted) must be a safe no-op.
        db_migration._migrate_relax_comicvine_id_not_null()

        self.assertFalse(self._notnull('volumes'))
        self.assertFalse(self._notnull('issues'))

        volume = self.connection.execute(
            'SELECT * FROM volumes WHERE id = 1;'
        ).fetchone()
        self.assertEqual(volume['comicvine_id'], 4050)

        violations = self.connection.execute(
            'PRAGMA foreign_key_check;'
        ).fetchall()
        self.assertEqual(violations, [])

    def test_partial_prior_run_is_completed_idempotently(self):
        # Simulate an interruption that completed the volumes rebuild but
        # not the issues rebuild (or a database that otherwise already has
        # a nullable volumes.comicvine_id but not yet issues.comicvine_id).
        self.connection.executescript("""
            PRAGMA foreign_keys = OFF;
            CREATE TEMPORARY TABLE temp_volumes_50 AS SELECT * FROM volumes;
            DROP TABLE volumes;
            CREATE TABLE volumes(
                id INTEGER PRIMARY KEY,
                comicvine_id INTEGER,
                title VARCHAR(255) NOT NULL,
                alt_title VARCHAR(255),
                year INTEGER(5),
                publisher VARCHAR(255),
                volume_number INTEGER(8) DEFAULT 1,
                description TEXT,
                site_url TEXT NOT NULL DEFAULT "",
                monitored BOOL NOT NULL DEFAULT 0,
                monitor_new_issues BOOL NOT NULL DEFAULT 1,
                root_folder INTEGER NOT NULL,
                folder TEXT,
                custom_folder BOOL NOT NULL DEFAULT 0,
                last_cv_fetch INTEGER(8) DEFAULT 0,
                special_version VARCHAR(255),
                special_version_locked BOOL NOT NULL DEFAULT 0,
                FOREIGN KEY (root_folder) REFERENCES root_folders(id)
            );
            INSERT INTO volumes SELECT * FROM temp_volumes_50;
            DROP TABLE temp_volumes_50;
            PRAGMA foreign_keys = ON;
        """)
        self.assertFalse(self._notnull('volumes'))
        self.assertTrue(self._notnull('issues'))

        db_migration._migrate_relax_comicvine_id_not_null()

        self.assertFalse(self._notnull('volumes'))
        self.assertFalse(self._notnull('issues'))

        volume = self.connection.execute(
            'SELECT * FROM volumes WHERE id = 1;'
        ).fetchone()
        self.assertEqual(volume['comicvine_id'], 4050)
        issue = self.connection.execute(
            'SELECT * FROM issues WHERE id = 1;'
        ).fetchone()
        self.assertEqual(issue['comicvine_id'], 9001)


class fresh_install_schema(unittest.TestCase):
    """A fresh install (DB_SCHEMA) must independently produce the same
    nullability as an upgraded database (migration 50).
    """

    def test_comicvine_id_is_nullable_on_fresh_install(self):
        connection = sqlite3.connect(':memory:')
        try:
            connection.executescript(db.DB_SCHEMA)

            volumes_notnull = any(
                row[1] == 'comicvine_id' and row[3]
                for row in connection.execute("PRAGMA table_info(volumes);")
            )
            issues_notnull = any(
                row[1] == 'comicvine_id' and row[3]
                for row in connection.execute("PRAGMA table_info(issues);")
            )

            self.assertFalse(volumes_notnull)
            self.assertFalse(issues_notnull)

            # And a Metron-native row (no ComicVine cross-link) is legal on
            # a fresh install too.
            connection.execute(
                "INSERT INTO root_folders(folder) VALUES ('/comics');"
            )
            connection.execute(
                "INSERT INTO volumes(comicvine_id, title, root_folder) "
                "VALUES (NULL, 'Metron Only', 1);"
            )
        finally:
            connection.close()


if __name__ == '__main__':
    unittest.main()
