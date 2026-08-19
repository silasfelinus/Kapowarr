# -*- coding: utf-8 -*-

"""t-056: Library.add must support adding a volume natively from a
non-ComicVine provider (e.g. Metron) by (provider_id, external_id), without
requiring a comicvine_id, while continuing to dedupe correctly and never
silently double-adding a volume.
"""

import unittest
from unittest.mock import patch

from backend.base.custom_exceptions import InvalidKeyValue, VolumeAlreadyAdded
from backend.implementations.volumes import Library


class library_add_dedup_short_circuits(unittest.TestCase):
    """These dedup checks are the very first thing `Library.add` does, so
    they can be exercised without standing up root folders, a metadata
    fetch, or any of the rest of the add pipeline: if the dedupe check
    doesn't raise before those, the test's patched dependencies (left
    unset/None) would themselves blow up.
    """

    def test_comicvine_add_requires_a_comicvine_id(self):
        with self.assertRaises(InvalidKeyValue):
            Library.add(None, root_folder_id=1, monitored=True)

    def test_metron_add_requires_an_external_id(self):
        with self.assertRaises(InvalidKeyValue):
            Library.add(
                None, root_folder_id=1, monitored=True,
                provider_id='metron', external_id=None
            )

    def test_comicvine_add_rejects_existing_cv_volume(self):
        with patch.object(Library, '_cv_to_id', return_value=42):
            with self.assertRaises(VolumeAlreadyAdded) as ctx:
                Library.add(4050, root_folder_id=1, monitored=True)

        self.assertEqual(ctx.exception.comicvine_id, 4050)
        self.assertEqual(ctx.exception.volume_id, 42)
        self.assertEqual(ctx.exception.provider_id, 'comicvine')

    def test_metron_add_rejects_volume_already_added_by_metron_identity(self):
        with patch(
            'backend.implementations.volumes.MetadataIdentityStore'
        ) as identity_store:
            identity_store.resolve.return_value = 99

            with self.assertRaises(VolumeAlreadyAdded) as ctx:
                Library.add(
                    None, root_folder_id=1, monitored=True,
                    provider_id='metron', external_id='abc-12'
                )

        identity_store.resolve.assert_called_once_with(
            'volume', 'metron', 'abc-12'
        )
        self.assertEqual(ctx.exception.volume_id, 99)
        self.assertEqual(ctx.exception.provider_id, 'metron')
        self.assertEqual(ctx.exception.external_id, 'abc-12')
        # No ComicVine cross-link is known yet at this point.
        self.assertIsNone(ctx.exception.comicvine_id)

    def test_metron_add_does_not_check_metron_identity_for_comicvine_provider(self):
        # A plain ComicVine add must not consult the Metron/portable
        # identity store at all -- it should behave exactly as before.
        with patch.object(
            Library, '_cv_to_id', return_value=None
        ), patch(
            'backend.implementations.volumes.MetadataIdentityStore'
        ) as identity_store, patch(
            'backend.implementations.volumes.RootFolders'
        ) as root_folders:
            # RootFolders().get_one(...) is the next thing `add` does once
            # dedupe passes; making it raise proves dedupe passed without
            # needing to stub out the rest of the (much larger) add pipeline.
            root_folders.return_value.get_one.side_effect = RuntimeError(
                'stop here, dedupe passed as expected'
            )
            with self.assertRaises(RuntimeError):
                Library.add(4050, root_folder_id=1, monitored=True)

        identity_store.resolve.assert_not_called()


class volume_already_added_exception(unittest.TestCase):
    """The exception itself: extended (in t-056) to optionally carry a
    provider identity, while staying backward compatible with existing
    ComicVine-only callers/consumers of `.api_response`.
    """

    def test_defaults_to_comicvine_identity(self):
        exc = VolumeAlreadyAdded(4050, 42)

        self.assertEqual(exc.provider_id, 'comicvine')
        self.assertEqual(exc.external_id, 4050)
        self.assertEqual(exc.api_response['result'], {
            'comicvine_id': 4050,
            'volume_id': 42,
            'provider_id': 'comicvine',
            'external_id': 4050
        })

    def test_carries_a_non_comicvine_provider_identity(self):
        exc = VolumeAlreadyAdded(None, 99, 'metron', 'abc-12')

        self.assertIsNone(exc.comicvine_id)
        self.assertEqual(exc.provider_id, 'metron')
        self.assertEqual(exc.external_id, 'abc-12')
        self.assertEqual(exc.api_response['result']['external_id'], 'abc-12')


if __name__ == '__main__':
    unittest.main()
