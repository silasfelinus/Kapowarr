# -*- coding: utf-8 -*-

import logging
import unittest

from backend.features.log_view import filter_log_entries, parse_log_entries


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

    def test_filters_by_minimum_level_query_and_newest_first(self):
        entries = parse_log_entries(
            '2026-08-19T00:01:01-0700 | MainProcess | MainThread | a.pyL1 | INFO | first\n'
            '2026-08-19T00:01:02-0700 | MainProcess | Queue | b.pyL2 | WARNING | slow indexer\n'
            '2026-08-19T00:01:03-0700 | MainProcess | Queue | c.pyL3 | ERROR | indexer failed\n'
        )

        filtered = filter_log_entries(
            entries,
            logging.WARNING,
            'indexer',
            10,
        )

        self.assertEqual([entry['level'] for entry in filtered], ['ERROR', 'WARNING'])
        self.assertEqual(filtered[0]['source'], 'c.pyL3')

    def test_limit_applies_after_filtering(self):
        entries = parse_log_entries(
            '2026-08-19T00:01:01-0700 | MainProcess | MainThread | a.pyL1 | INFO | one\n'
            '2026-08-19T00:01:02-0700 | MainProcess | MainThread | b.pyL2 | INFO | two\n'
            '2026-08-19T00:01:03-0700 | MainProcess | MainThread | c.pyL3 | INFO | three\n'
        )

        filtered = filter_log_entries(entries, logging.INFO, '', 2)

        self.assertEqual([entry['message'] for entry in filtered], ['three', 'two'])


if __name__ == '__main__':
    unittest.main()
