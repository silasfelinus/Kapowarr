# -*- coding: utf-8 -*-

"""A refused indexer result must say what happened, and what to do."""

import unittest

from backend.base.definitions import EnqueuingDownloadFailureReason as Reason


class the_refusal_names_the_override(unittest.TestCase):
    """Reusing NO_MATCHES described the wrong situation.

    NO_MATCHES is about scraping a page and finding nothing usable on it,
    which reads as though the release could not be reached. For a single
    indexer result it was reached, fetched, and then refused for being a
    different issue than the one asked for -- and the button that overrides
    that sits directly beside the one that was pressed.
    """

    def test_the_reason_exists_and_is_distinct(self):
        self.assertNotEqual(
            Reason.RESULT_DOES_NOT_MATCH.value, Reason.NO_MATCHES.value
        )

    def test_it_says_the_release_does_not_match(self):
        text = Reason.RESULT_DOES_NOT_MATCH.value

        self.assertIn('does not match', text)
        self.assertNotIn(
            'webpage', text,
            'there is no webpage involved in an indexer result'
        )

    def test_it_names_the_action_that_overrides_it(self):
        self.assertIn('Download anyway', Reason.RESULT_DOES_NOT_MATCH.value)

    def test_the_webpage_reason_is_untouched(self):
        """GetComics really does scrape a page, so its wording still fits."""
        self.assertIn('webpage', Reason.NO_MATCHES.value)


class the_indexer_paths_use_it(unittest.TestCase):
    def test_a_refused_indexer_result_raises_the_new_reason(self):
        import inspect

        from backend.implementations import indexers, indexers_core, torznab

        for module in (indexers, indexers_core, torznab):
            source = inspect.getsource(module)
            self.assertNotIn(
                'EnqueuingDownloadFailureReason.NO_MATCHES', source,
                f'{module.__name__} still reports a scraping failure'
            )

    def test_getcomics_still_reports_a_scraping_failure(self):
        import inspect

        from backend.implementations import getcomics

        self.assertIn(
            'EnqueuingDownloadFailureReason.NO_MATCHES',
            inspect.getsource(getcomics)
        )


class the_force_button_looks_like_a_download(unittest.TestCase):
    """It borrowed the manual-search icon, so a "search" glyph sat inside a
    list of search results and read as "search again" rather than as a
    variant of the button beside it."""

    def _template(self):
        with open('frontend/templates/view_volume.html') as handle:
            return handle.read()

    def test_the_action_row_uses_a_download_variant(self):
        template = self._template()

        self.assertIn('"force_download.svg"', template)
        self.assertNotIn(
            '{{ icon_button(\'\', "Force Download", "manual_search.svg") }}',
            template
        )

    def test_the_icon_file_exists(self):
        import os

        self.assertTrue(
            os.path.isfile('frontend/static/img/force_download.svg')
        )

    def test_its_tooltip_explains_what_it_skips(self):
        self.assertIn('skips the check', self._template())

    def test_manual_search_keeps_its_own_icon_elsewhere(self):
        # The issue row's real "search for this issue" action is unaffected.
        self.assertIn(
            '{{ icon_button(\'\', "Manually search for this issue", '
            '"manual_search.svg") }}',
            self._template()
        )


if __name__ == '__main__':
    unittest.main()
