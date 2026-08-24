# -*- coding: utf-8 -*-

"""Ask at the documented rate, and give ground when told to."""

import unittest
from unittest.mock import patch

from backend.features.library_import import (CV_REQUEST_CEILING,
                                             CV_REQUEST_FLOOR, AdaptiveDelay)


class it_starts_at_the_documented_rate(unittest.TestCase):
    """It used to sit at a flat 30 seconds.

    That was comfortably short of ComicVine's documented allowance, for
    a limit the import might never have come near -- and it was paid for
    in every hour of every import, roughly doubling the wall time of a
    full library pass.
    """

    def test_the_floor_is_the_documented_allowance(self):
        self.assertEqual(CV_REQUEST_FLOOR, 18.0, '200 requests per hour')

    def test_a_fresh_delay_asks_at_the_floor(self):
        self.assertEqual(AdaptiveDelay().current(), CV_REQUEST_FLOOR)


class it_widens_when_comicvine_objects(unittest.TestCase):
    def test_each_block_backs_further_off(self):
        delay = AdaptiveDelay()

        first = delay.record_block()
        second = delay.record_block()

        self.assertGreater(first, CV_REQUEST_FLOOR)
        self.assertGreater(second, first)

    def test_the_widened_interval_is_what_gets_used(self):
        delay = AdaptiveDelay()
        widened = delay.record_block()

        self.assertEqual(delay.current(), widened)

    def test_it_does_not_widen_without_end(self):
        """However often ComicVine objects, the import keeps moving."""
        delay = AdaptiveDelay()
        for _ in range(50):
            delay.record_block()

        self.assertEqual(delay.current(), CV_REQUEST_CEILING)


class it_eases_back_once_the_hour_is_clear(unittest.TestCase):
    """ComicVine's limit is reckoned per hour, so that is the period
    after which the earlier objection no longer says anything about the
    budget now."""

    def _at(self, delay, seconds):
        with patch(
            'backend.features.library_import.monotonic', return_value=seconds
        ):
            return delay.current()

    def test_an_hour_without_a_block_returns_to_the_floor(self):
        delay = AdaptiveDelay()
        with patch(
            'backend.features.library_import.monotonic', return_value=0.0
        ):
            delay.record_block()

        self.assertEqual(self._at(delay, 60 * 60 + 1), CV_REQUEST_FLOOR)

    def test_it_holds_the_wider_interval_until_then(self):
        delay = AdaptiveDelay()
        with patch(
            'backend.features.library_import.monotonic', return_value=0.0
        ):
            widened = delay.record_block()

        self.assertEqual(self._at(delay, 60 * 30), widened)

    def test_a_second_block_restarts_the_hour(self):
        delay = AdaptiveDelay()
        with patch(
            'backend.features.library_import.monotonic', return_value=0.0
        ):
            delay.record_block()
        with patch(
            'backend.features.library_import.monotonic', return_value=59 * 60
        ):
            widened = delay.record_block()

        self.assertEqual(
            self._at(delay, 59 * 60 + 30 * 60), widened,
            'the clock runs from the most recent objection'
        )


class both_importers_share_one_budget(unittest.TestCase):
    """Two importers pacing independently would each believe they were
    inside a limit that together they were exceeding."""

    def test_the_persistent_importer_uses_the_shared_delay(self):
        from backend.features import library_import, library_import_persistent

        self.assertIs(
            library_import_persistent.CV_REQUEST_DELAY,
            library_import.CV_REQUEST_DELAY
        )

    def test_a_block_is_recorded_rather_than_only_waited_out(self):
        """A flat 15 minute cooldown left the pace exactly as it was, so
        the import walked into the same wall again."""
        import inspect

        for module in (
            'backend.features.library_import',
            'backend.features.library_import_persistent'
        ):
            source = inspect.getsource(__import__(
                module, fromlist=['x']
            ))
            self.assertIn(
                'CV_REQUEST_DELAY.record_block()', source,
                f'{module} waits out a block without widening the interval'
            )


if __name__ == '__main__':
    unittest.main()
