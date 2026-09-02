# -*- coding: utf-8 -*-

"""One indexer ran out and took every other indexer with it.

2026-09-01, 20:24:36. Prowlarr's indexer 38 reported its daily quota gone and
asked for 27152 seconds -- seven and a half hours. Kapowarr recorded that
cooldown against the *hostname*, and Prowlarr puts every indexer behind one
hostname, telling them apart by a path prefix:

    https://prowlarr.example.com/38/api      <- out of quota
    https://prowlarr.example.com/9/api       <- untouched
    https://prowlarr.example.com/33/api      <- untouched

So all of them went dark together, including three pro Usenet accounts with
thousands of requests a day between them, and the Search All sweep spent the
next two hours asking nobody anything. Silas: "the queue is still empty and
history shows nothing downloaded for two hours."

Exactly backwards, too: the one indexer with nothing left is the reason to
ask the others.
"""

import unittest
from asyncio import run
from unittest.mock import MagicMock, patch

from backend.base import helpers


class _NullLock:
    "Stands in for the pacing lock without needing a running loop."

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class a_cooldown_belongs_to_the_indexer_that_earned_it(unittest.TestCase):
    HOST = 'https://prowlarr.example.com'

    def setUp(self):
        helpers.clear_rate_limits()
        self.addCleanup(helpers.clear_rate_limits)
        for indexer in (38, 9, 33):
            helpers.register_rate_limit_scope(f'{self.HOST}/{indexer}/api')
        return

    def _cooling(self, url):
        return helpers.rate_limit_cooldown_remaining(url) > 0

    def test_the_indexer_that_hit_its_quota_is_held(self):
        helpers.note_rate_limit(f'{self.HOST}/38/api', 27152.0)

        self.assertTrue(self._cooling(f'{self.HOST}/38/api'))
        return

    def test_its_neighbours_behind_the_same_host_are_not(self):
        helpers.note_rate_limit(f'{self.HOST}/38/api', 27152.0)

        self.assertFalse(self._cooling(f'{self.HOST}/9/api'))
        self.assertFalse(self._cooling(f'{self.HOST}/33/api'))
        return

    def test_the_hold_covers_the_indexer_s_other_endpoints(self):
        """An indexer's base URL names one endpoint; the links it returns
        name others beside it. Out of quota is out of quota for all."""
        helpers.note_rate_limit(f'{self.HOST}/38/api', 27152.0)

        self.assertTrue(self._cooling(
            f'{self.HOST}/38/download?apikey=x&link=y'))
        return

    def test_a_download_link_can_set_the_hold_too(self):
        "The 429 may arrive on the grab rather than the search."
        helpers.note_rate_limit(f'{self.HOST}/9/download?apikey=x', 900.0)

        self.assertTrue(self._cooling(f'{self.HOST}/9/api'))
        self.assertFalse(self._cooling(f'{self.HOST}/38/api'))
        return


class a_site_that_rations_as_a_whole_still_does(unittest.TestCase):
    """GetComics is one site, not a rack of indexers. A limit from one page
    of it has to stop the rest, which is what keying by hostname always got
    right and must keep getting right."""

    def setUp(self):
        helpers.clear_rate_limits()
        self.addCleanup(helpers.clear_rate_limits)
        return

    def test_an_unregistered_host_is_held_as_one(self):
        helpers.note_rate_limit('https://getcomics.org/page/6', 900.0)

        self.assertGreater(
            helpers.rate_limit_cooldown_remaining(
                'https://getcomics.org/other-comics/kaya-36-2026/'),
            0
        )
        return

    def test_a_registered_indexer_does_not_change_that(self):
        helpers.register_rate_limit_scope('https://prowlarr.example.com/38/api')
        helpers.note_rate_limit('https://getcomics.org/page/6', 900.0)

        self.assertGreater(
            helpers.rate_limit_cooldown_remaining('https://getcomics.org/x/y'),
            0
        )
        return

    def test_a_host_with_no_indexer_path_is_still_the_whole_host(self):
        "An indexer that lives at the root of its own hostname."
        helpers.register_rate_limit_scope('https://nzbgeek.example.com')
        helpers.note_rate_limit('https://nzbgeek.example.com/api', 900.0)

        self.assertGreater(
            helpers.rate_limit_cooldown_remaining(
                'https://nzbgeek.example.com/getnzb/abc'),
            0
        )
        return


class the_indexers_register_themselves(unittest.TestCase):
    """The registry is only right if the indexers actually populate it, so
    search each protocol for real and look at what it recorded."""

    URL = 'https://prowlarr.example.com/38/api'

    def setUp(self):
        helpers.clear_rate_limits()
        self.addCleanup(helpers.clear_rate_limits)
        return

    def _search(self, module, search):
        indexer = MagicMock()
        indexer.base_url = self.URL
        indexer.api_key = 'k'
        indexer.category_filter_enabled = False
        indexer.categories = ''
        session = MagicMock()

        async def no_body(*args, **kwargs):
            return ''

        session.get_text = no_body

        with patch.object(module, 'search_delay', return_value=0.0), \
                patch.object(module, '_request_state',
                             return_value=(_NullLock(), {}, 'k')):
            run(search(session, indexer, 'query'))

        # Its neighbour must be unaffected by its cooldown, which is only
        # true if the scope was registered.
        helpers.note_rate_limit(self.URL, 900.0)
        return (
            helpers.rate_limit_cooldown_remaining(self.URL),
            helpers.rate_limit_cooldown_remaining(
                'https://prowlarr.example.com/9/api')
        )

    def test_a_torznab_search_rations_its_indexer_alone(self):
        from backend.implementations import torznab as TZ

        held, neighbour = self._search(TZ, TZ.search_torznab_indexer)

        self.assertGreater(held, 0)
        self.assertEqual(neighbour, 0.0)
        return

    def test_a_newznab_search_rations_its_indexer_alone(self):
        from backend.implementations import indexers_core as IC

        held, neighbour = self._search(IC, IC.search_indexer)

        self.assertGreater(held, 0)
        self.assertEqual(neighbour, 0.0)
        return
