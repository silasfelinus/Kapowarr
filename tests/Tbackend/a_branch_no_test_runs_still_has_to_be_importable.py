# -*- coding: utf-8 -*-

"""A name that only resolves on a rare branch does not resolve at all.

`_review_content_less_folder` calls `folder_is_inside_folder`, and
`library_import_persistent` never imported it. Nothing noticed, because
the only way to reach that line is to find a folder that names a series,
holds no comics, and is already owned by a volume -- and no test built
one. It sat latent from d760142 until #162 stopped offering folders whose
only content was a stray image, which made content-less folders common,
and then it took the whole Continuous Library Import down with a
NameError on the first one (Silas's log, 2026-08-30 09:23).

Two guards. The first is the branch itself. The second is the class:
every function in the backend is checked for a global it uses and nobody
defines, so the next one is a failing test rather than a dead import.
"""

import builtins
import importlib
import pkgutil
import symtable
import unittest
from os.path import dirname, join
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import backend
from backend.features import library_import_persistent as P


class the_branch_that_took_the_import_down(unittest.TestCase):
    """A content-less folder the library already owns."""

    def _review(self, owned_of):
        cls = P.PersistentContinuousLibraryImport
        job = cls.__new__(cls)
        job.job_id = 1
        job.search_cache = {}

        with TemporaryDirectory() as root:
            folder = join(root, 'Death of Power')
            Path(folder).mkdir()

            with patch.object(
                P, 'is_content_less_series_folder', return_value=True
            ), patch.object(
                P, '_volume_owned_folders', return_value=owned_of(root, folder)
            ), patch.object(
                P, 'folder_search_query', return_value='death of power'
            ), patch.object(
                cls, '_wait_for_resource_slot', lambda self, key: True
            ), patch.object(
                cls, '_emit_persistent_status', lambda self, message: None
            ), patch.object(
                P, 'search_volumes_everywhere', side_effect=AssertionError(
                    'an owned folder must be settled before any search'
                )
            ):
                return cls._review_content_less_folder(job, folder, 0)

    def test_the_folder_itself_being_owned_is_not_a_question(self):
        # The line that raised. It must run, and it must return nothing --
        # an empty folder the library already owns is that series waiting
        # for its issues, not a question about which series it is.
        self.assertEqual(self._review(lambda root, folder: [folder]), [])

    def test_a_parent_folder_owning_it_counts_too(self):
        self.assertEqual(self._review(lambda root, folder: [root]), [])

    def test_an_unrelated_owned_folder_does_not_settle_it(self):
        # The other side of the same call: it has to actually compare, not
        # just avoid raising.
        with self.assertRaises(AssertionError):
            self._review(lambda root, folder: [join(root, 'Something Else')])


class every_name_a_function_uses_exists(unittest.TestCase):
    """The class of bug, not the instance.

    Checked with `symtable` rather than by importing and calling: a name
    used in a function but assigned nowhere in the module is a global,
    and a global the module does not have is a NameError waiting for the
    branch that reaches it. Candidates are then resolved against the
    imported module, so `from x import *` and conditional imports are
    not reported.
    """

    @staticmethod
    def _suspect_globals(source, filename):
        top = symtable.symtable(source, filename, 'exec')
        module_level = set(top.get_identifiers())
        found = set()

        def walk(table):
            for symbol in table.get_symbols():
                name = symbol.get_name()
                if (
                    symbol.is_global()
                    and not symbol.is_assigned()
                    and name not in module_level
                    and not hasattr(builtins, name)
                    # Module dunders are supplied by the loader.
                    and not (name.startswith('__') and name.endswith('__'))
                ):
                    found.add(name)
            for child in table.get_children():
                walk(child)

        walk(top)
        return found

    def test_no_module_reaches_for_a_name_it_does_not_have(self):
        # `backend` is a namespace package, so it has no `__file__`.
        root = dirname(list(backend.__path__)[0])
        missing = []

        for info in pkgutil.walk_packages(
            backend.__path__, prefix='backend.'
        ):
            if info.ispkg:
                continue

            path = join(root, info.name.replace('.', '/') + '.py')
            try:
                source = open(path, encoding='utf-8').read()
            except OSError:
                continue

            suspects = self._suspect_globals(source, path)
            if not suspects:
                continue

            try:
                module = importlib.import_module(info.name)
            except Exception:
                # A module that will not import is a different failure, and
                # the rest of the suite says so far more clearly than this.
                continue

            for name in sorted(suspects):
                # Resolved here rather than statically, so a star import
                # or a conditional one is not a false alarm.
                if not hasattr(module, name):
                    missing.append('%s uses undefined %r' % (info.name, name))

        self.assertEqual(missing, [], '\n'.join(missing))


if __name__ == '__main__':
    unittest.main()
