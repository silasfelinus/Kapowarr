# -*- coding: utf-8 -*-

"""t-056: refresh_and_scan needs a provider-neutral equivalent for
Metron-native volumes/issues (comicvine_id IS NULL), since they can't be
looked up by ComicVine ID. Covers `_refresh_metron_native_volumes`.
"""

import sqlite3
import unittest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

from backend.implementations import volumes
from backend.internals.db import KapowarrCursor


class refresh_metron_native_volumes(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(':memory:')
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript("""
            PRAGMA foreign_keys = ON;

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
                last_cv_fetch INTEGER(8) DEFAULT 0,
                special_version VARCHAR(255),
                special_version_locked BOOL NOT NULL DEFAULT 0
            );
            INSERT INTO volumes(
                id, comicvine_id, title, monitor_new_issues, last_cv_fetch
            ) VALUES (1, NULL, 'Old Title', 1, 1000);

            CREATE TABLE issues(
                id INTEGER PRIMARY KEY,
                volume_id INTEGER NOT NULL,
                comicvine_id INTEGER UNIQUE,
                issue_number VARCHAR(20) NOT NULL,
                calculated_issue_number FLOAT(20) NOT NULL,
                title VARCHAR(255),
                date VARCHAR(10),
                description TEXT,
                monitored BOOL NOT NULL DEFAULT 1
            );

            CREATE TABLE volumes_covers(
                volume_id INTEGER UNIQUE NOT NULL,
                cover BLOB,
                provider_id VARCHAR(50),
                external_id TEXT,
                source_url TEXT
            );
            INSERT INTO volumes_covers VALUES (1, NULL, NULL, NULL, NULL);

            CREATE TABLE volume_external_ids(
                volume_id INTEGER NOT NULL,
                provider_id VARCHAR(50) NOT NULL,
                external_id TEXT NOT NULL,
                source_url TEXT,
                updated_at INTEGER NOT NULL,
                PRIMARY KEY (volume_id, provider_id),
                UNIQUE (provider_id, external_id)
            );
            INSERT INTO volume_external_ids VALUES (
                1, 'metron', '92', NULL, 1000
            );

            CREATE TABLE issue_external_ids(
                issue_id INTEGER NOT NULL,
                provider_id VARCHAR(50) NOT NULL,
                external_id TEXT NOT NULL,
                source_url TEXT,
                updated_at INTEGER NOT NULL,
                PRIMARY KEY (issue_id, provider_id),
                UNIQUE (provider_id, external_id)
            );
        """)

        self.db_patch = patch.object(
            volumes, 'get_db', side_effect=self._cursor
        )
        self.db_patch.start()
        # MetadataIdentityStore lives in backend.features.metadata and
        # resolves its own get_db() independently -- point it at the same
        # connection so identity reads/writes are visible to the test.
        self.metadata_db_patch = patch(
            'backend.features.metadata.get_db', side_effect=self._cursor
        )
        self.metadata_db_patch.start()
        self.commit_patch = patch.object(volumes, 'commit')
        self.commit_patch.start()

    def tearDown(self):
        self.db_patch.stop()
        self.metadata_db_patch.stop()
        self.commit_patch.stop()
        self.connection.close()

    def _cursor(self):
        cursor = KapowarrCursor(self.connection)
        cursor.row_factory = sqlite3.Row
        return cursor

    def _volume_metadata(self, **overrides):
        data = {
            'provider_id': 'metron',
            'external_id': '92',
            'comicvine_id': None,
            'title': 'New Title',
            'year': 2026,
            'volume_number': 1,
            'cover_link': 'https://example.test/cover.jpg',
            'cover_source': {
                'provider_id': 'metron', 'external_id': '92',
                'source_url': 'https://example.test/cover.jpg'
            },
            'cover': b'IMG',
            'description': 'A description',
            'site_url': 'https://metron.cloud/series/92/',
            'aliases': [],
            'publisher': 'Test Publisher',
            'issue_count': 1,
            'translated': False,
            'already_added': None,
            'issues': [{
                'provider_id': 'metron',
                'external_id': '501',
                'volume_external_id': '92',
                'comicvine_id': None,
                'volume_id': None,
                'issue_number': '1',
                'calculated_issue_number': 1.0,
                'title': None,
                'date': '2026-01-01',
                'description': ''
            }]
        }
        data.update(overrides)
        return data

    def test_refreshes_volume_and_inserts_new_issue_with_metron_identity(self):
        metron = AsyncMock()
        metron.fetch_volume.return_value = self._volume_metadata()

        with patch.object(
            volumes, 'get_metadata_provider', return_value=metron
        ), patch.object(
            volumes, 'determine_special_version'
        ) as determine_sv:
            # A plain string stands in for the real SpecialVersion enum: the
            # sqlite3 adapter that lets the real enum bind directly is only
            # registered by setup_db() at app startup, which this focused
            # test doesn't run -- and determine_special_version's actual
            # value isn't what's under test here.
            determine_sv.return_value = 'normal'

            refreshed = volumes._refresh_metron_native_volumes(
                rows=[(1, 1000)],
                current_time=datetime.now(),
                thirty_days_ago=datetime.now() - timedelta(days=30),
                allow_skipping=True,
                single_volume_requested=True
            )

        self.assertEqual(refreshed, [1])
        metron.fetch_volume.assert_awaited_once_with('92')

        volume = self.connection.execute(
            'SELECT * FROM volumes WHERE id = 1;'
        ).fetchone()
        self.assertEqual(volume['title'], 'New Title')
        self.assertIsNone(volume['comicvine_id'])

        cover = self.connection.execute(
            'SELECT * FROM volumes_covers WHERE volume_id = 1;'
        ).fetchone()
        self.assertEqual(cover['provider_id'], 'metron')
        self.assertEqual(cover['external_id'], '92')

        issue = self.connection.execute(
            'SELECT * FROM issues WHERE volume_id = 1;'
        ).fetchone()
        self.assertIsNotNone(issue)
        self.assertIsNone(issue['comicvine_id'])
        self.assertEqual(issue['issue_number'], '1')
        self.assertTrue(issue['monitored'])

        issue_identity = self.connection.execute(
            "SELECT * FROM issue_external_ids WHERE provider_id = 'metron';"
        ).fetchone()
        self.assertEqual(issue_identity['external_id'], '501')
        self.assertEqual(issue_identity['issue_id'], issue['id'])

    def test_dual_writes_comicvine_cross_link_when_metron_reports_one(self):
        metron = AsyncMock()
        metron.fetch_volume.return_value = self._volume_metadata(
            comicvine_id=4050
        )

        with patch.object(
            volumes, 'get_metadata_provider', return_value=metron
        ), patch.object(
            volumes, 'determine_special_version', return_value='normal'
        ):
            volumes._refresh_metron_native_volumes(
                rows=[(1, 1000)],
                current_time=datetime.now(),
                thirty_days_ago=datetime.now() - timedelta(days=30),
                allow_skipping=True,
                single_volume_requested=True
            )

        volume = self.connection.execute(
            'SELECT comicvine_id FROM volumes WHERE id = 1;'
        ).fetchone()
        self.assertEqual(volume['comicvine_id'], 4050)

        cv_identity = self.connection.execute(
            "SELECT * FROM volume_external_ids "
            "WHERE volume_id = 1 AND provider_id = 'comicvine';"
        ).fetchone()
        self.assertEqual(cv_identity['external_id'], '4050')

    def test_updates_existing_issue_matched_by_metron_identity_not_duplicated(self):
        self.connection.execute(
            "INSERT INTO issues("
            "   id, volume_id, comicvine_id, issue_number,"
            "   calculated_issue_number, title"
            ") VALUES (1, 1, NULL, '1', 1.0, 'Old Issue Title');"
        )
        self.connection.execute(
            "INSERT INTO issue_external_ids VALUES (1, 'metron', '501', NULL, 1000);"
        )
        self.connection.commit()

        metron = AsyncMock()
        metron.fetch_volume.return_value = self._volume_metadata()
        metron.fetch_volume.return_value['issues'][0]['title'] = 'Updated Title'

        with patch.object(
            volumes, 'get_metadata_provider', return_value=metron
        ), patch.object(
            volumes, 'determine_special_version', return_value='normal'
        ):
            volumes._refresh_metron_native_volumes(
                rows=[(1, 1000)],
                current_time=datetime.now(),
                thirty_days_ago=datetime.now() - timedelta(days=30),
                allow_skipping=True,
                single_volume_requested=True
            )

        all_issues = self.connection.execute(
            'SELECT * FROM issues WHERE volume_id = 1;'
        ).fetchall()
        self.assertEqual(len(all_issues), 1)
        self.assertEqual(all_issues[0]['title'], 'Updated Title')

    def test_skips_volume_with_no_metron_identity(self):
        self.connection.execute(
            "DELETE FROM volume_external_ids WHERE volume_id = 1;"
        )
        self.connection.commit()

        metron = AsyncMock()

        with patch.object(volumes, 'get_metadata_provider', return_value=metron):
            refreshed = volumes._refresh_metron_native_volumes(
                rows=[(1, 1000)],
                current_time=datetime.now(),
                thirty_days_ago=datetime.now() - timedelta(days=30),
                allow_skipping=True,
                single_volume_requested=False
            )

        self.assertEqual(refreshed, [])
        metron.fetch_volume.assert_not_awaited()

    def test_skips_unchanged_volume_within_thirty_days_when_not_single(self):
        self.connection.execute(
            "INSERT INTO issues("
            "   id, volume_id, comicvine_id, issue_number,"
            "   calculated_issue_number"
            ") VALUES (1, 1, NULL, '1', 1.0);"
        )
        self.connection.commit()

        metron = AsyncMock()
        # issue_count matches the one already-present issue.
        metron.fetch_volume.return_value = self._volume_metadata(issue_count=1)

        # Recently fetched (well within the last 30 days), so the skip
        # condition applies.
        recently_fetched = (datetime.now() - timedelta(days=1)).timestamp()

        with patch.object(volumes, 'get_metadata_provider', return_value=metron):
            refreshed = volumes._refresh_metron_native_volumes(
                rows=[(1, recently_fetched)],
                current_time=datetime.now(),
                thirty_days_ago=datetime.now() - timedelta(days=30),
                allow_skipping=True,
                single_volume_requested=False
            )

        self.assertEqual(refreshed, [])

        # Confirmed unchanged: still the seeded title, not overwritten.
        volume = self.connection.execute(
            'SELECT title FROM volumes WHERE id = 1;'
        ).fetchone()
        self.assertEqual(volume['title'], 'Old Title')

    def test_metron_not_configured_skips_gracefully(self):
        from backend.implementations.metron import MetronError

        with patch.object(
            volumes, 'get_metadata_provider', side_effect=MetronError('no creds')
        ):
            refreshed = volumes._refresh_metron_native_volumes(
                rows=[(1, 1000)],
                current_time=datetime.now(),
                thirty_days_ago=datetime.now() - timedelta(days=30),
                allow_skipping=True,
                single_volume_requested=False
            )

        self.assertEqual(refreshed, [])


if __name__ == '__main__':
    unittest.main()
