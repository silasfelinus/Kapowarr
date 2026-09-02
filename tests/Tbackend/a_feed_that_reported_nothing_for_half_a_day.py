# -*- coding: utf-8 -*-

"""Two things the first day of feed sync showed.

    08:40:59  Indexer NZB Geek returned a non-JSON response
    08:40:59  Indexer NZB Planet returned a non-JSON response
    08:41:00  Indexer NZB.SU returned a non-JSON response
    08:41:19  Finished task Feed Sync

Every quarter of an hour, for four hours, on all three of Silas's Usenet
indexers. The task reported that it had run; it had reached nobody. Newznab's
native format is XML and `o=json` is an extension -- an indexer that honours
it for a search need not honour it for a query-less feed request, and these
did not.

    09:02  Orphaned downloads: importing 21 file(s) into volume 20
           linking X-Statix 13 .cbr ... (0 hardlink(s), 1 copied file(s))
           Converting file from cbr to cbz
    10:02  ... the same
    11:02  ... the same
    12:02  ... the same

And recovery had no way to stop. Linking a file in leaves the original where
it was -- that is the point, so a torrent can go on seeding -- and the
library then renames and converts its copy, so the two no longer share a
name. Every hour it found the same file again and made another full
cross-device copy of it.
"""

import os
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

from backend.features import orphaned_downloads as OD
from backend.implementations import indexers_core as IC

FEED = '''<?xml version="1.0"?>
<rss version="2.0"
     xmlns:newznab="http://www.newznab.com/DTD/2010/feeds/attributes/">
  <channel>
    <item>
      <title>Save Now 004 (2026) (digital) (Empire)</title>
      <link>https://indexer.example.com/getnzb/abc.nzb</link>
      <enclosure url="https://indexer.example.com/getnzb/abc.nzb"
                 type="application/x-nzb"/>
    </item>
  </channel>
</rss>'''


class an_indexer_that_answers_in_xml(unittest.TestCase):
    def setUp(self):
        self.indexer = MagicMock()
        self.indexer.title = 'NZB Geek'
        return

    def test_its_feed_is_read_rather_than_discarded(self):
        results = IC._results_from_xml(FEED, self.indexer)

        self.assertEqual(len(results), 1)
        self.assertEqual(
            results[0]['display_title'],
            'Save Now 004 (2026) (digital) (Empire)'
        )
        self.assertEqual(results[0]['source'], 'NZB Geek')
        return

    def test_the_release_is_parsed_the_same_way_a_search_result_is(self):
        results = IC._results_from_xml(FEED, self.indexer)

        self.assertEqual(results[0]['series'], 'Save Now')
        self.assertEqual(results[0]['issue_number'], 4.0)
        return

    def test_the_enclosure_is_preferred_for_the_link(self):
        "It is the NZB itself; `link` can be a landing page."
        results = IC._results_from_xml(FEED, self.indexer)

        self.assertEqual(
            results[0]['link'], 'https://indexer.example.com/getnzb/abc.nzb')
        return

    def test_an_xml_error_is_reported_with_what_it_said(self):
        """"Non-JSON response" hid the reason for four hours. Whatever the
        indexer is complaining about has to reach the log."""
        error = ('<?xml version="1.0"?>'
                 '<error code="200" description="Missing parameter"/>')

        with self.assertLogs(level='WARNING') as logged:
            results = IC._results_from_xml(error, self.indexer)

        self.assertEqual(results, [])
        said = '\n'.join(logged.output)
        self.assertIn('Missing parameter', said)
        self.assertIn('200', said)
        return

    def test_something_that_is_neither_says_what_it_saw(self):
        with self.assertLogs(level='WARNING') as logged:
            results = IC._results_from_xml('<html>go away', self.indexer)

        self.assertEqual(results, [])
        self.assertIn('go away', '\n'.join(logged.output))
        return


class recovery_stops_once_the_library_has_it(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp()
        self.files = []
        for number in (13, 14):
            path = os.path.join(
                self.folder, f'X-Statix {number} (2003) (digital).cbr')
            with open(path, 'wb') as f:
                f.write(b'x')
            settled = time.time() - 3600
            os.utime(path, (settled, settled))
            self.files.append(path)
        return

    def _still_missing(self, open_issues):
        with patch('backend.features.watched_folder_import.LibraryIndex',
                   MagicMock()), \
                patch('backend.features.watched_folder_import.'
                      'match_parsed_to_library_volume', return_value=20), \
                patch('backend.features.release_feed.wanted_issues_of',
                      return_value=open_issues):
            return [os.path.basename(f) for f in OD.still_missing(self.files)]

    def test_a_file_whose_issue_is_missing_is_recovered(self):
        kept = self._still_missing([(1, 13.0), (2, 14.0)])

        self.assertEqual(len(kept), 2)
        return

    def test_a_file_whose_issue_arrived_is_left_alone(self):
        kept = self._still_missing([(2, 14.0)])

        self.assertEqual(kept, ['X-Statix 14 (2003) (digital).cbr'])
        return

    def test_once_everything_landed_the_pass_does_nothing(self):
        "The loop's terminating condition, which it did not have."
        self.assertEqual(self._still_missing([]), [])
        return

    def test_a_file_belonging_to_no_volume_is_left_for_the_import(self):
        """Deciding here as well would be a second, quieter place to get it
        wrong; `import_loose_files` already leaves an unmatched file alone."""
        with patch('backend.features.watched_folder_import.LibraryIndex',
                   MagicMock()), \
                patch('backend.features.watched_folder_import.'
                      'match_parsed_to_library_volume', return_value=None):
            kept = OD.still_missing(self.files)

        self.assertEqual(len(kept), 2)
        return

    def test_the_narrowing_reaches_the_import(self):
        with patch.object(OD, 'files_in_use', return_value=set()), \
                patch.object(OD, 'Settings') as settings, \
                patch.object(OD, 'import_loose_files') as imported:
            settings.return_value.sv.download_folder = self.folder
            OD.recover_orphaned_downloads()

        self.assertIs(imported.call_args.kwargs['narrow'], OD.still_missing)
        self.assertTrue(imported.call_args.kwargs['leave_original'])
        return
