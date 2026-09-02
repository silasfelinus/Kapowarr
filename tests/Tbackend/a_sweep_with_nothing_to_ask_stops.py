# -*- coding: utf-8 -*-

"""Two hours of searching that asked nobody anything.

2026-09-01, 20:24 onwards. Prowlarr's indexer 38 reported its daily quota
gone and asked for 27152 seconds -- seven and a half hours. GetComics
followed. From then on every search in the Search All sweep took exactly 9.0
seconds and returned 0 results, because the nine seconds were the configured
inter-request delays being slept through before requests that the session
then declined to make. The sweep worked its way through Catwoman (1993) an
issue at a time until Silas noticed: "the queue is still empty and history
shows nothing downloaded for two hours."

Worse than the wasted time: the sweep stamped every volume it passed as
having had its turn. `last_auto_search` is what makes the rotation a
rotation, so a day where the quota runs out would have cost a full pass --
tomorrow's sweep would skip every volume this one pretended to search.

Two things follow. A request that is not going to be made must not be paced
first, and a search that reached no source at all is not a search: it must
not take the volume's turn, and there is no point continuing to the next
volume.
"""

import re
import unittest
from unittest.mock import MagicMock, patch

from backend.base import helpers
from backend.features import search as SR
from backend.features import tasks_core as TC


class telling_a_miss_from_a_silence(unittest.TestCase):
    def setUp(self):
        helpers.reset_request_tally()
        self.addCleanup(helpers.reset_request_tally)
        return

    def test_a_fresh_tally_is_not_a_silence(self):
        self.assertFalse(SR.nothing_could_be_asked())
        return

    def test_asking_and_finding_nothing_is_a_real_search(self):
        helpers._request_tally.made = 3

        self.assertFalse(SR.nothing_could_be_asked())
        return

    def test_asking_nobody_is_not(self):
        helpers._request_tally.skipped_rate_limited = 3

        self.assertTrue(SR.nothing_could_be_asked())
        return

    def test_reaching_even_one_source_still_counts_as_a_search(self):
        "A partial outage is a miss, not a silence."
        helpers._request_tally.made = 1
        helpers._request_tally.skipped_rate_limited = 5

        self.assertFalse(SR.nothing_could_be_asked())
        return


class the_sweep_stops_when_there_is_nothing_to_ask(unittest.TestCase):
    def _sweep(self, silent_from=None, volumes=((1, 'A'), (2, 'B'), (3, 'C'))):
        """Run a sweep where volumes from `silent_from` on reach no source.

        Returns:
            Tuple[List[int], List[int]]: The volumes searched, and the
                volumes whose turn was recorded.
        """
        cursor = MagicMock()
        cursor.execute.return_value.fetchall.return_value = list(volumes)
        searched, stamped = [], []

        def _auto_search(volume_id):
            searched.append(volume_id)
            return []

        def _silent():
            return silent_from is not None and searched[-1] >= silent_from

        with patch.object(TC, 'get_db', return_value=cursor), \
                patch.object(TC, 'WebSocket', MagicMock()), \
                patch.object(TC, 'reset_request_tally'), \
                patch.object(TC, 'nothing_could_be_asked', _silent), \
                patch.object(TC.SearchAll, '_mark_searched', stamped.append), \
                patch.object(TC, 'auto_search', _auto_search):
            TC.SearchAll().run()

        return searched, stamped

    def test_a_normal_sweep_searches_and_stamps_everything(self):
        searched, stamped = self._sweep()

        self.assertEqual(searched, [1, 2, 3])
        self.assertEqual(stamped, [1, 2, 3])
        return

    def test_it_stops_at_the_first_volume_that_reached_nobody(self):
        """No volume after it could be searched either, so grinding through
        the rest of the library only wastes the time and the rotation."""
        searched, _ = self._sweep(silent_from=2)

        self.assertEqual(searched, [1, 2])
        return

    def test_the_volume_that_reached_nobody_keeps_its_place(self):
        "It never had a turn, so it must be first in line next time."
        _, stamped = self._sweep(silent_from=2)

        self.assertEqual(stamped, [1])
        return

    def test_a_volume_whose_search_raised_is_still_stamped(self):
        """Different case, unchanged: a reliably-failing volume must not sit
        at the front of the rotation every day forever."""
        cursor = MagicMock()
        cursor.execute.return_value.fetchall.return_value = [(1, 'A'), (2, 'B')]
        stamped = []

        with patch.object(TC, 'get_db', return_value=cursor), \
                patch.object(TC, 'WebSocket', MagicMock()), \
                patch.object(TC, 'reset_request_tally'), \
                patch.object(TC, 'nothing_could_be_asked', return_value=False), \
                patch.object(TC.SearchAll, '_mark_searched', stamped.append), \
                patch.object(TC, 'auto_search',
                             side_effect=RuntimeError('indexer exploded')), \
                self.assertLogs(level='ERROR'):
            TC.SearchAll().run()

        self.assertEqual(stamped, [1, 2])
        return


class saying_which_source_and_for_how_long(unittest.TestCase):
    """"Every source is rate limited" is not actionable on its own. Silas's
    log said only that; which indexer, and until when, was the thing worth
    knowing -- one of his was asking for seven and a half hours."""

    def setUp(self):
        helpers.clear_rate_limits()
        self.addCleanup(helpers.clear_rate_limits)
        return

    def test_a_clear_run_says_so(self):
        self.assertEqual(
            helpers.describe_rate_limits(),
            'no host is in a rate-limit cooldown'
        )
        return

    def test_it_names_each_host_and_its_wait(self):
        helpers.note_rate_limit('https://prowlarr.example.com/38/api', 27152.0)
        helpers.note_rate_limit('https://getcomics.org/page/6', 900.0)

        described = helpers.describe_rate_limits()

        self.assertIn('getcomics.org for another 15 min', described)
        # 27152s is a touch over 452 minutes, and a moment of it has passed.
        prowlarr = re.search(
            r'prowlarr\.example\.com for another (\d+) min', described)
        self.assertIsNotNone(prowlarr)
        self.assertIn(int(prowlarr.group(1)), (452, 453))
        return

    def test_an_expired_cooldown_is_not_reported(self):
        helpers.note_rate_limit('https://done.example.com', -1.0)

        self.assertEqual(
            helpers.describe_rate_limits(),
            'no host is in a rate-limit cooldown'
        )
        return
