import json
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from backend.base.definitions import SpecialVersion
from backend.features import portable_metadata as pm


class portable_metadata_payload(unittest.TestCase):
    @staticmethod
    def _volume(folder='/library/Batman', comicvine_id=12345):
        data = SimpleNamespace(
            comicvine_id=comicvine_id,
            title='Batman',
            year=2020,
            volume_number=3,
            description='<p>Dark <b>Knight</b></p>',
            publisher='DC Comics',
            special_version=SpecialVersion.NORMAL,
            folder=folder,
        )
        volume = MagicMock()
        volume.get_data.return_value = data
        volume.vd = data
        volume.get_issues.return_value = [MagicMock(), MagicMock()]
        return volume

    def test_builds_mylar_102_shape_only_from_known_values(self):
        volume = self._volume()
        with patch.object(pm, 'Volume', return_value=volume), \
             patch.object(
                 pm.MetadataIdentityStore,
                 'get',
                 return_value={'comicvine': '12345', 'metron': '678'},
             ):
            payload = pm.build_series_json(7)

        self.assertEqual(payload['version'], '1.0.2')
        metadata = payload['metadata']
        self.assertEqual(metadata['type'], 'comicSeries')
        self.assertEqual(metadata['name'], 'Batman')
        self.assertEqual(metadata['comicid'], 12345)
        self.assertEqual(metadata['year'], 2020)
        self.assertEqual(metadata['volume'], 3)
        self.assertEqual(metadata['publisher'], 'DC Comics')
        self.assertEqual(metadata['description_text'], 'Dark Knight')
        self.assertEqual(metadata['description_formatted'], '<p>Dark <b>Knight</b></p>')
        self.assertEqual(metadata['total_issues'], 2)
        self.assertEqual(metadata['booktype'], 'Print')
        self.assertEqual(metadata['status'], 'Unknown')
        self.assertIsNone(metadata['publication_run'])
        self.assertNotIn('external_ids', metadata)

    def test_unknown_comicvine_identity_is_not_invented_from_other_provider(self):
        volume = self._volume(comicvine_id=None)
        with patch.object(pm, 'Volume', return_value=volume), \
             patch.object(
                 pm.MetadataIdentityStore,
                 'get',
                 return_value={'metron': '678'},
             ):
            payload = pm.build_series_json(7)

        self.assertIsNone(payload['metadata']['comicid'])

    def test_special_versions_map_to_conservative_booktypes(self):
        expected = {
            SpecialVersion.ONE_SHOT: 'One-Shot',
            SpecialVersion.TPB: 'TPB',
            SpecialVersion.HARD_COVER: 'Hard-Cover',
            SpecialVersion.OMNIBUS: 'Omnibus',
            SpecialVersion.NORMAL: 'Print',
            SpecialVersion.VOLUME_AS_ISSUE: 'Print',
        }
        for special_version, booktype in expected.items():
            with self.subTest(special_version=special_version):
                self.assertEqual(pm._booktype(special_version), booktype)


class portable_metadata_writeback(unittest.TestCase):
    @staticmethod
    def _fake_volume(folder):
        data = SimpleNamespace(folder=folder)
        volume = MagicMock()
        volume.vd = data
        return volume

    def test_create_is_exclusive_and_scanned_as_general_metadata(self):
        with tempfile.TemporaryDirectory() as folder:
            volume = self._fake_volume(folder)
            with patch.object(pm, 'Volume', return_value=volume), \
                 patch.object(pm, 'serialized_series_json', return_value='{"ok": true}\n'), \
                 patch.object(pm, 'scan_files') as scan:
                result = pm.write_series_json(7)

            path = os.path.join(folder, 'series.json')
            self.assertTrue(result['written'])
            self.assertEqual(result['reason'], 'created')
            with open(path, encoding='utf-8') as handle:
                self.assertEqual(json.load(handle), {'ok': True})
            scan.assert_called_once_with(7, filepath_filter=[path])

    def test_existing_series_json_is_preserved_by_default(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, 'series.json')
            with open(path, 'w', encoding='utf-8') as handle:
                handle.write('{"third_party": true}\n')
            volume = self._fake_volume(folder)

            with patch.object(pm, 'Volume', return_value=volume), \
                 patch.object(pm, 'serialized_series_json') as serialize, \
                 patch.object(pm, 'scan_files') as scan:
                result = pm.write_series_json(7)

            self.assertFalse(result['written'])
            self.assertEqual(result['reason'], 'existing_preserved')
            with open(path, encoding='utf-8') as handle:
                self.assertEqual(json.load(handle), {'third_party': True})
            serialize.assert_not_called()
            scan.assert_not_called()

    def test_explicit_overwrite_replaces_atomically_and_rescans(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, 'series.json')
            with open(path, 'w', encoding='utf-8') as handle:
                handle.write('{"old": true}\n')
            volume = self._fake_volume(folder)

            with patch.object(pm, 'Volume', return_value=volume), \
                 patch.object(pm, 'serialized_series_json', return_value='{"new": true}\n'), \
                 patch.object(pm, 'scan_files') as scan:
                result = pm.write_series_json(7, overwrite=True)

            self.assertTrue(result['written'])
            self.assertEqual(result['reason'], 'overwritten')
            with open(path, encoding='utf-8') as handle:
                self.assertEqual(json.load(handle), {'new': True})
            self.assertFalse(any(name.endswith('.tmp') for name in os.listdir(folder)))
            scan.assert_called_once_with(7, filepath_filter=[path])

    def test_missing_volume_folder_is_not_created(self):
        with tempfile.TemporaryDirectory() as root:
            folder = os.path.join(root, 'missing')
            volume = self._fake_volume(folder)
            with patch.object(pm, 'Volume', return_value=volume), \
                 patch.object(pm, 'scan_files') as scan:
                result = pm.write_series_json(7)

            self.assertFalse(result['written'])
            self.assertEqual(result['reason'], 'folder_missing')
            self.assertFalse(os.path.exists(folder))
            scan.assert_not_called()


if __name__ == '__main__':
    unittest.main()
