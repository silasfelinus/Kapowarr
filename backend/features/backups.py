# -*- coding: utf-8 -*-

"""Create, inspect, retain, and safely stage Kapowarr database backups."""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime
from os import listdir, remove, replace
from os.path import basename, dirname, exists, getmtime, getsize, isfile, join
from shutil import copyfileobj
from tempfile import NamedTemporaryFile, TemporaryDirectory
from threading import Timer
from time import time
from typing import TYPE_CHECKING, Any, Dict, List, Union
from zipfile import ZIP_DEFLATED, BadZipFile, ZipFile

from backend.base.definitions import Constants
from backend.base.files import create_folder, folder_path
from backend.base.helpers import Singleton, get_version_from_pyproject
from backend.base.logging import LOGGER
from backend.internals.db import DBConnection, commit, get_db

if TYPE_CHECKING:
    from flask import Flask

BACKUP_FOLDER = 'Backups'
BACKUP_RE = re.compile(
    r'^kapowarr-(?:backup|pre-restore)-\d{4}-\d{2}-\d{2}-\d{6}\.zip$'
)
BACKUP_DB_MEMBER = Constants.DB_NAME
BACKUP_MANIFEST_MEMBER = 'manifest.json'
PENDING_RESTORE_SUFFIX = '.restore'
SECONDS_PER_DAY = 86_400
AUTO_BACKUP_INTERVAL_DAYS = 7
BACKUP_RETENTION_DAYS = 28


def get_backup_folder() -> str:
    folder = folder_path(BACKUP_FOLDER)
    create_folder(folder)
    return folder


def get_backup_path(filename: str) -> str:
    if basename(filename) != filename or not BACKUP_RE.fullmatch(filename):
        raise ValueError('Invalid backup filename')

    path = join(get_backup_folder(), filename)
    if not isfile(path):
        raise FileNotFoundError(filename)
    return path


def _validate_database_file(filepath: str) -> int:
    """Validate a staged SQLite database before it can replace live state."""
    if not isfile(filepath):
        raise ValueError('Backup database is missing')

    connection = sqlite3.connect(filepath)
    try:
        quick_check = connection.execute('PRAGMA quick_check;').fetchone()
        if not quick_check or quick_check[0] != 'ok':
            raise ValueError('Backup database failed SQLite quick_check')

        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table';"
            )
        }
        if 'config' not in tables:
            raise ValueError('Backup database is not a Kapowarr database')

        version_row = connection.execute(
            "SELECT value FROM config WHERE key = 'database_version';"
        ).fetchone()
        if version_row is None:
            raise ValueError('Backup database has no database version')

        database_version = int(version_row[0])
        from backend.internals.db_migration import DatabaseMigrationHandler
        if database_version > DatabaseMigrationHandler.latest_db_version():
            raise ValueError('Backup database is newer than this Kapowarr version')

        return database_version
    finally:
        connection.close()


def _read_backup_manifest(
    filepath: str,
    verify_crc: bool = False,
) -> Dict[str, Any]:
    try:
        with ZipFile(filepath, 'r') as archive:
            names = set(archive.namelist())
            if BACKUP_DB_MEMBER not in names:
                raise ValueError('Backup archive has no Kapowarr database')
            if not names.issubset({BACKUP_DB_MEMBER, BACKUP_MANIFEST_MEMBER}):
                raise ValueError('Backup archive contains unexpected files')
            if verify_crc and archive.testzip() is not None:
                raise ValueError('Backup archive is corrupt')

            manifest: Dict[str, Any] = {}
            if BACKUP_MANIFEST_MEMBER in names:
                manifest = json.loads(
                    archive.read(BACKUP_MANIFEST_MEMBER).decode('utf-8')
                )
                if not isinstance(manifest, dict):
                    raise ValueError('Backup manifest is invalid')
            return manifest
    except (BadZipFile, json.JSONDecodeError) as error:
        raise ValueError('Backup archive is invalid') from error


def _backup_filename(prefix: str = 'backup') -> str:
    timestamp = datetime.now().strftime('%Y-%m-%d-%H%M%S')
    return f'kapowarr-{prefix}-{timestamp}.zip'


def create_backup(prefix: str = 'backup') -> Dict[str, Any]:
    """Create a transactionally consistent SQLite backup and zip it atomically."""
    if prefix not in ('backup', 'pre-restore'):
        raise ValueError('Invalid backup prefix')

    backup_folder = get_backup_folder()
    filename = _backup_filename(prefix)
    final_path = join(backup_folder, filename)

    # Avoid replacing a backup if two requests land within the same second.
    if exists(final_path):
        raise FileExistsError(filename)

    commit()
    with TemporaryDirectory(dir=backup_folder) as temp_folder:
        temp_db = join(temp_folder, Constants.DB_NAME)
        destination = sqlite3.connect(temp_db)
        try:
            get_db().connection.backup(destination)
            destination.commit()
        finally:
            destination.close()

        database_version = _validate_database_file(temp_db)
        manifest = {
            'created_at': round(time()),
            'app_version': get_version_from_pyproject(folder_path('pyproject.toml')),
            'database_version': database_version,
            'kind': prefix,
        }

        temp_zip = join(temp_folder, filename)
        with ZipFile(temp_zip, 'w', compression=ZIP_DEFLATED) as archive:
            archive.write(temp_db, BACKUP_DB_MEMBER)
            archive.writestr(
                BACKUP_MANIFEST_MEMBER,
                json.dumps(manifest, indent=2, sort_keys=True)
            )

        _read_backup_manifest(temp_zip, verify_crc=True)
        replace(temp_zip, final_path)

    LOGGER.info('Created database backup: %s', filename)
    prune_backups()
    return get_backup(filename)


def get_backup(filename: str) -> Dict[str, Any]:
    path = get_backup_path(filename)
    manifest = _read_backup_manifest(path)
    return {
        'filename': filename,
        'created_at': int(manifest.get('created_at') or getmtime(path)),
        'size': getsize(path),
        'kind': manifest.get('kind', 'backup'),
        'database_version': manifest.get('database_version'),
        'app_version': manifest.get('app_version'),
    }


def list_backups() -> List[Dict[str, Any]]:
    backups = []
    for filename in listdir(get_backup_folder()):
        if not BACKUP_RE.fullmatch(filename):
            continue
        try:
            backups.append(get_backup(filename))
        except (OSError, ValueError):
            LOGGER.exception('Ignoring invalid backup archive: %s', filename)

    backups.sort(key=lambda backup: backup['created_at'], reverse=True)
    return backups


def delete_backup(filename: str) -> None:
    path = get_backup_path(filename)
    remove(path)
    LOGGER.info('Deleted database backup: %s', filename)


def prune_backups() -> None:
    """Delete backup archives older than the familiar 28-day *arr window."""
    cutoff = time() - (BACKUP_RETENTION_DAYS * SECONDS_PER_DAY)
    for backup in list_backups():
        path = join(get_backup_folder(), backup['filename'])
        if getmtime(path) < cutoff:
            remove(path)
            LOGGER.info('Pruned expired database backup: %s', backup['filename'])


def stage_restore(filename: str) -> Dict[str, Any]:
    """Validate a saved backup, preserve current state, and stage it for restart."""
    source = get_backup_path(filename)
    _read_backup_manifest(source, verify_crc=True)

    pre_restore = create_backup(prefix='pre-restore')
    staged_path = DBConnection.file + PENDING_RESTORE_SUFFIX
    db_folder = dirname(DBConnection.file)

    temp_path = ''
    try:
        with NamedTemporaryFile(
            mode='wb',
            prefix='.kapowarr-restore-',
            suffix='.db',
            dir=db_folder,
            delete=False,
        ) as temp_file:
            temp_path = temp_file.name
            with ZipFile(source, 'r') as archive:
                with archive.open(BACKUP_DB_MEMBER, 'r') as backup_db:
                    copyfileobj(backup_db, temp_file)

        _validate_database_file(temp_path)
        replace(temp_path, staged_path)
        temp_path = ''
    finally:
        if temp_path and exists(temp_path):
            remove(temp_path)

    LOGGER.warning(
        'Staged database restore from %s; pre-restore backup is %s',
        filename,
        pre_restore['filename'],
    )
    return {
        'restore_from': filename,
        'pre_restore_backup': pre_restore['filename'],
    }


def apply_pending_restore() -> bool:
    """Apply a staged restore before Kapowarr opens/migrates its live database."""
    database = DBConnection.file
    staged = database + PENDING_RESTORE_SUFFIX
    if not isfile(staged):
        return False

    _validate_database_file(staged)

    # Ensure an old WAL is checkpointed before it is discarded. The previous
    # process has stopped by this point, so no application connection is live.
    if isfile(database):
        current = sqlite3.connect(database)
        try:
            current.execute('PRAGMA wal_checkpoint(TRUNCATE);')
            current.commit()
        finally:
            current.close()

    for suffix in ('-wal', '-shm'):
        sidecar = database + suffix
        if exists(sidecar):
            remove(sidecar)

    replace(staged, database)
    LOGGER.warning('Applied staged database restore before startup')
    return True


class BackupScheduler(metaclass=Singleton):
    """Small daemon timer for the familiar weekly automatic backup cadence."""

    timer: Union[Timer, None] = None
    app: Union['Flask', None] = None

    def start(self, app: 'Flask') -> None:
        self.stop()
        self.app = app

        normal_backups = [
            backup
            for backup in list_backups()
            if backup['kind'] == 'backup'
        ]
        last_backup = normal_backups[0]['created_at'] if normal_backups else time()
        interval = AUTO_BACKUP_INTERVAL_DAYS * SECONDS_PER_DAY
        delay = max(1, round(last_backup + interval - time()))
        self._schedule(delay)
        LOGGER.debug('Next automatic database backup in %d seconds', delay)

    def _schedule(self, delay: int) -> None:
        self.timer = Timer(delay, self._run)
        self.timer.name = 'BackupScheduler'
        self.timer.daemon = True
        self.timer.start()

    def _run(self) -> None:
        try:
            if self.app is None:
                return
            with self.app.app_context():
                create_backup()
        except Exception:
            LOGGER.exception('Automatic database backup failed')
        finally:
            if self.app is not None:
                self._schedule(AUTO_BACKUP_INTERVAL_DAYS * SECONDS_PER_DAY)

    def stop(self) -> None:
        if self.timer is not None:
            self.timer.cancel()
            self.timer = None
