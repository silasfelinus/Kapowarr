# -*- coding: utf-8 -*-

"""One second was Kapowarr's opinion, not the indexer's.

Silas: "how do i put a rate limit in prowlarr, all I see is query limit per
hour or day in the individual indexer listing." He is right that that is
what Prowlarr gives him -- and the lever he wanted was in Kapowarr all
along, hardcoded. `TORZNAB_REQUEST_MIN_INTERVAL = 1.0`, with a comment
explaining that it exists because of an observed 429 storm. Newznab had no
gate at all.

A second was demonstrably too fast for three public trackers behind
Prowlarr allowing a hundred queries a day between them. How fast an indexer
wants to be asked is a property of the indexer, so it is a setting, at the
old values by default: one second for torrents, none for Usenet.
"""

import unittest
from unittest.mock import patch

from backend.features import acquisition_preferences as AP
from backend.implementations import indexers_core as IC
from backend.implementations import torznab as TZ


class the_defaults_are_what_they_were(unittest.TestCase):
    def test_torrent_still_paces_at_a_second(self):
        self.assertEqual(AP.DEFAULT_TORRENT_SEARCH_DELAY, 1.0)
        self.assertEqual(
            TZ.TORZNAB_REQUEST_MIN_INTERVAL, AP.DEFAULT_TORRENT_SEARCH_DELAY
        )

    def test_usenet_still_does_not_pace(self):
        self.assertEqual(AP.DEFAULT_USENET_SEARCH_DELAY, 0.0)

    def test_and_that_is_what_a_fresh_install_reads(self):
        with patch.object(AP, 'has_app_context', return_value=False):
            self.assertEqual(AP.search_delay('torrent'), 1.0)
            self.assertEqual(AP.search_delay('usenet'), 0.0)


class validating_a_delay(unittest.TestCase):
    def _written(self, data):
        """What the update would persist. The fake does not store, and
        `update_acquisition_preferences` returns a fresh read, so the write
        is what says whether the value was accepted."""
        written = {}

        class _Cursor:
            def execute(self, query, *args):
                return self

            def executemany(self, query, rows):
                written.update(dict(rows))
                return self

            def fetchall(self):
                return []

        with patch.object(AP, 'has_app_context', return_value=True), \
                patch.object(AP, 'get_db', return_value=_Cursor()), \
                patch.object(AP, 'commit', lambda: None):
            AP.update_acquisition_preferences(data)

        return written

    def _update(self, data):
        return self._written(data)

    def test_a_plain_number_is_accepted(self):
        written = self._written({'torrent_search_delay_seconds': 30})

        self.assertEqual(written['torrent_search_delay_seconds'], '30.0')

    def test_zero_means_no_delay(self):
        written = self._written({'torrent_search_delay_seconds': 0})

        self.assertEqual(written['torrent_search_delay_seconds'], '0.0')

    def test_negative_and_absurd_are_refused(self):
        for value in (-1, AP.MAX_SEARCH_DELAY + 1, 'soon', True, None):
            with self.subTest(value=value):
                with self.assertRaises(AP.InvalidKeyValue):
                    self._update({'usenet_search_delay_seconds': value})

    def test_a_bad_stored_row_falls_back_rather_than_raising(self):
        # Read on the search path, so it must never raise there.
        self.assertEqual(AP._delay_or_default('nonsense', 1.0), 1.0)
        self.assertEqual(AP._delay_or_default(None, 0.0), 0.0)
        self.assertEqual(AP._delay_or_default('15', 1.0), 15.0)


class both_protocols_consult_the_setting(unittest.TestCase):
    def test_torznab_asks_before_every_request(self):
        import inspect
        source = inspect.getsource(TZ.search_torznab_indexer)

        self.assertIn("search_delay('torrent')", source)
        self.assertNotIn('TORZNAB_REQUEST_MIN_INTERVAL', source)

    def test_newznab_now_has_a_gate_at_all(self):
        import inspect
        source = inspect.getsource(IC.search_indexer)

        self.assertIn("search_delay('usenet')", source)
        self.assertIn('async_sleep(interval - elapsed)', source)

    def test_a_zero_delay_costs_no_lock(self):
        # The default for Usenet, so it must not add a lock acquisition and
        # a clock read to every search that did not ask for one.
        import inspect
        source = inspect.getsource(IC.search_indexer)

        self.assertIn('if interval > 0:', source)

    def test_the_newznab_pacing_state_is_per_loop(self):
        # Each `asyncio.run` builds a new loop, and a lock made in an
        # earlier one cannot be awaited in this one.
        import inspect
        source = inspect.getsource(IC._request_state)

        self.assertIn('get_running_loop()', source)
        self.assertIn('indexer.base_url.lower()', source)


if __name__ == '__main__':
    unittest.main()
