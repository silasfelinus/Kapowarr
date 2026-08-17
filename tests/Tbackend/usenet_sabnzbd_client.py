import unittest
from typing import Any, Dict, List
from unittest.mock import patch

from requests.exceptions import ConnectionError as RequestsConnectionError

from backend.base.custom_exceptions import ClientNotWorking, CredentialInvalid
from backend.base.definitions import DownloadState, DownloadType
from backend.implementations.external_clients import ExternalClients
from backend.implementations.usenet_clients import SABnzbd as SABnzbd_module
from backend.implementations.usenet_clients.SABnzbd import SABnzbd


class sabnzbd_registration(unittest.TestCase):
    def test_registered_under_its_client_type(self):
        types = ExternalClients.get_client_types()
        self.assertIs(types.get('SABnzbd'), SABnzbd)

    def test_download_type_is_usenet(self):
        self.assertIs(SABnzbd.download_type, DownloadType.USENET)


class FakeResponse:
    def __init__(
        self,
        json_body: Dict[str, Any],
        status_code: int = 200
    ) -> None:
        self._json_body = json_body
        self.status_code = status_code
        self.ok = 200 <= status_code < 400
        self.text = str(json_body)

    def json(self) -> Dict[str, Any]:
        return self._json_body


class FakeSession:
    """Stand-in for `backend.base.helpers.Session` that dispatches on the
    `mode` query param the way a real SABnzbd instance would, without any
    network access."""

    def __init__(self, responses: Dict[str, Any]) -> None:
        # mode -> FakeResponse, or a list of FakeResponse (consumed in
        # order, last one repeats) for modes queried more than once.
        self.responses = responses
        self.calls: List[Dict[str, Any]] = []

    def get(self, url: str, params: Dict[str, Any]) -> FakeResponse:
        self.calls.append({'url': url, 'params': params})
        mode = params['mode']
        entry = self.responses[mode]
        if isinstance(entry, list):
            return entry.pop(0) if len(entry) > 1 else entry[0]
        return entry


def _patched_session(fake_session: FakeSession):
    return patch.object(SABnzbd_module, 'Session', return_value=fake_session)


class sabnzbd_connection_test(unittest.TestCase):
    def test_test_succeeds_on_valid_version_response(self):
        fake = FakeSession({'version': FakeResponse({'version': '4.3.0'})})
        with _patched_session(fake):
            # Should not raise
            SABnzbd.test('http://localhost:8080', api_token='abc123')

    def test_test_raises_credential_invalid_without_api_token(self):
        with self.assertRaises(CredentialInvalid):
            SABnzbd.test('http://localhost:8080')

    def test_test_raises_credential_invalid_on_bad_api_key(self):
        fake = FakeSession({
            'version': FakeResponse(
                {'status': False, 'error': 'API Key Incorrect'}
            )
        })
        with _patched_session(fake):
            with self.assertRaises(CredentialInvalid):
                SABnzbd.test('http://localhost:8080', api_token='wrong')

    def test_test_raises_client_not_working_on_connection_error(self):
        class RaisingSession(FakeSession):
            def get(self, url, params):
                raise RequestsConnectionError()

        with patch.object(
            SABnzbd_module, 'Session', return_value=RaisingSession({})
        ):
            with self.assertRaises(ClientNotWorking):
                SABnzbd.test('http://localhost:8080', api_token='abc123')

    def test_test_raises_client_not_working_on_401(self):
        fake = FakeSession({
            'version': FakeResponse({}, status_code=401)
        })
        with _patched_session(fake):
            with self.assertRaises(CredentialInvalid):
                SABnzbd.test('http://localhost:8080', api_token='abc123')


class sabnzbd_client_instance(unittest.TestCase):
    """Exercises add_download/get_download/delete_download against an
    already-constructed SABnzbd instance, bypassing BaseExternalClient's
    DB-backed __init__ (covered separately for other clients' constructors;
    not the concern of this file)."""

    @staticmethod
    def _make_client() -> SABnzbd:
        client = SABnzbd.__new__(SABnzbd)
        client._id = 1
        client._title = 'My SABnzbd'
        client._base_url = 'http://localhost:8080'
        client._username = None
        client._password = None
        client._api_token = 'abc123'
        client.ssn = None
        client.known_ids = set()
        return client

    def test_add_download_returns_nzo_id(self):
        client = self._make_client()
        fake = FakeSession({
            'addurl': FakeResponse({'status': True, 'nzo_ids': ['SABnzbd_nzo_1']})
        })
        with _patched_session(fake):
            nzo_id = client.add_download(
                'http://indexer.example/get/1.nzb',
                '/downloads/kapowarr',
                'Batman 001'
            )

        self.assertEqual(nzo_id, 'SABnzbd_nzo_1')
        self.assertIn('SABnzbd_nzo_1', client.known_ids)
        # cat is always the fixed Kapowarr category -- SABnzbd has no
        # per-job arbitrary target-folder param.
        self.assertEqual(
            fake.calls[0]['params']['cat'],
            'kapowarr'
        )
        self.assertEqual(
            fake.calls[0]['params']['nzbname'],
            'Batman 001'
        )

    def test_add_download_raises_when_no_nzo_id_returned(self):
        client = self._make_client()
        fake = FakeSession({
            'addurl': FakeResponse({'status': True, 'nzo_ids': []})
        })
        with _patched_session(fake):
            with self.assertRaises(ClientNotWorking):
                client.add_download('http://indexer.example/get/1.nzb', '/x', None)

    def test_get_download_maps_queue_states(self):
        cases = (
            ('Downloading', DownloadState.DOWNLOADING_STATE),
            ('Paused', DownloadState.PAUSED_STATE),
            ('Queued', DownloadState.QUEUED_STATE),
            ('Grabbing', DownloadState.QUEUED_STATE),
            ('Propagating', DownloadState.DOWNLOADING_STATE),
        )
        for sab_status, expected_state in cases:
            with self.subTest(sab_status=sab_status):
                # Fresh client per case: get_download() caches self.ssn
                # once set, so a shared client across cases would keep
                # reusing the first case's patched session.
                client = self._make_client()
                client.known_ids.add('nzo_1')
                fake = FakeSession({
                    'queue': FakeResponse({
                        'queue': {'slots': [{
                            'nzo_id': 'nzo_1',
                            'status': sab_status,
                            'mb': '100',
                            'mbleft': '25',
                            'kbpersec': '512'
                        }]}
                    })
                })
                with _patched_session(fake):
                    result = client.get_download('nzo_1')

                self.assertEqual(result['state'], expected_state)
                self.assertEqual(result['progress'], 75.0)
                self.assertEqual(result['speed'], 512 * 1024)
                self.assertEqual(result['size'], round(100 * 1024 * 1024))
                self.assertIsNone(result['storage'])

    def test_get_download_completed_history_reports_importing_and_storage(self):
        client = self._make_client()
        client.known_ids.add('nzo_1')
        fake = FakeSession({
            'queue': FakeResponse({'queue': {'slots': []}}),
            'history': FakeResponse({
                'history': {'slots': [{
                    'nzo_id': 'nzo_1',
                    'status': 'Completed',
                    'bytes': '104857600',
                    'storage': '/downloads/kapowarr/Batman 001'
                }]}
            })
        })
        with _patched_session(fake):
            result = client.get_download('nzo_1')

        self.assertEqual(result['state'], DownloadState.IMPORTING_STATE)
        self.assertEqual(result['progress'], 100.0)
        self.assertEqual(result['storage'], '/downloads/kapowarr/Batman 001')

    def test_get_download_history_in_progress_stays_downloading(self):
        client = self._make_client()
        client.known_ids.add('nzo_1')
        fake = FakeSession({
            'queue': FakeResponse({'queue': {'slots': []}}),
            'history': FakeResponse({
                'history': {'slots': [{
                    'nzo_id': 'nzo_1',
                    'status': 'Extracting',
                    'bytes': '104857600'
                }]}
            })
        })
        with _patched_session(fake):
            result = client.get_download('nzo_1')

        self.assertEqual(result['state'], DownloadState.DOWNLOADING_STATE)
        self.assertIsNone(result['storage'])

    def test_get_download_failed_history(self):
        client = self._make_client()
        client.known_ids.add('nzo_1')
        fake = FakeSession({
            'queue': FakeResponse({'queue': {'slots': []}}),
            'history': FakeResponse({
                'history': {'slots': [{
                    'nzo_id': 'nzo_1',
                    'status': 'Failed',
                    'bytes': '0',
                    'fail_message': 'Unpacking failed'
                }]}
            })
        })
        with _patched_session(fake):
            result = client.get_download('nzo_1')

        self.assertEqual(result['state'], DownloadState.FAILED_STATE)

    def test_get_download_returns_none_when_known_id_disappears(self):
        client = self._make_client()
        client.known_ids.add('nzo_1')
        fake = FakeSession({
            'queue': FakeResponse({'queue': {'slots': []}}),
            'history': FakeResponse({'history': {'slots': []}})
        })
        with _patched_session(fake):
            result = client.get_download('nzo_1')

        self.assertIsNone(result)

    def test_get_download_returns_empty_dict_when_never_known(self):
        client = self._make_client()
        fake = FakeSession({
            'queue': FakeResponse({'queue': {'slots': []}}),
            'history': FakeResponse({'history': {'slots': []}})
        })
        with _patched_session(fake):
            result = client.get_download('nzo_unknown')

        self.assertEqual(result, {})

    def test_delete_download_hits_both_queue_and_history_and_forgets_id(self):
        client = self._make_client()
        client.known_ids.add('nzo_1')
        fake = FakeSession({
            'queue': FakeResponse({'status': True}),
            'history': FakeResponse({'status': True})
        })
        with _patched_session(fake):
            client.delete_download('nzo_1', delete_files=True)

        modes_called = [c['params']['mode'] for c in fake.calls]
        self.assertEqual(modes_called, ['queue', 'history'])
        self.assertNotIn('nzo_1', client.known_ids)
        for call in fake.calls:
            self.assertEqual(call['params']['del_files'], 1)


if __name__ == '__main__':
    unittest.main()
