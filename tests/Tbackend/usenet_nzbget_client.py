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

    def test_authenticates_with_a_username_and_password_not_an_api_key(self):
        self.assertIn('username', NZBGet.required_tokens)
        self.assertIn('password', NZBGet.required_tokens)
        self.assertNotIn('api_token', NZBGet.required_tokens)


class FakeResponse:
    def __init__(self, json_body: Any, status_code: int = 200) -> None:
        self._json_body = json_body
        self.status_code = status_code
        self.ok = 200 <= status_code < 400
        self.text = str(json_body)

    def json(self) -> Any:
        if isinstance(self._json_body, ValueError):
            raise self._json_body
        return self._json_body


class FakeSession:
    """Stand-in for `backend.base.helpers.Session` that dispatches on the
    JSON-RPC `method` the way a real NZBGet instance would, without any
    network access."""

    def __init__(self, responses: Dict[str, Any]) -> None:
        # method -> FakeResponse, or a list of FakeResponse (consumed in
        # order, last one repeats) for methods called more than once.
        self.responses = responses
        self.calls: List[Dict[str, Any]] = []

    def post(self, url: str, json: Dict[str, Any], auth=None) -> FakeResponse:
        self.calls.append({'url': url, 'body': json, 'auth': auth})
        entry = self.responses[json['method']]
        if isinstance(entry, list):
            return entry.pop(0) if len(entry) > 1 else entry[0]
        return entry


def _ok(result: Any) -> FakeResponse:
    "NZBGet's JSON-RPC 1.1 success envelope. Note: no `jsonrpc` member."
    return FakeResponse({'version': '1.1', 'id': 1, 'result': result})


def _patched_session(fake_session: FakeSession):
    return patch.object(NZBGet_module, 'Session', return_value=fake_session)


class nzbget_connection_test(unittest.TestCase):
    def test_test_succeeds_on_a_version_string(self):
        fake = FakeSession({'version': _ok('24.5')})
        with _patched_session(fake):
            # Should not raise
            NZBGet.test('http://localhost:6789', 'nzbget', 'tegbzn6789')

    def test_test_posts_to_the_jsonrpc_endpoint_with_basic_auth(self):
        fake = FakeSession({'version': _ok('24.5')})
        with _patched_session(fake):
            NZBGet.test('http://localhost:6789', 'user', 'pass')

        self.assertEqual(fake.calls[0]['url'], 'http://localhost:6789/jsonrpc')
        self.assertEqual(fake.calls[0]['auth'], ('user', 'pass'))
        self.assertEqual(fake.calls[0]['body']['method'], 'version')

    def test_an_instance_without_credentials_still_connects(self):
        """NZBGet can be configured with no control username/password at all,
        so missing credentials are not rejected up front the way SABnzbd's
        mandatory API key is -- only an actual 401 is."""
        fake = FakeSession({'version': _ok('24.5')})
        with _patched_session(fake):
            NZBGet.test('http://localhost:6789')

        self.assertEqual(fake.calls[0]['auth'], ('', ''))

    def test_test_raises_credential_invalid_on_401(self):
        fake = FakeSession({'version': FakeResponse({}, status_code=401)})
        with _patched_session(fake):
            with self.assertRaises(CredentialInvalid):
                NZBGet.test('http://localhost:6789', 'user', 'wrong')

    def test_test_raises_credential_invalid_on_403(self):
        fake = FakeSession({'version': FakeResponse({}, status_code=403)})
        with _patched_session(fake):
            with self.assertRaises(CredentialInvalid):
                NZBGet.test('http://localhost:6789', 'user', 'wrong')

    def test_test_raises_client_not_working_on_connection_error(self):
        class RaisingSession(FakeSession):
            def post(self, url, json, auth=None):
                raise RequestsConnectionError()

        with patch.object(
            NZBGet_module, 'Session', return_value=RaisingSession({})
        ):
            with self.assertRaises(ClientNotWorking):
                NZBGet.test('http://localhost:6789', 'user', 'pass')

    def test_test_raises_client_not_working_on_non_json_response(self):
        fake = FakeSession({'version': FakeResponse(ValueError())})
        with _patched_session(fake):
            with self.assertRaises(ClientNotWorking):
                NZBGet.test('http://localhost:6789', 'user', 'pass')

    def test_test_raises_client_not_working_on_a_jsonrpc_error(self):
        fake = FakeSession({'version': FakeResponse({
            'version': '1.1', 'id': 1,
            'error': {'name': 'JSONRPCError', 'code': 2, 'message': 'nope'}
        })})
        with _patched_session(fake):
            with self.assertRaises(ClientNotWorking):
                NZBGet.test('http://localhost:6789', 'user', 'pass')

    def test_something_that_speaks_json_but_is_not_nzbget_is_rejected(self):
        """`version` returns a plain string. A struct means whatever answered
        on this URL is some other service."""
        fake = FakeSession({'version': _ok({'version': '24.5'})})
        with _patched_session(fake):
            with self.assertRaises(ClientNotWorking):
                NZBGet.test('http://localhost:6789', 'user', 'pass')


class nzbget_client_instance(unittest.TestCase):
    """Exercises add_download/get_download/delete_download against an
    already-constructed NZBGet instance, bypassing BaseExternalClient's
    DB-backed __init__ (not the concern of this file)."""

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

    # region add_download

    def test_add_download_returns_the_nzbid_as_a_string(self):
        client = self._make_client()
        fake = FakeSession({'append': _ok(42)})
        with _patched_session(fake):
            nzb_id = client.add_download(
                'http://indexer.example/get/1.nzb',
                '/downloads/kapowarr',
                'Batman 001'
            )

        self.assertEqual(nzb_id, '42')
        self.assertIn('42', client.known_ids)

    def test_add_download_sends_appends_nine_positional_params_in_order(self):
        """NZBGet takes positional params only, and rejects the call outright
        if AddPaused is missing -- which in turn makes DupeKey/DupeScore/
        DupeMode mandatory. All nine must always be sent."""
        client = self._make_client()
        fake = FakeSession({'append': _ok(42)})
        with _patched_session(fake):
            client.add_download(
                'http://indexer.example/get/1.nzb', '/x', 'Batman 001'
            )

        params = fake.calls[0]['body']['params']
        self.assertEqual(params, [
            'Batman 001',
            'http://indexer.example/get/1.nzb',
            'kapowarr',
            0, False, False, '', 0, 'score'
        ])

    def test_add_download_submits_under_the_fixed_kapowarr_category(self):
        """NZBGet has no per-job target-folder param, so the category is the
        only way to steer where files land."""
        client = self._make_client()
        fake = FakeSession({'append': _ok(42)})
        with _patched_session(fake):
            client.add_download('http://indexer.example/1.nzb', '/x', None)

        self.assertEqual(fake.calls[0]['body']['params'][2], 'kapowarr')

    def test_add_download_sends_an_empty_filename_when_none_is_known(self):
        client = self._make_client()
        fake = FakeSession({'append': _ok(42)})
        with _patched_session(fake):
            client.add_download('http://indexer.example/1.nzb', '/x', None)

        self.assertEqual(fake.calls[0]['body']['params'][0], '')

    def test_add_download_raises_on_a_zero_or_negative_nzbid(self):
        """NZBGet reports append failures as 0 or a negative number rather
        than a JSON-RPC error."""
        for result in (0, -1):
            with self.subTest(result=result):
                client = self._make_client()
                fake = FakeSession({'append': _ok(result)})
                with _patched_session(fake):
                    with self.assertRaises(ClientNotWorking):
                        client.add_download('http://x/1.nzb', '/x', None)

    def test_add_download_raises_on_a_boolean_result(self):
        """The archive-content branch of `append` returns a bool instead of
        an NZBID. `True` must not be mistaken for a usable id."""
        client = self._make_client()
        fake = FakeSession({'append': _ok(True)})
        with _patched_session(fake):
            with self.assertRaises(ClientNotWorking):
                client.add_download('http://x/1.nzb', '/x', None)

    # region get_download -- queue

    def test_get_download_maps_queue_states(self):
        cases = (
            ('DOWNLOADING', DownloadState.DOWNLOADING_STATE),
            ('PAUSED', DownloadState.PAUSED_STATE),
            ('QUEUED', DownloadState.QUEUED_STATE),
            ('FETCHING', DownloadState.QUEUED_STATE),
            ('QS_QUEUED', DownloadState.QUEUED_STATE),
            ('QS_EXECUTING', DownloadState.DOWNLOADING_STATE),
        )
        for status, expected in cases:
            with self.subTest(status=status):
                client = self._make_client()
                fake = FakeSession({
                    'listgroups': _ok([{
                        'NZBID': 42, 'Status': status,
                        'FileSizeLo': 100 * 1024 * 1024, 'FileSizeHi': 0,
                        'RemainingSizeLo': 25 * 1024 * 1024,
                        'RemainingSizeHi': 0
                    }]),
                    'status': _ok({'DownloadRateLo': 512 * 1024,
                                   'DownloadRateHi': 0})
                })
                with _patched_session(fake):
                    result = client.get_download('42')

                self.assertEqual(result['state'], expected)
                self.assertEqual(result['progress'], 75.0)
                self.assertEqual(result['size'], 100 * 1024 * 1024)
                self.assertIsNone(result['storage'])

    def test_a_post_processing_stage_still_reads_as_downloading(self):
        """In NZBGet, unlike SABnzbd, post-processing happens while the job is
        still in the queue. The files aren't in their final form yet, so every
        such stage -- and any status a newer NZBGet adds -- is DOWNLOADING."""
        for status in ('PP_QUEUED', 'REPAIRING', 'UNPACKING', 'MOVING',
                       'EXECUTING_SCRIPT', 'PP_FINISHED',
                       'SOME_FUTURE_STAGE'):
            with self.subTest(status=status):
                client = self._make_client()
                fake = FakeSession({
                    'listgroups': _ok([{'NZBID': 42, 'Status': status}]),
                    'status': _ok({'DownloadRateLo': 0, 'DownloadRateHi': 0})
                })
                with _patched_session(fake):
                    result = client.get_download('42')

                self.assertEqual(
                    result['state'], DownloadState.DOWNLOADING_STATE
                )

    def test_speed_comes_from_the_global_rate_only_while_downloading(self):
        """NZBGet emits no per-group rate at all, so an actively downloading
        group borrows the instance-wide figure; a paused or queued one must
        not claim it."""
        client = self._make_client()
        fake = FakeSession({
            'listgroups': _ok([{'NZBID': 42, 'Status': 'DOWNLOADING'}]),
            'status': _ok({'DownloadRateLo': 512 * 1024, 'DownloadRateHi': 0})
        })
        with _patched_session(fake):
            self.assertEqual(client.get_download('42')['speed'], 512 * 1024)

        client = self._make_client()
        fake = FakeSession({
            'listgroups': _ok([{'NZBID': 42, 'Status': 'PAUSED'}]),
            'status': _ok({'DownloadRateLo': 512 * 1024, 'DownloadRateHi': 0})
        })
        with _patched_session(fake):
            self.assertEqual(client.get_download('42')['speed'], 0)
            # The status call is skipped entirely when it can't be used.
            self.assertNotIn(
                'status', [c['body']['method'] for c in fake.calls]
            )

    def test_a_failing_status_call_does_not_break_the_poll(self):
        """Speed is cosmetic; progress and state are not. A broken `status`
        must not take the whole status update down with it."""
        client = self._make_client()
        fake = FakeSession({
            'listgroups': _ok([{
                'NZBID': 42, 'Status': 'DOWNLOADING',
                'FileSizeLo': 100, 'RemainingSizeLo': 50
            }]),
            'status': FakeResponse({}, status_code=500)
        })
        with _patched_session(fake):
            result = client.get_download('42')

        self.assertEqual(result['speed'], 0)
        self.assertEqual(result['progress'], 50.0)
        self.assertEqual(result['state'], DownloadState.DOWNLOADING_STATE)

    def test_sizes_are_reassembled_from_the_split_64_bit_halves(self):
        """NZBGet splits 64-bit byte counts into unsigned 32-bit Lo/Hi. A
        download over 4GiB is wrong by exactly 2**32 if Hi is ignored."""
        client = self._make_client()
        fake = FakeSession({
            'listgroups': _ok([{
                'NZBID': 42, 'Status': 'PAUSED',
                'FileSizeLo': 1024, 'FileSizeHi': 2,
                'RemainingSizeLo': 0, 'RemainingSizeHi': 0
            }])
        })
        with _patched_session(fake):
            result = client.get_download('42')

        self.assertEqual(result['size'], (2 << 32) + 1024)

    def test_size_falls_back_to_the_megabyte_field(self):
        client = self._make_client()
        fake = FakeSession({
            'listgroups': _ok([{
                'NZBID': 42, 'Status': 'PAUSED', 'FileSizeMB': 100
            }])
        })
        with _patched_session(fake):
            result = client.get_download('42')

        self.assertEqual(result['size'], 100 * 1024 * 1024)

    # region get_download -- history

    def test_a_successful_history_item_reports_importing_and_its_folder(self):
        for status in ('SUCCESS/ALL', 'SUCCESS/UNPACK', 'SUCCESS/PAR',
                       'SUCCESS/HEALTH', 'SUCCESS/GOOD', 'SUCCESS/MARK'):
            with self.subTest(status=status):
                client = self._make_client()
                fake = FakeSession({
                    'listgroups': _ok([]),
                    'history': _ok([{
                        'NZBID': 42, 'Status': status,
                        'FileSizeLo': 104857600,
                        'DestDir': '/downloads/kapowarr/Batman 001'
                    }])
                })
                with _patched_session(fake):
                    result = client.get_download('42')

                self.assertEqual(result['state'], DownloadState.IMPORTING_STATE)
                self.assertEqual(result['progress'], 100.0)
                self.assertEqual(
                    result['storage'], '/downloads/kapowarr/Batman 001'
                )

    def test_finaldir_wins_over_destdir_when_post_processing_moved_it(self):
        client = self._make_client()
        fake = FakeSession({
            'listgroups': _ok([]),
            'history': _ok([{
                'NZBID': 42, 'Status': 'SUCCESS/ALL',
                'DestDir': '/intermediate/Batman 001',
                'FinalDir': '/downloads/kapowarr/Batman 001'
            }])
        })
        with _patched_session(fake):
            result = client.get_download('42')

        self.assertEqual(result['storage'], '/downloads/kapowarr/Batman 001')

    def test_a_script_warning_still_leaves_importable_files(self):
        client = self._make_client()
        fake = FakeSession({
            'listgroups': _ok([]),
            'history': _ok([{
                'NZBID': 42, 'Status': 'WARNING/SCRIPT',
                'DestDir': '/downloads/kapowarr/Batman 001'
            }])
        })
        with _patched_session(fake):
            result = client.get_download('42')

        self.assertEqual(result['state'], DownloadState.IMPORTING_STATE)

    def test_other_warnings_are_failures_not_importable(self):
        """WARNING/PASSWORD, /DAMAGED and friends mean the archive was never
        unpacked into a usable form. Handing those to the importer would
        import a broken or encrypted file as if it were the comic."""
        for status in ('WARNING/DAMAGED', 'WARNING/REPAIRABLE',
                       'WARNING/HEALTH', 'WARNING/SPACE', 'WARNING/PASSWORD',
                       'WARNING/SKIPPED'):
            with self.subTest(status=status):
                client = self._make_client()
                fake = FakeSession({
                    'listgroups': _ok([]),
                    'history': _ok([{'NZBID': 42, 'Status': status}])
                })
                with _patched_session(fake):
                    with self.assertLogs(level='WARNING'):
                        result = client.get_download('42')

                self.assertEqual(result['state'], DownloadState.FAILED_STATE)
                self.assertIsNone(result['storage'])

    def test_every_failure_prefix_is_a_failure(self):
        for status in ('FAILURE/PAR', 'FAILURE/UNPACK', 'FAILURE/MOVE',
                       'FAILURE/SCAN', 'FAILURE/BAD', 'FAILURE/HEALTH',
                       'FAILURE/FETCH', 'FAILURE/INTERNAL_ERROR'):
            with self.subTest(status=status):
                client = self._make_client()
                fake = FakeSession({
                    'listgroups': _ok([]),
                    'history': _ok([{'NZBID': 42, 'Status': status}])
                })
                with _patched_session(fake):
                    with self.assertLogs(level='WARNING'):
                        result = client.get_download('42')

                self.assertEqual(result['state'], DownloadState.FAILED_STATE)

    def test_a_deleted_history_item_reads_as_gone(self):
        """Deleted inside NZBGet while Kapowarr was waiting. None is what
        NZBDownload turns into CANCELED_STATE."""
        for status in ('DELETED/MANUAL', 'DELETED/DUPE', 'DELETED/COPY',
                       'DELETED/GOOD'):
            with self.subTest(status=status):
                client = self._make_client()
                fake = FakeSession({
                    'listgroups': _ok([]),
                    'history': _ok([{'NZBID': 42, 'Status': status}])
                })
                with _patched_session(fake):
                    self.assertIsNone(client.get_download('42'))

    def test_an_unrecognised_terminal_status_fails_rather_than_hangs(self):
        """History is terminal in NZBGet, so an unknown status will never
        resolve on a later poll. Failing stops the poll; it also refuses to
        hand files of unknown quality to the importer."""
        client = self._make_client()
        fake = FakeSession({
            'listgroups': _ok([]),
            'history': _ok([{'NZBID': 42, 'Status': 'SOMETHING/NEW'}])
        })
        with _patched_session(fake):
            with self.assertLogs(level='WARNING'):
                result = client.get_download('42')

        self.assertEqual(result['state'], DownloadState.FAILED_STATE)

    def test_history_is_requested_without_hidden_duplicate_records(self):
        """Hidden Kind=DUP records use a much smaller struct with no DestDir
        or FinalDir, so asking for them only invites a lookup on a shape that
        can't answer."""
        client = self._make_client()
        fake = FakeSession({
            'listgroups': _ok([]),
            'history': _ok([])
        })
        with _patched_session(fake):
            client.get_download('42')

        history_call = [
            c for c in fake.calls if c['body']['method'] == 'history'
        ][0]
        self.assertEqual(history_call['body']['params'], [False])

    # region get_download -- lookup edge cases

    def test_listgroups_is_called_with_the_required_zero_argument(self):
        client = self._make_client()
        fake = FakeSession({'listgroups': _ok([]), 'history': _ok([])})
        with _patched_session(fake):
            client.get_download('42')

        self.assertEqual(fake.calls[0]['body']['params'], [0])

    def test_get_download_returns_none_when_a_known_id_disappears(self):
        client = self._make_client()
        client.known_ids.add('42')
        fake = FakeSession({'listgroups': _ok([]), 'history': _ok([])})
        with _patched_session(fake):
            self.assertIsNone(client.get_download('42'))

    def test_get_download_returns_empty_dict_when_never_known(self):
        client = self._make_client()
        fake = FakeSession({'listgroups': _ok([]), 'history': _ok([])})
        with _patched_session(fake):
            self.assertEqual(client.get_download('999'), {})

    def test_get_download_tolerates_a_null_result_list(self):
        """An unhardened iteration over a present-but-null list raises
        TypeError inside the polling thread and silently kills the download's
        status updates -- the failure mode kapowarr/t-024 hardened SABnzbd
        against."""
        client = self._make_client()
        client.known_ids.add('42')
        fake = FakeSession({'listgroups': _ok(None), 'history': _ok(None)})
        with _patched_session(fake):
            self.assertIsNone(client.get_download('42'))

    def test_get_download_skips_non_mapping_entries(self):
        client = self._make_client()
        client.known_ids.add('42')
        fake = FakeSession({
            'listgroups': _ok(['not-a-dict']),
            'history': _ok([{
                'NZBID': 42, 'Status': 'SUCCESS/ALL', 'DestDir': '/d'
            }])
        })
        with _patched_session(fake):
            result = client.get_download('42')

        self.assertEqual(result['state'], DownloadState.IMPORTING_STATE)

    def test_a_job_first_seen_in_the_client_becomes_known(self):
        """So that its later disappearance reads as 'removed' rather than
        'never existed'."""
        client = self._make_client()
        fake = FakeSession({
            'listgroups': _ok([{'NZBID': 42, 'Status': 'DOWNLOADING'}]),
            'status': _ok({'DownloadRateLo': 0})
        })
        with _patched_session(fake):
            client.get_download('42')

        self.assertIn('42', client.known_ids)

    # region delete_download

    def test_delete_download_finally_deletes_from_queue_and_history(self):
        """The Final variants are used so the record is always erased rather
        than kept as a hidden duplicate-tracking entry, whose presence would
        otherwise depend on the server's DupeCheck setting."""
        client = self._make_client()
        client.known_ids.add('42')
        fake = FakeSession({'editqueue': _ok(True)})
        with _patched_session(fake):
            client.delete_download('42', delete_files=True)

        commands = [c['body']['params'][0] for c in fake.calls]
        self.assertEqual(commands, ['GroupFinalDelete', 'HistoryFinalDelete'])
        self.assertNotIn('42', client.known_ids)

    def test_delete_download_uses_the_backward_compatible_editqueue_form(self):
        """(Command, Offset, Param, IDs) is the only form pre-v18 accepts,
        and newer builds accept it too -- their parser reads a second-position
        int and falls through when it is a string instead."""
        client = self._make_client()
        fake = FakeSession({'editqueue': _ok(True)})
        with _patched_session(fake):
            client.delete_download('42', delete_files=False)

        for call in fake.calls:
            self.assertEqual(call['body']['params'][1:], [0, '', [42]])

    def test_delete_download_sends_the_nzbid_as_an_int(self):
        """editqueue takes an array of integer NZBIDs; the string form
        Kapowarr carries internally is rejected."""
        client = self._make_client()
        fake = FakeSession({'editqueue': _ok(True)})
        with _patched_session(fake):
            client.delete_download('42', delete_files=True)

        self.assertEqual(fake.calls[0]['body']['params'][3], [42])

    def test_delete_download_ignores_an_unparseable_id(self):
        client = self._make_client()
        fake = FakeSession({'editqueue': _ok(True)})
        with _patched_session(fake):
            with self.assertLogs(level='ERROR'):
                client.delete_download('not-an-id', delete_files=True)

        self.assertEqual(fake.calls, [])


if __name__ == '__main__':
    unittest.main()
