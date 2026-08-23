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


class the_row_offers_one_download_button(unittest.TestCase):
    """Two near-identical buttons, one of which never worked on the rows that
    needed it, read as the app being broken. Whether a result matches is
    already decided and shown in the Match column, so the row uses the right
    button rather than offering both."""

    def _template(self):
        with open('frontend/templates/view_volume.html') as handle:
            return handle.read()

    def test_the_second_download_button_is_gone(self):
        self.assertNotIn('Force Download', self._template())

    def test_both_icons_are_still_available_to_the_renderer(self):
        import os

        self.assertTrue(os.path.isfile('frontend/static/img/download.svg'))
        self.assertTrue(
            os.path.isfile('frontend/static/img/force_download.svg'),
            'the renderer swaps to this one for a row that needs forcing'
        )

    def test_manual_search_keeps_its_own_icon_elsewhere(self):
        # The issue row's real "search for this issue" action is unaffected.
        self.assertIn(
            '{{ icon_button(\'\', "Manually search for this issue", '
            '"manual_search.svg") }}',
            self._template()
        )


if __name__ == '__main__':
    unittest.main()
