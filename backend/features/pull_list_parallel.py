# -*- coding: utf-8 -*-

"""A small parallel lane for release-calendar refreshes.

Continuous Library Import deliberately occupies Kapowarr's normal TaskHandler
queue for its whole durable pass. A release-calendar refresh is independent of
that conveyor: it talks to the weekly release providers and writes its own
short-lived catalogue transaction. Running it in the serialized task queue can
therefore leave "Check Now" blocked for hours or days behind an import.

This runner gives only the release-calendar check a separate worker. It is not
a second general task queue, so other tasks keep the existing serialization and
its safety guarantees.
"""

from __future__ import annotations

from threading import Lock, Thread
from time import time
from typing import Any, Dict, List, Optional

from flask import Flask

from backend.base.logging import LOGGER
from backend.features.download_queue import DownloadHandler
from backend.features.pull_list import (check_weekly_pull_list,
                                        process_publisher_subscriptions)
from backend.internals.db import close_db, commit, get_db


class PullListCheckRunner:
    """Run at most one manual weekly release refresh outside TaskHandler."""

    _history_limit = 20

    def __init__(self) -> None:
        self._lock = Lock()
        self._next_id = 1
        self._checks: Dict[int, Dict[str, Any]] = {}

    @staticmethod
    def _public(check: Dict[str, Any]) -> Dict[str, Any]:
        return {
            key: value
            for key, value in check.items()
            if key != 'thread'
        }

    def _active(self) -> Optional[Dict[str, Any]]:
        return next((
            check
            for check in self._checks.values()
            if check['status'] in ('queued', 'running')
        ), None)

    def start(self) -> Dict[str, Any]:
        """Start a refresh, or return the one already in progress."""
        with self._lock:
            active = self._active()
            if active is not None:
                return self._public(active)

            check_id = self._next_id
            self._next_id += 1
            check: Dict[str, Any] = {
                'id': check_id,
                'status': 'queued',
                'message': 'Starting release calendar check...',
                'error': None,
                'release_count': None,
                'started_at': round(time()),
                'finished_at': None,
            }
            thread = Thread(
                target=self._run,
                args=(check_id,),
                name=f'PullListCheck-{check_id}',
                daemon=True
            )
            check['thread'] = thread
            self._checks[check_id] = check
            self._prune_locked()
            thread.start()
            return self._public(check)

    def get(self, check_id: int) -> Optional[Dict[str, Any]]:
        with self._lock:
            check = self._checks.get(check_id)
            return self._public(check) if check is not None else None

    def _set(self, check_id: int, **values: Any) -> None:
        with self._lock:
            check = self._checks.get(check_id)
            if check is not None:
                check.update(values)

    def _prune_locked(self) -> None:
        completed = [
            check_id
            for check_id, check in self._checks.items()
            if check['status'] in ('completed', 'failed')
        ]
        for check_id in completed[:-self._history_limit]:
            self._checks.pop(check_id, None)

    def _record_history(self, failed_message: Optional[str] = None) -> None:
        title = 'Weekly Pull List Check'
        if failed_message:
            title += f' — Failed: {failed_message}'
        try:
            get_db().execute(
                """
                INSERT INTO task_history(task_name, display_title, run_at)
                VALUES (?, ?, ?);
                """,
                ('weekly_pull_list_check', title, round(time()))
            )
            commit()
        except Exception:
            LOGGER.exception('Failed to record parallel pull-list task history')

    def _run(self, check_id: int) -> None:
        context_app = Flask(f'pull-list-check-{check_id}')
        context_app.teardown_appcontext(close_db)
        with context_app.app_context():
            try:
                self._set(
                    check_id,
                    status='running',
                    message='Refreshing the publisher release calendar'
                )
                entries = check_weekly_pull_list()

                self._set(
                    check_id,
                    message='Applying publisher subscriptions'
                )
                downloads = process_publisher_subscriptions(entries)
                if downloads:
                    DownloadHandler().add_multiple(
                        (link, volume_id, issue_id, False)
                        for link, volume_id, issue_id in downloads
                    )

                self._record_history()
                self._set(
                    check_id,
                    status='completed',
                    message='Release calendar updated.',
                    release_count=len(entries),
                    finished_at=round(time())
                )
            except Exception as error:
                LOGGER.exception('Parallel weekly pull-list check failed')
                detail = str(error).strip() or type(error).__name__
                detail = ' '.join(detail.split())[:240]
                self._record_history(detail)
                self._set(
                    check_id,
                    status='failed',
                    message=f'Check failed: {detail}',
                    error=detail,
                    finished_at=round(time())
                )


def get_pull_list_weeks() -> List[Dict[str, Any]]:
    """Return exactly which stored weeks contain release rows."""
    rows = get_db().execute(
        """
        SELECT week_start, COUNT(*) AS release_count,
               MIN(checked_at) AS checked_at
        FROM pull_list_entries
        GROUP BY week_start
        ORDER BY week_start DESC;
        """
    ).fetchalldict()
    for row in rows:
        # SQLite DATE converters can return date objects in production while
        # tests commonly return strings. JSON object values should be explicit.
        value = row.get('week_start')
        if hasattr(value, 'isoformat'):
            row['week_start'] = value.isoformat()
        elif value is not None:
            row['week_start'] = str(value)
    return rows


pull_list_check_runner = PullListCheckRunner()
