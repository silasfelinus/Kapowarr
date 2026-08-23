# -*- coding: utf-8 -*-

"""Being told to slow down is not the same as a broken link."""

import unittest
from unittest.mock import MagicMock, patch

from requests import RequestException

from backend.base.custom_exceptions import DownloadLimitReached, LinkBroken
from backend.base.definitions import Constants, DownloadSource


class a_throttle_is_not_a_broken_link(unittest.TestCase):
    """A blocklisted link is never tried again.

    GetComics answers 429 under load. Every link it offered while doing so was
    recorded as broken and blocklisted permanently -- so a burst of throttling
    destroyed working links, and they stayed dead long after the throttling
    stopped. The Pixeldrain branch beside this already drew the distinction for
    one source; 429 is the status that means it in general.
    """

    def _classify(self, status, url='https://getcomics.org/dls/abc'):
        """Run the real classifier and return the exception it raises."""
        from backend.implementations.download_clients import BaseDirectDownload

        download = BaseDirectDownload.__new__(BaseDirectDownload)
        download._source_type = DownloadSource.GETCOMICS

        response = MagicMock()
        response.url = url
        response.status_code = status
        error = RequestException()
        error.response = response

        with self.assertRaises(Exception) as caught:
            download._raise_for_request_failure(error, url)
        return caught.exception

    def test_a_429_reports_a_reached_limit(self):
        raised = self._classify(429)

        self.assertIsInstance(raised, DownloadLimitReached)
        self.assertEqual(raised.source, DownloadSource.GETCOMICS)

    def test_a_404_is_still_a_broken_link(self):
        self.assertIsInstance(self._classify(404), LinkBroken)

    def test_a_403_from_pixeldrain_is_still_its_own_limit(self):
        raised = self._classify(
            403, url=Constants.PIXELDRAIN_API_URL + '/file/abc'
        )

        self.assertIsInstance(raised, DownloadLimitReached)
        self.assertEqual(raised.source, DownloadSource.PIXELDRAIN)

    def test_a_403_from_anywhere_else_is_a_broken_link(self):
        self.assertIsInstance(
            self._classify(403, url='https://example.test/x'), LinkBroken
        )

    def test_a_failure_with_no_response_is_a_broken_link(self):
        """A connection that never answered says nothing about throttling."""
        from backend.implementations.download_clients import BaseDirectDownload

        download = BaseDirectDownload.__new__(BaseDirectDownload)
        download._source_type = DownloadSource.GETCOMICS
        error = RequestException()
        error.response = None

        with self.assertRaises(LinkBroken):
            download._raise_for_request_failure(error, 'https://x/y')


class a_throttle_is_retried(unittest.TestCase):
    def test_429_is_in_the_retry_forcelist(self):
        # It is the one status that explicitly asks to be retried. Without it
        # a throttled request failed on its first answer.
        self.assertIn(429, Constants.STATUS_FORCELIST_RETRIES)

    def test_the_server_error_codes_are_still_retried(self):
        for status in (500, 502, 503, 504):
            self.assertIn(status, Constants.STATUS_FORCELIST_RETRIES)

    def test_a_404_is_not_retried(self):
        """Retrying something genuinely gone just delays the real answer."""
        self.assertNotIn(404, Constants.STATUS_FORCELIST_RETRIES)

    def test_retry_after_is_honoured(self):
        from inspect import signature

        from urllib3.util import Retry

        self.assertIs(
            signature(Retry.__init__).parameters[
                'respect_retry_after_header'
            ].default,
            True,
            'a 429 retry must wait as long as the server asked'
        )


if __name__ == '__main__':
    unittest.main()
