# -*- coding: utf-8 -*-

"""Durable checkpoint state for Continuous Library Import.

The normal TaskHandler queue is intentionally process-local. Continuous library
imports can run for many hours, so this module stores only the durable facts the
worker needs to recover safely: the job status, its stable folder snapshot, each
folder's checkpoint state, imported-volume count, and any held review rows.
"""

from __future__ import annotations

from json import dumps, loads
from time import time
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from backend.features.library_import_metadata import is_library_import_artifact
from backend.internals.db import commit, get_db

JOB_RUNNING = 'running'
JOB_PAUSED = 'paused'
JOB_COMPLETE = 'complete'

ITEM_PENDING = 'pending'
ITEM_PROCESSING = 'processing'
ITEM_DONE = 'done'
ITEM_REVIEW = 'review'


_SCHEMA = """
CREATE TABLE IF NOT EXISTS library_import_jobs(
    id INTEGER PRIMARY KEY,
    status VARCHAR(20) NOT NULL,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    last_error TEXT
);
CREATE TABLE IF NOT EXISTS library_import_items(
    id INTEGER PRIMARY KEY,
    job_id INTEGER NOT NULL,
    position INTEGER NOT NULL,
    folder TEXT NOT NULL,
    state VARCHAR(20) NOT NULL DEFAULT 'pending',
    imported_volumes INTEGER NOT NULL DEFAULT 0,
    review_reason VARCHAR(30),
    review_items TEXT NOT NULL DEFAULT '[]',
    updated_at INTEGER NOT NULL,

    FOREIGN KEY (job_id) REFERENCES library_import_jobs(id)
        ON DELETE CASCADE,
    UNIQUE(job_id, folder)
);
CREATE INDEX IF NOT EXISTS library_import_items_job_state_position_index
    ON library_import_items(job_id, state, position);
"""


def ensure_schema() -> None:
    """Create the feature-owned checkpoint tables if they do not exist."""
    get_db().executescript(_SCHEMA)
    commit()
    return


def _row_to_dict(row: Any) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    return dict(row)


def create_job(folders: Iterable[str]) -> int:
    """Create a durable job from a stable, ordered folder snapshot."""
    ensure_schema()
    ordered_folders = list(dict.fromkeys(folders))
    now = round(time())
    cursor = get_db()
    cursor.execute(
        """
        INSERT INTO library_import_jobs(status, created_at, updated_at, last_error)
        VALUES (?, ?, ?, NULL);
        """,
        (JOB_RUNNING, now, now)
    )
    job_id = cursor.lastrowid
    cursor.executemany(
        """
        INSERT INTO library_import_items(
            job_id, position, folder, state, imported_volumes,
            review_reason, review_items, updated_at
        ) VALUES (?, ?, ?, ?, 0, NULL, '[]', ?);
        """,
        (
            (job_id, position, folder, ITEM_PENDING, now)
            for position, folder in enumerate(ordered_folders)
        )
    )
    commit()
    return job_id


def get_latest_job(statuses: Sequence[str]) -> Optional[Dict[str, Any]]:
    """Return the newest job whose status is one of ``statuses``."""
    ensure_schema()
    if not statuses:
        return None

    placeholders = ','.join('?' for _ in statuses)
    row = get_db().execute(
        f"""
        SELECT id, status, created_at, updated_at, last_error
        FROM library_import_jobs
        WHERE status IN ({placeholders})
        ORDER BY id DESC
        LIMIT 1;
        """,
        tuple(statuses)
    ).fetchone()
    return _row_to_dict(row)


def get_running_job() -> Optional[Dict[str, Any]]:
    return get_latest_job((JOB_RUNNING,))


def get_paused_job() -> Optional[Dict[str, Any]]:
    return get_latest_job((JOB_PAUSED,))


def get_active_job() -> Optional[Dict[str, Any]]:
    """Return the job the UI should be showing, running or not.

    A finished pass still has everything worth looking at: what it checked, what
    it imported, and which folders it held. Those live here rather than in the
    task queue, which drops a task the moment it finishes -- so anything that
    can only reach a job through a live task loses sight of the pass as soon as
    it ends, and again on every page reload.
    """
    return (
        get_running_job()
        or get_paused_job()
        or get_latest_job((JOB_RUNNING, JOB_PAUSED, JOB_COMPLETE))
    )


def mark_job_running(job_id: int) -> None:
    """Resume a job and replay only a folder that was interrupted mid-check."""
    now = round(time())
    cursor = get_db()
    cursor.execute(
        """
        UPDATE library_import_items
        SET state = ?, updated_at = ?
        WHERE job_id = ? AND state = ?;
        """,
        (ITEM_PENDING, now, job_id, ITEM_PROCESSING)
    )
    cursor.execute(
        """
        UPDATE library_import_jobs
        SET status = ?, updated_at = ?, last_error = NULL
        WHERE id = ?;
        """,
        (JOB_RUNNING, now, job_id)
    )
    commit()
    return


def mark_job_paused(job_id: int, error: Optional[str] = None) -> None:
    now = round(time())
    cursor = get_db()
    cursor.execute(
        """
        UPDATE library_import_items
        SET state = ?, updated_at = ?
        WHERE job_id = ? AND state = ?;
        """,
        (ITEM_PENDING, now, job_id, ITEM_PROCESSING)
    )
    cursor.execute(
        """
        UPDATE library_import_jobs
        SET status = ?, updated_at = ?, last_error = ?
        WHERE id = ?;
        """,
        (JOB_PAUSED, now, error, job_id)
    )
    commit()
    return


def mark_job_complete(job_id: int) -> None:
    now = round(time())
    get_db().execute(
        """
        UPDATE library_import_jobs
        SET status = ?, updated_at = ?, last_error = NULL
        WHERE id = ?;
        """,
        (JOB_COMPLETE, now, job_id)
    )
    commit()
    return


def get_pending_folders(job_id: int) -> List[Tuple[int, str]]:
    rows = get_db().execute(
        """
        SELECT position, folder
        FROM library_import_items
        WHERE job_id = ? AND state = ?
        ORDER BY position;
        """,
        (job_id, ITEM_PENDING)
    ).fetchall()
    return [(int(row['position']), str(row['folder'])) for row in rows]


def mark_folder_processing(job_id: int, folder: str) -> None:
    now = round(time())
    get_db().execute(
        """
        UPDATE library_import_items
        SET state = ?, updated_at = ?
        WHERE job_id = ? AND folder = ?;
        """,
        (ITEM_PROCESSING, now, job_id, folder)
    )
    commit()
    return


def mark_folder_pending(job_id: int, folder: str) -> None:
    now = round(time())
    get_db().execute(
        """
        UPDATE library_import_items
        SET state = ?, updated_at = ?
        WHERE job_id = ? AND folder = ? AND state = ?;
        """,
        (ITEM_PENDING, now, job_id, folder, ITEM_PROCESSING)
    )
    commit()
    return


def mark_folder_result(
    job_id: int,
    folder: str,
    imported_volumes: int,
    review_reason: Optional[str],
    review_items: List[Dict[str, Any]]
) -> None:
    """Atomically checkpoint one finished folder boundary."""
    now = round(time())
    state = ITEM_REVIEW if review_items else ITEM_DONE
    cursor = get_db()
    cursor.execute(
        """
        UPDATE library_import_items
        SET
            state = ?,
            imported_volumes = ?,
            review_reason = ?,
            review_items = ?,
            updated_at = ?
        WHERE job_id = ? AND folder = ?;
        """,
        (
            state,
            imported_volumes,
            review_reason if review_items else None,
            dumps(review_items),
            now,
            job_id,
            folder
        )
    )
    cursor.execute(
        "UPDATE library_import_jobs SET updated_at = ? WHERE id = ?;",
        (now, job_id)
    )
    commit()
    return


def get_job_summary(job_id: int) -> Dict[str, Any]:
    row = get_db().execute(
        """
        SELECT
            j.id,
            j.status,
            j.created_at,
            j.updated_at,
            j.last_error,
            COUNT(i.id) AS total_folders,
            COALESCE(SUM(
                CASE WHEN i.state IN (?, ?) THEN 1 ELSE 0 END
            ), 0) AS checked_folders,
            COALESCE(SUM(i.imported_volumes), 0) AS imported_volumes,
            COALESCE(SUM(
                CASE WHEN i.state = ? THEN 1 ELSE 0 END
            ), 0) AS review_folders
        FROM library_import_jobs j
        LEFT JOIN library_import_items i ON i.job_id = j.id
        WHERE j.id = ?
        GROUP BY j.id;
        """,
        (ITEM_DONE, ITEM_REVIEW, ITEM_REVIEW, job_id)
    ).fetchone()
    result = _row_to_dict(row)
    if result is None:
        raise ValueError(f'Unknown library import job {job_id}')

    result['total_folders'] = int(result['total_folders'])
    result['checked_folders'] = int(result['checked_folders'])
    result['imported_volumes'] = int(result['imported_volumes'])
    result['review_folders'] = int(result['review_folders'])
    result['remaining_folders'] = max(
        result['total_folders'] - result['checked_folders'],
        0
    )

    reason_rows = get_db().execute(
        """
        SELECT review_reason, COUNT(*) AS amount
        FROM library_import_items
        WHERE job_id = ? AND state = ? AND review_reason IS NOT NULL
        GROUP BY review_reason;
        """,
        (job_id, ITEM_REVIEW)
    ).fetchall()
    result['review_reasons'] = {
        str(reason['review_reason']): int(reason['amount'])
        for reason in reason_rows
    }
    return result


def _decode_review_items(raw: str) -> List[Dict[str, Any]]:
    try:
        decoded = loads(raw or '[]')
    except (TypeError, ValueError):
        return []
    if not isinstance(decoded, list):
        return []
    return [item for item in decoded if isinstance(item, dict)]


def _imported_filepaths() -> Set[str]:
    return {
        str(row['filepath'])
        for row in get_db().execute("SELECT filepath FROM files;").fetchall()
    }


def _prune_review_rows(
    rows: Sequence[Any],
    imported_paths: Set[str]
) -> Tuple[Dict[int, List[Dict[str, Any]]], bool]:
    """Reconcile held rows against the canonical ``files`` table.

    Manual review uses the normal import endpoint, not a special persistent-job
    mutation, so the queue has to be reconciled when it is read. Artwork/cache
    paths that should never have been volume-discovery candidates are dropped by
    the same classifier the continuous importer uses.
    """
    cursor = get_db()
    kept: Dict[int, List[Dict[str, Any]]] = {}
    changed = False
    now = round(time())

    for row in rows:
        row_id = int(row['id'])
        items = _decode_review_items(row['review_items'])
        filtered = [
            item
            for item in items
            if (
                item.get('filepath') not in imported_paths
                and not is_library_import_artifact(
                    str(item.get('filepath') or '')
                )
            )
        ]

        if len(filtered) != len(items):
            changed = True
            if filtered:
                cursor.execute(
                    """
                    UPDATE library_import_items
                    SET review_items = ?, updated_at = ?
                    WHERE id = ?;
                    """,
                    (dumps(filtered), now, row_id)
                )
            else:
                cursor.execute(
                    """
                    UPDATE library_import_items
                    SET
                        state = ?,
                        review_reason = NULL,
                        review_items = '[]',
                        updated_at = ?
                    WHERE id = ?;
                    """,
                    (ITEM_DONE, now, row_id)
                )

        kept[row_id] = filtered

    return kept, changed


def get_review_items(
    job_id: int,
    prune_resolved: bool = True
) -> List[Dict[str, Any]]:
    """Return one job's durable review rows, reconciled against `files`."""
    cursor = get_db()
    rows = cursor.execute(
        """
        SELECT id, review_items
        FROM library_import_items
        WHERE job_id = ? AND state = ?
        ORDER BY position;
        """,
        (job_id, ITEM_REVIEW)
    ).fetchall()

    if not prune_resolved:
        return [
            item
            for row in rows
            for item in _decode_review_items(row['review_items'])
        ]

    kept, changed = _prune_review_rows(
        rows,
        _imported_filepaths() if rows else set()
    )
    if changed:
        cursor.execute(
            "UPDATE library_import_jobs SET updated_at = ? WHERE id = ?;",
            (round(time()), job_id)
        )
        commit()

    return [item for row in rows for item in kept[int(row['id'])]]


def count_outstanding_review_folders() -> int:
    """Count held folders without decoding or reconciling any of them.

    The Library Import page polls while a pass runs, and the review queue can be
    hundreds of folders. Decoding every held row and cross-checking it against
    the whole `files` table on each poll is a lot of work to render one number,
    so this counts in SQL instead. It can briefly read high if holds were
    resolved by hand elsewhere; opening the review list reconciles them and the
    count settles.
    """
    ensure_schema()
    row = get_db().execute(
        """
        SELECT COUNT(DISTINCT folder) AS amount
        FROM library_import_items
        WHERE state = ?;
        """,
        (ITEM_REVIEW,)
    ).fetchone()
    return int(row['amount']) if row is not None else 0


def get_outstanding_review_items() -> List[Dict[str, Any]]:
    """Return every folder still waiting for review, across all passes.

    Review holds belong to the job that produced them, but a hold outlives its
    pass: nothing imported it, so the next pass finds the same files unimported
    and queues the same folder again. Scoping the review queue to one job
    therefore made the whole backlog disappear the moment a pass finished or a
    new one started, even though the rows were still sitting in SQLite.

    Newest pass wins per folder, so a re-evaluated folder replaces its earlier
    hold rather than showing up twice, and whole folders are kept together so a
    filename group is never split across passes.
    """
    ensure_schema()
    cursor = get_db()
    rows = cursor.execute(
        """
        SELECT id, job_id, folder, review_items
        FROM library_import_items
        WHERE state = ?
        ORDER BY job_id DESC, position;
        """,
        (ITEM_REVIEW,)
    ).fetchall()

    kept, changed = _prune_review_rows(
        rows,
        _imported_filepaths() if rows else set()
    )
    if changed:
        commit()

    result: List[Dict[str, Any]] = []
    seen_folders: Set[str] = set()
    for row in rows:
        folder = str(row['folder'])
        if folder in seen_folders:
            continue
        items = kept[int(row['id'])]
        if not items:
            continue
        seen_folders.add(folder)
        result.extend(items)

    return result


def get_job_details(job_id: int) -> Dict[str, Any]:
    review_items = get_review_items(job_id)
    # Review reconciliation can change any subset of held folders, so aggregate
    # counters are intentionally read after pruning rather than conditionally.
    result = get_job_summary(job_id)
    result['review_items'] = review_items
    return result
