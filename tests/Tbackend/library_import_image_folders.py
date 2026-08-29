import unittest
from unittest.mock import patch

from backend.features.library_import import _collect_unimported_files


class image_folder_checkpointing(unittest.TestCase):
    @staticmethod
    def _parsed(series: str):
        return {
            'series': series,
            'year': 2020,
            'volume_number': 1,
            'special_version': None,
            'issue_number': 1.0,
            'annual': False
        }

    def test_image_based_comic_is_checkpointed_as_its_own_folder(self):
        # Three pages, not two: a folder is only read as an unpacked comic
        # once it holds MIN_UNPACKED_COMIC_IMAGES of them. What this test is
        # about is the checkpoint boundary, not the count.
        image_one = '/library/Animosity/001.jpg'
        image_two = '/library/Animosity/002.jpg'
        image_three = '/library/Animosity/003.jpg'
        archive = '/library/Batman/Batman 001.cbz'

        def parsed(path, prefer_folder_year=False):
            self.assertTrue(prefer_folder_year)
            return self._parsed(
                'Animosity' if path.endswith('.jpg') else 'Batman'
            )

        with patch(
            'backend.features.library_import.RootFolders.get_folder_list',
            return_value=['/library']
        ), patch(
            'backend.features.library_import.list_files',
            return_value=[image_one, image_two, image_three, archive]
        ), patch(
            'backend.features.library_import.FilesDB.fetch',
            return_value=[]
        ), patch(
            'backend.features.library_import.extract_filename_data',
            side_effect=parsed
        ):
            files, file_to_folder = _collect_unimported_files()

        # An unpacked/image comic is represented by its directory, once, but
        # that directory is also the durable work unit. Hoisting it to the
        # configured root turns every top-level unpacked comic into one giant
        # synthetic root-folder checkpoint.
        self.assertIn('/library/Animosity', files)
        self.assertNotIn(image_one, files)
        self.assertNotIn(image_two, files)
        self.assertNotIn(image_three, files)
        self.assertEqual(
            file_to_folder['/library/Animosity'],
            '/library/Animosity'
        )

        # Normal archive/container imports keep their existing folder behavior.
        self.assertEqual(file_to_folder[archive], '/library/Batman')
        self.assertNotIn('/library', file_to_folder.values())

    def test_nested_image_comic_keeps_its_leaf_folder_boundary(self):
        pages = [
            '/library/ElfQuest/Hidden Years/%03d.jpg' % n
            for n in range(1, 4)
        ]

        with patch(
            'backend.features.library_import.RootFolders.get_folder_list',
            return_value=['/library']
        ), patch(
            'backend.features.library_import.list_files',
            return_value=pages
        ), patch(
            'backend.features.library_import.FilesDB.fetch',
            return_value=[]
        ), patch(
            'backend.features.library_import.extract_filename_data',
            return_value=self._parsed('ElfQuest: Hidden Years')
        ):
            files, file_to_folder = _collect_unimported_files()

        self.assertIn('/library/ElfQuest/Hidden Years', files)
        self.assertEqual(
            file_to_folder['/library/ElfQuest/Hidden Years'],
            '/library/ElfQuest/Hidden Years'
        )


if __name__ == '__main__':
    unittest.main()
