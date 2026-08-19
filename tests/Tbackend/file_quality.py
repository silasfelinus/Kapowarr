import unittest

from backend.features.file_quality import (
    explain_file_quality,
    source_protocol,
)


class explainable_file_quality(unittest.TestCase):
    @staticmethod
    def _file(**overrides):
        result = {
            'file_id': 1,
            'filepath': '/library/Batman/Batman 001.cbz',
            'size': 123456,
            'source_type': 'GetComics',
            'source_name': 'GetComics',
            'release_title': 'Batman 001',
            'web_title': 'Batman Vol. 1',
            'web_sub_title': 'Main Server HD',
            'acquired_at': 12345,
        }
        result.update(overrides)
        return result

    def test_known_traits_follow_explicit_user_preferences(self):
        result = explain_file_quality(
            self._file(),
            format_preference=['cbr', 'cbz', 'pdf'],
            source_preference=['torrent', 'direct', 'usenet'],
        )

        self.assertEqual(result['format'], 'cbz')
        self.assertEqual(result['format_preference_rank'], 2)
        self.assertEqual(result['source_protocol'], 'direct')
        self.assertEqual(result['source_preference_rank'], 2)
        self.assertEqual(result['explicit_quality'], 'hd')
        self.assertTrue(result['comparison_ready'])
        self.assertIn('CBZ is format preference #2', result['traits'])
        self.assertIn('Direct is source preference #2', result['traits'])
        self.assertIn('GetComics HD label', result['traits'])
        self.assertNotIn('score', result)

    def test_unknown_provenance_stays_unknown_instead_of_being_penalized(self):
        result = explain_file_quality(
            self._file(
                filepath='/library/Batman/Batman 001.pdf',
                source_type=None,
                source_name=None,
                web_sub_title=None,
            ),
            format_preference=[],
            source_preference=['direct', 'torrent', 'usenet'],
        )

        self.assertEqual(result['format'], 'pdf')
        self.assertIsNone(result['format_preference_rank'])
        self.assertIsNone(result['source_protocol'])
        self.assertIsNone(result['source_preference_rank'])
        self.assertIsNone(result['explicit_quality'])
        self.assertFalse(result['comparison_ready'])
        self.assertEqual(result['traits'], ['PDF format'])

    def test_hd_sd_label_only_counts_for_explicit_getcomics_sources(self):
        result = explain_file_quality(
            self._file(
                source_type='Usenet indexer',
                source_name='NZBgeek',
                web_sub_title='HD',
            ),
            format_preference=['cbz'],
            source_preference=['usenet', 'direct', 'torrent'],
        )
        self.assertIsNone(result['explicit_quality'])
        self.assertEqual(result['source_protocol'], 'usenet')

    def test_source_protocol_mapping_is_conservative(self):
        self.assertEqual(source_protocol('GetComics'), 'direct')
        self.assertEqual(source_protocol('GetComics (torrent)'), 'torrent')
        self.assertEqual(source_protocol('Torrent indexer'), 'torrent')
        self.assertEqual(source_protocol('Usenet indexer'), 'usenet')
        self.assertIsNone(source_protocol('Some future source'))
        self.assertIsNone(source_protocol(None))


if __name__ == '__main__':
    unittest.main()
