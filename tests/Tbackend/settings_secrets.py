import unittest

from backend.base.definitions import Constants
from backend.internals.settings import _redact_settings_for_log


class settings_log_redaction(unittest.TestCase):
    def test_redacts_credentials_but_keeps_non_secret_context(self):
        result = _redact_settings_for_log({
            'metron_api_token': 'token-value',
            'metron_password': 'password-value',
            'comicvine_api_key': 'comicvine-value',
            'api_key': 'kapowarr-value',
            'date_type': 'cover_date'
        })

        self.assertEqual(result['date_type'], 'cover_date')
        for key in (
            'metron_api_token', 'metron_password',
            'comicvine_api_key', 'api_key'
        ):
            self.assertEqual(result[key], Constants.CREDENTIAL_REPLACEMENT)

    def test_keeps_empty_secret_values_empty(self):
        self.assertEqual(
            _redact_settings_for_log({'metron_api_token': ''}),
            {'metron_api_token': ''}
        )


if __name__ == '__main__':
    unittest.main()
