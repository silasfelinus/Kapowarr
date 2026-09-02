# -*- coding: utf-8 -*-

"""Not asking again, quite so soon, for something nobody has.

A volume-level auto search fans out into one full search per missing issue.
On a back-catalogue volume that is enormous and almost entirely futile: in
one hour on 2026-09-02 a single volume consumed 103 searches, 289 issue
searches ran in total, and 284 of them found nothing. The sweep got through
eight volumes, spent the quota of every indexer doing it, and would have
asked the identical questions again the next day, and the day after.

The issues that come back empty are mostly old -- a 1966 Thor, a 1993
Catwoman -- and nobody is going to start seeding them tonight. Meanwhile the
new releases that *would* be found never get reached, because the quota is
gone by then. So the searches that cannot succeed are crowding out the ones
that can.

An issue that comes back empty is therefore asked again later rather than
next time, and later grows each time it disappoints. Nothing is given up on:
the wait is capped, so even a hopeless issue is retried every few weeks, and
the moment one is found the count resets. A human asking for a search is
never subject to any of this -- see `auto_search`, which only consults the
backoff for the unattended sweep.
"""

from __future__ import annotations

from time import time
from typing import Iterable, List, Tuple

from backend.base.logging import LOGGER
from backend.internals.db import commit, get_db

MISS_BACKOFF_SECONDS: Tuple[int, ...] = (
    0,          # never searched, or found last time: no wait
    86400,      # 1 miss  -- a day
    3 * 86400,  # 2       -- three days
    7 * 86400,  # 3       -- a week
    14 * 86400, # 4       -- a fortnight
    30 * 86400  # 5 or more -- a month, and no longer
)
"""How long to leave an issue alone after N consecutive fruitless searches.

Weekly comics mean a day is short enough that a new issue appearing is picked
up promptly, and the tail is long enough that a fifty-year-old issue nobody
has costs twelve searches a year instead of three hundred and sixty-five.
"""


def backoff_for(misses: int) -> int:
    """How long an issue with this many consecutive misses waits.

    Args:
        misses (int): Consecutive fruitless searches.

    Returns:
        int: Seconds to wait before asking again.
    """
    if misses < 0:
        return 0
    return MISS_BACKOFF_SECONDS[min(misses, len(MISS_BACKOFF_SECONDS) - 1)]


def due_issues(
    issues: Iterable[Tuple[int, float]],
    now: float = None # type: ignore
) -> List[Tuple[int, float]]:
    """Filter open issues down to the ones worth asking about again.

    Args:
        issues (Iterable[Tuple[int, float]]): The open issues, as
            `(issue id, calculated issue number)`.

        now (float, optional): The current epoch time, shared by every check
            in one pass. Defaults to None, meaning read the clock.

    Returns:
        List[Tuple[int, float]]: Those whose wait has elapsed. An issue whose
            state cannot be read is included rather than skipped: the cost of
            a wasted search is a search, and the cost of a wrongly skipped one
            is an issue that never arrives.
    """
    issues = list(issues)
    if not issues:
        return []

    if now is None:
        now = time()

    try:
        placeholders = ','.join('?' for _ in issues)
        state = {
            row[0]: (row[1], row[2])
            for row in get_db().execute(
                'SELECT id, last_auto_search, auto_search_misses '
                f'FROM issues WHERE id IN ({placeholders});',
                tuple(issue_id for issue_id, _ in issues)
            ).fetchall()
        }
    except Exception:
        LOGGER.exception(
            'Could not read the search backoff; searching everything: ')
        return issues

    due = []
    for issue in issues:
        last_search, misses = state.get(issue[0], (0, 0))
        if now >= last_search + backoff_for(misses):
            due.append(issue)

    return due


def record_miss(issue_id: int) -> None:
    """Note that a search for this issue found nothing.

    Args:
        issue_id (int): The issue that was searched.
    """
    _write(
        'UPDATE issues SET last_auto_search = ?, '
        'auto_search_misses = auto_search_misses + 1 WHERE id = ?;',
        (round(time()), issue_id)
    )
    return


def record_hit(issue_ids: Iterable[int]) -> None:
    """Note that these issues were found, so they start again from zero.

    Args:
        issue_ids (Iterable[int]): The issues something was found for.
    """
    ids = tuple(issue_ids)
    if not ids:
        return

    placeholders = ','.join('?' for _ in ids)
    _write(
        'UPDATE issues SET last_auto_search = ?, auto_search_misses = 0 '
        f'WHERE id IN ({placeholders});',
        (round(time()), *ids)
    )
    return


def _write(query: str, parameters: tuple) -> None:
    """Run a bookkeeping write that is never worth failing a search over.

    Losing one costs an issue one place in the queue of things to ask about,
    which is not worth ending a sweep for.
    """
    try:
        get_db().execute(query, parameters)
        commit()
    except Exception:
        LOGGER.warning(
            'Could not record the search backoff; the issue keeps its '
            'current standing', exc_info=True)
    return
