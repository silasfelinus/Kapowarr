# -*- coding: utf-8 -*-

from __future__ import annotations

from asyncio import gather, run
from os import listdir
from os.path import basename, join
from typing import TYPE_CHECKING, Any, Dict, Iterable, List, Tuple, Type, Union

from typing_extensions import assert_never

from backend.base.custom_exceptions import (ClientNotWorking,
                                            CredentialInvalid,
                                            DownloadLimitReached,
                                            DownloadNotFound,
                                            DownloadUnmovable,
                                            EnqueuingDownloadFailure,
                                            InvalidKeyValue, IssueNotFound,
                                            LinkBroken)
from backend.base.definitions import (BlocklistReason, Constants, Download,
                                      DownloadSource, DownloadState,
                                      EnqueuingDownloadFailureReason,
                                      ExternalDownload, SeedingHandling)
from backend.base.files import create_folder, delete_file_folder
from backend.base.helpers import (CommaList, Singleton, get_subclasses,
                                  redact_url)
from backend.base.logging import LOGGER
from backend.features.post_processing import (PostProcessor,
                                              PostProcessorTorrentsComplete,
                                              PostProcessorTorrentsCopy)
from backend.implementations.blocklist import add_to_blocklist
from backend.implementations.download_clients import (BaseDirectDownload,
                                                      MegaDownload,
                                                      NZBDownload,
                                                      TorrentDownload)
from backend.implementations.download_preppers import DownloadPreppers
from backend.implementations.external_clients import ExternalClients
# Compatibility patch points retained for existing callers/tests. Queue
# dispatch itself goes through DownloadPreppers rather than these classes.
from backend.implementations.getcomics import GetComicsPage
from backend.implementations.indexers import Indexers
from backend.implementations.volumes import Issue
from backend.internals.db import get_db, iter_commit
from backend.internals.server import (AddedToQueueEvent, QueueStatusEvent,
                                      RemovedFromQueueEvent, Server, WebSocket)
from backend.internals.settings import Settings

if TYPE_CHECKING:
    from threading import Thread


# =====================
# Download handling
# =====================
download_type_to_class: Dict[str, Type[Download]] = {
    c.identifier: c
    for c in get_subclasses(BaseDirectDownload)
}


class DownloadHandler(metaclass=Singleton):
    queue: List[Download] = []

    def __init__(self) -> None:
        """Setup the download handler"""
        self.settings = Settings()
        create_folder(self.settings.sv.download_folder)
        return

    # region Running Download
    def __run_download(self, download: Download) -> None:
        """Start a download. Intended to be run in a thread.

        Args:
            download (Download): The download to run.
                One of the entries in self.queue.
        """
        LOGGER.info(f'Starting download: {download.id}')

        ws = WebSocket()
        status_event = QueueStatusEvent(download)
        try:
            download.run()

        except DownloadLimitReached as e:
            download.stop(DownloadState.FAILED_STATE)
            if e.source == DownloadSource.MEGA:
                self._remove_mega(exclude_id=download.id)

        ws.emit(status_event)
        if download.state == DownloadState.SHUTDOWN_STATE:
            PostProcessor.shutdown(download)
            return

        elif download.state == DownloadState.CANCELED_STATE:
            PostProcessor.canceled(download)

        elif download.state == DownloadState.FAILED_STATE:
            PostProcessor.failed(download)

        elif download.state == DownloadState.DOWNLOADING_STATE:
            download.state = DownloadState.IMPORTING_STATE
            ws.emit(status_event)

            # While this download is post-processing, start the next one.
            self._process_queue()

            PostProcessor.success(download)

        self.queue.remove(download)
        ws.emit(RemovedFromQueueEvent(download))

        self._process_queue()
        return

    def __run_external_download(
        self,
        download: ExternalDownload,
        post_processer: Type[PostProcessor]
    ) -> None:
        """Poll an external (torrent or Usenet) download until it finishes.
        Intended to be run in a thread. `download.run()` has already been
        called by the caller-specific wrapper below, which also picks the
        right `post_processer` for the protocol.

        Args:
            download (ExternalDownload): The external download to run.
                One of the entries in self.queue.

            post_processer (Type[PostProcessor]): The post-processor to
                apply once the download reaches a terminal/importing state.
        """
        ws = WebSocket()
        status_event = QueueStatusEvent(download)

        # Only meaningful for a torrent with seeding_handling == 'copy'
        # (post_processer is PostProcessorTorrentsCopy in that case);
        # a download whose state never becomes SEEDING_STATE (e.g. Usenet,
        # which doesn't seed) never touches this branch.
        files_copied = False

        # `update_status()` is documented to raise `ClientNotWorking`/
        # `CredentialInvalid` (see `ExternalDownload.update_status()`), but
        # nothing here used to catch it -- an outage (client restart,
        # network blip) or a malformed client response raised straight out
        # of this loop, which is run in a background thread. That silently
        # killed the thread: no log line pointing at this download, no
        # state change, no post-processing -- the download just sat frozen
        # in the queue forever, indistinguishable from one still genuinely
        # in progress (kapowarr/t-024). Tolerate a bounded run of
        # consecutive failures (transient outage) before giving up and
        # marking the download failed through the same terminal-state
        # branch already used for a graceful failure below.
        consecutive_status_failures = 0

        while True:
            try:
                download.update_status()
                consecutive_status_failures = 0

            except (ClientNotWorking, CredentialInvalid) as e:
                consecutive_status_failures += 1
                LOGGER.warning(
                    "Failed to update status of download %r (attempt %d/%d): %s",
                    download.title, consecutive_status_failures,
                    Constants.EXTERNAL_DOWNLOAD_STATUS_FAILURE_LIMIT, e
                )

                if (
                    consecutive_status_failures
                    < Constants.EXTERNAL_DOWNLOAD_STATUS_FAILURE_LIMIT
                ):
                    download.sleep_event.wait(
                        timeout=Constants.TORRENT_UPDATE_INTERVAL
                    )
                    continue

                LOGGER.error(
                    "Giving up on download %r after %d consecutive failed "
                    "status updates; marking as failed",
                    download.title, consecutive_status_failures
                )
                # Fall through to the FAILED_STATE branch below instead of
                # calling update_status() again -- it would just raise the
                # same way and spin this loop with no sleep in between.
                download.stop(state=DownloadState.FAILED_STATE)

            ws.emit(status_event)

            if download.state == DownloadState.CANCELED_STATE:
                self._remove_from_client(download, delete_files=True)
                post_processer.canceled(download)
                self.queue.remove(download)
                break

            elif download.state == DownloadState.FAILED_STATE:
                self._remove_from_client(download, delete_files=True)
                post_processer.perm_failed(download)
                self.queue.remove(download)
                break

            elif download.state == DownloadState.SHUTDOWN_STATE:
                break

            elif (
                download.state == DownloadState.SEEDING_STATE
                and not files_copied
            ):
                files_copied = True
                post_processer.seeding(download)

            elif download.state == DownloadState.IMPORTING_STATE:
                if self.settings.sv.delete_completed_downloads:
                    self._remove_from_client(download, delete_files=False)
                post_processer.success(download)
                self.queue.remove(download)
                break

            else:
                # Queued
                # Or downloading
                # Or seeding with files copied
                # Or seeding with seeding_handling = 'complete'
                download.sleep_event.wait(
                    timeout=Constants.TORRENT_UPDATE_INTERVAL
                )

        ws.emit(RemovedFromQueueEvent(download))
        return

    @staticmethod
    def _remove_from_client(download: Download, delete_files: bool) -> None:
        """Remove a finished download from its external client, best effort.

        Removal is tidying up. The client has already delivered the files or
        failed to, and what happens next -- importing a completed download,
        recording a failed one -- does not depend on it succeeding. So a client
        that answers the delete call with an error must not take the download
        down with it.

        SABnzbd answers exactly that way for a job it no longer has, which is
        its normal response to being asked to delete something twice. The
        exception escaped into the download thread between a download
        completing and its post-processing: the files were downloaded, and then
        never imported, because clearing them from the client afterwards
        failed.
        """
        try:
            download.remove_from_client(delete_files=delete_files)
        except Exception:
            # Deliberately broad. Nothing this call can raise is worth losing
            # a completed download over, and the traceback is preserved.
            LOGGER.exception(
                'Could not remove %r from its download client; continuing '
                'with post-processing anyway',
                download.title
            )
        return

    def __run_torrent_download(self, download: TorrentDownload) -> None:
        """Start a torrent download. Intended to be run in a thread.

        Args:
            download (TorrentDownload): The torrent download to run.
                One of the entries in self.queue.
        """
        download.run()

        seeding_handling = self.settings.sv.seeding_handling
        if seeding_handling == SeedingHandling.COMPLETE:
            post_processer = PostProcessorTorrentsComplete

        elif seeding_handling == SeedingHandling.COPY:
            post_processer = PostProcessorTorrentsCopy

        else:
            assert_never(seeding_handling)

        self.__run_external_download(download, post_processer)
        return

    def __run_usenet_download(self, download: NZBDownload) -> None:
        """Start a Usenet (e.g. SABnzbd) download. Intended to be run in a
        thread.

        Usenet downloads never seed -- there's no `seeding_handling` choice
        to make here, unlike torrents. `PostProcessorTorrentsComplete`'s
        success actions (folder extraction + scan, same as a completed
        torrent) apply unchanged: SABnzbd, like a torrent client, delivers
        a download as a folder that needs extracting, not a single file.

        Args:
            download (NZBDownload): The Usenet download to run.
                One of the entries in self.queue.
        """
        download.run()
        self.__run_external_download(download, PostProcessorTorrentsComplete)
        return

    # region Queue Management
    def _process_queue(self) -> None:
        """
        Handle the queue. In the case that there is something in the queue
        and not the max amount of downloads are active, start a download.
        This can safely be called at any point in time and with the queue in
        any state.
        """
        active_downloads = 0
        max_downloads = self.settings.sv.concurrent_direct_downloads
        for download in self.queue:
            if not isinstance(download, ExternalDownload):
                if download.state == DownloadState.DOWNLOADING_STATE:
                    active_downloads += 1

                elif (
                    download.state == DownloadState.QUEUED_STATE
                    and active_downloads < max_downloads
                ):
                    if download.download_thread is not None:
                        download.download_thread.start()
                    active_downloads += 1

                if active_downloads >= max_downloads:
                    break

        return

    def set_queue_location(
        self,
        download_id: int,
        index: int
    ) -> None:
        """Set the location of a download in the queue.

        Args:
            download_id (int): The ID of the download to move.

            index (int): The new index of the download.

        Raises:
            DownloadNotFound: The ID doesn't map to any download in the queue.
            DownloadUnmovable: The download is not allowed to be moved.
            InvalidKeyValue: The index is out of bounds.
        """
        download = self.get_one(download_id)
        if download.state != DownloadState.QUEUED_STATE:
            raise DownloadUnmovable(download_id)

        if index < 0 or index >= len(self.queue):
            raise InvalidKeyValue('index', index)

        self.queue.remove(download)
        self.queue.insert(index, download)
        return

    def __prepare_downloads_for_queue(
        self,
        downloads: List[Download],
        forced_match: bool
    ) -> List[Download]:
        """Get download instances ready to be put in the queue.
        Registers them in the db if not already. Creates the download thread.
        For torrents, it chooses the client and runs the download (status) thread.

        Args:
            downloads (List[Download]): The downloads to get ready.

            forced_match (bool): The download was forced.

        Returns:
            List[Download]: The downloads, now prepared.
        """
        cursor = get_db()
        for download in downloads:
            if download.id is None:
                if isinstance(download, ExternalDownload):
                    external_client_id = download.external_client.id
                else:
                    external_client_id = None

                if isinstance(download.covered_issues, tuple):
                    covered_issues = CommaList(
                        map(str, download.covered_issues)
                    ).__str__()

                elif isinstance(download.covered_issues, float):
                    covered_issues = str(download.covered_issues)

                else:
                    covered_issues = None

                download.id = cursor.execute(
                    """
                    INSERT INTO download_queue(
                        volume_id, client_type, external_client_id,
                        download_link, covered_issues, force_original_name,
                        source_type, source_name,
                        web_link, web_title, web_sub_title
                    )
                    VALUES (
                        :volume_id, :client_type, :external_client_id,
                        :download_link, :covered_issues, :force_original_name,
                        :source_type, :source_name,
                        :web_link, :web_title, :web_sub_title
                    );
                    """,
                    {
                        'volume_id': download.volume_id,
                        'client_type': download.identifier,
                        'external_client_id': external_client_id,
                        'download_link': download.download_link,
                        'covered_issues': covered_issues,
                        'force_original_name': forced_match,
                        'source_type': download.source_type.value,
                        'source_name': download.source_name,
                        'web_link': download.web_link,
                        'web_title': download.web_title,
                        'web_sub_title': download.web_sub_title
                    }
                ).lastrowid

            if not isinstance(download, ExternalDownload):
                download.download_thread = Server().get_db_thread(
                    target=self.__run_download,
                    args=(download,),
                    name=f'DownloadThread-{download.id}'
                )

            if isinstance(download, TorrentDownload):
                thread = Server().get_db_thread(
                    target=self.__run_torrent_download,
                    args=(download,),
                    name=f'TorrentDownloadThread-{download.id}'
                )
                download.download_thread = thread
                thread.start()

            elif isinstance(download, NZBDownload):
                thread = Server().get_db_thread(
                    target=self.__run_usenet_download,
                    args=(download,),
                    name=f'UsenetDownloadThread-{download.id}'
                )
                download.download_thread = thread
                thread.start()

            WebSocket().emit(AddedToQueueEvent(download))
        return downloads

    # region Getting
    def get_all(self) -> List[dict]:
        """Get all queue entries

        Returns:
            List[dict]: All queue entries, formatted using `Download.as_dict()`.
        """
        return [e.as_dict() for e in self.queue]

    def get_one(self, download_id: int) -> Download:
        """Get a queue entry based on it's ID.

        Args:
            download_id (int): The ID of the download to fetch.

        Raises:
            DownloadNotFound: The ID doesn't map to any download in the queue.

        Returns:
            Download: The queue entry.
        """
        for entry in self.queue:
            if entry.id == download_id:
                return entry
        raise DownloadNotFound(download_id)

    # region Adding
    def __determine_link_type(self, link: str) -> Union[str, None]:
        """Determine which registered download prepper owns a result link."""
        prepper = DownloadPreppers.get_for_link(link)
        return prepper.identifier if prepper is not None else None

    def link_in_queue(self, link: str) -> bool:
        """Check if a link is already in the queue.

        Args:
            link (str): The link to check for.

        Returns:
            bool: Whether the link is in the queue.
        """
        return any(
            link in (d.web_link, d.download_link)
            for d in self.queue
        )

    def download_for_volume_queued(self, volume_id: int) -> bool:
        """Check whether there is a download in the queue for a given volume.

        Args:
            volume_id (int): The ID of the volume to check for.

        Returns:
            bool: Whether there is a download in the queue.
        """
        return any(
            d.volume_id == volume_id
            for d in self.queue
        )

    async def add(
        self,
        link: str,
        volume_id: int,
        issue_id: Union[int, None] = None,
        force_match: bool = False
    ) -> Tuple[List[dict], Union[EnqueuingDownloadFailureReason, None]]:
        """Add a source result to the queue through its registered prepper.

        Args:
            link (str): A source result/download link.

            volume_id (int): The id of the volume for which the download is
            intended.

            issue_id (Union[int, None], optional): The id of the issue for which
            the download is intended.
                Defaults to None.

            force_match (bool, optional): On sources where downloads are
            filtered, skip this and instead download everything.
                Defaults to False.

        Returns:
            Tuple[List[dict], Union[FailReason, None]]:
            Queue entries that were added from the link and reason for failing
            if no entries were added.
        """
        # An indexer link carries its API key in the query string, and this
        # line put it in the log in plain text on every enqueue.
        LOGGER.info(
            'Adding download for ' +
            f'volume {volume_id}{f" issue {issue_id}" if issue_id else ""}: ' +
            f'{redact_url(link)}'
        )

        if self.link_in_queue(link):
            LOGGER.info('Download already in queue')
            return [], None

        link_type = self.__determine_link_type(link)
        if link_type is None:
            LOGGER.warning(
                'No download prepper recognised link: %s', redact_url(link)
            )
            return [], None

        try:
            downloads = await DownloadPreppers.get(link_type).prepare(
                link,
                volume_id,
                issue_id,
                force_match
            )
        except EnqueuingDownloadFailure as error:
            return [], error.reason

        result = self.__prepare_downloads_for_queue(
            downloads,
            forced_match=force_match
        )
        self.queue += result

        self._process_queue()
        return [r.as_dict() for r in result], None

    def add_multiple(
        self,
        add_args: Iterable[Tuple[str, int, Union[int, None], bool]]
    ) -> None:
        async def add_wrapper():
            await gather(
                *(self.add(*entry)
                for entry in add_args)
            )

        run(add_wrapper())
        return

    def __load_downloads(self) -> None:
        """
        Load downloads from the database and add them to the queue
        for re-downloading
        """
        cursor = get_db()
        downloads = cursor.execute("""
            SELECT
                id, volume_id, client_type, external_client_id,
                download_link, covered_issues,
                force_original_name,
                source_type, source_name,
                web_link, web_title, web_sub_title
            FROM download_queue;
        """).fetchall()

        if downloads:
            LOGGER.info('Loading downloads')

        for download in iter_commit(downloads):
            LOGGER.debug(f'Download from database: {dict(download)}')
            if download['covered_issues'] is None:
                covered_issues = None

            elif ',' in download['covered_issues']:
                covered_issues = (
                    float(download['covered_issues'].split(',')[0]),
                    float(download['covered_issues'].split(',')[1])
                )

            else:
                covered_issues = float(download['covered_issues'])

            kwargs = {}
            if issubclass(
                download_type_to_class[download['client_type']],
                ExternalDownload
            ):
                kwargs = {
                    'external_client': ExternalClients.get_client(
                        download['external_client_id']
                    )
                }

            try:
                dl_instance = download_type_to_class[download['client_type']](
                    download_link=download['download_link'],
                    volume_id=download['volume_id'],
                    covered_issues=covered_issues,
                    source_type=DownloadSource(download['source_type']),
                    source_name=download['source_name'],
                    web_link=download['web_link'],
                    web_title=download['web_title'],
                    web_sub_title=download['web_sub_title'],
                    forced_match=download['force_original_name'],
                    **kwargs
                )
                dl_instance.id = download['id']

            except LinkBroken:
                # Link is broken

                issue_id = None
                if isinstance(covered_issues, float):
                    issue_id = Issue.from_volume_and_calc_number(
                        download['volume_id'],
                        covered_issues
                    ).id

                add_to_blocklist(
                    web_link=download['web_link'],
                    web_title=download['web_title'],
                    web_sub_title=download['web_sub_title'],
                    download_link=download['download_link'],
                    source=DownloadSource(download['source_type']),
                    volume_id=download['volume_id'],
                    issue_id=issue_id,
                    reason=BlocklistReason.LINK_BROKEN
                )
                cursor.execute(
                    "DELETE FROM download_queue WHERE id = ?;",
                    (download['id'],)
                )
                continue

            except (DownloadLimitReached, IssueNotFound, ClientNotWorking):
                cursor.execute(
                    "DELETE FROM download_queue WHERE id = ?;",
                    (download['id'],)
                )
                continue

            self.queue += self.__prepare_downloads_for_queue(
                [dl_instance],
                forced_match=download['force_original_name']
            )

        self._process_queue()
        return

    def load_downloads(self) -> Thread:
        """Load downloads from the database and add them to the queue
        for re-downloading. This is done in a separate thread.

        Returns:
            Thread: The thread that is loading the downloads.
        """
        result = Server().get_db_thread(
            target=self.__load_downloads,
            name="DownloadImportThread"
        )
        result.start()
        return result

    # region Removing and stopping
    def remove(self, download_id: int, blocklist: bool = False) -> None:
        """Remove a download entry from the queue.

        Args:
            download_id (int): The ID of the download to remove from the queue.

            blocklist (bool, optional): Add the page link to the blocklist.
                Defaults to False.

        Raises:
            DownloadNotFound: The ID doesn't map to any download in the queue.
        """
        LOGGER.info(f'Removing download with id {download_id} and {blocklist=}')

        download = self.get_one(download_id)
        if not download.download_thread:
            return

        prev_state = download.state
        was_thread_running = download.download_thread.is_alive()
        download.stop()
        WebSocket().emit(QueueStatusEvent(download))

        if (
            # Direct download
            not isinstance(download, ExternalDownload)
            and (
                # Download was queued when we stopped it
                prev_state == DownloadState.QUEUED_STATE
                or
                (
                    # Download errored out without catching it
                    prev_state == DownloadState.DOWNLOADING_STATE
                    and not was_thread_running
                )
            )
        ):
            self.queue.remove(download)
            PostProcessor.canceled(download)
            WebSocket().emit(RemovedFromQueueEvent(download))

        if blocklist:
            add_to_blocklist(
                web_link=download.web_link,
                web_title=download.web_title,
                web_sub_title=download.web_sub_title,
                download_link=download.download_link,
                source=download.source_type,
                volume_id=download.volume_id,
                issue_id=download.issue_id,
                reason=BlocklistReason.ADDED_BY_USER
            )

        return

    def _remove_mega(self, exclude_id: int) -> None:
        """Remove all Mega downloads from the queue except for the one with
        the id of `exclude_id`. That one will be handled by the download itself.

        Args:
            exclude_id (int): The ID of the Mega download to not remove from the
            queue.
        """
        for download in self.queue[::-1]:
            if (
                isinstance(download, MegaDownload)
                and download.id != exclude_id
            ):
                self.remove(download.id)
        return

    def remove_all(self) -> None:
        """Remove all downloads from the queue"""
        for download in self.queue[::-1]:
            self.remove(download.id)

        for download in self.queue:
            if download.download_thread is not None:
                download.download_thread.join()

        get_db().execute(
            "DELETE FROM download_queue;"
        )

        return

    def stop_handle(self) -> None:
        """Cancel any running download and stop the handler"""
        LOGGER.debug('Stopping download thread')

        for e in self.queue:
            e.stop(DownloadState.SHUTDOWN_STATE)

        for e in self.queue:
            if (
                e.download_thread is not None
                and e.download_thread.is_alive()
            ):
                e.download_thread.join()

        return

    def empty_download_folder(self) -> None:
        """
        Empty the temporary download folder of files that aren't being downloaded.
        Handy in the case that a crash left half-downloaded files behind in the folder.
        """
        LOGGER.info('Emptying the temporary download folder')
        folder = self.settings.sv.download_folder

        files_in_queue = [
            basename(file)
            for download in self.queue
            for file in download.files
        ]
        files_in_folder = listdir(folder)
        ghost_files = [
            join(folder, f)
            for f in files_in_folder
            if f not in files_in_queue
        ]

        for f in ghost_files:
            delete_file_folder(f)

        return


# =====================
# region Download History
# =====================
def get_download_history(
    volume_id: Union[int, None] = None,
    issue_id: Union[int, None] = None,
    offset: int = 0
) -> List[Dict[str, Any]]:
    """Get the download history in blocks of 50.

    Args:
        volume_id (Union[int, None], optional): Get the history of a specific
        volume.
            Defaults to None.

        issue_id (Union[int, None], optional): Get the history of a specific
        issue. No need to supply volume_id in order to get issue history.
            Defaults to None.

        offset (int, optional): The offset of the list.
        The higher the number, the deeper into history you go.
            Defaults to 0.

    Returns:
        List[Dict[str, Any]]: The history entries.
    """
    if issue_id is not None:
        comm = """
            SELECT
                web_link, web_title, web_sub_title,
                file_title,
                volume_id, issue_id,
                source, downloaded_at, success
            FROM download_history
            WHERE issue_id = :issue_id
            ORDER BY downloaded_at DESC
            LIMIT 50
            OFFSET :offset;
            """

    elif volume_id is not None:
        comm = """
            SELECT
                web_link, web_title, web_sub_title,
                file_title,
                volume_id, issue_id,
                source, downloaded_at, success
            FROM download_history
            WHERE volume_id = :volume_id
            ORDER BY downloaded_at DESC
            LIMIT 50
            OFFSET :offset;
            """

    else:
        comm = """
            SELECT
                web_link, web_title, web_sub_title,
                file_title,
                volume_id, issue_id,
                source, downloaded_at, success
            FROM download_history
            ORDER BY downloaded_at DESC
            LIMIT 50
            OFFSET :offset;
            """

    return get_db().execute(
        comm,
        {
            'issue_id': issue_id,
            'volume_id': volume_id,
            'offset': offset * 50
        }
    ).fetchalldict()


def delete_download_history() -> None:
    """
    Delete complete download history
    """
    LOGGER.info("Deleting download history")
    get_db().execute("DELETE FROM download_history;")
    return
