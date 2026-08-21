import sqlite3
import unittest
from asyncio import sleep
from datetime import date, timedelta
from unittest.mock import AsyncMock, patch

from backend.base.definitions import WeeklyReleaseSource
from backend.features import pull_list as pull_list_module
from backend.features.pull_list import (GetComicsWeeklyReleases,
                                        MylarWeeklyReleases,
                                        WeeklyReleaseSources,
                                        _merge_release_sources,
                                        check_weekly_pull_list,
                                        get_publishers, get_pull_list,
                                        match_releases_to_library,
                                        set_publisher_subscription)
from backend.internals.db import KapowarrCursor


def _current_week():
    today = date.today()
    return (today - timedelta(days=today.weekday())).isoformat()


def _previous_week():
    return (
        date.fromisoformat(_current_week()) - timedelta(weeks=1)
    ).isoformat()


def _release(series, issue_number='1', year=None,
             source='Mylar Release Provider',
             link='http://x/1', publisher='DC Comics', comicvine_volume_id=None,
             comicvine_issue_id=None):
    return {
        'series': series,
        'issue_number': issue_number,
        'year': year,
        'link': link,
        'source': source,
        'publisher': publisher,
        'release_date': date.today().isoformat(),
        'cover_date': None,
        'week_start': _current_week(),
        'comicvine_volume_id': comicvine_volume_id,
        'comicvine_issue_id': comicvine_issue_id,
        'availability_source': None,
        'availability_link': None
    }


def _volume(id, title, monitored=True, comicvine_id=None, year=None):
    return {
        'id': id,
        'title': title,
        'monitored': monitored,
        'comicvine_id': comicvine_id,
        'year': year
    }


class release_to_library_matching(unittest.TestCase):
    def test_exact_title_match(self):
        entries = match_releases_to_library(
            [_release('Batman', '123')], [_volume(1, 'Batman')]
        )

        self.assertEqual(entries[0]['volume_id'], 1)
        self.assertEqual(entries[0]['volume_title'], 'Batman')
        self.assertEqual(entries[0]['issue_number'], '123')

    def test_unmatched_release_is_kept_in_catalogue(self):
        entries = match_releases_to_library(
            [_release('Batman', '123')], [_volume(1, 'Daredevil')]
        )

        self.assertEqual(len(entries), 1)
        self.assertIsNone(entries[0]['volume_id'])

    def test_getcomics_is_merged_as_availability(self):
        catalogue = _release('Batman', '123')
        available = _release(
            'Batman', '123', source='GetComics', publisher=None
        )
        available['availability_source'] = 'GetComics'
        available['availability_link'] = 'https://getcomics.example/batman'

        releases = _merge_release_sources([[catalogue], [available]])

        self.assertEqual(len(releases), 1)
        self.assertEqual(releases[0]['source'], 'Mylar Release Provider')
        self.assertEqual(releases[0]['availability_source'], 'GetComics')

    def test_comicvine_id_takes_precedence_over_title(self):
        entries = match_releases_to_library(
            [_release('Renamed Batman', comicvine_volume_id=4050)],
            [_volume(1, 'Batman', comicvine_id=4050)]
        )

        self.assertEqual(entries[0]['volume_id'], 1)

    def test_year_prevents_wrong_title_match(self):
        entries = match_releases_to_library(
            [_release('Batman', year=2025)],
            [_volume(1, 'Batman', year=2016)]
        )

        self.assertIsNone(entries[0]['volume_id'])


class weekly_release_sources_registry(unittest.TestCase):
    def test_builtin_sources_are_registered(self):
        self.assertIn(MylarWeeklyReleases, WeeklyReleaseSources.sources)
        self.assertIn(GetComicsWeeklyReleases, WeeklyReleaseSources.sources)

    def test_get_active_returns_instances(self):
        active = WeeklyReleaseSources.get_active()
        self.assertTrue(any(isinstance(s, MylarWeeklyReleases) for s in active))

    def test_register_adds_a_new_source(self):
        class _DummySource(WeeklyReleaseSource):
            async def fetch(self, session, requested_date=None):
                return []

        original_sources = list(WeeklyReleaseSources.sources)
        try:
            WeeklyReleaseSources.register(_DummySource)
            self.assertIn(_DummySource, WeeklyReleaseSources.sources)
        finally:
            WeeklyReleaseSources.sources = original_sources


class weekly_release_source_timeout(unittest.IsolatedAsyncioTestCase):
    async def test_slow_source_is_bounded_and_skipped(self):
        class _SlowSource(WeeklyReleaseSource):
            async def fetch(self, session, requested_date=None):
                await sleep(0.05)
                return [_release('Too Late')]

        with patch.object(
            pull_list_module, 'WEEKLY_RELEASE_FETCH_TIMEOUT', 0.001
        ):
            releases = await pull_list_module._fetch_release_source(
                _SlowSource(), None, date.today()
            )

        self.assertEqual(releases, [])


class weekly_pull_list_persistence(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(':memory:')
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript("""
            CREATE TABLE root_folders(id INTEGER PRIMARY KEY, folder TEXT);
            CREATE TABLE volumes(
                id INTEGER PRIMARY KEY,
                comicvine_id INTEGER,
                title VARCHAR(255) NOT NULL,
                year INTEGER,
                monitored BOOL NOT NULL DEFAULT 0
            );
            CREATE TABLE issues(
                id INTEGER PRIMARY KEY,
                volume_id INTEGER,
                comicvine_id INTEGER,
                issue_number VARCHAR(20),
                calculated_issue_number REAL,
                monitored BOOL NOT NULL DEFAULT 0
            );
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
        self.connection.execute(
            "INSERT INTO volumes VALUES (1, 4050, 'Batman', 2024, 1);"
        )
        self.connection.execute(
            "INSERT INTO issues VALUES (10, 1, 9001, '123', 123.0, 1);"
        )
        self.connection.commit()

        self.get_db_patch = patch.object(
            pull_list_module, 'get_db',
            side_effect=lambda *a, **k: self._cursor()
        )
        self.get_db_patch.start()

    def _cursor(self):
        cursor = KapowarrCursor(self.connection)
        cursor.row_factory = sqlite3.Row
        return cursor

    def tearDown(self):
        self.get_db_patch.stop()
        self.connection.close()

    def _check(self, releases):
        with patch.object(
            pull_list_module, '_fetch_all_weekly_releases',
            new=AsyncMock(return_value=releases)
        ), patch.object(
            pull_list_module.Library, 'get_public_volumes',
            return_value=[_volume(
                1, 'Batman', comicvine_id=4050, year=2024
            )]
        ):
            return check_weekly_pull_list()

    def test_check_stores_full_catalogue_and_issue_match(self):
        entries = self._check([
            _release('Batman', '123', 2024,
                     comicvine_volume_id=4050, comicvine_issue_id=9001),
            _release('New Series', '1', 2026, publisher='Image Comics')
        ])

        self.assertEqual(len(entries), 2)
        stored = get_pull_list(_current_week())
        self.assertEqual(len(stored), 2)
        batman = next(row for row in stored if row['release_title'] == 'Batman')
        self.assertEqual(batman['volume_id'], 1)
        self.assertEqual(batman['issue_id'], 10)
        self.assertTrue(any(row['volume_id'] is None for row in stored))

    def test_empty_source_preserves_previous_results_and_fails(self):
        self._check([_release('Batman')])
        with self.assertRaisesRegex(
            RuntimeError, 'previous pull list was kept'
        ):
            self._check([])
        self.assertEqual(len(get_pull_list(_current_week())), 1)

    def test_missing_current_week_preserves_previous_results_and_fails(self):
        self._check([_release('Batman')])
        previous = _release('Old Batman')
        previous['week_start'] = _previous_week()

        with self.assertRaisesRegex(
            RuntimeError, 'No current-week publisher releases'
        ):
            self._check([previous])

        stored = get_pull_list(_current_week())
        self.assertEqual([row['release_title'] for row in stored], ['Batman'])

    def test_availability_only_result_does_not_replace_catalogue(self):
        self._check([_release('Batman')])
        available = _release(
            'Batman', source='GetComics', publisher=None
        )
        available['availability_source'] = 'GetComics'
        available['availability_link'] = 'https://getcomics.example/batman'

        with self.assertRaisesRegex(
            RuntimeError, 'No current-week publisher releases'
        ):
            self._check([available])

        self.assertEqual(len(get_pull_list(_current_week())), 1)

    def test_publisher_subscription_is_returned_with_counts(self):
        self._check([_release('Batman'), _release('Nightwing')])
        set_publisher_subscription('DC Comics', 1, True)

        publishers = get_publishers()

        self.assertEqual(publishers[0]['publisher'], 'DC Comics')
        self.assertEqual(publishers[0]['release_count'], 2)
        self.assertEqual(
            publishers[0]['release_counts'][_current_week()], 2
        )
        self.assertEqual(publishers[0]['auto_search'], 1)

    def test_publisher_counts_are_broken_out_by_week(self):
        current = _release('Batman')
        previous = _release('Nightwing')
        previous['week_start'] = _previous_week()
        self._check([current, previous])

        publisher = get_publishers()[0]

        self.assertEqual(publisher['release_count'], 2)
        self.assertEqual(publisher['release_counts'][_current_week()], 1)
        self.assertEqual(publisher['release_counts'][_previous_week()], 1)


if __name__ == '__main__':
    unittest.main()
