"""The status iTerm2's tab bar shows, read when a session's `status` variable is empty (as after
iTerm2 restarts), and when iTerm2 set the variable, with stand-in sessions (no real iTerm2)."""

import asyncio
import unittest
import unittest.mock

import iterm2

from agent_command_tab import dashboard_core
from agent_command_tab.dashboard_core import DashboardCore
from agent_command_tab.terminal_backends import iterm2_terminal_backend
from agent_command_tab.terminal_backends.iterm2_terminal_backend import Iterm2TerminalBackend, read_status_shown_in_iterm2s_tab_bar
from condition_waiting import wait_until_in_a_coroutine
from gathered_window_builders import make_gathered_window
from snapshot_builder_tests import resolve


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


def make_iterm2_terminal_backend():
    return Iterm2TerminalBackend(lambda: None, lambda: None, lambda terminal_name, message: None)


def gather_tab(command_line, status_variable, session, iterm2_terminal_backend_to_gather_with=None):
    """Returns the first session that the iTerm2 backend (a new one when None) gathers for a tab
    with this one session, from stand-in iTerm2 variables of the tab and of the session, each
    read by its name."""
    iterm2_terminal_backend_to_gather_with = iterm2_terminal_backend_to_gather_with or make_iterm2_terminal_backend()
    variables_by_tab_id_and_session_id = {
        ("1", None): {"title": "tab title", "titleOverrideFormat": None},
        (None, session.session_id): {"jobPid": None, "commandLine": command_line, "status": status_variable, "path": "/p"}}

    def read_variables(connection, names, session_id=None, tab_id=None, window_id=None):
        return [variables_by_tab_id_and_session_id[(tab_id, session_id)][name] for name in names]
    tab = unittest.mock.Mock(tab_id="1", sessions=[session])
    with unittest.mock.patch.object(iterm2_terminal_backend, "read_iterm2_variables",
                                    unittest.mock.AsyncMock(side_effect=read_variables)):
        return asyncio.run(iterm2_terminal_backend_to_gather_with.gather_tab(tab))["first_session"]


class StatusInIterm2sTabBarTests(unittest.TestCase):
    def test_the_function_result_gives_the_status_or_none(self):
        session = StandInSession({"status": "idle", "turn_open": None, "dot_color": "#00d75f"})
        self.assertEqual(asyncio.run(read_status_shown_in_iterm2s_tab_bar(session)), "idle")
        self.assertEqual(session.invocations, ['iterm2.get_session_status(session_id: "session-1")'])
        for session in (StandInSession({"status": None}), StandInSession("unexpected"),
                        StandInSession(error=iterm2.RPCException("Timeout"))):
            self.assertIsNone(asyncio.run(read_status_shown_in_iterm2s_tab_bar(session)))

    def test_a_gather_reads_it_only_for_a_program_other_than_a_shell_whose_variable_is_empty(self):
        session = StandInSession({"status": "idle"})
        self.assertEqual(gather_tab("claude --verbose", None, session)["status"], "idle")
        self.assertEqual(len(session.invocations), 1)
        session = StandInSession({"status": "idle"})
        self.assertEqual(gather_tab("claude --verbose", "working", session)["status"], "working")  # the variable wins
        self.assertEqual(gather_tab("-zsh", None, session)["status"], None)  # a shell is no agent
        self.assertEqual(session.invocations, [])

    def test_the_tab_bars_status_makes_an_unknown_program_an_agent_only_when_the_variable_changed_after_the_program_started(self):
        # `gh` runs since 500 in a tab whose variable is empty while its tab bar shows idle. A gather
        # passes the tab bar's status on as the one set in iTerm2, so it counts as the variable's
        # own value does: by when iTerm2 last reported a change of the variable.
        def list_agent_tool_names(status_set_in_iterm2_at_by_session_id):
            backend = make_iterm2_terminal_backend()
            backend.status_set_in_iterm2_at_by_session_id = status_set_in_iterm2_at_by_session_id
            first_session = gather_tab("gh pr view 12", None, StandInSession({"status": "idle"}), backend)
            self.assertEqual((first_session["status"], first_session["status_set_in_iterm2"]), ("idle", "idle"))
            # The stand-in tab has no job process whose start the gather could read.
            gathered_tab = {"tab_id": "1", "first_session": dict(first_session, job_pid=77, job_started_at=500.0)}
            return [agent_session["agent_tool_name"] for agent_session in resolve([make_gathered_window("w", [gathered_tab])]).values()]

        self.assertEqual(list_agent_tool_names({}), [])  # no change reported since the dashboard connected, as after iTerm2 restarts
        self.assertEqual(list_agent_tool_names({"session-1": 499.0}), [])  # the last change came before gh started
        self.assertEqual(list_agent_tool_names({"session-1": 501.0}), ["gh"])


class TimeIterm2SetTheStatusTests(unittest.TestCase):
    def test_a_status_change_is_timed_also_during_a_gather_and_gathers_carry_the_time(self):
        backend = make_iterm2_terminal_backend()
        first_session = {"session_id": "session-1", "status_set_in_iterm2": None, "status_set_in_iterm2_at": None}
        backend.gathered_windows = [{"tabs": [{"first_session": first_session}]}]
        notification = unittest.mock.Mock(identifier="session-1", json_new_value='"working"')
        notification.name = "status"  # Mock(name=...) would name the mock itself

        def report_change_at(now):
            with unittest.mock.patch.object(iterm2_terminal_backend.time, "time", return_value=now):
                asyncio.run(backend.handle_session_variable_change(None, notification))

        report_change_at(501.0)
        self.assertEqual((first_session["status_set_in_iterm2"], first_session["status_set_in_iterm2_at"]), ("working", 501.0))
        backend.gather_is_in_progress = True
        report_change_at(502.0)  # the gather reads the value, and the time comes from here
        self.assertEqual(gather_tab("gh pr view", "working", StandInSession(), backend)["status_set_in_iterm2_at"], 502.0)
        # No change reported since the dashboard connected: no time.
        self.assertIsNone(gather_tab("gh pr view", "idle", StandInSession())["status_set_in_iterm2_at"])


class NoStatusAtAllTests(unittest.TestCase):
    def test_a_tab_iterm2_gives_no_status_goes_by_claude_codes_session_file_read_now(self):
        # Not by the `claude agents --json` record, which is up to a minute old.
        core = DashboardCore(lambda snapshot: None, lambda message: None)
        core.gathered_windows = [{"window_id": "w1", "terminal_name": "iTerm2", "tabs": [
            {"tab_id": "t1", "first_session": {"session_id": "s1", "status": None}},
            {"tab_id": "t2", "first_session": {"session_id": "s2", "status": "working"}}]}]
        core.agent_sessions = {"s1": {"process_id": 101}, "s2": {"process_id": 102}}
        core.claude_agents_by_process_id = {101: {"status": "idle", "config_folder": None},
                                            102: {"status": "idle", "config_folder": None}}
        with unittest.mock.patch.object(dashboard_core, "read_claude_code_session_status",
                                        lambda config_folder, process_id: "busy"):
            claude_agents_by_process_id = core.read_again_the_status_of_claude_agents_iterm2_reports_none_for()
        self.assertEqual(claude_agents_by_process_id[101]["status"], "busy")
        self.assertEqual(claude_agents_by_process_id[102]["status"], "idle")  # iTerm2 gives this tab a status
        self.assertEqual(core.claude_agents_by_process_id[101]["status"], "idle")  # the stored record stays

    def test_a_change_of_that_session_files_status_asks_for_a_snapshot(self):
        # Nothing else tells the core, so an "asking" would otherwise show only at the next snapshot.
        core = DashboardCore(lambda snapshot: None, lambda message: None)
        core.gathered_windows = [{"window_id": "w1", "terminal_name": "iTerm2", "tabs": [
            {"tab_id": "t1", "first_session": {"session_id": "s1", "status": None}}]}]
        core.agent_sessions = {"s1": {"process_id": 101}}
        core.claude_agents_by_process_id = {101: {"status": "busy", "config_folder": None}}
        statuses = ["busy", "busy", "waiting"]  # what the session file says at each reading
        statuses_read = []

        def read_the_session_file(config_folder, process_id):
            statuses_read.append(statuses[min(len(statuses_read), len(statuses) - 1)])
            return statuses_read[-1]

        async def watch_three_readings():
            watching_task = asyncio.ensure_future(core.watch_the_own_status_of_agents_whose_terminal_reports_none())
            snapshot_asked_for_after_each_reading = []
            for reading_count in range(1, len(statuses) + 1):
                self.assertTrue(await wait_until_in_a_coroutine(lambda: len(statuses_read) >= reading_count))
                snapshot_asked_for_after_each_reading.append(core.snapshot_needed.is_set())
                core.snapshot_needed.clear()  # well before the next reading
            watching_task.cancel()
            return snapshot_asked_for_after_each_reading
        with unittest.mock.patch.object(dashboard_core, "SECONDS_BETWEEN_OWN_STATUS_READINGS", 0.3), \
                unittest.mock.patch.object(dashboard_core, "read_claude_code_session_status", read_the_session_file):
            snapshot_asked_for_after_each_reading = asyncio.run(watch_three_readings())
        self.assertEqual(snapshot_asked_for_after_each_reading, [True, False, True])  # the first reading, then the change


if __name__ == "__main__":
    unittest.main()
