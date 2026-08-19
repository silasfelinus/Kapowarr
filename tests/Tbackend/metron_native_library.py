import sqlite3
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from backend.base.definitions import MonitorScheme, SpecialVersion
from backend.implementations import volumes
from backend.internals.db import KapowarrCursor


class metron_native_library_add(unittest.TestCase):
    def setUp(self):
        sqlite3.register_adapter(SpecialVersion, lambda value: value.value)
        self.connection = sqlite3.connect(':memory:')
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript("""
            CREATE TABLE volumes(
                id INTEGER PRIMARY KEY,
                comicvine_id INTEGER,
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
                special_version_locked BOOL NOT NULL DEFAULT 0
            );
            CREATE TABLE volumes_covers(
                volume_id INTEGER UNIQUE NOT NULL,
                cover BLOB,
                provider_id TEXT,
                external_id TEXT,
                source_url TEXT
            );
            CREATE TABLE issues(
                id INTEGER PRIMARY KEY,
                volume_id INTEGER NOT NULL,
                comicvine_id INTEGER UNIQUE,
                issue_number TEXT NOT NULL,
                calculated_issue_number FLOAT NOT NULL,
                title TEXT,
                date TEXT,
                description TEXT,
                monitored BOOL NOT NULL DEFAULT 1
            );
            CREATE TABLE volume_external_ids(
                volume_id INTEGER NOT NULL,
                provider_id TEXT NOT NULL,
                external_id TEXT NOT NULL,
                source_url TEXT,
                updated_at INTEGER NOT NULL,
                PRIMARY KEY (volume_id, provider_id),
                UNIQUE (provider_id, external_id)
            );
            CREATE TABLE issue_external_ids(
                issue_id INTEGER NOT NULL,
                provider_id TEXT NOT NULL,
                external_id TEXT NOT NULL,
                source_url TEXT,
                updated_at INTEGER NOT NULL,
                PRIMARY KEY (issue_id, provider_id),
                UNIQUE (provider_id, external_id)
            );
        """)

    def tearDown(self):
        self.connection.close()

    def _cursor(self):
        cursor = KapowarrCursor(self.connection)
        cursor.row_factory = sqlite3.Row
        return cursor

    def test_adds_and_refreshes_native_ids_without_inventing_comicvine_ids(self):
        provider = MagicMock()
        provider.fetch_volume = AsyncMock(return_value={
            'provider_id': 'metron',
            'external_id': 'series-9',
            'comicvine_id': None,
            'title': 'Metron Native',
            'year': 2026,
            'volume_number': 1,
            'cover_link': 'https://example.test/cover.jpg',
            'cover_source': {
                'provider_id': 'metron',
                'external_id': 'series-9',
                'source_url': 'https://example.test/cover.jpg'
            },
            'cover': b'cover',
            'description': '',
            'site_url': 'https://metron.cloud/series/9/',
            'aliases': [],
            'publisher': 'Independent',
            'issue_count': 2,
            'translated': False,
            'already_added': None,
            'issues': [
                {
                    'provider_id': 'metron',
                    'external_id': f'issue-{number}',
                    'volume_external_id': 'series-9',
                    'comicvine_id': None,
                    'volume_id': None,
                    'issue_number': str(number),
                    'calculated_issue_number': float(number),
                    'title': None,
                    'date': '2026-08-19',
                    'description': ''
                }
                for number in (1, 2)
            ]
        })
        settings = MagicMock()
        settings.sv.create_empty_volume_folders = False

        with patch.object(
            volumes, 'get_db', side_effect=self._cursor
        ), patch(
            'backend.features.metadata.get_db', side_effect=self._cursor
        ), patch.object(
            volumes, 'get_metadata_provider', return_value=provider
        ), patch.object(
            volumes.RootFolders, 'get_one',
            return_value=SimpleNamespace(id=1, folder='/comics')
        ), patch.object(
            volumes, 'determine_special_version',
            return_value=SpecialVersion.NORMAL
        ), patch(
            'backend.implementations.naming.generate_volume_folder_path',
            return_value='/comics/Metron Native (2026)'
        ), patch.object(
            volumes, 'Settings', return_value=settings
        ), patch.object(
            volumes, 'mass_process_files'
        ), patch.object(
            volumes.Volume, 'apply_monitor_scheme'
        ), patch.object(
            volumes, 'scan_files'
        ), patch.object(
            volumes, 'commit', side_effect=self.connection.commit
        ):
            volume_id = volumes.Library.add(
                None, 1, True, MonitorScheme.ALL,
                metadata_provider_id='metron',
                metadata_external_id='series-9'
            )
            updated = dict(provider.fetch_volume.return_value)
            updated['title'] = 'Metron Native: Updated'
            updated['issue_count'] = 3
            updated['issues'] = list(updated['issues']) + [{
                'provider_id': 'metron',
                'external_id': 'issue-3',
                'volume_external_id': 'series-9',
                'comicvine_id': None,
                'volume_id': None,
                'issue_number': '3',
                'calculated_issue_number': 3.0,
                'title': None,
                'date': '2026-09-19',
                'description': ''
            }]
            provider.fetch_volumes = AsyncMock(return_value=[updated])
            volumes.refresh_and_scan(volume_id, allow_skipping=False)

        volume = self.connection.execute(
            'SELECT * FROM volumes WHERE id = ?;', (volume_id,)
        ).fetchone()
        issues = list(self.connection.execute(
            'SELECT * FROM issues WHERE volume_id = ? ORDER BY id;',
            (volume_id,)
        ))
        identities = list(self.connection.execute("""
            SELECT provider_id, external_id
            FROM issue_external_ids
            ORDER BY external_id;
        """))

        self.assertIsNone(volume['comicvine_id'])
        self.assertEqual(volume['title'], 'Metron Native: Updated')
        self.assertEqual(len(issues), 3)
        self.assertTrue(all(issue['comicvine_id'] is None for issue in issues))
        self.assertEqual(
            [(row['provider_id'], row['external_id']) for row in identities],
            [
                ('metron', 'issue-1'),
                ('metron', 'issue-2'),
                ('metron', 'issue-3')
            ]
        )
