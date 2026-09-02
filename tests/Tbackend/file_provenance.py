import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from Tbackend.a_test_database_behaves_like_the_real_one import (
    connect as connect_test_db, cursor as test_db_cursor)
from backend.features import file_provenance
from backend.features.post_processing import (
    PostProcessor,
    PostProcessorTorrentsComplete,
    PostProcessorTorrentsCopy,
    convert_file,
    record_download_file_provenance,
    reset_file_link,
)


class durable_file_provenance(unittest.TestCase):
    def setUp(self):
        self.connection = connect_test_db()
        self.connection.execute(
            'CREATE TABLE files('
            'id INTEGER PRIMARY KEY, filepath TEXT UNIQUE NOT NULL, size INTEGER);'
        )
        self.cursor = test_db_cursor(self.connection)
        self.get_db_patch = patch.object(
            file_provenance,
            'get_db',
            return_value=self.cursor,
        )
        self.get_db_patch.start()

    def tearDown(self):
        self.get_db_patch.stop()
        self.connection.close()

    @staticmethod
    def _download(filepath, source_name='NZBgeek'):
        return SimpleNamespace(
            files=[filepath],
            source_type=SimpleNamespace(value='Usenet indexer'),
            source_name=source_name,
            title='Batman 001 (2025)',
            web_title='Batman #1',
            web_sub_title='Digital',
            download_link='https://example.test/get?nzb=1&apikey=SECRET',
            pure_link='magnet:?xt=urn:btih:SECRET',
        )

    def test_records_source_context_without_sensitive_download_urls(self):
        with tempfile.TemporaryDirectory() as root:
            filepath = os.path.join(root, 'Batman 001.cbz')
            with open(filepath, 'wb') as handle:
                handle.write(b'comic-data')
            self.connection.execute(
                'INSERT INTO files(id, filepath, size) VALUES (1, ?, 1);',
                (filepath,),
            )
            self.connection.commit()

            recorded = file_provenance.record_download_file_provenance(
                self._download(filepath)
            )
            row = self.connection.execute(
                'SELECT source_type, source_name, release_title, '
                'web_title, web_sub_title, acquired_at '
                'FROM file_provenance WHERE file_id = 1;'
            ).fetchone()

        self.assertEqual(recorded, 1)
        self.assertEqual(row[0], 'Usenet indexer')
        self.assertEqual(row[1], 'NZBgeek')
        self.assertEqual(row[2], 'Batman 001 (2025)')
        self.assertEqual(row[3], 'Batman #1')
        self.assertEqual(row[4], 'Digital')
        self.assertGreater(row[5], 0)
        self.assertNotIn('download_link', file_provenance.PROVENANCE_SCHEMA)
        self.assertNotIn('pure_link', file_provenance.PROVENANCE_SCHEMA)

    def test_replacement_updates_provenance_and_stored_file_size(self):
        with tempfile.TemporaryDirectory() as root:
            filepath = os.path.join(root, 'Batman 001.cbz')
            with open(filepath, 'wb') as handle:
                handle.write(b'old')
            self.connection.execute(
                'INSERT INTO files(id, filepath, size) VALUES (1, ?, 3);',
                (filepath,),
            )
            self.connection.commit()
            file_provenance.record_download_file_provenance(
                self._download(filepath, source_name='Indexer A')
            )

            with open(filepath, 'wb') as handle:
                handle.write(b'new-and-larger')
            file_provenance.record_download_file_provenance(
                self._download(filepath, source_name='Indexer B')
            )

            source_name = self.connection.execute(
                'SELECT source_name FROM file_provenance WHERE file_id = 1;'
            ).fetchone()[0]
            stored_size = self.connection.execute(
                'SELECT size FROM files WHERE id = 1;'
            ).fetchone()[0]
            provenance_count = self.connection.execute(
                'SELECT COUNT(*) FROM file_provenance WHERE file_id = 1;'
            ).fetchone()[0]

        self.assertEqual(source_name, 'Indexer B')
        self.assertEqual(stored_size, len(b'new-and-larger'))
        self.assertEqual(provenance_count, 1)

    def test_provenance_storage_cascades_when_file_is_deleted(self):
        with tempfile.TemporaryDirectory() as root:
            filepath = os.path.join(root, 'Batman 001.cbz')
            with open(filepath, 'wb') as handle:
                handle.write(b'comic-data')
            self.connection.execute(
                'INSERT INTO files(id, filepath, size) VALUES (1, ?, 10);',
                (filepath,),
            )
            self.connection.commit()
            file_provenance.record_download_file_provenance(
                self._download(filepath)
            )

            self.connection.execute('DELETE FROM files WHERE id = 1;')
            self.connection.commit()
            count = self.connection.execute(
                'SELECT COUNT(*) FROM file_provenance;'
            ).fetchone()[0]

        self.assertEqual(count, 0)

    def test_success_paths_record_after_conversion_and_before_seed_reset(self):
        for actions in (
            PostProcessor.actions_success,
            PostProcessorTorrentsComplete.actions_success,
            PostProcessorTorrentsCopy.actions_seeding,
        ):
            with self.subTest(actions=actions):
                self.assertIn(record_download_file_provenance, actions)
                self.assertLess(
                    actions.index(convert_file),
                    actions.index(record_download_file_provenance),
                )

        copy_actions = PostProcessorTorrentsCopy.actions_seeding
        self.assertLess(
            copy_actions.index(record_download_file_provenance),
            copy_actions.index(reset_file_link),
        )


if __name__ == '__main__':
    unittest.main()
