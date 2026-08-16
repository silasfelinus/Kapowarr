# -*- coding: utf-8 -*-

"""Restart-safe Continuous Library Import task.

This lives beside the upstream-shaped importer so the fork's persistence layer
stays easy to identify and remove. Importing this module replaces the task
registry entry for ``continuous_library_import`` with the durable subclass.
"""

from __future__ import annotations

from asyncio import run as asyncio_run
from glob import escape as glob_escape
from os.path import basename, isfile, splitext
from time import sleep
from typing import Any, Dict, List, Optional, Set

from backend.base.custom_exceptions import CVRateLimitReached
from backend.base.definitions import CVFileMapping, FilenameData
from backend.features.library_import import (
    CONTINUOUS_IMPORT_CV_DELAY,
    CONTINUOUS_IMPORT_RATE_LIMIT_BACKOFF,
    ContinuousLibraryImport,
    _collect_unimported_files,
    _match_file_groups,
    create_groups,
    import_library,
)
from backend.features.library_import_policy import (
    REVIEW_REASON_NO_CANDIDATE,
    REVIEW_REASON_TIE,
    REVIEW_REASON_WEAK_SCORE,
)
from backend.features.library_import_state import (
    create_job,
    get_job_details,
    get_job_summary,
    get_paused_job,
    get_pending_folders,
    get_review_items,
    get_running_job,
    mark_folder_pending,
    mark_folder_processing,
    mark_folder_result,
    mark_job_complete,
    mark_job_paused,
    mark_job_running,
)
from backend.features.tasks import task_library
from backend.internals.server import TaskStatusEvent, WebSocket


class PersistentContinuousLibraryImport(ContinuousLibraryImport):
    """Continuous import with durable folder and review checkpoints."""

    def __init__(self, job_id: Optional[int] = None) -> None:
        super().__init__()
        self.job_id = job_id
        return

    @classmethod
    def restore_running_job(cls) -> Optional['PersistentContinuousLibraryImport']:
        """Return the unfinished job that should auto-resume after app restart."""
        job = get_running_job()
        if job is None:
            return None
        return cls(job_id=int(job['id']))

    def get_task_details(self) -> Dict[str, Any]:
        """Expose the durable review queue through the existing task API."""
        if self.job_id is None:
            return super().get_task_details()

        details = get_job_details(self.job_id)
        return {
            'review_items': details['review_items'],
            'stop_requested': self.stop_requested,
            'job': {
                key: value
                for key, value in details.items()
                if key != 'review_items'
            }
        }

    def _should_stop(self) -> bool:
        # ``stop_requested`` is the user's Stop Import button. ``stop`` is the
        # TaskHandler's process-shutdown signal. They intentionally have
        # different persistence semantics at the end of run().
        return self.stop_requested or self.stop

    def _emit_persistent_status(self, detail: str = '') -> None:
        if self.job_id is None:
            return

        summary = get_job_summary(self.job_id)
        self.message = (
            f"Continuous import: {summary['checked_folders']}/"
            f"{summary['total_folders']} folders checked · "
            f"{summary['imported_volumes']} volumes imported · "
            f"{summary['review_folders']} need review · "
            f"{summary['remaining_folders']} left"
        )

        review_labels = (
            (REVIEW_REASON_TIE, 'tied'),
            (REVIEW_REASON_WEAK_SCORE, 'weak'),
            (REVIEW_REASON_NO_CANDIDATE, 'no candidate')
        )
        review_breakdown = ' · '.join(
            f"{summary['review_reasons'][reason]} {label}"
            for reason, label in review_labels
            if summary['review_reasons'].get(reason)
        )
        if review_breakdown:
            self.message += f' · review holds: {review_breakdown}'

        if detail:
            self.message += f' · {detail}'
        WebSocket().emit(TaskStatusEvent(self.message))
        return

    def _build_review_group(
        self,
        folder: str,
        folder_position: int,
        group_number: int,
        files: Dict[str, FilenameData],
        cv_match: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """Build serializable review rows for one held filename group."""
        review_candidate = cv_match.get('review_candidate') or {
            'id': None,
            'title': None,
            'issue_count': None,
            'link': None
        }
        review_reason = cv_match.get(
            'review_reason',
            REVIEW_REASON_NO_CANDIDATE
        )
        durable_group_number = (
            f'continuous-review-{self.job_id}-'
            f'{folder_position}-{group_number}'
        )

        return [
            {
                'filepath': filepath,
                'file_title': (
                    splitext(basename(filepath))[0]
                    if isfile(filepath) else
                    basename(filepath)
                ),
                'cv': dict(review_candidate),
                'group_number': durable_group_number,
                'folder': folder,
                'review_reason': review_reason
            }
            for filepath in files
        ]

    @staticmethod
    def _load_folder_files(folder: str) -> Dict[str, FilenameData]:
        """Rebuild only one pending folder after a process restart."""
        all_files, file_to_folder = _collect_unimported_files(
            folder_filter=glob_escape(folder)
        )
        return {
            filepath: file_data
            for filepath, file_data in all_files.items()
            if file_to_folder.get(filepath) == folder
        }

    @staticmethod
    def _primary_review_reason(reasons: Set[str]) -> str:
        return next((
            reason
            for reason in (
                REVIEW_REASON_NO_CANDIDATE,
                REVIEW_REASON_WEAK_SCORE,
                REVIEW_REASON_TIE
            )
            if reason in reasons
        ), REVIEW_REASON_NO_CANDIDATE)

    def _start_or_resume_job(
        self
    ) -> Dict[str, Dict[str, FilenameData]]:
        """Resolve the durable job and return an optional first-run file cache."""
        if self.job_id is not None:
            mark_job_running(self.job_id)
            return {}

        # A user-paused job does not auto-resume at application startup, but the
        # next explicit Continuous Auto-Import click continues it rather than
        # throwing away its checkpoints and review queue.
        paused_job = get_paused_job()
        if paused_job is not None:
            self.job_id = int(paused_job['id'])
            mark_job_running(self.job_id)
            return {}

        all_files, file_to_folder = _collect_unimported_files()
        folder_to_files: Dict[str, Dict[str, FilenameData]] = {}
        for filepath, file_data in all_files.items():
            folder_to_files.setdefault(
                file_to_folder[filepath], {}
            )[filepath] = file_data

        self.job_id = create_job(folder_to_files.keys())
        return folder_to_files

    def run(self) -> None:
        """Continue a persistent folder snapshot until complete, paused, or exit.

        Each folder changes from pending -> processing -> done/review. If the
        process disappears while a folder is processing, startup changes only
        that row back to pending. Already completed folders and held review rows
        stay committed in SQLite and are never reconstructed from task memory.
        """
        initial_folder_files: Dict[str, Dict[str, FilenameData]] = {}
        current_folder: Optional[str] = None

        try:
            had_job = self.job_id is not None
            initial_folder_files = self._start_or_resume_job()
            if self.job_id is None:
                return

            self._emit_persistent_status(
                'resuming saved job' if had_job or not initial_folder_files else 'starting'
            )

            for folder_position, folder in get_pending_folders(self.job_id):
                current_folder = folder
                if self._should_stop():
                    break

                mark_folder_processing(self.job_id, folder)
                folder_files = initial_folder_files.get(folder)
                if folder_files is None:
                    folder_files = self._load_folder_files(folder)

                # A folder can disappear, move, or become fully imported while a
                # job is paused. That is a completed checkpoint, not an error.
                if not folder_files:
                    mark_folder_result(
                        self.job_id,
                        folder,
                        imported_volumes=0,
                        review_reason=None,
                        review_items=[]
                    )
                    self._emit_persistent_status()
                    current_folder = None
                    continue

                while True:
                    if self._should_stop():
                        mark_folder_pending(self.job_id, folder)
                        break

                    try:
                        group_to_files = create_groups(folder_files)
                        group_to_cv = asyncio_run(_match_file_groups(
                            group_to_files,
                            only_english=True,
                            request_delay=CONTINUOUS_IMPORT_CV_DELAY,
                            search_cache=self.search_cache,
                            require_confident_match=True,
                            request_clock=self.cv_request_clock
                        ))

                        matches: List[CVFileMapping] = []
                        review_items: List[Dict[str, Any]] = []
                        folder_review_reasons: Set[str] = set()

                        for group_number, files in group_to_files.items():
                            cv_match = group_to_cv[group_number]
                            if cv_match['id'] is None:
                                review_reason = cv_match.get(
                                    'review_reason',
                                    REVIEW_REASON_NO_CANDIDATE
                                )
                                folder_review_reasons.add(review_reason)
                                review_items.extend(self._build_review_group(
                                    folder,
                                    folder_position,
                                    group_number,
                                    files,
                                    cv_match
                                ))
                                continue

                            matches.extend(
                                {
                                    'filepath': filepath,
                                    'id': cv_match['id']
                                }
                                for filepath in files
                            )

                        imported_volumes = 0
                        if matches:
                            import_library(matches, rename_files=False)
                            imported_volumes = len({
                                match['id']
                                for match in matches
                            })

                        mark_folder_result(
                            self.job_id,
                            folder,
                            imported_volumes=imported_volumes,
                            review_reason=(
                                self._primary_review_reason(folder_review_reasons)
                                if review_items else
                                None
                            ),
                            review_items=review_items
                        )
                        self._emit_persistent_status()
                        current_folder = None
                        break

                    except CVRateLimitReached:
                        self._emit_persistent_status(
                            'ComicVine rate limit reached; cooling down for 15 minutes'
                        )
                        for _ in range(CONTINUOUS_IMPORT_RATE_LIMIT_BACKOFF):
                            if self._should_stop():
                                break
                            sleep(1)

                        if self._should_stop():
                            mark_folder_pending(self.job_id, folder)
                            break

                if self._should_stop():
                    break

            if self.stop_requested:
                mark_job_paused(self.job_id)
                self._emit_persistent_status('paused by user; safe to resume later')

            elif self.stop:
                # Application/container shutdown is not a user pause. Reset an
                # in-flight folder but intentionally leave the job marked running
                # so Kapowarr can auto-resume it on the next process startup.
                mark_job_running(self.job_id)

            else:
                mark_job_complete(self.job_id)
                self._emit_persistent_status('complete')

        except Exception as error:
            if self.job_id is not None:
                # A real worker error should not spin forever on every page load.
                # Preserve all checkpoints and wait for an explicit retry.
                mark_job_paused(
                    self.job_id,
                    f'{type(error).__name__}: {error}'
                )
            raise

        finally:
            if (
                current_folder is not None
                and self.job_id is not None
                and self._should_stop()
            ):
                mark_folder_pending(self.job_id, current_folder)

        return


# Replace the in-memory implementation registered by library_import.py. The API
# continues using the same command/action string and needs no special route.
task_library[PersistentContinuousLibraryImport.action] = (
    PersistentContinuousLibraryImport
)
