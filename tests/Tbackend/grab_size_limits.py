# -*- coding: utf-8 -*-

from asyncio import run
from unittest import TestCase
from unittest.mock import patch

from backend.base.custom_exceptions import InvalidKeyValue
from backend.features import grab_size_limits as limits
from backend.implementations.indexers import (_parse_newznab_json,
                                              _parse_newznab_xml)
from backend.implementations.torznab import search_torznab_indexer


MIB = 1024 * 1024
MISSING = object()


def result(size_marker=MISSING):
    entry = {
        'series': 'Batman',
        'year': 2020,
        'volume_number': 1,
        'special_version': None,
        'issue_number': 1.0,
        'annual': False,
        'link': 'https://example.invalid/release',
        'display_title': 'Batman 001 (2020)',
        'source': 'test'
    }
    if size_marker is not MISSING:
        entry['size'] = size_marker
    return entry


class grab_size_filter(TestCase):
    @patch.object(
        limits,
        'get_grab_size_limits',
        return_value={
            'minimum_grab_size_mb': 1,
            'maximum_grab_size_mb': 300
        }
    )
    def test_default_range_is_inclusive(self, _get_limits):
        entries = [
            result(512 * 1024),
            result(1 * MIB),
            result(300 * MIB),
            result(301 * MIB)
        ]
        self.assertEqual(
            limits.filter_search_results(entries),
            entries[1:3]
        )

    @patch.object(
        limits,
        'get_grab_size_limits',
        return_value={
            'minimum_grab_size_mb': 1,
            'maximum_grab_size_mb': 300
        }
    )
    def test_unknown_size_stays_eligible(self, _get_limits):
        unknown = result()
        explicit_none = result(None)
        malformed = result('25 MB')
        self.assertEqual(
            limits.filter_search_results([unknown, explicit_none, malformed]),
            [unknown, explicit_none, malformed]
        )

    @patch.object(
        limits,
        'get_grab_size_limits',
        return_value={
            'minimum_grab_size_mb': 0,
            'maximum_grab_size_mb': 0
        }
    )
    def test_zero_disables_both_limits(self, _get_limits):
        entries = [result(1), result(2 * 1024 * MIB)]
        self.assertEqual(limits.filter_search_results(entries), entries)

    def test_limits_are_non_negative_integers(self):
        for value in (-1, True, 1.5, '10'):
            with self.subTest(value=value):
                with self.assertRaises(InvalidKeyValue):
                    limits._validated_limit('minimum_grab_size_mb', value)

    @patch.object(
        limits,
        'get_grab_size_limits',
        return_value={
            'minimum_grab_size_mb': 1,
            'maximum_grab_size_mb': 300
        }
    )
    def test_enabled_minimum_cannot_exceed_enabled_maximum(self, _get_limits):
        with self.assertRaises(InvalidKeyValue):
            limits.update_grab_size_limits({
                'minimum_grab_size_mb': 301
            })


class newznab_size_metadata(TestCase):
    class Indexer:
        title = 'Example'

    def test_xml_reads_newznab_size_attribute(self):
        parsed = _parse_newznab_xml(
            '''<?xml version="1.0"?>
            <rss xmlns:newznab="http://www.newznab.com/DTD/2010/feeds/attributes/">
              <channel><item>
                <title>Batman 001 (2020)</title>
                <link>https://example.invalid/1</link>
                <newznab:attr name="size" value="10485760" />
              </item></channel>
            </rss>''',
            self.Indexer()
        )
        self.assertEqual(parsed[0]['size'], 10 * MIB)

    def test_xml_falls_back_to_enclosure_length(self):
        parsed = _parse_newznab_xml(
            '''<?xml version="1.0"?>
            <rss><channel><item>
                <title>Batman 001 (2020)</title>
                <enclosure url="https://example.invalid/1" length="20971520" />
            </item></channel></rss>''',
            self.Indexer()
        )
        self.assertEqual(parsed[0]['size'], 20 * MIB)

    def test_json_reads_extended_newznab_size_attribute(self):
        parsed = _parse_newznab_json({
            'channel': {
                'item': {
                    'title': 'Batman 001 (2020)',
                    'link': 'https://example.invalid/1',
                    'newznab:attr': [{
                        '@attributes': {
                            'name': 'size',
                            'value': str(30 * MIB)
                        }
                    }]
                }
            }
        }, self.Indexer())
        self.assertEqual(parsed[0]['size'], 30 * MIB)


class torznab_size_metadata(TestCase):
    class Indexer:
        id = 7
        title = 'Example Torrents'
        base_url = 'https://example.invalid/api'
        api_key = 'secret'
        category_filter_enabled = False
        categories = ''

    class Session:
        async def get_text(self, *_args, **_kwargs):
            return '''<?xml version="1.0"?>
            <rss xmlns:torznab="http://torznab.com/schemas/2015/feed">
              <channel><item>
                <title>Batman 001 (2020)</title>
                <enclosure url="https://example.invalid/1" length="41943040" />
                <torznab:attr name="seeders" value="12" />
              </item></channel>
            </rss>'''

    @patch(
        'backend.implementations.torznab.filter_search_results',
        side_effect=lambda entries: entries
    )
    def test_enclosure_length_is_exposed_as_size(self, _filter):
        parsed = run(search_torznab_indexer(
            self.Session(), self.Indexer(), 'Batman'
        ))
        self.assertEqual(parsed[0]['size'], 40 * MIB)
