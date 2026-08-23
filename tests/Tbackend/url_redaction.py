# -*- coding: utf-8 -*-

"""Credentials in URLs must not reach the log file people share."""

import unittest

from backend.base.helpers import redact_url


class credential_bearing_urls_are_masked(unittest.TestCase):
    """Indexer and tracker URLs carry a working key in the query string.

    Kapowarr logs the download link on every enqueue, on every request retry,
    and again when a broken link is blocklisted -- so a log downloaded for a
    bug report, or handed to someone helping, contained a live Prowlarr API
    key in plain text several times over.
    """

    def test_a_prowlarr_api_key_is_masked(self):
        self.assertEqual(
            redact_url(
                'https://prowlarr.example.com/9/download'
                '?apikey=0123456789abcdef0123456789abcdef'
            ),
            'https://prowlarr.example.com/9/download?apikey=***'
        )

    def test_the_rest_of_the_query_survives(self):
        """The other parameters are what make a link diagnosable at all."""
        self.assertEqual(
            redact_url('https://p/9/download?apikey=k&link=abc&file=n.nzb'),
            'https://p/9/download?apikey=***&link=abc&file=n.nzb'
        )

    def test_tracker_passkeys_are_masked_too(self):
        self.assertEqual(
            redact_url('http://tracker/announce?passkey=deadbeef'),
            'http://tracker/announce?passkey=***'
        )

    def test_case_does_not_matter(self):
        self.assertEqual(
            redact_url('https://x/y?ApiKey=secret'),
            'https://x/y?ApiKey=***'
        )

    def test_a_url_with_no_credentials_is_returned_unchanged(self):
        url = 'https://getcomics.org/dc/100-bullets-the-us-of-anger-2-2026/'
        self.assertEqual(redact_url(url), url)

    def test_a_query_with_nothing_sensitive_is_untouched(self):
        url = 'https://x/y?page=2&sort=name'
        self.assertEqual(redact_url(url), url)

    def test_an_empty_value_is_left_alone(self):
        """Masking an absent key would invent a credential that is not there."""
        self.assertEqual(redact_url('https://x/y?apikey='), 'https://x/y?apikey=')

    def test_junk_is_returned_rather_than_raising(self):
        # Logging must never be able to raise on the way to reporting a
        # failure.
        for value in ('', 'not a url', 'http://['):
            self.assertIsInstance(redact_url(value), str)


class credentials_outside_the_query_string(unittest.TestCase):
    """A credential is not always a query parameter.

    Masking only the query left two shapes readable: a Discord webhook token,
    which sits in the path and lets anyone holding it post to the channel, and
    a proxy password in the `user:password@host` authority.
    """

    def test_a_webhook_token_in_the_path_is_masked(self):
        self.assertEqual(
            redact_url('https://discord.com/api/webhooks/123456789/tokenXYZ'),
            'https://discord.com/api/webhooks/123456789/***'
        )

    def test_the_webhook_id_survives_so_the_target_is_identifiable(self):
        # Which webhook failed is the diagnostic value; the token is not.
        self.assertIn(
            '123456789',
            redact_url('https://discord.com/api/webhooks/123456789/tokenXYZ')
        )

    def test_a_proxy_password_is_masked_but_the_user_is_kept(self):
        self.assertEqual(
            redact_url('http://user:hunter2@proxy.internal:8080'),
            'http://user:***@proxy.internal:8080'
        )

    def test_a_host_with_no_userinfo_is_untouched(self):
        self.assertEqual(
            redact_url('http://proxy.internal:8080/x'),
            'http://proxy.internal:8080/x'
        )

    def test_an_ordinary_path_is_not_mistaken_for_a_token(self):
        url = 'https://getcomics.org/dc/100-bullets-the-us-of-anger-2-2026/'
        self.assertEqual(redact_url(url), url)


class every_call_site_can_actually_call_it(unittest.TestCase):
    """Matching source text does not prove the name resolves.

    The previous version of this asserted that `redact_url(...)` appeared in
    each module's source, which it did -- in a module that never imported it.
    Every call raised NameError instead, and because the failing call was
    inside the blocklist path taken when a download link breaks, a handled
    "link broken" became a 500 from the API. Resolve the name in each module
    rather than reading for it.
    """

    def test_the_name_resolves_in_every_module_that_uses_it(self):
        import importlib

        modules = (
            'backend.base.helpers',
            'backend.features.download_queue',
            'backend.implementations.blocklist',
            'backend.implementations.indexers_core',
            'backend.implementations.notifications',
            'backend.implementations.weekly_releases',
        )
        missing = []
        for name in modules:
            module = importlib.import_module(name)
            with open(module.__file__) as handle:
                uses_it = 'redact_url(' in handle.read()
            if uses_it and not hasattr(module, 'redact_url'):
                missing.append(name)

        self.assertEqual(
            missing, [],
            'these modules call redact_url without importing it'
        )


class blocklisting_a_broken_link_works(unittest.TestCase):
    """The path a failed download actually takes.

    A broken indexer link is blocklisted, and that call logs the link. When
    the log line raised NameError, the blocklist insert never happened and the
    API returned a 500 instead of the handled failure.
    """

    def test_logging_the_link_does_not_raise(self):
        from unittest.mock import MagicMock, patch

        from backend.base.definitions import BlocklistReason
        from backend.implementations import blocklist as bl

        with patch.object(bl, 'get_db', return_value=MagicMock()), \
                patch.object(bl, 'blocklist_contains', return_value=None), \
                patch.object(bl, 'get_blocklist_entry'), \
                patch.object(bl, 'LOGGER') as logger:
            bl.add_to_blocklist(
                web_link=None, web_title=None, web_sub_title=None,
                download_link=(
                    'https://prowlarr.example.com/9/download?apikey=secret'
                ),
                source=None, volume_id=1, issue_id=None,
                reason=BlocklistReason.LINK_BROKEN
            )

        logged = logger.info.call_args[0][0]
        self.assertIn('apikey=***', logged)
        self.assertNotIn('secret', logged)


class a_failed_request_says_why(unittest.TestCase):
    """"Request failed" alone cannot be diagnosed.

    A 503 from the indexer, a refused connection and an expired certificate
    all read identically, and the caller reports every one of them as
    "Download link broken" -- which is what made a whole class of download
    failure impossible to tell apart from the log.
    """

    def test_the_retry_warning_carries_a_reason(self):
        import inspect

        from backend.base import helpers

        source = inspect.getsource(helpers.AsyncSession)
        self.assertIn("reason = f'HTTP {response.status}'", source)
        self.assertIn('type(error).__name__', source)
        self.assertIn('Retrying for round', source)

    def test_exhausting_retries_is_logged_with_the_reason(self):
        import inspect

        from backend.base import helpers

        self.assertIn(
            'failed after %d attempts',
            inspect.getsource(helpers.AsyncSession)
        )


if __name__ == '__main__':
    unittest.main()


class the_formatter_is_the_backstop(unittest.TestCase):
    """Call-site redaction cannot cover everything that reaches the file.

    An exception carrying a URL in its message is formatted by the logging
    framework, so its traceback prints the credential whatever the call site
    did, and a third-party library logs whatever it likes. Redacting the
    finished line is the only place that sees all of it.
    """

    def _log(self, emit):
        import tempfile

        from backend.base.logging import (LOGGER, get_log_file_contents,
                                          setup_logging)

        setup_logging(tempfile.mkdtemp(), 'redaction-test.log', 20)
        emit(LOGGER)
        return get_log_file_contents().getvalue()

    def test_a_key_inside_a_traceback_is_scrubbed(self):
        def emit(logger):
            try:
                raise RuntimeError(
                    'failed fetching https://prowlarr.example.com/9/download'
                    '?apikey=SUPERSECRET123&link=abc'
                )
            except RuntimeError:
                logger.exception('Download failed')

        output = self._log(emit)

        self.assertNotIn('SUPERSECRET123', output)
        self.assertIn('apikey=***', output)
        self.assertIn(
            'link=abc', output,
            'the rest of the link is what makes the failure diagnosable'
        )

    def test_a_library_message_is_scrubbed_too(self):
        import logging as stdlib_logging

        output = self._log(
            lambda _: stdlib_logging.getLogger('waitress').error(
                'boom for http://user:hunter2@proxy:8080'
            )
        )

        self.assertNotIn('hunter2', output)
        self.assertIn('user:***@proxy:8080', output)

    def test_a_webhook_token_in_a_message_is_scrubbed(self):
        output = self._log(
            lambda logger: logger.info(
                'hook https://discord.com/api/webhooks/999/TOKENabc'
            )
        )

        self.assertNotIn('TOKENabc', output)
        self.assertIn('/webhooks/999/***', output)

    def test_ordinary_diagnostics_survive_untouched(self):
        output = self._log(
            lambda logger: logger.info(
                'Adding download for volume 2409 issue 40901: '
                'https://getcomics.org/dc/100-bullets-the-us-of-anger-2-2026/'
            )
        )

        self.assertIn('volume 2409 issue 40901', output)
        self.assertIn('100-bullets-the-us-of-anger-2-2026', output)
        self.assertNotIn('***', output)

    def test_the_exact_line_that_leaked(self):
        """The shape from the report, verbatim apart from the key itself."""
        output = self._log(
            lambda logger: logger.info(
                'Adding download for volume 2409 issue 40901: '
                'https://prowlarr.example.com/9/download'
                '?apikey=0123456789abcdef0123456789abcdef'
            )
        )

        self.assertNotIn('0123456789abcdef', output)
        self.assertIn('apikey=***', output)
