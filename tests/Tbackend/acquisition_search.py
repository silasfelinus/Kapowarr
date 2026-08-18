import unittest
from types import SimpleNamespace
from unittest.mock import patch

from backend.base.definitions import DownloadType, SpecialVersion
from backend.features import search
from backend.implementations.query_builders import QueryBuilders


class QueryBuilderTest(unittest.TestCase):
    def _volume(self, **overrides):
        data = {
            'title': 'Batman',
            'alt_title': None,
            'year': 2016,
            'volume_number': 3,
            'special_version': SpecialVersion.NORMAL
        }
        data.update(overrides)
        return SimpleNamespace(**data)

    def test_direct_and_usenet_have_independent_registered_builders(self):
        direct = QueryBuilders.get(DownloadType.DIRECT)
        usenet = QueryBuilders.get(DownloadType.USENET)

        self.assertIsNot(direct, usenet)
        self.assertEqual(
            direct.build(self._volume(), 'Batman', '42'),
            usenet.build(self._volume(), 'Batman', '42')
        )

    def test_issue_queries_preserve_current_search_policy(self):
        queries = QueryBuilders.get(DownloadType.DIRECT).build(
            self._volume(),
            'Batman',
            '42'
        )
        self.assertEqual(queries, [
            'Batman #42 (2016)',
            'Batman Vol. 3 #42',
            'Batman #42',
            'Batman'
        ])

    def test_yearless_volume_queries_do_not_leave_formatting_debris(self):
        queries = QueryBuilders.get(DownloadType.USENET).build(
            self._volume(year=None),
            'Batman',
            None
        )
        self.assertEqual(queries, [
            'Batman Vol. 3',
            'Batman',
        ])


class SearchCoordinatorTest(unittest.TestCase):
    def test_getcomics_and_newznab_are_registered_as_protocol_peers(self):
        self.assertIn(search.SearchGetComics, search.SearchSources.sources[
            DownloadType.DIRECT
        ])
        self.assertIn(search.SearchIndexers, search.SearchSources.sources[
            DownloadType.USENET
        ])

    @patch('backend.features.search.Volume')
    def test_manual_search_builds_a_query_plan_per_active_protocol(self, Volume):
        volume = Volume.return_value
        volume.get_data.return_value = SimpleNamespace(
            title='Batman',
            alt_title=None,
            year=2016,
            volume_number=3,
            special_version=SpecialVersion.NORMAL
        )
        volume.get_issues.return_value = []

        captured = []

        async def fake_search_planned_queries(plan):
            captured.append(plan)
            return []

        with patch.object(
            search,
            'search_planned_queries',
            side_effect=fake_search_planned_queries
        ):
            result = search.manual_search(7)

        self.assertEqual(result, [])
        self.assertEqual(len(captured), 1)
        self.assertEqual(set(captured[0]), {
            DownloadType.DIRECT,
            DownloadType.USENET
        })
        self.assertEqual(
            list(captured[0][DownloadType.DIRECT]),
            list(captured[0][DownloadType.USENET])
        )


if __name__ == '__main__':
    unittest.main()
