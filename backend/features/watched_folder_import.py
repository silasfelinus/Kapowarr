# -*- coding: utf-8 -*-

"""Watched-folder auto import of externally acquired files.

Upstream issue #122: a lot of comics arrive on disk without Kapowarr having
asked for them -- a download client Kapowarr doesn't manage, a file copied off
a NAS, a share mounted into the container. Before this, the only way in was the
Wanted workbench's manual import (`backend.features.manual_import`), which
needs a human to name the volume for every file.

This module is the unattended counterpart. It watches one inbound folder, waits
for each file to stop changing, works out which volume *already in the library*
it belongs to, and then hands the file to the exact same import path a manual
import uses -- move into the volume folder, filename-match, rename, convert,
set file properties.

Deliberate boundaries
---------------------

**It never creates a volume.** Identifying an unknown series is
`backend.features.library_import`'s job, and its Continuous Library Import runs
a ComicVine-paced search per unresolved folder to do it. A watched folder that
also produced new volumes would duplicate that machinery and race it for the
same rate limit. A file this module can't match to a volume already in the
library is left exactly where it is, untouched, for the user to handle in the
manual-import workbench (or to be picked up later, once the volume is added).

**It never deletes anything it didn't import.** Unmatched files, in-progress
files and unreadable files are left alone. Only the empty directories left
behind by files this run actually moved are cleaned up.

**It doesn't compete with the root library.** `Settings` rejects a watched
folder that collides with a root folder or the download folder, so this can
never sweep up the library itself or a direct download still being written.
That check lives in `backend.internals.settings` next to `download_folder`'s
equivalent, because it has to run at the moment the setting is saved.
"""

from __future__ import annotations

from os.path import getmtime, isdir, isfile
from time import time
from typing import (Callable, Container, Dict, List, Optional, Tuple,
                    Union)

from backend.base.definitions import (FileConstants, FilenameData,
                                      IssueData, VolumeData)
from backend.base.file_extraction import extract_filename_data
from backend.base.files import delete_empty_child_folders, list_files
from backend.base.helpers import extract_year_from_date
from backend.base.logging import LOGGER
from backend.features.manual_import import manual_import_files
from backend.implementations.conversion import mass_convert
from backend.implementations.file_processing import mass_process_files
from backend.implementations.matching import file_importing_filter, match_title
from backend.implementations.naming import mass_rename
from backend.implementations.volumes import Library, Volume
from backend.internals.settings import Settings

# How long a file has to sit unmodified before it's considered finished. An
# external client writing a 400MB archive touches mtime continuously, so this
# is what keeps a half-written file from being imported as a truncated one.
# Deliberately generous: importing a partial file is destructive (it gets moved
# and matched), waiting one more cycle costs nothing.
WATCHED_FOLDER_SETTLE_SECONDS = 120

# The interval the task runs at, seeded into `task_intervals`.
WATCHED_FOLDER_IMPORT_INTERVAL_SECONDS = 900


def file_has_settled(
    filepath: str,
    now: Union[float, None] = None,
    settle_seconds: int = WATCHED_FOLDER_SETTLE_SECONDS
) -> bool:
    """Whether a file has been unmodified long enough to be safe to import.

    Args:
        filepath (str): The file to check.

        now (Union[float, None], optional): The current epoch time. Passed in
            so one scan judges every file against a single clock reading.
            Defaults to None, meaning read the clock.

        settle_seconds (int, optional): How long the file must have been
            unmodified. Defaults to `WATCHED_FOLDER_SETTLE_SECONDS`.

    Returns:
        bool: Whether the file looks finished. A file that vanished or can't be
            stat'd counts as not settled, never as settled.
    """
    try:
        modified_at = getmtime(filepath)
    except OSError:
        return False

    if now is None:
        now = time()

    return (now - modified_at) >= settle_seconds


class LibraryIndex:
    """Volume data read once and reused for every file in one pass.

    Matching is naturally O(files x volumes), and both `Volume.get_data()` and
    `Volume.get_issues()` are database round-trips. Without this, a watched
    folder holding 30 files on a 2000-volume library would issue 60,000 volume
    reads for a single scan -- the same "one big library stalls a background
    importer" shape that Continuous Import has already had to be fixed for.

    Built per pass rather than cached across passes on purpose: a volume added,
    renamed or deleted between runs must be seen on the very next one.
    """

    def __init__(self, volume_ids: Union[List[int], None] = None) -> None:
        if volume_ids is None:
            volume_ids = Library.get_volumes()

        self.volume_ids: List[int] = list(volume_ids)
        self._volumes: Dict[int, Volume] = {}
        self._data: Dict[int, VolumeData] = {}
        self._issues: Dict[
            int, Tuple[List[IssueData], Dict[float, Union[int, None]]]
        ] = {}

    def _volume(self, volume_id: int) -> Volume:
        if volume_id not in self._volumes:
            self._volumes[volume_id] = Volume(volume_id)
        return self._volumes[volume_id]

    def data(self, volume_id: int) -> VolumeData:
        "The volume's own fields (title, year, volume number, ...)."
        if volume_id not in self._data:
            self._data[volume_id] = self._volume(volume_id).get_data()
        return self._data[volume_id]

    def issues(
        self,
        volume_id: int
    ) -> Tuple[List[IssueData], Dict[float, Union[int, None]]]:
        """The volume's issues plus their issue-number-to-year map.

        Loaded lazily and only for volumes whose title already matched, so a
        library-wide scan never pays for the issue list of every volume.
        """
        if volume_id not in self._issues:
            volume_issues: List[IssueData] = self._volume(
                volume_id
            ).get_issues(_skip_files=True)
            self._issues[volume_id] = (
                volume_issues,
                {
                    i.calculated_issue_number: extract_year_from_date(i.date)
                    for i in volume_issues
                }
            )
        return self._issues[volume_id]


def match_file_to_library_volume(
    filepath: str,
    index: Union[LibraryIndex, None] = None
) -> Union[int, None]:
    """Find the one volume in the library that a file belongs to.

    Reuses the same two predicates the library's own importer and namer use --
    `match_title()` on the parsed series name, then `file_importing_filter()`
    for volume number / year / special version -- rather than inventing a
    second, subtly different notion of "this file is this volume".

    Args:
        filepath (str): The file to identify.

        index (Union[LibraryIndex, None], optional): A prepared index to match
            against, shared across the files of one pass. Defaults to None,
            meaning build a fresh one over the whole library.

    Returns:
        Union[int, None]: The volume's ID, or `None` if no volume matched --
            or if more than one did. An ambiguous file is left for a human on
            purpose: guessing between two volumes moves the file into the wrong
            folder, which is exactly the outcome this feature must not produce.
    """
    return match_parsed_to_library_volume(
        extract_filename_data(filepath), index, filepath)


def match_parsed_to_library_volume(
    file_data: FilenameData,
    index: Union[LibraryIndex, None] = None,
    described_as: str = '',
    quiet: bool = False
) -> Union[int, None]:
    """Find the one volume a already-parsed name belongs to.

    The body of `match_file_to_library_volume`, reachable without a file. An
    indexer release arrives already parsed -- `SearchResultData` extends
    `FilenameData` -- so the feed sync matches those fields rather than
    re-deriving them from the display title, which is a different string.

    Args:
        file_data (FilenameData): The parsed name.

        index (Union[LibraryIndex, None], optional): A prepared index to match
            against. Defaults to None, meaning build one over the library.

        described_as (str, optional): What to call it in the log. Defaults to
            the parsed series.

        quiet (bool, optional): Say nothing about an ambiguous name. The feed
            sync sees the same releases every quarter of an hour and counts
            them into its summary instead; a file someone dropped in the
            watched folder is a one-off worth a line. Defaults to False.

    Returns:
        Union[int, None]: The volume's ID, or None if none matched, or if more
            than one did.
    """
    if not file_data['series']:
        return None

    if index is None:
        index = LibraryIndex()

    described_as = described_as or str(file_data['series'])
    matches: List[int] = []
    for volume_id in index.volume_ids:
        volume_data = index.data(volume_id)

        if not match_title(volume_data.title, file_data['series']):
            continue

        volume_issues, number_to_year = index.issues(volume_id)

        if file_importing_filter(
            file_data,
            volume_data,
            volume_issues,
            number_to_year
        ):
            matches.append(volume_id)

        if len(matches) > 1:
            if not quiet:
                LOGGER.info(
                    '%s matches more than one volume in the library; leaving '
                    'it alone', described_as
                )
            return None

    if not matches:
        return None

    return matches[0]


def find_importable_files(
    watched_folder: str,
    now: Union[float, None] = None
) -> Tuple[List[str], int]:
    """List the files in the watched folder that are ready to be imported.

    Args:
        watched_folder (str): The folder to scan.

        now (Union[float, None], optional): The current epoch time, shared by
            every settle check in this scan. Defaults to None.

    Returns:
        Tuple[List[str], int]: The settled, scannable files, and how many
            otherwise-eligible files were skipped because they are still being
            written.
    """
    if now is None:
        now = time()

    candidates = list_files(
        watched_folder,
        FileConstants.SCANNABLE_EXTENSIONS
    )

    ready: List[str] = []
    unsettled = 0
    for filepath in candidates:
        if file_has_settled(filepath, now):
            ready.append(filepath)
        else:
            unsettled += 1

    return ready, unsettled


def _final_paths(import_result: Dict[str, List[Dict[str, Optional[str]]]]) -> List[str]:
    """The on-disk paths of the files a manual import actually placed."""
    return [
        entry['moved_to'] or entry['filepath']
        for entry in import_result['imported']
    ]


def _post_process_volume(volume_id: int, filepaths: List[str]) -> None:
    """Run the library's own rename/convert/properties steps over new files.

    This is the same sequence, driven by the same settings, that
    `backend.features.post_processing` runs on a completed download -- but
    reached through the public `mass_*` helpers instead of by fabricating a
    `Download` object, since nothing was downloaded.
    """
    settings = Settings().get_settings()
    present = [f for f in filepaths if isfile(f)]
    if not present:
        return

    if settings.rename_downloaded_files:
        renamed = mass_rename(
            volume_id,
            filepath_filter=present,
            process_individual_files=False
        )
        if renamed:
            present = [f for f in renamed if isfile(f)]

    if settings.convert and present:
        mass_convert(
            volume_id,
            filepath_filter=present,
            update_websocket_files=True,
            process_individual_files=False
        )

    mass_process_files(volume_id)
    return


class WatchedFolderImportSummary(dict):
    """The outcome of one watched-folder pass.

    A plain dict subclass so it stays trivially serialisable for the task
    message and the tests, while still being a named thing at call sites.

    Keys:
        imported (int): Files moved into a volume folder and matched.
        unmatched (int): Settled files no library volume claimed.
        unsettled (int): Files skipped because they're still being written.
        skipped (int): Files the import path itself declined (name collision
            in the target folder, file vanished mid-run).
        volumes (int): How many distinct volumes received files.
        errors (int): Volumes whose import raised. Their files are still in
            the watched folder and get retried next pass.
    """


def run_watched_folder_import(
    should_stop: Union[Callable[[], bool], None] = None
) -> WatchedFolderImportSummary:
    """Do one pass over the configured watched folder.

    Args:
        should_stop (Union[Callable[[], bool], None], optional): Polled between
            volumes so a user-requested stop takes effect at a safe boundary --
            never part-way through one volume's move/rename/convert sequence.
            Defaults to None.

    Returns:
        WatchedFolderImportSummary: What the pass did. An unconfigured or
            missing watched folder is not an error; it returns an all-zero
            summary, because the task is enrolled on an interval for every
            install and most installs will never configure one.
    """
    watched_folder = Settings().sv.watched_folder
    if not watched_folder:
        return WatchedFolderImportSummary(
            imported=0, unmatched=0, unsettled=0, skipped=0, volumes=0,
            errors=0
        )

    return import_loose_files(
        watched_folder, should_stop, description='Watched folder')


def import_loose_files(
    folder: str,
    should_stop: Union[Callable[[], bool], None] = None,
    leave_alone: Union[Container[str], None] = None,
    description: str = 'Loose files',
    leave_original: bool = False,
    narrow: Union[Callable[[List[str]], List[str]], None] = None
) -> WatchedFolderImportSummary:
    """Import whatever finished files in a folder belong to a known volume.

    The watched folder is one caller; recovering downloads that finished but
    never reached the library is the other (see
    `backend.features.orphaned_downloads`). Both want the same thing: take a
    file sitting on disk, work out which volume already in the library it
    belongs to, and hand it to the same import path a manual import uses.

    Args:
        folder (str): The folder to scan.

        should_stop (Union[Callable[[], bool], None], optional): Polled
            between volumes so a stop takes effect at a safe boundary -- never
            part-way through one volume's move/rename/convert sequence.
            Defaults to None.

        leave_alone (Union[Container[str], None], optional): Paths this pass
            must not touch, whatever their state. The download folder holds
            files that belong to downloads still running, and a torrent that
            is still seeding is being read from where it sits. Defaults to
            None, meaning everything in the folder is fair game.

        description (str, optional): What to call this pass in the log.
            Defaults to 'Loose files'.

        leave_original (bool, optional): Link the file into the volume folder
            and leave the source where it is, instead of moving it. Defaults
            to False.

        narrow (Union[Callable[[List[str]], List[str]], None], optional): A
            last say over which settled files are worth importing. The
            watched folder wants everything a user drops in it; orphan
            recovery wants only what the library is still missing, since
            linking a file in leaves the original behind to be found again.
            Defaults to None, meaning import whatever matched.

    Returns:
        WatchedFolderImportSummary: What the pass did.
    """
    summary = WatchedFolderImportSummary(
        imported=0, unmatched=0, unsettled=0, skipped=0, volumes=0, errors=0
    )

    if not isdir(folder):
        LOGGER.warning(
            '%s: %s no longer exists; skipping this pass', description, folder
        )
        return summary

    ready, summary['unsettled'] = find_importable_files(folder)
    if leave_alone is not None:
        ready = [f for f in ready if f not in leave_alone]

    if ready and narrow is not None:
        considered = len(ready)
        ready = narrow(ready)
        if len(ready) != considered:
            LOGGER.info(
                '%s: %d of %d file(s) are still worth importing',
                description, len(ready), considered
            )

    if not ready:
        return summary

    index = LibraryIndex()
    per_volume: Dict[int, List[str]] = {}
    for filepath in ready:
        volume_id = match_file_to_library_volume(filepath, index)
        if volume_id is None:
            summary['unmatched'] += 1
            continue
        per_volume.setdefault(volume_id, []).append(filepath)

    moved_anything = False
    for volume_id, filepaths in per_volume.items():
        if should_stop is not None and should_stop():
            LOGGER.info('%s: import stopped between volumes', description)
            break

        LOGGER.info(
            '%s: importing %d file(s) into volume %d',
            description, len(filepaths), volume_id
        )
        try:
            result = manual_import_files(
                volume_id, filepaths, leave_original=leave_original)

            summary['imported'] += len(result['imported'])
            summary['skipped'] += len(result['skipped'])
            if not result['imported']:
                continue

            summary['volumes'] += 1
            moved_anything = True
            _post_process_volume(volume_id, _final_paths(result))

        except Exception:
            # One volume failing must not throw away the other volumes' work
            # in the same pass -- notably a volume deleted between the scan
            # and the import, which raises VolumeNotFound. Counted, not
            # swallowed: the count surfaces in the task message, and the
            # traceback is logged in full.
            LOGGER.exception(
                '%s: failed to import into volume %d', description, volume_id
            )
            summary['errors'] += 1

    if moved_anything and not leave_original:
        # Only the directories the moved files vacated. `list_files()` ignores
        # hidden files, so a folder holding only e.g. a `.DS_Store` would look
        # empty to the scan but must not be removed -- hence skip_hidden_folders
        # is left off and deletion is driven by real emptiness on disk.
        delete_empty_child_folders(folder)

    return summary


def describe_summary(summary: WatchedFolderImportSummary) -> str:
    """A one-line, user-facing description of a pass, for the task message."""
    message = (
        f"Watched folder: {summary['imported']} imported into "
        f"{summary['volumes']} volume(s) · {summary['unmatched']} unmatched · "
        f"{summary['unsettled']} still downloading · {summary['skipped']} skipped"
    )
    if summary.get('errors'):
        message += f" · {summary['errors']} failed"
    return message
