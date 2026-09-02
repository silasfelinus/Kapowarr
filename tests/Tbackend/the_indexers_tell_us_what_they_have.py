# -*- coding: utf-8 -*-

"""Kapowarr only ever asked questions. It never listened.

One search per volume, then one per missing issue. An hour of that on
2026-09-02 reached eight volumes out of thousands, spent every indexer's
daily quota doing it, and found five things.

Newznab and Torznab both answer `t=search` with no `q` by handing back their
most recent releases, so one request per indexer covers the whole library.
This is the standing "RSS sync" the rest of the *arr suite runs on, and
Kapowarr had no equivalent -- not removed, never built: it began around
GetComics, which has no feed, and the indexer support that does came later
without the model being revisited. Silas: "Omg, we don't have rss sync!!!!!!
I'm honestly flabbergasted."
"""

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from backend.features import release_feed as RF


def _release(series, issue_number, link, year=2026):
    """A release as an indexer's parser hands it over."""
    return {
        'link': link,
        'display_title': f'{series} {issue_number:03.0f} ({year})',
        'source': 'Usenet indexer',
        'series': series,
        'year': year,
        'volume_number': None,
        'special_version': None,
        'issue_number': float(issue_number),
        'annual': False
    }


class an_empty_query_is_a_feed_request(unittest.TestCase):
    """`t=search` with no `q` is what makes one request cover everything, so
    the query must be left out rather than sent empty -- an indexer given
    `q=` may treat it as a search for nothing."""

    def test_torznab_omits_the_query(self):
        from backend.implementations import torznab as TZ
        sent = self._params_of(TZ, TZ.search_torznab_indexer, '')

        self.assertNotIn('q', sent)
        self.assertEqual(sent['t'], 'search')
        return

    def test_torznab_still_sends_a_real_one(self):
        from backend.implementations import torznab as TZ
        sent = self._params_of(TZ, TZ.search_torznab_indexer, 'Save Now')

        self.assertEqual(sent['q'], 'Save Now')
        return

    def test_newznab_omits_the_query(self):
        from backend.implementations import indexers_core as IC
        sent = self._params_of(IC, IC.search_indexer, '')

        self.assertNotIn('q', sent)
        self.assertEqual(sent['t'], 'search')
        return

    def test_newznab_still_sends_a_real_one(self):
        from backend.implementations import indexers_core as IC
        sent = self._params_of(IC, IC.search_indexer, 'Save Now')

        self.assertEqual(sent['q'], 'Save Now')
        return

    @staticmethod
    def _params_of(module, search, query, categories='', enabled=False):
        from asyncio import run

        captured = {}

        async def capture(url, params=None, **kwargs):
            captured.update(params or {})
            return ''

        indexer = MagicMock()
        indexer.base_url = 'https://indexer.example.com'
        indexer.api_key = 'k'
        indexer.category_filter_enabled = enabled
        indexer.categories = categories
        session = MagicMock()
        session.get_text = capture

        with patch.object(module, 'search_delay', return_value=0.0), \
                patch.object(module, 'register_rate_limit_scope'), \
                patch.object(module, 'rate_limit_cooldown_remaining',
                             return_value=0.0):
            run(search(session, indexer, query))

        return captured


class a_feed_has_to_be_narrowed_to_comics(unittest.TestCase):
    """`category_filter_enabled` decides whether a *search* is confined to
    comics, which is a reasonable thing to turn off: the query does the
    narrowing. A feed request has no query, so on a general-purpose tracker
    its hundred most recent items would be films and television and no comic
    would ever appear in the window. Without this the whole feature is noise
    on exactly the indexers most people run."""

    CATEGORY = '7030'

    def test_a_torznab_feed_asks_for_comics_even_with_the_filter_off(self):
        from backend.implementations import torznab as TZ
        sent = an_empty_query_is_a_feed_request._params_of(
            TZ, TZ.search_torznab_indexer, '', categories='', enabled=False)

        self.assertEqual(sent['cat'], self.CATEGORY)
        return

    def test_a_torznab_feed_prefers_the_categories_the_user_configured(self):
        from backend.implementations import torznab as TZ
        sent = an_empty_query_is_a_feed_request._params_of(
            TZ, TZ.search_torznab_indexer, '', categories='8000,8010',
            enabled=False)

        self.assertEqual(sent['cat'], '8000,8010')
        return

    def test_a_torznab_search_still_obeys_the_setting(self):
        "Turning the filter off must still widen an actual search."
        from backend.implementations import torznab as TZ
        sent = an_empty_query_is_a_feed_request._params_of(
            TZ, TZ.search_torznab_indexer, 'Save Now', categories='7030',
            enabled=False)

        self.assertNotIn('cat', sent)
        return

    def test_a_torznab_search_narrows_when_the_setting_is_on(self):
        from backend.implementations import torznab as TZ
        sent = an_empty_query_is_a_feed_request._params_of(
            TZ, TZ.search_torznab_indexer, 'Save Now', categories='7030',
            enabled=True)

        self.assertEqual(sent['cat'], '7030')
        return

    def test_a_newznab_feed_asks_for_comics(self):
        "Newznab indexers have no category setting here at all."
        from backend.implementations import indexers_core as IC
        sent = an_empty_query_is_a_feed_request._params_of(
            IC, IC.search_indexer, '')

        self.assertEqual(sent['cat'], self.CATEGORY)
        return

    def test_a_newznab_search_is_left_wide(self):
        from backend.implementations import indexers_core as IC
        sent = an_empty_query_is_a_feed_request._params_of(
            IC, IC.search_indexer, 'Save Now')

        self.assertNotIn('cat', sent)
        return


class deciding_what_the_library_wants(unittest.TestCase):
    def _worth_grabbing(self, releases, volume_of, open_issues, matches=True):
        """Run the decision with the library and matcher stubbed."""
        def match_volume(parsed, index=None, described_as='', quiet=False):
            return volume_of.get(parsed['series'])

        def manual_search(volume_id, issue_id=None, already_fetched=None):
            release = already_fetched[0]
            return [{**release, 'match': matches, 'match_issue': None}]

        with patch.object(RF, 'match_parsed_to_library_volume', match_volume), \
                patch.object(RF, 'LibraryIndex', MagicMock()), \
                patch.object(RF, 'wanted_issues_of',
                             side_effect=lambda v: open_issues.get(v, [])), \
                patch('backend.features.search.manual_search', manual_search):
            return RF.releases_worth_grabbing(releases)

    def test_a_release_for_a_missing_issue_is_taken(self):
        taken = self._worth_grabbing(
            [_release('Save Now', 4, 'https://x/4')],
            volume_of={'Save Now': 19},
            open_issues={19: [(101, 4.0)]}
        )

        self.assertEqual(taken, [('https://x/4', 19, None)])
        return

    def test_a_release_for_an_issue_already_here_is_not(self):
        taken = self._worth_grabbing(
            [_release('Save Now', 4, 'https://x/4')],
            volume_of={'Save Now': 19},
            open_issues={19: [(101, 7.0)]}   # 7 is missing, 4 is not
        )

        self.assertEqual(taken, [])
        return

    def test_a_release_for_a_volume_we_do_not_have_is_not(self):
        taken = self._worth_grabbing(
            [_release('Some Other Book', 1, 'https://x/1')],
            volume_of={},
            open_issues={}
        )

        self.assertEqual(taken, [])
        return

    def test_a_volume_with_nothing_missing_is_not_asked_about_further(self):
        taken = self._worth_grabbing(
            [_release('Save Now', 4, 'https://x/4')],
            volume_of={'Save Now': 19},
            open_issues={19: []}
        )

        self.assertEqual(taken, [])
        return

    def test_a_release_the_matcher_rejects_is_not(self):
        "Blocklisted, wrong year, wrong volume -- the matcher's call, not ours."
        taken = self._worth_grabbing(
            [_release('Save Now', 4, 'https://x/4')],
            volume_of={'Save Now': 19},
            open_issues={19: [(101, 4.0)]},
            matches=False
        )

        self.assertEqual(taken, [])
        return

    def test_the_same_link_twice_is_taken_once(self):
        taken = self._worth_grabbing(
            [_release('Save Now', 4, 'https://x/4'),
             _release('Save Now', 4, 'https://x/4')],
            volume_of={'Save Now': 19},
            open_issues={19: [(101, 4.0)]}
        )

        self.assertEqual(len(taken), 1)
        return


class one_indexer_being_down(unittest.TestCase):
    def test_does_not_cost_the_poll_the_others(self):
        from asyncio import run

        async def good(*a, **k):
            return [_release('Save Now', 4, 'https://x/4')]

        async def bad(*a, **k):
            raise RuntimeError('indexer on fire')

        with patch.object(RF, 'Indexers') as newznab, \
                patch.object(RF, 'TorznabIndexers') as torznab, \
                patch.object(RF, 'search_indexer', bad), \
                patch.object(RF, 'search_torznab_indexer', good), \
                self.assertLogs(level='WARNING'):
            newznab.get_enabled.return_value = [MagicMock()]
            torznab.get_enabled.return_value = [MagicMock()]
            releases = run(RF._fetch_all(MagicMock()))

        self.assertEqual([r['link'] for r in releases], ['https://x/4'])
        return

    def test_the_same_release_from_two_indexers_is_one_release(self):
        from asyncio import run

        async def same(*a, **k):
            return [_release('Save Now', 4, 'https://x/4')]

        with patch.object(RF, 'Indexers') as newznab, \
                patch.object(RF, 'TorznabIndexers') as torznab, \
                patch.object(RF, 'search_indexer', same), \
                patch.object(RF, 'search_torznab_indexer', same):
            newznab.get_enabled.return_value = [MagicMock()]
            torznab.get_enabled.return_value = [MagicMock()]
            releases = run(RF._fetch_all(MagicMock()))

        self.assertEqual(len(releases), 1)
        return


class a_whole_poll(unittest.TestCase):
    def test_it_reads_queues_and_says_what_it_did(self):
        queued = []
        handler = MagicMock()
        handler.add_multiple = lambda entries: queued.extend(entries)

        with patch.object(RF, 'fetch_recent_releases',
                          return_value=([_release('Save Now', 4, 'https://x/4')], 3)), \
                patch.object(RF, 'releases_worth_grabbing',
                             return_value=[('https://x/4', 19, None)]), \
                patch('backend.features.download_queue.DownloadHandler',
                      return_value=handler):
            summary = RF.poll_release_feeds()

        self.assertEqual(queued, [('https://x/4', 19, None, False)])
        self.assertEqual(summary['queued'], 1)
        self.assertIn('3 indexer(s)', RF.describe_sync(summary))
        return

    def test_a_stop_lands_before_anything_is_queued(self):
        with patch.object(RF, 'fetch_recent_releases',
                          return_value=([_release('Save Now', 4, 'https://x/4')], 1)), \
                patch.object(RF, 'releases_worth_grabbing',
                             return_value=[('https://x/4', 19, None)]), \
                patch('backend.features.download_queue.DownloadHandler') as dh:
            summary = RF.poll_release_feeds(lambda: True)

        dh.assert_not_called()
        self.assertEqual(summary['queued'], 0)
        return

    def test_no_indexers_is_not_an_error(self):
        with patch.object(RF, 'fetch_recent_releases', return_value=([], 0)):
            summary = RF.poll_release_feeds()

        self.assertEqual(summary['indexers'], 0)
        self.assertEqual(
            RF.describe_sync(summary), 'No indexers to read a feed from')
        return


class it_runs_without_being_asked(unittest.TestCase):
    def test_it_is_enrolled_on_a_quarter_hourly_interval(self):
        from backend.internals.settings import task_intervals

        self.assertEqual(task_intervals['release_feed_sync'], 900)
        return

    def test_the_task_is_findable_by_its_action(self):
        from backend.features.tasks_core import task_library

        self.assertIn('release_feed_sync', task_library)
        return

    def test_one_poll_costs_one_request_per_indexer(self):
        """The whole point: the cost does not grow with the library. A
        targeted sweep of the same library needs one search per volume and
        one per missing issue."""
        from asyncio import run

        asked = []

        async def count(session, indexer, query):
            asked.append((indexer, query))
            return []

        with patch.object(RF, 'Indexers') as newznab, \
                patch.object(RF, 'TorznabIndexers') as torznab, \
                patch.object(RF, 'search_indexer', count), \
                patch.object(RF, 'search_torznab_indexer', count):
            newznab.get_enabled.return_value = [MagicMock(), MagicMock()]
            torznab.get_enabled.return_value = [MagicMock()]
            run(RF._fetch_all(MagicMock()))

        self.assertEqual(len(asked), 3)
        self.assertEqual([query for _, query in asked], ['', '', ''])
        return


class a_poll_says_what_it_did(unittest.TestCase):
    """Four hours of feed syncs on 2026-09-02 said only that they had
    finished. The interesting number -- how many releases were read, and why
    the ones that were passed over were passed over -- reached the task
    message and nothing else, so a log could not tell a working feed from a
    silent one. A poll that queues nothing has to say whether that is because
    the library is complete or because nothing it saw belonged to it."""

    @staticmethod
    def _summary(**counts):
        base = dict(indexers=3, releases=100, matched=0, queued=0,
                    unknown=0, ambiguous=0, already_held=0)
        base.update(counts)
        return RF.FeedSyncSummary(**base)

    def test_a_poll_that_queued_something_says_so(self):
        described = RF.describe_sync(self._summary(matched=2, queued=2))

        self.assertIn('2 queued', described)
        self.assertIn('3 indexer(s)', described)
        return

    def test_a_complete_library_reads_differently_from_an_irrelevant_feed(self):
        complete = RF.describe_sync(self._summary(already_held=100))
        irrelevant = RF.describe_sync(self._summary(unknown=100))

        self.assertIn('already held', complete)
        self.assertIn('not in the library', irrelevant)
        self.assertNotEqual(complete, irrelevant)
        return

    def test_an_empty_feed_does_not_invent_reasons(self):
        described = RF.describe_sync(self._summary(releases=0))

        self.assertNotIn('already held', described)
        self.assertNotIn('not in the library', described)
        return

    def test_an_empty_feed_is_the_one_most_worth_saying_out_loud(self):
        "It is what four silent hours of polling looked like."
        with patch.object(RF, 'fetch_recent_releases', return_value=([], 3)), \
                self.assertLogs(level='INFO') as logged:
            RF.poll_release_feeds()

        self.assertIn('0 recent release(s)', '\n'.join(logged.output))
        return

    def test_the_poll_writes_it_to_the_log(self):
        with patch.object(
                RF, 'fetch_recent_releases',
                return_value=([_release('Save Now', 4, 'https://x/4')], 2)), \
                patch.object(RF, 'releases_worth_grabbing', return_value=[]), \
                self.assertLogs(level='INFO') as logged:
            RF.poll_release_feeds()

        self.assertIn('2 indexer(s)', '\n'.join(logged.output))
        return

    def test_the_reasons_are_counted_as_releases_are_judged(self):
        rows = [_release('Save Now', 4, 'https://x/4'),
                _release('Unknown Book', 1, 'https://x/u')]

        def match_volume(parsed, index=None, described_as='', quiet=False):
            return 19 if parsed['series'] == 'Save Now' else None

        def manual_search(volume_id, issue_id=None, already_fetched=None):
            return [{**already_fetched[0], 'match': True, 'match_issue': None}]

        counts = {}
        with patch.object(RF, 'match_parsed_to_library_volume', match_volume), \
                patch.object(RF, 'LibraryIndex', MagicMock()), \
                patch.object(RF, 'wanted_issues_of', return_value=[]), \
                patch('backend.features.search.manual_search', manual_search):
            RF.releases_worth_grabbing(rows, counts=counts)

        self.assertEqual(counts['unknown'], 1)
        self.assertEqual(counts['already_held'], 1)
        return

    def test_an_ambiguous_release_is_not_announced_every_poll(self):
        """The feed sees the same releases every quarter of an hour. A file
        someone dropped in the watched folder still gets its line."""
        from backend.features import watched_folder_import as WFI

        index = MagicMock()
        index.volume_ids = [1, 2]
        index.data.return_value = MagicMock(title='Spider-Man')
        index.issues.return_value = ([], {})

        with patch.object(WFI, 'match_title', return_value=True), \
                patch.object(WFI, 'file_importing_filter', return_value=True), \
                patch.object(WFI.LOGGER, 'info') as said:
            WFI.match_parsed_to_library_volume(
                {'series': 'Spider-Man'}, index, 'x', quiet=True)
            said.assert_not_called()

            WFI.match_parsed_to_library_volume(
                {'series': 'Spider-Man'}, index, 'x')
            said.assert_called_once()
        return
