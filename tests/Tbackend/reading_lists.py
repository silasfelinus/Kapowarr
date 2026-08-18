import unittest
from unittest.mock import MagicMock, patch
from xml.etree import ElementTree as ET

from backend.features import reading_lists


class CBLParsingTest(unittest.TestCase):
    def test_parses_common_comicrack_database_ids_in_order(self):
        payload = b'''<?xml version="1.0" encoding="utf-8"?>
<ReadingList>
  <Name>My Arc</Name>
  <NumIssues>2</NumIssues>
  <Books>
    <Book Series="Fantastic Four" Number="1" Volume="1961" Year="1961">
      <Database Name="cv" Series="2045" Issue="5558" />
    </Book>
    <Book Series="Amazing Fantasy" Number="15" Volume="1962" Year="1962">
      <Database Name="cv" Series="5533" Issue="105342" />
    </Book>
  </Books>
</ReadingList>'''

        title, entries = reading_lists.parse_cbl(payload)

        self.assertEqual(title, 'My Arc')
        self.assertEqual([entry['position'] for entry in entries], [1, 2])
        self.assertEqual(entries[0]['comicvine_volume_id'], 2045)
        self.assertEqual(entries[0]['comicvine_issue_id'], 5558)
        self.assertEqual(entries[1]['issue_number'], '15')

    def test_supports_explicit_comicid_issueid_dialect(self):
        payload = b'''<ReadingList><Name>Arc</Name><Books>
<Book Series="Batman" Number="1" Volume="2016" Year="2016">
<ComicID>123</ComicID><IssueID>456</IssueID>
</Book></Books></ReadingList>'''

        _, entries = reading_lists.parse_cbl(payload)

        self.assertEqual(entries[0]['comicvine_volume_id'], 123)
        self.assertEqual(entries[0]['comicvine_issue_id'], 456)

    def test_rejects_entity_declarations(self):
        payload = b'''<!DOCTYPE x [<!ENTITY nope "boom">]>
<ReadingList><Name>&nope;</Name><Books /></ReadingList>'''
        with self.assertRaises(ValueError):
            reading_lists.parse_cbl(payload)


class CBLMatchingTest(unittest.TestCase):
    @patch.object(reading_lists, 'get_db')
    def test_exact_comicvine_issue_id_wins(self, get_db):
        cursor = MagicMock()
        get_db.return_value = cursor
        cursor.execute.return_value.fetchone.return_value = {
            'volume_id': 9,
            'id': 27
        }

        result = reading_lists._resolve_entry({
            'series': 'Anything',
            'issue_number': '1',
            'comicvine_issue_id': 5558,
            'comicvine_volume_id': None,
            'volume_year': None,
            'issue_year': None
        })

        self.assertEqual(result, (9, 27))

    @patch.object(reading_lists, 'match_title', return_value=True)
    @patch.object(reading_lists, 'get_db')
    def test_ambiguous_fallback_stays_unresolved(self, get_db, _match_title):
        cursor = MagicMock()
        get_db.return_value = cursor
        cursor.execute.return_value.fetchalldict.return_value = [
            {'issue_id': 1, 'volume_id': 10, 'date': '2016-01-01',
             'volume_title': 'Batman', 'volume_year': 2016},
            {'issue_id': 2, 'volume_id': 11, 'date': '2016-02-01',
             'volume_title': 'Batman', 'volume_year': 2016}
        ]

        result = reading_lists._resolve_entry({
            'series': 'Batman',
            'issue_number': '1',
            'comicvine_issue_id': None,
            'comicvine_volume_id': None,
            'volume_year': 2016,
            'issue_year': 2016
        })

        self.assertEqual(result, (None, None))


class CBLExportTest(unittest.TestCase):
    @patch.object(reading_lists, 'get_reading_list')
    def test_exports_order_and_comicvine_ids(self, get_reading_list):
        get_reading_list.return_value = {
            'id': 1,
            'title': 'My Arc',
            'entry_count': 2,
            'entries': [
                {
                    'series': 'Batman', 'issue_number': '2',
                    'volume_year': 2016, 'issue_year': 2016,
                    'filename': None, 'comicvine_volume_id': 100,
                    'comicvine_issue_id': 200
                },
                {
                    'series': 'Batman', 'issue_number': '1',
                    'volume_year': 2016, 'issue_year': 2016,
                    'filename': None, 'comicvine_volume_id': 100,
                    'comicvine_issue_id': 199
                }
            ]
        }

        payload = reading_lists.export_cbl(1)
        root = ET.fromstring(payload)
        books = root.find('Books').findall('Book')

        self.assertEqual([book.get('Number') for book in books], ['2', '1'])
        self.assertEqual(books[0].find('Database').get('Issue'), '200')
        self.assertEqual(root.findtext('Name'), 'My Arc')


if __name__ == '__main__':
    unittest.main()
