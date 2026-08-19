import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from zipfile import ZIP_DEFLATED, ZipFile

from backend.features.library_import_metadata import (
    MAX_COMICINFO_BYTES,
    load_local_series_metadata,
    select_local_series_metadata,
)


class comicinfo_library_import(unittest.TestCase):
    @staticmethod
    def _file_data(series='Batman', year=2020):
        return {
            'series': series,
            'year': year,
            'volume_number': 1,
            'special_version': None,
            'issue_number': 1.0,
            'annual': False,
        }

    @staticmethod
    def _comicinfo(
        series='Batman',
        web='https://comicvine.gamespot.com/batman/4050-12345/',
        year='2020',
        volume='3',
        count='12',
    ):
        return f'''<?xml version="1.0" encoding="utf-8"?>
<ComicInfo>
  <Series>{series}</Series>
  <Number>1</Number>
  <Count>{count}</Count>
  <Volume>{volume}</Volume>
  <Year>{year}</Year>
  <Web>{web}</Web>
</ComicInfo>'''.encode('utf-8')

    def test_sidecar_comicinfo_uses_standard_web_volume_url_as_exact_identity(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, 'ComicInfo.xml')
            with open(path, 'wb') as handle:
                handle.write(self._comicinfo())

            metadata = load_local_series_metadata(folder)

        self.assertIsNotNone(metadata)
        self.assertEqual(metadata['comicvine_id'], 12345)
        self.assertEqual(metadata['name'], 'Batman')
        self.assertEqual(metadata['year'], 2020)
        self.assertEqual(metadata['volume_number'], 3)
        self.assertEqual(metadata['issue_count'], 12)
        self.assertEqual(metadata['source'], 'ComicInfo.xml')

    def test_non_comicvine_and_issue_web_urls_are_not_synthesized_into_volume_ids(self):
        for web in (
            'https://example.test/batman/12345',
            'https://comicvine.gamespot.com/batman-1/4000-99999/',
            '',
        ):
            with self.subTest(web=web), tempfile.TemporaryDirectory() as folder:
                with open(os.path.join(folder, 'ComicInfo.xml'), 'wb') as handle:
                    handle.write(self._comicinfo(web=web))
                self.assertIsNone(load_local_series_metadata(folder))

    def test_embedded_cbz_comicinfo_can_skip_external_identity_search(self):
        with tempfile.TemporaryDirectory() as folder:
            comic = os.path.join(folder, 'Batman 001.cbz')
            with ZipFile(comic, 'w', ZIP_DEFLATED) as archive:
                archive.writestr('ComicInfo.xml', self._comicinfo())
                archive.writestr('001.jpg', b'page')
            group = {comic: self._file_data()}

            metadata = select_local_series_metadata(folder, group)

        self.assertIsNotNone(metadata)
        self.assertEqual(metadata['comicvine_id'], 12345)
        self.assertIn('!ComicInfo.xml', metadata['path'])

    def test_embedded_cbr_and_rar_comicinfo_use_bundled_rar_reader(self):
        for extension in ('.cbr', '.rar'):
            with self.subTest(extension=extension), tempfile.TemporaryDirectory() as folder:
                comic = os.path.join(folder, 'Batman 001' + extension)
                open(comic, 'wb').close()
                group = {comic: self._file_data()}
                calls = []

                def fake_rar(args):
                    calls.append(args)
                    if args[0] == 'lb':
                        return SimpleNamespace(
                            returncode=0,
                            stdout='metadata\\ComicInfo.xml\n001.jpg\n',
                        )
                    self.assertEqual(args[0], 'e')
                    self.assertEqual(args[4], 'metadata\\ComicInfo.xml')
                    with open(
                        os.path.join(args[-1], 'ComicInfo.xml'),
                        'wb',
                    ) as handle:
                        handle.write(self._comicinfo())
                    return SimpleNamespace(returncode=0, stdout='')

                with patch(
                    'backend.features.library_import_metadata.run_rar',
                    side_effect=fake_rar,
                ):
                    metadata = select_local_series_metadata(folder, group)

                self.assertIsNotNone(metadata)
                self.assertEqual(metadata['comicvine_id'], 12345)
                self.assertEqual(calls[0], ['lb', comic])
                self.assertEqual(calls[1][0:4], ['e', '-inul', '-o+', comic])
                self.assertIn('!metadata\\ComicInfo.xml', metadata['path'])

    def test_rar_comicinfo_extraction_failure_is_ignored(self):
        with tempfile.TemporaryDirectory() as folder:
            comic = os.path.join(folder, 'Batman 001.cbr')
            open(comic, 'wb').close()
            group = {comic: self._file_data()}

            def fake_rar(args):
                if args[0] == 'lb':
                    return SimpleNamespace(
                        returncode=0,
                        stdout='ComicInfo.xml\n',
                    )
                return SimpleNamespace(returncode=3, stdout='')

            with patch(
                'backend.features.library_import_metadata.run_rar',
                side_effect=fake_rar,
            ):
                self.assertIsNone(load_local_series_metadata(folder, group))

    def test_oversized_rar_comicinfo_is_not_loaded(self):
        with tempfile.TemporaryDirectory() as folder:
            comic = os.path.join(folder, 'Batman 001.rar')
            open(comic, 'wb').close()
            group = {comic: self._file_data()}

            def fake_rar(args):
                if args[0] == 'lb':
                    return SimpleNamespace(
                        returncode=0,
                        stdout='ComicInfo.xml\n',
                    )
                with open(
                    os.path.join(args[-1], 'ComicInfo.xml'),
                    'wb',
                ) as handle:
                    handle.write(b'x' * (MAX_COMICINFO_BYTES + 1))
                return SimpleNamespace(returncode=0, stdout='')

            with patch(
                'backend.features.library_import_metadata.run_rar',
                side_effect=fake_rar,
            ):
                self.assertIsNone(load_local_series_metadata(folder, group))

    def test_embedded_comicinfo_title_still_must_match_the_filename_group(self):
        with tempfile.TemporaryDirectory() as folder:
            comic = os.path.join(folder, 'Detective Comics 001.cbz')
            with ZipFile(comic, 'w', ZIP_DEFLATED) as archive:
                archive.writestr('ComicInfo.xml', self._comicinfo(series='Batman'))
            group = {comic: self._file_data(series='Detective Comics')}

            self.assertIsNone(select_local_series_metadata(folder, group))

    def test_conflicting_embedded_comicinfo_ids_are_not_auto_trusted(self):
        with tempfile.TemporaryDirectory() as folder:
            group = {}
            for number, comicvine_id in ((1, 111), (2, 222)):
                comic = os.path.join(folder, f'Batman {number:03d}.cbz')
                with ZipFile(comic, 'w', ZIP_DEFLATED) as archive:
                    archive.writestr(
                        'metadata/ComicInfo.xml',
                        self._comicinfo(
                            web=(
                                'https://comicvine.gamespot.com/batman/'
                                f'4050-{comicvine_id}/'
                            )
                        ),
                    )
                group[comic] = {
                    **self._file_data(),
                    'issue_number': float(number),
                }

            self.assertIsNone(load_local_series_metadata(folder, group))

    def test_legacy_cvinfo_xml_is_accepted_without_treating_arbitrary_xml_as_comicinfo(self):
        with tempfile.TemporaryDirectory() as root:
            folder = os.path.join(root, 'Batman (2020)')
            os.makedirs(folder)
            with open(os.path.join(folder, 'cvinfo.xml'), 'w', encoding='utf-8') as handle:
                handle.write(
                    '<cvinfo><url>https://comicvine.gamespot.com/batman/'
                    '4050-12345/</url></cvinfo>'
                )
            metadata = load_local_series_metadata(folder)

        self.assertIsNotNone(metadata)
        self.assertEqual(metadata['comicvine_id'], 12345)
        self.assertEqual(metadata['source'], 'cvinfo.xml')


if __name__ == '__main__':
    unittest.main()
