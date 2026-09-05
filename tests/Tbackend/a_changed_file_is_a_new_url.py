# -*- coding: utf-8 -*-

"""A stale cache is indistinguishable from a broken fix.

The templates asked for `/static/js/library_import_review_ui.js` and
nothing else, so a browser holding the old copy kept it across any number
of container restarts. On 2026-09-05 that made four separate frontend
fixes look like they had done nothing -- the image was right, the page was
not -- and the only way to tell the difference was to go and read the file
inside the container.

Every `url_for('static', ...)` now carries the file's modification time, so
a changed file is a new URL and the browser fetches it because it has never
seen that URL before. 102 references across 27 templates, none of them
touched.
"""

import os
import tempfile
import unittest
from os.path import getmtime, join
from pathlib import Path
from typing import Any, Dict

from flask import Flask, url_for

SOURCE = (
    Path(__file__).resolve().parents[2]
    / 'backend' / 'internals' / 'server.py'
).read_text()


def install_hook(app: Flask) -> None:
    """Run the versioning hook as shipped, lifted out of `server.py`.

    Copying it into the test would let the two drift, and the whole point
    of this file is that the shipped one behaves.
    """
    start = SOURCE.index('        static_versions: Dict[str, str] = {}')
    end = SOURCE.index(
        '            return\n',
        SOURCE.index("values['v'] = static_versions")
    ) + len('            return\n')
    body = '\n'.join(line[8:] for line in SOURCE[start:end].split('\n'))
    exec(
        compile(body, 'server.py:url_defaults', 'exec'),
        {'app': app, 'getmtime': getmtime, 'join': join,
         'Dict': Dict, 'Any': Any}
    )


class a_static_url_carries_the_files_version(unittest.TestCase):
    def setUp(self):
        self.static = tempfile.mkdtemp()
        os.makedirs(join(self.static, 'js'), exist_ok=True)
        self.asset = join(self.static, 'js', 'app.js')
        with open(self.asset, 'w') as f:
            f.write('//')

        self.app = Flask(
            __name__, static_folder=self.static, static_url_path='/static'
        )
        install_hook(self.app)

    def test_the_url_is_versioned(self):
        with self.app.test_request_context():
            url = url_for('static', filename='js/app.js')

        self.assertIn('?v=', url)
        self.assertEqual(
            url.split('?v=')[1], str(int(getmtime(self.asset)))
        )

    def test_the_path_itself_is_unchanged(self):
        """Anything reading these URLs still sees the same file."""
        with self.app.test_request_context():
            url = url_for('static', filename='js/app.js')

        self.assertTrue(url.startswith('/static/js/app.js?'))

    def test_a_file_that_is_not_there_is_not_versioned(self):
        """A URL for a missing file is a broken link either way, and
        versioning is not the place to raise it."""
        with self.app.test_request_context():
            url = url_for('static', filename='js/gone.js')

        self.assertEqual(url, '/static/js/gone.js')

    def test_the_same_file_gives_the_same_url(self):
        with self.app.test_request_context():
            first = url_for('static', filename='js/app.js')
            second = url_for('static', filename='js/app.js')

        self.assertEqual(first, second)

    def test_a_different_file_gives_a_different_version(self):
        other = join(self.static, 'js', 'other.js')
        with open(other, 'w') as f:
            f.write('//')
        os.utime(other, (1_600_000_000, 1_600_000_000))

        with self.app.test_request_context():
            app_url = url_for('static', filename='js/app.js')
            other_url = url_for('static', filename='js/other.js')

        self.assertNotEqual(
            app_url.split('?v=')[1], other_url.split('?v=')[1]
        )
        self.assertTrue(other_url.endswith('?v=1600000000'))

    def test_endpoints_that_are_not_static_are_left_alone(self):
        @self.app.route('/thing/<name>')
        def thing(name):
            return name

        with self.app.test_request_context():
            self.assertEqual(url_for('thing', name='x'), '/thing/x')


class the_hook_is_wired_into_the_real_app(unittest.TestCase):
    def test_it_is_registered_as_a_url_default(self):
        self.assertIn('@app.url_defaults', SOURCE)
        self.assertIn('def version_static_files', SOURCE)

    def test_it_is_read_once_per_file(self):
        """A page asks for a hundred of these. A stat per URL per load is
        a hundred syscalls to catch something that cannot happen inside a
        container, where a new build is a new container."""
        self.assertIn('if filename not in static_versions:', SOURCE)


if __name__ == '__main__':
    unittest.main()
