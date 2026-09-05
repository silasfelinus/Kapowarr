# -*- coding: utf-8 -*-

"""The Review Holds button sat disabled showing another task's message.

`waitForTaskCompletion` in `library_import_review_ui.js` waits out a
maintenance pass by polling `/system/tasks` for the id it was handed and
finishing when nothing in the queue has it. That is sound only if an id
belongs to one task for as long as anyone might ask about it.

It did not. A task's id was `queue[-1]['id'] + 1` -- the tail of a list
entries are removed from the *middle* of -- so finishing the newest task
handed its number straight to the next one. Silas's 2026-09-05 log has
Watched Folder Import (5) still queued while Feed Sync ids climbed past
40, which is exactly that shape: one long job with quarter-hourly ones
coming and going behind it.

So "Reset & Re-evaluate Holds" would finish, its id would be reissued to
Recover Orphaned Downloads, and the poll would find a stranger and keep
waiting -- showing that task's message, "Checking the download folder for
anything left behind", with every maintenance button disabled behind it.
"""

import unittest
from itertools import count
from unittest.mock import patch

from backend.features import tasks as tasks_module


class _FakeTask:
    action = 'some_task'
    display_title = 'Some Task'
    stop = False
    category = ''

    def __init__(self, name):
        self.action = name
        self.display_title = name


class _FakeHandler:
    """Just the queue and the two methods that touch ids."""

    def __init__(self):
        self.queue = []

    def _process_queue(self):
        return

    def _TaskHandler__run_task(self, entry):
        return

    add = tasks_module.TaskHandler.add


class ids_are_a_sequence_not_a_lookup(unittest.TestCase):
    def setUp(self):
        self.handler = _FakeHandler()
        # Each test gets its own counter so ids are predictable.
        self.counter = count(1)
        patcher = patch.object(tasks_module, '_TASK_IDS', self.counter)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _add(self, name):
        with patch.object(tasks_module, 'WebSocket'):
            return self.handler.add(_FakeTask(name))

    def _finish(self, name):
        self.handler.queue[:] = [
            e for e in self.handler.queue if e['task'].action != name
        ]

    def test_finishing_the_newest_task_does_not_hand_on_its_id(self):
        """The exact shape from the log: one long job, short ones behind."""
        long_id = self._add('watched_folder_import')

        issued = [long_id]
        for round in range(4):
            issued.append(self._add(f'feed_sync_{round}'))
            self._finish(f'feed_sync_{round}')

        self.assertEqual(len(set(issued)), len(issued), msg=f'reused: {issued}')

    def test_an_id_is_never_seen_twice_however_the_queue_drains(self):
        issued = []
        for round in range(20):
            issued.append(self._add(f'a{round}'))
            issued.append(self._add(f'b{round}'))
            # Drain in the order that used to lower the next id.
            self._finish(f'b{round}')
            if round % 2:
                self._finish(f'a{round}')

        self.assertEqual(len(set(issued)), len(issued))

    def test_an_emptied_queue_does_not_start_over(self):
        first = self._add('one')
        self._finish('one')
        self.assertEqual(self.handler.queue, [])

        self.assertNotEqual(self._add('two'), first)

    def test_the_id_still_names_the_thread(self):
        task_id = self._add('one')
        self.assertEqual(
            self.handler.queue[0]['thread'].name, f'TaskThread-{task_id}'
        )


class the_allocator_reads_no_state_from_the_queue(unittest.TestCase):
    def test_it_does_not_index_the_queue_for_a_number(self):
        import inspect

        source = inspect.getsource(tasks_module._add_laned)
        self.assertIn('next(_TASK_IDS)', source)
        self.assertNotIn("self.queue[-1]['id']", source)


if __name__ == '__main__':
    unittest.main()
