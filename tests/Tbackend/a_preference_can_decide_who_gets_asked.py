# -*- coding: utf-8 -*-

"""Source Priority ordered results. It could not stop a search.

Every protocol was searched at once and the preference broke ties between
otherwise-equal results. That is the right default -- it finds the most,
fastest -- and it assumes every protocol costs the same to ask.

Silas's does not. Three pro Usenet accounts allow around ten thousand
queries a day between them; three public torrent indexers allow a hundred
(torrentdownload 50, limetorrents 25, 1337x 25). Under the concurrent
search every issue spends the scarce quota whether the plentiful one had
it or not, so the torrent side is exhausted within the first ~33 issues of
a sweep over thousands of volumes. Silas, 2026-09-01: "I would rather miss
something on the first go around, and then pick them up later as we drip
torrent checks, then delays everything."

`first_match` works down Source Priority and stops at the first protocol
that actually has the issue. Whether the results *match* is the question,
not whether there are any: an indexer returns fifty rows for a title it
does not carry, and stopping on those would spend the preferred protocol's
answer on nothing while never asking the one that had it.
"""

import unittest
from asyncio import run
from unittest.mock import patch

from backend.base.definitions import DownloadType
from backend.features import search as S


class _Source:
    """One indexer. Records that it was asked."""

    def __init__(self, query, asked, name, results):
        self.query = query
        self._asked = asked
        self._name = name
        self._results = results

    async def search(self, session):
        self._asked.append(self._name)
        return list(self._results)


def _source(name, asked, results):
    return lambda query: _Source(query, asked, name, results)


def _result(title, matches):
    return {'link': title, 'title': title, '_matches': matches}


def _search(sources_by_type, accepts=None, order=None):
    asked = []
    built = {
        download_type: [_source(name, asked, results)
                        for name, results in sources]
        for download_type, sources in sources_by_type.items()
    }
    plan = {download_type: ['batman'] for download_type in built}

    class _Sources:
        sources = built

    with patch.object(S, 'SearchSources', _Sources), \
            patch.object(S, 'AsyncSession', _Session), \
            patch.object(
                S, 'ordered_download_types',
                lambda types: list(order or types)
            ):
        results = run(S.search_planned_queries(plan, accepts))

    return asked, results


class _Session:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _has_match(results):
    return any(result.get('_matches') for result in results)


USENET = DownloadType.USENET
TORRENT = DownloadType.TORRENT
DIRECT = DownloadType.DIRECT


class without_a_predicate_nothing_changes(unittest.TestCase):
    def test_every_protocol_is_still_asked(self):
        asked, results = _search(
            {
                USENET: [('nzbgeek', [_result('a', True)])],
                TORRENT: [('1337x', [_result('b', True)])]
            },
            order=[USENET, TORRENT]
        )

        self.assertEqual(sorted(asked), ['1337x', 'nzbgeek'])
        self.assertEqual(len(results), 2)


class stopping_at_the_first_match(unittest.TestCase):
    def test_a_preferred_hit_leaves_the_scarce_source_unasked(self):
        asked, results = _search(
            {
                USENET: [('nzbgeek', [_result('a', True)])],
                TORRENT: [('1337x', [_result('b', True)])]
            },
            accepts=_has_match,
            order=[USENET, TORRENT]
        )

        self.assertEqual(asked, ['nzbgeek'])
        self.assertEqual([r['link'] for r in results], ['a'])

    def test_a_preferred_miss_falls_through(self):
        asked, results = _search(
            {
                USENET: [('nzbgeek', [])],
                TORRENT: [('1337x', [_result('b', True)])]
            },
            accepts=_has_match,
            order=[USENET, TORRENT]
        )

        self.assertEqual(asked, ['nzbgeek', '1337x'])
        self.assertEqual([r['link'] for r in results], ['b'])

    def test_rows_that_do_not_match_are_not_a_hit(self):
        # The failure mode that would make this worse than not gating: an
        # indexer answering a title it does not carry.
        asked, _ = _search(
            {
                USENET: [('nzbgeek', [_result('unrelated', False)])],
                TORRENT: [('1337x', [_result('b', True)])]
            },
            accepts=_has_match,
            order=[USENET, TORRENT]
        )

        self.assertEqual(asked, ['nzbgeek', '1337x'])

    def test_every_source_in_one_tier_is_asked_together(self):
        # Tiers are protocols, not indexers: the three usenet indexers are
        # peers and are searched concurrently as before.
        asked, _ = _search(
            {
                USENET: [
                    ('nzbgeek', []), ('nzbsu', [_result('a', True)]),
                    ('nzbplanet', [])
                ],
                TORRENT: [('1337x', [_result('b', True)])]
            },
            accepts=_has_match,
            order=[USENET, TORRENT]
        )

        self.assertEqual(sorted(asked), ['nzbgeek', 'nzbplanet', 'nzbsu'])

    def test_nobody_matching_still_returns_everything_gathered(self):
        # So the caller ranks over the same pool it would have had anyway.
        asked, results = _search(
            {
                USENET: [('nzbgeek', [_result('a', False)])],
                TORRENT: [('1337x', [_result('b', False)])]
            },
            accepts=_has_match,
            order=[USENET, TORRENT]
        )

        self.assertEqual(asked, ['nzbgeek', '1337x'])
        self.assertEqual(sorted(r['link'] for r in results), ['a', 'b'])

    def test_the_order_is_the_users(self):
        asked, _ = _search(
            {
                USENET: [('nzbgeek', [_result('a', True)])],
                TORRENT: [('1337x', [_result('b', True)])]
            },
            accepts=_has_match,
            order=[TORRENT, USENET]
        )

        self.assertEqual(asked, ['1337x'])


if __name__ == '__main__':
    unittest.main()
