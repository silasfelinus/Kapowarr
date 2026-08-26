# -*- coding: utf-8 -*-

"""Background task API with lane-aware scheduling.

``tasks_core`` contains the original task definitions. This wrapper keeps that
public API intact while allowing the durable Continuous Library Import to run in
its own lane instead of monopolising every unrelated task for hours.
"""

from threading import RLock, Thread
from time import monotonic, sleep, time

from backend.features.tasks_core import *
from backend.features.tasks_core import TaskHandler
from backend.features.download_queue import DownloadHandler
from backend.base.logging import LOGGER
from backend.internals.db import commit, get_db
from backend.internals.server import (TaskAddedEvent, TaskEndedEvent,
                                      TaskStatusEvent, WebSocket)


_TASK_QUEUE_LOCK = RLock()
TaskHandler.queue_lock = _TASK_QUEUE_LOCK

# These scheduled jobs should never stack duplicate copies while one is already
# queued/running. In the old single lane, a long continuous import could let
# interval ticks pile dozens of identical jobs behind it.
_DEDUPE_ACTIONS = {
    'continuous_library_import',
    'update_all',
    'weekly_pull_list_check',
    'watched_folder_import',
}


# Both of these tasks are heavyweight writers to the same library tables.
# Running them in separate lanes allowed Update All's multiprocessing scan to
# collide with a continuous import and exhaust SQLite's 10-second busy timeout.
# Keep the historical lane name for API/UI compatibility, but make Update All
# and import-queue maintenance share it so they cannot mutate one durable
# snapshot while the conveyor is still finishing its current folder.
_LIBRARY_WRITE_ACTIONS = {
    'continuous_library_import',
    'recheck_continuous_library_import',
    'rescan_continuous_library_import',
    'update_all',
}


def _task_lane(task) -> str:
    if getattr(task, 'action', '') in _LIBRARY_WRITE_ACTIONS:
        return 'continuous_import'
    return getattr(task, 'queue_lane', 'default') or 'default'


def _run_task_entry(self, entry_or_task) -> None:
    """Run a queue entry, while retaining the old direct-task test seam."""
    if isinstance(entry_or_task, dict):
        entry = entry_or_task
        task = entry['task']
    else:
        # Some internal tests and extensions call the private runner directly
        # with a Task, matching TaskHandler's historical signature. Resolve its
        # real queue entry when present so cleanup semantics stay identical.
        task = entry_or_task
        entry = next((
            queued for queued in self.queue
            if queued.get('task') is task
        ), {
            'task': task,
            'id': 0,
            'status': 'running',
            'lane': _task_lane(task),
            'thread': None,
        })

    lane = entry.get('lane', _task_lane(task))
    LOGGER.debug('Running task %s in lane %s', task.display_title, lane)
    with self.context():
        socket = WebSocket()
        history_title = task.display_title
        try:
            result = task.run()

            if not task.stop:
                if task.category == 'download' and result:
                    DownloadHandler().add_multiple(
                        (link, volume_id, issue_id, False)
                        for link, volume_id, issue_id in result
                    )
                LOGGER.info('Finished task %s', task.display_title)

        except Exception as error:
            LOGGER.exception('An error occured while trying to run a task: ')
            error_detail = str(error).strip() or type(error).__name__
            error_detail = ' '.join(error_detail.split())[:240]
            task.message = f'Failed: {error_detail}'
            history_title = f'{task.display_title} — {task.message}'
            socket.emit(TaskStatusEvent(task.message))
            sleep(1.5)

        finally:
            if not task.stop:
                try:
                    get_db().execute(
                        """
                        INSERT INTO task_history(task_name, display_title, run_at)
                        VALUES (?,?,?);
                        """,
                        (task.action, history_title, round(time()))
                    )
                    commit()
                except Exception:
                    LOGGER.exception(
                        'Failed to record task history for %s', task.display_title
                    )

            socket.emit(TaskEndedEvent(task))
            with _TASK_QUEUE_LOCK:
                if entry in self.queue:
                    self.queue.remove(entry)
            self._process_queue()


def _process_queue_lanes(self) -> None:
    """Start the oldest queued task in every currently free lane."""
    to_start = []
    with _TASK_QUEUE_LOCK:
        running_lanes = {
            entry.get('lane', 'default')
            for entry in self.queue
            if entry['status'] == 'running'
        }
        for entry in self.queue:
            lane = entry.get('lane', 'default')
            if entry['status'] != 'queued' or lane in running_lanes:
                continue
            entry['status'] = 'running'
            running_lanes.add(lane)
            to_start.append(entry['thread'])

    for thread in to_start:
        thread.start()


def _add_laned(self, task) -> int:
    lane = _task_lane(task)
    with _TASK_QUEUE_LOCK:
        if task.action in _DEDUPE_ACTIONS:
            existing = next((
                entry for entry in self.queue
                if entry['task'].action == task.action
                and entry['status'] in ('queued', 'running')
            ), None)
            if existing is not None:
                LOGGER.info(
                    'Task already queued/running: %s (%d)',
                    task.display_title, existing['id']
                )
                return existing['id']

        task_id = self.queue[-1]['id'] + 1 if self.queue else 1
        entry = {
            'task': task,
            'id': task_id,
            'status': 'queued',
            'lane': lane,
            'thread': None,
        }
        entry['thread'] = Thread(
            target=self._TaskHandler__run_task,
            args=(entry,),
            name=f'TaskThread-{task_id}'
        )
        self.queue.append(entry)

    LOGGER.info('Added task: %s (%d) [lane=%s]', task.display_title, task_id, lane)
    WebSocket().emit(TaskAddedEvent(task))
    self._process_queue()
    return task_id


def _format_entry_laned(self, entry, include_details: bool = False) -> dict:
    task = entry['task']
    result = {
        'id': entry['id'],
        'action': task.action,
        'display_title': task.display_title,
        'status': entry['status'],
        'message': task.message,
        'volume_id': task.volume_id,
        'issue_id': task.issue_id,
        'queue_lane': entry.get('lane', 'default'),
    }
    if include_details:
        detail_getter = getattr(task, 'get_task_details', None)
        if callable(detail_getter):
            result['details'] = detail_getter()
    return result


def _remove_laned(self, task_id: int) -> None:
    entry = self._TaskHandler__get_raw_entry(task_id)
    if entry['status'] == 'running':
        request_stop = getattr(entry['task'], 'request_stop', None)
        if not callable(request_stop):
            raise TaskNotDeletable(task_id)
        request_stop()
        LOGGER.info('Stop requested: %s (%d)', entry['task'].display_title, task_id)
        return

    with _TASK_QUEUE_LOCK:
        if entry in self.queue:
            self.queue.remove(entry)
    LOGGER.info('Removed task: %s (%d)', entry['task'].display_title, task_id)
    WebSocket().emit(TaskEndedEvent(entry['task']))
    self._process_queue()


# A task gets this long, in total, to notice `stop` and leave its work
# somewhere it can be resumed from.
SHUTDOWN_GRACE_SECONDS = 30.0


def _stop_handle_laned(self) -> None:
    LOGGER.debug('Stopping task threads')
    if self.task_interval_waiter:
        self.task_interval_waiter.cancel()

    with _TASK_QUEUE_LOCK:
        running = [
            entry for entry in self.queue if entry['status'] == 'running'
        ]
    # `stop` is the process-shutdown signal; `request_stop()` is the user's
    # Stop button. They are not interchangeable, and tasks that expose both
    # persist differently for each: the continuous import records a
    # user-requested stop as a *paused* job, which does not auto-resume, and a
    # shutdown as a still-*running* one, which does. Calling request_stop()
    # here told every such task that a human had paused it, so an update, a
    # config change or a crash fix left the import stopped until someone
    # noticed and pressed Resume.
    for entry in running:
        entry['task'].stop = True

    deadline = monotonic() + SHUTDOWN_GRACE_SECONDS
    for entry in running:
        thread = entry.get('thread')
        if thread is None or not thread.is_alive():
            continue

        thread.join(timeout=max(deadline - monotonic(), 0.0))
        if thread.is_alive():
            # Shutdown must not hang on a task that will not stop. Its job row
            # is left as it stands, which for the continuous import is exactly
            # the state the next start resumes from.
            LOGGER.warning(
                'Task %s did not stop within %.0fs; leaving it to be picked '
                'up on the next start',
                entry['task'].display_title, SHUTDOWN_GRACE_SECONDS
            )


# Patch the class object exported by tasks_core. Every existing importer sees
# these methods because the class identity is unchanged.
TaskHandler._TaskHandler__run_task = _run_task_entry
TaskHandler._process_queue = _process_queue_lanes
TaskHandler.add = _add_laned
TaskHandler._TaskHandler__format_entry = _format_entry_laned
TaskHandler.remove = _remove_laned
TaskHandler.stop_handle = _stop_handle_laned
