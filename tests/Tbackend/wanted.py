import sqlite3
import unittest
from unittest.mock import patch

from backend.features import wanted as wanted_module
from backend.features.wanted import (DEFAULT_WANTED_LIMIT,
                                     MAX_WANTED_LIMIT, get_wanted_issues)
from backend.internals.db import KapowarrCursor


class global_wanted_issue_query(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(':memory:')
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript("""
            CREATE TABLE volumes(
                id INTEGER PRIMARY KEY,
                title VARCHAR(255) NOT NULL,
                year INTEGER(5),
                publisher VARCHAR(255),
                monitored BOOL NOT NULL DEFAULT 0
            );
            CREATE TABLE issues(
                id INTEGER PRIMARY KEY,
                volume_id INTEGER NOT NULL,
                issue_number VARCHAR(20) NOT NULL,
                calculated_issue_number FLOAT(20) NOT NULL,
                title VARCHAR(255),
                date VARCHAR(10),
                monitored BOOL NOT NULL DEFAULT 1
            );
            CREATE TABLE files(
                id INTEGER PRIMARY KEY,
                filepath TEXT UNIQUE NOT NULL
            );
            CREATE TABLE issues_files(
                file_id INTEGER NOT NULL,
                issue_id INTEGER NOT NULL
            );

            INSERT INTO volumes(id, title, year, publisher, monitored)
                VALUES (1, 'Batman', 2011, 'DC Comics', 1);
            INSERT INTO volumes(id, title, year, publisher, monitored)
                VALUES (2, 'Unmonitored Volume', 2011, 'DC Comics', 0);

            -- Volume 1, monitored issue, no file -> wanted
            INSERT INTO issues(
                id, volume_id, issue_number, calculated_issue_number,
                title, date, monitored
            ) VALUES (1, 1, '1', 1.0, 'Batman Begins', '2011-09-01', 1);

            -- Volume 1, monitored issue, has a file -> not wanted
            INSERT INTO issues(
                id, volume_id, issue_number, calculated_issue_number,
                title, date, monitored
            ) VALUES (2, 1, '2', 2.0, 'The Court of Owls', '2011-10-01', 1);
            INSERT INTO files(id, filepath) VALUES (1, '/comics/Batman/002.cbz');
            INSERT INTO issues_files(file_id, issue_id) VALUES (1, 2);

            -- Volume 1, unmonitored issue, no file -> not wanted
            INSERT INTO issues(
                id, volume_id, issue_number, calculated_issue_number,
                title, date, monitored
            ) VALUES (3, 1, '3', 3.0, 'Death of the Family', '2012-11-01', 0);

            -- Volume 2 (unmonitored volume), monitored issue, no file
            -- -> not wanted, because the volume itself isn't monitored
            INSERT INTO issues(
                id, volume_id, issue_number, calculated_issue_number,
                title, date, monitored
            ) VALUES (4, 2, '1', 1.0, 'Some Issue', '2013-01-01', 1);
        """)
        self.connection.commit()

        self.get_db_patch = patch.object(
            wanted_module, 'get_db', side_effect=lambda *a, **k: self._cursor()
        )
        self.get_db_patch.start()

    def _cursor(self) -> KapowarrCursor:
        c = KapowarrCursor(self.connection)
        c.row_factory = sqlite3.Row
        return c

    def tearDown(self):
        self.get_db_patch.stop()
        self.connection.close()

    def test_only_monitored_issues_in_monitored_volumes_without_a_file(self):
        result = get_wanted_issues()

        self.assertEqual(result['total'], 1)
        self.assertEqual(len(result['items']), 1)
        self.assertEqual(result['items'][0]['issue_id'], 1)
        self.assertEqual(result['items'][0]['volume_id'], 1)
        self.assertEqual(result['items'][0]['volume_title'], 'Batman')
        self.assertEqual(result['items'][0]['publisher'], 'DC Comics')

    def test_default_response_shape(self):
        result = get_wanted_issues()

        self.assertEqual(result['offset'], 0)
        self.assertEqual(result['limit'], DEFAULT_WANTED_LIMIT)
        self.assertEqual(result['query'], '')

    def test_query_filters_by_volume_title(self):
        self.connection.execute(
            "UPDATE issues SET monitored = 1 WHERE id = 3;"
        )
        self.connection.commit()

        result = get_wanted_issues(query='batman')

        self.assertEqual(result['total'], 2)
        self.assertTrue(
            all(i['volume_title'] == 'Batman' for i in result['items'])
        )

    def test_query_filters_by_issue_title(self):
        result = get_wanted_issues(query='begins')

        self.assertEqual(result['total'], 1)
        self.assertEqual(result['items'][0]['issue_id'], 1)

    def test_query_matching_nothing_returns_empty(self):
        result = get_wanted_issues(query='nonexistent-series-xyz')

        self.assertEqual(result['total'], 0)
        self.assertEqual(result['items'], [])

    def test_pagination_offset_and_limit_are_honored(self):
        self.connection.executescript("""
            UPDATE issues SET monitored = 1 WHERE id = 3;
            INSERT INTO issues(
                id, volume_id, issue_number, calculated_issue_number,
                title, date, monitored
            ) VALUES (5, 1, '4', 4.0, 'Another Missing Issue', '2013-02-01', 1);
        """)
        self.connection.commit()

        first_page = get_wanted_issues(limit=1, offset=0)
        second_page = get_wanted_issues(limit=1, offset=1)

        self.assertEqual(first_page['total'], 3)
        self.assertEqual(len(first_page['items']), 1)
        self.assertEqual(len(second_page['items']), 1)
        self.assertNotEqual(
            first_page['items'][0]['issue_id'],
            second_page['items'][0]['issue_id']
        )

    def test_limit_is_capped_by_caller_supplied_value(self):
        # get_wanted_issues() itself doesn't clamp; that's the API route's
        # job (see frontend.wanted). It should still just use whatever it's
        # given.
        result = get_wanted_issues(limit=MAX_WANTED_LIMIT)
        self.assertEqual(result['limit'], MAX_WANTED_LIMIT)


if __name__ == '__main__':
    unittest.main()
