import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from zipfile import ZipFile

from backend.base.definitions import FileConstants
from backend.base.files import list_files
from backend.features import post_processing as pp
from backend.features.pack_normalization import (
    archive_contains_complete_issues,
    issue_number_overlaps_missing,
    normalize_downloaded_range_pack,
)


class PackNormalizationTest(unittest.TestCase):
    def test_cbz_pack_alias_detects_nested_issue_containers(self):
        with tempfile.TemporaryDirectory() as tmp:
            filepath = os.path.join(tmp, 'Series 001-100.cbz')
            with ZipFile(filepath, 'w') as archive:
                archive.writestr('Series 001.cbz', b'issue-one')
                archive.writestr('Series 002.cbr', b'issue-two')

            self.assertTrue(archive_contains_complete_issues(filepath))

    def test_monolithic_cbz_is_not_treated_as_issue_pack(self):
        with tempfile.TemporaryDirectory() as tmp:
            filepath = os.path.join(tmp, 'Series 001-100.cbz')
            with ZipFile(filepath, 'w') as archive:
                archive.writestr('001.jpg', b'page-one')
                archive.writestr('002.jpg', b'page-two')

            self.assertFalse(archive_contains_complete_issues(filepath))

    @patch('backend.features.pack_normalization.run_rar')
    def test_cbr_alias_uses_rar_listing(self, run_rar):
        run_rar.return_value = SimpleNamespace(
            returncode=0,
            stdout='Series 001.cbz\nSeries 002.cbz\n'
        )

        self.assertTrue(
            archive_contains_complete_issues('/library/Series 001-100.cbr')
        )
        run_rar.assert_called_once_with([
            'lb', '-inul', '/library/Series 001-100.cbr'
        ])

    def test_range_overlap_checks_any_missing_issue(self):
        missing = {4.0, 9.0}
        self.assertTrue(issue_number_overlaps_missing((1.0, 5.0), missing))
        self.assertTrue(issue_number_overlaps_missing(9.0, missing))
        self.assertFalse(issue_number_overlaps_missing((10.0, 20.0), missing))

    @patch('backend.features.pack_normalization.Volume')
    @patch('backend.features.pack_normalization.extract_files_from_folder')
    def test_range_pack_keeps_only_issue_files_that_are_missing(
        self,
        extract_files_from_folder,
        Volume
    ):
        with tempfile.TemporaryDirectory() as tmp:
            pack = os.path.join(tmp, 'Series 001-003.zip')
            with ZipFile(pack, 'w') as archive:
                archive.writestr('Series 001.cbz', b'one')
                archive.writestr('Series 002.cbz', b'two')
                archive.writestr('Series 003.cbz', b'three')

            volume = Volume.return_value
            volume.vd.folder = tmp
            volume.get_issues.return_value = [
                SimpleNamespace(calculated_issue_number=1.0, files=[]),
                SimpleNamespace(
                    calculated_issue_number=2.0,
                    files=[{'filepath': '/library/Series 002.cbz'}]
                ),
                SimpleNamespace(calculated_issue_number=3.0, files=[])
            ]

            def fake_extract(folder, volume_id):
                files = list_files(folder, FileConstants.SCANNABLE_EXTENSIONS)
                names = sorted(os.path.basename(filepath) for filepath in files)
                return [os.path.join(tmp, name) for name in names]

            extract_files_from_folder.side_effect = fake_extract
            download = SimpleNamespace(
                covered_issues=(1.0, 3.0),
                volume_id=123,
                files=[pack]
            )

            changed = normalize_downloaded_range_pack(download)

            self.assertTrue(changed)
            self.assertFalse(os.path.exists(pack))
            self.assertEqual(
                [os.path.basename(filepath) for filepath in download.files],
                ['Series 001.cbz', 'Series 003.cbz']
            )
            self.assertTrue(download._normalized_range_pack)

    def test_direct_registration_normalizes_before_scanning(self):
        events = []
        download = SimpleNamespace(volume_id=8, files=['/library/pack.zip'])

        with patch.object(
            pp,
            'normalize_downloaded_range_pack',
            side_effect=lambda _: events.append('normalize') or False
        ), patch.object(
            pp,
            'scan_files',
            side_effect=lambda *args, **kwargs: events.append('scan')
        ):
            pp.add_file_to_database(download)

        self.assertEqual(events, ['normalize', 'scan'])


if __name__ == '__main__':
    unittest.main()
