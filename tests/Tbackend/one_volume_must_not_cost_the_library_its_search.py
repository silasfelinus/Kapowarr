# -*- coding: utf-8 -*-

"""Unattended acquisition ran once a day and stopped at the first error.

`SearchAll` is the whole of unattended acquisition: a daily sweep over every
monitored volume, and the only thing that fills in issues nobody went looking
for by hand. Its loop called `auto_search` with nothing around it, so any
exception from any source it touches ended the task where it stood and left
every volume after that one unsearched until tomorrow -- when the same thing
would happen at roughly the same place.

On 2026-08-31 that was a "database is locked" raised out of a config read on
the search path, twenty-one volumes into a library of thousands. Silas:
"Why have we failed to find and grab new comics? ... with the exception of
4-5 comics the only issues I've grabbed have been through specifically
searching."

The config read is fixed too (see
`a_read_on_the_search_path_does_not_write.py`), but that is one cause of one
failure. The loop has to survive the next one.
"""

import unittest
from unittest.mock import MagicMock, patch

from backend.features import tasks_core as TC


def _task(volumes):
    task = TC.SearchAll()
    cursor = MagicMock()
    cursor.execute.return_value.fetchall.return_value = volumes
    return task, cursor


def _run(task, cursor, auto_search):
    """Returns what the sweep queued, which is now the thing to assert on:
    results are enqueued per volume rather than handed back at the end."""
    queued = []
    with patch.object(TC, 'get_db', return_value=cursor), \
            patch.object(TC, 'WebSocket', MagicMock()), \
            patch.object(TC, 'auto_search', side_effect=auto_search), \
            patch.object(
                TC.SearchAll, '_queue',
                staticmethod(lambda entries: queued.extend(entries))
            ):
        task.run()
    return queued


class a_failing_volume(unittest.TestCase):
    VOLUMES = [(1, 'Aardvark'), (2, 'Batman'), (3, 'Creepy')]

    def test_the_sweep_continues_past_it(self):
        searched = []

        def auto_search(volume_id):
            searched.append(volume_id)
            if volume_id == 1:
                raise Exception('database is locked')
            return [{'link': 'https://example.test/%d' % volume_id}]

        task, cursor = _task(self.VOLUMES)
        downloads = _run(task, cursor, auto_search)

        self.assertEqual(searched, [1, 2, 3], 'every volume must be searched')
        self.assertEqual(
            downloads,
            [('https://example.test/2', 2, None, False),
             ('https://example.test/3', 3, None, False)]
        )

    def test_the_failure_is_not_silent(self):
        def auto_search(volume_id):
            raise RuntimeError('indexer exploded')

        task, cursor = _task(self.VOLUMES)
        with self.assertLogs(TC.LOGGER, 'ERROR') as captured:
            _run(task, cursor, auto_search)

        self.assertEqual(len(captured.records), 3, 'one line per volume')
        self.assertIn('Aardvark', captured.output[0])
        self.assertIn('indexer exploded', captured.output[0])

    def test_every_volume_failing_is_still_a_completed_sweep(self):
        # Rather than an exception out of the task runner, which is what
        # took the history record down with it.
        task, cursor = _task(self.VOLUMES)
        with self.assertLogs(TC.LOGGER, 'ERROR'):
            downloads = _run(
                task, cursor, lambda volume_id: 1 / 0
            )

        self.assertEqual(downloads, [])


class what_the_loop_still_honours(unittest.TestCase):
    def test_a_stop_request_still_stops_it(self):
        searched = []

        def auto_search(volume_id):
            searched.append(volume_id)
            task.stop = True
            raise Exception('and then it broke')

        task, cursor = _task([(1, 'A'), (2, 'B'), (3, 'C')])
        with self.assertLogs(TC.LOGGER, 'ERROR'):
            _run(task, cursor, auto_search)

        self.assertEqual(
            searched, [1],
            'catching the error must not swallow the stop that followed it'
        )

    def test_results_from_good_volumes_are_still_collected(self):
        task, cursor = _task([(7, 'Saga')])
        downloads = _run(
            task, cursor,
            lambda volume_id: [{'link': 'a'}, {'link': 'b'}]
        )

        self.assertEqual(
            downloads, [('a', 7, None, False), ('b', 7, None, False)]
        )


class what_a_stopped_sweep_keeps(unittest.TestCase):
    """The runner enqueues `if not task.stop`, so everything a sweep had
    found was thrown away the moment the user asked it to finish early."""

    def test_results_found_before_the_stop_are_already_queued(self):
        def auto_search(volume_id):
            if volume_id == 2:
                task.stop = True
            return [{'link': 'link-%d' % volume_id}]

        task, cursor = _task([(1, 'A'), (2, 'B'), (3, 'C')])
        queued = _run(task, cursor, auto_search)

        self.assertEqual(
            [entry[1] for entry in queued], [1, 2],
            'what the sweep found before stopping must survive the stop'
        )

    def test_the_runner_is_not_asked_to_queue_them_again(self):
        # It would double every download.
        task, cursor = _task([(1, 'A')])
        with patch.object(TC, 'get_db', return_value=cursor), \
                patch.object(TC, 'WebSocket', MagicMock()), \
                patch.object(
                    TC, 'auto_search', return_value=[{'link': 'a'}]
                ), \
                patch.object(TC.SearchAll, '_queue', staticmethod(lambda e: None)):
            returned = task.run()

        self.assertEqual(returned, [])


if __name__ == '__main__':
    unittest.main()


class a_lost_queue_write(unittest.TestCase):
    """Queueing is a write, and a write can lose. Losing one volume's
    results must not cost the rest of the library its turn."""

    def test_the_sweep_continues_past_it(self):
        cursor = MagicMock()
        cursor.execute.return_value.fetchall.return_value = [
            (1, 'A'), (2, 'B'), (3, 'C')]
        queued = []

        def flaky(entries):
            entries = list(entries)
            if entries and entries[0][1] == 1:
                raise Exception('database is locked')
            queued.extend(entries)

        with patch.object(TC, 'get_db', return_value=cursor), \
                patch.object(TC, 'WebSocket', MagicMock()), \
                patch.object(TC.SearchAll, '_mark_searched'), \
                patch.object(TC.SearchAll, '_queue', side_effect=flaky), \
                patch.object(
                    TC, 'auto_search',
                    side_effect=lambda v: [{'link': f'l{v}'}]):
            TC.SearchAll().run()

        self.assertEqual([v for _, v, _, _ in queued], [2, 3])
        return
