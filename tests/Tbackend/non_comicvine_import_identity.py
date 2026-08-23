# -*- coding: utf-8 -*-

"""A match with no ComicVine ID must still be importable."""

import unittest
from unittest.mock import MagicMock, patch

from backend.features import library_import as LI


class a_gcd_match_survives_the_trip_to_library_add(unittest.TestCase):
    """The provider fan-out found the series and then threw it away.

    GCD never carries a ComicVine ID -- `comicvine_id` is `None` by
    design, since GCD has no cross-link -- and Metron only sometimes
    does. The match dict carried `id` alone, so a GCD rescue arrived at
    `Library.add(comicvine_id=None)` and the folder was held for review
    anyway. Asking the other providers was pure cost with no effect.
    """

    def _import(self, matches):
        added = []

        def _add(**kwargs):
            added.append(kwargs)
            return len(added)

        root = MagicMock()
        root.folder = '/content'
        root.id = 1

        with patch.object(LI, 'RootFolders') as roots, \
                patch.object(LI, 'exists', return_value=True), \
                patch.object(LI, 'commit'), \
                patch.object(LI, 'folder_is_inside_folder', return_value=True), \
                patch.object(LI, 'common_folder', return_value='/content/X'), \
                patch.object(LI, 'Library') as library:
            library.add = _add
            roots.return_value.get_all.return_value = [root]
            try:
                LI.import_library(matches, continue_on_error=True)
            except Exception:
                pass
        return added

    def test_the_provider_and_its_own_id_reach_library_add(self):
        added = self._import([{
            'filepath': '/content/Danger Jane/Danger Jane 001.cbz',
            'id': None, 'provider_id': 'gcd', 'external_id': '55123'
        }])

        self.assertEqual(len(added), 1)
        self.assertEqual(added[0]['metadata_provider_id'], 'gcd')
        self.assertEqual(added[0]['metadata_external_id'], '55123')

    def test_two_gcd_volumes_are_not_collapsed_into_one(self):
        """Keyed on `id`, every GCD match shared the same `None` bucket."""
        added = self._import([
            {'filepath': '/content/A/A 001.cbz', 'id': None,
             'provider_id': 'gcd', 'external_id': '1'},
            {'filepath': '/content/B/B 001.cbz', 'id': None,
             'provider_id': 'gcd', 'external_id': '2'},
        ])

        self.assertEqual(len(added), 2, 'two series, two volumes')
        self.assertEqual(
            sorted(a['metadata_external_id'] for a in added), ['1', '2']
        )

    def test_a_comicvine_match_is_unchanged(self):
        added = self._import([{
            'filepath': '/content/Batman/Batman 001.cbz', 'id': 796
        }])

        self.assertEqual(len(added), 1)
        self.assertEqual(added[0]['comicvine_id'], 796)
        self.assertEqual(added[0]['metadata_provider_id'], 'comicvine')
        self.assertEqual(
            added[0]['metadata_external_id'], 796,
            'an absent external id falls back to the ComicVine one'
        )


class the_match_carries_it_from_the_search(unittest.TestCase):
    def test_a_result_keeps_its_provider(self):
        """`_match_file_groups` builds the dict that starts the journey."""
        import inspect

        source = inspect.getsource(LI)
        self.assertIn("'provider_id': result.get('provider_id'", source)
        self.assertIn("'external_id': result.get(", source)


if __name__ == '__main__':
    unittest.main()
