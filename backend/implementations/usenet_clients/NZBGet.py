# -*- coding: utf-8 -*-

"""
NZBGet external client -- second Usenet peer alongside `SABnzbd.py`.

Where SABnzbd exposes a query-string API (`<base_url>/api?mode=...`),
NZBGet speaks JSON-RPC over a single endpoint (`<base_url>/jsonrpc`,
`{"method": ..., "params": [...]}`) authenticated with HTTP Basic using
NZBGet's `ControlUsername`/`ControlPassword` -- hence
`required_tokens` carries `username`/`password` here and `api_token`
there.

The two clients agree on the shape that matters to
`NZBDownload.update_status()`: a job lives in exactly one of two places,
the download queue (`listgroups`) or the history (`history`), and it
moves from the former to the latter once NZBGet has finished both
downloading and its own post-processing (par2 repair/unpack/move). The
queue therefore also carries NZBGet's post-processing states
(`UNPACKING`, `REPAIRING`, ...), which map to `DOWNLOADING_STATE` for the
same reason SABnzbd's in-progress *history* statuses do: the files are
not in their final form/location for Kapowarr to import yet.

The `target_folder` constraint SABnzbd's module docstring describes
applies here verbatim: NZBGet has no per-job "download to this absolute
path" parameter either, so `add_download()` submits under the
`Constants.USENET_CATEGORY` category and that category's `DestDir` must
be configured in NZBGet's own Settings > Categories to match Kapowarr's
download folder.

Field names used against NZBGet's JSON-RPC API (`NZBID`/`Status`/
`FileSizeLo`/`FileSizeHi`/`RemainingSizeLo`/`RemainingSizeHi`/
`DownloadRate` in `listgroups`; `NZBID`/`Status`/`DestDir`/`FileSizeLo`/
`FileSizeHi` in `history`; the four-argument `editqueue(Command, Offset,
EditText, IDs)` form) come from NZBGet's long-stable public API -- the
same calls and argument shapes the other *arr applications use -- and are
exercised here against representative fixtures in
`tests/Tbackend/usenet_nzbget_client.py`. Like SABnzbd's, they have not
been verified against a live NZBGet instance (none was reachable in this
sandbox); kapowarr/t-024's live-verification gate covers both.
"""

from typing import Any, Dict, List, Union

from requests.exceptions import RequestException

from backend.base.custom_exceptions import ClientNotWorking, CredentialInvalid
from backend.base.definitions import (BrokenClientReason, Constants,
                                      DownloadState, DownloadType)
from backend.base.helpers import Session
from backend.base.logging import LOGGER
from backend.implementations.external_clients import BaseExternalClient

# NZBGet's per-group `Status` while the job is still in the download
# queue. Everything from `PP_QUEUED` down is NZBGet's own
# post-processing (par2 repair, unpack, rename, move) -- the files are
# not yet in their final form/location, so those map to
# DOWNLOADING_STATE just like SABnzbd's in-progress history statuses.
GROUP_STATE_MAPPING = {
    'QUEUED': DownloadState.QUEUED_STATE,
    'PAUSED': DownloadState.PAUSED_STATE,
    'FETCHING': DownloadState.QUEUED_STATE,
    'DOWNLOADING': DownloadState.DOWNLOADING_STATE,
    'PP_QUEUED': DownloadState.DOWNLOADING_STATE,
    'LOADING_PARS': DownloadState.DOWNLOADING_STATE,
    'VERIFYING_SOURCES': DownloadState.DOWNLOADING_STATE,
    'REPAIRING': DownloadState.DOWNLOADING_STATE,
    'VERIFYING_REPAIRED': DownloadState.DOWNLOADING_STATE,
    'RENAMING': DownloadState.DOWNLOADING_STATE,
    'UNPACKING': DownloadState.DOWNLOADING_STATE,
    'MOVING': DownloadState.DOWNLOADING_STATE,
    'EXECUTING_SCRIPT': DownloadState.DOWNLOADING_STATE,
    'POST_QUEUED': DownloadState.DOWNLOADING_STATE,
    'DELETING': DownloadState.DOWNLOADING_STATE,
}

# A history item's `Status` is a `CATEGORY/DETAIL` pair (e.g.
# 'SUCCESS/ALL', 'FAILURE/PAR', 'DELETED/MANUAL'); only the category
# before the slash decides the outcome.
HISTORY_SUCCESS_CATEGORY = 'SUCCESS'
HISTORY_WARNING_CATEGORY = 'WARNING'
HISTORY_FAILURE_CATEGORY = 'FAILURE'
HISTORY_DELETED_CATEGORY = 'DELETED'


def _combine_size(
    slot: Dict[str, Any],
    prefix: str
) -> int:
    """Reassemble one of NZBGet's split 64-bit byte counts.

    NZBGet reports sizes as a `<prefix>Hi`/`<prefix>Lo` pair of 32-bit
    halves (plus a lossy `<prefix>MB` convenience field) because its
    JSON-RPC API predates reliable 64-bit integer handling in its
    clients. The exact byte count is the pair; `<prefix>MB` is only used
    as a fallback if neither half is present.

    Args:
        slot (Dict[str, Any]): The `listgroups`/`history` entry.
        prefix (str): The field prefix, e.g. `'FileSize'`.

    Returns:
        int: The size in bytes.
    """
    low = slot.get(f'{prefix}Lo')
    high = slot.get(f'{prefix}Hi')
    if low is not None or high is not None:
        try:
            return int(high or 0) * (2 ** 32) + int(low or 0)
        except (TypeError, ValueError):
            return 0

    try:
        return round(float(slot.get(f'{prefix}MB') or 0) * 1024 * 1024)
    except (TypeError, ValueError):
        return 0


class NZBGet(BaseExternalClient):
    client_type = 'NZBGet'
    download_type = DownloadType.USENET

    required_tokens = ('title', 'base_url', 'username', 'password')

    def __init__(self, client_id: int) -> None:
        super().__init__(client_id)

        self.ssn: Union[Session, None] = None
        # NZBIDs submitted or otherwise seen by get_download(), so a job
        # that later disappears from both the queue and history can be
        # told apart from one that was simply never found (see
        # get_download()'s trailing branches). Mirrors `SABnzbd`'s
        # `known_ids` and the torrent clients' `torrent_hashes`.
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
        """Make a JSON-RPC call against NZBGet's `<base_url>/jsonrpc`.

        Args:
            ssn (Session): The session to make the request with.
            base_url (str): Base URL of the NZBGet instance.
            username (Union[str, None]): NZBGet's `ControlUsername`.
            password (Union[str, None]): NZBGet's `ControlPassword`.
            method (str): The JSON-RPC method (e.g. `'listgroups'`).
            params (Union[List[Any], None], optional): Positional
                arguments for the method. Defaults to None.

        Raises:
            ClientNotWorking: Can't connect to client, or client returned
                an unexpected (non-JSON) response.
            CredentialInvalid: The credentials were rejected.

        Returns:
            Any: The JSON-RPC `result` value, whose type depends on the
            method (a list for `listgroups`/`history`, a string for
            `version`, an int for `append`, a bool for `editqueue`).
        """
        body = {
            'version': '1.1',
            'id': 1,
            'method': method,
            'params': params or []
        }

        try:
            response = ssn.post(
                f'{base_url}/jsonrpc',
                json=body,
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
            LOGGER.error(
                f"Not connected to NZBGet instance: {response.text}"
            )
            raise ClientNotWorking(BrokenClientReason.NOT_CLIENT_INSTANCE)

        try:
            result: Dict[str, Any] = response.json()

        except ValueError:
            LOGGER.error(
                f"Unexpected response from NZBGet instance: {response.text}"
            )
            raise ClientNotWorking(BrokenClientReason.FAILED_PROCESSING_RESPONSE)

        if not isinstance(result, dict):
            LOGGER.error(
                f"Unexpected response from NZBGet instance: {response.text}"
            )
            raise ClientNotWorking(BrokenClientReason.FAILED_PROCESSING_RESPONSE)

        error = result.get('error')
        if error:
            # NZBGet's own error shape:
            # {"error": {"name": ..., "code": ..., "message": ...}}.
            # Some builds answer an unauthorised call with a 200 + this
            # body instead of a 401, so the credential case is
            # recognised here too rather than only above.
            message = str(
                error.get('message', error)
                if isinstance(error, dict) else error
            ).lower()
            if 'unauthorized' in message or 'access denied' in message:
                LOGGER.error(
                    f"Failed to authenticate for NZBGet instance: {result}"
                )
                raise CredentialInvalid
            LOGGER.error(f"NZBGet instance returned an error: {result}")
            raise ClientNotWorking(BrokenClientReason.FAILED_PROCESSING_RESPONSE)

        if 'result' not in result:
            LOGGER.error(
                f"Unexpected response from NZBGet instance: {response.text}"
            )
            raise ClientNotWorking(BrokenClientReason.FAILED_PROCESSING_RESPONSE)

        return result['result']

    def add_download(
        self,
        download_link: str,
        target_folder: str,
        download_name: Union[str, None]
    ) -> str:
        if not self.ssn:
            self.ssn = Session()

        # append(NZBFilename, Content, Category, Priority, AddToTop,
        #        AddPaused, DupeKey, DupeScore, DupeMode).
        # `Content` may be a URL instead of base64 NZB data, in which
        # case NZBGet fetches the NZB itself -- the equivalent of
        # SABnzbd's `mode=addurl`, and the reason no NZB body is ever
        # downloaded on Kapowarr's side (see `indexers.py`).
        result = self._api_request(
            self.ssn, self.base_url, self.username, self.password,
            method='append',
            params=[
                download_name or '',
                download_link,
                Constants.USENET_CATEGORY,
                0,      # Priority: normal
                False,  # AddToTop
                False,  # AddPaused
                '',     # DupeKey
                0,      # DupeScore
                'SCORE' # DupeMode
            ]
        )

        # NZBGet returns the new NZBID, or 0/-1 when it refused the job.
        if isinstance(result, bool):
            # NZBGet before v13 answered `append` with a plain
            # success/failure boolean and had no NZBID to return, so
            # there is nothing to poll or delete later. Caught
            # explicitly because `int(True)` is 1 -- without this the
            # job would silently be tracked under whatever real NZBID 1
            # happens to be.
            LOGGER.error(
                'NZBGet returned a boolean from append() -- this NZBGet is '
                'too old to report the job id Kapowarr needs to track it'
            )
            raise ClientNotWorking(BrokenClientReason.VERSION_NOT_SUPPORTED)

        try:
            nzb_id = int(result)
        except (TypeError, ValueError):
            raise ClientNotWorking(BrokenClientReason.FAILED_PROCESSING_RESPONSE)

        if nzb_id <= 0:
            raise ClientNotWorking(BrokenClientReason.FAILED_PROCESSING_RESPONSE)

        download_id = str(nzb_id)
        self.known_ids.add(download_id)
        return download_id

    def _find_in(
        self,
        method: str,
        params: Union[List[Any], None],
        download_id: str
    ) -> Union[Dict[str, Any], None]:
        """Find the entry for `download_id` in a `listgroups`/`history`
        response.

        Args:
            method (str): The JSON-RPC method to call.
            params (Union[List[Any], None]): Its positional arguments.
            download_id (str): The NZBID to look for, as a string.

        Returns:
            Union[Dict[str, Any], None]: The matching entry, or `None`.
        """
        if not self.ssn:
            self.ssn = Session()

        result = self._api_request(
            self.ssn, self.base_url, self.username, self.password,
            method=method, params=params
        )
        # A present-but-`null` result, or a non-mapping entry, is
        # unlikely from a real NZBGet instance but not worth an
        # unhandled TypeError killing the polling loop over -- same
        # defensiveness as `SABnzbd._find_in_queue()`.
        for slot in (result or []):
            if (
                isinstance(slot, dict)
                and str(slot.get('NZBID')) == download_id
            ):
                return slot
        return None

    def get_download(self, download_id: str) -> Union[dict, None]:
        group = self._find_in('listgroups', [0], download_id)
        if group is not None:
            self.known_ids.add(download_id)

            size = _combine_size(group, 'FileSize')
            remaining = _combine_size(group, 'RemainingSize')
            progress = 100.0
            if size > 0:
                progress = round((size - remaining) / size * 100, 2)

            try:
                # Per-group `DownloadRate` (bytes/s). Absent on NZBGet
                # versions that only report a global rate, in which case
                # a job with no reported speed is the honest answer --
                # the global figure would be wrong the moment more than
                # one group is active.
                speed = round(float(group.get('DownloadRate') or 0))
            except (TypeError, ValueError):
                speed = 0

            state = GROUP_STATE_MAPPING.get(
                group.get('Status', ''),
                DownloadState.DOWNLOADING_STATE
            )

            return {
                'size': size,
                'progress': progress,
                'speed': speed,
                'state': state,
                # Not applicable until the job reaches history -- see
                # the 'DestDir' key below.
                'storage': None
            }

        history = self._find_in('history', [False], download_id)
        if history is not None:
            self.known_ids.add(download_id)

            status = str(history.get('Status', ''))
            category = status.split('/', 1)[0]
            size = _combine_size(history, 'FileSize')
            speed = 0
            progress = 100.0
            storage = None

            if category == HISTORY_DELETED_CATEGORY:
                # The job was deleted at NZBGet's end (manually, or as a
                # duplicate). SABnzbd's equivalent simply vanishes from
                # both lists; either way it means "removed externally",
                # which `NZBDownload.update_status()` reads as a
                # canceled download.
                LOGGER.warning(
                    'NZBGet download %s was deleted at the client: %s',
                    download_id, status
                )
                return None

            if category == HISTORY_FAILURE_CATEGORY:
                state = DownloadState.FAILED_STATE

            elif category in (
                HISTORY_SUCCESS_CATEGORY, HISTORY_WARNING_CATEGORY
            ):
                # WARNING/* means NZBGet finished and wrote the files but
                # flagged something about them (health below par, a
                # post-processing script's exit code). The files exist in
                # DestDir either way, so it is handed to Kapowarr's own
                # import -- which validates what it actually finds --
                # rather than failed outright here.
                if category == HISTORY_WARNING_CATEGORY:
                    LOGGER.warning(
                        'NZBGet download %s completed with a warning: %s',
                        download_id, status
                    )
                state = DownloadState.IMPORTING_STATE
                # The final folder NZBGet stored the completed download
                # in -- only known once it reaches history.
                # `NZBDownload.update_status()` reads this to set its
                # `files` before handing off to Kapowarr's own import.
                storage = history.get('DestDir')

            else:
                # An unrecognised category. NZBGet only moves a job to
                # history once it is finished with it, so treating this
                # as still-in-progress would poll forever; failing is the
                # safe reading, and the status is logged so an unknown
                # value is diagnosable rather than silent.
                LOGGER.warning(
                    'Unrecognised NZBGet history status for download %s: %s',
                    download_id, status
                )
                state = DownloadState.FAILED_STATE

            return {
                'size': size,
                'progress': progress,
                'speed': speed,
                'state': state,
                'storage': storage
            }

        if download_id in self.known_ids:
            # It was known (submitted or seen before) but is now in
            # neither the queue nor history -- removed externally.
            return None

        return {}

    def delete_download(self, download_id: str, delete_files: bool) -> None:
        if not self.ssn:
            self.ssn = Session()

        # editqueue(Command, Offset, EditText, IDs). `Offset` has been
        # deprecated-but-required since NZBGet 13; it must be 0.
        #
        # A job can be in either the queue or the history at deletion
        # time, and NZBGet answers a command aimed at the section that
        # doesn't hold it with `result: false` rather than an error --
        # so, as in `SABnzbd.delete_download()`, issuing both is safe and
        # avoids having to look the job up again first.
        #
        # NZBGet has no per-item "also delete the downloaded files" flag
        # comparable to SABnzbd's `del_files`: deleting a queue group
        # discards that group's partially-downloaded intermediate files
        # as a side effect, but a job that already completed has its
        # files in `DestDir`, and those stay on disk either way.
        # `delete_files` is therefore honored on a best-effort basis;
        # once a job reaches history its folder belongs to Kapowarr's own
        # post-processing.
        try:
            nzb_id = int(download_id)
        except (TypeError, ValueError):
            LOGGER.error(
                'Refusing to delete non-numeric NZBGet download id: %s',
                download_id
            )
            raise ClientNotWorking(BrokenClientReason.FAILED_PROCESSING_RESPONSE)

        for command in ('GroupFinalDelete', 'HistoryFinalDelete'):
            self._api_request(
                self.ssn, self.base_url, self.username, self.password,
                method='editqueue',
                params=[command, 0, '', [nzb_id]]
            )

        self.known_ids.discard(download_id)
        return

    @staticmethod
    def test(
        base_url: str,
        username: Union[str, None] = None,
        password: Union[str, None] = None,
        api_token: Union[str, None] = None
    ) -> None:
        if not username or not password:
            raise CredentialInvalid

        NZBGet._api_request(
            Session(), base_url, username, password,
            method='version'
        )
        return
