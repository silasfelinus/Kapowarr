import os
import tempfile
import unittest
from zipfile import ZipFile

from backend.features.comic_reader import (
    build_pages_for_files,
    find_pdf_file,
    is_reader_supported_file,
    list_archive_pages,
    natural_sort_key,
)


class ComicReaderTest(unittest.TestCase):
    def test_natural_sort_keeps_numeric_page_order(self):
        pages = ['page10.jpg', 'page2.jpg', 'page1.jpg']
        self.assertEqual(
            sorted(pages, key=natural_sort_key),
            ['page1.jpg', 'page2.jpg', 'page10.jpg']
        )

    def test_reader_support_is_deliberately_narrow(self):
        self.assertTrue(is_reader_supported_file('/library/Issue 1.cbz'))
        self.assertTrue(is_reader_supported_file('/library/Issue 1.ZIP'))
        self.assertTrue(is_reader_supported_file('/library/001.webp'))
        self.assertTrue(is_reader_supported_file('/library/Issue 1.pdf'))
        self.assertTrue(is_reader_supported_file('/library/Issue 1.PDF'))
        self.assertFalse(is_reader_supported_file('/library/Issue 1.cbr'))

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
            self.assertEqual(
                [page['member'] for page in pages[1:]],
                ['001.jpg', '002.jpg']
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
