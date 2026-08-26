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
from time import monotonic, sleep
from typing import Any, Dict, List, Optional, Set, Tuple

from backend.base.custom_exceptions import CVRateLimitReached
from backend.base.definitions import CVFileMapping, FilenameData
from backend.features.library_import import (
    CONTINUOUS_IMPORT_RATE_LIMIT_BACKOFF,
    CV_REQUEST_DELAY,
    ContinuousLibraryImport,
    _collect_unimported_files,
    _match_file_groups,
    create_groups,
    import_library,
    match_identifies_a_volume,
)
from backend.features.library_import_context import apply_series_run_context
from backend.features.library_import_diagnostics import (
    append_review_postmortem,
    build_review_diagnostics,
    get_postmortem_path,
)
from backend.features.library_import_metadata import (
    filter_library_import_files,
    is_library_import_artifact,
    select_local_series_metadata,
)
from backend.features.library_import_normalization import (
    folder_search_query,
    normalize_import_filename_data,
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
    get_running_job,
    mark_folder_pending,
    mark_folder_processing,
    mark_folder_result,
    mark_job_complete,
    mark_job_paused,
    mark_job_running,
)
from backend.features.metadata import search_volumes_everywhere
from backend.features.tasks import Task, task_library
from backend.internals.server import TaskStatusEvent, WebSocket


# ComicVine documents a per-resource hourly limit. Search requests and the
# metadata fetches triggered by Library.add() use independent clocks because
# they hit different resources. This also closes the old fast-path hole where
# trusted Mylar cvinfo/series.json skipped the paced search and could then
# hammer volume/issue metadata requests.
#
# The interval itself is no longer a constant. It used to sit at a flat 30
# seconds -- comfortably short of the documented rate, for a limit it might
# never have come near, and paid for in every hour of every import.
# `CV_REQUEST_DELAY` starts at the documented rate and widens only when
# ComicVine actually objects.

# A folder containing many differently parsed series is often an organizer
# longbox rather than one volume per directory. Before spending one paced search
# per parsed title, try the clean folder name once and reuse that result pool for
# any groups it can resolve confidently. Unresolved groups still get their own
# normal search, so this optimization cannot turn a broad result miss into a
# false "no candidate" hold.
LARGE_FOLDER_SHARED_SEARCH_MIN_TITLES = 8


class PersistentContinuousLibraryImport(ContinuousLibraryImport):
    """Continuous import with durable folder and review checkpoints."""

    def __init__(self, job_id: Optional[int] = None) -> None:
        super().__init__()
        self.job_id = job_id
        return

    def request_stop(self) -> None:
        """Acknowledge a user stop immediately, then exit at the next safe check."""
        super().request_stop()
        if 'stop requested' not in self.message.lower():
            if self.message:
                self.message += ' · stop requested; pausing safely'
            else:
                self.message = 'Continuous import · stop requested; pausing safely'
            WebSocket().emit(TaskStatusEvent(self.message))
        return

    @classmethod
    def restore_running_job(
        cls
    ) -> Optional['PersistentContinuousLibraryImport']:
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
            'review_postmortem_file': get_postmortem_path(),
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

    def _interruptible_wait(self, seconds: float) -> bool:
        """Sleep in short slices so a user pause is observed promptly."""
        remaining = max(float(seconds), 0.0)
        while remaining > 0:
            if self._should_stop():
                return False
            step = min(1.0, remaining)
            sleep(step)
            remaining -= step
        return not self._should_stop()

    def _wait_for_resource_slot(self, clock_key: str) -> bool:
        """Wait for one paced ComicVine resource lane without hiding Stop."""
        last_started = self.cv_request_clock.get(clock_key)
        if last_started is not None:
            elapsed = monotonic() - last_started
            remaining_delay = max(
                CV_REQUEST_DELAY.current() - elapsed,
                0.0
            )
            if remaining_delay and not self._interruptible_wait(remaining_delay):
                return False

        if self._should_stop():
            return False
        self.cv_request_clock[clock_key] = monotonic()
        return True

    def _wait_for_metadata_slot(self) -> bool:
        """Pace ComicVine volume/issue fetch starts independently of searches."""
        return self._wait_for_resource_slot('last_metadata_started')

    def _shared_search_results(
        self,
        folder: str,
        title_count: int
    ) -> Optional[Tuple[str, List[Any]]]:
        """Search a large organizer folder once before title-by-title fallback."""
        query = folder_search_query(folder)
        if not query:
            return None

        if query in self.search_cache:
            return query, self.search_cache[query]

        self._emit_persistent_status(
            f'{basename(folder)}: shared search for {title_count} parsed titles'
        )
        if not self._wait_for_resource_slot('last_started'):
            return None

        results = asyncio_run(search_volumes_everywhere(query))
        # The folder query is itself a legitimate exact title query when a group
        # has the same series name, so retaining it under its own key is safe.
        self.search_cache[query] = results
        return query, results

    def _match_search_groups(
        self,
        search_groups: Dict[int, Dict[str, FilenameData]],
        folder: str
    ) -> Optional[Tuple[Dict[int, Dict[str, Any]], Dict[str, List[Any]]]]:
        """Match groups with stop-aware pacing and large-folder result reuse.

        Returns the matches plus a folder-local search cache for run-context
        scoring. Broad folder results are not persisted under unrelated title
        keys, preventing a later folder from accidentally reusing an incomplete
        broad result set as though it were an exact-title search.
        """
        title_to_groups: Dict[str, Dict[int, Dict[str, FilenameData]]] = {}
        for group_number, files in search_groups.items():
            title = next(iter(files.values()))['series'].lower()
            title_to_groups.setdefault(title, {})[group_number] = files

        searched_matches: Dict[int, Dict[str, Any]] = {}
        context_cache: Dict[str, List[Any]] = {}
        pending_titles = dict(title_to_groups)
        total_titles = len(title_to_groups)

        if total_titles >= LARGE_FOLDER_SHARED_SEARCH_MIN_TITLES:
            shared = self._shared_search_results(folder, total_titles)
            if shared is None:
                if self._should_stop():
                    return None
            else:
                shared_query, shared_results = shared
                for title, title_groups in list(pending_titles.items()):
                    # Evaluate this title against the already-fetched broad pool
                    # without inserting that pool into the persistent exact-title
                    # cache unless the broad query actually equals the title.
                    temporary_cache = {title: shared_results}
                    matches = asyncio_run(_match_file_groups(
                        title_groups,
                        only_english=True,
                        request_delay=0.0,
                        search_cache=temporary_cache,
                        require_confident_match=True
                    ))
                    if matches and all(
                        match.get('id') is not None
                        for match in matches.values()
                    ):
                        searched_matches.update(matches)
                        context_cache[title] = shared_results
                        pending_titles.pop(title)
                    elif title == shared_query:
                        # This *is* the exact query for this title. Preserve it so
                        # the fallback below does not spend the same request twice.
                        context_cache[title] = shared_results

        already_resolved = total_titles - len(pending_titles)
        for offset, (title, title_groups) in enumerate(
            pending_titles.items(),
            start=1
        ):
            if self._should_stop():
                return None

            self._emit_persistent_status(
                f'{basename(folder)}: matching title '
                f'{already_resolved + offset}/{total_titles} · {title[:80]}'
            )

            # Cached titles spend no request budget, so only gate a title when
            # the shared cache proves that a real provider search will start.
            if title not in self.search_cache:
                if not self._wait_for_resource_slot('last_started'):
                    return None

            matches = asyncio_run(_match_file_groups(
                title_groups,
                only_english=True,
                request_delay=0.0,
                search_cache=self.search_cache,
                require_confident_match=True
            ))
            searched_matches.update(matches)
            context_cache[title] = self.search_cache.get(title, [])

        return searched_matches, context_cache

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
        return filter_library_import_files({
            filepath: file_data
            for filepath, file_data in all_files.items()
            if file_to_folder.get(filepath) == folder
        })

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
    ) -> Tuple[Dict[str, Dict[str, FilenameData]], bool]:
        """Resolve the durable job and report whether it is being resumed."""
        if self.job_id is not None:
            mark_job_running(self.job_id)
            return {}, True

        # A user-paused job does not auto-resume at application startup, but the
        # next explicit Continuous Auto-Import click continues it rather than
        # throwing away its checkpoints and review queue.
        paused_job = get_paused_job()
        if paused_job is not None:
            self.job_id = int(paused_job['id'])
            mark_job_running(self.job_id)
            return {}, True

        all_files, file_to_folder = _collect_unimported_files()
        folder_to_files: Dict[str, Dict[str, FilenameData]] = {}
        for filepath, file_data in all_files.items():
            if is_library_import_artifact(filepath):
                continue
            folder_to_files.setdefault(
                file_to_folder[filepath], {}
            )[filepath] = file_data

        self.job_id = create_job(folder_to_files.keys())
        return folder_to_files, False

    @staticmethod
    def _match_groups_with_local_metadata(
        folder: str,
        group_to_files: Dict[int, Dict[str, FilenameData]]
    ) -> Tuple[Dict[int, Dict[str, Any]], Dict[int, Dict[str, FilenameData]]]:
        """Resolve safe exact sidecar matches before spending search requests."""
        local_matches: Dict[int, Dict[str, Any]] = {}
        search_groups: Dict[int, Dict[str, FilenameData]] = {}

        for group_number, files in group_to_files.items():
            metadata = select_local_series_metadata(folder, files)
            if metadata is None:
                search_groups[group_number] = files
                continue

            year = metadata.get('year')
            title = metadata['name']
            local_matches[group_number] = {
                'id': metadata['comicvine_id'],
                'title': f"{title} ({year})" if year is not None else title,
                'issue_count': metadata.get('issue_count'),
                'link': None,
                'local_metadata': metadata.get('source')
            }

        return local_matches, search_groups

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
            initial_folder_files, resumed = self._start_or_resume_job()
            if self.job_id is None:
                return

            self._emit_persistent_status(
                'resuming saved job' if resumed else 'starting'
            )

            for folder_position, folder in get_pending_folders(self.job_id):
                current_folder = folder
                if self._should_stop():
                    break

                mark_folder_processing(self.job_id, folder)
                folder_files = initial_folder_files.get(folder)
                if folder_files is None:
                    folder_files = self._load_folder_files(folder)
                else:
                    folder_files = filter_library_import_files(folder_files)
                folder_files = normalize_import_filename_data(folder_files)

                # A folder can disappear, move, become fully imported, or turn
                # out to contain only artwork/cache files while a job is paused.
                # Those are completed checkpoints, not errors or review holds.
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
                        group_to_cv, search_groups = (
                            self._match_groups_with_local_metadata(
                                folder,
                                group_to_files
                            )
                        )
                        if search_groups:
                            search_result = self._match_search_groups(
                                search_groups,
                                folder
                            )
                            if search_result is None:
                                mark_folder_pending(self.job_id, folder)
                                break
                            searched_matches, context_search_cache = search_result
                            searched_matches = apply_series_run_context(
                                search_groups,
                                searched_matches,
                                context_search_cache,
                                only_english=True,
                            )
                            group_to_cv.update(searched_matches)

                        matches: List[CVFileMapping] = []
                        review_items: List[Dict[str, Any]] = []
                        folder_review_reasons: Set[str] = set()

                        for group_number, files in group_to_files.items():
                            cv_match = group_to_cv[group_number]
                            if not match_identifies_a_volume(cv_match):
                                review_reason = cv_match.get(
                                    'review_reason',
                                    REVIEW_REASON_NO_CANDIDATE
                                )
                                folder_review_reasons.add(review_reason)

                                search_query = next(
                                    iter(files.values())
                                )['series'].lower()
                                diagnostics = build_review_diagnostics(
                                    files,
                                    self.search_cache.get(search_query, []),
                                    only_english=True,
                                    review_reason=review_reason
                                )
                                append_review_postmortem(
                                    self.job_id,
                                    folder,
                                    folder_position,
                                    group_number,
                                    diagnostics
                                )

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
                                    'id': cv_match['id'],
                                    'provider_id': cv_match.get(
                                        'provider_id', 'comicvine'
                                    ),
                                    'external_id': cv_match.get('external_id')
                                }
                                for filepath in files
                            )

                        imported_volumes = 0
                        metadata_wait_stopped = False
                        if matches:
                            # By provider identity, not ComicVine ID: a GCD
                            # match has none, so keying on `id` put every GCD
                            # volume in the batch into one `None` bucket and
                            # imported them as though they were one volume.
                            matches_by_id: Dict[Any, List[CVFileMapping]] = {}
                            for match in matches:
                                matches_by_id.setdefault(
                                    (
                                        match.get('provider_id')
                                        or 'comicvine',
                                        match.get('external_id')
                                        if match.get('external_id') is not None
                                        else match['id']
                                    ), []
                                ).append(match)

                            total_import_volumes = len(matches_by_id)
                            for import_index, volume_matches in enumerate(
                                matches_by_id.values(),
                                start=1
                            ):
                                self._emit_persistent_status(
                                    f'{basename(folder)}: importing volume '
                                    f'{import_index}/{total_import_volumes}'
                                )
                                if not self._wait_for_metadata_slot():
                                    mark_folder_pending(self.job_id, folder)
                                    metadata_wait_stopped = True
                                    break

                                import_result = import_library(
                                    volume_matches,
                                    rename_files=False
                                )
                                imported_volumes += len(
                                    import_result['imported']
                                )

                        if metadata_wait_stopped:
                            break

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
                        widened = CV_REQUEST_DELAY.record_block()
                        self._emit_persistent_status(
                            'ComicVine rate limit reached; cooling down for '
                            f'15 minutes, then {widened:.0f}s between requests'
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
                self._emit_persistent_status(
                    'paused by user; safe to resume later'
                )

            elif self.stop:
                # Application/container shutdown is not a user pause. Reset an
                # in-flight folder but intentionally leave the job marked running
                # so Kapowarr can auto-resume it on the next process startup.
                mark_job_running(self.job_id)

            else:
                mark_job_complete(self.job_id)
                self._emit_persistent_status('complete')

        except Exception as error:
            if self.job_id is not None and not self.stop:
                # A real worker error should not spin forever on every page load.
                # Preserve all checkpoints and wait for an explicit retry.
                mark_job_paused(
                    self.job_id,
                    f'{type(error).__name__}: {error}'
                )
            # A shutdown that interrupts the worker is deliberately left alone.
            # Whatever it raises on the way down -- a closed database, a torn
            # down app context -- is a consequence of stopping, not a reason to
            # hold the job for a human. The row is already `running`, and that
            # is exactly the state startup resumes from; pausing it here is
            # what made a restart cost the rest of the import.
            raise

        finally:
            if (
                current_folder is not None
                and self.job_id is not None
                and self._should_stop()
            ):
                mark_folder_pending(self.job_id, current_folder)

        return


class RecheckContinuousLibraryImport(Task):
    """Discard stale holds and build a fresh paused snapshot for re-evaluation."""

    stop = False
    message = ''
    action = 'recheck_continuous_library_import'
    display_title = 'Re-evaluate Library Import Holds'
    category = ''

    @property
    def volume_id(self) -> None:
        return None

    @property
    def issue_id(self) -> None:
        return None

    def __init__(self) -> None:
        return

    def run(self) -> None:
        """Retire saved paused passes, rescan current paths, and stage a new pass.

        This task is intentionally queued behind a running continuous importer.
        The UI requests a cooperative stop first, then queues this task. By the
        time it executes, the old job is paused at a folder boundary. Every old
        paused pass is retired so a reset can never accidentally resume stale
        review rows if the fresh filesystem scan later fails.
        """
        self.message = 'Discarding stale review decisions...'
        WebSocket().emit(TaskStatusEvent(self.message))

        paused_job = get_paused_job()
        while paused_job is not None:
            mark_job_complete(int(paused_job['id']))
            paused_job = get_paused_job()

        self.message = 'Scanning current unimported folders...'
        WebSocket().emit(TaskStatusEvent(self.message))
        all_files, file_to_folder = _collect_unimported_files()
        folders: List[str] = []
        seen_folders: Set[str] = set()
        for filepath in all_files:
            if is_library_import_artifact(filepath):
                continue
            folder = file_to_folder[filepath]
            if folder in seen_folders:
                continue
            seen_folders.add(folder)
            folders.append(folder)

        job_id = create_job(folders)
        # A reset should only begin because the user explicitly clicked it. Keep
        # the new snapshot paused until the UI starts Continuous Auto-Import.
        mark_job_paused(job_id)

        self.message = (
            f'Ready to re-evaluate {len(folders)} current unimported folders'
        )
        WebSocket().emit(TaskStatusEvent(self.message))
        return


# Replace the in-memory implementation registered by library_import.py. The API
# continues using the same command/action string and needs no special route.
task_library[PersistentContinuousLibraryImport.action] = (
    PersistentContinuousLibraryImport
)
task_library[RecheckContinuousLibraryImport.action] = (
    RecheckContinuousLibraryImport
)
