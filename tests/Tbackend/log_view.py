# -*- coding: utf-8 -*-

import logging
import unittest

from backend.features.log_view import (
    LOG_LEVEL_ALL,
    filter_log_entries,
    page_log_entries,
    parse_log_entries,
)


class LogViewTest(unittest.TestCase):
    def test_parses_detailed_entries_and_preserves_multiline_tracebacks(self):
        contents = (
            '2026-08-19T00:01:02-0700 | MainProcess | MainThread | app.pyL12 | INFO | Started\n'
            '2026-08-19T00:01:03-0700 | MainProcess | Worker | task.pyL44 | ERROR | Failed\n'
            'Traceback (most recent call last):\n'
            '  File "task.py", line 44, in run\n'
            'RuntimeError: boom\n'
        )

        entries = parse_log_entries(contents)

        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0]['level'], 'INFO')
        self.assertEqual(entries[1]['source'], 'task.pyL44')
        self.assertEqual(entries[1]['level_no'], logging.ERROR)
        self.assertIn('Traceback', entries[1]['message'])
        self.assertIn('RuntimeError: boom', entries[1]['message'])

    def _entries(self):
        return parse_log_entries(
            '2026-08-19T00:01:01-0700 | MainProcess | MainThread | a.pyL1 | INFO | first\n'
            '2026-08-19T00:01:02-0700 | MainProcess | Queue | b.pyL2 | WARNING | slow indexer\n'
            '2026-08-19T00:01:03-0700 | MainProcess | Queue | c.pyL3 | ERROR | indexer failed\n'
        )

    def test_all_levels_returns_everything_newest_first(self):
        filtered = filter_log_entries(self._entries(), LOG_LEVEL_ALL)

        self.assertEqual(
            [entry['level'] for entry in filtered],
            ['ERROR', 'WARNING', 'INFO']
        )

    def test_a_level_selects_that_level_alone(self):
        """Picking a level off a filter means "show me these".

        A minimum instead returned everything beneath it too, so asking for
        warnings buried them under every info line in the log.
        """
        filtered = filter_log_entries(self._entries(), 'WARNING')

        self.assertEqual([entry['level'] for entry in filtered], ['WARNING'])
        self.assertEqual(filtered[0]['source'], 'b.pyL2')

    def test_paging_slices_and_reports_where_it_is(self):
        entries = filter_log_entries(self._entries(), LOG_LEVEL_ALL)

        page, meta = page_log_entries(entries, page=2, page_size=2)

        self.assertEqual([entry['level'] for entry in page], ['INFO'])
        self.assertEqual(meta['page'], 2)
        self.assertEqual(meta['total_pages'], 2)
        self.assertEqual(meta['total_entries'], 3)

    def test_a_page_past_the_end_clamps_to_the_last_one(self):
        """A filter that shortens the log must not strand the reader on an
        empty page with nothing explaining why."""
        entries = filter_log_entries(self._entries(), LOG_LEVEL_ALL)

        page, meta = page_log_entries(entries, page=99, page_size=2)

        self.assertEqual(meta['page'], 2)
        self.assertEqual(len(page), 1)

    def test_an_empty_log_still_reports_one_page(self):
        page, meta = page_log_entries([], page=1, page_size=50)

        self.assertEqual(page, [])
        self.assertEqual(meta['total_pages'], 1)
        self.assertEqual(meta['total_entries'], 0)


if __name__ == '__main__':
    unittest.main()
