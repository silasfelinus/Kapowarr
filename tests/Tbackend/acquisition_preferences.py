# -*- coding: utf-8 -*-

from asyncio import run
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import AsyncMock, patch

from backend.base.definitions import DownloadType
from backend.features import acquisition_preferences as preferences
from backend.features.search import (SearchIndexers, SearchTorznab,
                                     _rank_search_result)


def group(title):
    return {
        'web_sub_title': title,
        'info': {
            'series': 'Batman',
            'year': 2020,
            'volume_number': 1,
            'special_version': None,
            'issue_number': 1.0,
            'annual': False
        },
        'links': {}
    }


def result(issue_number, match=True):
    return {
        'series': 'batman',
        'year': 2020,
        'volume_number': 1,
        'special_version': None,
        'issue_number': issue_number,
        'annual': False,
        'link': 'https://example.invalid/release',
        'display_title': 'Batman',
        'source': 'test',
        'match': match,
        'match_issue': None
    }


class acquisition_source_order(TestCase):
    def test_default_preserves_historical_protocol_order(self):
        with patch.object(
            preferences,
            'get_acquisition_preferences',
            return_value={
                'acquisition_source_preference': ['direct', 'torrent', 'usenet'],
                'getcomics_quality_preference': 'any',
                'pack_preference': 'neutral'
            }
        ):
            ordered = preferences.ordered_download_types([
                DownloadType.DIRECT,
                DownloadType.USENET,
                DownloadType.TORRENT
            ])

        self.assertEqual(ordered, [
            DownloadType.DIRECT,
            DownloadType.TORRENT,
            DownloadType.USENET
        ])

    def test_user_order_is_respected(self):
        with patch.object(
            preferences,
            'get_acquisition_preferences',
            return_value={
                'acquisition_source_preference': ['usenet', 'direct', 'torrent'],
                'getcomics_quality_preference': 'any',
                'pack_preference': 'neutral'
            }
        ):
            ordered = preferences.ordered_download_types([
                DownloadType.TORRENT,
                DownloadType.DIRECT,
                DownloadType.USENET
            ])

        self.assertEqual(ordered, [
            DownloadType.USENET,
            DownloadType.DIRECT,
            DownloadType.TORRENT
        ])


class indexer_priority_policy(TestCase):
    def test_priority_map_validation(self):
        self.assertEqual(
            preferences._validated_priority_map({
                'newznab:1': 1,
                'torznab:20': 100
            }),
            {'newznab:1': 1, 'torznab:20': 100}
        )

        invalid_values = (
            {'other:1': 1},
            {'newznab:1': 0},
            {'newznab:1': 101},
            {'newznab:1': True},
            {'newznab:1': '1'}
        )
        for value in invalid_values:
            with self.subTest(value=value):
                with self.assertRaises(Exception):
                    preferences._validated_priority_map(value)

    def test_priority_defaults_to_fifty(self):
        with patch.object(
            preferences,
            'get_acquisition_preferences',
            return_value={'indexer_priorities': {'newznab:7': 3}}
        ):
            self.assertEqual(preferences.indexer_priority('newznab', 7), 3)
            self.assertEqual(preferences.indexer_priority('newznab', 8), 50)
            self.assertEqual(preferences.indexer_priority('torznab', 7), 50)

    @patch('backend.features.search.search_indexer', new_callable=AsyncMock)
    @patch('backend.features.search.indexer_priority')
    @patch('backend.features.search.Indexers.get_enabled')
    def test_newznab_search_uses_priority_order(
        self, get_enabled, priority, search_indexer
    ):
        low = SimpleNamespace(id=1, title='Low')
        high = SimpleNamespace(id=2, title='High')
        get_enabled.return_value = [low, high]
        priority.side_effect = lambda protocol, indexer_id: {1: 90, 2: 5}[indexer_id]

        async def search_side_effect(session, indexer, query):
            return [{'source': indexer.title}]
        search_indexer.side_effect = search_side_effect

        found = run(SearchIndexers('Batman').search(object()))
        self.assertEqual([entry['source'] for entry in found], ['High', 'Low'])
        self.assertEqual(
            [call.args for call in priority.call_args_list],
            [('newznab', 1), ('newznab', 2)]
        )

    @patch('backend.features.search.search_torznab_indexer', new_callable=AsyncMock)
    @patch('backend.features.search.indexer_priority')
    @patch('backend.features.search.TorznabIndexers.get_enabled')
    def test_torznab_search_uses_priority_order(
        self, get_enabled, priority, search_indexer
    ):
        first = SimpleNamespace(id=3, title='First')
        second = SimpleNamespace(id=4, title='Second')
        get_enabled.return_value = [first, second]
        priority.side_effect = lambda protocol, indexer_id: {3: 1, 4: 80}[indexer_id]

        async def search_side_effect(session, indexer, query):
            return [{'source': indexer.title}]
        search_indexer.side_effect = search_side_effect

        found = run(SearchTorznab('Batman').search(object()))
        self.assertEqual([entry['source'] for entry in found], ['First', 'Second'])
        self.assertEqual(
            [call.args for call in priority.call_args_list],
            [('torznab', 3), ('torznab', 4)]
        )


class pack_policy(TestCase):
    def test_neutral_does_not_change_rank_component(self):
        with patch.object(
            preferences,
            'get_acquisition_preferences',
            return_value={
                'acquisition_source_preference': ['direct', 'torrent', 'usenet'],
                'getcomics_quality_preference': 'any',
                'pack_preference': 'neutral'
            }
        ):
            self.assertEqual(preferences.pack_preference_rank((1.0, 5.0)), 0)
            self.assertEqual(preferences.pack_preference_rank(3.0), 0)

    def test_prefer_and_avoid_reverse_range_tiebreak(self):
        base = {
            'acquisition_source_preference': ['direct', 'torrent', 'usenet'],
            'getcomics_quality_preference': 'any'
        }
        with patch.object(
            preferences,
            'get_acquisition_preferences',
            return_value={**base, 'pack_preference': 'prefer'}
        ):
            self.assertLess(
                preferences.pack_preference_rank((1.0, 5.0)),
                preferences.pack_preference_rank(3.0)
            )

        with patch.object(
            preferences,
            'get_acquisition_preferences',
            return_value={**base, 'pack_preference': 'avoid'}
        ):
            self.assertGreater(
                preferences.pack_preference_rank((1.0, 5.0)),
                preferences.pack_preference_rank(3.0)
            )

    @patch('backend.features.search.pack_preference_rank')
    def test_pack_policy_never_outranks_match_correctness(self, pack_rank):
        pack_rank.side_effect = lambda issue: 0 if isinstance(issue, tuple) else 1
        matched_single = _rank_search_result(
            result(3.0, match=True), 'batman', 1, (2020, 2020), 3.0
        )
        mismatched_pack = _rank_search_result(
            result((1.0, 5.0), match=False), 'batman', 1, (2020, 2020), 3.0
        )
        self.assertLess(matched_single, mismatched_pack)


def peer_result(issue_number=3.0, match=True, **availability):
    """A search result carrying (or deliberately omitting) peer counts.

    Passing no keyword arguments produces the GetComics/Newznab shape: the
    ``seeders``/``leechers`` keys are absent entirely, because
    ``SearchResultAvailabilityData`` is ``total=False``.
    """
    return {**result(issue_number, match=match), **availability}


class search_result_availability(TestCase):
    def test_absent_none_and_healthy_all_rank_neutrally(self):
        # A non-torrent source omits the key entirely; Torznab can send an
        # explicit None. Neither may be scored worse than a healthy torrent,
        # or the change quietly demotes every source that has no peer data.
        self.assertEqual(preferences.availability_rank(peer_result()), 0)
        self.assertEqual(
            preferences.availability_rank(peer_result(seeders=None)), 0
        )
        self.assertEqual(
            preferences.availability_rank(peer_result(seeders=1)), 0
        )
        self.assertEqual(
            preferences.availability_rank(peer_result(seeders=500)), 0
        )

    def test_only_an_explicit_zero_is_demoted(self):
        self.assertGreater(
            preferences.availability_rank(peer_result(seeders=0)),
            preferences.availability_rank(peer_result(seeders=1))
        )
        self.assertGreater(
            preferences.availability_rank(peer_result(seeders=0)),
            preferences.availability_rank(peer_result())
        )

    def test_negative_counts_are_treated_as_dead_not_as_best(self):
        # A malformed indexer response must not sort to the front.
        self.assertEqual(
            preferences.availability_rank(peer_result(seeders=-1)), 1
        )

    # pack_preference_rank reads the settings database; availability_rank
    # deliberately does not, because a dead release is not a user preference.
    # These cases neutralise the pack component so they exercise ranking
    # rather than the settings layer, matching the pattern above.
    @patch('backend.features.search.pack_preference_rank', return_value=0)
    def test_healthy_release_outranks_dead_one_all_else_equal(self, _pack_rank):
        healthy = _rank_search_result(
            peer_result(seeders=12), 'batman', 1, (2020, 2020), 3.0
        )
        dead = _rank_search_result(
            peer_result(seeders=0), 'batman', 1, (2020, 2020), 3.0
        )
        self.assertLess(healthy, dead)

    @patch('backend.features.search.pack_preference_rank', return_value=0)
    def test_source_without_peer_data_is_not_demoted(self, _pack_rank):
        no_data = _rank_search_result(
            peer_result(), 'batman', 1, (2020, 2020), 3.0
        )
        healthy = _rank_search_result(
            peer_result(seeders=12), 'batman', 1, (2020, 2020), 3.0
        )
        self.assertEqual(no_data, healthy)

    @patch('backend.features.search.pack_preference_rank', return_value=0)
    def test_availability_never_outranks_match_correctness(self, _pack_rank):
        # A well-seeded wrong issue is still the wrong issue.
        matched_dead = _rank_search_result(
            peer_result(match=True, seeders=0), 'batman', 1, (2020, 2020), 3.0
        )
        mismatched_healthy = _rank_search_result(
            peer_result(match=False, seeders=99), 'batman', 1, (2020, 2020), 3.0
        )
        self.assertLess(matched_dead, mismatched_healthy)

    @patch('backend.features.search.pack_preference_rank')
    def test_availability_outranks_pack_preference(self, pack_rank):
        # Preferring the shape of a release nobody can download is meaningless,
        # so availability is checked first.
        pack_rank.side_effect = lambda issue: 0 if isinstance(issue, tuple) else 1
        healthy_single = _rank_search_result(
            peer_result(3.0, seeders=12), 'batman', 1, (2020, 2020), 3.0
        )
        dead_pack = _rank_search_result(
            peer_result((1.0, 5.0), seeders=0), 'batman', 1, (2020, 2020), 3.0
        )
        self.assertLess(healthy_single, dead_pack)


class getcomics_quality(TestCase):
    def test_quality_labels_use_token_boundaries(self):
        self.assertEqual(preferences.getcomics_quality_label('Batman 001 (HD)'), 'hd')
        self.assertEqual(preferences.getcomics_quality_label('Batman 001 - SD'), 'sd')
        self.assertEqual(preferences.getcomics_quality_label('Shadowman 001'), 'unknown')
        self.assertEqual(preferences.getcomics_quality_label('Batman HDCover'), 'unknown')

    def test_hd_preference_orders_hd_unknown_sd(self):
        with patch.object(
            preferences,
            'get_acquisition_preferences',
            return_value={
                'acquisition_source_preference': ['direct', 'torrent', 'usenet'],
                'getcomics_quality_preference': 'hd',
                'pack_preference': 'neutral'
            }
        ):
            ordered = preferences.order_getcomics_groups([
                group('Batman 001 SD'),
                group('Batman 001'),
                group('Batman 001 HD')
            ])

        self.assertEqual(
            [entry['web_sub_title'] for entry in ordered],
            ['Batman 001 HD', 'Batman 001', 'Batman 001 SD']
        )

    def test_sd_preference_reverses_explicit_variants(self):
        with patch.object(
            preferences,
            'get_acquisition_preferences',
            return_value={
                'acquisition_source_preference': ['direct', 'torrent', 'usenet'],
                'getcomics_quality_preference': 'sd',
                'pack_preference': 'neutral'
            }
        ):
            ordered = preferences.order_getcomics_groups([
                group('Batman 001 HD'),
                group('Batman 001 SD')
            ])

        self.assertEqual(
            [entry['web_sub_title'] for entry in ordered],
            ['Batman 001 SD', 'Batman 001 HD']
        )

    def test_any_preference_preserves_page_order(self):
        original = [
            group('Batman 001 SD'),
            group('Batman 001'),
            group('Batman 001 HD')
        ]
        with patch.object(
            preferences,
            'get_acquisition_preferences',
            return_value={
                'acquisition_source_preference': ['direct', 'torrent', 'usenet'],
                'getcomics_quality_preference': 'any',
                'pack_preference': 'neutral'
            }
        ):
            ordered = preferences.order_getcomics_groups(original)

        self.assertEqual(
            [entry['web_sub_title'] for entry in ordered],
            [entry['web_sub_title'] for entry in original]
        )


# The rating components _rank_search_result() appends, in the order it appends
# them. That ORDER is the actual ranking policy -- it is what decides that a
# well-seeded wrong issue loses to a correct dead one -- and until now it was
# documented only in inline comments and enforced only by pairwise tests that
# each pinned one adjacent boundary. Every one of those can still pass while two
# components are silently transposed, or a new component is inserted at the wrong
# tier. This table states the whole order in one place.
#
# Each entry degrades EXACTLY ONE component relative to BASELINE_RESULT, listed
# most significant first:
#   name       -- what the component means
#   mutation   -- the change to the search result that degrades it
#   pack_rank  -- what the (patched) pack_preference_rank returns for this case
#
# pack_preference_rank is patched rather than driven through a real preference
# because in production it and the issue-number tier share one input
# (result['issue_number']), so there is no result that degrades the pack tier
# alone. Patching its return value is what lets the pack tier be varied on its
# own; it is the one coupling in the list worth knowing about.
RANKING_TIERS = [
    ('match', {'match': False}, 0),
    ('title word overlap', {'series': 'batman annual special'}, 0),
    ('volume/year', {'volume_number': 2}, 0),
    ('peer availability', {'seeders': 0}, 0),
    ('pack preference', {}, 1),
    ('issue-number fit', {'issue_number': 7.0}, 0)
]

BASELINE_RESULT = {'seeders': 12}
BASELINE_QUERY = ('batman', 1, (2020, 2020), 3.0)


def rank_with_pack(overrides, pack_rank):
    """Rank a result built from the baseline plus `overrides`."""
    with patch(
        'backend.features.search.pack_preference_rank', return_value=pack_rank
    ):
        return _rank_search_result(
            {**result(3.0), **BASELINE_RESULT, **overrides}, *BASELINE_QUERY
        )


class search_ranking_tier_order(TestCase):
    def test_each_entry_degrades_exactly_its_own_component(self):
        # Guards the table itself: if a mutation leaks into a neighbouring
        # component, the ordering assertion below could pass for the wrong
        # reason. This also fails loudly when a new component is inserted,
        # which is the point -- the table must be updated deliberately.
        baseline = rank_with_pack({}, 0)
        self.assertEqual(len(baseline), len(RANKING_TIERS))

        for index, (name, mutation, pack_rank) in enumerate(RANKING_TIERS):
            with self.subTest(tier=name):
                degraded = rank_with_pack(mutation, pack_rank)
                differing = [
                    position
                    for position, (before, after) in enumerate(
                        zip(baseline, degraded)
                    )
                    if before != after
                ]
                self.assertEqual(differing, [index])
                self.assertGreater(degraded[index], baseline[index])

    def test_full_tier_order_in_one_place(self):
        # Rank lists compare lexicographically, so a result degraded at tier N
        # is worse than one degraded at any later tier, whatever the magnitudes
        # involved. Sorting therefore reproduces the policy exactly backwards:
        # the untouched result first, then the least significant degradation,
        # up to the most significant one last.
        ranked = [('unblemished', rank_with_pack({}, 0))] + [
            (name, rank_with_pack(mutation, pack_rank))
            for name, mutation, pack_rank in RANKING_TIERS
        ]

        self.assertEqual(
            [name for name, _ in sorted(ranked, key=lambda entry: entry[1])],
            [
                'unblemished',
                'issue-number fit',
                'pack preference',
                'peer availability',
                'volume/year',
                'title word overlap',
                'match'
            ]
        )


class neutral_ranking_components(TestCase):
    """Every preference-driven component must be a no-op when not configured.

    pack_preference_rank, getcomics_quality_rank and availability_rank each
    share an unwritten contract: under their neutral setting they return 0 for
    *every* result, so they add the same constant to every rating and preserve
    the historical order exactly. Each is asserted individually elsewhere for
    one or two inputs; this states the shared contract across the shapes that
    actually reach them.
    """

    NEUTRAL_PREFERENCES = {
        'acquisition_source_preference': ['direct', 'torrent', 'usenet'],
        'getcomics_quality_preference': 'any',
        'pack_preference': 'neutral'
    }

    def test_neutral_components_return_zero_for_every_result(self):
        cases = [
            (
                'pack_preference_rank',
                preferences.pack_preference_rank,
                [(1.0, 5.0), 3.0, None]
            ),
            (
                'getcomics_quality_rank',
                preferences.getcomics_quality_rank,
                [
                    (group('Batman 001 HD'),),
                    (group('Batman 001 SD'),),
                    (group('Batman 001'),),
                    ()
                ]
            ),
            (
                # availability_rank has no setting -- it is not a preference.
                # Its neutral case is the absence of an explicit zero, which
                # every non-torrent source is in permanently.
                'availability_rank',
                preferences.availability_rank,
                [peer_result(), peer_result(seeders=None), peer_result(seeders=1),
                 peer_result(seeders=500)]
            )
        ]

        with patch.object(
            preferences,
            'get_acquisition_preferences',
            return_value=self.NEUTRAL_PREFERENCES
        ):
            for name, component, inputs in cases:
                for value in inputs:
                    with self.subTest(component=name, value=value):
                        self.assertEqual(component(value), 0)
