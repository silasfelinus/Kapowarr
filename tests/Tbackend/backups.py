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
