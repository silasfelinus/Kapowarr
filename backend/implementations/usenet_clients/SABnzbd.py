# -*- coding: utf-8 -*-

"""
SABnzbd external client -- Usenet peer to the torrent_clients package.

SABnzbd's HTTP API (`<base_url>/api`, `mode=...`) is intentionally simpler
than qBittorrent/Transmission's: a job lives in exactly one of two places,
the active queue (`mode=queue`) or the history (`mode=history`), and it
moves from the former to the latter once SABnzbd itself has finished
downloading *and* its own post-processing (unpack/par2 repair/move) --
there is no single "get info" call that covers both, unlike
`qBittorrent`'s `/torrents/info` or `Transmission`'s `torrent-get`.

One real API constraint, not present in the torrent clients this file is
modeled on: SABnzbd has no per-job "download to this absolute path"
parameter -- `target_folder` (freely settable per download for
qBittorrent/Transmission) can only be honored by pointing this client at a
SABnzbd *category* whose folder has been configured, in SABnzbd itself, to
match Kapowarr's `target_folder`. `add_download()` always submits jobs
under the `Constants.USENET_CATEGORY` category name; that category's
folder must be created/mapped in SABnzbd's own Config > Categories before
use -- this is a real operational precondition, not a bug, and is worth
surfacing in setup docs (see kapowarr/t-011).

Field names used against SABnzbd's JSON API (`mb`/`mbleft`/`percentage`/
`kbpersec`/`status`/`nzo_id` in `mode=queue`; `nzo_id`/`status`/`storage`/
`bytes`/`fail_message` in `mode=history`) come from SABnzbd's long-stable
public API and are exercised here against representative fixtures in
`tests/Tbackend/usenet_sabnzbd_client.py` -- they have not been verified
against a live SABnzbd instance (none was reachable in this sandbox).
"""

from threading import Lock
from time import monotonic
from typing import Any, Dict, List, Tuple, Union

from requests.exceptions import RequestException

from backend.base.custom_exceptions import ClientNotWorking, CredentialInvalid
from backend.base.definitions import (BrokenClientReason, Constants,
                                      DownloadState, DownloadType)
from backend.base.helpers import Session
from backend.base.logging import LOGGER
from backend.implementations.external_clients import BaseExternalClient

# How long one look at SABnzbd's queue stands in for the next.
#
# SABnzbd has no per-job status call: `mode=queue` and `mode=history` each
# return everything, and asking about one download means fetching the lot
# and finding it. Every download had its own thread doing that on its own
# five-second timer, so the cost of watching N downloads was N full dumps
# of the same list.
#
# On 2026-09-03 that was around 470 usenet downloads pointed at one
# SABnzbd -- roughly ninety requests a second, every one of them asking
# for the same thing. SABnzbd stopped answering within the thirty-second
# read timeout, `requests` retried each of them four times, and the log
# filled with `ReadTimeoutError` from threads 2844 through 3312. The
# instance looked broken; it was being asked the same question ninety
# times a second.
#
# Two seconds against a five-second poll keeps a progress bar honest while
# making the request count a property of the interval rather than of how
# many comics are downloading.
SNAPSHOT_TTL = 2.0

_SNAPSHOTS: Dict[Tuple[str, str], Tuple[float, Any]] = {}
_SNAPSHOT_LOCKS: Dict[Tuple[str, str], Lock] = {}
_SNAPSHOT_GUARD = Lock()


def forget_snapshots() -> None:
    """Drop every remembered look at a SABnzbd instance.

    `ExternalClients.get_client` builds a fresh client object per call, so
    the snapshots cannot live on the instance and are process-wide. That
    makes them outlive a test, and a stale queue answering the next one is
    a confusing way to find out -- eleven existing tests failed that way
    when this was added.
    """
    with _SNAPSHOT_GUARD:
        _SNAPSHOTS.clear()
        _SNAPSHOT_LOCKS.clear()
    return


# States a job can carry while still in SABnzbd's active queue (i.e. before
# SABnzbd has finished fetching + assembling the raw articles).
QUEUE_STATE_MAPPING = {
    'Paused': DownloadState.PAUSED_STATE,
    'Queued': DownloadState.QUEUED_STATE,
    'Grabbing': DownloadState.QUEUED_STATE,
    'Propagating': DownloadState.DOWNLOADING_STATE,
    'Downloading': DownloadState.DOWNLOADING_STATE,
    'Checking': DownloadState.DOWNLOADING_STATE,
}

# States a job can carry once SABnzbd has moved it to history. Everything
# short of 'Completed'/'Failed' is SABnzbd's own post-processing (unpack,
# par2 repair, move to the category folder) still running -- the files
# aren't in their final form/location for Kapowarr to pick up yet, so this
# maps to DOWNLOADING_STATE, same as an in-progress queue entry.
HISTORY_IN_PROGRESS_STATUSES = (
    'Queued', 'Downloading', 'Extracting', 'Verifying', 'Repairing',
    'Moving', 'Running',
)


class SABnzbd(BaseExternalClient):
    client_type = 'SABnzbd'
    download_type = DownloadType.USENET

    required_tokens = ('title', 'base_url', 'api_token')

    def __init__(self, client_id: int) -> None:
        super().__init__(client_id)

        self.ssn: Union[Session, None] = None
        # nzo_ids submitted or otherwise seen by get_download(), so a job
        # that later disappears from both the queue and history can be
        # told apart from one that was simply never found (see
        # get_download()'s trailing branches). Mirrors the role
        # `torrent_hashes` plays for the torrent clients.
        self.known_ids: set = set()
        return

    @classmethod
    def _api_request(
        cls,
        ssn: Session,
        base_url: str,
        api_token: Union[str, None],
        mode: str,
        params: Union[Dict[str, Any], None] = None,
        tolerate_nothing_matched: bool = False
    ) -> Dict[str, Any]:
        """Make a request against SABnzbd's `<base_url>/api` endpoint.

        Args:
            ssn (Session): The session to make the request with.
            base_url (str): Base URL of the SABnzbd instance.
            api_token (Union[str, None]): SABnzbd API key.
            mode (str): The `mode` query parameter (e.g. `'queue'`).
            params (Union[Dict[str, Any], None], optional): Extra query
                parameters. Defaults to None.

            tolerate_nothing_matched (bool, optional): Treat a bare
                `status: false` -- no error text with it -- as "nothing here
                matched", rather than as the client failing. For deletes,
                where nothing matching is the outcome being asked for.
                Defaults to False.

        Raises:
            ClientNotWorking: Can't connect to client, or client returned
                an unexpected (non-JSON) response.
            CredentialInvalid: The API key was rejected.

        Returns:
            Dict[str, Any]: The parsed JSON response.
        """
        query = {
            'mode': mode,
            'output': 'json',
            'apikey': api_token or '',
            **(params or {})
        }

        try:
            response = ssn.get(f'{base_url}/api', params=query)

        except RequestException:
            LOGGER.exception("Can't connect to SABnzbd instance: ")
            raise ClientNotWorking(BrokenClientReason.CONNECTION_ERROR)

        if response.status_code == 401:
            LOGGER.error(
                f"Failed to authenticate for SABnzbd instance: {response.text}"
            )
            raise CredentialInvalid

        if not response.ok:
            LOGGER.error(
                f"Not connected to SABnzbd instance: {response.text}"
            )
            raise ClientNotWorking(BrokenClientReason.NOT_CLIENT_INSTANCE)

        try:
            result: Dict[str, Any] = response.json()

        except ValueError:
            LOGGER.error(
                f"Unexpected response from SABnzbd instance: {response.text}"
            )
            raise ClientNotWorking(BrokenClientReason.FAILED_PROCESSING_RESPONSE)

        if result.get('status') is False:
            # SABnzbd's own error shape: {"status": false, "error": "..."}
            error = str(result.get('error', '')).lower()

            if tolerate_nothing_matched and not error:
                # `{"status": false, "nzo_ids": []}` -- status false, nothing
                # said. That is SABnzbd's answer when the id names nothing in
                # the section asked, which for a delete is the state wanted
                # rather than a failure. Every completed download produced two
                # of these, sixteen in one run on 2026-09-02, each logged as
                # an error with a traceback behind it. Real faults were in
                # there too and had nothing to stand out against.
                LOGGER.debug(
                    "SABnzbd had nothing matching in %s; nothing to do",
                    mode
                )
                return result

            if 'api key' in error or 'apikey' in error:
                LOGGER.error(
                    f"Failed to authenticate for SABnzbd instance: {result}"
                )
                raise CredentialInvalid
            LOGGER.error(f"SABnzbd instance returned an error: {result}")
            raise ClientNotWorking(BrokenClientReason.FAILED_PROCESSING_RESPONSE)

        return result

    def add_download(
        self,
        download_link: str,
        target_folder: str,
        download_name: Union[str, None]
    ) -> str:
        if not self.ssn:
            self.ssn = Session()

        params: Dict[str, Any] = {
            'name': download_link,
            'cat': Constants.USENET_CATEGORY,
        }
        if download_name is not None:
            params['nzbname'] = download_name

        result = self._api_request(
            self.ssn, self.base_url, self.api_token,
            mode='addurl',
            params=params
        )

        nzo_ids: List[str] = result.get('nzo_ids') or []
        if not nzo_ids:
            raise ClientNotWorking(BrokenClientReason.FAILED_PROCESSING_RESPONSE)

        nzo_id = nzo_ids[0]
        self.known_ids.add(nzo_id)
        return nzo_id

    def _slots(self, mode: str) -> List[Any]:
        """Every job SABnzbd currently has under `mode`, shared.

        `mode=queue` and `mode=history` each return the whole list; there
        is no per-job call. One snapshot therefore answers every download
        waiting on it, and the lock makes concurrent askers wait for the
        one request rather than each making their own.

        Args:
            mode (str): `'queue'` or `'history'`.

        Returns:
            List[Any]: The slots SABnzbd reported.
        """
        key = (self.base_url, mode)
        with _SNAPSHOT_GUARD:
            lock = _SNAPSHOT_LOCKS.setdefault(key, Lock())

        with lock:
            cached = _SNAPSHOTS.get(key)
            if cached is not None and monotonic() - cached[0] < SNAPSHOT_TTL:
                if isinstance(cached[1], Exception):
                    raise cached[1]
                return cached[1]

            if not self.ssn:
                self.ssn = Session()

            try:
                result = self._api_request(
                    self.ssn, self.base_url, self.api_token, mode=mode
                )
            except Exception as error:
                # Shared too. Without this, an instance that is down turns
                # every waiting download into its own doomed request, one
                # after another, each waiting out the full read timeout.
                _SNAPSHOTS[key] = (monotonic(), error)
                raise

            # `.get(mode, {})` alone doesn't defend against a present-but-
            # `null` key (the default only applies when the key is
            # *absent*), and a slot itself could in principle not be a
            # mapping -- both are unlikely from a real SABnzbd instance but
            # not worth an unhandled AttributeError killing the polling
            # loop over.
            slots = (result.get(mode) or {}).get('slots') or []
            _SNAPSHOTS[key] = (monotonic(), slots)
            return slots

    def _find_in(
        self,
        mode: str,
        download_id: str
    ) -> Union[Dict[str, Any], None]:
        for slot in self._slots(mode):
            if isinstance(slot, dict) and slot.get('nzo_id') == download_id:
                return slot
        return None

    def _find_in_queue(
        self,
        download_id: str
    ) -> Union[Dict[str, Any], None]:
        return self._find_in('queue', download_id)

    def _find_in_history(
        self,
        download_id: str
    ) -> Union[Dict[str, Any], None]:
        return self._find_in('history', download_id)

    def get_download(self, download_id: str) -> Union[dict, None]:
        queue_slot = self._find_in_queue(download_id)
        if queue_slot is not None:
            mb_total = float(queue_slot.get('mb') or 0)
            mb_left = float(queue_slot.get('mbleft') or 0)
            size = round(mb_total * 1024 * 1024)
            progress = 100.0
            if mb_total > 0:
                progress = round((mb_total - mb_left) / mb_total * 100, 2)

            try:
                speed = round(float(queue_slot.get('kbpersec') or 0) * 1024)
            except (TypeError, ValueError):
                speed = 0

            state = QUEUE_STATE_MAPPING.get(
                queue_slot.get('status', ''),
                DownloadState.DOWNLOADING_STATE
            )

            return {
                'size': size,
                'progress': progress,
                'speed': speed,
                'state': state,
                # Not applicable until the job is actually in SABnzbd's
                # history -- see the 'storage' key below.
                'storage': None
            }

        history_slot = self._find_in_history(download_id)
        if history_slot is not None:
            status = history_slot.get('status', '')
            size = round(float(history_slot.get('bytes') or 0))
            speed = 0
            progress = 100.0 if status not in HISTORY_IN_PROGRESS_STATUSES else 0.0
            storage = None

            if status == 'Failed':
                state = DownloadState.FAILED_STATE
                LOGGER.warning(
                    'SABnzbd download %s failed: %s',
                    download_id, history_slot.get('fail_message')
                )

            elif status == 'Completed':
                state = DownloadState.IMPORTING_STATE
                # The final folder SABnzbd stored the completed download
                # in -- only known once history reports 'Completed'.
                # `NZBDownload.update_status()` reads this to set its
                # `files` before handing off to Kapowarr's own import.
                storage = history_slot.get('storage')

            else:
                state = DownloadState.DOWNLOADING_STATE

            return {
                'size': size,
                'progress': progress,
                'speed': speed,
                'state': state,
                'storage': storage
            }

        if download_id in self.known_ids:
            # It was known (submitted or seen before) but is now in neither
            # the queue nor history -- removed externally.
            return None

        return {}

    def delete_download(self, download_id: str, delete_files: bool) -> None:
        if not self.ssn:
            self.ssn = Session()

        params = {
            'name': 'delete',
            'value': download_id,
            'del_files': 1 if delete_files else 0
        }
        # A job can be in either the queue or history at deletion time, so
        # both are asked. The section that does not have it answers
        # `{"status": false, "nzo_ids": []}` -- not silence, as this once
        # assumed, but a bare false that the shared request handler read as
        # the client having failed. At least one of every pair said that, and
        # a job SABnzbd has already purged makes it two.
        for section in ('queue', 'history'):
            self._api_request(
                self.ssn, self.base_url, self.api_token,
                mode=section, params=params,
                tolerate_nothing_matched=True
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
        if not api_token:
            raise CredentialInvalid

        SABnzbd._api_request(
            Session(), base_url, api_token,
            mode='version'
        )
        return
