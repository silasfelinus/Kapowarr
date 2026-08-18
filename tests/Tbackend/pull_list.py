import sqlite3
import unittest
from unittest.mock import AsyncMock, patch

from backend.base.definitions import WeeklyReleaseSource
from backend.features import pull_list as pull_list_module
from backend.features.pull_list import (GetComicsWeeklyReleases,
                                        WeeklyReleaseSources,
                                        check_weekly_pull_list, get_pull_list,
                                        match_releases_to_library)
from backend.internals.db import KapowarrCursor


def _release(series, issue_number='1', year=None, source='GetComics',
             link='http://x/1'):
    return {
        'series': series,
        'issue_number': issue_number,
        'year': year,
        'link': link,
        'source': source
    }


def _volume(id, title, monitored=True):
    return {'id': id, 'title': title, 'monitored': monitored}


# =====================
# match_releases_to_library()
# =====================
class release_to_library_matching(unittest.TestCase):
    def test_exact_title_match(self):
        releases = [_release('Batman', '123')]
        volumes = [_volume(1, 'Batman')]

        matches = match_releases_to_library(releases, volumes)

        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]['volume_id'], 1)
        self.assertEqual(matches[0]['volume_title'], 'Batman')
        self.assertEqual(matches[0]['issue_number'], '123')
        self.assertEqual(matches[0]['release_title'], 'Batman')

    def test_no_match_when_title_differs(self):
        releases = [_release('Batman', '123')]
        volumes = [_volume(1, 'Daredevil')]

        matches = match_releases_to_library(releases, volumes)

        self.assertEqual(matches, [])

    def test_no_monitored_volumes_returns_empty(self):
        releases = [_release('Batman', '123')]

        matches = match_releases_to_library(releases, [])

        self.assertEqual(matches, [])

    def test_case_and_whitespace_insensitive_match(self):
        releases = [_release('  batman  ', '123')]
        volumes = [_volume(1, 'Batman')]

        matches = match_releases_to_library(releases, volumes)

        self.assertEqual(len(matches), 1)

    def test_multiple_releases_matched_independently(self):
        releases = [
            _release('Batman', '123'),
            _release('Daredevil', '10'),
            _release('Nothing Monitored', '1')
        ]
        volumes = [_volume(1, 'Batman'), _volume(2, 'Daredevil')]

        matches = match_releases_to_library(releases, volumes)

        self.assertEqual(len(matches), 2)
        self.assertEqual(
            {m['volume_title'] for m in matches}, {'Batman', 'Daredevil'}
        )

    def test_release_only_credited_to_first_matching_volume(self):
        releases = [_release('Batman', '123')]
        volumes = [_volume(1, 'Batman'), _volume(2, 'Batman')]

        matches = match_releases_to_library(releases, volumes)

        # Only one match is produced, not one per monitored volume with an
        # identical title.
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]['volume_id'], 1)


# =====================
# WeeklyReleaseSources registry
# =====================
class weekly_release_sources_registry(unittest.TestCase):
    def test_getcomics_is_registered_by_default(self):
        self.assertIn(GetComicsWeeklyReleases, WeeklyReleaseSources.sources)

    def test_get_active_returns_instances(self):
        active = WeeklyReleaseSources.get_active()
        self.assertTrue(
            any(isinstance(s, GetComicsWeeklyReleases) for s in active)
        )

    def test_register_adds_a_new_source(self):
        class _DummySource(WeeklyReleaseSource):
            async def fetch(self, session):
                return []

        original_sources = list(WeeklyReleaseSources.sources)
        try:
            WeeklyReleaseSources.register(_DummySource)
            self.assertIn(_DummySource, WeeklyReleaseSources.sources)
        finally:
            WeeklyReleaseSources.sources = original_sources


# =====================
# check_weekly_pull_list() / get_pull_list(), with an in-memory DB
# =====================
class weekly_pull_list_persistence(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(':memory:')
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript("""
            CREATE TABLE volumes(
                id INTEGER PRIMARY KEY,
                title VARCHAR(255) NOT NULL,
                monitored BOOL NOT NULL DEFAULT 0
            );
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
        """)
        self.connection.execute(
            "INSERT INTO volumes(id, title, monitored) VALUES (1, 'Batman', 1);"
        )
        self.connection.commit()

        self.get_db_patch = patch.object(
            pull_list_module, 'get_db', side_effect=lambda *a, **k: self._cursor()
        )
        self.get_db_patch.start()

    def _cursor(self) -> KapowarrCursor:
        c = KapowarrCursor(self.connection)
        c.row_factory = sqlite3.Row
        return c

    def tearDown(self):
        self.get_db_patch.stop()
        self.connection.close()

    def test_check_stores_matches_and_get_pull_list_reads_them_back(self):
        releases = [_release('Batman', '123', year=2024)]
        with patch.object(
            pull_list_module, '_fetch_all_weekly_releases',
            new=AsyncMock(return_value=releases)
        ), patch.object(
            pull_list_module.Library, 'get_public_volumes',
            return_value=[_volume(1, 'Batman')]
        ):
            matches = check_weekly_pull_list()

        self.assertEqual(len(matches), 1)

        stored = get_pull_list()
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0]['volume_id'], 1)
        self.assertEqual(stored[0]['volume_title'], 'Batman')
        self.assertEqual(stored[0]['issue_number'], '123')
        self.assertEqual(stored[0]['year'], 2024)
        self.assertEqual(stored[0]['source'], 'GetComics')

    def test_check_replaces_previous_results(self):
        with patch.object(
            pull_list_module, '_fetch_all_weekly_releases',
            new=AsyncMock(return_value=[_release('Batman', '1')])
        ), patch.object(
            pull_list_module.Library, 'get_public_volumes',
            return_value=[_volume(1, 'Batman')]
        ):
            check_weekly_pull_list()

        self.assertEqual(len(get_pull_list()), 1)

        # A second, empty check should clear the previous week's entries
        # rather than accumulating alongside them.
        with patch.object(
            pull_list_module, '_fetch_all_weekly_releases',
            new=AsyncMock(return_value=[])
        ), patch.object(
            pull_list_module.Library, 'get_public_volumes',
            return_value=[_volume(1, 'Batman')]
        ):
            check_weekly_pull_list()

        self.assertEqual(get_pull_list(), [])

    def test_no_matches_stores_nothing(self):
        with patch.object(
            pull_list_module, '_fetch_all_weekly_releases',
            new=AsyncMock(return_value=[_release('Not Monitored', '1')])
        ), patch.object(
            pull_list_module.Library, 'get_public_volumes',
            return_value=[_volume(1, 'Batman')]
        ):
            matches = check_weekly_pull_list()

        self.assertEqual(matches, [])
        self.assertEqual(get_pull_list(), [])

    def test_get_pull_list_orders_most_recently_checked_first(self):
        self.connection.executescript("""
            INSERT INTO volumes(id, title, monitored) VALUES (2, 'Daredevil', 1);
            INSERT INTO pull_list_entries(
                volume_id, issue_number, release_title, year,
                source, link, checked_at
            ) VALUES (1, '1', 'Batman', 2023, 'GetComics', 'http://x/1', 100);
            INSERT INTO pull_list_entries(
                volume_id, issue_number, release_title, year,
                source, link, checked_at
            ) VALUES (2, '1', 'Daredevil', 2023, 'GetComics', 'http://x/2', 200);
        """)
        self.connection.commit()

        result = get_pull_list()

        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]['volume_title'], 'Daredevil')
        self.assertEqual(result[1]['volume_title'], 'Batman')


if __name__ == '__main__':
    unittest.main()
