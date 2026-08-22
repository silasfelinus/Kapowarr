import unittest
from asyncio import run
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from backend.base.definitions import Constants
from backend.features.download_queue import DownloadHandler
from backend.implementations import download_preppers as dp


class DownloadPrepperRegistryTest(unittest.TestCase):
    def test_getcomics_link_resolves_to_gc_prepper(self):
        prepper = dp.DownloadPreppers.get_for_link(
            Constants.GC_SITE_URL + '/comic/batman'
        )
        self.assertIs(prepper, dp.GetComicsDownloadPrepper)
        self.assertEqual(prepper.identifier, 'gc')

    @patch.object(dp.TorznabIndexers, 'find_by_link', return_value=None)
    @patch.object(dp.Indexers, 'find_by_link', return_value=MagicMock())
    def test_newznab_link_resolves_to_nzb_prepper(self, _find, _torznab_find):
        prepper = dp.DownloadPreppers.get_for_link(
            'https://indexer.example/api?t=get&id=42'
        )
        self.assertIs(prepper, dp.NewznabDownloadPrepper)
        self.assertEqual(prepper.identifier, 'nzb')

    @patch.object(dp.TorznabIndexers, 'find_by_link', return_value=None)
    @patch.object(dp.Indexers, 'find_by_link', return_value=None)
    def test_unknown_link_has_no_prepper(self, _find, _torznab_find):
        self.assertIsNone(
            dp.DownloadPreppers.get_for_link('https://example.invalid/file')
        )

    @patch.object(dp.Indexers, 'find_by_link', return_value=MagicMock())
    @patch.object(dp.TorznabIndexers, 'find_by_link', return_value=MagicMock())
    def test_same_host_torznab_route_is_not_claimed_as_nzb(
        self, _torznab_find, _newznab_find
    ):
        # Prowlarr can host both /33/api (Newznab) and /39/api (Torznab).
        # Even if Newznab's compatibility ownership recognises the shared host,
        # an explicitly configured Torznab feed path wins protocol ownership.
        self.assertFalse(dp.NewznabDownloadPrepper.matches(
            'https://prowlarr.example/39/api?t=get&id=torrent'
        ))

    def test_newznab_prepper_delegates_to_existing_download_factory(self):
        expected = MagicMock()
        with patch.object(
            dp,
            'create_nzb_download',
            new=AsyncMock(return_value=expected)
        ) as create:
            result = run(dp.NewznabDownloadPrepper.prepare(
                'https://indexer.example/get/42',
                7,
                11,
                True
            ))

        self.assertEqual(result, [expected])
        create.assert_awaited_once_with(
            'https://indexer.example/get/42', 7, 11, True
        )


class QueuePrepperDispatchTest(unittest.TestCase):
    def test_new_source_can_dispatch_without_a_downloadhandler_branch(self):
        handler = DownloadHandler.__new__(DownloadHandler)
        handler.queue = []
        handler.settings = SimpleNamespace(
            sv=SimpleNamespace(concurrent_direct_downloads=1)
        )
        handler._process_queue = MagicMock()
        handler._DownloadHandler__prepare_downloads_for_queue = MagicMock(
            return_value=[]
        )

        fake_prepper = MagicMock()
        fake_prepper.identifier = 'future-source'
        fake_prepper.prepare = AsyncMock(return_value=[])

        with patch.object(
            dp.DownloadPreppers,
            'get_for_link',
            return_value=fake_prepper
        ), patch.object(
            dp.DownloadPreppers,
            'get',
            return_value=fake_prepper
        ):
            # DownloadHandler imported the same registry class object, so
            # patching it here affects the queue's registry lookup as well.
            result, failure = run(handler.add(
                'https://future.example/release/1',
                volume_id=9,
                issue_id=12,
                force_match=True
            ))

        self.assertEqual(result, [])
        self.assertIsNone(failure)
        fake_prepper.prepare.assert_awaited_once_with(
            'https://future.example/release/1', 9, 12, True
        )
        handler._DownloadHandler__prepare_downloads_for_queue.assert_called_once_with(
            [], forced_match=True
        )
        handler._process_queue.assert_called_once_with()


if __name__ == '__main__':
    unittest.main()
