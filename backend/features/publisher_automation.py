# -*- coding: utf-8 -*-

"""Bulk helpers for pull-list publisher automation."""

from typing import Any, Dict, List, Tuple

from backend.internals.db import get_db


def set_all_publisher_subscriptions(root_folder_id: int) -> Dict[str, Any]:
    """Enable auto-add + grab for every publisher in the stored catalogue."""
    cursor = get_db()
    publishers = [
        row['publisher']
        for row in cursor.execute(
            """
            SELECT DISTINCT publisher
            FROM pull_list_entries
            WHERE publisher IS NOT NULL AND publisher != ''
            ORDER BY publisher COLLATE NOCASE;
            """
        ).fetchalldict()
    ]
    values: List[Tuple[str, int]] = [
        (publisher, root_folder_id) for publisher in publishers
    ]

    with cursor:
        cursor.executemany(
            """
            INSERT INTO publisher_subscriptions(
                publisher, root_folder_id, auto_search
            ) VALUES (?, ?, 1)
            ON CONFLICT(publisher) DO UPDATE SET
                root_folder_id = excluded.root_folder_id,
                auto_search = 1;
            """,
            values
        )

    return {
        'updated': len(publishers),
        'root_folder_id': root_folder_id,
        'auto_search': True
    }
