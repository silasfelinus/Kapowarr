# -*- coding: utf-8 -*-

"""One row with no file behind it stopped a volume being moved at all.

Silas's library on 2026-09-04 had One Piece (1997) sharing
`/content/WildC.A.T.S/WildCATS Covert Action Teams` with Hercules (1998),
and six of One Piece's "issues downloaded" were Detective Comics files
that had since been deleted from disk. Asking Kapowarr to put the volume
back where it belonged raised

    FileNotFoundError: [Errno 2] No such file or directory:
    '/content/WildC.A.T.S/WildCATS Covert Action Teams/
     Detective.Comics.074.[DC].[Apr.1943].[c2c-8.fiche]/...cbz'

out of `rename_file`, and the move aborted -- with whichever files had
already been renamed sitting in the new folder while the database still
named the old one, and no record of the split.

A row with no file behind it has nothing to move. It is not a reason to
refuse the move, and it must not be a reason to leave the volume in two
places at once.
"""

import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from backend.base.logging import LOGGER
from backend.implementations.volumes import Volume


class a_row_with_no_file_behind_it(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = self.tmp.name
        self.old = os.path.join(self.root, 'Wrong Folder')
        self.new = os.path.join(self.root, 'One Piece (1997)')
        os.makedirs(self.old)

        self.real = os.path.join(self.old, 'One Piece 001.cbz')
        with open(self.real, 'wb') as f:
            f.write(b'x')
        self.gone = os.path.join(self.old, 'Detective Comics 074.cbz')

    def _move(self):
        """Run `change_volume_folder` against the temp library."""
        volume = Volume.__new__(Volume)
        volume.id = 5456
        renamed = {}
        updated = {}

        def rename(before, after):
            os.makedirs(os.path.dirname(after), exist_ok=True)
            os.replace(before, after)
            renamed[before] = after

        with patch.object(
            Volume, 'get_data',
            return_value=SimpleNamespace(
                title='One Piece', year=1997, volume_number=1,
                special_version=None, root_folder=1, folder=self.old
            )
        ), patch.object(
            Volume, 'get_all_files',
            return_value=[
                {'filepath': self.real}, {'filepath': self.gone}
            ]
        ), patch.object(
            Volume, 'update', side_effect=lambda data: updated.update(data)
        ), patch(
            'backend.implementations.volumes.RootFolders',
            return_value={1: self.root}
        ), patch(
            'backend.implementations.naming.generate_volume_folder_path',
            return_value=self.new
        ), patch(
            'backend.implementations.volumes.rename_file', side_effect=rename
        ), patch(
            'backend.implementations.volumes.FilesDB'
        ) as files_db, patch(
            'backend.implementations.volumes.delete_empty_child_folders'
        ), patch(
            'backend.implementations.volumes.Settings',
            return_value=SimpleNamespace(
                sv=SimpleNamespace(create_empty_volume_folders=False)
            )
        ), patch(
            'backend.implementations.volumes.delete_empty_parent_folders'
        ), patch(
            'backend.implementations.volumes.folder_is_inside_folder',
            return_value=False
        ), patch.object(
            Volume, '_Volume__volume_folder_used_by_other_volume',
            return_value=True
        ), patch(
            'backend.implementations.volumes.mass_process_files'
        ):
            volume.change_volume_folder(None)

        return renamed, updated, files_db

    def test_the_move_finishes(self):
        renamed, updated, _ = self._move()

        self.assertEqual(
            renamed, {self.real: os.path.join(self.new, 'One Piece 001.cbz')}
        )
        self.assertEqual(updated.get('folder'), self.new)

    def test_the_database_is_not_left_naming_the_old_folder(self):
        """The failure mode this replaces: files moved, folder not."""
        _, updated, files_db = self._move()

        self.assertEqual(updated.get('folder'), self.new)
        files_db.update_filepaths.assert_called_once()

    def test_the_missing_row_is_not_claimed_to_have_moved(self):
        _, _, files_db = self._move()

        moved = files_db.update_filepaths.call_args[0][0]
        self.assertNotIn(self.gone, moved)

    def test_it_says_what_it_left_behind(self):
        with self.assertLogs(LOGGER, level='WARNING') as captured:
            self._move()

        joined = '\n'.join(captured.output)
        self.assertIn('not on disk', joined)
        self.assertIn('Detective Comics 074.cbz', joined)
        # Named so it can be acted on, and with the way out.
        self.assertIn('Rescan', joined)


if __name__ == '__main__':
    unittest.main()
