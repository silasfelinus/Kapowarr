# -*- coding: utf-8 -*-

"""Three bugs one 2026-09-02 sweep showed in ninety seconds of log.

    02:51:49  Starting manual search: A1 (1992)
    02:51:50  Auto search failed for volume 155 (A1)
              ZeroDivisionError: float division by zero
    02:51:52  Search finished in 1.8s: Aama (2014) -- 74 result(s)
    02:51:52  Stopping the sweep at volume 156 (Aama): every source is rate
              limited

The sweep reached three volumes and then stopped for the day. It stopped one
second after a search returned seventy-four results, it lost a volume to a
crash in the ranking on the way, and having done almost none of its work it
would not have run again until tomorrow.
"""

import logging
import unittest
from unittest.mock import MagicMock, patch

from backend.base import helpers
from backend.features import search as SR
from backend.features import tasks_core as TC


class results_came_back_so_somebody_was_asked(unittest.TestCase):
    """The two counters lived on different classes: successful requests were
    counted on the synchronous session, skipped ones on the asynchronous
    session that searches actually use. So any search that skipped a single
    cooled host, however much else it found, reported that it had reached
    nobody -- and the sweep believed it and stopped."""

    def setUp(self):
        helpers.reset_request_tally()
        self.addCleanup(helpers.reset_request_tally)
        return

    def test_the_session_searches_use_counts_what_it_asks_for(self):
        from asyncio import run

        session = helpers.AsyncSession.__new__(helpers.AsyncSession)
        session.fs = MagicMock()
        session.fs.get_ua_cookies.return_value = ('UA', {})

        with patch.object(type(session), 'headers', {}), \
                patch.object(type(session), 'cookie_jar', MagicMock()), \
                patch.object(helpers.AsyncSession.__bases__[0], '_request',
                             side_effect=RuntimeError('stop here')):
            try:
                run(session._request('GET', 'https://example.com/x'))
            except Exception:
                pass

        self.assertEqual(helpers.request_tally()[0], 1)
        return

    def test_the_synchronous_session_counts_too(self):
        session = helpers.Session.__new__(helpers.Session)
        session.fs = MagicMock()
        session.fs.get_ua_cookies.return_value = ('UA', {})
        session.headers = {}
        session.cookies = MagicMock()

        with patch.object(helpers.Session.__bases__[0], 'request',
                          side_effect=RuntimeError('stop here')):
            try:
                session.request('GET', 'https://example.com/x')
            except Exception:
                pass

        self.assertEqual(helpers.request_tally()[0], 1)
        return

    def test_a_search_that_reached_someone_is_not_a_silence(self):
        helpers._request_tally.made = 1
        helpers._request_tally.skipped_rate_limited = 4

        self.assertFalse(SR.nothing_could_be_asked())
        return


class a_nonsense_issue_range_is_not_fatal(unittest.TestCase):
    """The range comes out of a parsed release name and need not be sane. A1
    (1992) produced one whose ends were the wrong way round, which divided by
    zero and took the whole volume's search down."""

    BASE = dict(
        match=True, match_issue=None, display_title='A1', special_version=None,
        filesize=None, pages=None, releaser=None, scan_type=None,
        resolution=None, dpi=None, extension=None, comics_id=None,
        source='x', link='l', series='A1', year=1992, volume_number=None,
        annual=False
    )

    def _fit(self, issue_number):
        return SR._rank_search_result(
            {**self.BASE, 'issue_number': issue_number},
            'a1', None, (1992, None), None
        )[-1]

    def test_a_backwards_range_does_not_divide_by_zero(self):
        self.assertEqual(self._fit((5.0, 4.0)), self._fit((4.0, 5.0)))
        return

    def test_a_single_issue_range_ranks_as_one_issue(self):
        self.assertEqual(self._fit((3.0, 3.0)), 1.0)
        return

    def test_a_wider_pack_still_ranks_below_a_narrower_one(self):
        self.assertLess(self._fit((1.0, 100.0)), self._fit((1.0, 5.0)))
        return


class stopping_early_brings_the_next_sweep_forward(unittest.TestCase):
    def setUp(self):
        helpers.clear_rate_limits()
        self.addCleanup(helpers.clear_rate_limits)
        return

    def _stopped_sweep(self):
        """Run a sweep that reaches nobody, and report the UPDATE it made.

        The stop is logged on purpose; it is not printed over the run.
        """
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, logging.NOTSET)
        cursor = MagicMock()
        cursor.execute.return_value.fetchall.return_value = [(1, 'A'), (2, 'B')]
        writes = []
        cursor.execute.side_effect = lambda q, p=None: (
            writes.append((q, p)) if 'task_intervals' in q else None
        ) or cursor.execute.return_value

        with patch.object(TC, 'get_db', return_value=cursor), \
                patch.object(TC, 'commit'), \
                patch.object(TC, 'WebSocket', MagicMock()), \
                patch.object(TC, 'reset_request_tally'), \
                patch.object(TC, 'nothing_could_be_asked', return_value=True), \
                patch.object(TC.SearchAll, '_mark_searched'), \
                patch.object(TC, 'auto_search', return_value=[]):
            TC.SearchAll().run()

        return writes

    def test_it_asks_to_run_again_when_the_first_quota_returns(self):
        helpers.note_rate_limit('https://prowlarr.example.com/38/api', 3939.0)

        writes = self._stopped_sweep()

        self.assertEqual(len(writes), 1)
        query, params = writes[0]
        self.assertIn('task_intervals', query)
        self.assertEqual(params[1], TC.SearchAll.action)
        return

    def test_it_never_pushes_the_next_sweep_later(self):
        "MIN, so a sweep that stops cannot delay one already due sooner."
        helpers.note_rate_limit('https://prowlarr.example.com/38/api', 3939.0)

        query, _ = self._stopped_sweep()[0]

        self.assertIn('MIN(next_run', query)
        return

    def test_nothing_in_a_cooldown_means_nothing_to_bring_forward(self):
        self.assertEqual(self._stopped_sweep(), [])
        return

    def test_the_margin_clears_the_cooldown_rather_than_landing_on_it(self):
        self.assertGreater(TC.SearchAll.RETRY_MARGIN_SECONDS, 0)
        return


class how_long_until_a_quota_returns(unittest.TestCase):
    def setUp(self):
        helpers.clear_rate_limits()
        self.addCleanup(helpers.clear_rate_limits)
        return

    def test_nothing_cooling_is_no_wait(self):
        self.assertEqual(helpers.shortest_rate_limit_cooldown(), 0.0)
        return

    def test_it_is_the_first_one_back_not_the_last(self):
        helpers.note_rate_limit('https://slow.example.com', 3939.0)
        helpers.note_rate_limit('https://quick.example.com', 900.0)

        self.assertLessEqual(helpers.shortest_rate_limit_cooldown(), 900.0)
        return

    def test_an_expired_cooldown_is_not_counted(self):
        helpers.note_rate_limit('https://done.example.com', -1.0)

        self.assertEqual(helpers.shortest_rate_limit_cooldown(), 0.0)
        return
