# -*- coding: utf-8 -*-

"""Two config readers wrote before they read, on the hottest path there is.

`filter_search_results` runs for every indexer response, inside
`asyncio.gather` over every source, inside `Search All`. `file_quality`
consults the acquisition preferences once per candidate it ranks. Both of
those reached config accessors that seeded the table with defaults first --
a write, taken on the search path, contending with whatever long job holds
the SQLite writer.

`WRITE_TRANSACTION_MODE = IMMEDIATE` (see `internals/db.py`) turned that
from an instant failure into a bounded wait, which is the right fix for a
write that has to happen. It does not help a write that did not need to
happen at all: the library import holds the writer for longer than
`DB_TIMEOUT`, the wait expires, and "database is locked" comes back out
through the search. On 2026-08-31 it ended the day's sweep after twenty-one
volumes.

Nothing needed the rows. Both accessors already fall back to their defaults
for any key the query does not return.
"""

import unittest
from sqlite3 import OperationalError
from unittest.mock import MagicMock, patch

from backend.features import acquisition_preferences as AP
from backend.features import grab_size_limits as GS


class _Cursor:
    """Records what it was asked to run."""

    def __init__(self, rows=(), raises=None):
        self.rows = list(rows)
        self.raises = raises
        self.statements = []

    def execute(self, query, *args):
        self.statements.append(query)
        if self.raises is not None:
            raise self.raises
        return self

    def executemany(self, query, *args):
        self.statements.append(query)
        if self.raises is not None:
            raise self.raises
        return self

    def fetchall(self):
        return list(self.rows)

    def writes(self):
        return [
            statement for statement in self.statements
            if 'INSERT' in statement.upper() or 'UPDATE' in statement.upper()
        ]


class reading_the_grab_size_limits(unittest.TestCase):
    def test_takes_no_write(self):
        cursor = _Cursor([('minimum_grab_size_mb', 2)])

        with patch.object(GS, 'has_app_context', return_value=True), \
                patch.object(GS, 'get_db', return_value=cursor):
            limits = GS.get_grab_size_limits()

        self.assertEqual(cursor.writes(), [])
        self.assertEqual(limits['minimum_grab_size_mb'], 2)

    def test_a_missing_row_is_simply_the_default(self):
        cursor = _Cursor([])

        with patch.object(GS, 'has_app_context', return_value=True), \
                patch.object(GS, 'get_db', return_value=cursor):
            limits = GS.get_grab_size_limits()

        self.assertEqual(limits, {
            'minimum_grab_size_mb': GS.DEFAULT_MINIMUM_GRAB_SIZE_MB,
            'maximum_grab_size_mb': GS.DEFAULT_MAXIMUM_GRAB_SIZE_MB
        })

    def test_a_locked_database_does_not_reach_the_caller(self):
        # The failure that ended the sweep. A size limit is not worth a task.
        cursor = _Cursor(raises=OperationalError('database is locked'))

        with patch.object(GS, 'has_app_context', return_value=True), \
                patch.object(GS, 'get_db', return_value=cursor):
            limits = GS.get_grab_size_limits()

        self.assertEqual(
            limits['maximum_grab_size_mb'], GS.DEFAULT_MAXIMUM_GRAB_SIZE_MB
        )

    def test_and_filtering_still_works_through_it(self):
        cursor = _Cursor(raises=OperationalError('database is locked'))
        results = [
            {'link': 'small', 'size': 1},
            {'link': 'fine', 'size': 50 * GS.MEBIBYTE},
            {'link': 'unknown'}
        ]

        with patch.object(GS, 'has_app_context', return_value=True), \
                patch.object(GS, 'get_db', return_value=cursor):
            kept = GS.filter_search_results(results)

        self.assertEqual(
            [r['link'] for r in kept], ['fine', 'unknown'],
            'the default range applies; a sizeless result stays eligible'
        )


class reading_the_acquisition_preferences(unittest.TestCase):
    def test_takes_no_write(self):
        cursor = _Cursor([])

        with patch.object(AP, 'get_db', return_value=cursor):
            AP.get_acquisition_preferences()

        self.assertEqual(cursor.writes(), [])

    def test_a_locked_database_falls_back_to_the_defaults(self):
        cursor = _Cursor(raises=OperationalError('database is locked'))

        with patch.object(AP, 'get_db', return_value=cursor):
            preferences = AP.get_acquisition_preferences()

        self.assertEqual(
            preferences['acquisition_source_preference'],
            list(AP.DEFAULT_SOURCE_PREFERENCE)
        )


class the_seeding_helpers_are_gone(unittest.TestCase):
    def test_neither_module_still_has_one(self):
        # Kept as a rule rather than a fix: the next reader that "may as
        # well leave the row behind" puts the write back on the path.
        self.assertFalse(hasattr(GS, '_ensure_defaults'))
        self.assertFalse(hasattr(AP, '_ensure_defaults'))


if __name__ == '__main__':
    unittest.main()
