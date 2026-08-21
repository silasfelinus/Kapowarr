import json
import unittest
from datetime import date
from unittest.mock import patch

from backend.features import pull_list as pull_list_module


class _Rows:
    def __init__(self, rows):
        self.rows = rows

    def fetchalldict(self):
        return [dict(row) for row in self.rows]


class _Cursor:
    def __init__(self):
        self.calls = 0

    def execute(self, *args, **kwargs):
        self.calls += 1
        if self.calls == 1:
            return _Rows([{
                'publisher': 'Marvel Comics',
                'root_folder_id': None,
                'auto_search': 0,
                'release_count': 129
            }])
        return _Rows([{
            'publisher': 'Marvel Comics',
            'week_start': date(2026, 8, 17),
            'release_count': 129
        }])


class pull_list_publisher_json_keys(unittest.TestCase):
    def test_date_week_keys_are_normalized_before_api_serialization(self):
        with patch.object(
            pull_list_module, 'get_db', return_value=_Cursor()
        ):
            publishers = pull_list_module.get_publishers()

        self.assertEqual(
            publishers[0]['release_counts'],
            {'2026-08-17': 129}
        )
        json.dumps({'error': None, 'result': publishers})


if __name__ == '__main__':
    unittest.main()
