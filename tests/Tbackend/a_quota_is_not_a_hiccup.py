# -*- coding: utf-8 -*-

"""Retrying a 429 spends the very thing it says you have run out of.

429 sat in `STATUS_FORCELIST_RETRIES` beside 500 and 503, so a rate-limited
request was retried five times with exponential backoff. Those rejected
requests are what the limiter counts, so one search cost five queries and
the limit arrived sooner and lasted longer for it.

Then the sweep. `Search All` walks every monitored volume; without a memory
of the limit, an indexer that has spent its daily quota is rediscovered on
every one of them -- five requests at a time. Silas's log for 2026-09-01
shows nine searches turning into forty-five requests against Prowlarr, over
indexers configured for a hundred queries a day between them
(torrentdownload 50, limetorrents 25, 1337x 25).

A short `Retry-After` is different: that is a burst limiter catching its
breath, and waiting it out is exactly right.
"""

import unittest
from types import SimpleNamespace
from unittest.mock import patch
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

from backend.base import helpers as H
from backend.base.definitions import Constants


class reading_retry_after(unittest.TestCase):
    def test_seconds(self):
        self.assertEqual(H.parse_retry_after('120'), 120.0)

    def test_an_http_date(self):
        when = datetime.now(timezone.utc) + timedelta(seconds=90)
        parsed = H.parse_retry_after(format_datetime(when))

        self.assertIsNotNone(parsed)
        self.assertTrue(80 <= parsed <= 100, parsed)

    def test_a_date_in_the_past_is_no_wait_at_all(self):
        when = datetime.now(timezone.utc) - timedelta(hours=1)

        self.assertEqual(H.parse_retry_after(format_datetime(when)), 0.0)

    def test_nonsense_and_absence(self):
        for value in (None, '', 'soon', 'Retry-After: yes'):
            with self.subTest(value=value):
                self.assertIsNone(H.parse_retry_after(value))


class remembering_a_limit(unittest.TestCase):
    URL = 'https://prowlarr.example/33/api?query=batman'

    def setUp(self):
        H.clear_rate_limits()
        self.addCleanup(H.clear_rate_limits)

    def test_a_host_is_left_alone_for_the_cooldown(self):
        held = H.note_rate_limit(self.URL)

        self.assertEqual(held, Constants.RATE_LIMIT_DEFAULT_COOLDOWN)
        self.assertGreater(H.rate_limit_cooldown_remaining(self.URL), 0)

    def test_it_applies_to_the_whole_host_not_one_query(self):
        # The quota belongs to the indexer, not the search that found it.
        H.note_rate_limit(self.URL)

        self.assertGreater(
            H.rate_limit_cooldown_remaining(
                'https://PROWLARR.example/44/api?query=hulk'
            ),
            0
        )

    def test_and_not_to_anybody_else(self):
        H.note_rate_limit(self.URL)

        self.assertEqual(
            H.rate_limit_cooldown_remaining('https://nzb.example/api'), 0.0
        )

    def test_a_supplied_wait_is_used_instead_of_the_default(self):
        held = H.note_rate_limit(self.URL, 5.0)

        self.assertEqual(held, 5.0)
        self.assertLessEqual(H.rate_limit_cooldown_remaining(self.URL), 5.0)

    def test_an_expired_cooldown_is_over(self):
        H.note_rate_limit(self.URL, 0.0)

        self.assertEqual(H.rate_limit_cooldown_remaining(self.URL), 0.0)


class the_retry_loop(unittest.TestCase):
    def test_a_rate_limit_is_not_in_the_retried_set(self):
        # `RateLimited` is a `ClientError` so callers are unchanged, and its
        # own type so the loop can refuse to retry it.
        self.assertTrue(issubclass(H.RateLimited, H.ClientError))

        import inspect
        source = inspect.getsource(H.AsyncSession._request)

        self.assertIn('except RateLimited:', source)
        self.assertLess(
            source.index('except RateLimited:'),
            source.index('except ClientError as error:'),
            'the narrower handler has to come first or it never runs'
        )

    def test_a_short_retry_after_is_waited_out_instead(self):
        import inspect
        source = inspect.getsource(H.AsyncSession._request)

        self.assertIn('RATE_LIMIT_MAX_HONOURED_WAIT', source)
        self.assertIn('await sleep(retry_after)', source)

    def test_a_host_in_cooldown_is_skipped_before_the_request(self):
        import inspect
        source = inspect.getsource(H.AsyncSession._request)

        self.assertLess(
            source.index('rate_limit_cooldown_remaining'),
            source.index('for round in range'),
            'the point is to not make the request at all'
        )


class what_it_actually_costs(unittest.IsolatedAsyncioTestCase):
    """Counted, not read off the source."""

    URL = 'https://prowlarr.example/33/api?query=batman'

    def setUp(self):
        H.clear_rate_limits()
        self.addCleanup(H.clear_rate_limits)

    async def _drive(self, status, headers=None, url=None):
        calls = []

        async def fake_request(self, *args, **kwargs):
            calls.append(args[1])
            return SimpleNamespace(
                status=status, url=args[1], headers=headers or {}
            )

        # A real session: `_request` calls `super()._request`, which needs a
        # genuine instance, and `headers`/`cookie_jar` are read-only.
        # `AsyncSession.__init__` builds a FlareSolverr, which wants an app
        # context. Nothing here goes near it.
        stub_fs = lambda: SimpleNamespace(
            get_ua_cookies=lambda url: (Constants.DEFAULT_USERAGENT, {})
        )

        with patch.object(H.ClientSession, '_request', fake_request), \
                patch.object(H, 'sleep', _noop), \
                patch(
                    'backend.implementations.flaresolverr.FlareSolverr',
                    stub_fs
                ):
            async with H.AsyncSession() as session:
                try:
                    await session._request('GET', url or self.URL)
                except H.ClientError as error:
                    return calls, error
        return calls, None

    async def test_one_rate_limited_search_costs_one_request(self):
        calls, error = await self._drive(429)

        self.assertEqual(
            len(calls), 1,
            'five attempts is four more bites out of the same quota'
        )
        self.assertIsInstance(error, H.RateLimited)

    async def test_a_server_error_is_still_retried(self):
        # The forcelist is right for everything else in it.
        calls, error = await self._drive(503)

        self.assertEqual(len(calls), Constants.TOTAL_RETRIES)
        self.assertNotIsInstance(error, H.RateLimited)

    async def test_the_next_search_does_not_ask_again(self):
        await self._drive(429)
        calls, error = await self._drive(429)

        self.assertEqual(
            calls, [],
            'the sweep must not rediscover the limit volume by volume'
        )
        self.assertIsInstance(error, H.RateLimited)

    async def test_a_different_indexer_is_unaffected(self):
        await self._drive(429)
        calls, _ = await self._drive(200, url='https://nzb.example/api')

        self.assertEqual(len(calls), 1)

    async def test_a_short_retry_after_is_honoured_rather_than_banked(self):
        calls, _ = await self._drive(429, headers={'Retry-After': '2'})

        self.assertEqual(
            len(calls), Constants.TOTAL_RETRIES,
            'a burst limiter catching its breath is worth waiting out'
        )

    async def test_a_long_retry_after_is_taken_at_its_word(self):
        calls, _ = await self._drive(429, headers={'Retry-After': '3600'})

        self.assertEqual(len(calls), 1)
        self.assertGreater(H.rate_limit_cooldown_remaining(self.URL), 3000)


async def _noop(seconds=0):
    """Stands in for `asyncio.sleep`, so a backoff costs no wall clock."""
    return None


if __name__ == '__main__':
    unittest.main()
