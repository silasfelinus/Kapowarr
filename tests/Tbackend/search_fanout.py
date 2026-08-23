# -*- coding: utf-8 -*-

"""One search should not cost one request per phrasing."""

import unittest
from asyncio import run
from unittest.mock import patch

from backend.features import search as search_module
from backend.features.search import _probe_order, search_planned_queries


ISSUE_QUERIES = [
    'A Quiet Place Storm Warning #5 (2026)',
    'A Quiet Place Storm Warning Vol. 1 #5',
    'A Quiet Place Storm Warning #5',
    'A Quiet Place Storm Warning',
]


class the_broad_query_goes_first(unittest.TestCase):
    """Every specific phrasing is the broad one plus extra terms.

    Newznab and Torznab AND the terms in `q`, so the broad query returns a
    superset of what any of the others would. Sending them all spends a
    request per phrasing to receive results already contained in the first.
    """

    def test_the_shortest_query_is_probed_first(self):
        self.assertEqual(
            _probe_order(ISSUE_QUERIES)[0], 'A Quiet Place Storm Warning'
        )

    def test_every_other_phrasing_is_a_superset_of_it(self):
        # The property the ordering relies on, asserted rather than assumed.
        broad = set(_probe_order(ISSUE_QUERIES)[0].lower().split())
        for query in ISSUE_QUERIES:
            self.assertLessEqual(broad, set(query.lower().split()), query)

    def test_the_rest_keep_their_original_order(self):
        self.assertEqual(
            _probe_order(ISSUE_QUERIES)[1:],
            [q for q in ISSUE_QUERIES if q != 'A Quiet Place Storm Warning']
        )

    def test_a_single_query_is_left_alone(self):
        self.assertEqual(_probe_order(['Batman']), ['Batman'])

    def test_no_queries_is_not_an_error(self):
        self.assertEqual(_probe_order([]), [])


class _RecordingSource:
    """A search source that records every query it is asked for."""

    asked = []
    results_for = {}

    def __init__(self, query):
        self.query = query

    async def search(self, _session):
        type(self).asked.append(self.query)
        return list(type(self).results_for.get(self.query, []))


class widening_only_happens_when_needed(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        _RecordingSource.asked = []
        _RecordingSource.results_for = {}

    async def _run(self, queries):
        from backend.base.definitions import DownloadType

        plan = {DownloadType.USENET: queries}
        with patch.object(
            search_module.SearchSources, 'sources',
            {DownloadType.USENET: [_RecordingSource]}
        ), patch.object(
            search_module, 'ordered_download_types',
            return_value=[DownloadType.USENET]
        ), patch.object(search_module, 'AsyncSession', _NullSession):
            return await search_planned_queries(plan)

    async def test_one_request_when_the_broad_query_finds_something(self):
        _RecordingSource.results_for = {
            'A Quiet Place Storm Warning': [{'link': 'a'}, {'link': 'b'}]
        }

        results = await self._run(ISSUE_QUERIES)

        self.assertEqual(
            _RecordingSource.asked, ['A Quiet Place Storm Warning'],
            'four phrasings cost four requests before this'
        )
        self.assertEqual([r['link'] for r in results], ['a', 'b'])

    async def test_it_widens_when_the_broad_query_finds_nothing(self):
        """An indexer caps its result list, so a broad query against a
        prolific title can push the wanted release off the end."""
        _RecordingSource.results_for = {
            'A Quiet Place Storm Warning #5': [{'link': 'c'}]
        }

        results = await self._run(ISSUE_QUERIES)

        self.assertEqual([r['link'] for r in results], ['c'])
        self.assertEqual(
            _RecordingSource.asked[0], 'A Quiet Place Storm Warning'
        )
        self.assertIn('A Quiet Place Storm Warning #5', _RecordingSource.asked)

    async def test_it_stops_at_the_first_phrasing_that_answers(self):
        _RecordingSource.results_for = {
            'A Quiet Place Storm Warning Vol. 1 #5': [{'link': 'd'}],
            'A Quiet Place Storm Warning #5': [{'link': 'e'}],
        }

        await self._run(ISSUE_QUERIES)

        self.assertNotIn(
            'A Quiet Place Storm Warning #5', _RecordingSource.asked,
            'nothing after the first non-empty phrasing should be sent'
        )

    async def test_everything_empty_tries_them_all_and_returns_nothing(self):
        results = await self._run(ISSUE_QUERIES)

        self.assertEqual(results, [])
        self.assertEqual(len(_RecordingSource.asked), len(ISSUE_QUERIES))


class _NullSession:
    async def __aenter__(self):
        return None

    async def __aexit__(self, *_):
        return False


if __name__ == '__main__':
    unittest.main()
