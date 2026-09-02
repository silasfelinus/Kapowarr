import unittest
from unittest.mock import patch

from backend.features import publisher_automation
from Tbackend.a_test_database_behaves_like_the_real_one import (
    connect as connect_test_db, cursor as test_db_cursor)


class publisher_bulk_automation(unittest.TestCase):
    def setUp(self):
        self.connection = connect_test_db()
        self.connection.executescript("""
            CREATE TABLE pull_list_entries(
                id INTEGER PRIMARY KEY,
                publisher VARCHAR(255)
            );
            CREATE TABLE publisher_subscriptions(
                publisher VARCHAR(255) PRIMARY KEY COLLATE NOCASE,
                root_folder_id INTEGER NOT NULL,
                auto_search BOOL NOT NULL DEFAULT 0
            );
        """)
        self.connection.executemany(
            'INSERT INTO pull_list_entries(publisher) VALUES (?);',
            [
                ('Marvel Comics',),
                ('Marvel Comics',),
                ('DC Comics',),
                ('',),
                (None,)
            ]
        )
        self.connection.commit()
        self.get_db_patch = patch.object(
            publisher_automation,
            'get_db',
            side_effect=lambda *a, **k: self._cursor()
        )
        self.get_db_patch.start()

    def _cursor(self):
        return test_db_cursor(self.connection)

    def tearDown(self):
        self.get_db_patch.stop()
        self.connection.close()

    def test_enables_every_distinct_listed_publisher(self):
        result = publisher_automation.set_all_publisher_subscriptions(7)

        self.assertEqual(result['updated'], 2)
        rows = self.connection.execute(
            'SELECT * FROM publisher_subscriptions ORDER BY publisher;'
        ).fetchall()
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(row['root_folder_id'] == 7 for row in rows))
        self.assertTrue(all(row['auto_search'] == 1 for row in rows))

    def test_existing_rules_are_upgraded_to_grab_and_new_root(self):
        self.connection.execute(
            """
            INSERT INTO publisher_subscriptions(
                publisher, root_folder_id, auto_search
            ) VALUES ('DC Comics', 1, 0);
            """
        )
        self.connection.commit()

        publisher_automation.set_all_publisher_subscriptions(9)

        row = self.connection.execute(
            "SELECT * FROM publisher_subscriptions WHERE publisher = 'DC Comics';"
        ).fetchone()
        self.assertEqual(row['root_folder_id'], 9)
        self.assertEqual(row['auto_search'], 1)


if __name__ == '__main__':
    unittest.main()
