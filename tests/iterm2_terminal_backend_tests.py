"""The iTerm2 terminal backend's actions, with stand-ins for the iterm2 package's objects (no real iTerm2)."""

import asyncio
import unittest
import unittest.mock

import iterm2

from agent_command_tab.dashboard_core_api import ActionResult
from agent_command_tab.terminal_backends import iterm2_terminal_backend
from agent_command_tab.terminal_backends.base import TerminalRequestFailed
from agent_command_tab.terminal_backends.iterm2_terminal_backend import Iterm2TerminalBackend, Iterm2TerminalSession


def make_iterm2_terminal_backend(app=None):
    backend = Iterm2TerminalBackend(lambda: None, lambda: None, lambda terminal_name, message: None)
    backend.app = app
    return backend


class StartAgentSessionTests(unittest.TestCase):
    def test_a_new_window_returns_its_id_for_the_lists_to_select_it(self):  # user request, 2026-10-30
        # And its tab's, which a restored session's name is given to (user request, 2026-11-06).
        for new_window, expected_result in ((unittest.mock.Mock(window_id="w9", current_tab=unittest.mock.Mock(tab_id="t9")),
                                             ActionResult(True, details={"window_id": "w9", "tab_id": "t9"})),
                                            (None, ActionResult(True))):  # iTerm2 gives no window when its session ended right away
            with unittest.mock.patch.object(iterm2.Window, "async_create", unittest.mock.AsyncMock(return_value=new_window)):
                self.assertEqual(asyncio.run(make_iterm2_terminal_backend().start_agent_session(None, "/tmp", "claude")),
                                 expected_result)

    def test_a_duplicate_goes_right_after_its_tab(self):
        window = unittest.mock.Mock(tabs=[unittest.mock.Mock(tab_id="t1"), unittest.mock.Mock(tab_id="t2")],
                                    async_create_tab=unittest.mock.AsyncMock(return_value=unittest.mock.Mock(tab_id="t3")))
        backend = make_iterm2_terminal_backend(unittest.mock.Mock(get_window_by_id=lambda window_id: window))
        self.assertEqual(asyncio.run(backend.start_agent_session("w1", "/tmp", "claude", after_tab_id="t1")),
                         ActionResult(True, details={"tab_id": "t3"}))
        self.assertEqual(window.async_create_tab.call_args.kwargs["index"], 1)
        self.assertFalse(asyncio.run(backend.start_agent_session("w1", "/tmp", "claude", after_tab_id="gone")).succeeded)


class PeekAndConnectionTests(unittest.TestCase):
    def test_ending_a_peek_makes_iterm2_active_again_when_it_was_in_front(self):
        # A peek at a tmux tab shown in another terminal app made that app active.
        window = unittest.mock.Mock(window_id="w1", current_tab=None, async_activate=unittest.mock.AsyncMock())
        app = unittest.mock.Mock(terminal_windows=[window], get_window_by_id=lambda window_id: window,
                                 async_activate=unittest.mock.AsyncMock())
        backend = make_iterm2_terminal_backend(app)
        asyncio.run(backend.put_back_what_was_in_front({"key_window_id": "w1", "selected_tab_id_by_window_id": {}}, True))
        window.async_activate.assert_awaited_once()
        app.async_activate.assert_awaited_once_with(raise_all_windows=False)

    def test_a_refused_connection_is_no_problem_once_iterm2_quit(self):
        problems = []
        backend = Iterm2TerminalBackend(lambda: None, lambda: None, lambda terminal_name, message: problems.append(message))

        async def run_for_a_moment():
            try:
                await asyncio.wait_for(backend.run(), 0.2)
            except asyncio.TimeoutError:
                pass
        # iTerm2 runs until the connection is refused, then quits.
        with unittest.mock.patch.object(iterm2_terminal_backend, "list_iterm2_process_ids", lambda: [] if problems else [7]), \
                unittest.mock.patch.object(iterm2.auth, "authenticate", return_value=True), \
                unittest.mock.patch.object(iterm2.Connection, "async_create", side_effect=OSError("Python API is off")), \
                unittest.mock.patch.object(iterm2_terminal_backend, "SECONDS_BEFORE_CONNECTING_AGAIN_AFTER_A_FAILURE", 0), \
                unittest.mock.patch.object(iterm2_terminal_backend, "SECONDS_BETWEEN_CHECKS_FOR_ITERM2_RUNNING", 0.01):
            asyncio.run(run_for_a_moment())
        self.assertIn("Python API is off", problems[0])
        self.assertIsNone(problems[-1])


class Iterm2TerminalSessionTests(unittest.TestCase):
    def test_typing_goes_to_the_session_as_it_is_and_a_paste_is_bracketed(self):
        session = unittest.mock.Mock(async_send_text=unittest.mock.AsyncMock())
        asyncio.run(Iterm2TerminalSession(session).send_text("\x15fg\r"))
        asyncio.run(Iterm2TerminalSession(session).paste_text("two\nlines"))
        self.assertEqual([call.args[0] for call in session.async_send_text.call_args_list],
                         ["\x15fg\r", "\x1b[200~two\nlines\x1b[201~"])

    def test_a_session_that_is_gone_raises_terminal_request_failed(self):
        session = unittest.mock.Mock(async_send_text=unittest.mock.AsyncMock(side_effect=iterm2.RPCException("SESSION_NOT_FOUND")),
                                     async_get_screen_contents=unittest.mock.AsyncMock(side_effect=iterm2.RPCException("SESSION_NOT_FOUND")))
        for read_or_type in (lambda: Iterm2TerminalSession(session).send_text("x"),
                             lambda: Iterm2TerminalSession(session).read_screen_lines()):
            with self.assertRaises(TerminalRequestFailed):
                asyncio.run(read_or_type())


if __name__ == "__main__":
    unittest.main()
