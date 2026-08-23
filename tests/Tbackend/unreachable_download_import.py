# -*- coding: utf-8 -*-

"""A download nobody could import must not be filed as a success."""

import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from backend.base.definitions import DownloadState
from backend.features import post_processing as PP
from backend.implementations.download_clients import NZBDownload


def _download(files, title='A Year of Marvels'):
    return SimpleNamespace(
        id=1, files=list(files), title=title, volume_id=1, issue_id=None,
        web_link=None, web_title=title, web_sub_title=None,
        source_type=SimpleNamespace(value='usenet'),
        state=DownloadState.IMPORTING_STATE
    )


class nothing_at_the_path_is_not_a_success(unittest.TestCase):
    """Three titles sat in the client's finished folder showing Success.

    The client reports where *it* put the file. With no remote path
    mapping that path means nothing here, and every gate waved it
    through: `verify_archive` opened it, got FileNotFoundError, and
    reported UNSUPPORTED, which counts as ok. The move steps returned
    silently on their `if not exists(...)` guard. And `add_to_history`
    runs before the move, so the row was already written as a success.
    """

    def setUp(self):
        self.ran = []
        self.patches = [
            patch.object(PP, 'send_notification'),
            patch.object(PP, 'Settings'),
        ]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)

    def _run_success(self, download):
        recorded = {}

        def _remove_from_queue(dl):
            self.ran.append('remove_from_queue')

        def _add_to_history(dl):
            self.ran.append('add_to_history')
            recorded['success'] = dl.state != DownloadState.FAILED_STATE

        def _named(name):
            def action(dl):
                self.ran.append(name)
            return action

        with patch.object(PP, 'remove_from_queue', _remove_from_queue), \
                patch.object(PP, 'add_to_history', _add_to_history), \
                patch.object(PP, 'move_torrent_to_dest', _named('move')), \
                patch.object(PP, 'add_dl_to_blocklist', _named('blocklist')), \
                patch.object(PP, 'delete_file', _named('delete')), \
                patch.object(PP, 'failed_integrity_check', return_value=None):
            # The action lists are built at class definition time, so they
            # hold the original functions; rebuild them from the patched
            # module so the substitutions above are the ones that run.
            processor = type(
                'Probe', (PP.PostProcessorTorrentsComplete,), {
                    'actions_success': [
                        PP.remove_from_queue, PP.add_to_history,
                        PP.move_torrent_to_dest
                    ],
                    'actions_import_failed': [
                        PP.remove_from_queue, PP.add_to_history
                    ],
                }
            )
            processor.success(download)
        return recorded

    def test_it_is_recorded_as_a_failure(self):
        download = _download(['/pc/miss_kitty/prowlarr/kapowarr/A Year'])
        recorded = self._run_success(download)

        self.assertEqual(
            recorded.get('success'), False,
            'a download that never reached the library is not a success'
        )

    def test_the_import_steps_do_not_run_against_a_path_that_is_not_there(self):
        download = _download(['/pc/miss_kitty/prowlarr/kapowarr/A Year'])
        self._run_success(download)

        self.assertNotIn('move', self.ran)

    def test_the_release_is_not_blocklisted_for_it(self):
        """The release is fine. The path is wrong. Blocklisting would
        stand in the way of the retry that fixing the mapping enables."""
        download = _download(['/pc/miss_kitty/prowlarr/kapowarr/A Year'])
        self._run_success(download)

        self.assertNotIn('blocklist', self.ran)
        self.assertNotIn('delete', self.ran)

    def test_a_download_that_is_really_there_still_imports(self):
        with tempfile.TemporaryDirectory() as tmp:
            present = os.path.join(tmp, 'A Year of Marvels')
            os.makedirs(present)
            download = _download([present])
            recorded = self._run_success(download)

        self.assertEqual(recorded.get('success'), True)
        self.assertIn('move', self.ran)


class the_error_names_the_path_and_the_remedy(unittest.TestCase):
    def test_it_says_what_to_do(self):
        download = _download(['/pc/miss_kitty/prowlarr/kapowarr/A Year'])

        with patch.object(PP, 'send_notification'), \
                patch.object(
                    PP.PostProcessorTorrentsComplete, '_run_actions'
                ), \
                self.assertLogs(PP.LOGGER, level='ERROR') as logs:
            PP.PostProcessorTorrentsComplete.success(download)

        message = ' '.join(r.getMessage() for r in logs.records)
        self.assertIn('/pc/miss_kitty/prowlarr/kapowarr/A Year', message)
        self.assertIn('Remote Path Mapping', message)


class finding_it_without_a_mapping(unittest.TestCase):
    """Both sides commonly mount the same storage at different points."""

    def _nzb(self, download_folder):
        dl = NZBDownload.__new__(NZBDownload)
        dl._external_client = MagicMock()
        dl._external_client.id = 3
        dl._download_folder = download_folder
        dl._title = 'A Year of Marvels'
        return dl

    def test_the_mapped_path_wins_when_it_is_there(self):
        with tempfile.TemporaryDirectory() as tmp:
            real = os.path.join(tmp, 'mapped')
            os.makedirs(real)
            dl = self._nzb(tmp)

            with patch(
                'backend.implementations.download_clients.RemoteMappings'
                '.remote_to_local', return_value=real
            ):
                self.assertEqual(
                    dl._locate_completed_download('/remote/mapped'), real
                )

    def test_the_download_folder_is_tried_when_the_mapping_misses(self):
        with tempfile.TemporaryDirectory() as tmp:
            here = os.path.join(tmp, 'A Year of Marvels')
            os.makedirs(here)
            dl = self._nzb(tmp)

            with patch(
                'backend.implementations.download_clients.RemoteMappings'
                '.remote_to_local', side_effect=lambda cid, p: p
            ):
                found = dl._locate_completed_download(
                    '/pc/miss_kitty/prowlarr/kapowarr/A Year of Marvels'
                )

            self.assertEqual(found, here)

    def test_otherwise_it_reports_the_path_worth_mapping(self):
        with tempfile.TemporaryDirectory() as tmp:
            dl = self._nzb(tmp)
            remote = '/pc/miss_kitty/prowlarr/kapowarr/Nowhere'

            with patch(
                'backend.implementations.download_clients.RemoteMappings'
                '.remote_to_local', side_effect=lambda cid, p: p
            ):
                self.assertEqual(
                    dl._locate_completed_download(remote), remote
                )


if __name__ == '__main__':
    unittest.main()
