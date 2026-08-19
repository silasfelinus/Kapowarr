import unittest
from unittest.mock import Mock, patch

from backend.features import import_lists
from backend.features.import_list_task import ImportListSync
from backend.features.tasks import task_library


class remote_cbl_import_lists(unittest.TestCase):
    @staticmethod
    def _definition(enable_auto=False):
        return {
            'id': 1,
            'name': 'My List',
            'provider': 'remote_cbl',
            'source_url': 'https://example.test/list.cbl',
            'enabled': True,
            'enable_auto': enable_auto,
            'root_folder_id': 7,
            'monitored': True,
            'monitor_new_issues': True,
            'search_on_add': False,
        }

    @staticmethod
    def _entries():
        return [
            {'comicvine_volume_id': 101},
            {'comicvine_volume_id': 101},
            {'comicvine_volume_id': 202},
            {'comicvine_volume_id': 303},
            {'comicvine_volume_id': None},
        ]

    def test_source_url_only_accepts_http_and_https(self):
        self.assertEqual(
            import_lists._validate_source_url('https://example.test/list.cbl'),
            'https://example.test/list.cbl',
        )
        self.assertEqual(
            import_lists._validate_source_url('http://example.test/list.cbl'),
            'http://example.test/list.cbl',
        )
        for value in ('', 'file:///tmp/list.cbl', 'javascript:alert(1)'):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    import_lists._validate_source_url(value)

    def test_preview_sync_deduplicates_exact_ids_and_never_guesses(self):
        with patch.object(
            import_lists, 'get_import_list',
            return_value=self._definition(enable_auto=False),
        ), patch.object(
            import_lists, '_fetch_remote_cbl',
            return_value=b'<ReadingList/>',
        ), patch.object(
            import_lists, 'parse_cbl',
            return_value=('My List', self._entries()),
        ), patch.object(
            import_lists, '_record_sync',
        ) as record_sync, patch.object(
            import_lists.Library, 'add',
        ) as add_volume:
            summary = import_lists.sync_import_list(1)

        self.assertEqual(summary['item_count'], 5)
        self.assertEqual(summary['exact_volume_count'], 3)
        self.assertEqual(summary['unresolved_count'], 1)
        self.assertEqual(summary['added_count'], 0)
        add_volume.assert_not_called()
        record_sync.assert_called_once()

    def test_auto_add_uses_only_new_non_excluded_exact_volume_ids(self):
        fake_db = Mock()
        fake_db.execute.return_value.fetchalldict.return_value = [
            {'comicvine_id': 101}
        ]

        with patch.object(
            import_lists, 'get_import_list',
            return_value=self._definition(enable_auto=True),
        ), patch.object(
            import_lists, '_fetch_remote_cbl',
            return_value=b'<ReadingList/>',
        ), patch.object(
            import_lists, 'parse_cbl',
            return_value=('My List', self._entries()),
        ), patch.object(
            import_lists, 'get_import_list_exclusions',
            return_value=[{'comicvine_volume_id': 202}],
        ), patch.object(
            import_lists, 'get_db',
            return_value=fake_db,
        ), patch.object(
            import_lists, 'commit',
        ), patch.object(
            import_lists, '_wait_for_metadata_slot',
            return_value=True,
        ), patch.object(
            import_lists.Library, 'add',
            return_value=99,
        ) as add_volume:
            summary = import_lists.sync_import_list(1)

        add_volume.assert_called_once_with(
            303,
            7,
            True,
            monitor_scheme=import_lists.MonitorScheme.ALL,
            monitor_new_issues=True,
            auto_search=False,
        )
        self.assertEqual(summary['already_added_count'], 1)
        self.assertEqual(summary['excluded_count'], 1)
        self.assertEqual(summary['added_count'], 1)
        self.assertEqual(summary['unresolved_count'], 1)

    def test_enabled_lists_share_one_metadata_pacing_clock(self):
        definitions = [
            {'id': 1, 'enabled': True},
            {'id': 2, 'enabled': False},
            {'id': 3, 'enabled': True},
        ]
        clocks = []

        def fake_sync(import_list_id, should_stop, request_clock):
            clocks.append(request_clock)
            return {'import_list_id': import_list_id, 'added_count': 0}

        with patch.object(
            import_lists, 'get_import_lists', return_value=definitions
        ), patch.object(
            import_lists, 'sync_import_list', side_effect=fake_sync
        ):
            summaries = import_lists.sync_enabled_import_lists()

        self.assertEqual(
            [summary['import_list_id'] for summary in summaries],
            [1, 3],
        )
        self.assertIs(clocks[0], clocks[1])

    def test_import_list_task_is_registered_and_runs_every_twelve_hours(self):
        self.assertIs(task_library['import_list_sync'], ImportListSync)
        self.assertEqual(import_lists.IMPORT_LIST_SYNC_INTERVAL_SECONDS, 43200)
        self.assertEqual(import_lists.IMPORT_LIST_CV_RESOURCE_DELAY, 30.0)


if __name__ == '__main__':
    unittest.main()
