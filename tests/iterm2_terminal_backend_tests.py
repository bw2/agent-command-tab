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


def make_iterm2_session_whose_connection_replies(reply):
    """Returns an iterm2.Session ("s1") whose stand-in connection answers every request with reply
    (an iterm2.api_pb2.ServerOriginatedMessage), so the iterm2 package's own code runs, up to the
    connection that would send the request to iTerm2."""
    connection = unittest.mock.Mock(async_send_message=unittest.mock.AsyncMock(),
                                    async_dispatch_until_id=unittest.mock.AsyncMock(return_value=reply))
    return iterm2.Session(connection, None, iterm2.api_pb2.SessionSummary(unique_identifier="s1"))


def build_reply_to_typed_text(status_name):
    """Returns iTerm2's reply to text typed into a session: "OK", or "SESSION_NOT_FOUND" for one that has closed."""
    return iterm2.api_pb2.ServerOriginatedMessage(send_text_response=iterm2.api_pb2.SendTextResponse(status=status_name))


class StartAgentSessionTests(unittest.TestCase):
    def test_a_new_window_returns_its_id_for_the_lists_to_select_it(self):
        # And its tab's, which a restored session's name is given to.
        new_window = unittest.mock.Mock(window_id="w9", tabs=[unittest.mock.Mock(tab_id="t9")])
        with unittest.mock.patch.object(iterm2.Window, "async_create", unittest.mock.AsyncMock(return_value=new_window)):
            self.assertEqual(asyncio.run(make_iterm2_terminal_backend().start_agent_session(None, "/tmp", "claude")),
                             ActionResult(True, details={"window_id": "w9", "tab_id": "t9"}))

    def test_a_new_window_or_tab_that_closed_at_once_is_a_failure(self):
        # iTerm2 gives no window or tab when the new session ended right away.
        with unittest.mock.patch.object(iterm2.Window, "async_create", unittest.mock.AsyncMock(return_value=None)):
            self.assertEqual(asyncio.run(make_iterm2_terminal_backend().start_agent_session(None, "/tmp", "claude")),
                             ActionResult(False, "the new window closed at once"))
        window = unittest.mock.Mock(tabs=[unittest.mock.Mock(tab_id="t1")], async_create_tab=unittest.mock.AsyncMock(return_value=None))
        backend = make_iterm2_terminal_backend(unittest.mock.Mock(get_window_by_id=lambda window_id: window))
        for after_tab_id in (None, "t1"):  # a new tab at the window's end, and a duplicate right after its tab
            self.assertEqual(asyncio.run(backend.start_agent_session("w1", "/tmp", "claude", after_tab_id=after_tab_id)),
                             ActionResult(False, "the new tab closed at once"))

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
        window = unittest.mock.Mock(window_id="w1", tabs=[], current_tab=None, async_activate=unittest.mock.AsyncMock())
        app = unittest.mock.Mock(terminal_windows=[window], get_window_by_id=lambda window_id: window,
                                 async_activate=unittest.mock.AsyncMock())
        backend = make_iterm2_terminal_backend(app)
        asyncio.run(backend.put_back_what_was_in_front(
            {"key_window_id": "w1", "selected_tab_id_by_window_id": {}, "active_session_id_by_tab_id": {}}, True))
        window.async_activate.assert_awaited_once()
        app.async_activate.assert_awaited_once_with(raise_all_windows=False)

    def test_ending_a_peek_gives_a_split_tab_back_the_pane_its_keys_went_to(self):
        # The peek activates the agent's pane, the tab's first, while its user was typing into the second.
        agent_session, session_typed_into, session_of_another_tab = (
            unittest.mock.Mock(session_id=session_id, async_activate=unittest.mock.AsyncMock()) for session_id in ("s1", "s2", "s3"))
        split_tab = unittest.mock.Mock(tab_id="t1", current_session=session_typed_into, async_select=unittest.mock.AsyncMock())
        tab_the_peek_leaves_alone = unittest.mock.Mock(tab_id="t2", current_session=session_of_another_tab,
                                                       async_select=unittest.mock.AsyncMock())
        tab_whose_active_pane_isnt_known = unittest.mock.Mock(tab_id="t3", current_session=None, async_select=unittest.mock.AsyncMock())
        window = unittest.mock.Mock(window_id="w1", current_tab=split_tab,
                                    tabs=[split_tab, tab_the_peek_leaves_alone, tab_whose_active_pane_isnt_known])
        app = unittest.mock.Mock(terminal_windows=[window], current_terminal_window=window, get_session_by_id={
            "s1": agent_session, "s2": session_typed_into, "s3": session_of_another_tab}.get,
            get_tab_by_id={tab.tab_id: tab for tab in window.tabs}.get)
        backend = make_iterm2_terminal_backend(app)
        what_was_in_front = asyncio.run(backend.remember_what_is_in_front())
        self.assertEqual(what_was_in_front["active_session_id_by_tab_id"], {"t1": "s2", "t2": "s3"})
        split_tab.current_session = agent_session  # as iTerm2 reports once the peek has activated the agent's pane
        self.assertTrue(asyncio.run(backend.put_back_what_was_in_front(what_was_in_front, False)).succeeded)
        # Its tab is selected already, and its window isn't raised: the app in front before the peek stays there.
        session_typed_into.async_activate.assert_awaited_once_with(select_tab=False, order_window_front=False)
        for session_left_alone in (agent_session, session_of_another_tab):
            session_left_alone.async_activate.assert_not_awaited()
        for tab in window.tabs:
            tab.async_select.assert_not_awaited()

    def test_a_refused_connection_is_no_problem_once_iterm2_quit(self):
        problems = []
        backend = Iterm2TerminalBackend(lambda: None, lambda: None, lambda terminal_name, message: problems.append(message))

        async def run_for_a_moment():
            try:
                await asyncio.wait_for(backend.run(), 0.2)
            except asyncio.TimeoutError:
                pass
        # iTerm2 runs until the connection is refused, then quits. run() changes gAppKitAvailable for
        # the whole process, so it is patched (to the value it has) to get that value back afterwards.
        with unittest.mock.patch.object(iterm2_terminal_backend, "list_iterm2_process_ids", lambda: [] if problems else [7]), \
                unittest.mock.patch.object(iterm2.auth, "gAppKitAvailable", iterm2.auth.gAppKitAvailable), \
                unittest.mock.patch.object(iterm2.auth, "authenticate", return_value=True), \
                unittest.mock.patch.object(iterm2.Connection, "async_create", side_effect=OSError("Python API is off")), \
                unittest.mock.patch.object(iterm2_terminal_backend, "SECONDS_BEFORE_CONNECTING_AGAIN_AFTER_A_FAILURE", 0), \
                unittest.mock.patch.object(iterm2_terminal_backend, "SECONDS_BETWEEN_CHECKS_FOR_ITERM2_RUNNING", 0.01):
            asyncio.run(run_for_a_moment())
        self.assertIn("Python API is off", problems[0])
        self.assertIsNone(problems[-1])

    def test_iterm2s_process_ids_are_looked_up_each_time_rather_than_kept_from_connecting(self):
        # Read once right after connecting, they came back empty in about half the starts, and the
        # tab in front then lost its outline as soon as the list opened.
        backend = make_iterm2_terminal_backend()
        for iterm2_process_ids in ([], [1472]):
            with unittest.mock.patch.object(iterm2_terminal_backend, "list_iterm2_process_ids", return_value=iterm2_process_ids):
                self.assertEqual(backend.list_application_process_ids(), iterm2_process_ids)


class Iterm2TerminalSessionTests(unittest.TestCase):
    def test_typing_goes_to_the_session_as_it_is_and_a_paste_is_bracketed(self):
        session = make_iterm2_session_whose_connection_replies(build_reply_to_typed_text("OK"))
        asyncio.run(Iterm2TerminalSession(session).send_text("\x15fg\r"))
        asyncio.run(Iterm2TerminalSession(session).paste_text("two\nlines"))
        # suppress_broadcast keeps the text out of the other sessions when iTerm2 broadcasts input to them.
        self.assertEqual([(request.session, request.text, request.suppress_broadcast) for request in (
            sent_message.args[0].send_text_request for sent_message in session.connection.async_send_message.await_args_list)],
            [("s1", "\x15fg\r", True), ("s1", "\x1b[200~two\nlines\x1b[201~", True)])

    def test_a_session_that_is_gone_raises_terminal_request_failed(self):
        # iTerm2 answers text typed into a closed session with a status, and a request it can't read with an error.
        for reply in (build_reply_to_typed_text("SESSION_NOT_FOUND"), iterm2.api_pb2.ServerOriginatedMessage(error="malformed request")):
            session = make_iterm2_session_whose_connection_replies(reply)
            session.async_get_screen_contents = unittest.mock.AsyncMock(side_effect=iterm2.RPCException("SESSION_NOT_FOUND"))
            for type_paste_or_read in (lambda: Iterm2TerminalSession(session).send_text("x"),
                                       lambda: Iterm2TerminalSession(session).paste_text("x"),
                                       lambda: Iterm2TerminalSession(session).read_screen_lines()):
                with self.assertRaises(TerminalRequestFailed):
                    asyncio.run(type_paste_or_read())


class WindowTitleSetFromTheCommandLineTests(unittest.TestCase):
    def test_a_title_set_in_a_session_becomes_its_windows_own_title(self):
        window = unittest.mock.Mock(window_id="w1", async_set_title=unittest.mock.AsyncMock())
        session = unittest.mock.Mock(session_id="s1")
        sessions_found = []  # what each lookup finds: s1 only after the App object's refresh here

        def find_session(session_id):
            return sessions_found[-1] if sessions_found else None
        app = unittest.mock.Mock(get_session_by_id=find_session, get_window_by_id=lambda window_id: window,
                                 get_window_and_tab_for_session=lambda found_session: (window, None),
                                 async_refresh=unittest.mock.AsyncMock(side_effect=lambda: sessions_found.append(session)))
        backend = make_iterm2_terminal_backend(app)

        def set_title(json_new_value):
            asyncio.run(backend.give_a_window_the_title_set_from_its_command_line(
                None, unittest.mock.Mock(identifier="s1", json_new_value=json_new_value)))
        # The command empties the variable before it sets the title: nothing to do then.
        for json_new_value in ('""', None, '"  "'):
            set_title(json_new_value)
        window.async_set_title.assert_not_awaited()
        set_title('"Michelle"')  # in a session newer than the App object's last refresh
        set_title('"C:\\\\Data"')  # a backslash is no interpolation in iTerm2's title
        self.assertEqual([call.args for call in window.async_set_title.await_args_list], [("Michelle",), ("C:\\\\Data",)])

    def test_a_title_set_in_a_session_that_is_gone_is_only_reported(self):
        app = unittest.mock.Mock(get_session_by_id=lambda session_id: None, async_refresh=unittest.mock.AsyncMock())
        with unittest.mock.patch("sys.stderr") as standard_error:
            asyncio.run(make_iterm2_terminal_backend(app).give_a_window_the_title_set_from_its_command_line(
                None, unittest.mock.Mock(identifier="gone", json_new_value='"Michelle"')))
        self.assertIn("its session is gone", "".join(call.args[0] for call in standard_error.write.call_args_list))


if __name__ == "__main__":
    unittest.main()
