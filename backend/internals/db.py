# -*- coding: utf-8 -*-

"""
Setting up the database, handling connections, using it and closing it.
"""

from __future__ import annotations

from os.path import dirname, exists, isdir, join
from random import uniform
from sqlite3 import (PARSE_DECLTYPES, Connection, Cursor, OperationalError,
                     ProgrammingError, Row, register_adapter,
                     register_converter)
from threading import current_thread
from time import monotonic, sleep, time
from typing import Any, Callable, Dict, Iterable, Iterator, List, Type, Union

from flask import g

from backend.base.definitions import (Constants, DateType, FileDate, ProxyType,
                                      SeedingHandling, SpecialVersion, T)
from backend.base.files import create_folder, folder_path
from backend.base.helpers import CommaList, current_thread_id
from backend.base.logging import LOGGER, set_log_level


# SQLite reports both of its contention errors -- SQLITE_BUSY as "database is
# locked" and SQLITE_LOCKED as "database table is locked" -- through the same
# `OperationalError` it uses for a typo in a query or a missing column, so the
# message is the only thing that tells them apart. Everything else that
# arrives as an `OperationalError` is a real bug that must keep travelling.
LOCK_ERROR_MARKERS = (
    "database is locked",
    "database table is locked"
)


def is_lock_error(error: BaseException) -> bool:
    """Whether an exception is SQLite refusing to wait any longer for a lock.

    Args:
        error (BaseException): The exception to judge.

    Returns:
        bool: Whether the exception is a lock/busy timeout rather than a real
            error in the statement.
    """
    if not isinstance(error, OperationalError):
        return False
    message = str(error).lower()
    return any(marker in message for marker in LOCK_ERROR_MARKERS)


class KapowarrCursor(Cursor):
    row_factory: Union[Type[Row], None] # type: ignore

    @property
    def lastrowid(self) -> int:
        return super().lastrowid or 1

    @property
    def connection(self) -> DBConnection:
        return super().connection # type: ignore

    def __init__(self, cursor: DBConnection, /) -> None:
        super().__init__(cursor)
        return

    @staticmethod
    def _run_waiting_for_lock(
        statement: Callable[..., Any],
        *args: Any,
        **kwargs: Any
    ) -> Any:
        """Run a statement, standing in line again if SQLite reports the
        database as locked instead of giving up on the caller's behalf.

        A statement that fails on a lock never got the write lock, so it never
        wrote anything: re-running it is the same statement, not a second one.

        Args:
            statement (Callable[..., Any]): The underlying `execute` or
                `executemany` to run.

        Raises:
            OperationalError: The database stayed locked for the whole of
                `Constants.DB_LOCK_RETRY_TIMEOUT`, or the error was never
                about a lock in the first place.

        Returns:
            Any: Whatever the statement returned.
        """
        deadline = monotonic() + Constants.DB_LOCK_RETRY_TIMEOUT
        wait = Constants.DB_LOCK_RETRY_FIRST_WAIT
        attempt = 0

        while True:
            try:
                return statement(*args, **kwargs)

            except OperationalError as error:
                if not is_lock_error(error):
                    raise

                remaining = deadline - monotonic()
                if remaining <= 0:
                    LOGGER.error(
                        'Database stayed locked through %d retries; giving up '
                        'on this statement',
                        attempt
                    )
                    raise

                attempt += 1
                # Jitter, so that several threads that piled up behind the
                # same writer don't all come back at the same instant and
                # collide with each other instead.
                delay = min(wait * uniform(0.5, 1.5), remaining)
                LOGGER.warning(
                    'Database is locked; retrying in %.1fs (attempt %d)',
                    delay, attempt
                )
                sleep(delay)
                wait = min(wait * 2, Constants.DB_LOCK_RETRY_MAX_WAIT)

    def execute(self, *args: Any, **kwargs: Any) -> KapowarrCursor:
        return self._run_waiting_for_lock(super().execute, *args, **kwargs)

    def executemany(self, *args: Any, **kwargs: Any) -> KapowarrCursor:
        # `executemany` applies one statement to many rows, and under
        # autocommit each row would land on its own. A retry halfway through
        # would then re-apply the rows that already succeeded, so take the
        # write lock up front and let the whole batch be one transaction:
        # then only the `BEGIN` can meet a lock, and that has written nothing.
        connection = self.connection
        if connection.transaction_depth or connection.in_transaction:
            return self._run_waiting_for_lock(
                super().executemany, *args, **kwargs
            )

        with self:
            return self._run_waiting_for_lock(
                super().executemany, *args, **kwargs
            )

    def fetchonedict(self) -> Union[Dict[str, Any], None]:
        """Same as `fetchone` but convert the Row object to a dict.

        Returns:
            Union[Dict[str, Any], None]: The dict or None in case of no result.
        """
        r = self.fetchone()
        if r is None:
            return r
        return dict(r)

    def fetchmanydict(self, size: Union[int, None] = 1) -> List[Dict[str, Any]]:
        """Same as `fetchmany` but convert the Row object to a dict.

        Args:
            size (Union[int, None], optional): The amount of rows to return.
                Defaults to 1.

        Returns:
            List[Dict[str, Any]]: The rows.
        """
        return [dict(e) for e in self.fetchmany(size)]

    def fetchalldict(self) -> List[Dict[str, Any]]:
        """Same as `fetchall` but convert the Row object to a dict.

        Returns:
            List[Dict[str, Any]]: The results.
        """
        return [dict(e) for e in self]

    def exists(self) -> Union[Any, None]:
        """Return the first column of the first row, or `None` if not found.

        Returns:
            Union[Any, None]: The value of the first column of the first row,
                or `None` if not found.
        """
        r = self.fetchone()
        if r is None:
            return r
        return r[0]

    def __enter__(self):
        """Start a transaction, or join the one already running.

        Nesting has to be allowed because these blocks call each other:
        `refresh_and_scan` wraps its own writes and calls `scan_files`, which
        wraps its own. SQLite has no nested transactions, so the outermost
        block owns the real one and the inner blocks ride along.
        """
        connection = self.connection
        if not connection.transaction_depth:
            # IMMEDIATE, not the default DEFERRED -- see
            # `WRITE_TRANSACTION_MODE`.
            self.execute(f"BEGIN {WRITE_TRANSACTION_MODE};")
        connection.transaction_depth += 1
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Commit the transaction or rollback if an exception occurred"""
        connection = self.connection
        connection.transaction_depth = max(
            connection.transaction_depth - 1, 0
        )

        if connection.transaction_depth:
            # An enclosing block owns the transaction. An exception in here is
            # still on its way up to that block, which will roll back for us.
            return

        if connection.in_transaction:
            if exc_type is not None:
                self.execute("ROLLBACK;")
            else:
                self.execute("COMMIT;")

        return


class DBConnectionManager(type):
    instances: Dict[int, DBConnection] = {}

    def __call__(cls, **kwargs: Any) -> DBConnection:
        thread_id = current_thread_id()

        if (
            not thread_id in cls.instances
            or cls.instances[thread_id].closed
        ):
            cls.instances[thread_id] = super().__call__(**kwargs)

        return cls.instances[thread_id]

    @classmethod
    def close_connection_of_thread(cls) -> None:
        """Close the DB connection of the current thread"""
        thread_id = current_thread_id()
        if (
            thread_id in cls.instances
            and not cls.instances[thread_id].closed
        ):
            cls.instances[thread_id].close()
            del cls.instances[thread_id]
        return


# The mode every explicit transaction is opened in. DEFERRED -- SQLite's
# default -- takes no lock until the first statement, so a transaction that
# reads before it writes holds a read lock and then has to upgrade. If
# another connection took the write lock in between, SQLite cannot make that
# upgrade wait: both sides would be waiting on each other, so it returns
# SQLITE_BUSY at once. `timeout` does not help, because the busy handler is
# exactly what SQLite refuses to run here.
#
# That is not a hypothetical. Two tasks died of it in one import run
# (2026-08-24): `Search All` in `grab_size_limits._ensure_defaults`, and
# `refresh_and_scan` in `scan_files` -- both read-then-write, both killed
# mid-run by "database is locked" while the library import held the writer.
#
# IMMEDIATE takes the write lock up front instead, which the busy handler
# *can* wait on, so contention becomes a wait rather than an instant failure.
WRITE_TRANSACTION_MODE = "IMMEDIATE"

# `isolation_level` is sqlite3's legacy transaction control: set to a mode, it
# opens a transaction before the first write and then keeps it open until
# something calls `commit()`. That is the wrong default for Kapowarr, because
# connections here are cached per thread and the threads live as long as the
# process (see `DBConnectionManager`). Of the ~83 functions in the backend
# that write, 75 never commit at all -- they leave that to a caller, or to
# nobody. Under legacy mode every one of those leaves the write lock held on
# its thread's connection until that thread happens to write-and-commit again,
# which may be minutes of network I/O later.
#
# That is what killed three threads on 2026-09-01: two `DownloadThread`s in
# `remove_from_queue` and, worse, the `TaskIntervalThread` in
# `__check_intervals` -- and the interval thread reschedules itself at the end
# of that method, so losing it meant no scheduled task ran again until Silas
# restarted the container. All three waited out the full `DB_TIMEOUT` while a
# `Search All` sweep sat on an open transaction it had no further use for,
# 80 seconds at a time, waiting on an indexer.
#
# `None` is autocommit: a lone write commits the instant it completes and the
# write lock is held for microseconds. Where several statements genuinely have
# to land together, say so with `with cursor:`, which opens a real IMMEDIATE
# transaction for exactly as long as the block runs.
AUTOCOMMIT = None

CONNECTION_PRAGMAS = (
    "PRAGMA foreign_keys = ON;",

    # The database is in WAL mode (see `setup_db`), which already lets readers
    # run while a writer holds the write lock. `synchronous` is per-connection
    # though, and defaults to FULL: every commit then waits on an fsync. Long
    # write-heavy jobs (library import, refresh & scan, post-processing) commit
    # in tight loops -- see `iter_commit` -- so with FULL they turn into a
    # stream of fsyncs that saturates the disk and starves the read queries the
    # web UI is waiting on. NORMAL is the value SQLite documents as the
    # sensible pairing with WAL: a crash of Kapowarr itself still can't corrupt
    # or lose committed data, only a power loss/OS crash can drop the most
    # recent transactions, which for a library index is a re-scan and not a
    # disaster.
    "PRAGMA synchronous = NORMAL;",

    # Negative values are KiB of page cache rather than a page count, so this
    # is an 8 MiB cache per connection instead of the 2 MiB default. Modest
    # rather than generous on purpose: this is paid per connection, and
    # Kapowarr is routinely run on a Pi or a NAS where a per-thread 32 MiB
    # cache would be a worse trade than the extra page reads it saves.
    "PRAGMA cache_size = -8000;",

    # Keep temp b-trees (ORDER BY, the correlated subqueries in the library
    # listing) in memory instead of spilling them to a temp file.
    "PRAGMA temp_store = MEMORY;",
)
"Applied to every new connection, in order."


class DBConnection(Connection, metaclass=DBConnectionManager):
    file = ''

    transaction_depth: int = 0
    """How many `with cursor:` blocks are currently open on this connection.
    Only the outermost one begins and ends the actual transaction."""

    def __init__(
        self, *,
        timeout: float = Constants.DB_TIMEOUT
    ) -> None:
        """Create a connection with a database

        Args:
            timeout (float, optional): How long to wait before giving up
                on a command.
                Defaults to Constants.DB_TIMEOUT.
        """
        self.closed = False
        LOGGER.debug(f'Creating connection {self}')
        super().__init__(
            self.file,
            timeout=timeout,
            detect_types=PARSE_DECLTYPES
        )
        self.isolation_level = AUTOCOMMIT
        self.transaction_depth = 0
        c = super().cursor()
        for pragma in CONNECTION_PRAGMAS:
            c.execute(pragma)
        return

    def cursor( # type: ignore
        self,
        force_new: bool = False
    ) -> KapowarrCursor:
        """Get a database cursor from the connection.

        Args:
            force_new (bool, optional): Get a new cursor instead of the cached
                one.
                Defaults to False.

        Returns:
            KapowarrCursor: The database cursor.
        """
        if not hasattr(g, 'cursors'):
            g.cursors = []

        if not g.cursors:
            c = KapowarrCursor(self)
            c.row_factory = Row
            g.cursors.append(c)

        if not force_new:
            return g.cursors[0]
        else:
            c = KapowarrCursor(self)
            c.row_factory = Row
            g.cursors.append(c)
            return g.cursors[-1]

    def close(self) -> None:
        """Close the database connection"""
        LOGGER.debug(f'Closing connection {self}')
        self.closed = True
        super().close()
        return

    def __repr__(self) -> str:
        return f'<{self.__class__.__name__}; {current_thread().name}; {id(self)}; closed={self.closed}>'


def set_db_location(
    db_folder: Union[str, None]
) -> None:
    """Setup database location. Create folder for database and set location for
    `db.DBConnection`.

    Args:
        db_folder (Union[str, None], optional): The folder in which the database
            will be stored or in which a database is for Kapowarr to use. Give
            `None` for the default location.

    Raises:
        ValueError: Value of `db_folder` exists but is not a folder.
    """
    if db_folder:
        if exists(db_folder) and not isdir(db_folder):
            raise ValueError('Database location is not a folder')

    db_file_location = join(
        db_folder or folder_path(*Constants.DB_FOLDER),
        Constants.DB_NAME
    )

    LOGGER.debug(f'Setting database location: {db_file_location}')

    create_folder(dirname(db_file_location))

    DBConnection.file = db_file_location

    return


def get_db(force_new: bool = False) -> KapowarrCursor:
    """Get a database cursor instance or create a new one if needed.

    Args:
        force_new (bool, optional): Decides whether a new cursor is
            returned instead of the standard one.
            Defaults to False.

    Returns:
        KapowarrCursor: Database cursor instance that outputs Row objects.
    """
    return DBConnection().cursor(force_new=force_new)


def commit() -> None:
    """Commit the database changes.

    Does nothing while a `with cursor:` block is open: that block owns the
    transaction and commits it on the way out, so committing here would cut
    the block in half and leave the rest of it unprotected.
    """
    connection = get_db().connection
    if connection.transaction_depth:
        return

    connection.commit()
    return


def iter_commit(iterable: Iterable[T]) -> Iterator[T]:
    """Commit the database after yielding each value in the iterable. Also
    commits just before the first iteration starts.

    ```
    # commits
    for i in iter_commit(iterable):
        ...
        # commits
    ```

    Args:
        iterable (Iterable[T]): Iterable that will be iterated over like normal.

    Yields:
        Iterator[T]: Items of iterable.
    """
    commit()
    for i in iterable:
        yield i
        commit()
    return


def close_db(e: Union[BaseException, None] = None) -> None:
    """Close database cursor, commit database and close database.

    Args:
        e (Union[BaseException, None], optional): Error. Defaults to None.
    """
    if not hasattr(g, 'cursors'):
        return

    try:
        cursors = g.cursors
        db: DBConnection = cursors[0].connection
        for c in cursors:
            c.close()
        delattr(g, 'cursors')
        db.commit()
        if not current_thread().name.startswith('waitress-'):
            DBConnectionManager.close_connection_of_thread()

    except ProgrammingError:
        pass

    return


def setup_db_adapters_and_converters() -> None:
    """Add DB adapters and converters for custom types and bool"""
    register_adapter(bool, lambda b: int(b))
    register_converter("BOOL", lambda b: b == b'1')
    register_adapter(CommaList, lambda c: str(c))
    register_adapter(ProxyType, lambda e: e.value)
    register_adapter(FileDate, lambda e: e.value)
    register_adapter(SeedingHandling, lambda e: e.value)
    register_adapter(SpecialVersion, lambda e: e.value)
    register_adapter(DateType, lambda e: e.value)
    return


def setup_db() -> None:
    """Setup the default config and database connection and tables"""
    from backend.internals.db_migration import DatabaseMigrationHandler
    from backend.internals.settings import Settings, task_intervals

    cursor = get_db()
    cursor.execute("PRAGMA journal_mode = wal;")
    setup_db_adapters_and_converters()

    cursor.executescript(DB_SCHEMA)

    settings = Settings()
    settings_values = settings.get_settings()

    set_log_level(settings_values.log_level)

    DatabaseMigrationHandler.migrate()

    # Generate api key
    if not settings_values.api_key:
        settings.generate_api_key()

    # Add task intervals
    LOGGER.debug(f'Inserting task intervals: {task_intervals}')
    current_time = round(time())
    cursor.executemany(
        """
        INSERT INTO task_intervals
        VALUES (?, ?, ?)
        ON CONFLICT(task_name) DO
        UPDATE
        SET
            interval = ?;
        """,
        ((k, v, current_time, v) for k, v in task_intervals.items())
    )

    return


DB_SCHEMA = """
CREATE TABLE IF NOT EXISTS config(
    key VARCHAR(100) PRIMARY KEY,
    value BLOB
);
CREATE TABLE IF NOT EXISTS root_folders(
    id INTEGER PRIMARY KEY,
    folder VARCHAR(254) UNIQUE NOT NULL
);
CREATE TABLE IF NOT EXISTS volumes(
    id INTEGER PRIMARY KEY,
    comicvine_id INTEGER,
    title VARCHAR(255) NOT NULL,
    alt_title VARCHAR(255),
    year INTEGER(5),
    publisher VARCHAR(255),
    volume_number INTEGER(8) DEFAULT 1,
    description TEXT,
    site_url TEXT NOT NULL DEFAULT "",
    monitored BOOL NOT NULL DEFAULT 0,
    monitor_new_issues BOOL NOT NULL DEFAULT 1,
    root_folder INTEGER NOT NULL,
    folder TEXT,
    custom_folder BOOL NOT NULL DEFAULT 0,
    last_cv_fetch INTEGER(8) DEFAULT 0,
    special_version VARCHAR(255),
    special_version_locked BOOL NOT NULL DEFAULT 0,
    last_auto_search INTEGER(8) NOT NULL DEFAULT 0,

    FOREIGN KEY (root_folder) REFERENCES root_folders(id)
);
CREATE TABLE IF NOT EXISTS volumes_covers(
    volume_id INTEGER UNIQUE NOT NULL,
    cover BLOB,
    provider_id VARCHAR(50),
    external_id TEXT,
    source_url TEXT,
    FOREIGN KEY (volume_id) REFERENCES volumes(id)
        ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS volumes_covers_volume_id_index
    ON volumes_covers(volume_id);
CREATE TABLE IF NOT EXISTS issues(
    id INTEGER PRIMARY KEY,
    volume_id INTEGER NOT NULL,
    comicvine_id INTEGER UNIQUE,
    issue_number VARCHAR(20) NOT NULL,
    calculated_issue_number FLOAT(20) NOT NULL,
    title VARCHAR(255),
    date VARCHAR(10),
    description TEXT,
    monitored BOOL NOT NULL DEFAULT 1,

    FOREIGN KEY (volume_id) REFERENCES volumes(id)
        ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS issues_volume_number_index
    ON issues(volume_id, calculated_issue_number);
CREATE INDEX IF NOT EXISTS issues_volume_index
    ON issues(volume_id);
CREATE TABLE IF NOT EXISTS volume_external_ids(
    volume_id INTEGER NOT NULL,
    provider_id VARCHAR(50) NOT NULL,
    external_id TEXT NOT NULL,
    source_url TEXT,
    updated_at INTEGER NOT NULL,

    PRIMARY KEY (volume_id, provider_id),
    UNIQUE (provider_id, external_id),
    FOREIGN KEY (volume_id) REFERENCES volumes(id)
        ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS issue_external_ids(
    issue_id INTEGER NOT NULL,
    provider_id VARCHAR(50) NOT NULL,
    external_id TEXT NOT NULL,
    source_url TEXT,
    updated_at INTEGER NOT NULL,

    PRIMARY KEY (issue_id, provider_id),
    UNIQUE (provider_id, external_id),
    FOREIGN KEY (issue_id) REFERENCES issues(id)
        ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS files(
    id INTEGER PRIMARY KEY,
    filepath TEXT UNIQUE NOT NULL,
    size INTEGER
);
CREATE TABLE IF NOT EXISTS issues_files(
    file_id INTEGER NOT NULL,
    issue_id INTEGER NOT NULL,
    forced BOOL NOT NULL DEFAULT 0,

    FOREIGN KEY (file_id) REFERENCES files(id)
        ON DELETE CASCADE,
    FOREIGN KEY (issue_id) REFERENCES issues(id),
    CONSTRAINT PK_issues_files PRIMARY KEY (
        file_id,
        issue_id
    )
);
CREATE INDEX IF NOT EXISTS issues_files_issue_id_index
    ON issues_files(issue_id);
CREATE TABLE IF NOT EXISTS volume_files(
    file_id INTEGER PRIMARY KEY,
    volume_id INTEGER NOT NULL,
    file_type VARCHAR(15) NOT NULL,
    forced BOOL NOT NULL DEFAULT 0,

    FOREIGN KEY (volume_id) REFERENCES volumes(id)
        ON DELETE CASCADE,
    FOREIGN KEY (file_id) REFERENCES files(id)
        ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS external_download_clients(
    id INTEGER PRIMARY KEY,
    download_type INTEGER NOT NULL,
    client_type VARCHAR(255) NOT NULL,
    title VARCHAR(255) NOT NULL,
    base_url TEXT NOT NULL,
    username VARCHAR(255),
    password VARCHAR(255),
    api_token VARCHAR(255)
);
CREATE TABLE IF NOT EXISTS download_queue(
    id INTEGER PRIMARY KEY,
    volume_id INTEGER NOT NULL,
    client_type VARCHAR(255) NOT NULL,
    external_client_id INTEGER,

    download_link TEXT NOT NULL,
    covered_issues VARCHAR(255),
    force_original_name BOOL,

    source_type VARCHAR(25) NOT NULL,
    source_name VARCHAR(255) NOT NULL,

    web_link TEXT,
    web_title TEXT,
    web_sub_title TEXT,

    FOREIGN KEY (external_client_id) REFERENCES external_download_clients(id),
    FOREIGN KEY (volume_id) REFERENCES volumes(id)
);
CREATE TABLE IF NOT EXISTS download_history(
    web_link TEXT,
    web_title TEXT,
    web_sub_title TEXT,
    file_title TEXT,

    volume_id INTEGER,
    issue_id INTEGER,

    source VARCHAR(25),
    downloaded_at INTEGER NOT NULL CHECK (downloaded_at > 0),
    success BOOL,

    FOREIGN KEY (volume_id) REFERENCES volumes(id)
        ON DELETE SET NULL,
    FOREIGN KEY (issue_id) REFERENCES issues(id)
        ON DELETE SET NULL
);
CREATE TABLE IF NOT EXISTS task_history(
    task_name NOT NULL,
    display_title NOT NULL,
    run_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS task_intervals(
    task_name PRIMARY KEY,
    interval INTEGER NOT NULL,
    next_run INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS blocklist(
    id INTEGER PRIMARY KEY,
    volume_id INTEGER,
    issue_id INTEGER,

    web_link TEXT,
    web_title TEXT,
    web_sub_title TEXT,

    download_link TEXT UNIQUE,
    source VARCHAR(30),

    reason INTEGER NOT NULL CHECK (reason > 0),
    added_at INTEGER NOT NULL CHECK (added_at > 0),

    FOREIGN KEY (volume_id) REFERENCES volumes(id)
        ON DELETE SET NULL,
    FOREIGN KEY (issue_id) REFERENCES issues(id)
        ON DELETE SET NULL
);
CREATE TABLE IF NOT EXISTS credentials(
    id INTEGER PRIMARY KEY,
    source VARCHAR(30) NOT NULL,
    username TEXT,
    email TEXT,
    password TEXT,
    api_key TEXT
);
CREATE TABLE IF NOT EXISTS remote_mappings(
    id INTEGER PRIMARY KEY,
    external_download_client_id INTEGER NOT NULL,
    remote_path TEXT NOT NULL,
    local_path TEXT NOT NULL,

    FOREIGN KEY (external_download_client_id)
        REFERENCES external_download_clients(id)
        ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS notification_services(
    id INTEGER PRIMARY KEY,
    service_type VARCHAR(30) NOT NULL,
    title VARCHAR(255) NOT NULL,
    url TEXT NOT NULL,
    events VARCHAR(255) NOT NULL DEFAULT "",
    enabled BOOL NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS indexers(
    id INTEGER PRIMARY KEY,
    title VARCHAR(255) NOT NULL,
    base_url TEXT NOT NULL,
    api_key VARCHAR(255) NOT NULL,
    enabled BOOL NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS pull_list_entries(
    id INTEGER PRIMARY KEY,
    volume_id INTEGER,
    issue_id INTEGER,
    comicvine_volume_id INTEGER,
    comicvine_issue_id INTEGER,
    issue_number VARCHAR(20),
    release_title VARCHAR(255) NOT NULL,
    publisher VARCHAR(255),
    release_date DATE,
    cover_date DATE,
    week_start DATE NOT NULL,
    year INTEGER(5),
    source VARCHAR(50) NOT NULL,
    link TEXT NOT NULL,
    availability_source VARCHAR(50),
    availability_link TEXT,
    checked_at INTEGER NOT NULL,

    FOREIGN KEY (volume_id) REFERENCES volumes(id)
        ON DELETE SET NULL,
    FOREIGN KEY (issue_id) REFERENCES issues(id)
        ON DELETE SET NULL
);
CREATE TABLE IF NOT EXISTS publisher_subscriptions(
    publisher VARCHAR(255) PRIMARY KEY COLLATE NOCASE,
    root_folder_id INTEGER NOT NULL,
    auto_search BOOL NOT NULL DEFAULT 0,

    FOREIGN KEY (root_folder_id) REFERENCES root_folders(id)
        ON DELETE RESTRICT
);
CREATE TABLE IF NOT EXISTS publisher_automation_history(
    release_key VARCHAR(255) NOT NULL,
    action VARCHAR(20) NOT NULL,
    success BOOL NOT NULL,
    message TEXT,
    attempted_at INTEGER NOT NULL,

    PRIMARY KEY (release_key, action)
);
"""
