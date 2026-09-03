import inspect
import os
import select
import socket
import unittest
from unittest.mock import patch

import waitress.wasyncore as wasyncore

from backend.internals import server as server_module


class _Channel:
    """The shape waitress's loop expects of a thing it is watching."""

    def __init__(self, sock) -> None:
        self.socket = sock

    def fileno(self) -> int:
        return self.socket.fileno()

    def readable(self) -> bool:
        return True

    def writable(self) -> bool:
        return False

    def handle_read_event(self) -> None:
        return

    def handle_write_event(self) -> None:
        return

    def handle_expt_event(self) -> None:
        return

    def handle_close(self) -> None:
        return

    def handle_error(self) -> None:
        raise


class select_cannot_see_a_high_descriptor(unittest.TestCase):
    """Silas, 2026-09-03, with downloads running and six tabs open:

        ValueError: filedescriptor out of range in select()
          File "/app/backend/internals/server.py", line 195, in run
            self.server.run()

    The web interface stopped loading and the process died.

    `select()` cannot see a descriptor numbered 1024 or higher. That is
    FD_SETSIZE, a compile-time constant of the platform's C library, and it
    is about the *number* a descriptor was given, not how many are open. So
    one busy moment is enough to poison the loop for good: a socket handed a
    high number makes every later pass raise, long after whatever caused the
    spike has gone.
    """

    def setUp(self):
        # Push a socket's number above FD_SETSIZE, the way a busy moment does.
        self.holders = [
            os.open(os.devnull, os.O_RDONLY) for _ in range(1100)
        ]
        self.socket = socket.socket()
        if self.socket.fileno() < 1024:
            self.skipTest('could not raise a descriptor above FD_SETSIZE')
        self.map = {self.socket.fileno(): _Channel(self.socket)}

    def tearDown(self):
        self.socket.close()
        for fd in self.holders:
            os.close(fd)

    def test_the_select_loop_raises_on_it(self):
        with self.assertRaises(ValueError) as caught:
            wasyncore.poll(0.0, self.map)

        self.assertIn('filedescriptor out of range', str(caught.exception))

    def test_the_poll_loop_does_not(self):
        "`poll()` has no such ceiling, which is the whole fix."
        wasyncore.poll2(0.0, self.map)


class the_server_asks_for_the_loop_without_a_ceiling(unittest.TestCase):
    def test_it_is_created_with_poll_where_poll_exists(self):
        captured = {}

        def fake_create_server(app, **kwargs):
            captured.update(kwargs)
            raise RuntimeError('stop here')

        instance = server_module.Server.__new__(server_module.Server)
        instance.app = server_module.Flask(__name__)

        with patch.object(
            server_module, 'create_server', fake_create_server
        ), patch.object(server_module, 'ThreadedTaskDispatcher'):
            with self.assertRaises(RuntimeError):
                instance.run('0.0.0.0', 5656, '')

        self.assertEqual(
            captured.get('asyncore_use_poll'), hasattr(select, 'poll')
        )

    def test_windows_keeps_select_because_it_has_no_poll(self):
        """`select.poll` does not exist there, so asking for it would be an
        AttributeError at startup rather than a fix."""
        source = inspect.getsource(server_module.Server.run)

        self.assertIn("asyncore_use_poll=hasattr(select, 'poll')", source)

    def test_a_dead_serving_loop_says_so(self):
        """It used to come out as a bare traceback from waitress with
        nothing in it naming Kapowarr, and the process ended there --
        taking the downloads and tasks with it, unexplained.
        """
        source = inspect.getsource(server_module.Server.run)

        self.assertIn('LOGGER.exception', source)
        self.assertIn('cannot', source)
        # Still fatal: there is no Kapowarr without a web server.
        self.assertIn('raise', source)
