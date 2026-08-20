import unittest
from typing import Any, Dict, List
from unittest.mock import patch

from requests.exceptions import ConnectionError as RequestsConnectionError

from backend.base.custom_exceptions import ClientNotWorking, CredentialInvalid
from backend.base.definitions import DownloadState, DownloadType
from backend.implementations.external_clients import ExternalClients
from backend.implementations.usenet_clients import NZBGet as NZBGet_module
from backend.implementations.usenet_clients.NZBGet import NZBGet


class nzbget_registration(unittest.TestCase):
    def test_registered_under_its_client_type(self):
        types = ExternalClients.get_client_types()
        self.assertIs(types.get('NZBGet'), NZBGet)

    def test_download_type_is_usenet(self):
        self.assertIs(NZBGet.download_type, DownloadType.USENET)

    def test_sits_alongside_sabnzbd_as_a_second_usenet_client(self):
        """The point of the client seam: two independent USENET clients
        registered at once, so `get_least_used_client(USENET)` has a real
        choice instead of SABnzbd being the only possible answer."""
        from backend.implementations.usenet_clients.SABnzbd import SABnzbd
        usenet_types = {
            name: client
            for name, client in ExternalClients.get_client_types().items()
            if client.download_type is DownloadType.USENET
        }
        self.assertEqual(set(usenet_types), {'NZBGet', 'SABnzbd'})
        self.assertIs(usenet_types['SABnzbd'], SABnzbd)

    def test_authenticates_with_username_and_password(self):
        # NZBGet uses ControlUsername/ControlPassword, not an API key --
        # the one field-level difference from SABnzbd, and what drives
        # which inputs the settings form renders.
        self.assertEqual(
            NZBGet.required_tokens,
            ('title', 'base_url', 'username', 'password')
        )


class FakeResponse:
    def __init__(
        self,
        json_body: Any,
        status_code: int = 200
    ) -> None:
        self._json_body = json_body
        self.status_code = status_code
        self.ok = 200 <= status_code < 400
        self.text = str(json_body)

    def json(self) -> Any:
        return self._json_body


class FakeSession:
    """Stand-in for `backend.base.helpers.Session` that dispatches on the
    JSON-RPC `method` in the posted body the way a real NZBGet instance
    would, without any network access."""

    def __init__(self, responses: Dict[str, Any]) -> None:
        # method -> FakeResponse, or a list of FakeResponse (consumed in
        # order, last one repeats) for methods called more than once.
        self.responses = responses
        self.calls: List[Dict[str, Any]] = []

    def post(self, url: str, json: Dict[str, Any], auth=None) -> FakeResponse:
        self.calls.append({'url': url, 'body': json, 'auth': auth})
        method = json['method']
        entry = self.responses[method]
        if isinstance(entry, list):
            return entry.pop(0) if len(entry) > 1 else entry[0]
        return entry


def _patched_session(fake_session: FakeSession):
    return patch.object(NZBGet_module, 'Session', return_value=fake_session)


class nzbget_connection_test(unittest.TestCase):
    def test_test_succeeds_on_valid_version_response(self):
        fake = FakeSession({'version': FakeResponse({'result': '21.1'})})
        with _patched_session(fake):
            # Should not raise
            NZBGet.test('http://localhost:6789', 'nzbget', 'tegbzn6789')

        self.assertEqual(fake.calls[0]['url'], 'http://localhost:6789/jsonrpc')
        self.assertEqual(fake.calls[0]['auth'], ('nzbget', 'tegbzn6789'))

    def test_test_raises_credential_invalid_without_credentials(self):
        with self.assertRaises(CredentialInvalid):
            NZBGet.test('http://localhost:6789')

        with self.assertRaises(CredentialInvalid):
            NZBGet.test('http://localhost:6789', 'nzbget')

    def test_test_raises_credential_invalid_on_401(self):
        fake = FakeSession({'version': FakeResponse({}, status_code=401)})
        with _patched_session(fake):
            with self.assertRaises(CredentialInvalid):
                NZBGet.test('http://localhost:6789', 'nzbget', 'wrong')

    def test_test_raises_credential_invalid_on_unauthorized_rpc_error(self):
        # Some builds answer an unauthorised call with 200 + a JSON-RPC
        # error body rather than a 401.
        fake = FakeSession({
            'version': FakeResponse({
                'error': {'code': 2, 'message': 'Unauthorized'}
            })
        })
        with _patched_session(fake):
            with self.assertRaises(CredentialInvalid):
                NZBGet.test('http://localhost:6789', 'nzbget', 'wrong')

    def test_test_raises_client_not_working_on_connection_error(self):
        class RaisingSession(FakeSession):
            def post(self, url, json, auth=None):
                raise RequestsConnectionError()

        with patch.object(
            NZBGet_module, 'Session', return_value=RaisingSession({})
        ):
            with self.assertRaises(ClientNotWorking):
                NZBGet.test('http://localhost:6789', 'nzbget', 'tegbzn6789')

    def test_test_raises_client_not_working_on_non_json_response(self):
        class NonJSONResponse(FakeResponse):
            def json(self):
                raise ValueError('not json')

        fake = FakeSession({'version': NonJSONResponse('<html>')})
        with _patched_session(fake):
            with self.assertRaises(ClientNotWorking):
                NZBGet.test('http://localhost:6789', 'nzbget', 'tegbzn6789')

    def test_test_raises_client_not_working_on_generic_rpc_error(self):
        fake = FakeSession({
            'version': FakeResponse({
                'error': {'code': 2, 'message': 'Invalid method'}
            })
        })
        with _patched_session(fake):
            with self.assertRaises(ClientNotWorking):
                NZBGet.test('http://localhost:6789', 'nzbget', 'tegbzn6789')


class nzbget_client_instance(unittest.TestCase):
    """Exercises add_download/get_download/delete_download against an
    already-constructed NZBGet instance, bypassing BaseExternalClient's
    DB-backed __init__ (covered separately for other clients'
    constructors; not the concern of this file)."""

    @staticmethod
    def _make_client() -> NZBGet:
        client = NZBGet.__new__(NZBGet)
        client._id = 1
        client._title = 'My NZBGet'
        client._base_url = 'http://localhost:6789'
        client._username = 'nzbget'
        client._password = 'tegbzn6789'
        client._api_token = None
        client.ssn = None
        client.known_ids = set()
        return client

    def test_add_download_returns_nzb_id_as_string(self):
        client = self._make_client()
        fake = FakeSession({'append': FakeResponse({'result': 42})})
        with _patched_session(fake):
            download_id = client.add_download(
                'http://indexer.example/get/1.nzb',
                '/downloads/kapowarr',
                'Batman 001'
            )

        self.assertEqual(download_id, '42')
        self.assertIn('42', client.known_ids)

        params = fake.calls[0]['body']['params']
        self.assertEqual(params[0], 'Batman 001')
        self.assertEqual(params[1], 'http://indexer.example/get/1.nzb')
        # Always the fixed Kapowarr category -- NZBGet, like SABnzbd, has
        # no per-job arbitrary target-folder parameter.
        self.assertEqual(params[2], 'kapowarr')

    def test_add_download_raises_when_client_refuses_the_job(self):
        for refusal in (0, -1):
            with self.subTest(refusal=refusal):
                client = self._make_client()
                fake = FakeSession({'append': FakeResponse({'result': refusal})})
                with _patched_session(fake):
                    with self.assertRaises(ClientNotWorking):
                        client.add_download(
                            'http://indexer.example/get/1.nzb', '/x', None
                        )

    def test_add_download_rejects_pre_v13_boolean_result(self):
        # NZBGet before v13 answered `append` with a success/failure
        # boolean and no NZBID. `int(True)` is 1, so without an explicit
        # bool check the job would be tracked under whatever real NZBID
        # 1 happens to be.
        for result in (True, False):
            with self.subTest(result=result):
                client = self._make_client()
                fake = FakeSession({'append': FakeResponse({'result': result})})
                with _patched_session(fake):
                    with self.assertRaises(ClientNotWorking):
                        client.add_download(
                            'http://indexer.example/get/1.nzb', '/x', None
                        )

                self.assertEqual(client.known_ids, set())

    def test_add_download_raises_on_non_numeric_result(self):
        client = self._make_client()
        fake = FakeSession({'append': FakeResponse({'result': 'nope'})})
        with _patched_session(fake):
            with self.assertRaises(ClientNotWorking):
                client.add_download(
                    'http://indexer.example/get/1.nzb', '/x', None
                )

    def test_get_download_maps_queue_states(self):
        cases = (
            ('DOWNLOADING', DownloadState.DOWNLOADING_STATE),
            ('PAUSED', DownloadState.PAUSED_STATE),
            ('QUEUED', DownloadState.QUEUED_STATE),
            ('FETCHING', DownloadState.QUEUED_STATE),
            # NZBGet keeps post-processing jobs in the *queue*, not the
            # history -- the files aren't importable yet, same reading as
            # SABnzbd's in-progress history statuses.
            ('UNPACKING', DownloadState.DOWNLOADING_STATE),
            ('REPAIRING', DownloadState.DOWNLOADING_STATE),
            ('MOVING', DownloadState.DOWNLOADING_STATE),
            ('PP_QUEUED', DownloadState.DOWNLOADING_STATE),
        )
        for status, expected_state in cases:
            with self.subTest(status=status):
                # Fresh client per case: get_download() caches self.ssn
                # once set, so a shared client across cases would keep
                # reusing the first case's patched session.
                client = self._make_client()
                client.known_ids.add('42')
                fake = FakeSession({
                    'listgroups': FakeResponse({'result': [{
                        'NZBID': 42,
                        'Status': status,
                        'FileSizeLo': 100 * 1024 * 1024,
                        'FileSizeHi': 0,
                        'RemainingSizeLo': 25 * 1024 * 1024,
                        'RemainingSizeHi': 0,
                        'DownloadRate': 524288
                    }]})
                })
                with _patched_session(fake):
                    result = client.get_download('42')

                self.assertEqual(result['state'], expected_state)
                self.assertEqual(result['progress'], 75.0)
                self.assertEqual(result['speed'], 512 * 1024)
                self.assertEqual(result['size'], 100 * 1024 * 1024)
                self.assertIsNone(result['storage'])

    def test_get_download_combines_split_64_bit_sizes(self):
        # NZBGet splits byte counts into 32-bit Hi/Lo halves; a >4GiB job
        # is only reported correctly if both halves are recombined.
        client = self._make_client()
        client.known_ids.add('42')
        fake = FakeSession({
            'listgroups': FakeResponse({'result': [{
                'NZBID': 42,
                'Status': 'DOWNLOADING',
                'FileSizeHi': 1,
                'FileSizeLo': 0,
                'RemainingSizeHi': 0,
                'RemainingSizeLo': 0
            }]})
        })
        with _patched_session(fake):
            result = client.get_download('42')

        self.assertEqual(result['size'], 2 ** 32)
        self.assertEqual(result['progress'], 100.0)

    def test_get_download_falls_back_to_megabyte_field(self):
        client = self._make_client()
        client.known_ids.add('42')
        fake = FakeSession({
            'listgroups': FakeResponse({'result': [{
                'NZBID': 42,
                'Status': 'DOWNLOADING',
                'FileSizeMB': 100,
                'RemainingSizeMB': 25
            }]})
        })
        with _patched_session(fake):
            result = client.get_download('42')

        self.assertEqual(result['size'], 100 * 1024 * 1024)
        self.assertEqual(result['progress'], 75.0)

    def test_get_download_reports_zero_speed_without_per_group_rate(self):
        # Older NZBGet builds only report a global download rate, which
        # would be wrong to attribute to one group of several.
        client = self._make_client()
        client.known_ids.add('42')
        fake = FakeSession({
            'listgroups': FakeResponse({'result': [{
                'NZBID': 42,
                'Status': 'DOWNLOADING',
                'FileSizeMB': 100,
                'RemainingSizeMB': 25
            }]})
        })
        with _patched_session(fake):
            result = client.get_download('42')

        self.assertEqual(result['speed'], 0)

    def test_get_download_completed_history_reports_importing_and_storage(self):
        for status in ('SUCCESS/ALL', 'SUCCESS/UNPACK', 'SUCCESS/PAR'):
            with self.subTest(status=status):
                client = self._make_client()
                client.known_ids.add('42')
                fake = FakeSession({
                    'listgroups': FakeResponse({'result': []}),
                    'history': FakeResponse({'result': [{
                        'NZBID': 42,
                        'Status': status,
                        'FileSizeLo': 104857600,
                        'FileSizeHi': 0,
                        'DestDir': '/downloads/kapowarr/Batman 001'
                    }]})
                })
                with _patched_session(fake):
                    result = client.get_download('42')

                self.assertEqual(result['state'], DownloadState.IMPORTING_STATE)
                self.assertEqual(result['progress'], 100.0)
                self.assertEqual(
                    result['storage'], '/downloads/kapowarr/Batman 001'
                )

    def test_get_download_warning_history_still_imports(self):
        # WARNING/* means NZBGet finished and wrote the files but flagged
        # something about them; Kapowarr's own import validates what it
        # actually finds, so the folder is still handed over.
        client = self._make_client()
        client.known_ids.add('42')
        fake = FakeSession({
            'listgroups': FakeResponse({'result': []}),
            'history': FakeResponse({'result': [{
                'NZBID': 42,
                'Status': 'WARNING/HEALTH',
                'FileSizeLo': 104857600,
                'FileSizeHi': 0,
                'DestDir': '/downloads/kapowarr/Batman 001'
            }]})
        })
        with _patched_session(fake):
            result = client.get_download('42')

        self.assertEqual(result['state'], DownloadState.IMPORTING_STATE)
        self.assertEqual(result['storage'], '/downloads/kapowarr/Batman 001')

    def test_get_download_failed_history(self):
        for status in ('FAILURE/PAR', 'FAILURE/UNPACK', 'FAILURE/HEALTH'):
            with self.subTest(status=status):
                client = self._make_client()
                client.known_ids.add('42')
                fake = FakeSession({
                    'listgroups': FakeResponse({'result': []}),
                    'history': FakeResponse({'result': [{
                        'NZBID': 42,
                        'Status': status,
                        'FileSizeLo': 0,
                        'FileSizeHi': 0
                    }]})
                })
                with _patched_session(fake):
                    result = client.get_download('42')

                self.assertEqual(result['state'], DownloadState.FAILED_STATE)
                self.assertIsNone(result['storage'])

    def test_get_download_deleted_history_reads_as_removed_externally(self):
        # `None` is what NZBDownload.update_status() turns into
        # CANCELED_STATE -- the same outcome SABnzbd produces by simply
        # vanishing from both lists.
        for status in ('DELETED/MANUAL', 'DELETED/DUPE'):
            with self.subTest(status=status):
                client = self._make_client()
                client.known_ids.add('42')
                fake = FakeSession({
                    'listgroups': FakeResponse({'result': []}),
                    'history': FakeResponse({'result': [{
                        'NZBID': 42,
                        'Status': status
                    }]})
                })
                with _patched_session(fake):
                    result = client.get_download('42')

                self.assertIsNone(result)

    def test_get_download_unrecognised_history_status_fails_rather_than_polls(self):
        client = self._make_client()
        client.known_ids.add('42')
        fake = FakeSession({
            'listgroups': FakeResponse({'result': []}),
            'history': FakeResponse({'result': [{
                'NZBID': 42,
                'Status': 'SOMETHING/NEW'
            }]})
        })
        with _patched_session(fake):
            result = client.get_download('42')

        self.assertEqual(result['state'], DownloadState.FAILED_STATE)

    def test_get_download_returns_none_when_known_id_disappears(self):
        client = self._make_client()
        client.known_ids.add('42')
        fake = FakeSession({
            'listgroups': FakeResponse({'result': []}),
            'history': FakeResponse({'result': []})
        })
        with _patched_session(fake):
            result = client.get_download('42')

        self.assertIsNone(result)

    def test_get_download_returns_empty_dict_when_never_known(self):
        client = self._make_client()
        fake = FakeSession({
            'listgroups': FakeResponse({'result': []}),
            'history': FakeResponse({'result': []})
        })
        with _patched_session(fake):
            result = client.get_download('99')

        self.assertEqual(result, {})

    def test_get_download_learns_ids_it_did_not_submit(self):
        # A client instance is rebuilt per request from the DB, so the
        # instance polling a job is usually not the one that submitted
        # it. Seeing the job once has to be enough for a later
        # disappearance to read as "removed externally" rather than
        # "never existed".
        client = self._make_client()
        fake = FakeSession({
            'listgroups': [
                FakeResponse({'result': [{
                    'NZBID': 42,
                    'Status': 'DOWNLOADING',
                    'FileSizeMB': 100,
                    'RemainingSizeMB': 25
                }]}),
                FakeResponse({'result': []})
            ],
            'history': FakeResponse({'result': []})
        })
        with _patched_session(fake):
            first = client.get_download('42')
            second = client.get_download('42')

        self.assertEqual(first['state'], DownloadState.DOWNLOADING_STATE)
        self.assertIsNone(second)

    # Same defensiveness kapowarr/t-024 added to the SABnzbd client: a
    # present-but-`null` payload, or a non-mapping entry, must not raise
    # an unhandled AttributeError/TypeError and kill the download's
    # polling thread. Not known to come from a real NZBGet instance, but
    # a proxy in front of one plausibly could, and silent thread death is
    # severe enough to defend against cheaply.
    def test_get_download_tolerates_null_result(self):
        client = self._make_client()
        client.known_ids.add('42')
        fake = FakeSession({
            'listgroups': FakeResponse({'result': None}),
            'history': FakeResponse({'result': None})
        })
        with _patched_session(fake):
            result = client.get_download('42')

        self.assertIsNone(result)

    def test_get_download_skips_non_dict_entries(self):
        client = self._make_client()
        client.known_ids.add('42')
        fake = FakeSession({
            'listgroups': FakeResponse({'result': ['not-a-dict']}),
            'history': FakeResponse({'result': [{
                'NZBID': 42,
                'Status': 'SUCCESS/ALL',
                'FileSizeLo': 104857600,
                'FileSizeHi': 0,
                'DestDir': '/downloads/kapowarr/Batman 001'
            }]})
        })
        with _patched_session(fake):
            result = client.get_download('42')

        self.assertEqual(result['state'], DownloadState.IMPORTING_STATE)

    def test_get_download_raises_client_not_working_on_missing_result_key(self):
        client = self._make_client()
        fake = FakeSession({'listgroups': FakeResponse({'version': '1.1'})})
        with _patched_session(fake):
            with self.assertRaises(ClientNotWorking):
                client.get_download('42')

    def test_delete_download_hits_both_queue_and_history_and_forgets_id(self):
        client = self._make_client()
        client.known_ids.add('42')
        fake = FakeSession({'editqueue': FakeResponse({'result': True})})
        with _patched_session(fake):
            client.delete_download('42', delete_files=True)

        commands = [c['body']['params'][0] for c in fake.calls]
        self.assertEqual(commands, ['GroupFinalDelete', 'HistoryFinalDelete'])
        self.assertNotIn('42', client.known_ids)
        for call in fake.calls:
            params = call['body']['params']
            # editqueue(Command, Offset, EditText, IDs); Offset is
            # deprecated-but-required since NZBGet 13 and must be 0, and
            # IDs are integers, not the string form Kapowarr stores.
            self.assertEqual(params[1], 0)
            self.assertEqual(params[2], '')
            self.assertEqual(params[3], [42])

    def test_delete_download_refuses_non_numeric_id(self):
        client = self._make_client()
        fake = FakeSession({'editqueue': FakeResponse({'result': True})})
        with _patched_session(fake):
            with self.assertRaises(ClientNotWorking):
                client.delete_download('not-an-id', delete_files=True)

        self.assertEqual(fake.calls, [])


if __name__ == '__main__':
    unittest.main()
