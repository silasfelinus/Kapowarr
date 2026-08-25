# -*- coding: utf-8 -*-

"""The review backlog did not go down while the importer worked through it.

Review holds outlive the pass that produced them, on purpose: nothing
imported the files, so the next pass finds them again. But an old hold row
kept `state = review` even after a later pass imported that folder, because
the queue only retires rows when it is *read*, and the number on the
progress panel is a SQL `COUNT(DISTINCT folder)` over every job -- counted
that way precisely so polling it does not decode every held row.

So "Held for review" and the "Review Holds (N)" button sat at the stale
number for the length of a pass and only dropped when the user opened the
list. The better the importer gets at resolving old holds unattended, the
more wrong that number is for the entire time it is being right.
"""

import unittest
from json import loads
from unittest.mock import MagicMock, patch

from backend.features import library_import_state as state


class recording_a_folder_supersedes_older_verdicts(unittest.TestCase):
    def _writes(self, review_items):
        cursor = MagicMock()
        with patch.object(state, 'get_db', return_value=cursor), \
                patch.object(state, 'commit'), \
                patch.object(state, 'time', return_value=1000):
            state.mark_folder_result(
                job_id=9,
                folder='/content/Adult/Druuna',
                imported_volumes=1,
                review_reason=None,
                review_items=review_items
            )

        return [
            (call.args[0], call.args[1])
            for call in cursor.execute.call_args_list
        ]

    def _supersede(self, writes):
        for sql, params in writes:
            if 'job_id != ?' in sql:
                return sql, params
        return None, None

    def test_an_earlier_pass_hold_on_the_same_folder_is_retired(self):
        sql, params = self._supersede(self._writes([]))

        self.assertIsNotNone(sql, 'nothing retires the superseded hold')
        self.assertIn('/content/Adult/Druuna', params)
        self.assertIn(9, params)
        self.assertIn(state.ITEM_DONE, params)
        self.assertIn(state.ITEM_REVIEW, params)

    def test_only_folders_still_marked_for_review_are_touched(self):
        sql, _ = self._supersede(self._writes([]))

        self.assertIn('state = ?', sql)
        self.assertIn('WHERE folder = ?', sql)

    def test_this_job_own_row_is_never_caught_by_it(self):
        """It writes this job's verdict one statement earlier."""
        _, params = self._supersede(self._writes([]))

        self.assertIn(9, params)

    def test_a_folder_this_pass_also_holds_still_supersedes_the_old_row(self):
        """The newest verdict is the live one either way.

        The folder stays counted -- this pass filed its own hold for it --
        but through one row instead of two.
        """
        writes = self._writes([{'filepath': '/content/Adult/Druuna/x.cbz'}])

        own = next(
            (sql, params)
            for sql, params in writes
            if 'job_id = ? AND folder = ?' in sql
        )
        self.assertIn(state.ITEM_REVIEW, own[1])
        self.assertEqual(
            loads(next(p for p in own[1] if isinstance(p, str)
                       and p.startswith('['))),
            [{'filepath': '/content/Adult/Druuna/x.cbz'}]
        )

        sql, _ = self._supersede(writes)
        self.assertIsNotNone(sql)
