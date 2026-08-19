# -*- coding: utf-8 -*-

"""Task integration for comic-native Import Lists."""

from time import time
from typing import Optional

from backend.features.import_lists import (
    IMPORT_LIST_SYNC_INTERVAL_SECONDS,
    sync_enabled_import_lists,
    sync_import_list,
)
from backend.features.tasks import Task, task_library
from backend.internals.db import commit, get_db
from backend.internals.server import TaskStatusEvent, WebSocket


class ImportListSync(Task):
    """Refresh every enabled Import List, or one explicitly requested list."""

    stop = False
    message = ''
    action = 'import_list_sync'
    display_title = 'Import List Sync'
    category = ''

    @property
    def volume_id(self) -> None:
        return None

    @property
    def issue_id(self) -> None:
        return None

    def __init__(self, import_list_id: Optional[int] = None) -> None:
        self.import_list_id = import_list_id
        return

    def run(self) -> None:
        self.message = (
            f'Syncing Import List {self.import_list_id}'
            if self.import_list_id is not None
            else 'Syncing enabled Import Lists'
        )
        WebSocket().emit(TaskStatusEvent(self.message))

        should_stop = lambda: self.stop
        if self.import_list_id is not None:
            summary = sync_import_list(self.import_list_id, should_stop)
            self.message = (
                f"Import List sync: {summary['added_count']} added · "
                f"{summary['exact_volume_count']} exact volumes · "
                f"{summary['unresolved_count']} unresolved entries"
            )
        else:
            summaries = sync_enabled_import_lists(should_stop)
            self.message = (
                f"Import List sync: {sum(s['added_count'] for s in summaries)} added "
                f"from {len(summaries)} list(s)"
            )
        WebSocket().emit(TaskStatusEvent(self.message))
        return


def ensure_import_list_interval() -> None:
    """Enroll the persistent 12-hour sync without resetting an existing clock."""
    current_time = round(time())
    get_db().execute("""
        INSERT INTO task_intervals(task_name, interval, next_run)
        VALUES (?, ?, ?)
        ON CONFLICT(task_name) DO UPDATE SET interval = excluded.interval;
    """, (
        ImportListSync.action,
        IMPORT_LIST_SYNC_INTERVAL_SECONDS,
        current_time,
    ))
    commit()


task_library[ImportListSync.action] = ImportListSync
