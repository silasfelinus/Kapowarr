import errno
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from backend.base.definitions import SpecialVersion, VolumeData
from backend.features import post_processing as pp
from backend.features import search
from backend.features.pack_normalization import prune_downloaded_range_files
from backend.features.seed_import import (hardlink_or_copy_file,
                                          hardlink_or_copy_path)


class SeedImportTest(unittest.TestCase):
    def test_file_uses_hardlink_on_same_filesystem(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = os.path.join(tmp, 'source.cbz')
            target = os.path.join(tmp, 'library', 'Issue 001.cbz')
            with open(source, 'wb') as handle:
                handle.write(b'comic-bytes')

            linked = hardlink_or_copy_file(source, target)

            self.assertTrue(linked)
            self.assertEqual(os.stat(source).st_ino, os.stat(target).st_ino)
            os.rename(target, target + '.renamed')
            self.assertTrue(os.path.exists(source))
            with open(source, 'rb') as handle:
                self.assertEqual(handle.read(), b'comic-bytes')

    @patch('backend.features.seed_import.link', side_effect=OSError(errno.EXDEV, 'cross-device'))
    def test_file_falls_back_to_copy_when_hardlink_fails(self, _link):
        with tempfile.TemporaryDirectory() as tmp:
            source = os.path.join(tmp, 'source.cbz')
            target = os.path.join(tmp, 'library', 'Issue 001.cbz')
            with open(source, 'wb') as handle:
                handle.write(b'comic-bytes')

            linked = hardlink_or_copy_file(source, target)

            self.assertFalse(linked)
            with open(target, 'rb') as handle:
                self.assertEqual(handle.read(), b'comic-bytes')

    def test_directory_tree_hardlinks_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = os.path.join(tmp, 'source')
            target = os.path.join(tmp, 'library')
            os.makedirs(os.path.join(source, 'nested'))
            first = os.path.join(source, '001.cbz')
            second = os.path.join(source, 'nested', '002.cbz')
            for filepath in (first, second):
                with open(filepath, 'wb') as handle:
                    handle.write(filepath.encode())

            linked, copied = hardlink_or_copy_path(source, target)

            self.assertEqual((linked, copied), (2, 0))
            self.assertEqual(
                os.stat(first).st_ino,
                os.stat(os.path.join(target, '001.cbz')).st_ino
            )
            self.assertEqual(
                os.stat(second).st_ino,
                os.stat(os.path.join(target, 'nested', '002.cbz')).st_ino
            )


class TorrentRootHandlingTest(unittest.TestCase):
    @patch.object(pp, 'commit')
    @patch.object(pp, 'scan_files')
    @patch.object(pp, 'Settings')
    @patch.object(pp, 'Volume')
    def test_seeding_copy_handles_single_file_torrent(
        self,
        Volume,
        Settings,
        scan_files,
        _commit
    ):
        with tempfile.TemporaryDirectory() as tmp:
            source_folder = os.path.join(tmp, 'downloads')
            library_folder = os.path.join(tmp, 'library')
            os.makedirs(source_folder)
            os.makedirs(library_folder)
            source = os.path.join(source_folder, 'Batman 001.cbz')
            with open(source, 'wb') as handle:
                handle.write(b'comic')

            Volume.return_value.vd.folder = library_folder
            Settings.return_value.sv.rename_downloaded_files = False
            download = SimpleNamespace(
                files=[source],
                volume_id=1,
                covered_issues=1.0
            )

            pp.copy_file_torrent(download)

            target = os.path.join(library_folder, 'Batman 001.cbz')
            self.assertEqual(download.files, [target])
            self.assertEqual(os.stat(source).st_ino, os.stat(target).st_ino)
            scan_files.assert_called_once()

            pp.reset_file_link(download)
            self.assertEqual(download.files, [source])

    @patch.object(pp, 'commit')
    @patch.object(pp, 'scan_files')
    @patch.object(pp, 'Settings')
    @patch.object(pp, 'Volume')
    def test_complete_mode_moves_single_file_without_folder_extraction(
        self,
        Volume,
        Settings,
        scan_files,
        _commit
    ):
        with tempfile.TemporaryDirectory() as tmp:
            source_folder = os.path.join(tmp, 'downloads')
            library_folder = os.path.join(tmp, 'library')
            os.makedirs(source_folder)
            os.makedirs(library_folder)
            source = os.path.join(source_folder, 'Batman 001.cbz')
            with open(source, 'wb') as handle:
                handle.write(b'comic')

            Volume.return_value.vd.folder = library_folder
            Settings.return_value.sv.rename_downloaded_files = False
            download = SimpleNamespace(
                files=[source],
                volume_id=1,
                filename_body='Batman 001',
                covered_issues=1.0
            )

            pp.move_torrent_to_dest(download)

            target = os.path.join(library_folder, 'Batman 001.cbz')
            self.assertEqual(download.files, [target])
            self.assertTrue(os.path.isfile(target))
            self.assertFalse(os.path.exists(source))
            scan_files.assert_called_once()

    def test_inode_metadata_processing_waits_until_seed_source_is_deleted(self):
        self.assertNotIn(
            pp.set_file_properties,
            pp.PostProcessorTorrentsCopy.actions_seeding
        )
        success = pp.PostProcessorTorrentsCopy.actions_success
        self.assertIn(pp.set_file_properties, success)
        self.assertLess(
            success.index(pp.delete_file),
            success.index(pp.set_file_properties)
        )


class RangePruningTest(unittest.TestCase):
    @patch('backend.features.pack_normalization.Volume')
    def test_direct_issue_files_already_owned_are_discarded(self, Volume):
        with tempfile.TemporaryDirectory() as tmp:
            paths = []
            for number in (1, 2, 3):
                filepath = os.path.join(tmp, f'Batman {number:03d}.cbz')
                with open(filepath, 'wb') as handle:
                    handle.write(str(number).encode())
                paths.append(filepath)

            Volume.return_value.get_issues.return_value = [
                SimpleNamespace(calculated_issue_number=1.0, files=[]),
                SimpleNamespace(calculated_issue_number=2.0, files=[{'id': 9}]),
                SimpleNamespace(calculated_issue_number=3.0, files=[])
            ]
            download = SimpleNamespace(
                covered_issues=(1.0, 3.0),
                volume_id=1,
                files=list(paths)
            )

            removed = prune_downloaded_range_files(download)

            self.assertEqual(removed, 1)
            self.assertEqual(
                [os.path.basename(path) for path in download.files],
                ['Batman 001.cbz', 'Batman 003.cbz']
            )
            self.assertFalse(os.path.exists(paths[1]))


class RangeSearchTest(unittest.TestCase):
    def _volume_data(self):
        return VolumeData(
            id=1,
            comicvine_id=1,
            title='Batman',
            alt_title=None,
            year=2016,
            volume_number=1,
            description='',
            site_url='',
            publisher=None,
            monitored=True,
            monitor_new_issues=True,
            root_folder=1,
            folder='/Batman',
            custom_folder=False,
            special_version=SpecialVersion.NORMAL,
            special_version_locked=False,
            last_cv_fetch=0
        )

    @patch('backend.implementations.matching.blocklist_contains', return_value=False)
    def test_issue_search_accepts_range_covering_requested_issue(self, _blocked):
        result = {
            'series': 'Batman',
            'year': 2016,
            'volume_number': 1,
            'special_version': None,
            'issue_number': (1.0, 5.0),
            'annual': False,
            'link': 'https://example/pack',
            'display_title': 'Batman 1-5',
            'source': 'test'
        }
        years = {float(number): 2016 for number in range(1, 6)}

        match = search._match_search_result(
            result,
            self._volume_data(),
            [],
            years,
            3.0
        )

        self.assertTrue(match['match'])
        self.assertEqual(result['issue_number'], (1.0, 5.0))

    @patch('backend.features.search.manual_search')
    @patch('backend.features.search.Volume')
    def test_volume_fallback_queues_covering_pack_only_once(
        self,
        Volume,
        manual_search
    ):
        volume = Volume.return_value
        volume.get_data.return_value = SimpleNamespace(
            monitored=True,
            special_version=SpecialVersion.NORMAL
        )
        volume.get_issues.return_value = [
            SimpleNamespace(id=3, calculated_issue_number=3.0),
            SimpleNamespace(id=4, calculated_issue_number=4.0)
        ]
        volume.get_open_issues.return_value = [(3, 3.0), (4, 4.0)]

        def get_issue(issue_id):
            issue = MagicMock()
            issue.get_data.return_value = SimpleNamespace(
                monitored=True,
                calculated_issue_number=float(issue_id)
            )
            issue.get_files.return_value = []
            return issue

        volume.get_issue.side_effect = get_issue
        pack = {
            'match': True,
            'link': 'https://example/1-5',
            'issue_number': (1.0, 5.0),
            'special_version': None
        }

        def search_results(_volume_id, issue_id=None):
            return [] if issue_id is None else [dict(pack)]

        manual_search.side_effect = search_results

        chosen = search.auto_search(1)

        self.assertEqual(len(chosen), 1)
        self.assertEqual(chosen[0]['link'], 'https://example/1-5')
        manual_search.assert_any_call(1, 3)
        self.assertNotIn(
            ((1, 4),),
            [call.args for call in manual_search.call_args_list]
        )


if __name__ == '__main__':
    unittest.main()
