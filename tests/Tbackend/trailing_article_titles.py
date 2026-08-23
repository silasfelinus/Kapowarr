# -*- coding: utf-8 -*-

"""A series filed under its trailing article is still that series."""

import unittest

from backend.implementations.matching import match_title


class a_trailing_the_still_matches(unittest.TestCase):
    """Every case here is a folder from a real library that went unmatched.

    The library files series under a trailing article -- "Immortal Hulk, The",
    "Crow - Pestilence, The", and in this shape all the way up to a folder
    literally named "Art of, The". `clean_title_regex` stripped "the" only
    when whitespace followed it, so it removed the leading "The" from the
    ComicVine title and kept the trailing one from the folder. The two cleaned
    strings could then never be equal, every candidate was filtered out before
    scoring, and the folder was held for review as 'no-candidate' -- not
    because ComicVine lacked the series, but because the comparison could not
    see it. Replaying one review queue, 77 folders were held for exactly this.
    """

    def test_the_hulk_by_either_name(self):
        self.assertTrue(match_title('Immortal Hulk, The', 'The Immortal Hulk'))

    def test_an_article_at_both_ends(self):
        self.assertTrue(
            match_title('EC Archives The Haunt of Fear, The',
                        'The EC Archives: The Haunt of Fear')
        )

    def test_the_candidate_need_not_have_an_article_at_all(self):
        self.assertTrue(match_title('Hellblazer, The', 'Hellblazer'))

    def test_a_subtitle_keeps_its_own_article(self):
        self.assertTrue(
            match_title('Crow Pestilence, The', 'The Crow: Pestilence')
        )
        self.assertTrue(
            match_title('Dreaming Waking Hours, The',
                        'The Dreaming: Waking Hours')
        )

    def test_the_folder_convention_taken_to_its_limit(self):
        """This is an actual folder name in the library."""
        self.assertTrue(match_title('Art of, The', 'The Art of'))


class a_question_mark_is_not_part_of_the_name(unittest.TestCase):
    """Most filesystems will not carry one, so the folder never has it."""

    def test_darkseid(self):
        self.assertTrue(
            match_title('Are You Afraid of Darkseid',
                        'Are You Afraid of Darkseid?')
        )

    def test_a_question_mark_in_the_middle(self):
        self.assertTrue(
            match_title('Do Androids Dream of Electric Sheep Dust to Dust',
                        'Do Androids Dream of Electric Sheep? Dust to Dust')
        )

    def test_combined_with_the_punctuation_already_stripped(self):
        self.assertTrue(
            match_title('Batman Bruce Wayne Murderer',
                        'Batman: Bruce Wayne-Murderer?')
        )


class titles_that_must_still_be_told_apart(unittest.TestCase):
    """Loosening the comparison must not start merging distinct series."""

    def test_different_series_do_not_collapse(self):
        self.assertFalse(match_title('Immortal Hulk', 'Immortal Iron Fist'))
        self.assertFalse(match_title('The Crow', 'The Crow: Pestilence'))
        self.assertFalse(match_title('Batman', 'Batman Beyond'))

    def test_a_word_merely_containing_the_is_untouched(self):
        """`\\bthe\\b` is a word boundary, so 'Theseus' keeps its start."""
        self.assertTrue(match_title('Theseus', 'Theseus'))
        self.assertFalse(match_title('Theseus', 'seus'))

    def test_the_previously_working_cases_are_unchanged(self):
        self.assertTrue(match_title('The Amazing Spider-Man',
                                    'Amazing Spider-Man'))
        self.assertTrue(match_title('Batman & Robin', 'Batman and Robin'))
        self.assertTrue(match_title('X-Men', 'X Men'))


if __name__ == '__main__':
    unittest.main()
