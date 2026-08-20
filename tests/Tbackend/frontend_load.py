# -*- coding: utf-8 -*-

"""
Regression tests for what the web UI has to pull down and wait on to render a
page. Everything here exists because it was measured to be slow on a tablet
against a real library, not because it looked untidy.
"""

import sqlite3
import unittest
from io import BytesIO
from tempfile import TemporaryDirectory
from unittest.mock import patch

from flask import Flask

import frontend.api as api_module
from backend.implementations import volumes as volumes_module
from backend.implementations.volumes import Library
from backend.internals.db import (CONNECTION_PRAGMAS, DB_SCHEMA,
                                  DBConnection, DBConnectionManager,
                                  KapowarrCursor, set_db_location)


class connection_pragmas(unittest.TestCase):
    """Every connection, not just the one that ran `setup_db`, has to opt into
    the settings that keep a write-heavy background job from stalling reads.
    """

    def setUp(self):
        self.folder = TemporaryDirectory()
        self.previous_file = DBConnection.file
        set_db_location(self.folder.name)
        # `DBConnection` is one instance per thread, so an earlier test leaving
        # one open would hand it back here instead of opening a new one.
        DBConnectionManager.close_connection_of_thread()

    def tearDown(self):
        DBConnectionManager.close_connection_of_thread()
        DBConnection.file = self.previous_file
        self.folder.cleanup()

    def _pragma(self, connection, name):
        cursor = sqlite3.Connection.cursor(connection)
        return cursor.execute('PRAGMA ' + name).fetchone()[0]

    def test_new_connections_apply_the_performance_pragmas(self):
        connection = DBConnection(timeout=5.0)

        # FULL (2) means an fsync per commit. Import and refresh-and-scan commit
        # in tight loops, so FULL turns them into an I/O storm that the UI's own
        # reads then queue behind.
        self.assertEqual(self._pragma(connection, 'synchronous'), 1)
        # Negative == KiB, so this is a cache larger than the 2 MiB default.
        self.assertLessEqual(self._pragma(connection, 'cache_size'), -8000)
        # 2 == MEMORY.
        self.assertEqual(self._pragma(connection, 'temp_store'), 2)
        # Still on -- the pragmas were added alongside it, not instead of it.
        self.assertEqual(self._pragma(connection, 'foreign_keys'), 1)

    def test_foreign_keys_pragma_is_still_declared(self):
        self.assertIn('PRAGMA foreign_keys = ON;', CONNECTION_PRAGMAS)


class library_listing_payload(unittest.TestCase):
    """`/api/volumes` returns every volume at once, so anything included in it
    is multiplied by the size of the library.
    """

    def setUp(self):
        self.connection = sqlite3.connect(':memory:')
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(DB_SCHEMA)
        self.connection.executescript("""
            INSERT INTO root_folders VALUES (1, '/comics');
            INSERT INTO volumes (
                id, comicvine_id, title, alt_title, year, publisher,
                volume_number, description, site_url, monitored,
                monitor_new_issues, root_folder, folder, custom_folder,
                last_cv_fetch, special_version, special_version_locked
            ) VALUES (
                1, 4050001, 'Saga', NULL, 2012, 'Image', 1,
                'A very long ComicVine description that nothing in the library
                 view ever renders.', '', 1, 1, 1, '/comics/Saga', 0, 0,
                NULL, 0
            );
            INSERT INTO issues (
                id, volume_id, comicvine_id, issue_number,
                calculated_issue_number, title, date, description, monitored
            ) VALUES (1, 1, 900001, '1', 1.0, 'Chapter One', '2012-03-14',
                'issue description', 1);
        """)
        self.patch = patch.object(
            volumes_module,
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

    def test_listing_leaves_out_the_description_blob(self):
        volumes = Library.get_public_volumes()

        self.assertEqual(len(volumes), 1)
        self.assertNotIn('description', volumes[0])

    def test_listing_still_carries_what_the_library_view_draws(self):
        volume = Library.get_public_volumes()[0]

        for key in (
            'id', 'title', 'year', 'publisher', 'volume_number', 'monitored',
            'issue_count', 'issue_count_monitored', 'issues_downloaded',
            'issues_downloaded_monitored'
        ):
            self.assertIn(key, volume)
        self.assertEqual(volume['title'], 'Saga')
        self.assertEqual(volume['issue_count'], 1)


class _FakeVolume:
    def __init__(self, cover: bytes) -> None:
        self._cover = cover

    def get_cover(self) -> BytesIO:
        return BytesIO(self._cover)


class cover_caching(unittest.TestCase):
    """The library page asks for one cover per volume on every load. Without a
    validator the browser has to re-download all of them every time.
    """

    COVER = b'\xff\xd8\xff\xe0 not really a jpeg, but bytes are bytes'

    def setUp(self):
        app = Flask(__name__)
        app.register_blueprint(api_module.api, url_prefix='/api')
        self.client = app.test_client()

        # `auth` only needs to not reject us; what is under test is the caching.
        patches = (
            patch.object(api_module, 'extract_key', return_value='key'),
            patch.object(api_module, 'StartTypeHandlers'),
            patch.object(
                api_module.Library, 'get_volume',
                return_value=_FakeVolume(self.COVER)
            ),
        )
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def test_cover_is_sent_with_a_validator_and_a_max_age(self):
        response = self.client.get('/api/volumes/1/cover?api_key=key')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, self.COVER)
        self.assertTrue(response.headers['ETag'])
        self.assertIn('max-age=', response.headers['Cache-Control'])
        self.assertIn('private', response.headers['Cache-Control'])

    def test_matching_etag_answers_304_without_the_body(self):
        etag = self.client.get(
            '/api/volumes/1/cover?api_key=key'
        ).headers['ETag']

        response = self.client.get(
            '/api/volumes/1/cover?api_key=key',
            headers={'If-None-Match': etag}
        )

        self.assertEqual(response.status_code, 304)
        self.assertEqual(response.data, b'')
        self.assertEqual(response.headers['ETag'], etag)

    def test_a_different_cover_gets_a_different_etag(self):
        first = self.client.get(
            '/api/volumes/1/cover?api_key=key'
        ).headers['ETag']

        with patch.object(
            api_module.Library, 'get_volume',
            return_value=_FakeVolume(b'a replaced cover')
        ):
            second = self.client.get(
                '/api/volumes/1/cover?api_key=key'
            ).headers['ETag']

        self.assertNotEqual(first, second)

    def test_stale_etag_gets_the_new_cover_rather_than_a_304(self):
        response = self.client.get(
            '/api/volumes/1/cover?api_key=key',
            headers={'If-None-Match': '"0000000000000000000000000000cafe"'}
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, self.COVER)
