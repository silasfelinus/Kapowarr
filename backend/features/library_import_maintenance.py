# -*- coding: utf-8 -*-

"""Maintenance tasks for the durable Continuous Library Import queue.

Re-evaluating Review Holds and rediscovering every untracked folder are two
very different operations.  Keep them separate here so a small review backlog
cannot silently turn into a whole-library pass.
"""

from typing import Iterable, List, Set

from backend.features.library_import import _collect_unimported_files
from backend.features.library_import_metadata import is_library_import_artifact
from backend.features.library_import_state import (
    create_job,
    get_outstanding_review_items,
    get_paused_job,
    mark_job_complete,
    mark_job_paused,
)
from backend.features.tasks_core import Task, task_library
from backend.internals.server import TaskStatusEvent, WebSocket


def _emit(task: Task, message: str) -> None:
    task.message = message
    WebSocket().emit(TaskStatusEvent(message))
    return


def _retire_paused_jobs() -> None:
    """Retire snapshots replaced by an explicitly requested maintenance pass."""
    paused_job = get_paused_job()
    while paused_job is not None:
        mark_job_complete(int(paused_job['id']))
        paused_job = get_paused_job()
    return


def _stage_paused_job(folders: Iterable[str]) -> int:
    """Create the stable snapshot without starting it before the UI is ready."""
    job_id = create_job(folders)
    mark_job_paused(job_id)
    return job_id


def _live_review_folders() -> List[str]:
    """Return unique folders that still have actionable Review Holds."""
    folders: List[str] = []
    seen: Set[str] = set()
    for item in get_outstanding_review_items():
        folder = str(item.get('folder') or '')
        if not folder or folder in seen:
            continue
        seen.add(folder)
        folders.append(folder)
    return folders


def _current_untracked_folders() -> List[str]:
    """Return every current unimported scan folder in configured roots."""
    all_files, file_to_folder = _collect_unimported_files()
    folders: List[str] = []
    seen: Set[str] = set()
    for filepath in all_files:
        if is_library_import_artifact(filepath):
            continue
        folder = file_to_folder[filepath]
        if folder in seen:
            continue
        seen.add(folder)
        folders.append(folder)
    return folders


class RecheckContinuousLibraryImport(Task):
    """Build a new pass from only the Review Holds that are still actionable."""

    stop = False
    message = ''
    action = 'recheck_continuous_library_import'
    display_title = 'Re-evaluate Library Import Holds'
    category = ''

    @property
    def volume_id(self):
        return None

    @property
    def issue_id(self):
        return None

    def __init__(self) -> None:
        return

    def run(self) -> None:
        _emit(self, 'Discarding stale review decisions...')
        _retire_paused_jobs()

        _emit(self, 'Collecting current Review Holds...')
        folders = _live_review_folders()
        _stage_paused_job(folders)

        _emit(
            self,
            f'Ready to re-evaluate {len(folders)} review-held folders'
        )
        return


class RescanContinuousLibraryImport(Task):
    """Build a fresh pass from every folder currently untracked by Kapowarr."""

    stop = False
    message = ''
    action = 'rescan_continuous_library_import'
    display_title = 'Rescan Untracked Library'
    category = ''

    @property
    def volume_id(self):
        return None

    @property
    def issue_id(self):
        return None

    def __init__(self) -> None:
        return

    def run(self) -> None:
        _emit(self, 'Discarding stale import snapshot...')
        _retire_paused_jobs()

        _emit(self, 'Scanning current unimported folders...')
        folders = _current_untracked_folders()
        _stage_paused_job(folders)

        _emit(
            self,
            f'Ready to scan {len(folders)} current unimported folders'
        )
        return


def register_library_import_maintenance_tasks() -> None:
    """Override the legacy reset command and register the explicit full rescan."""
    task_library[RecheckContinuousLibraryImport.action] = (
        RecheckContinuousLibraryImport
    )
    task_library[RescanContinuousLibraryImport.action] = (
        RescanContinuousLibraryImport
    )
    return
