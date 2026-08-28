# -*- coding: utf-8 -*-

"""A folder the user has decided about stops being asked about.

`/content/MAD Magazine (2018)` holds every issue from 1955 on, on purpose:
one folder is how a reader wants a magazine's whole run. The matcher is
right to refuse the 1955 files -- they are not issues of the 2018 volume --
but a refused file never reaches `files`, and `_collect_unimported_files`
decides a folder is untracked by asking exactly that table. So 540 files
came back on every Rescan Untracked Library, forever, and the only way to
stop it was to move them out of the folder the user wants them in.

Adopting them records the decision instead of moving the files. Each one
stays where it is, gains a `files` row so the importer stops offering it,
and a forced `volume_files` binding so every later `scan_files` skips it --
while claiming no issue, so the issues the volume really is missing stay
wanted and stay searchable.
"""

import unittest
from unittest.mock import patch

from backend.base.definitions import GeneralFileType
from backend.implementations import file_matching as FM


def _match(filepath, issue_ids=[], general_file=False, forced_match=False):
    return {
        'filepath': filepath,
        'issue_ids': list(issue_ids),
        'general_file': general_file,
        'forced_match': forced_match
    }


class what_an_adopted_file_is_recorded_as(unittest.TestCase):
    """Every non-metadata file used to be filed as cover art."""

    def test_a_comic_kept_on_purpose_is_adopted_not_a_cover(self):
        self.assertEqual(
            FM._general_file_type_for(
                '/content/MAD Magazine (2018)/MAD Magazine 024 (1955).cbr'
            ),
            GeneralFileType.ADOPTED.value
        )

    def test_an_actual_image_is_still_a_cover(self):
        self.assertEqual(
            FM._general_file_type_for('/content/MAD Magazine (2018)/cover.jpg'),
            GeneralFileType.COVER.value
        )

    def test_metadata_is_still_metadata(self):
        self.assertEqual(
            FM._general_file_type_for(
                '/content/MAD Magazine (2018)/ComicInfo.xml'
            ),
            GeneralFileType.METADATA.value
        )


class adopting_what_nothing_claims(unittest.TestCase):
    FOLDER = '/content/MAD Magazine (2018)'

    def _adopt(self, matches):
        applied = []
        with patch.object(FM, 'get_file_matching', return_value=matches), \
                patch.object(
                    FM, 'set_file_matching',
                    side_effect=lambda vid, m: applied.extend(m)):
            adopted = FM.adopt_unmatched_files(1)
        return adopted, applied

    def test_a_refused_file_is_kept_where_it_is(self):
        refused = self.FOLDER + '/MAD Magazine 024 (1955).cbr'

        adopted, applied = self._adopt([_match(refused)])

        self.assertEqual(adopted, [refused])
        self.assertEqual(applied, [{
            'filepath': refused,
            'issue_ids': [],
            'general_file': True,
            'forced_match': True
        }])

    def test_a_matched_file_is_never_unbound(self):
        # The footgun this action exists to avoid: adopting a file that is
        # already matched would drop its issue binding and un-download the
        # issue.
        matched = self.FOLDER + '/MAD Magazine 601 (2018).cbr'

        adopted, applied = self._adopt([_match(matched, issue_ids=[7])])

        self.assertEqual(adopted, [])
        self.assertEqual(applied, [])

    def test_a_file_already_adopted_is_left_alone(self):
        adopted, applied = self._adopt([
            _match(self.FOLDER + '/cover.jpg', general_file=True)
        ])

        self.assertEqual(adopted, [])
        self.assertEqual(applied, [])

    def test_another_readers_cache_is_not_adopted(self):
        # Kapowarr already refuses to treat YACReader Server's thumbnails as
        # content. Taking ownership of them here would undo that.
        adopted, applied = self._adopt([
            _match(self.FOLDER + '/MAD Magazine 024 (1955)_thumb.jpg')
        ])

        self.assertEqual(adopted, [])
        self.assertEqual(applied, [])

    def test_a_settled_folder_asks_for_no_further_work(self):
        # Nothing unclaimed means no write and no rescan -- running the
        # action twice is free.
        adopted, applied = self._adopt([
            _match(self.FOLDER + '/MAD Magazine 601 (2018).cbr', issue_ids=[7]),
            _match(self.FOLDER + '/MAD Magazine 024 (1955).cbr',
                   general_file=True, forced_match=True)
        ])

        self.assertEqual(adopted, [])
        self.assertEqual(applied, [])

    def test_the_whole_refused_era_goes_in_one_pass(self):
        era = [
            '%s/MAD Magazine %03d (19%02d).cbr' % (self.FOLDER, n, 55 + n)
            for n in range(1, 25)
        ]
        matches = [_match(f) for f in era]
        matches.append(
            _match(self.FOLDER + '/MAD Magazine 601 (2018).cbr', issue_ids=[7])
        )

        adopted, applied = self._adopt(matches)

        self.assertEqual(adopted, era)
        self.assertTrue(all(
            entry['general_file'] and entry['forced_match']
            and entry['issue_ids'] == []
            for entry in applied
        ))


if __name__ == '__main__':
    unittest.main()
