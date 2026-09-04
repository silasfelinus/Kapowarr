# -*- coding: utf-8 -*-

"""Recovering downloads that finished but never reached the library.

A download that completes hands itself to `post_processing`, which moves the
file into the volume folder and records it. If that sequence fails part-way,
the bytes are already on disk in the download folder and nothing is coming
back for them: the queue moves on, the library never hears about the file,
and the next search finds the same issue missing and grabs it again.

That was not hypothetical. On 2026-09-01 twenty-three finished downloads --
two Kaya issues and twenty-one X-Statix -- were lost exactly this way in a
single run, all to the same locked write, and nothing in the application knew
they were sitting there. `post_processing` has since been reordered so the
queue row outlives a failed import, which covers a failure from that point
on. This covers what is already on disk, and anything a future failure leaves
behind.

Deliberate boundaries
---------------------

**It never moves anything.** `seeding_handling` defaults to `copy`, which
means a torrent client goes on seeding out of the download folder long after
Kapowarr has finished with the release and stopped tracking it. Moving such a
file would silently break the seed. So this links the file into the volume
folder -- a hardlink where the filesystem allows one, a copy where it does
not -- and leaves the original exactly where it was, for whatever is still
reading it and for the user to clear when they choose.

**It only recovers what the library is still missing.** Linking a file in
does not make the original vanish -- that is the point -- and the library's
own post-processing renames and converts what it imports, so the copy in the
library no longer shares a name with the file in the download folder. Without
this check the pass therefore re-imported the same file every hour, copying
and re-converting it each time: on 2026-09-02 X-Statix 13 was recovered at
09:02, 10:02, 11:02 and 12:02, a full cross-device copy apiece. A file whose
issue the library already has is done, whatever it is called.

**It only touches what nothing else claims.** A file belonging to a download
still in the queue is left alone; it may still be being written. Files also
have to have stopped changing for `WATCHED_FOLDER_SETTLE_SECONDS` before they
are considered, the same bar the watched folder uses.

**It never creates a volume, and never deletes what it did not import.** It
reuses `import_loose_files`, so a file it cannot match to a volume already in
the library stays exactly where it is.
"""

from __future__ import annotations

from os.path import basename, dirname, splitext
from re import compile
from typing import Any, Callable, Dict, List, Set, Union

from backend.base.logging import LOGGER
from backend.features.watched_folder_import import (WatchedFolderImportSummary,
                                                    import_loose_files)
from backend.internals.settings import Settings

RECOVERY_INTERVAL_SECONDS = 3600
"The interval the task runs at, seeded into `task_intervals`."


def files_in_use() -> Set[str]:
    """Every path the download queue is currently working with.

    Returns:
        Set[str]: The paths to leave alone. Empty if the queue cannot be
            read, which deliberately errs towards importing nothing rather
            than towards touching a file something else is using -- see the
            caller, which treats an empty answer as a reason to skip.
    """
    from backend.features.download_queue import DownloadHandler

    in_use: Set[str] = set()
    for download in DownloadHandler().queue:
        for filepath in (getattr(download, 'files', None) or ()):
            if isinstance(filepath, str):
                in_use.add(filepath)

        original = getattr(download, '_original_files', None) or ()
        for filepath in original:
            if isinstance(filepath, str):
                in_use.add(filepath)

    return in_use


def still_missing(
    filepaths: List[str],
    index: Union[Any, None] = None
) -> List[str]:
    """Narrow a list of files to those whose issue the library still lacks.

    Args:
        filepaths (List[str]): Candidate files from the download folder.

        index (Union[Any, None], optional): A prepared `LibraryIndex`, built
            once for the pass. Defaults to None, meaning build one.

    Returns:
        List[str]: The ones worth importing. A file that cannot be placed is
            kept, because `import_loose_files` leaves an unmatched file alone
            anyway and deciding here would be a second, quieter place to get
            that wrong.
    """
    from backend.base.file_extraction import extract_filename_data
    from backend.base.helpers import check_overlapping_issues
    from backend.features.release_feed import wanted_issues_of
    from backend.features.watched_folder_import import (
        LibraryIndex, match_parsed_to_library_volume)

    if index is None:
        index = LibraryIndex()

    try:
        fetched_for = downloads_by_job_name()
    except Exception:
        LOGGER.exception(
            'Could not read what these downloads were fetched for; '
            'narrowing on filenames alone: '
        )
        fetched_for = {}

    wanted: Dict[int, List[Any]] = {}
    keep: List[str] = []
    for filepath in filepaths:
        try:
            parsed = extract_filename_data(filepath)
            # `quiet`: the import pass right after this one matches every
            # one of these files again and reports what it could not place,
            # grouped by the volumes competing for it. Saying it here too
            # put a line per stuck file back in the log that the grouped
            # report exists to replace.
            volume_id = match_parsed_to_library_volume(
                parsed, index, filepath, quiet=True
            )
            if volume_id is None:
                volume_id = volume_it_was_fetched_for(filepath, fetched_for)
        except Exception:
            LOGGER.exception(
                'Could not work out what %s is; leaving it to the import: ',
                filepath)
            keep.append(filepath)
            continue

        if volume_id is None:
            keep.append(filepath)
            continue

        if volume_id not in wanted:
            wanted[volume_id] = wanted_issues_of(volume_id)

        covers = parsed.get('issue_number')
        if covers is None:
            # A special or a whole-volume file: no issue number to compare,
            # so fall back to whether the volume wants anything at all.
            if wanted[volume_id]:
                keep.append(filepath)
            continue

        if any(
            check_overlapping_issues(number, covers)
            for _, number in wanted[volume_id]
        ):
            keep.append(filepath)

    return keep


# SABnzbd appends `.1`, `.2` and so on to a job folder whose name is
# already taken, which is exactly what happens when the same release is
# fetched again -- Flash Gordon 017 was in the download folder eight times
# on 2026-09-04, as `...-Empire]`, `...-Empire].1` and so on up to `.7`.
# The job's name is what history recorded, so the suffix comes off before
# the lookup.
RETRY_SUFFIX = compile(r'\.\d+$')


def downloads_by_job_name() -> Dict[str, Union[int, None]]:
    """What each recorded download was fetched on behalf of, by job name.

    Args:
        None.

    Returns:
        Dict[str, Union[int, None]]: Job name to volume ID. A name two
            different volumes were both recorded against maps to None: the
            record does not settle it either, and a guess here would be no
            better than a guess from the filename.
    """
    from backend.internals.db import get_db

    fetched_for: Dict[str, Union[int, None]] = {}
    for job_name, volume_id in get_db().execute("""
        SELECT DISTINCT file_title, volume_id
        FROM download_history
        WHERE file_title IS NOT NULL
            AND volume_id IS NOT NULL;
    """):
        if fetched_for.setdefault(job_name, volume_id) != volume_id:
            fetched_for[job_name] = None

    return fetched_for


def volume_it_was_fetched_for(
    filepath: str,
    fetched_for: Dict[str, Union[int, None]]
) -> Union[int, None]:
    """Which volume Kapowarr asked for this file, if it asked for it at all.

    A release Kapowarr grabbed was grabbed on behalf of one volume, and
    that was written down at the time. The importer then throws it away and
    re-derives the volume from the filename, which is why a file whose name
    fits two runs of a series sits in the download folder for days while
    the issue stays wanted and the same release is fetched again.

    Args:
        filepath (str): The file in the download folder.

        fetched_for (Dict[str, Union[int, None]]): The recorded downloads,
            from `downloads_by_job_name`.

    Returns:
        Union[int, None]: The volume's ID, or None if nothing was recorded
            for it -- a file that arrived some other way, or one whose job
            name two volumes share.
    """
    # The job name is the folder SABnzbd made for it; a client that wrote
    # the file straight into the download folder leaves the file's own
    # name as the only candidate.
    candidates = [
        basename(dirname(filepath)),
        splitext(basename(filepath))[0]
    ]
    for name in candidates:
        if not name:
            continue
        for attempt in (name, RETRY_SUFFIX.sub('', name)):
            if attempt in fetched_for:
                return fetched_for[attempt]

    return None


def recover_orphaned_downloads(
    should_stop: Union[Callable[[], bool], None] = None
) -> WatchedFolderImportSummary:
    """Import finished downloads that never made it out of the download folder.

    Args:
        should_stop (Union[Callable[[], bool], None], optional): Polled
            between volumes so a stop takes effect at a safe boundary.
            Defaults to None.

    Returns:
        WatchedFolderImportSummary: What the pass did.
    """
    download_folder = Settings().sv.download_folder

    try:
        in_use = files_in_use()
    except Exception:
        # Better to do nothing this pass than to move a file out from under a
        # download that is still running. There will be another pass.
        LOGGER.exception(
            'Could not work out which downloads are in flight; leaving the '
            'download folder alone this pass: '
        )
        return WatchedFolderImportSummary(
            imported=0, unmatched=0, unsettled=0, skipped=0, volumes=0,
            errors=0
        )

    try:
        fetched_for = downloads_by_job_name()
    except Exception:
        # Worth continuing without: the filename still places most files,
        # and the ones it cannot place were already being left alone.
        LOGGER.exception(
            'Could not read what these downloads were fetched for; falling '
            'back to filenames alone: '
        )
        fetched_for = {}

    return import_loose_files(
        download_folder,
        should_stop,
        leave_alone=in_use,
        description='Orphaned downloads',
        # Never move: something may still be seeding out of this folder.
        leave_original=True,
        # And never re-import what the library already has: see
        # `still_missing`.
        narrow=still_missing,
        # These are Kapowarr's own downloads, so when a name fits two runs
        # of a series there is a record of which one it was asked for.
        resolve=lambda filepath: volume_it_was_fetched_for(
            filepath, fetched_for
        )
    )


def describe_recovery(summary: WatchedFolderImportSummary) -> str:
    """A one-line, user-facing description of a pass, for the task message.

    Args:
        summary (WatchedFolderImportSummary): What the pass did.

    Returns:
        str: The description.
    """
    if not any(summary.values()):
        return 'Nothing left behind in the download folder'

    message = (
        f"Recovered {summary['imported']} orphaned download(s) into "
        f"{summary['volumes']} volume(s) · {summary['unmatched']} unmatched · "
        f"{summary['unsettled']} still in flight · {summary['skipped']} skipped"
    )
    if summary.get('errors'):
        message += f" · {summary['errors']} failed"
    return message
