import threading
import time
import unittest
from unittest.mock import patch

from backend.implementations.usenet_clients import SABnzbd as sab_module
from backend.implementations.usenet_clients.SABnzbd import (SABnzbd,
                                                            forget_snapshots)


def _client(base_url='http://sab.example'):
    client = SABnzbd.__new__(SABnzbd)
    client.ssn = object()
    client._base_url = base_url
    return client


class _Patched:
    """A SABnzbd whose base_url and api_token answer without a database."""

    def __enter__(self):
        self._patches = [
            patch.object(type(_client()), 'base_url',
                         property(lambda self: self._base_url)),
            patch.object(type(_client()), 'api_token',
                         property(lambda self: 'key')),
        ]
        for p in self._patches:
            p.start()
        forget_snapshots()
        return self

    def __exit__(self, *exc):
        for p in self._patches:
            p.stop()
        forget_snapshots()
        return False


class asking_about_one_job_means_fetching_the_lot(unittest.TestCase):
    """SABnzbd has no per-job status call. `mode=queue` returns everything,
    so asking about one download means fetching the whole list and finding
    it -- and every download had its own thread doing that on its own
    five-second timer.

    On 2026-09-03 Silas had around 470 usenet downloads pointed at one
    SABnzbd: roughly ninety requests a second, all asking the same
    question. SABnzbd stopped answering inside the thirty-second read
    timeout, `requests` retried each four times, and the log filled with
    ReadTimeoutError from threads 2844 through 3312. The instance looked
    broken. It was being asked the same thing ninety times a second.
    """

    def test_every_waiting_download_shares_one_request(self):
        client = _client()
        calls = []

        def slow_api(ssn, base_url, api_token, **kwargs):
            calls.append(kwargs['mode'])
            time.sleep(0.05)
            return {kwargs['mode']: {'slots': [{'nzo_id': 'abc'}]}}

        with _Patched(), patch.object(
            SABnzbd, '_api_request', staticmethod(slow_api)
        ):
            found = []
            threads = [
                threading.Thread(
                    target=lambda: found.append(client._find_in_queue('abc'))
                )
                for _ in range(200)
            ]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

        self.assertEqual(len(calls), 1, 'one request should have served them')
        self.assertEqual(len(found), 200)
        self.assertTrue(all(f == {'nzo_id': 'abc'} for f in found))

    def test_an_instance_that_is_down_is_only_asked_once(self):
        """Otherwise every waiting download makes its own doomed request and
        waits out the full read timeout, one after another.
        """
        client = _client()
        calls = []

        def failing_api(ssn, base_url, api_token, **kwargs):
            calls.append(kwargs['mode'])
            raise RuntimeError('read timed out')

        with _Patched(), patch.object(
            SABnzbd, '_api_request', staticmethod(failing_api)
        ):
            told = 0
            for _ in range(50):
                try:
                    client._find_in_queue('abc')
                except RuntimeError:
                    told += 1

        self.assertEqual(len(calls), 1)
        # Shared, not swallowed: every caller still hears about it.
        self.assertEqual(told, 50)

    def test_the_snapshot_does_not_stand_forever(self):
        client = _client()
        calls = []

        def api(ssn, base_url, api_token, **kwargs):
            calls.append(kwargs['mode'])
            return {kwargs['mode']: {'slots': []}}

        with _Patched(), patch.object(
            SABnzbd, '_api_request', staticmethod(api)
        ), patch.object(sab_module, 'SNAPSHOT_TTL', 0.05):
            client._find_in_queue('a')
            client._find_in_queue('a')
            time.sleep(0.08)
            client._find_in_queue('a')

        self.assertEqual(len(calls), 2)

    def test_the_queue_and_the_history_are_asked_separately(self):
        client = _client()
        calls = []

        def api(ssn, base_url, api_token, **kwargs):
            calls.append(kwargs['mode'])
            return {kwargs['mode']: {'slots': []}}

        with _Patched(), patch.object(
            SABnzbd, '_api_request', staticmethod(api)
        ):
            client._find_in_queue('a')
            client._find_in_history('a')

        self.assertEqual(calls, ['queue', 'history'])

    def test_two_instances_do_not_share_a_snapshot(self):
        one, two = _client('http://a.example'), _client('http://b.example')
        seen = []

        def api(ssn, base_url, api_token, **kwargs):
            seen.append(base_url)
            return {kwargs['mode']: {'slots': []}}

        with _Patched(), patch.object(
            SABnzbd, '_api_request', staticmethod(api)
        ):
            one._find_in_queue('a')
            two._find_in_queue('a')

        self.assertEqual(seen, ['http://a.example', 'http://b.example'])

    def test_a_malformed_answer_still_does_not_kill_the_loop(self):
        "A present-but-null key, and a slot that is not a mapping."
        client = _client()

        with _Patched(), patch.object(
            SABnzbd, '_api_request',
            staticmethod(lambda ssn, b, a, **k: {'queue': None})
        ):
            self.assertIsNone(client._find_in_queue('abc'))

        with _Patched(), patch.object(
            SABnzbd, '_api_request',
            staticmethod(lambda ssn, b, a, **k: {'queue': {'slots': ['nope']}})
        ):
            self.assertIsNone(client._find_in_queue('abc'))
