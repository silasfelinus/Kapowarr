# -*- coding: utf-8 -*-

import json
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from backend.base.definitions import Constants
from backend.features import backups
from backend.features.tasks import task_library
from backend.internals.db import DBConnection
from backend.internals.db_migration import DatabaseMigrationHandler


class BackupTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.original_db_file = DBConnection.file
        self.addCleanup(setattr, DBConnection, 'file', self.original_db_file)

    def create_database(self, path, marker='current', version=None):
        if version is None:
            version = DatabaseMigrationHandler.latest_db_version()
        connection = sqlite3.connect(path)
        try:
            connection.execute(
                'CREATE TABLE config(key VARCHAR(100) PRIMARY KEY, value BLOB);'
            )
            connection.execute(
                'INSERT INTO config(key, value) VALUES (?, ?);',
                ('database_version', version),
            )
            connection.execute('CREATE TABLE marker(value TEXT);')
            connection.execute('INSERT INTO marker(value) VALUES (?);', (marker,))
            connection.commit()
        finally:
            connection.close()

    def create_archive(self, path, database, manifest=None, extra=None):
        with ZipFile(path, 'w') as archive:
            archive.write(database, Constants.DB_NAME)
            archive.writestr(
                backups.BACKUP_MANIFEST_MEMBER,
                json.dumps(manifest or {'created_at': 1, 'kind': 'backup'}),
            )
            if extra is not None:
                archive.writestr(extra, 'unexpected')

    def read_marker(self, path):
        connection = sqlite3.connect(path)
        try:
            return connection.execute('SELECT value FROM marker;').fetchone()[0]
        finally:
            connection.close()

    def _schedule(self, interval_days=7, keep_count=4):
        return patch.object(
            backups,
            'get_backup_schedule',
            return_value={
                'interval_days': interval_days,
                'interval_seconds': interval_days * backups.SECONDS_PER_DAY,
                'keep_count': keep_count,
            },
        )

    def test_backup_task_is_registered_with_shared_task_scheduler(self):
        self.assertIs(
            task_library[backups.DatabaseBackup.action],
            backups.DatabaseBackup,
        )

    def test_scheduler_adapter_enrolls_persistent_interval(self):
        with patch.object(backups, 'ensure_backup_interval') as ensure:
            backups.BackupScheduler().start(object())
        ensure.assert_called_once_with()

    def _interval_db(self):
        connection = sqlite3.connect(':memory:')
        self.addCleanup(connection.close)
        connection.execute(
            'CREATE TABLE task_intervals('
            'task_name PRIMARY KEY, interval INTEGER NOT NULL, next_run INTEGER NOT NULL);'
        )
        return connection

    def _read_interval(self, connection):
        return connection.execute(
            'SELECT interval, next_run FROM task_intervals WHERE task_name = ?;',
            (backups.DatabaseBackup.action,),
        ).fetchone()

    def test_ensure_backup_interval_preserves_existing_next_run(self):
        """A restart must not push the pending backup out by another interval."""
        connection = self._interval_db()
        cursor = connection.cursor()

        with patch.object(backups, 'get_db', return_value=cursor), patch.object(
            backups, 'commit', side_effect=connection.commit,
        ), self._schedule(interval_days=7):
            backups.ensure_backup_interval()
            self.assertEqual(
                self._read_interval(connection)[0], 7 * backups.SECONDS_PER_DAY
            )

            connection.execute(
                'UPDATE task_intervals SET next_run = 12345 WHERE task_name = ?;',
                (backups.DatabaseBackup.action,),
            )
            connection.commit()
            backups.ensure_backup_interval()

        self.assertEqual(
            self._read_interval(connection),
            (7 * backups.SECONDS_PER_DAY, 12345),
        )

    def test_changing_the_frequency_reschedules_the_pending_run(self):
        """Otherwise the pending run keeps the schedule the old interval set.

        Going from monthly to daily would still mean waiting out the rest of
        the month before the new frequency took effect.
        """
        connection = self._interval_db()
        cursor = connection.cursor()

        with patch.object(backups, 'get_db', return_value=cursor), patch.object(
            backups, 'commit', side_effect=connection.commit,
        ):
            with self._schedule(interval_days=30):
                backups.ensure_backup_interval()
            connection.execute(
                'UPDATE task_intervals SET next_run = ? WHERE task_name = ?;',
                (2_000_000_000, backups.DatabaseBackup.action),
            )
            connection.commit()

            with self._schedule(interval_days=1):
                backups.ensure_backup_interval()

        interval, next_run = self._read_interval(connection)
        self.assertEqual(interval, backups.SECONDS_PER_DAY)
        self.assertLess(
            next_run, 2_000_000_000,
            'a shortened interval has to bring the pending run forward'
        )

    def _seed_backups(self, entries):
        """Write archives into the backup folder, newest `created_at` last."""
        DBConnection.file = os.path.join(self.temp.name, Constants.DB_NAME)
        folder = backups.get_backup_folder()
        database = os.path.join(self.temp.name, 'seed.db')
        self.create_database(database)
        for index, (kind, created_at) in enumerate(entries):
            name = (
                f'kapowarr-{kind}-2026-08-'
                f'{(index % 28) + 1:02d}-{index:06d}.zip'
            )
            self.create_archive(
                os.path.join(folder, name),
                database,
                manifest={'created_at': created_at, 'kind': kind},
            )

    def _remaining(self):
        return sorted(b['created_at'] for b in backups.list_backups())

    def test_pruning_keeps_the_configured_number_of_backups(self):
        self._seed_backups([('backup', t) for t in (10, 20, 30, 40, 50)])

        with self._schedule(keep_count=2):
            backups.prune_backups()

        self.assertEqual(
            self._remaining(), [40, 50],
            'the newest are the ones worth keeping'
        )

    def test_pruning_keeps_everything_when_under_the_limit(self):
        self._seed_backups([('backup', t) for t in (10, 20)])

        with self._schedule(keep_count=5):
            backups.prune_backups()

        self.assertEqual(self._remaining(), [10, 20])

    def test_pre_restore_backups_are_outside_the_retention_count(self):
        """They are the undo for a restore, so ordinary retention must not
        be able to delete one -- and they must not consume the budget the
        scheduled backups are counted against either."""
        self._seed_backups([
            ('pre-restore', 5),
            ('backup', 10),
            ('backup', 20),
            ('backup', 30),
        ])

        with self._schedule(keep_count=2):
            backups.prune_backups()

        self.assertEqual(self._remaining(), [5, 20, 30])

    def test_backup_folder_follows_configured_database_folder(self):
        DBConnection.file = os.path.join(self.temp.name, Constants.DB_NAME)
        self.assertEqual(
            backups.get_backup_folder(),
            os.path.join(self.temp.name, backups.BACKUP_FOLDER),
        )

    def test_validates_kapowarr_database_and_rejects_future_schema(self):
        current = os.path.join(self.temp.name, 'current.db')
        future = os.path.join(self.temp.name, 'future.db')
        self.create_database(current)
        self.create_database(
            future,
            version=DatabaseMigrationHandler.latest_db_version() + 1,
        )

        self.assertEqual(
            backups._validate_database_file(current),
            DatabaseMigrationHandler.latest_db_version(),
        )
        with self.assertRaises(ValueError):
            backups._validate_database_file(future)

    def test_backup_archive_rejects_unexpected_members(self):
        database = os.path.join(self.temp.name, 'source.db')
        archive = os.path.join(self.temp.name, 'backup.zip')
        self.create_database(database)
        self.create_archive(archive, database, extra='../escape.txt')

        with self.assertRaises(ValueError):
            backups._read_backup_manifest(archive, verify_crc=True)

    def test_stage_restore_validates_before_creating_pending_database(self):
        current = os.path.join(self.temp.name, Constants.DB_NAME)
        restored = os.path.join(self.temp.name, 'restored.db')
        archive = os.path.join(self.temp.name, 'kapowarr-backup-2026-08-19-000000.zip')
        self.create_database(current, marker='current')
        self.create_database(restored, marker='restored')
        self.create_archive(archive, restored)
        DBConnection.file = current

        with patch.object(backups, 'get_backup_path', return_value=archive), patch.object(
            backups,
            'create_backup',
            return_value={'filename': 'kapowarr-pre-restore-2026-08-19-000001.zip'},
        ):
            result = backups.stage_restore(os.path.basename(archive))

        self.assertTrue(os.path.isfile(current + backups.PENDING_RESTORE_SUFFIX))
        self.assertEqual(self.read_marker(current), 'current')
        self.assertEqual(
            self.read_marker(current + backups.PENDING_RESTORE_SUFFIX),
            'restored',
        )
        self.assertEqual(
            result['pre_restore_backup'],
            'kapowarr-pre-restore-2026-08-19-000001.zip',
        )

    def test_apply_pending_restore_replaces_database_before_startup(self):
        current = os.path.join(self.temp.name, Constants.DB_NAME)
        staged = current + backups.PENDING_RESTORE_SUFFIX
        self.create_database(current, marker='current')
        self.create_database(staged, marker='restored')
        DBConnection.file = current

        self.assertTrue(backups.apply_pending_restore())
        self.assertFalse(os.path.exists(staged))
        self.assertEqual(self.read_marker(current), 'restored')

    def test_safe_backup_path_rejects_traversal(self):
        with self.assertRaises(ValueError):
            backups.get_backup_path('../kapowarr-backup-2026-08-19-000000.zip')


if __name__ == '__main__':
    unittest.main()
