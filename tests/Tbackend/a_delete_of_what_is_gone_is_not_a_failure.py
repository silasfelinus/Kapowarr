# -*- coding: utf-8 -*-

"""Sixteen of thirty-five errors in one run were SABnzbd agreeing with us.

    ERROR  SABnzbd instance returned an error: {'status': False, 'nzo_ids': []}
    WARNING The download client isn't working: Got an unexpected response back
    ERROR  Could not remove 'AVX.VS.05...' from its download client;
           continuing with post-processing anyway
           Traceback (most recent call last): ...

A finished job is deleted from both the queue and the history, because it
could be in either. The section that does not have it answers with a bare
`status: false` and no error text -- not silence, as the code assumed, but
something the shared request handler read as the client having failed. Every
completed download therefore produced at least one, and one SABnzbd had
already purged produced two, each with a traceback behind it. Real faults
were in that run too and had nothing to stand out against.

Nothing matching a delete is the state being asked for.
"""

import logging
import unittest
from unittest.mock import MagicMock, patch

from backend.base.custom_exceptions import ClientNotWorking, CredentialInvalid
from backend.implementations.usenet_clients import SABnzbd as SAB


class a_bare_status_false(unittest.TestCase):
    NOTHING_MATCHED = {'status': False, 'nzo_ids': []}

    def setUp(self):
        # The refusals below are the expected result; they are not printed
        # over the test run.
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, logging.NOTSET)
        return

    def _request(self, payload, **kwargs):
        session = MagicMock()
        session.get.return_value.json.return_value = payload
        with patch.object(SAB, 'Session', return_value=session):
            return SAB.SABnzbd._api_request(
                session, 'https://sab.example.com', 'key',
                mode='queue', **kwargs
            )

    def test_is_tolerated_when_a_delete_asked_for_it(self):
        result = self._request(
            self.NOTHING_MATCHED, tolerate_nothing_matched=True)

        self.assertEqual(result, self.NOTHING_MATCHED)
        return

    def test_is_still_a_failure_anywhere_else(self):
        "Adding a download that comes back with nothing is a real problem."
        with self.assertRaises(ClientNotWorking):
            self._request(self.NOTHING_MATCHED)
        return

    def test_a_real_error_is_never_tolerated(self):
        "Tolerance is for silence, not for the client saying what went wrong."
        with self.assertRaises(ClientNotWorking):
            self._request(
                {'status': False, 'error': 'disk full'},
                tolerate_nothing_matched=True
            )
        return

    def test_a_bad_api_key_still_says_so(self):
        with self.assertRaises(CredentialInvalid):
            self._request(
                {'status': False, 'error': 'API Key Incorrect'},
                tolerate_nothing_matched=True
            )
        return


def _client():
    "A SABnzbd client with the bits `delete_download` reads, and no more."
    client = SAB.SABnzbd.__new__(SAB.SABnzbd)
    client.ssn = MagicMock()
    client.known_ids = {'abc'}
    for read_only, value in (('base_url', 'https://sab.example.com'),
                             ('api_token', 'key')):
        patcher = patch.object(
            type(client), read_only, property(lambda self, v=value: v))
        patcher.start()
    return client


class deleting_a_finished_job(unittest.TestCase):
    def tearDown(self):
        patch.stopall()
        return

    def test_asks_both_sections_and_tolerates_the_empty_one(self):
        client = _client()

        with patch.object(SAB.SABnzbd, '_api_request') as request:
            client.delete_download('abc', delete_files=True)

        asked = [call.kwargs for call in request.call_args_list]
        self.assertEqual([a['mode'] for a in asked], ['queue', 'history'])
        self.assertTrue(all(a['tolerate_nothing_matched'] for a in asked))
        return

    def test_and_forgets_the_job_afterwards(self):
        client = _client()

        with patch.object(SAB.SABnzbd, '_api_request'):
            client.delete_download('abc', delete_files=False)

        self.assertNotIn('abc', client.known_ids)
        return
