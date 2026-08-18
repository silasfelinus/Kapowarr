# -*- coding: utf-8 -*-

from unittest import TestCase
from unittest.mock import MagicMock, patch

from backend.base.custom_exceptions import ExternalClientNotFound
from backend.base.definitions import DownloadType
from backend.features import acquisition_preferences as preferences
from backend.implementations.external_clients import ExternalClients


class client_priority_policy(TestCase):
    def test_client_priority_map_validation(self):
        self.assertEqual(
            preferences._validated_client_priority_map({'1': 1, '20': 100}),
            {'1': 1, '20': 100}
        )

        invalid_values = (
            {'client:1': 1},
            {'0': 1},
            {'1': 0},
            {'1': 101},
            {'1': True},
            {'1': '1'}
        )
        for value in invalid_values:
            with self.subTest(value=value):
                with self.assertRaises(Exception):
                    preferences._validated_client_priority_map(value)

    def test_client_priority_defaults_to_fifty(self):
        with patch.object(
            preferences,
            'get_acquisition_preferences',
            return_value={'client_priorities': {'7': 3}}
        ):
            self.assertEqual(preferences.client_priority(7), 3)
            self.assertEqual(preferences.client_priority(8), 50)

    @patch.object(preferences, 'update_acquisition_preferences')
    @patch.object(preferences, 'get_acquisition_preferences')
    def test_removing_client_priority_prunes_only_that_client(
        self, get_preferences, update_preferences
    ):
        get_preferences.return_value = {
            'client_priorities': {'7': 3, '8': 40}
        }

        preferences.remove_client_priority(7)

        update_preferences.assert_called_once_with({
            'client_priorities': {'8': 40}
        })


class external_client_selection(TestCase):
    @staticmethod
    def _cursor(rows):
        cursor = MagicMock()
        cursor.execute.return_value.fetchall.return_value = rows
        return cursor

    @patch('backend.implementations.external_clients.ExternalClients.get_client')
    @patch('backend.implementations.external_clients.client_priority')
    @patch('backend.implementations.external_clients.get_db')
    def test_higher_priority_beats_lower_even_when_busier(
        self, get_db, client_priority, get_client
    ):
        get_db.return_value = self._cursor([
            {'id': 1, 'queue_count': 9},
            {'id': 2, 'queue_count': 0}
        ])
        client_priority.side_effect = lambda client_id: {1: 1, 2: 50}[client_id]
        get_client.side_effect = lambda client_id: client_id

        selected = ExternalClients.get_least_used_client(DownloadType.USENET)

        self.assertEqual(selected, 1)

    @patch('backend.implementations.external_clients.ExternalClients.get_client')
    @patch('backend.implementations.external_clients.client_priority')
    @patch('backend.implementations.external_clients.get_db')
    def test_equal_priority_keeps_least_used_balancing(
        self, get_db, client_priority, get_client
    ):
        get_db.return_value = self._cursor([
            {'id': 1, 'queue_count': 5},
            {'id': 2, 'queue_count': 1}
        ])
        client_priority.return_value = 10
        get_client.side_effect = lambda client_id: client_id

        selected = ExternalClients.get_least_used_client(DownloadType.TORRENT)

        self.assertEqual(selected, 2)

    @patch('backend.implementations.external_clients.ExternalClients.get_client')
    @patch('backend.implementations.external_clients.client_priority')
    @patch('backend.implementations.external_clients.get_db')
    def test_equal_priority_and_load_use_stable_id_tiebreak(
        self, get_db, client_priority, get_client
    ):
        get_db.return_value = self._cursor([
            {'id': 2, 'queue_count': 1},
            {'id': 1, 'queue_count': 1}
        ])
        client_priority.return_value = 10
        get_client.side_effect = lambda client_id: client_id

        selected = ExternalClients.get_least_used_client(DownloadType.USENET)

        self.assertEqual(selected, 1)

    @patch('backend.implementations.external_clients.get_db')
    def test_no_matching_clients_raises(self, get_db):
        get_db.return_value = self._cursor([])

        with self.assertRaises(ExternalClientNotFound):
            ExternalClients.get_least_used_client(DownloadType.USENET)
