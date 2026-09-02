# -*- coding: utf-8 -*-

"""A sweep that cannot finish must not always search the same prefix.

`Search All` read `SELECT id, title FROM volumes WHERE monitored = 1` with
no ordering, so SQLite handed the volumes back in rowid order -- the order
they were added to the library -- and handed them back that way every day.

On a library whose sweep finishes comfortably that is merely arbitrary.
Silas noticed it as a curiosity: "Curious why the first search starts in
the K's with Kaya" -- volume id 6, the first he had added that was missing
anything.

On a library whose sweep does not finish it is unfair in a way that
matters: the oldest additions are searched every single day and the newest
are never reached at all. The newest is exactly where a pull list lives,
and unfilled pull-list gaps going back weeks is where this whole thread
started.

Ordering by when each volume was last searched turns the sweep from a
prefix into a rotation. A run that covers a third of the library covers a
different third tomorrow.
"""

import inspect
import unittest
from unittest.mock import MagicMock, patch

from backend.features import tasks_core as TC
from backend.internals.db_migration import DatabaseMigrationHandler


class the_order_the_sweep_asks_for(unittest.TestCase):
    def test_it_is_least_recently_searched_first(self):
        cursor = MagicMock()
        cursor.execute.return_value.fetchall.return_value = []

        with patch.object(TC, 'get_db', return_value=cursor), \
                patch.object(TC, 'WebSocket', MagicMock()):
            TC.SearchAll().run()

        query = ' '.join(cursor.execute.call_args[0][0].split())
        self.assertIn('ORDER BY last_auto_search, id', query)
        self.assertIn('WHERE monitored = 1', query)

    def test_the_column_exists_for_a_fresh_install(self):
        from backend.internals.db import DB_SCHEMA

        self.assertIn(
            'last_auto_search INTEGER(8) NOT NULL DEFAULT 0', DB_SCHEMA
        )

    def test_and_an_upgraded_one_gets_it_too(self):
        # What matters is that the migration exists and is reached, not what
        # number happens to be newest -- pinning that made every later
        # migration fail this test.
        self.assertIn(51, DatabaseMigrationHandler.handlers)
        self.assertGreater(DatabaseMigrationHandler.latest_db_version(), 51)
        self.assertIn(
            'last_auto_search',
            inspect.getsource(DatabaseMigrationHandler.handlers[51])
        )


class when_the_turn_is_recorded(unittest.TestCase):
    def _sweep(self, volumes, auto_search):
        cursor = MagicMock()
        cursor.execute.return_value.fetchall.return_value = volumes
        stamped = []
        task = TC.SearchAll()

        with patch.object(TC, 'get_db', return_value=cursor), \
                patch.object(TC, 'WebSocket', MagicMock()), \
                patch.object(TC, 'auto_search', side_effect=auto_search), \
                patch.object(
                    TC.SearchAll, '_mark_searched',
                    staticmethod(lambda volume_id: stamped.append(volume_id))
                ), \
                patch.object(TC.SearchAll, '_queue', staticmethod(lambda e: None)):
            task.run()

        return task, stamped

    def test_a_searched_volume_is_stamped(self):
        _, stamped = self._sweep(
            [(1, 'A'), (2, 'B')], lambda volume_id: []
        )

        self.assertEqual(stamped, [1, 2])

    def test_a_volume_whose_search_fails_is_stamped_anyway(self):
        # Otherwise one reliably-failing volume sits at the front of the
        # queue every day forever -- the exact problem this ordering fixes.
        def auto_search(volume_id, respect_backoff=False):
            if volume_id == 1:
                raise RuntimeError('indexer exploded')
            return []

        with self.assertLogs(TC.LOGGER, 'ERROR'):
            _, stamped = self._sweep([(1, 'A'), (2, 'B')], auto_search)

        self.assertEqual(stamped, [1, 2])

    def test_a_stop_leaves_the_rest_unstamped(self):
        # So tomorrow picks up where this run was interrupted.
        holder = {}

        def auto_search(volume_id, respect_backoff=False):
            holder['task'].stop = True
            return []

        def sweep():
            cursor = MagicMock()
            cursor.execute.return_value.fetchall.return_value = [(1, 'A'), (2, 'B'), (3, 'C')]
            stamped = []
            holder['task'] = task = TC.SearchAll()

            with patch.object(TC, 'get_db', return_value=cursor), \
                    patch.object(TC, 'WebSocket', MagicMock()), \
                    patch.object(TC, 'auto_search', side_effect=auto_search), \
                    patch.object(
                        TC.SearchAll, '_mark_searched',
                        staticmethod(lambda vid: stamped.append(vid))
                    ), \
                    patch.object(
                        TC.SearchAll, '_queue', staticmethod(lambda e: None)
                    ):
                task.run()
            return stamped

        self.assertEqual(sweep(), [1])


class recording_a_turn_is_never_worth_a_task(unittest.TestCase):
    def test_a_failed_stamp_does_not_raise(self):
        # A sweep shares the database with library import and refresh.
        # Losing one stamp costs a volume its place for a day; losing the
        # sweep costs the library its search.
        cursor = MagicMock()
        cursor.execute.side_effect = Exception('database is locked')

        with patch.object(TC, 'get_db', return_value=cursor), \
                self.assertLogs(TC.LOGGER, 'WARNING') as captured:
            TC.SearchAll._mark_searched(7)

        self.assertIn('keeps its place in the rotation', captured.output[0])


if __name__ == '__main__':
    unittest.main()
