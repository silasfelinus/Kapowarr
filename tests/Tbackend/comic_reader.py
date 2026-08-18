import os
import tempfile
import unittest
from subprocess import CompletedProcess
from unittest.mock import patch
from zipfile import ZipFile

from backend.features.comic_reader import (
    build_pages_for_files,
    find_pdf_file,
    is_reader_supported_file,
    list_archive_pages,
    list_rar_pages,
    natural_sort_key,
    read_rar_member,
)


class ComicReaderTest(unittest.TestCase):
    def test_natural_sort_keeps_numeric_page_order(self):
        pages = ['page10.jpg', 'page2.jpg', 'page1.jpg']
        self.assertEqual(
            sorted(pages, key=natural_sort_key),
            ['page1.jpg', 'page2.jpg', 'page10.jpg']
        )

    def test_reader_support_includes_common_comic_formats(self):
        self.assertTrue(is_reader_supported_file('/library/Issue 1.cbz'))
        self.assertTrue(is_reader_supported_file('/library/Issue 1.ZIP'))
        self.assertTrue(is_reader_supported_file('/library/Issue 1.cbr'))
        self.assertTrue(is_reader_supported_file('/library/Issue 1.RAR'))
        self.assertTrue(is_reader_supported_file('/library/001.webp'))
        self.assertTrue(is_reader_supported_file('/library/Issue 1.pdf'))
        self.assertTrue(is_reader_supported_file('/library/Issue 1.PDF'))
        self.assertFalse(is_reader_supported_file('/library/Issue 1.epub'))

    def test_archive_pages_ignore_metadata_and_sort_images(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive_path = os.path.join(tmp, 'issue.cbz')
            with ZipFile(archive_path, 'w') as archive:
                archive.writestr('ComicInfo.xml', '<ComicInfo/>')
                archive.writestr('pages/10.jpg', b'ten')
                archive.writestr('pages/2.jpg', b'two')
                archive.writestr('pages/1.png', b'one')

            self.assertEqual(
                list_archive_pages(archive_path),
                ['pages/1.png', 'pages/2.jpg', 'pages/10.jpg']
            )

    @patch('backend.features.comic_reader.run_rar')
    def test_rar_pages_ignore_metadata_and_sort_images(self, run_rar):
        run_rar.return_value = CompletedProcess(
            args=[],
            returncode=0,
            stdout=(
                'ComicInfo.xml\n'
                'pages/10.jpg\n'
                'pages/2.jpg\n'
                'pages/1.png\n'
            ),
            stderr=''
        )

        self.assertEqual(
            list_rar_pages('/library/issue.cbr'),
            ['pages/1.png', 'pages/2.jpg', 'pages/10.jpg']
        )
        run_rar.assert_called_once_with([
            'lb', '-inul', '/library/issue.cbr'
        ])

    @patch('backend.features.comic_reader.run_rar')
    def test_bad_rar_produces_no_pages(self, run_rar):
        run_rar.return_value = CompletedProcess(
            args=[],
            returncode=2,
            stdout='',
            stderr='broken archive'
        )
        self.assertEqual(list_rar_pages('/library/broken.cbr'), [])

    @patch('backend.features.comic_reader.run')
    @patch('backend.features.comic_reader.folder_path')
    def test_rar_member_is_read_as_binary_without_extraction(
        self,
        folder_path,
        run_process
    ):
        folder_path.return_value = '/app/backend/lib/rar'
        run_process.return_value = CompletedProcess(
            args=[],
            returncode=0,
            stdout=b'image-bytes',
            stderr=b''
        )

        result = read_rar_member('/library/issue.cbr', 'pages/001.jpg')

        self.assertEqual(result, b'image-bytes')
        self.assertEqual(run_process.call_args.kwargs, {'capture_output': True})
        command = run_process.call_args.args[0]
        self.assertEqual(command[0], '/app/backend/lib/rar')
        self.assertEqual(command[1:], [
            'p', '-inul', '/library/issue.cbr', 'pages/001.jpg'
        ])

    def test_build_pages_combines_loose_images_and_cbz_members(self):
        with tempfile.TemporaryDirectory() as tmp:
            loose_path = os.path.join(tmp, '000.jpg')
            with open(loose_path, 'wb') as loose:
                loose.write(b'loose')

            archive_path = os.path.join(tmp, 'issue.cbz')
            with ZipFile(archive_path, 'w') as archive:
                archive.writestr('001.jpg', b'one')
                archive.writestr('002.jpg', b'two')

            pages = build_pages_for_files([
                {'id': 2, 'filepath': archive_path, 'size': 10},
                {'id': 1, 'filepath': loose_path, 'size': 5}
            ])

            self.assertEqual(len(pages), 3)
            self.assertEqual(pages[0]['file_id'], 1)
            self.assertIsNone(pages[0]['member'])
            self.assertIsNone(pages[0]['archive_type'])
            self.assertEqual(
                [page['member'] for page in pages[1:]],
                ['001.jpg', '002.jpg']
            )
            self.assertEqual(
                [page['archive_type'] for page in pages[1:]],
                ['zip', 'zip']
            )

    @patch('backend.features.comic_reader.list_rar_pages')
    def test_build_pages_adds_cbr_members_as_rar_pages(self, list_rar_pages):
        list_rar_pages.return_value = ['001.jpg', '002.png']
        pages = build_pages_for_files([
            {'id': 9, 'filepath': '/library/issue.cbr', 'size': 10}
        ])

        self.assertEqual(len(pages), 2)
        self.assertEqual(
            [page['archive_type'] for page in pages],
            ['rar', 'rar']
        )
        self.assertEqual(
            [page['member'] for page in pages],
            ['001.jpg', '002.png']
        )

    def test_pdf_is_document_not_image_page(self):
        pages = build_pages_for_files([
            {'id': 1, 'filepath': '/library/Issue 1.pdf', 'size': 50}
        ])
        self.assertEqual(pages, [])

    def test_find_pdf_file_uses_natural_order_and_ignores_other_formats(self):
        pdf = find_pdf_file([
            {'id': 4, 'filepath': '/library/Issue 10.pdf', 'size': 10},
            {'id': 3, 'filepath': '/library/Issue 2.PDF', 'size': 10},
            {'id': 2, 'filepath': '/library/Issue 1.cbr', 'size': 10}
        ])
        self.assertIsNotNone(pdf)
        self.assertEqual(pdf['id'], 3)

    def test_find_pdf_file_returns_none_without_pdf(self):
        self.assertIsNone(find_pdf_file([
            {'id': 1, 'filepath': '/library/Issue 1.cbz', 'size': 10}
        ]))

    def test_bad_zip_produces_no_pages(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive_path = os.path.join(tmp, 'broken.cbz')
            with open(archive_path, 'wb') as broken:
                broken.write(b'not a zip')
            self.assertEqual(list_archive_pages(archive_path), [])


if __name__ == '__main__':
    unittest.main()
