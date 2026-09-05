# -*- coding: utf-8 -*-

"""Update All died because one file went away while it was working.

    File "/app/backend/implementations/file_matching.py", line 366, in scan_files
        cursor.executemany(\"\"\"
            INSERT INTO volume_files(
    ...
    sqlite3.IntegrityError: FOREIGN KEY constraint failed

`refresh_and_scan` runs a pool over every volume in the `continuous_import`
lane, while Recover Orphaned Downloads and Watched Folder Import run in
`default` -- deliberately, so a long refresh does not monopolise
everything. Both of those create and delete `files` rows. So a file this
scan read can be gone by the time the scan writes its binding.

On 2026-09-05 at 00:33:39 that cost the whole hourly refresh: the
IntegrityError came out of the pool and killed the task, with Recover
Orphaned Downloads importing on another thread in the same second. 5,568
volumes abandoned because one file went away mid-scan.

A binding for a row that no longer exists has nothing to say, so skipping
it loses nothing -- and anything else the constraint would have caught
still raises.

These run the SQL as shipped, lifted out of the module rather than copied,
so the test cannot pass against a statement the app does not use.
"""

import re
import sqlite3
import unittest
from pathlib import Path

SOURCE = (
    Path(__file__).resolve().parents[2]
    / 'backend' / 'implementations' / 'file_matching.py'
).read_text()

SCHEMA = """
CREATE TABLE volumes(id INTEGER PRIMARY KEY);
CREATE TABLE issues(id INTEGER PRIMARY KEY);
CREATE TABLE files(id INTEGER PRIMARY KEY);
CREATE TABLE issues_files(
    file_id INTEGER NOT NULL,
    issue_id INTEGER NOT NULL,
    FOREIGN KEY (file_id) REFERENCES files(id) ON DELETE CASCADE,
    FOREIGN KEY (issue_id) REFERENCES issues(id)
);
CREATE TABLE volume_files(
    file_id INTEGER PRIMARY KEY,
    volume_id INTEGER NOT NULL,
    file_type VARCHAR(15) NOT NULL,
    FOREIGN KEY (volume_id) REFERENCES volumes(id) ON DELETE CASCADE,
    FOREIGN KEY (file_id) REFERENCES files(id) ON DELETE CASCADE
);
"""


def shipped_sql(table: str) -> str:
    """The INSERT the app actually runs, out of the source it runs from."""
    start = SOURCE.index(f'INSERT INTO {table}')
    end = SOURCE.index('"""', start)
    statement = SOURCE[start:end]
    assert 'WHERE EXISTS' in statement, (
        f'the {table} insert no longer guards its foreign keys'
    )
    return statement


class the_binding_is_skipped_not_raised(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.db.execute('PRAGMA foreign_keys = ON')
        self.db.executescript(SCHEMA)
        self.db.executescript(
            'INSERT INTO volumes(id) VALUES (1);'
            'INSERT INTO issues(id) VALUES (10);'
            'INSERT INTO files(id) VALUES (100);'
        )
        self.addCleanup(self.db.close)

    def test_an_issue_binding_for_a_file_that_went_away(self):
        sql = shipped_sql('issues_files')
        self.db.executemany(sql, [
            (100, 10, 100, 10),     # the file is still there
            (999, 10, 999, 10)      # it went away mid-scan
        ])

        self.assertEqual(
            self.db.execute('SELECT * FROM issues_files').fetchall(),
            [(100, 10)]
        )

    def test_a_volume_binding_for_a_file_that_went_away(self):
        sql = shipped_sql('volume_files')
        self.db.executemany(sql, [
            (100, 1, 'cover', 100, 1, 'cover'),
            (999, 1, 'cover', 999, 1, 'cover')
        ])

        self.assertEqual(
            self.db.execute('SELECT * FROM volume_files').fetchall(),
            [(100, 1, 'cover')]
        )

    def test_a_volume_deleted_while_the_pool_worked(self):
        """The other half of the same race: a volume can be deleted from
        the UI while the refresh is still going round the library."""
        sql = shipped_sql('volume_files')
        self.db.executemany(sql, [(100, 42, 'cover', 100, 42, 'cover')])

        self.assertEqual(
            self.db.execute('SELECT * FROM volume_files').fetchall(), []
        )

    def test_the_upsert_still_upserts(self):
        """`ON CONFLICT DO UPDATE` is what keeps a file's type current when
        a rescan reclassifies it. The guard must not cost that."""
        sql = shipped_sql('volume_files')
        self.db.executemany(sql, [(100, 1, 'cover', 100, 1, 'cover')])
        self.db.executemany(sql, [(100, 1, 'metadata', 100, 1, 'metadata')])

        self.assertEqual(
            self.db.execute('SELECT * FROM volume_files').fetchall(),
            [(100, 1, 'metadata')]
        )

    def test_a_whole_batch_of_survivors_still_lands(self):
        """One casualty must not take the rest of the batch with it, which
        is what `executemany` on a raising statement did."""
        self.db.executemany(
            'INSERT INTO files(id) VALUES (?);', [(n,) for n in range(1, 6)]
        )
        sql = shipped_sql('volume_files')
        self.db.executemany(sql, [
            (n, 1, 'cover', n, 1, 'cover') for n in (1, 2, 999, 3, 4, 5)
        ])

        self.assertEqual(
            [r[0] for r in self.db.execute(
                'SELECT file_id FROM volume_files ORDER BY file_id'
            )],
            [1, 2, 3, 4, 5]
        )


class the_guard_is_on_the_statement_the_app_runs(unittest.TestCase):
    def test_neither_insert_takes_a_bare_values_list(self):
        for table in ('issues_files', 'volume_files'):
            statement = shipped_sql(table)
            self.assertNotRegex(
                statement, re.compile(r'VALUES\s*\(\s*\?', re.S),
                msg=f'{table} is inserting without checking its rows exist'
            )


if __name__ == '__main__':
    unittest.main()
