# -*- coding: utf-8 -*-

"""Global Wanted/Missing query helpers.

The Wanted view is deliberately a projection over the existing library state,
not another persisted queue. An issue is wanted when both its volume and issue
are monitored and no library file is linked to it.
"""

from typing import Any, Dict, Union

from backend.base.definitions import WantedIssuesResponse
from backend.internals.db import get_db

DEFAULT_WANTED_LIMIT = 100
MAX_WANTED_LIMIT = 250


def get_wanted_issues(
    query: Union[str, None] = None,
    offset: int = 0,
    limit: int = DEFAULT_WANTED_LIMIT
) -> WantedIssuesResponse:
    """Return a paged global view of monitored issues missing a file."""
    search = (query or '').strip().lower()
    params: Dict[str, Any] = {
        'offset': offset,
        'limit': limit,
        'query': f'%{search}%'
    }

    where = """
        v.monitored = 1
        AND i.monitored = 1
        AND NOT EXISTS (
            SELECT 1
            FROM issues_files issue_file
            WHERE issue_file.issue_id = i.id
        )
    """
    if search:
        where += """
            AND (
                LOWER(v.title) LIKE :query
                OR LOWER(COALESCE(i.title, '')) LIKE :query
                OR LOWER(i.issue_number) LIKE :query
                OR LOWER(COALESCE(v.publisher, '')) LIKE :query
            )
        """

    cursor = get_db()
    total = cursor.execute(
        f"""
        SELECT COUNT(*)
        FROM issues i
        INNER JOIN volumes v ON v.id = i.volume_id
        WHERE {where};
        """,
        params
    ).fetchone()[0]

    items = cursor.execute(
        f"""
        SELECT
            i.id AS issue_id,
            i.volume_id AS volume_id,
            v.title AS volume_title,
            v.year AS volume_year,
            v.publisher AS publisher,
            i.issue_number AS issue_number,
            i.calculated_issue_number AS calculated_issue_number,
            i.title AS issue_title,
            i.date AS issue_date
        FROM issues i
        INNER JOIN volumes v ON v.id = i.volume_id
        WHERE {where}
        ORDER BY
            COALESCE(i.date, '0000-00-00') DESC,
            LOWER(v.title),
            i.calculated_issue_number
        LIMIT :limit OFFSET :offset;
        """,
        params
    ).fetchalldict()

    return {
        'items': items,
        'total': total,
        'offset': offset,
        'limit': limit,
        'query': query or ''
    }
