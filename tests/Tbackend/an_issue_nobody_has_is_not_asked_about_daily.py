# -*- coding: utf-8 -*-

"""The sweep spent its whole quota learning the same nothing.

An hour of Search All on 2026-09-02, measured from the log:

    103   searches spent on one volume
    289   issue-level searches in total
    284   of them found nothing
      8   volumes reached, out of thousands

The issues coming back empty are old -- a 1966 Thor, a 1993 Catwoman -- and
nobody is going to start seeding them tonight. Meanwhile the new releases
that *would* be found are never reached, because the indexer quota is gone
long before the sweep gets there. The searches that cannot succeed crowd out
the ones that can, and the next day it happens again, identically.

An issue that comes back empty is now asked again later rather than next
time, and later grows each time it disappoints -- capped, so nothing is ever
given up on, and reset the moment something is found.
"""

import logging
import unittest
from unittest.mock import MagicMock, patch

from Tbackend.a_test_database_behaves_like_the_real_one import (
    connect as connect_test_db, cursor as test_db_cursor)
from backend.features import search_backoff as SB
from backend.features.search_backoff import MISS_BACKOFF_SECONDS, backoff_for

DAY = 86400


class how_long_to_wait(unittest.TestCase):
    def test_something_never_searched_is_asked_at_once(self):
        self.assertEqual(backoff_for(0), 0)
        return

    def test_the_wait_grows_with_each_disappointment(self):
        waits = [backoff_for(n) for n in range(1, len(MISS_BACKOFF_SECONDS))]

        self.assertEqual(waits, sorted(waits))
        self.assertEqual(len(set(waits)), len(waits))
        return

    def test_the_first_wait_is_short_enough_for_a_weekly_comic(self):
        "A new issue that is not out yet must be picked up promptly."
        self.assertLessEqual(backoff_for(1), DAY)
        return

    def test_nothing_is_ever_given_up_on(self):
        "Even fifty misses deep, it is retried within the month."
        self.assertLessEqual(backoff_for(50), 31 * DAY)
        self.assertEqual(backoff_for(50), backoff_for(500))
        return


class which_issues_are_due(unittest.TestCase):
    def setUp(self):
        self.connection = connect_test_db()
        self.connection.execute(
            'CREATE TABLE issues(id INTEGER PRIMARY KEY, '
            'last_auto_search INTEGER NOT NULL DEFAULT 0, '
            'auto_search_misses INTEGER NOT NULL DEFAULT 0);'
        )
        self.cursor = test_db_cursor(self.connection)
        patcher = patch.object(SB, 'get_db', return_value=self.cursor)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.now = 1_000_000_000
        return

    def _issue(self, issue_id, last_search=0, misses=0):
        self.cursor.execute(
            'INSERT INTO issues(id, last_auto_search, auto_search_misses) '
            'VALUES (?,?,?);', (issue_id, last_search, misses))
        return (issue_id, float(issue_id))

    def test_an_issue_never_searched_is_due(self):
        issues = [self._issue(1)]

        self.assertEqual(SB.due_issues(issues, self.now), issues)
        return

    def test_one_that_missed_yesterday_is_left_alone(self):
        issues = [self._issue(1, last_search=self.now - 60, misses=1)]

        self.assertEqual(SB.due_issues(issues, self.now), [])
        return

    def test_one_whose_wait_has_elapsed_comes_back(self):
        issues = [self._issue(
            1, last_search=self.now - backoff_for(1) - 1, misses=1)]

        self.assertEqual(SB.due_issues(issues, self.now), issues)
        return

    def test_a_deeper_miss_waits_longer_for_the_same_gap(self):
        gap = backoff_for(1) + 1
        shallow = self._issue(1, last_search=self.now - gap, misses=1)
        deep = self._issue(2, last_search=self.now - gap, misses=4)

        due = SB.due_issues([shallow, deep], self.now)

        self.assertEqual(due, [shallow])
        return

    def test_an_unreadable_backoff_searches_everything(self):
        """A wasted search costs a search; a wrongly skipped one costs an
        issue that never arrives."""
        issues = [(1, 1.0), (2, 2.0)]
        # The traceback is the expected result, not something to print over
        # the test run.
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, logging.NOTSET)
        with patch.object(SB, 'get_db', side_effect=RuntimeError('no db')):
            self.assertEqual(SB.due_issues(issues, self.now), issues)
        return

    def test_a_miss_is_recorded_and_compounds(self):
        self._issue(1, last_search=0, misses=2)

        SB.record_miss(1)

        row = self.cursor.execute(
            'SELECT last_auto_search, auto_search_misses FROM issues '
            'WHERE id = 1;').fetchone()
        self.assertEqual(row[1], 3)
        self.assertGreater(row[0], 0)
        return

    def test_finding_it_wipes_the_slate(self):
        self._issue(1, last_search=500, misses=5)
        self._issue(2, last_search=500, misses=5)

        SB.record_hit((1, 2))

        self.assertEqual(
            [r[0] for r in self.cursor.execute(
                'SELECT auto_search_misses FROM issues ORDER BY id;'
            ).fetchall()],
            [0, 0]
        )
        return

    def test_recording_nothing_is_not_a_write(self):
        with patch.object(type(self.cursor), 'execute') as write:
            SB.record_hit(())

        write.assert_not_called()
        return


class a_person_asking_is_always_honoured(unittest.TestCase):
    """The backoff is for the unattended sweep. Someone who clicks search on
    an issue has said they want it looked for now, however many times it has
    come up empty."""

    def test_the_sweep_asks_for_the_backoff(self):
        from backend.features import tasks_core as TC

        cursor = MagicMock()
        cursor.execute.return_value.fetchall.return_value = [(1, 'A')]
        asked = []

        with patch.object(TC, 'get_db', return_value=cursor), \
                patch.object(TC, 'WebSocket', MagicMock()), \
                patch.object(TC, 'reset_request_tally'), \
                patch.object(TC, 'nothing_could_be_asked', return_value=False), \
                patch.object(TC.SearchAll, '_mark_searched'), \
                patch.object(
                    TC, 'auto_search',
                    side_effect=lambda v, **kw: asked.append(kw) or []):
            TC.SearchAll().run()

        self.assertEqual(asked, [{'respect_backoff': True}])
        return

    def test_and_it_is_off_by_default(self):
        from backend.features.search import auto_search
        import inspect

        signature = inspect.signature(auto_search)
        self.assertIs(
            signature.parameters['respect_backoff'].default, False)
        return

    def test_a_manual_issue_search_does_not_record_a_miss(self):
        from backend.features import search as SR

        volume = MagicMock()
        volume.get_data.return_value = MagicMock(monitored=True)
        volume.get_issues.return_value = []
        issue = MagicMock()
        issue.get_data.return_value = MagicMock(
            monitored=True, calculated_issue_number=1.0)
        issue.get_files.return_value = []
        volume.get_issue.return_value = issue

        with patch.object(SR, 'Volume', return_value=volume), \
                patch.object(SR, 'manual_search', return_value=[]), \
                patch.object(SR, 'record_miss') as missed:
            SR.auto_search(1, 5)

        missed.assert_not_called()
        return

    def test_the_sweep_s_issue_search_does(self):
        from backend.features import search as SR

        volume = MagicMock()
        volume.get_data.return_value = MagicMock(monitored=True)
        volume.get_issues.return_value = []
        issue = MagicMock()
        issue.get_data.return_value = MagicMock(
            monitored=True, calculated_issue_number=1.0)
        issue.get_files.return_value = []
        volume.get_issue.return_value = issue

        with patch.object(SR, 'Volume', return_value=volume), \
                patch.object(SR, 'manual_search', return_value=[]), \
                patch.object(SR, 'record_miss') as missed:
            SR.auto_search(1, 5, respect_backoff=True)

        missed.assert_called_once_with(5)
        return
