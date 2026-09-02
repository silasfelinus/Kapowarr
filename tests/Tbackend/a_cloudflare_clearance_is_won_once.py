# -*- coding: utf-8 -*-

"""FlareSolverr was doing its work over and over and throwing it away.

Silas: "are we using flareresolver? I thought that would help some of our
issues, but it seems to do nothing."

Two answers. It could not have helped the issue he had -- it only engages on
a 403 carrying Cloudflare's challenge header, and what stopped his sweep was
429s from exhausted indexer quotas, which no amount of solving fixes.

But it also would not have worked well if it had been needed. Cloudflare
issues `cf_clearance` against a domain; Kapowarr filed the solved cookies and
user agent under the exact URL, query string and all. A clearance won for
`getcomics.org/?s=Kaya+36` was therefore never found again for
`getcomics.org/?s=Hellboy+11`, so every search re-challenged and every
challenge spun up and tore down a fresh FlareSolverr session -- which the
code's own comment notes is the slow part -- to redo work already done.
"""

import unittest
from unittest.mock import patch

from backend.base.definitions import Constants
from backend.implementations import flaresolverr as FS
from backend.implementations.flaresolverr import FlareSolverr, cleared_scope


class a_clearance_covers_the_domain_it_was_won_for(unittest.TestCase):
    SOLVED_FOR = 'https://getcomics.org/?s=Kaya+36'

    def setUp(self):
        FlareSolverr.ua_mapping.clear()
        FlareSolverr.cookie_mapping.clear()
        self.addCleanup(FlareSolverr.ua_mapping.clear)
        self.addCleanup(FlareSolverr.cookie_mapping.clear)

        self.fs = FlareSolverr.__new__(FlareSolverr)
        scope = cleared_scope(self.SOLVED_FOR)
        FlareSolverr.ua_mapping[scope] = 'SolvedUA/1.0'
        FlareSolverr.cookie_mapping[scope] = {'cf_clearance': 'abc'}
        return

    def _cookies(self, url):
        return self.fs.get_ua_cookies(url)[1]

    def test_another_search_on_the_same_site_reuses_it(self):
        self.assertEqual(
            self._cookies('https://getcomics.org/?s=Hellboy+11'),
            {'cf_clearance': 'abc'}
        )
        return

    def test_a_download_page_on_the_same_site_reuses_it(self):
        self.assertEqual(
            self._cookies('https://getcomics.org/other-comics/kaya-36-2026/'),
            {'cf_clearance': 'abc'}
        )
        return

    def test_the_user_agent_it_was_won_with_comes_with_it(self):
        "Cloudflare ties the clearance to the UA that solved it."
        self.assertEqual(
            self.fs.get_ua_cookies('https://getcomics.org/x')[0],
            'SolvedUA/1.0'
        )
        return

    def test_a_different_site_gets_nothing(self):
        self.assertEqual(self._cookies('https://elsewhere.example.com/x'), {})
        self.assertEqual(
            self.fs.get_ua_cookies('https://elsewhere.example.com/x')[0],
            Constants.DEFAULT_USERAGENT
        )
        return

    def test_an_unsolved_site_gets_the_default_user_agent(self):
        self.assertEqual(
            self.fs.get_ua_cookies('https://never-seen.example.com/')[0],
            Constants.DEFAULT_USERAGENT
        )
        return


class recognising_a_challenge(unittest.TestCase):
    def test_the_challenge_header_is_what_triggers_it(self):
        name, value = Constants.CF_CHALLENGE_HEADER

        self.assertTrue(FS.challenge_headers({name: value}))
        return

    def test_an_ordinary_refusal_is_not_a_challenge(self):
        "A 403 for a bad API key must not go to FlareSolverr."
        self.assertFalse(FS.challenge_headers({'server': 'nginx'}))
        return

    def test_cloudflare_without_the_header_is_said_out_loud(self):
        """The header is recent and not always sent. If one of these ever
        turns up in a log, that is the answer to why FlareSolverr sat idle --
        so it must not be silent."""
        with self.assertLogs(level='WARNING') as logged:
            asked = FS.challenge_headers({'server': 'cloudflare'})

        self.assertFalse(asked)
        self.assertIn('FlareSolverr', '\n'.join(logged.output))
        return

    def test_the_challenge_header_wins_over_the_near_miss(self):
        name, value = Constants.CF_CHALLENGE_HEADER

        with patch.object(FS.LOGGER, 'warning') as warned:
            self.assertTrue(FS.challenge_headers(
                {name: value, 'server': 'cloudflare'}))

        warned.assert_not_called()
        return
