# -*- coding: utf-8 -*-

"""Asking an indexer for "Save Now #2" hands back the same rows as "Save Now".

Silas, reading a sweep: "why don't we batch searches so that 'catwoman' is a
search that tells us all the catwoman issues and volumes... if rate limits
are important, I *definitely* donut think that one catwoman query should
result in 80 requests."

He is right, and the log says so plainly:

    Search finished in 80.7s: Save Now (2025)     -- 159 result(s)
    Search finished in 80.2s: Save Now (2025) #1  -- 159 result(s)
    Search finished in 79.9s: Save Now (2025) #2  -- 159 result(s)
    Search finished in 79.9s: Save Now (2025) #3  -- 159 result(s)

An indexer query is a text search on the series, so the issue number barely
narrows what comes back; Kapowarr picks the issue out locally afterwards.
Four fetches of the same 159 rows, eighty seconds apiece, to do three matches
that needed no fetch at all.

The volume search now hands its rows to the issue searches. A real query is
still made when those rows hold nothing for an issue -- an indexer caps how
many rows it returns, so a phrasing naming the issue can surface something
that fell off the end of the broad one -- and when the volume search came
back with nothing to hand on.
"""

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from backend.base.definitions import SpecialVersion
from backend.features import search as SR


def _volume(open_issues):
    volume = MagicMock()
    volume.get_data.return_value = SimpleNamespace(
        monitored=True,
        special_version=SpecialVersion.NORMAL,
        title='Save Now',
        alt_title=None,
        year=2025,
        volume_number=1
    )
    volume.get_issues.return_value = [
        SimpleNamespace(id=i, calculated_issue_number=float(i), date=None)
        for i, _ in open_issues
    ]
    volume.get_open_issues.return_value = list(open_issues)

    def get_issue(issue_id):
        issue = MagicMock()
        issue.get_data.return_value = SimpleNamespace(
            monitored=True, calculated_issue_number=float(issue_id),
            issue_number=str(issue_id))
        issue.get_files.return_value = []
        return issue

    volume.get_issue.side_effect = get_issue
    return volume


class the_volume_search_is_fetched_once(unittest.TestCase):
    OPEN = [(1, 1.0), (2, 2.0), (3, 3.0)]

    def _run(self, per_issue_hits):
        """Run a volume auto search and report every manual_search call.

        `per_issue_hits` decides whether the rows handed on match an issue.
        """
        fetches = []

        def fake_manual_search(volume_id, issue_id=None, already_fetched=None):
            fetches.append((issue_id, already_fetched is not None))
            if already_fetched is not None:
                return [dict(r) for r in already_fetched] if per_issue_hits \
                    else []
            if issue_id is None:
                # Rows for issues this volume does not have open, so the
                # volume-level combination picks none of them and every open
                # issue goes to the fan-out -- which is the path under test.
                return [{
                    'match': True, 'link': f'https://example/{n}',
                    'issue_number': float(n), 'special_version': None
                } for n in (91, 92, 93)]

            return [{
                'match': True, 'link': f'https://example/issue-{issue_id}',
                'issue_number': float(issue_id), 'special_version': None
            }]

        with patch.object(SR, 'Volume', return_value=_volume(self.OPEN)), \
                patch.object(SR, 'manual_search', fake_manual_search):
            SR.auto_search(1)

        return fetches

    def test_the_issue_searches_reuse_what_the_volume_search_brought_back(self):
        fetches = self._run(per_issue_hits=True)

        asked_the_indexers = [f for f in fetches if not f[1]]
        self.assertEqual(
            len(asked_the_indexers), 1,
            'only the volume-level search should reach an indexer'
        )
        self.assertIsNone(asked_the_indexers[0][0])
        return

    def test_an_issue_the_rows_cannot_answer_still_gets_a_real_query(self):
        """An indexer caps its rows, so a phrasing naming the issue can
        surface what fell off the end of the broad query. Recall first."""
        fetches = self._run(per_issue_hits=False)

        issues_asked_for_real = [f[0] for f in fetches if not f[1] and f[0]]
        self.assertTrue(issues_asked_for_real)
        return


class nothing_to_hand_on(unittest.TestCase):
    def test_an_empty_volume_search_does_not_silence_the_issue_searches(self):
        """`already_fetched=[]` must not read as "here are the rows, and
        there are none of them" -- that would turn a volume the indexers
        simply did not answer for into a volume that is never searched."""
        calls = []

        def fake_manual_search(volume_id, issue_id=None, already_fetched=None):
            calls.append((issue_id, already_fetched))
            if issue_id is None:
                return []
            return [{
                'match': True, 'link': 'https://example/found',
                'issue_number': 1.0, 'special_version': None
            }]

        with patch.object(SR, 'Volume', return_value=_volume([(1, 1.0)])), \
                patch.object(SR, 'manual_search', fake_manual_search):
            chosen = SR.auto_search(1)

        self.assertEqual([c['link'] for c in chosen], ['https://example/found'])
        self.assertIn((1, None), calls)
        return


class matching_without_asking(unittest.TestCase):
    def test_handing_rows_in_asks_nobody(self):
        rows = [{
            'match': True, 'link': 'https://example/1',
            'issue_number': 1.0, 'special_version': None
        }]
        volume = _volume([(1, 1.0)])

        # What is under test is that nothing is fetched. Matching a release
        # properly is `matching`'s job and has its own tests.
        with patch.object(SR, 'Volume', return_value=volume), \
                patch.object(SR, '_match_search_result',
                             return_value={'match': True,
                                           'match_issue': None}), \
                patch.object(SR, '_rank_search_result', return_value=0), \
                patch.object(SR, 'run') as searched:
            results = SR.manual_search(1, 1, already_fetched=rows)

        searched.assert_not_called()
        self.assertEqual(len(results), 1)
        return

    def test_without_them_it_searches_as_before(self):
        volume = _volume([(1, 1.0)])
        planned = []

        def plan(query_plan, accepts=None):
            planned.append(query_plan)
            return []

        with patch.object(SR, 'Volume', return_value=volume), \
                patch.object(SR, 'run', side_effect=lambda c: c), \
                patch.object(SR, 'search_planned_queries', plan), \
                patch.object(SR, 'reset_request_tally'), \
                patch.object(SR, 'SearchSources') as sources:
            sources.active_types.return_value = []
            SR.manual_search(1, 1)

        self.assertTrue(planned, 'it should have built and run a query plan')
        return


class the_rows_handed_on_are_all_of_them(unittest.TestCase):
    """Star Trek (1967), 2026-09-02: 142 releases came back for the volume,
    none of them matched *the volume* -- a release naming issue #34 is not a
    match for a search for the whole run -- and twenty-seven issue searches
    then went and asked the indexers for those same rows one at a time.

    `match` is answered against what the search asked for, so filtering by it
    before handing the rows on throws away exactly the ones the issue
    searches need.
    """

    def test_rows_that_did_not_match_the_volume_still_reach_the_issues(self):
        handed_on = []

        def fake_manual_search(volume_id, issue_id=None, already_fetched=None):
            if already_fetched is not None:
                handed_on.append(len(already_fetched))
                # They match once asked about the right issue.
                return [{**r, 'match': True} for r in already_fetched
                        if r['issue_number'] == float(issue_id)]

            if issue_id is None:
                # What the indexers return for the volume: one row per issue,
                # none of which is a match for the volume as a whole.
                return [{
                    'match': False, 'link': f'https://example/{n}',
                    'issue_number': float(n), 'special_version': None
                } for n in (1, 2, 3)]

            return []

        with patch.object(SR, 'Volume', return_value=_volume(
                [(1, 1.0), (2, 2.0), (3, 3.0)])), \
                patch.object(SR, 'manual_search', fake_manual_search):
            chosen = SR.auto_search(1)

        self.assertEqual(
            handed_on, [3, 3, 3],
            'every issue search should have been given all three rows'
        )
        self.assertEqual(
            sorted(c['link'] for c in chosen),
            ['https://example/1', 'https://example/2', 'https://example/3']
        )
        return
