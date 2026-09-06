# -*- coding: utf-8 -*-

"""A volume claimed every comic beneath it, including other volumes'.

`scan_files` calls `list_files(folder=volume_data.folder)`, which walks the
whole tree, and nothing told it that a subdirectory might be a different
volume's folder. A library organised by franchise nests them:
`/content/Catwoman` holds `Catwoman (2011)`, `Catwoman Lonely City (2021)`,
`Cavewoman Deep Water (2017)` and a dozen more, each its own volume. A
volume pointed at the franchise directory itself contained all of them.

Claiming them was not only wrong on paper. `change_volume_folder` moves the
files a volume claims, so moving such a volume carried its neighbours away
with it. On 2026-09-06, in Silas's library:

    Volume 32 (Catwoman) has 11 file(s) that name a different series;
    they move with it. First: /content/Catwoman/Cavewoman Carrie's Oasis
    Diary (2017)/Cavewoman - Carrie's Oasis Diary 001 (2017)...

and Catwoman (1993) dragged an entire `Catwoman Annual (1994)` directory,
twice. The same four volumes each claimed the same 90 files of the 2018
run, because all four were pointed at the franchise folder.

A file inside another volume's folder is that volume's, whatever the tree
above it says.
"""

import unittest
from unittest.mock import patch

from backend.implementations import file_matching
from backend.implementations.file_matching import (nested_volume_folders,
                                                   outside_other_volumes)

# The real tree, from `/content/Catwoman` on 2026-09-06.
LIBRARY = {
    28: '/content/Catwoman/Catwoman (1993)',
    32: '/content/Catwoman',
    44: '/content/Catwoman/Catwoman Lonely City (2021)',
    99: '/content/Catwoman/Cavewoman Deep Water (2017)',
    100: '/content/Batman',
    101: None
}

CONTENTS = [
    '/content/Catwoman/Catwoman.v01-Who.is.Selina.Kyle.cbz',
    '/content/Catwoman/folder.jpg',
    '/content/Catwoman/Catwoman (1993)/Catwoman 084.cbr',
    '/content/Catwoman/Catwoman Lonely City (2021)/Lonely City 01 (2021).cbz',
    '/content/Catwoman/Cavewoman Deep Water (2017)/Deep Water 001 (2017).cbz',
    '/content/Catwoman/Catwoman Annual (1994)/series.json'
]


class _Rows:
    """Just enough of the db cursor: `SELECT folder FROM volumes`."""

    def __init__(self, library):
        self.library = library

    def execute(self, query, args):
        assert 'FROM volumes' in query, query
        (asking_for,) = args
        return [
            (folder,)
            for volume_id, folder in self.library.items()
            if volume_id != asking_for
        ]


class a_volume_does_not_reach_into_another(unittest.TestCase):
    def setUp(self):
        patcher = patch.object(
            file_matching, 'get_db', lambda: _Rows(LIBRARY)
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_the_franchise_folder_keeps_only_what_is_loose_in_it(self):
        """Two files sit directly in `/content/Catwoman`, and one sits in a
        directory no volume claims -- those are volume 32's. The three in
        other volumes' folders are not."""
        self.assertEqual(
            outside_other_volumes(32, '/content/Catwoman', CONTENTS),
            [
                '/content/Catwoman/Catwoman.v01-Who.is.Selina.Kyle.cbz',
                '/content/Catwoman/folder.jpg',
                '/content/Catwoman/Catwoman Annual (1994)/series.json'
            ]
        )

    def test_a_volume_in_its_own_folder_is_unaffected(self):
        """Nothing nests inside `Catwoman (1993)`, so its scan is exactly
        what it always was. This must not cost anything for the ordinary
        case, which is nearly every volume."""
        contents = ['/content/Catwoman/Catwoman (1993)/Catwoman 084.cbr']

        self.assertEqual(
            outside_other_volumes(28, '/content/Catwoman/Catwoman (1993)',
                                  contents),
            contents
        )

    def test_a_volume_never_excludes_itself(self):
        self.assertEqual(
            nested_volume_folders(32, '/content/Catwoman'),
            [
                '/content/Catwoman/Catwoman (1993)/',
                '/content/Catwoman/Catwoman Lonely City (2021)/',
                '/content/Catwoman/Cavewoman Deep Water (2017)/'
            ]
        )

    def test_a_volume_sharing_the_exact_folder_is_not_nested(self):
        """Two volumes with one folder is a different problem, and one
        `scripts/library_conflicts.py` reports. Excluding the shared
        contents would leave both of them holding nothing, which fixes
        neither and loses the files of whichever was right."""
        shared = {1: '/content/Bunker', 2: '/content/Bunker'}
        with patch.object(file_matching, 'get_db', lambda: _Rows(shared)):
            self.assertEqual(nested_volume_folders(1, '/content/Bunker'), [])
            self.assertEqual(
                outside_other_volumes(1, '/content/Bunker',
                                      ['/content/Bunker/The Bunker 01.cbz']),
                ['/content/Bunker/The Bunker 01.cbz']
            )

    def test_a_sibling_that_merely_starts_the_same_is_not_inside(self):
        """`/content/Catwoman (1993)` is not under `/content/Catwoman`, and
        a prefix test without the separator would say it was. Silas had
        exactly that path for an hour on 2026-09-06."""
        library = {1: '/content/Catwoman', 2: '/content/Catwoman (1993)'}
        with patch.object(file_matching, 'get_db', lambda: _Rows(library)):
            self.assertEqual(nested_volume_folders(1, '/content/Catwoman'), [])

    def test_a_trailing_separator_does_not_make_a_folder_different(self):
        library = {1: '/content/Catwoman/', 2: '/content/Catwoman'}
        with patch.object(file_matching, 'get_db', lambda: _Rows(library)):
            self.assertEqual(nested_volume_folders(2, '/content/Catwoman'), [])
