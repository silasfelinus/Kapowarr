# -*- coding: utf-8 -*-

"""
NZBGet external client -- second Usenet peer beside `SABnzbd`.

Generalising the Usenet seam beyond a single downloader (kapowarr/t-035,
upstream-adjacent parity work) mostly means accepting that NZBGet's API is
shaped differently from SABnzbd's in four ways that matter here:

1. **JSON-RPC 1.1, not REST and not JSON-RPC 2.0.** Requests POST to
   `<base_url>/jsonrpc` with `{"method", "params", "id"}` and a *positional*
   params array -- named parameters are unsupported. Successful replies are
   `{"version": "1.1", "id": N, "result": ...}`; there is no `jsonrpc` member
   in the response, so nothing here validates one.

2. **A job stays in the queue through post-processing.** `listgroups` reports
   par-repair/unpack/move/script stages as ordinary group statuses, and an
   item only reaches `history` once it is genuinely finished. History is
   therefore terminal -- the opposite of SABnzbd, where post-processing
   happens *in* history and `HISTORY_IN_PROGRESS_STATUSES` exists to spot it.

3. **There is no per-group download rate.** Verified as a firm negative, not a
   documentation gap: `DownloadRate` appears only in the `status` command's
   response, never in `listgroups`. `get_download()` therefore reports the
   *global* rate for a group that is actively downloading and 0 otherwise --
   accurate with one active job, an over-estimate with several. Progress and
   size are exact regardless; only `speed` is affected, and it is display-only.

4. **64-bit numbers arrive split into unsigned 32-bit `Lo`/`Hi` halves.** The
   `...MB` fields are lossy rounding of the same value, so `_size_from()`
   prefers the halves and only falls back to MB.

Like SABnzbd, NZBGet has no per-job "download to this absolute path"
parameter, so `add_download()` submits under `Constants.USENET_CATEGORY` and
that category's folder must be pointed at Kapowarr's download folder in
NZBGet's own Settings > Categories. Same operational precondition, same
reason.

Field names, status vocabularies and the `append`/`editqueue` parameter orders
below were taken from NZBGet's published API docs cross-checked against its
implementation (`daemon/remote/XmlRpc.cpp`, `daemon/queue/DownloadInfo.cpp`,
`daemon/queue/HistoryCoordinator.cpp`). They are exercised here against
representative fixtures in `tests/Tbackend/usenet_nzbget_client.py` and have
**not** been verified against a live NZBGet instance -- none was reachable in
this sandbox. The same caveat `SABnzbd.py` carries applies.
"""

from typing import Any, Dict, List, Mapping, Union

from requests.exceptions import RequestException

from backend.base.custom_exceptions import ClientNotWorking, CredentialInvalid
from backend.base.definitions import (BrokenClientReason, Constants,
                                      DownloadState, DownloadType)
from backend.base.helpers import Session
from backend.base.logging import LOGGER
from backend.implementations.external_clients import BaseExternalClient

# Group statuses that mean something other than "NZBGet is working on it".
# Everything absent from this map -- the post-processing stages (PP_QUEUED,
# LOADING_PARS, VERIFYING_SOURCES, REPAIRING, VERIFYING_REPAIRED, RENAMING,
# UNPACKING, MOVING, POST_UNPACK_RENAMING, EXECUTING_SCRIPT, PP_FINISHED) and
# any status a newer NZBGet adds -- falls through to DOWNLOADING_STATE, which
# is correct for all of them: the files are not in their final form yet.
QUEUE_STATE_MAPPING = {
    'QUEUED': DownloadState.QUEUED_STATE,
    'PAUSED': DownloadState.PAUSED_STATE,
    # Still fetching the .nzb from the indexer URL; nothing downloading yet.
    'FETCHING': DownloadState.QUEUED_STATE,
    'DOWNLOADING': DownloadState.DOWNLOADING_STATE,
    # Queue-script stages. Undocumented but genuinely emitted, so they are
    # mapped rather than left to the default.
    'QS_QUEUED': DownloadState.QUEUED_STATE,
    'QS_EXECUTING': DownloadState.DOWNLOADING_STATE,
}

# History statuses are `PREFIX/SUFFIX`. The prefix is the reliable signal --
# NZBGet emits suffixes this file does not enumerate (and adds new ones
# between versions), so everything below keys on the prefix.
HISTORY_SUCCESS_PREFIX = 'SUCCESS/'
HISTORY_WARNING_PREFIX = 'WARNING/'
HISTORY_FAILURE_PREFIX = 'FAILURE/'
HISTORY_DELETED_PREFIX = 'DELETED/'

# The one WARNING outcome that still leaves usable files in the final folder:
# post-processing completed and only a user script complained. Every other
# WARNING (DAMAGED, REPAIRABLE, HEALTH, SPACE, PASSWORD, SKIPPED) means the
# download is not in a form Kapowarr can import, so it is treated as failed
# rather than handed to the importer.
HISTORY_IMPORTABLE_WARNINGS = ('WARNING/SCRIPT',)


class NZBGet(BaseExternalClient):
    client_type = 'NZBGet'
    download_type = DownloadType.USENET

    # NZBGet authenticates with a username/password pair (HTTP basic), not an
    # API key. An instance configured with no credentials at all still works:
    # empty strings are sent and never rejected, and `test()` only raises
    # CredentialInvalid on an actual 401.
    required_tokens = ('title', 'base_url', 'username', 'password')

    def __init__(self, client_id: int) -> None:
        super().__init__(client_id)

        self.ssn: Union[Session, None] = None
        # NZBIDs submitted or otherwise seen by get_download(), so a job that
        # later disappears from both the queue and history can be told apart
        # from one that was simply never found. Mirrors SABnzbd's known_ids
        # and the torrent clients' torrent_hashes.
        self.known_ids: set = set()
        return

    @classmethod
    def _api_request(
        cls,
        ssn: Session,
        base_url: str,
        username: Union[str, None],
        password: Union[str, None],
        method: str,
        params: Union[List[Any], None] = None
    ) -> Any:
        """Call one NZBGet JSON-RPC method.

        Args:
            ssn (Session): The session to make the request with.
            base_url (str): Base URL of the NZBGet instance.
            username (Union[str, None]): Control username, if any.
            password (Union[str, None]): Control password, if any.
            method (str): The RPC method name (e.g. `'listgroups'`).
            params (Union[List[Any], None], optional): Positional parameters,
                in the order the method expects. NZBGet does not support
                named parameters. Defaults to None.

        Raises:
            ClientNotWorking: Can't connect to client, or client returned an
                unexpected (non-JSON, or JSON-RPC error) response.
            CredentialInvalid: The credentials were rejected.

        Returns:
            Any: The `result` member of the reply, whose type depends on the
                method (a list for `listgroups`/`history`, an int for
                `append`, a bool for `editqueue`, a string for `version`).
        """
        try:
            response = ssn.post(
                f'{base_url}/jsonrpc',
                json={'id': 1, 'method': method, 'params': params or []},
                auth=(username or '', password or '')
            )

        except RequestException:
            LOGGER.exception("Can't connect to NZBGet instance: ")
            raise ClientNotWorking(BrokenClientReason.CONNECTION_ERROR)

        if response.status_code in (401, 403):
            LOGGER.error(
                f"Failed to authenticate for NZBGet instance: {response.text}"
            )
            raise CredentialInvalid

        if not response.ok:
            LOGGER.error(f"Not connected to NZBGet instance: {response.text}")
            raise ClientNotWorking(BrokenClientReason.NOT_CLIENT_INSTANCE)

        try:
            body = response.json()

        except ValueError:
            LOGGER.error(
                f"Unexpected response from NZBGet instance: {response.text}"
            )
            raise ClientNotWorking(BrokenClientReason.FAILED_PROCESSING_RESPONSE)

        if not isinstance(body, dict):
            LOGGER.error(f"Unexpected response from NZBGet instance: {body}")
            raise ClientNotWorking(BrokenClientReason.FAILED_PROCESSING_RESPONSE)

        if body.get('error'):
            # NZBGet's error codes are ad hoc and undocumented, so nothing
            # branches on them -- only the message is worth logging.
            LOGGER.error(f"NZBGet instance returned an error: {body['error']}")
            raise ClientNotWorking(BrokenClientReason.FAILED_PROCESSING_RESPONSE)

        if 'result' not in body:
            LOGGER.error(f"Unexpected response from NZBGet instance: {body}")
            raise ClientNotWorking(BrokenClientReason.FAILED_PROCESSING_RESPONSE)

        return body['result']

    @staticmethod
    def _size_from(entry: Mapping[str, Any], prefix: str) -> int:
        """Reassemble one of NZBGet's split 64-bit byte counts.

        NZBGet reports large numbers as unsigned 32-bit `<prefix>Lo` and
        `<prefix>Hi` halves, alongside a lossy `<prefix>MB`. The halves are
        preferred; MB is the fallback for a field or version that only sends
        that.

        Args:
            entry (Mapping[str, Any]): A group or history item.
            prefix (str): The field prefix, e.g. `'FileSize'`.

        Returns:
            int: The value in bytes, or 0 if neither form is usable.
        """
        low = entry.get(f'{prefix}Lo')
        high = entry.get(f'{prefix}Hi')
        if low is not None or high is not None:
            try:
                return (int(high or 0) << 32) + int(low or 0)
            except (TypeError, ValueError):
                pass

        try:
            return round(float(entry.get(f'{prefix}MB') or 0) * 1024 * 1024)
        except (TypeError, ValueError):
            return 0

    def _ensure_session(self) -> Session:
        if not self.ssn:
            self.ssn = Session()
        return self.ssn

    def _call(self, method: str, params: Union[List[Any], None] = None) -> Any:
        return self._api_request(
            self._ensure_session(), self.base_url,
            self.username, self.password,
            method, params
        )

    def add_download(
        self,
        download_link: str,
        target_folder: str,
        download_name: Union[str, None]
    ) -> str:
        # `append`'s positional parameters, in NZBGet's order:
        #   Filename, Content, Category, Priority, AddToTop, AddPaused,
        #   DupeKey, DupeScore, DupeMode[, AutoCategory, PPParameters]
        # AddPaused is not optional in practice -- omitting it makes NZBGet
        # reject the call outright -- and supplying it makes DupeKey/
        # DupeScore/DupeMode mandatory too, so all nine are always sent.
        # Content is treated as a URL rather than base64 NZB content purely
        # because it starts with http:// or https://.
        # Filename deliberately carries no extension: an archive extension
        # (.zip/.rar/...) routes NZBGet into a branch that returns a bool
        # instead of the NZBID this method has to return.
        result = self._call('append', [
            download_name or '',
            download_link,
            Constants.USENET_CATEGORY,
            0,      # Priority: normal
            False,  # AddToTop
            False,  # AddPaused
            '',     # DupeKey
            0,      # DupeScore
            'score'  # DupeMode
        ])

        # A positive int is the NZBID; 0 or negative is NZBGet's way of
        # reporting failure, and a bool would mean the archive branch above.
        if isinstance(result, bool) or not isinstance(result, int) or result <= 0:
            LOGGER.error(f"NZBGet did not accept the download: {result}")
            raise ClientNotWorking(BrokenClientReason.FAILED_PROCESSING_RESPONSE)

        nzb_id = str(result)
        self.known_ids.add(nzb_id)
        return nzb_id

    @staticmethod
    def _find_by_id(
        entries: Any,
        download_id: str
    ) -> Union[Dict[str, Any], None]:
        """Pick the entry with this NZBID out of a listgroups/history reply.

        Defensive in the same way `SABnzbd._find_in_queue()` is (kapowarr/
        t-024): a present-but-null list, or a non-mapping entry inside it,
        must not raise out of the polling thread and kill the download's
        status updates.
        """
        for entry in entries or []:
            if not isinstance(entry, dict):
                continue
            if str(entry.get('NZBID')) == download_id:
                return entry
        return None

    def _global_download_rate(self) -> int:
        """The instance-wide download rate in bytes/sec.

        NZBGet has no per-group rate, so this is the closest available
        figure. Failing to read it must never break a status poll -- speed is
        cosmetic, unlike progress and state -- so any error yields 0.
        """
        try:
            status = self._call('status')
        except ClientNotWorking:
            return 0

        if not isinstance(status, dict):
            return 0

        rate = self._size_from(status, 'DownloadRate')
        if rate:
            return rate

        # `DownloadRate` (deprecated in v24.2 in favour of the Lo/Hi pair,
        # but still emitted) is the fallback for a build that sends only it.
        try:
            return round(float(status.get('DownloadRate') or 0))
        except (TypeError, ValueError):
            return 0

    def get_download(self, download_id: str) -> Union[dict, None]:
        group = self._find_by_id(self._call('listgroups', [0]), download_id)
        if group is not None:
            self.known_ids.add(download_id)

            size = self._size_from(group, 'FileSize')
            remaining = self._size_from(group, 'RemainingSize')
            progress = 100.0
            if size > 0:
                progress = round((size - remaining) / size * 100, 2)

            state = QUEUE_STATE_MAPPING.get(
                group.get('Status', ''),
                DownloadState.DOWNLOADING_STATE
            )

            speed = 0
            if state == DownloadState.DOWNLOADING_STATE:
                speed = self._global_download_rate()

            return {
                'size': size,
                'progress': progress,
                'speed': speed,
                'state': state,
                # Not final until the job reaches history -- see below.
                'storage': None
            }

        # `history(False)` deliberately excludes hidden Kind=DUP records,
        # which use a much smaller struct with no DestDir/FinalDir at all.
        item = self._find_by_id(self._call('history', [False]), download_id)
        if item is not None:
            self.known_ids.add(download_id)

            status = str(item.get('Status') or '')
            size = self._size_from(item, 'FileSize')

            if status.startswith(HISTORY_DELETED_PREFIX):
                # Removed inside NZBGet while Kapowarr was waiting on it.
                # Same meaning as vanishing entirely: the job is not coming
                # back, and NZBDownload reads None as CANCELED_STATE.
                LOGGER.info(
                    'NZBGet download %s was deleted in the client: %s',
                    download_id, status
                )
                return None

            if (
                status.startswith(HISTORY_SUCCESS_PREFIX)
                or status in HISTORY_IMPORTABLE_WARNINGS
            ):
                state = DownloadState.IMPORTING_STATE
                # FinalDir is set when post-processing relocated the files;
                # DestDir is where they landed otherwise.
                storage = item.get('FinalDir') or item.get('DestDir') or None

            else:
                # FAILURE/*, every WARNING/* that isn't importable, and any
                # status a newer NZBGet adds. History is terminal in NZBGet
                # -- post-processing happens while the job is still in the
                # queue -- so an unrecognised status here will never resolve
                # on a later poll. Failing is the safe reading: it stops the
                # poll and never hands unusable files to the importer.
                state = DownloadState.FAILED_STATE
                storage = None
                LOGGER.warning(
                    'NZBGet download %s did not complete successfully: %s',
                    download_id, status
                )

            return {
                'size': size,
                'progress': 100.0,
                'speed': 0,
                'state': state,
                'storage': storage
            }

        if download_id in self.known_ids:
            # Known before, in neither the queue nor history now -- removed
            # externally.
            return None

        return {}

    def delete_download(self, download_id: str, delete_files: bool) -> None:
        # `delete_files` maps onto NZBGet's own conditional disk handling
        # rather than onto a flag, because NZBGet already does exactly what
        # each caller wants:
        #
        #  - delete_files=True is a cancelled or failed download.
        #    GroupFinalDelete cleans the partial files off disk, and
        #    HistoryFinalDelete deletes DestDir with all its content
        #    *because* the item failed.
        #  - delete_files=False is a successful download Kapowarr is about to
        #    import. HistoryFinalDelete leaves a successful item's DestDir
        #    untouched and only erases the record -- which is precisely the
        #    wanted behaviour.
        #
        # The `Final` variants are chosen over plain GroupDelete/
        # HistoryDelete so the record is always erased rather than kept as a
        # hidden duplicate-tracking entry, whose presence depends on the
        # server's DupeCheck setting.
        #
        # A job is in either the queue or history, never both, and editqueue
        # against the wrong one is a no-op returning False -- so both are
        # called rather than looking the job up again first.
        #
        # The four-parameter (Command, Offset, Param, IDs) form is used
        # because it is the only one pre-v18 accepts, and newer builds accept
        # it too: their parser reads a second-position int and falls through
        # when it is a string instead.
        try:
            nzb_id = int(download_id)
        except (TypeError, ValueError):
            LOGGER.error(f'Not a valid NZBGet NZBID: {download_id!r}')
            return

        self._call('editqueue', ['GroupFinalDelete', 0, '', [nzb_id]])
        self._call('editqueue', ['HistoryFinalDelete', 0, '', [nzb_id]])
        self.known_ids.discard(download_id)
        return

    @staticmethod
    def test(
        base_url: str,
        username: Union[str, None] = None,
        password: Union[str, None] = None,
        api_token: Union[str, None] = None
    ) -> None:
        result = NZBGet._api_request(
            Session(), base_url, username, password,
            method='version'
        )

        # `version` returns a plain string. Anything else means whatever
        # answered on this URL speaks JSON but is not an NZBGet instance.
        if not isinstance(result, str) or not result:
            LOGGER.error(f"Unexpected version from NZBGet instance: {result}")
            raise ClientNotWorking(BrokenClientReason.NOT_CLIENT_INSTANCE)
        return
