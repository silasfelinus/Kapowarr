import unittest
from asyncio import run
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from backend.base.custom_exceptions import EnqueuingDownloadFailure
from backend.base.definitions import (Constants,
                                      EnqueuingDownloadFailureReason)
from backend.features.download_queue import DownloadHandler
from backend.implementations import download_preppers as dp


class DownloadPrepperRegistryTest(unittest.TestCase):
    def test_getcomics_link_resolves_to_gc_prepper(self):
        prepper = dp.DownloadPreppers.get_for_link(
            Constants.GC_SITE_URL + '/comic/batman'
        )
        self.assertIs(prepper, dp.GetComicsDownloadPrepper)
        self.assertEqual(prepper.identifier, 'gc')

    @patch.object(dp.Indexers, 'find_by_link', return_value=MagicMock())
    def test_newznab_link_resolves_to_nzb_prepper(self, _find):
        prepper = dp.DownloadPreppers.get_for_link(
            'https://indexer.example/api?t=get&id=42'
        )
        self.assertIs(prepper, dp.NewznabDownloadPrepper)
        self.assertEqual(prepper.identifier, 'nzb')

    @patch.object(dp.Indexers, 'find_by_link', return_value=None)
    def test_unknown_link_has_no_prepper(self, _find):
        self.assertIsNone(
            dp.DownloadPreppers.get_for_link('https://example.invalid/file')
        )

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


class the_blocklist_is_only_for_releases_that_are_gone(unittest.TestCase):
    """Blocklisting is one-way: a release on it is never asked about again,
    so nothing establishes later that the link was fine all along.

    Every failed fetch used to land there. On 2026-09-02 fourteen releases
    were recorded as broken in two seconds, and whether the indexer had
    said they were gone or merely answered badly was not written down
    anywhere.
    """

    def _prep(self, prepper, reason, factory):
        failure = EnqueuingDownloadFailure(reason)
        with patch.object(dp, factory, AsyncMock(side_effect=failure)), \
                patch.object(dp, 'add_to_blocklist') as blocklist:
            with self.assertRaises(EnqueuingDownloadFailure):
                run(prepper.prepare(
                    'https://indexer.example/download/1', 1, None
                ))
        return blocklist

    def test_a_release_the_indexer_says_is_gone_is_blocklisted(self):
        for prepper, factory in (
            (dp.NewznabDownloadPrepper, 'create_nzb_download'),
            (dp.TorznabDownloadPrepper, 'create_torznab_download')
        ):
            with self.subTest(prepper=prepper.identifier):
                blocklist = self._prep(
                    prepper,
                    EnqueuingDownloadFailureReason.LINK_BROKEN,
                    factory
                )
                blocklist.assert_called_once()

    def test_an_indexer_that_could_not_be_reached_costs_nothing(self):
        for prepper, factory in (
            (dp.NewznabDownloadPrepper, 'create_nzb_download'),
            (dp.TorznabDownloadPrepper, 'create_torznab_download')
        ):
            with self.subTest(prepper=prepper.identifier):
                blocklist = self._prep(
                    prepper,
                    EnqueuingDownloadFailureReason.SOURCE_UNAVAILABLE,
                    factory
                )
                blocklist.assert_not_called()
