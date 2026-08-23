# -*- coding: utf-8 -*-

"""Read-only integrity checks for Kapowarr's live SQLite database."""

from __future__ import annotations

from os.path import getsize, isfile
from pathlib import Path
from sqlite3 import DatabaseError, connect
from typing import List


class DatabaseIntegrityError(RuntimeError):
    """The live SQLite database failed its pre-start integrity check."""

    def __init__(self, filepath: str, details: List[str]) -> None:
        self.filepath = filepath
        self.details = details
        detail = '; '.join(details) if details else 'unknown SQLite integrity error'
        super().__init__(
            f'Database integrity check failed for {filepath}: {detail}. '
            'Kapowarr will not start writers against a damaged database. '
            'Keep this file intact for recovery and restore a known-good backup '
            'from the Backups folder, or recover it offline into a new database.'
        )


def verify_database_integrity(filepath: str) -> None:
    """Fail before startup writes when an existing SQLite database is corrupt.

    New/empty database paths are intentionally ignored: ``setup_db`` owns creating
    their schema. Existing databases are opened read-only so this preflight cannot
    modify the file it is trying to assess.

    Args:
        filepath: Path to the live Kapowarr SQLite database.

    Raises:
        DatabaseIntegrityError: SQLite cannot read the database cleanly.
    """
    if not isfile(filepath) or getsize(filepath) == 0:
        return

    connection = None
    try:
        uri = Path(filepath).resolve().as_uri() + '?mode=ro'
        connection = connect(uri, uri=True)
        rows = connection.execute('PRAGMA quick_check(1);').fetchall()
    except (DatabaseError, OSError) as error:
        raise DatabaseIntegrityError(filepath, [str(error)]) from error
    finally:
        if connection is not None:
            connection.close()

    details = [str(row[0]) for row in rows if row]
    if details != ['ok']:
        raise DatabaseIntegrityError(filepath, details)
