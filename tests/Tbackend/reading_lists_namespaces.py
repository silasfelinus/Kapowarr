import unittest

from backend.features.reading_lists import parse_cbl


class NamespacedCBLTest(unittest.TestCase):
    def test_default_namespace_keeps_books_and_comicvine_ids(self):
        payload = b'''<?xml version="1.0" encoding="utf-8"?>
<ReadingList xmlns="urn:comicrack:reading-list">
  <Name>Namespace Arc</Name>
  <Books>
    <Book Series="Batman" Number="1" Volume="2016" Year="2016">
      <Database Name="cv" Series="40000" Issue="50000" />
    </Book>
  </Books>
</ReadingList>'''

        title, entries = parse_cbl(payload)

        self.assertEqual(title, 'Namespace Arc')
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]['series'], 'Batman')
        self.assertEqual(entries[0]['comicvine_volume_id'], 40000)
        self.assertEqual(entries[0]['comicvine_issue_id'], 50000)


if __name__ == '__main__':
    unittest.main()
