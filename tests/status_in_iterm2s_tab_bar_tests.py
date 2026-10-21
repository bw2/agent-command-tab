"""The status iTerm2's tab bar shows, read when a session's `status` variable is empty (as after
iTerm2 restarts), with stand-in sessions (no real iTerm2)."""

import asyncio
import unittest
import unittest.mock

import iterm2

from agent_command_tab import iterm2_connection_thread
from agent_command_tab.iterm2_connection_thread import DashboardCore, read_status_shown_in_iterm2s_tab_bar


class StandInSession:
    """Answers iterm2.get_session_status with result, or raises error; records the invocations."""

    session_id = "session-1"

    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.invocations = []

    async def async_invoke_function(self, invocation, timeout=-1):
        self.invocations.append(invocation)
        if self.error is not None:
            raise self.error
        return self.result


class StatusInIterm2sTabBarTests(unittest.TestCase):
    def test_the_function_result_gives_the_status_or_none(self):
        session = StandInSession({"status": "idle", "turn_open": None, "dot_color": "#00d75f"})
        self.assertEqual(asyncio.run(read_status_shown_in_iterm2s_tab_bar(session)), "idle")
        self.assertEqual(session.invocations, ['iterm2.get_session_status(session_id: "session-1")'])
        for session in (StandInSession({"status": None}), StandInSession("unexpected"),
                        StandInSession(error=iterm2.RPCException("Timeout"))):
            self.assertIsNone(asyncio.run(read_status_shown_in_iterm2s_tab_bar(session)))

    def gather_tab(self, command_line, status_variable, session):
        def read_variables(connection, names, session_id=None, tab_id=None, window_id=None):
            return ["tab title", None] if tab_id is not None else [None, command_line, status_variable, "/p"]
        tab = unittest.mock.Mock(tab_id="1", sessions=[session])
        with unittest.mock.patch.object(iterm2_connection_thread, "read_iterm2_variables",
                                        unittest.mock.AsyncMock(side_effect=read_variables)):
            return asyncio.run(DashboardCore(lambda snapshot: None, lambda message: None).gather_iterm2_tab(tab))["first_session"]

    def test_a_gather_reads_it_only_for_a_program_other_than_a_shell_whose_variable_is_empty(self):
        session = StandInSession({"status": "idle"})
        self.assertEqual(self.gather_tab("claude --verbose", None, session)["status"], "idle")
        self.assertEqual(len(session.invocations), 1)
        session = StandInSession({"status": "idle"})
        self.assertEqual(self.gather_tab("claude --verbose", "working", session)["status"], "working")  # the variable wins
        self.assertEqual(self.gather_tab("-zsh", None, session)["status"], None)  # a shell is no agent
        self.assertEqual(session.invocations, [])


if __name__ == "__main__":
    unittest.main()
