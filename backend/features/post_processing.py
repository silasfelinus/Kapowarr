# -*- coding: utf-8 -*-

"""
The post-download processing (a.k.a. post-processing or PP) of downloads.
"""

from __future__ import annotations

from os.path import basename, exists, isfile, join, splitext
from time import time
from typing import TYPE_CHECKING, Dict

from backend.base.definitions import (BlocklistReason, DownloadState,
                                      FileConstants, NotificationEvent)
from backend.base.files import (delete_file_folder, rename_file,
                                set_detected_extension)
from backend.base.logging import LOGGER
from backend.features.pack_normalization import (
    normalize_downloaded_range_pack,
    prune_downloaded_range_files,
)
from backend.features.seed_import import hardlink_or_copy_path
from backend.implementations.blocklist import add_to_blocklist
from backend.implementations.conversion import mass_convert
from backend.implementations.converters import extract_files_from_folder
from backend.implementations.download_clients import TorrentDownload
from backend.implementations.file_matching import scan_files
from backend.implementations.file_processing import mass_process_files
from backend.implementations.naming import mass_rename
from backend.implementations.notifications import send_notification
from backend.implementations.volumes import Volume
from backend.internals.db import commit, get_db
from backend.internals.db_models import FilesDB
from backend.internals.settings import Settings

# Preserve the historical post-processing patch point while changing the
# implementation underneath from byte-copy-only to hardlink-first/copy-fallback.
copy_directory = hardlink_or_copy_path

if TYPE_CHECKING:
    from backend.base.definitions import Download


# region General
def reset_file_link(download: TorrentDownload) -> None:
    "Set download.files back to original folder from the copied folder"
    download.files = download._original_files
    return


# region Database
def remove_from_queue(download: Download) -> None:
    "Delete the download from the queue in the database"
    get_db().execute(
        "DELETE FROM download_queue WHERE id = ?",
        (download.id,)
    ).connection.commit()
    return


def add_to_history(download: Download) -> None:
    "Add the download to history in the database"
    get_db().execute(
        """
        INSERT INTO download_history(
            web_link, web_title, web_sub_title,
            file_title,
            volume_id, issue_id,
            source, downloaded_at, success
        ) VALUES (
            :web_link, :web_title, :web_sub_title,
            :file_title,
            :volume_id, :issue_id,
            :source, :downloaded_at, :success
        );
        """,
        {
            'web_link': download.web_link,
            'web_title': download.web_title,
            'web_sub_title': download.web_sub_title,
            'file_title': download.title,
            'volume_id': download.volume_id,
            'issue_id': download.issue_id,
            'source': download.source_type.value,
            'downloaded_at': round(time()),
            'success': download.state != DownloadState.FAILED_STATE
        }
    )
    return


def _normalize_downloaded_range(download: Download) -> bool:
    """Normalize wrapped packs and remove already-owned individual issues."""
    normalized_pack = normalize_downloaded_range_pack(download)
    prune_downloaded_range_files(download)
    return normalized_pack


def add_file_to_database(download: Download) -> None:
    """Register downloaded files in the database and match them to issues.

    A search result can describe a true multi-issue range even when the search
    was launched from one issue. Before the first scan, normalize a downloaded
    outer pack that contains complete nested issue files so the scanner sees
    those real files rather than one giant archive covering the whole range.
    """
    normalized_pack = _normalize_downloaded_range(download)
    if not download.files:
        return

    scan_files(
        download.volume_id,
        filepath_filter=download.files,
        update_websocket=True
    )

    if normalized_pack and Settings().sv.rename_downloaded_files:
        download.files = mass_rename(
            download.volume_id,
            filepath_filter=download.files,
            process_individual_files=False
        )
    return


# region Blocklist
def add_dl_to_blocklist(download: Download) -> None:
    "Add the download to the blocklist in the database"
    add_to_blocklist(
        download.web_link,
        download.web_title,
        download.web_sub_title,
        download.download_link,
        download.source_type,
        download.volume_id,
        download.issue_id,
        BlocklistReason.LINK_BROKEN
    )
    return


# region Moving
def move_to_dest(download: Download) -> None:
    "Move file/fold from download folder to final destination"
    if not exists(download.files[0]):
        return

    folder = Volume(download.volume_id).vd.folder
    extension = splitext(download.files[0])[1].lower()
    if extension not in FileConstants.SCANNABLE_EXTENSIONS:
        extension = ''

    file_dest = join(
        folder,
        download.filename_body + extension
    )
    LOGGER.debug(
        f'Moving download to final destination: {download}, Dest: {file_dest}'
    )

    # If it takes very long to delete/move the file/folder (because of it's size),
    # the DB is left locked for a long period leading to timeouts.
    commit()

    if exists(file_dest):
        LOGGER.warning(
            f'The file/folder {file_dest} already exists; replacing with downloaded file'
        )
        delete_file_folder(file_dest)

    rename_file(download.files[0], file_dest)
    download.files = [file_dest]
    return


def _expand_external_download_root(download: Download) -> None:
    """Resolve either a single-file or directory external download."""
    if not download.files:
        return

    root = download.files[0]
    if isfile(root):
        download.files = [root]
    else:
        download.files = extract_files_from_folder(
            root,
            download.volume_id
        )

    if download.files:
        _normalize_downloaded_range(download)
    return


def _scan_and_rename_download(download: Download) -> None:
    if not download.files:
        return

    scan_files(
        download.volume_id,
        filepath_filter=download.files,
        update_websocket=True
    )

    if Settings().sv.rename_downloaded_files:
        download.files = mass_rename(
            download.volume_id,
            filepath_filter=download.files,
            process_individual_files=False
        )
    return


def move_torrent_to_dest(download: TorrentDownload) -> None:
    """
    Move a completed torrent root to the final destination, handling both
    single-file and directory torrents before scanning/renaming.
    """
    if not exists(download.files[0]):
        return

    move_to_dest(download)
    _expand_external_download_root(download)
    _scan_and_rename_download(download)
    return


def copy_file_torrent(download: TorrentDownload) -> None:
    """Create a seed-safe library-side torrent copy.

    Prefer hardlinks so active seeds do not consume duplicate storage; fall back
    to normal file copies when hardlinks are unavailable (for example across
    filesystems). Rename/conversion then operate only on the library-side path.
    Change back to the torrent client's source path with ``reset_file_link``.
    """
    download._original_files = list(download.files)
    if not download.files or not exists(download.files[0]):
        return

    folder = Volume(download.volume_id).vd.folder
    file_dest = join(folder, basename(download.files[0]))
    LOGGER.debug(
        f'Preparing seed-safe library copy: {download}, Dest: {file_dest}'
    )

    # Copying/linking a large tree should not hold the DB lock.
    commit()

    if exists(file_dest):
        LOGGER.warning(
            f'The file/folder {file_dest} already exists; replacing with downloaded file'
        )
        delete_file_folder(file_dest)

    copy_directory(download.files[0], file_dest)
    download.files = [file_dest]
    # File ownership, permissions and dates can mutate hardlink inode metadata.
    # Keep the historical post-processing action order, but turn that action
    # into a deferred marker until the torrent client no longer needs its path.
    download._defer_file_properties_until_seed_cleanup = True

    _expand_external_download_root(download)
    _scan_and_rename_download(download)
    return


# region Extras
def delete_file(download: Download) -> None:
    "Delete file from download folder"
    for f in download.files:
        delete_file_folder(f)

    if getattr(
        download,
        '_defer_file_properties_until_seed_cleanup',
        False
    ) is True:
        download._defer_file_properties_until_seed_cleanup = False
        mass_process_files(
            download.volume_id,
            download.issue_id
        )
    return


def rename_with_proper_extension(download: Download) -> None:
    """
    Rename a file with the proper extension based on mimetype. Rescan files
    in case a rename is done.
    """
    renamed_files: Dict[str, str] = {}
    for idx, file in enumerate(download.files):
        if not isfile(file):
            continue

        new_file = set_detected_extension(file)
        if new_file != file:
            rename_file(file, new_file)
            download.files[idx] = new_file
            renamed_files[file] = new_file

    if renamed_files:
        FilesDB.update_filepaths(renamed_files)
        commit()

    return


def convert_file(download: Download) -> None:
    "Convert a file into a different format based on settings"
    if not download.files or not Settings().sv.convert:
        return

    download.files += mass_convert(
        download.volume_id,
        download.issue_id,
        filepath_filter=download.files,
        update_websocket_files=True,
        process_individual_files=False
    )
    return


def set_file_properties(download: Download) -> None:
    "Process the file to set ownership, permissions and file date"
    if getattr(
        download,
        '_defer_file_properties_until_seed_cleanup',
        False
    ) is True:
        return

    mass_process_files(
        download.volume_id,
        download.issue_id
    )
    return


# region Post-Processors
class PostProcessor:
    actions_success = [
        remove_from_queue,
        add_to_history,
        move_to_dest,
        rename_with_proper_extension,
        add_file_to_database,
        convert_file,
        set_file_properties
    ]

    actions_seeding = []

    actions_canceled = [
        delete_file,
        remove_from_queue
    ]

    actions_shutdown = [
        delete_file
    ]

    actions_failed = [
        remove_from_queue,
        add_to_history,
        delete_file
    ]

    actions_perm_failed = [
        remove_from_queue,
        add_to_history,
        add_dl_to_blocklist,
        delete_file
    ]

    @staticmethod
    def _run_actions(actions: list, download) -> None:
        for action in actions:
            action(download)
        return

    @classmethod
    def success(cls, download) -> None:
        LOGGER.info(f'Postprocessing of successful download: {download.id}')
        cls._run_actions(cls.actions_success, download)
        send_notification(
            NotificationEvent.DOWNLOAD_COMPLETED,
            'Download completed',
            download.title
        )
        return

    @classmethod
    def seeding(cls, download) -> None:
        LOGGER.info(f'Postprocessing of seeding download: {download.id}')
        cls._run_actions(cls.actions_seeding, download)
        return

    @classmethod
    def canceled(cls, download) -> None:
        LOGGER.info(f'Postprocessing of canceled download: {download.id}')
        cls._run_actions(cls.actions_canceled, download)
        return

    @classmethod
    def shutdown(cls, download) -> None:
        LOGGER.info(f'Postprocessing of shut down download: {download.id}')
        cls._run_actions(cls.actions_shutdown, download)
        return

    @classmethod
    def failed(cls, download) -> None:
        LOGGER.info(f'Postprocessing of failed download: {download.id}')
        cls._run_actions(cls.actions_failed, download)
        return

    @classmethod
    def perm_failed(cls, download) -> None:
        LOGGER.info(
            f'Postprocessing of permanently failed download: {download.id}'
        )
        cls._run_actions(cls.actions_perm_failed, download)
        send_notification(
            NotificationEvent.IMPORT_FAILED,
            'Import failed',
            download.title
        )
        return


class PostProcessorTorrentsComplete(PostProcessor):
    actions_success = [
        remove_from_queue,
        add_to_history,
        move_torrent_to_dest,
        convert_file,
        set_file_properties
    ]


class PostProcessorTorrentsCopy(PostProcessor):
    actions_success = [
        remove_from_queue,
        delete_file
    ]

    actions_seeding = [
        add_to_history,
        copy_file_torrent,
        convert_file,
        set_file_properties,
        reset_file_link
    ]
