# -*- coding: utf-8 -*-

"""Library chatter must not read as a Kapowarr fault in Kapowarr's log."""

import logging
import unittest

from backend.base.logging import ThirdPartyNoiseFilter


def _record(name: str, level: int, message: str) -> logging.LogRecord:
    return logging.LogRecord(
        name=name, level=level, pathname=__file__, lineno=1,
        msg=message, args=(), exc_info=None
    )


class third_party_noise_is_demoted(unittest.TestCase):
    """Two library messages borrow Kapowarr's own vocabulary.

    waitress logs "Task queue depth is N" whenever more HTTP requests arrive at
    once than it has idle threads -- "Task queue" is a Kapowarr surface (System
    > Tasks), and this is neither that queue nor a problem. engineio logs
    "Invalid session <sid>" at ERROR when a browser reconnects a websocket
    whose session predates a restart, which is what every restart with a tab
    left open looks like. Both showed up as the loudest entries on the Logs
    page while meaning nothing.
    """

    def setUp(self):
        self.filter = ThirdPartyNoiseFilter()

    def test_waitress_queue_depth_is_not_an_error(self):
        record = _record('waitress.queue', logging.WARNING,
                         'Task queue depth is 2')

        self.assertTrue(self.filter.filter(record))
        self.assertEqual(record.levelname, 'INFO')
        self.assertEqual(record.levelno, logging.INFO)

    def test_engineio_stale_session_is_not_an_error(self):
        record = _record('engineio.server', logging.ERROR,
                         'Invalid session mzTfHxIY3hJx1khIAAAM')

        self.assertTrue(self.filter.filter(record))
        self.assertEqual(record.levelname, 'INFO')

    def test_the_record_is_kept_rather_than_dropped(self):
        """Under real sustained load the queue depth is worth seeing, and
        dropping it would remove the evidence behind a slow-server report."""
        record = _record('waitress.queue', logging.WARNING,
                         'Task queue depth is 40')

        self.assertTrue(
            self.filter.filter(record),
            'demoted, not discarded'
        )
        self.assertEqual(record.getMessage(), 'Task queue depth is 40')

    def test_a_real_kapowarr_error_is_untouched(self):
        record = _record('backend.features.tasks', logging.ERROR,
                         'Library import failed')

        self.assertTrue(self.filter.filter(record))
        self.assertEqual(record.levelname, 'ERROR')
        self.assertEqual(record.levelno, logging.ERROR)

    def test_a_genuine_waitress_error_is_untouched(self):
        """Only the queue-depth logger is demoted, not waitress as a whole."""
        record = _record('waitress', logging.ERROR,
                         'Exception when servicing request')

        self.assertTrue(self.filter.filter(record))
        self.assertEqual(record.levelname, 'ERROR')

    def test_info_from_a_demoted_logger_stays_info(self):
        record = _record('engineio.server', logging.INFO, 'Client connected')

        self.assertTrue(self.filter.filter(record))
        self.assertEqual(record.levelname, 'INFO')


class the_filter_is_wired_into_the_handlers(unittest.TestCase):
    def test_every_handler_that_could_show_an_error_applies_it(self):
        from backend.base.logging import LOGGING_CONFIG

        self.assertIn('third_party_noise', LOGGING_CONFIG['filters'])
        for handler in ('file', 'console', 'console_error'):
            self.assertIn(
                'third_party_noise',
                LOGGING_CONFIG['handlers'][handler].get('filters', []),
                f'{handler} would still report library chatter as a fault'
            )


if __name__ == '__main__':
    unittest.main()
