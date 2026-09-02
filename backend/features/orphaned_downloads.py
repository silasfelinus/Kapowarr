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

**It only touches what nothing else claims.** A file belonging to a download
still in the queue is left alone; it may still be being written. Files also
have to have stopped changing for `WATCHED_FOLDER_SETTLE_SECONDS` before they
are considered, the same bar the watched folder uses.

**It never creates a volume, and never deletes what it did not import.** It
reuses `import_loose_files`, so a file it cannot match to a volume already in
the library stays exactly where it is.
"""

from __future__ import annotations

from typing import Callable, Set, Union

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

    return import_loose_files(
        download_folder,
        should_stop,
        leave_alone=in_use,
        description='Orphaned downloads',
        # Never move: something may still be seeding out of this folder.
        leave_original=True
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
