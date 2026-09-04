# -*- coding: utf-8 -*-

"""A library is organised by franchise, and two checks failed to notice.

Grouping by title said 250 series were duplicated -- Batman's four runs,
Daredevil's nine. Asking whether a folder's path mentions the volume's
title said 426 folders were misfiled -- `ElfQuest: New Blood` under
`/content/ElfQuest`, `Marvel Previews` under `/content/Marvel Universe`,
`Lockjaw and the Pet Avengers` under `/content/Pet Avengers`. Both were
describing a library working exactly as intended, and the second one came
with an offer to move 426 folders.

What survives is narrower and asks about words rather than paths:

  one folder, unrelated series   Volumes sharing a directory with no
                                 meaningful word between them. Thirteen
                                 ElfQuests share "elfquest"; One Piece and
                                 Hercules share nothing.

  named after another series     A volume whose folder's own last part has
                                 no word in common with its title.
                                 `ElfQuest: New Blood` in `ElfQuest` keeps
                                 its name; `Golden Kamuy` in
                                 `Art of Atari (2016)` does not.

Every case below is a real row from Silas's library on 2026-09-04. The
`must not be flagged` set is the one that matters: it is what the two
discarded checks got wrong.
"""

import os
import sys
import unittest

sys.path.insert(
    0,
    os.path.join(os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))), 'scripts')
)

from library_conflicts import (meaningful, shared_folders,  # noqa: E402
                               title_words, wrongly_named)


def _volume(volume_id, title, folder, downloaded=0, issues=1, year=2000):
    return {
        'id': volume_id, 'title': title, 'year': year, 'folder': folder,
        'issue_count': issues, 'issues_downloaded': downloaded
    }


# Real rows that must produce no finding at all.
ORGANISED = [
    ('ElfQuest: New Blood', '/content/ElfQuest'),
    ('ElfQuest', '/content/ElfQuest'),
    ('Hellboy in Hell', '/content/Hellboy'),
    ('Simpsons Comics', '/content/Simpsons'),
    ('Miles Morales: Spider-Man', '/content/Spider-Man'),
    ('Spider-Man', '/content/Spider-Man'),
    ('Marvel Previews', '/content/Marvel Universe'),
    ('Batman/Catwoman', '/content/Batman/Batman-Catwoman (2020)'),
    ('2000 AD', '/content/2000 AD (1977)'),
    ("Jim Henson's Labyrinth: Coronation",
     '/content/Jim Henson Presents/Labyrinth'),
    ('Lockjaw and the Pet Avengers', '/content/Pet Avengers'),
    ('The All New Atom', '/content/Atom'),
    ('Patsy Walker, A.K.A. Hellcat!', '/content/Hellcat'),
    ('Dial H', '/content/Dial H for Hero'),
    ('John Constantine: Hellblazer', '/content/Hellblazer'),
    ('Will Eisner’s The Spirit', '/content/Spirit, The'),
    ('A Righteous Thirst For Vengeance',
     '/content/Righteous Thirst For Vengeance, A (2021)'),
    ('Skull and Bones', '/content/Skull & Bones Savage Storm (2023)'),
    ("John Carpenter's Asylum", '/content/Asylum (2013)'),
    ('Catwoman Annual', '/content/Catwoman'),
    ('Catwoman', '/content/Catwoman'),
    ('Detective Comics', '/content/Batman/Detective Comics (2016)'),
    ('Detective Comics Annual', '/content/Batman/Detective Comics (2016)'),
    ('The Bunker', '/content/Bunker'),
    ('Bunker', '/content/Bunker'),
    ('Vote Loki', '/content/Loki'),
    ('Loki', '/content/Loki'),
    ('Batman', '/content/Batman'),
    ('All Star Batman and Robin, the Boy Wonder', '/content/Batman'),
]


class a_library_organised_by_franchise(unittest.TestCase):
    """The regression the two discarded checks were. Every row here was a
    finding under one or both of them.
    """

    def setUp(self):
        self.volumes = [
            _volume(index, title, folder, downloaded=index)
            for index, (title, folder) in enumerate(ORGANISED, start=1)
        ]

    def test_no_folder_is_reported_as_holding_unrelated_series(self):
        self.assertEqual(
            [[v['title'] for v in g] for g in shared_folders(self.volumes)],
            []
        )

    def test_no_volume_is_reported_as_wrongly_named(self):
        self.assertEqual(
            [v['title'] for v in wrongly_named(self.volumes)], []
        )


class folders_holding_unrelated_series(unittest.TestCase):
    def test_one_piece_and_hercules_share_a_wildcats_folder(self):
        volumes = [
            _volume(5456, 'One Piece',
                    '/content/WildC.A.T.S/WildCATS Covert Action Teams', 6),
            _volume(5454, 'Hercules',
                    '/content/WildC.A.T.S/WildCATS Covert Action Teams', 1)
        ]

        groups = shared_folders(volumes)
        self.assertEqual(len(groups), 1)
        # Most files first: whichever holds comics is the one to look at.
        self.assertEqual([v['id'] for v in groups[0]], [5456, 5454])

    def test_superman_shares_the_black_hammer_omnibus_folder(self):
        folder = '/content/Black Hammer/Black Hammer Omnibus (2022)'
        volumes = [
            _volume(3150, 'Black Hammer Omnibus', folder, 2),
            _volume(1691, 'Superman: The Man of Steel', folder, 116)
        ]

        self.assertEqual(len(shared_folders(volumes)), 1)

    def test_a_franchise_folder_of_many_volumes_is_not_reported(self):
        volumes = [
            _volume(n, title, '/content/ElfQuest', n) for n, title in
            enumerate((
                'ElfQuest', 'ElfQuest: Hidden Years', 'ElfQuest: Jink',
                'ElfQuest: New Blood', 'Elfquest: The Discovery',
                'ElfQuest: Wave Dancers'
            ), start=1)
        ]

        self.assertEqual(shared_folders(volumes), [])

    def test_a_volume_alone_in_its_folder_is_never_a_group(self):
        self.assertEqual(shared_folders([
            _volume(1, 'Batman', '/content/Batman (1940)')
        ]), [])


class a_folder_named_after_another_series(unittest.TestCase):
    def test_golden_kamuy_in_an_art_of_atari_folder(self):
        self.assertEqual(
            [v['id'] for v in wrongly_named([
                _volume(1617, 'Golden Kamuy',
                        '/content/Art of, The/Art of Atari (2016)', 1)
            ])],
            [1617]
        )

    def test_the_deepest_part_is_what_is_compared(self):
        """`Batman/Catwoman` sits under `/content/Batman`, which does not
        name it -- but its own directory does, and that is the one asked."""
        self.assertEqual(wrongly_named([
            _volume(757, 'Batman/Catwoman',
                    '/content/Batman/Batman-Catwoman (2020)', 11)
        ]), [])

    def test_a_volume_with_comics_in_the_wrong_place_leads(self):
        found = wrongly_named([
            _volume(107, 'The Fly',
                    '/content/Art of Atari Poster Collection (2018)', 0),
            _volume(1691, 'Superman: The Man of Steel',
                    '/content/Black Hammer/Black Hammer Omnibus (2022)', 116)
        ])

        self.assertEqual([v['id'] for v in found], [1691, 107])

    def test_a_volume_with_no_folder_is_not_judged(self):
        self.assertEqual(wrongly_named([_volume(1, 'Batman', '')]), [])


class words_a_title_is_made_of(unittest.TestCase):
    def test_punctuation_separates_rather_than_belongs(self):
        self.assertEqual(
            title_words('Batman/Catwoman'), {'batman', 'catwoman'}
        )
        self.assertEqual(
            title_words('Hack/Slash: Body Bags'),
            {'hack', 'slash', 'body', 'bags'}
        )

    def test_words_too_common_to_mean_anything_are_dropped(self):
        """"George R.R. Martin's A Clash of Kings" and "Game of Thrones"
        share "of", and that is not evidence they are the same series."""
        self.assertNotIn('of', meaningful('A Clash of Kings'))
        self.assertNotIn('annual', meaningful('Batman Annual'))
        self.assertIn('batman', meaningful('Batman Annual'))

    def test_a_title_of_nothing_but_noise_matches_nothing(self):
        self.assertEqual(meaningful('The Complete Collection'), set())


class it_can_only_report(unittest.TestCase):
    def test_it_never_writes(self):
        import inspect

        import library_conflicts

        source = inspect.getsource(library_conflicts)
        for verb in ("'DELETE'", "'PUT'", 'delete_folder', 'fix_folders'):
            self.assertNotIn(verb, source, msg=f'{verb} must not be issued')

    def test_the_only_call_it_makes_is_a_read(self):
        import inspect

        import library_conflicts

        self.assertIn(
            "urlopen(Request(url), timeout=120)",
            inspect.getsource(library_conflicts.call)
        )


if __name__ == '__main__':
    unittest.main()
