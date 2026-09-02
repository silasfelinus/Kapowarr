# -*- coding: utf-8 -*-

"""
Background tasks and their handling
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from threading import Thread, Timer
from time import sleep, time
from typing import Dict, List, Tuple, Type, Union

from flask import Flask

from backend.base.custom_exceptions import (InvalidComicVineApiKey,
                                            TaskNotDeletable, TaskNotFound)
from backend.base.helpers import Singleton, get_subclasses
from backend.base.logging import LOGGER
from backend.features.download_queue import DownloadHandler
from backend.features.pull_list import (check_weekly_pull_list,
                                        process_publisher_subscriptions)
from backend.features.search import auto_search
from backend.features.watched_folder_import import (describe_summary,
                                                    run_watched_folder_import)
from backend.implementations.conversion import mass_convert
from backend.implementations.naming import mass_rename
from backend.implementations.volumes import Volume, refresh_and_scan
from backend.internals.db import close_db, commit, get_db
from backend.internals.server import (TaskAddedEvent, TaskEndedEvent,
                                      TaskStatusEvent, WebSocket)


class Task(ABC):
    stop: bool
    message: str
    action: str
    display_title: str
    category: str

    @property
    @abstractmethod
    def volume_id(self) -> Union[int, None]:
        ...

    @property
    @abstractmethod
    def issue_id(self) -> Union[int, None]:
        ...

    @abstractmethod
    def __init__(self, **kwargs) -> None:
        ...

    @abstractmethod
    def run(self) -> Union[None, List[Tuple[str, int, Union[int, None]]]]:
        """Run the task

        Returns:
            Union[None, List[Tuple[str, int, Union[int, None]]]]:
            Either `None` if the task has no result or
            `List[Tuple[str, int, Union[int, None]]]` if the task returns
            search results.
        """
        ...

# =====================
# Issue tasks
# =====================


class AutoSearchIssue(Task):
    "Do an automatic search for an issue"

    stop = False
    message = ''
    action = 'auto_search_issue'
    display_title = 'Auto Search'
    category = 'download'

    @property
    def volume_id(self) -> int:
        return self._volume_id

    @property
    def issue_id(self) -> int:
        return self._issue_id

    def __init__(self, volume_id: int, issue_id: int) -> None:
        """Create the task

        Args:
            volume_id (int): The id of the volume in which the issue is
            issue_id (int): The id of the issue to search for
        """
        self._volume_id = volume_id
        self._issue_id = issue_id
        return

    def run(self) -> List[Tuple[str, int, Union[int, None]]]:
        volume = Volume(self._volume_id)
        volume_title = volume.vd.title
        issue_number = volume.get_issue(self._issue_id).get_data().issue_number
        self.message = f'Searching for {volume_title} #{issue_number}'
        WebSocket().emit(TaskStatusEvent(self.message))

        # Get search results and download them
        results = auto_search(self._volume_id, self._issue_id)
        if results:
            return [
                (result['link'], self._volume_id, self._issue_id)
                for result in results
            ]
        return []


class MassRenameIssue(Task):
    "Trigger a mass rename for an issue"

    stop = False
    message = ''
    action = 'mass_rename_issue'
    display_title = 'Mass Rename'
    category = ''

    @property
    def volume_id(self) -> int:
        return self._volume_id

    @property
    def issue_id(self) -> int:
        return self._issue_id

    def __init__(
        self,
        volume_id: int,
        issue_id: int,
        filepath_filter: List[str] = []
    ) -> None:
        """Create the task

        Args:
            volume_id (int): The ID of the volume for which to perform the task.
            issue_id (int): The ID of the issue for which to perform the task.
            filepath_filter (List[str], optional): Only rename files in this
            list.
                Defaults to [].
        """
        self._volume_id = volume_id
        self._issue_id = issue_id
        self.filepath_filter = filepath_filter
        return

    def run(self) -> None:
        volume = Volume(self._volume_id)
        volume_title = volume.vd.title
        issue_number = volume.get_issue(self._issue_id).get_data().issue_number
        self.message = f'Renaming files for {volume_title} #{issue_number}'
        WebSocket().emit(TaskStatusEvent(self.message))

        mass_rename(
            self._volume_id,
            self._issue_id,
            filepath_filter=self.filepath_filter,
            update_websocket=True
        )

        return


class MassConvertIssue(Task):
    "Trigger a mass convert for an issue"

    stop = False
    message = ''
    action = 'mass_convert_issue'
    display_title = 'Mass Convert'
    category = ''

    @property
    def volume_id(self) -> int:
        return self._volume_id

    @property
    def issue_id(self) -> int:
        return self._issue_id

    def __init__(
        self,
        volume_id: int,
        issue_id: int,
        filepath_filter: List[str] = []
    ) -> None:
        """Create the task

        Args:
            volume_id (int): The ID of the volume for which to perform the task.
            issue_id (int): The ID of the issue for which to perform the task.
            filepath_filter (List[str], optional): Only rename files in this
            list.
                Defaults to [].
        """
        self._volume_id = volume_id
        self._issue_id = issue_id
        self.filepath_filter = filepath_filter
        return

    def run(self) -> None:
        volume = Volume(self._volume_id)
        volume_title = volume.vd.title
        issue_number = volume.get_issue(self._issue_id).get_data().issue_number
        self.message = f'Converting files for {volume_title} #{issue_number}'
        WebSocket().emit(TaskStatusEvent(self.message))

        mass_convert(
            self._volume_id,
            self._issue_id,
            filepath_filter=self.filepath_filter,
            update_websocket_progress=True,
            update_websocket_files=True
        )

        return

# =====================
# Volume tasks
# =====================


class AutoSearchVolume(Task):
    "Do an automatic search for a volume"

    stop = False
    message = ''
    action = 'auto_search'
    display_title = 'Auto Search'
    category = 'download'

    @property
    def volume_id(self) -> int:
        return self._volume_id

    @property
    def issue_id(self) -> None:
        return None

    def __init__(self, volume_id: int) -> None:
        """Create the task

        Args:
            volume_id (int): The id of the volume to search for
        """
        self._volume_id = volume_id
        return

    def run(self) -> List[Tuple[str, int, Union[int, None]]]:
        volume_title = Volume(self._volume_id).vd.title
        self.message = f'Searching for {volume_title}'
        WebSocket().emit(TaskStatusEvent(self.message))

        # Get search results and download them
        results = auto_search(self._volume_id)
        if results:
            return [
                (result['link'], self._volume_id, None)
                for result in results
            ]
        return []


class RefreshAndScanVolume(Task):
    "Trigger a refresh and scan for a volume"

    stop = False
    message = ''
    action = 'refresh_and_scan'
    display_title = 'Refresh And Scan'
    category = ''

    @property
    def volume_id(self) -> int:
        return self._volume_id

    @property
    def issue_id(self) -> None:
        return None

    def __init__(self, volume_id: int) -> None:
        """Create the task

        Args:
            volume_id (int): The id of the volume for which to perform the task
        """
        self._volume_id = volume_id
        return

    def run(self) -> None:
        volume_title = Volume(self._volume_id).vd.title
        self.message = f'Updating info on {volume_title}'
        WebSocket().emit(TaskStatusEvent(self.message))

        try:
            refresh_and_scan(self._volume_id, update_websocket=True)
        except InvalidComicVineApiKey:
            pass

        return


class MassRenameVolume(Task):
    "Trigger a mass rename for a volume"

    stop = False
    message = ''
    action = 'mass_rename'
    display_title = 'Mass Rename'
    category = ''

    @property
    def volume_id(self) -> int:
        return self._volume_id

    @property
    def issue_id(self) -> None:
        return None

    def __init__(
        self,
        volume_id: int,
        filepath_filter: List[str] = []
    ) -> None:
        """Create the task

        Args:
            volume_id (int): The ID of the volume for which to perform the task.
            filepath_filter (List[str], optional): Only rename files in this
            list.
                Defaults to [].
        """
        self._volume_id = volume_id
        self.filepath_filter = filepath_filter
        return

    def run(self) -> None:
        volume_title = Volume(self._volume_id).vd.title
        self.message = f'Renaming files for {volume_title}'
        WebSocket().emit(TaskStatusEvent(self.message))

        mass_rename(
            self._volume_id,
            filepath_filter=self.filepath_filter,
            update_websocket=True
        )

        return


class MassConvertVolume(Task):
    "Trigger a mass convert for a volume"

    stop = False
    message = ''
    action = 'mass_convert'
    display_title = 'Mass Convert'
    category = ''

    @property
    def volume_id(self) -> int:
        return self._volume_id

    @property
    def issue_id(self) -> None:
        return None

    def __init__(
        self,
        volume_id: int,
        filepath_filter: List[str] = []
    ) -> None:
        """Create the task

        Args:
            volume_id (int): The ID of the volume for which to perform the task.
            filepath_filter (List[str], optional): Only convert files in this
            list.
                Defaults to [].
        """
        self._volume_id = volume_id
        self.filepath_filter = filepath_filter
        return

    def run(self) -> None:
        volume_title = Volume(self._volume_id).vd.title
        self.message = f'Converting files for {volume_title}'
        WebSocket().emit(TaskStatusEvent(self.message))

        mass_convert(
            self._volume_id,
            filepath_filter=self.filepath_filter,
            update_websocket_progress=True,
            update_websocket_files=True
        )

        return

# =====================
# Library tasks
# =====================


class UpdateAll(Task):
    "Trigger a refresh and scan for each volume in the library"

    stop = False
    message = ''
    action = 'update_all'
    display_title = 'Update All'
    category = ''

    @property
    def volume_id(self) -> None:
        return None

    @property
    def issue_id(self) -> None:
        return None

    def __init__(self, allow_skipping: bool = False) -> None:
        """Create the task

        Args:
            allow_skipping (bool, optional): Skip volumes that have been updated in the last 24 hours.
                Defaults to False.
        """
        self.allow_skipping = allow_skipping
        return

    def run(self) -> None:
        self.message = f'Updating info on all volumes'
        WebSocket().emit(TaskStatusEvent(self.message))

        try:
            refresh_and_scan(
                update_websocket=True,
                allow_skipping=self.allow_skipping
            )
        except InvalidComicVineApiKey:
            pass

        return


class SearchAll(Task):
    "Trigger an automatic search for each volume in the library"

    stop = False
    message = ''
    action = 'search_all'
    display_title = 'Search All'
    category = 'download'

    @property
    def volume_id(self) -> None:
        return None

    @property
    def issue_id(self) -> None:
        return None

    def __init__(self) -> None:
        return

    def run(self) -> List[Tuple[str, int, Union[int, None]]]:
        cursor = get_db(force_new=True)
        # Whoever has waited longest goes first.
        #
        # Unordered, SQLite returns rowid order -- the order volumes were
        # added to the library -- and it returns it that way every day. On a
        # library whose sweep finishes comfortably that is merely arbitrary.
        # On one where it does not, the oldest additions are searched every
        # single day and the newest are never reached at all: exactly the
        # volumes a pull list puts at the end, and exactly the gaps Silas
        # had been watching go unfilled for weeks.
        #
        # Ordering by when each volume was last searched makes the sweep a
        # rotation instead of a prefix. A run that covers a third of the
        # library covers a different third tomorrow, and everything gets a
        # turn within three days rather than the first third getting all of
        # them forever.
        cursor.execute("""
            SELECT id, title FROM volumes
            WHERE monitored = 1
            ORDER BY last_auto_search, id;
            """
        )
        downloads: List[Tuple[str, int, Union[int, None]]] = []
        ws = WebSocket()
        for volume_id, volume_title in cursor:
            if self.stop:
                break
            self.message = f'Searching for {volume_title}'
            ws.emit(TaskStatusEvent(self.message))

            # Stamped before the search, not after. A volume whose search
            # fails, or that the user stops the sweep in the middle of, has
            # still had its turn -- and if the stamp only landed on success
            # then one reliably-failing volume would be first in the queue
            # every day forever, which is the problem this ordering exists
            # to fix.
            self._mark_searched(volume_id)

            try:
                results = auto_search(volume_id)

            except Exception:
                # One volume must not cost the rest of the library its
                # nightly search.
                #
                # This loop is the whole of unattended acquisition: it runs
                # once a day, and it is what fills in issues nobody went
                # looking for by hand. Any exception from any of the sources
                # it touches used to end the task where it stood, leaving
                # every volume after that one unsearched until tomorrow --
                # when the same thing would happen at roughly the same
                # place. On 2026-08-31 that was a "database is locked" out
                # of a config read on the search path, twenty-one volumes
                # into a library of thousands, and it is why almost nothing
                # arrived that had not been searched for by hand.
                #
                # Broad on purpose. The sources here are indexers, trackers
                # and HTTP scrapers, and the failure that matters is the one
                # not thought of.
                LOGGER.exception(
                    'Auto search failed for volume %d (%s); '
                    'continuing with the rest of the library',
                    volume_id, volume_title
                )
                continue

            if not results:
                continue

            # Queued as they are found, not at the end of the sweep.
            #
            # This used to accumulate every result and hand the whole list
            # back for the runner to enqueue after `run()` returned. Over a
            # library of thousands that means nothing downloads until the
            # last volume has been searched -- Silas, watching a sweep that
            # had been running twenty minutes: "it's kinda weird that
            # nothing has been added to the queue yet."
            #
            # And the runner only enqueues `if not task.stop`, so pressing
            # Stop threw away everything the sweep had found. Hours of
            # searching, discarded because the user asked it to finish
            # early.
            downloads += [
                (result['link'], volume_id, None)
                for result in results
            ]
            self._queue(
                (result['link'], volume_id, None, False)
                for result in results
            )

        # Already queued. Returned for the caller that wants to know what a
        # sweep found; the runner skips an empty list.
        return []

    @staticmethod
    def _mark_searched(volume_id: int) -> None:
        """Record that this volume has had its turn.

        Never raises. A sweep is long, it shares the database with library
        import and refresh, and a bookkeeping write is not worth a task:
        losing one stamp costs a volume its place in the rotation for a
        day, which is a great deal cheaper than losing the sweep.
        """
        try:
            get_db().execute(
                "UPDATE volumes SET last_auto_search = ? WHERE id = ?;",
                (round(time()), volume_id)
            )
            commit()

        except Exception:
            LOGGER.warning(
                'Could not record the search time for volume %d; it keeps '
                'its place in the rotation', volume_id
            )

    @staticmethod
    def _queue(entries) -> None:
        from backend.features.download_queue import DownloadHandler
        DownloadHandler().add_multiple(entries)


class WeeklyPullListCheck(Task):
    "Refresh releases and apply opt-in publisher automation"

    stop = False
    message = ''
    action = 'weekly_pull_list_check'
    display_title = 'Weekly Pull List Check'
    category = 'download'

    @property
    def volume_id(self) -> None:
        return None

    @property
    def issue_id(self) -> None:
        return None

    def __init__(self) -> None:
        return

    def run(self) -> List[Tuple[str, int, Union[int, None]]]:
        self.message = "Refreshing the publisher release calendar"
        WebSocket().emit(TaskStatusEvent(self.message))

        entries = check_weekly_pull_list()
        self.message = "Applying publisher subscriptions"
        WebSocket().emit(TaskStatusEvent(self.message))
        return process_publisher_subscriptions(entries)


class WatchedFolderImport(Task):
    "Import externally acquired files dropped into the watched folder"

    stop = False
    message = ''
    action = 'watched_folder_import'
    display_title = 'Watched Folder Import'
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
        # Declared here, beside the other interval tasks, so `task_library`'s
        # `get_subclasses(Task)` sweep registers it without the module needing
        # to be imported first -- the interval is seeded for every install, so
        # a lookup miss would break the whole interval loop, not just this
        # task. The work itself lives in backend.features.watched_folder_import.
        self.message = 'Scanning the watched folder'
        WebSocket().emit(TaskStatusEvent(self.message))

        summary = run_watched_folder_import(lambda: self.stop)

        self.message = describe_summary(summary)
        WebSocket().emit(TaskStatusEvent(self.message))
        return


# =====================
# Task handling
# =====================
# Maps action attr to class for all tasks
# Only works for classes that directly inherit from Task
task_library: Dict[str, Type[Task]] = {
    c.action: c
    for c in get_subclasses(Task)
}


class TaskHandler(metaclass=Singleton):
    "Note: Singleton"

    queue: List[dict] = []
    task_interval_waiter: Union[Timer, None] = None

    INTERVAL_FALLBACK_DELAY = 60
    """How long to wait before looking at the intervals again when working out
    the real answer failed."""

    def __init__(self) -> None:
        """Setup the handler"""
        handler_context = Flask('handler')
        handler_context.teardown_appcontext(close_db)
        self.context = handler_context.app_context
        return

    def __run_task(self, task: Task) -> None:
        """Run a task

        Args:
            task (Task): The task to run
        """
        LOGGER.debug(f'Running task {task.display_title}')
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

                    LOGGER.info(f'Finished task {task.display_title}')

            except Exception as error:
                LOGGER.exception(
                    'An error occured while trying to run a task: ')
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
                            INSERT INTO task_history(
                                task_name, display_title, run_at
                            ) VALUES (?,?,?);
                            """,
                            (task.action, history_title, round(time()))
                        )
                        # The task-ended event tells open Tasks pages to
                        # reload history. Make the new row visible to those
                        # request connections before emitting the event.
                        commit()
                    except Exception:
                        # History must never prevent queue cleanup or the next
                        # task from starting, even if the database itself is
                        # the thing that failed.
                        LOGGER.exception(
                            'Failed to record task history for %s',
                            task.display_title
                        )

                    socket.emit(TaskEndedEvent(task))
                    self.queue.pop(0)
                    self._process_queue()

        return

    def _process_queue(self) -> None:
        """
        Handle the queue. In the case that there is something in the queue and
        it isn't already running, start the task. This can safely be called
        multiple times while a task is going or while there is nothing in the queue.
        """
        if not self.queue:
            return

        first_entry = self.queue[0]
        if first_entry['status'] != 'running':
            first_entry['status'] = 'running'
            first_entry['thread'].start()
        return

    def add(self, task: Task) -> int:
        """Add a task to the queue

        Args:
            task (Task): The task to add to the queue

        Returns:
            int: The id of the entry in the queue
        """
        LOGGER.debug(f'Adding task to queue: {task.display_title}')
        id = self.queue[-1]['id'] + 1 if self.queue else 1
        task_data = {
            'task': task,
            'id': id,
            'status': 'queued',
            'thread': Thread(
                target=self.__run_task,
                args=(task,),
                name=f"TaskThread-{id}"
            )
        }
        self.queue.append(task_data)
        LOGGER.info(f'Added task: {task.display_title} ({id})')
        WebSocket().emit(TaskAddedEvent(task))
        self._process_queue()
        return id

    @staticmethod
    def task_for_volume_running(volume_id: int) -> bool:
        """Whether or not there is a task in the queue that targets the volume.

        Args:
            volume_id (int): The volume ID to check for.

        Returns:
            bool: Whether or not a task is in the queue targeting the volume.
        """
        return any(
            t
            for t in TaskHandler.queue
            if (isinstance(t['task'], (UpdateAll, SearchAll))
                or t['task'].volume_id == volume_id)
        )

    def __check_intervals(self) -> None:
        "Check if any interval task needs to be run and add to queue if so"
        LOGGER.debug('Checking task intervals')
        try:
            with self.context():
                current_time = time()

                cursor = get_db()
                interval_tasks = cursor.execute(
                    "SELECT task_name, interval, next_run FROM task_intervals;"
                ).fetchall()
                LOGGER.debug(
                    f'Task intervals: {list(map(dict, interval_tasks))}')
                for task in interval_tasks:
                    if task['next_run'] <= current_time:
                        # Add task to queue
                        task_class = task_library[task['task_name']]
                        if task_class is UpdateAll:
                            inst = task_class(allow_skipping=True)
                        else:
                            inst = task_class()
                        self.add(inst)

                        # Update next_run
                        next_run = round(current_time + task['interval'])
                        cursor.execute(
                            "UPDATE task_intervals SET next_run = ? WHERE task_name = ?;",
                            (next_run, task['task_name']))

        except Exception:
            # This method is the only thing that schedules the next run of
            # itself, so an exception escaping it doesn't skip one check --
            # it ends scheduled tasks for the lifetime of the process. On
            # 2026-09-01 a locked database did exactly that, and nothing ran
            # on an interval again until the container was restarted. Log it
            # and get back in the rota.
            LOGGER.exception('Could not check the task intervals; retrying: ')

        self.handle_intervals()
        return

    def handle_intervals(self) -> None:
        "Find next time an interval task needs to be run"
        try:
            with self.context():
                next_run = get_db().execute(
                    "SELECT MIN(next_run) FROM task_intervals"
                ).fetchone()[0]
            timedelta = next_run - round(time()) + 1

        except Exception:
            # Same reasoning as above: whatever went wrong, the one thing we
            # can't do is fail to set the next alarm.
            LOGGER.exception(
                'Could not work out when the next interval task is due; '
                'checking again in %ds: ',
                self.INTERVAL_FALLBACK_DELAY
            )
            timedelta = self.INTERVAL_FALLBACK_DELAY

        LOGGER.debug(f'Next interval task is in {timedelta} seconds')

        self.task_interval_waiter = Timer(timedelta, self.__check_intervals)
        self.task_interval_waiter.name = "TaskIntervalThread"
        self.task_interval_waiter.start()
        return

    def stop_handle(self) -> None:
        "Stop the task handler"
        LOGGER.debug('Stopping task thread')

        if self.task_interval_waiter:
            self.task_interval_waiter.cancel()

        if self.queue:
            self.queue[0]['task'].stop = True
            self.queue[0]['thread'].join()

        return

    def __format_entry(self, task: dict, include_details: bool = False) -> dict:
        """Format a queue entry for API response.

        Detailed task state is opt-in so the lightweight queue poll does not
        repeatedly ship large task-specific payloads. GET on a single task can
        include details exposed by that task through get_task_details().
        """
        result = {
            'id': task['id'],
            'action': task['task'].action,
            'display_title': task['task'].display_title,
            'status': task['status'],
            'message': task['task'].message,
            'volume_id': task['task'].volume_id,
            'issue_id': task['task'].issue_id
        }

        if include_details:
            detail_getter = getattr(task['task'], 'get_task_details', None)
            if callable(detail_getter):
                result['details'] = detail_getter()

        return result

    def get_all(self) -> List[dict]:
        """Get all tasks in the queue

        Returns:
            List[dict]: A list with all tasks in the queue.
                Formatted using `self.__format_entry()`.
        """
        return [self.__format_entry(t) for t in self.queue]

    def get_one(self, task_id: int) -> dict:
        """Get one task from the queue based on it's id

        Args:
            task_id (int): The id of the task to get from the queue

        Raises:
            TaskNotFound: The id doesn't match with any task in the queue

        Returns:
            dict: The info of the task in the queue.
                Formatted using `self.__format_entry()`.
        """
        return self.__format_entry(
            self.__get_raw_entry(task_id),
            include_details=True
        )

    def __get_raw_entry(self, task_id: int) -> dict:
        """Get the raw entry from the queue based on it's id

        Args:
            task_id (int): The id of the task to get from the queue

        Raises:
            TaskNotFound: The id doesn't match with any task in the queue

        Returns:
            dict: The raw entry of the task in the queue.
        """
        for entry in self.queue:
            if entry['id'] == task_id:
                return entry
        raise TaskNotFound(task_id)

    def remove(self, task_id: int) -> None:
        """Remove a queued task or cooperatively stop a running task.

        Running tasks remain non-deletable unless they explicitly expose a
        request_stop() method. That keeps the existing safety behavior for tasks
        that cannot be interrupted cleanly while letting long-running tasks opt
        into a safe stop boundary.
        """
        task = self.__get_raw_entry(task_id)

        if self.queue[0] == task:
            request_stop = getattr(task['task'], 'request_stop', None)
            if not callable(request_stop):
                raise TaskNotDeletable(task_id)

            request_stop()
            LOGGER.info(
                f'Stop requested: {task["task"].display_title} ({task_id})'
            )
            return

        # A non-running queue entry has never started, so joining its thread
        # would raise RuntimeError. Removing it directly is the clean cancel.
        self.queue.remove(task)
        LOGGER.info(f'Removed task: {task["task"].display_title} ({task_id})')
        WebSocket().emit(TaskEndedEvent(task['task']))
        return


def get_task_history(offset: int = 0) -> List[dict]:
    """Get the task history in blocks of 50.

    Args:
        offset (int, optional): The offset of the list.
            The higher the number, the deeper into history you go.

            Defaults to 0.

    Returns:
        List[dict]: The history entries.
    """
    result = get_db().execute(
        """
        SELECT
            task_name, display_title, run_at
        FROM task_history
        ORDER BY run_at DESC
        LIMIT 50
        OFFSET ?;
        """,
        (offset * 50,)
    ).fetchalldict()
    return result


def delete_task_history() -> None:
    "Delete the complete task history"
    LOGGER.info(f'Deleting task history')
    get_db().execute("DELETE FROM task_history;")
    return


def get_task_planning() -> List[dict]:
    """Get the planning of each interval task (interval, next run and last run)

    Returns:
        List[dict]: List of interval tasks and their planning
    """
    tasks = get_db().execute(
        """
        SELECT
            i.task_name, interval, next_run, run_at AS last_run
        FROM task_intervals i
        LEFT JOIN (
            SELECT
                task_name,
                MAX(run_at) AS run_at
            FROM task_history
            GROUP BY task_name
        ) h
        ON i.task_name = h.task_name;
        """
    ).fetchalldict()

    for t in tasks:
        t['display_name'] = task_library[t['task_name']].display_title

    return tasks
