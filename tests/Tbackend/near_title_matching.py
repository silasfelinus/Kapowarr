# -*- coding: utf-8 -*-

"""Strict title equality is the first filter, and it was the last word.

`_rank_volume_results_for_file` admitted a candidate only when the parsed
series equalled the provider's title exactly after cleaning. That is right
when the parsed series is the series, and a real library is full of folders
where it is not: ComicVine files a subtitle the folder does not have, the
parser keeps a trailing issue number, a scene release carries a phase the
database omits, one side says "Monsters" and the other "Monster". In each
case the correct volume was in the response and was discarded before it
could be scored, so the folder was held as `no-candidate` -- "no database
has this" -- on every pass, forever.

The relaxation is a second tier, consulted only when the strict pass
admits nobody, so no folder that matches today can be affected by it.
"""

import unittest

from backend.base.definitions import SpecialVersion
from backend.implementations.matching import (
    _rank_volume_results_for_file,
    match_title,
    match_title_nearly,
)


def _candidate(title, comicvine_id=1, year=2023, issue_count=6,
               volume_number=1):
    return {
        'comicvine_id': comicvine_id,
        'provider_id': 'comicvine',
        'external_id': comicvine_id,
        'title': title,
        'year': year,
        'volume_number': volume_number,
        'cover_link': '',
        'cover': None,
        'description': '',
        'site_url': '',
        'aliases': [],
        'publisher': 'Example',
        'issue_count': issue_count,
        'translated': False,
        'already_added': None,
        'issues': None
    }


def _group(series, year=2023, issue_number=1.0):
    return {
        f'/content/{series}/{series} 01.cbz': {
            'series': series,
            'year': year,
            'volume_number': 1,
            'issue_number': issue_number,
            'special_version': SpecialVersion.NORMAL,
            'annual': False
        }
    }


class titles_close_enough_to_be_worth_scoring(unittest.TestCase):
    RECOVERED = [
        # The provider files a subtitle the folder does not have.
        ('Burn the Orphanage', 'Burn the Orphanage: Reign of Terror'),
        ('Little Black', 'Little Black Book'),
        ('Tear Us', 'Tear Us Apart'),
        ('Dr. Atomic Pipe & Dope', 'Dr. Atomic: Pipe & Dope Book'),
        # The parser kept a trailing issue number in the series.
        ('Detective Comics 074', 'Detective Comics'),
        ('Hell Her Way 001', 'Hell Her Way'),
        # A scene release carries a phase the database omits.
        (
            'Star Wars The High Republic Adventures Phase III',
            'Star Wars: The High Republic Adventures'
        ),
        # An edition in parentheses the folder never carries.
        ('Blackest Night Saga', 'Blackest Night Saga (DC Essential Edition)'),
        ('Thundercats Ho! Special 2025', 'Thundercats Ho!(liday) Special 2025'),
        # One side pluralises and the other does not.
        (
            'Stuff of Nightmares The Monsters Makers',
            'Stuff of Nightmares: The Monster Makers'
        ),
        # A one- or two-character ornament.
        ('ODY', 'ODY-C'),
        # Spacing around a number is not a difference.
        ('Gen13 European Vacation Collection', 'Gen 13: European Vacation'),
    ]

    # A different comic that happens to start, end or read the same way.
    # Every one of these is a real pair from the live review queue.
    REJECTED = [
        ('Sex Cells', 'Cells'),
        ('Sleep Deprivation Ninja', 'Sleep'),
        ("Junji Ito's Frankenstein", 'Frankenstein'),
        ('Danger Jane', 'Jane'),
        ('Kinky Magic', 'Magic'),
        ('The Lust of Us', 'Lust'),
        ('Sinner University', 'Sinner'),
        ('Crimson After Hours', 'After Hours'),
        ('Naughty Science Fiction Worlds', 'Science Fiction'),
        ('Nymphonomicon Digital Sketchbook', 'Sketchbook'),
        ('Sexy Sundays', 'Sexy'),
        ('Amnesiac', 'Amnesiac Ex, Unforgettable Vows'),
        ('Batman', 'Superman'),
    ]

    def test_near_titles_are_admitted(self):
        for series, title in self.RECOVERED:
            with self.subTest(series=series, title=title):
                self.assertTrue(match_title_nearly(series, title))

    def test_merely_overlapping_titles_are_not(self):
        for series, title in self.REJECTED:
            with self.subTest(series=series, title=title):
                self.assertFalse(match_title_nearly(series, title))

    def test_strict_matching_is_unchanged(self):
        # The relaxation must not leak into the exact comparison every
        # other caller relies on.
        self.assertTrue(match_title('Gen13', 'Gen 13'))
        for series, title in self.RECOVERED:
            with self.subTest(series=series, title=title):
                if series == 'Gen13 European Vacation Collection':
                    continue
                self.assertFalse(match_title(series, title))


class the_near_tier_only_runs_when_nothing_matched(unittest.TestCase):
    def test_an_exact_match_hides_every_near_one(self):
        ranked = _rank_volume_results_for_file(
            _group('Future State'),
            [
                _candidate('Future State: Gotham', comicvine_id=1),
                _candidate('Future State', comicvine_id=2),
                _candidate('Future State: Superman', comicvine_id=3)
            ],
            only_english=True
        )
        self.assertEqual(
            [result['comicvine_id'] for result, _ in ranked],
            [2]
        )

    def test_near_matches_are_ranked_when_nothing_is_exact(self):
        ranked = _rank_volume_results_for_file(
            _group('Tear Us'),
            [
                _candidate('Tear Us Apart', comicvine_id=1),
                _candidate('Something Else', comicvine_id=2)
            ],
            only_english=True
        )
        self.assertEqual(
            [result['comicvine_id'] for result, _ in ranked],
            [1]
        )

    def test_a_genuine_miss_still_has_no_candidate(self):
        ranked = _rank_volume_results_for_file(
            _group('Sex Cells'),
            [_candidate('Cells', comicvine_id=1)],
            only_english=True
        )
        self.assertEqual(ranked, [])

    def test_the_near_tier_still_obeys_the_issue_count_gate(self):
        # A near title does not buy a candidate past the issue-count
        # gate; it only gets it as far as that filter. #146 opened that
        # gate for a volume the user already owns and deliberately left
        # it shut for everything else.
        group = _group('Tear Us')
        group['/content/Tear Us/Tear Us 04.cbz'] = dict(
            group['/content/Tear Us/Tear Us 01.cbz'], issue_number=4.0
        )
        ranked = _rank_volume_results_for_file(
            group,
            [_candidate('Tear Us Apart', comicvine_id=1, issue_count=1)],
            only_english=True
        )
        self.assertEqual(ranked, [])

    def test_an_english_match_hides_a_translated_one(self):
        translated = _candidate('Tear Us Apart', comicvine_id=1)
        translated['translated'] = True
        ranked = _rank_volume_results_for_file(
            _group('Tear Us'),
            [translated, _candidate('Tear Us Apart', comicvine_id=2)],
            only_english=True
        )
        self.assertEqual(
            [result['comicvine_id'] for result, _ in ranked],
            [2]
        )

    def test_a_translated_flag_is_not_the_last_word_on_its_own(self):
        # ComicVine's `translated` was dropping "Astronaut Down" (2022)
        # on an exact title and an exact year, with nothing else in the
        # response to prefer.
        translated = _candidate('Astronaut Down', comicvine_id=1, year=2022)
        translated['translated'] = True
        ranked = _rank_volume_results_for_file(
            _group('Astronaut Down', year=2022),
            [translated],
            only_english=True
        )
        self.assertEqual(len(ranked), 1)


if __name__ == '__main__':
    unittest.main()
