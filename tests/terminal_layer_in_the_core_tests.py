"""The core with several terminals behind the terminal layer (user request, 2026-11-01), with
stand-in terminal backends (no real iTerm2 or tmux): routing, status for a terminal that reports
none, which tab is in front, gathering, problems; and how the snapshot shows a tmux session."""

import asyncio
import unittest
import unittest.mock

from agent_command_tab import dashboard_core
from agent_command_tab.agent_adapters.claude_code import ClaudeCodeAdapter
from agent_command_tab.dashboard_core import DashboardCore
from agent_command_tab.dashboard_core_api import ActionRequest, ActionResult
from agent_command_tab.snapshot_builder import build_computer_snapshot
from agent_command_tab.snapshot_data_classes import AgentSessionEntry, WindowEntry, build_window_title_shown_in_the_list
from agent_command_tab.terminal_backends.base import TerminalBackend, TerminalClient, TerminalRequestFailed

MENU_BAR_MONITOR = {"monitor_id": "built-in", "name": "Built-in", "frame": (1000, 0, 1000, 800), "visible_frame": (1000, 0, 1000, 780)}
LEFT_MONITOR = {"monitor_id": "left", "name": "Left", "frame": (0, 0, 1000, 800), "visible_frame": (0, 0, 1000, 800)}


class StandInTerminalBackend(TerminalBackend):
    """A terminal with fixed windows; records the actions it is asked for."""

    def __init__(self, terminal_name, gathered_windows, **attributes):
        super().__init__(lambda: None, lambda: None, lambda terminal_name, message: None)
        self.terminal_name = terminal_name
        self.gathered_windows = gathered_windows
        self.connected = True
        self.runs_without_a_connection = False  # iTerm2 with its Python API off
        self.front_tab_id = None
        self.has_keyboard_focus = False
        self.clients = []
        self.application_process_ids = []
        self.actions = []
        self.gather_error = None
        self.__dict__.update(attributes)

    def is_connected(self):
        return self.connected

    def terminal_runs(self):
        return self.connected or self.runs_without_a_connection

    async def gather_windows(self):
        if self.gather_error is not None:
            raise self.gather_error
        return self.gathered_windows

    def find_selected_tab_id(self, window_id):
        return None

    def find_front_tab_id(self):
        return self.front_tab_id

    def a_terminal_window_has_keyboard_focus(self):
        return self.has_keyboard_focus

    def list_application_process_ids(self):
        return self.application_process_ids

    def list_clients(self):
        return self.clients

    async def focus_session(self, session_id):
        self.actions.append(("focus_session", session_id))
        return ActionResult(True, details={"terminal_client_process_id": 900} if self.shows_windows_through_clients else None)

    async def move_tab(self, tab_id, target_window_id, drop_before_tab_id):
        self.actions.append(("move_tab", tab_id, target_window_id))
        return ActionResult(True)

    async def start_agent_session(self, window_id, folder, launch_command, after_tab_id=None):
        self.actions.append(("start_agent_session", window_id))
        return ActionResult(True)


def make_gathered_window(terminal_name, window_id, tab_id, session_id, job_pid, frame=None):
    return {"window_id": window_id, "terminal_name": terminal_name, "frame": frame, "title": window_id,
            "title_override_format": None, "tabs": [{"tab_id": tab_id, "title": "", "title_override_format": None,
                                                     "first_session": {"session_id": session_id, "command_line": "claude",
                                                                       "job_pid": job_pid, "job_folder": "/p", "job_started_at": 1.0,
                                                                       "status_set_in_iterm2": None,
                                                                       "status_set_in_iterm2_at": None, "status": None,
                                                                       "path": "/p"}}]}


def make_core():
    """An iTerm2 window w1 (tab t1, session s1) whose tab runs the tmux client (process 900), and
    a tmux session $1 (window @1, pane %1) running Claude Code (process 101)."""
    core = DashboardCore(lambda snapshot: None, lambda message: None)
    iterm2 = StandInTerminalBackend("iTerm2", [make_gathered_window("iTerm2", "w1", "t1", "s1", 900, (0, 0, 800, 600))],
                                    reports_agent_status=True, windows_have_frames=True, application_process_ids=[50])
    tmux = StandInTerminalBackend("tmux", [make_gathered_window("tmux", "$1", "@1", "%1", 101)],
                                  shows_windows_through_clients=True, clients=[TerminalClient(900, "@1")])
    core.terminal_backends = [iterm2, tmux]
    core.terminal_backend_by_name = {"iTerm2": iterm2, "tmux": tmux}
    core.gathered_windows = asyncio.run(core.gather_every_terminals_windows())
    return core, iterm2, tmux


class RoutingTests(unittest.TestCase):
    def test_a_tab_moves_only_into_a_window_of_its_own_terminal(self):
        core, iterm2, tmux = make_core()
        action_result = asyncio.run(core.handle_move_tab(ActionRequest("move_tab", {"tab_id": "@1"}, {"target_window_id": "w1"})))
        self.assertEqual(action_result, ActionResult(False, "a tmux tab can't move into a iTerm2 window"))
        asyncio.run(core.handle_move_tab(ActionRequest("move_tab", {"tab_id": "@1"}, {"target_window_id": "$1"})))
        self.assertEqual(tmux.actions, [("move_tab", "@1", "$1")])
        self.assertEqual(asyncio.run(core.handle_rename_tab(ActionRequest("rename_tab", {"tab_id": "gone"}, {"new_title": "x"}))),
                         ActionResult(False, "that tab no longer exists"))

    def test_focusing_a_tmux_tab_also_brings_the_iterm2_tab_running_its_client_to_the_front(self):
        core, iterm2, tmux = make_core()
        action_result = asyncio.run(core.handle_focus_agent_session(ActionRequest("focus_agent_session", {"session_id": "%1"})))
        self.assertEqual((tmux.actions, iterm2.actions), ([("focus_session", "%1")], [("focus_session", "s1")]))
        self.assertEqual(action_result, ActionResult(True))  # the app has no other terminal app to bring forward
        iterm2.gathered_windows[0]["tabs"][0]["first_session"]["job_pid"] = 777  # the client runs elsewhere (Terminal, say)
        action_result = asyncio.run(core.handle_focus_agent_session(ActionRequest("focus_agent_session", {"session_id": "%1"})))
        self.assertEqual(action_result.details, {"terminal_client_process_id": 900})  # the app brings that app forward

    def test_a_new_window_opens_in_the_first_terminal_that_runs(self):
        core, iterm2, tmux = make_core()
        request = ActionRequest("start_agent_session", {}, {"folder": "/", "launch_command": "claude"})
        asyncio.run(core.handle_start_agent_session(request))
        iterm2.connected = False
        asyncio.run(core.handle_start_agent_session(request))
        self.assertEqual((iterm2.actions, tmux.actions), ([("start_agent_session", None)], [("start_agent_session", None)]))
        tmux.connected = False
        self.assertEqual(asyncio.run(core.handle_start_agent_session(request)),
                         ActionResult(False, "no terminal runs: start iTerm2, or a tmux session"))
        iterm2.runs_without_a_connection = True
        self.assertEqual(asyncio.run(core.handle_start_agent_session(request)),
                         ActionResult(False, "iTerm2 can't be reached yet (see the line at the top of the list)"))

    def test_a_new_tab_needs_a_folder_that_exists_and_a_launch_command(self):  # checked once, for every terminal
        core, iterm2, tmux = make_core()
        for folder, launch_command, message in (("/nonexistent/folder", "claude", "the folder /nonexistent/folder doesn't exist"),
                                                ("/", " ", "the agent tool has no launch command (see Settings)")):
            self.assertEqual(asyncio.run(core.handle_start_agent_session(ActionRequest(
                "start_agent_session", {"window_id": "$1"}, {"folder": folder, "launch_command": launch_command}))),
                ActionResult(False, message))
        self.assertEqual(tmux.actions, [])


class GatheringTests(unittest.TestCase):
    def test_a_terminal_whose_gather_fails_keeps_its_windows_and_one_not_running_has_none(self):
        core, iterm2, tmux = make_core()
        tmux.gather_error = TerminalRequestFailed("the server is busy")
        with unittest.mock.patch("sys.stderr"):  # where the failure is printed
            self.assertEqual([gathered_window["window_id"] for gathered_window in asyncio.run(core.gather_every_terminals_windows())],
                             ["w1", "$1"])
            iterm2.connected = False
            self.assertEqual([gathered_window["window_id"] for gathered_window in asyncio.run(core.gather_every_terminals_windows())],
                             ["$1"])

    def test_saved_frames_wait_for_a_terminal_with_frames(self):
        core = DashboardCore(lambda snapshot: None, lambda message: None)
        tmux = StandInTerminalBackend("tmux", [make_gathered_window("tmux", "$1", "@1", "%1", 101)], shows_windows_through_clients=True)
        core.terminal_backends, core.terminal_backend_by_name = [tmux], {"tmux": tmux}
        core.gathered_windows = asyncio.run(core.gather_every_terminals_windows())
        self.assertFalse(core.windows_were_gathered)  # a tmux session has no frame to match a saved one to
        self.assertEqual(core.list_gathered_windows_with_frames(), [])

    def test_problems_of_every_terminal_are_reported_together_until_they_end(self):
        reported = []
        core = DashboardCore(lambda snapshot: None, lambda message: None, report_terminal_problem=reported.append)
        with unittest.mock.patch("sys.stderr"):  # where the problems are printed
            core.note_terminal_problem("iTerm2", "could not connect")
            core.note_terminal_problem("iTerm2", "could not connect")  # the same again: nothing new to report
            core.note_terminal_problem("tmux", "the server is busy")
            core.note_terminal_problem("iTerm2", None)
            core.note_terminal_problem("tmux", None)
            core.note_terminal_problem("tmux", None)  # (iTerm2 not running clears its problem every few seconds)
        self.assertEqual(reported, ["could not connect", "could not connect; the server is busy", "the server is busy", None])


class StatusInATerminalThatReportsNoneTests(unittest.TestCase):
    def test_a_tmux_tab_goes_by_claude_codes_own_report(self):
        core, iterm2, tmux = make_core()
        core.agent_sessions = {"%1": {"adapter": ClaudeCodeAdapter(), "process_id": 101, "is_in_background": False}}
        status = {"value": "busy"}
        with unittest.mock.patch.object(dashboard_core, "read_claude_code_session_status",
                                        lambda config_folder, process_id: status["value"]), \
                unittest.mock.patch.object(dashboard_core, "process_is_stopped", lambda process_id: False):
            core.update_session_tracking(10.0)
            self.assertEqual(core.gathered_windows[1]["tabs"][0]["first_session"]["status"], "working")
            status["value"] = "waiting"
            core.update_session_tracking(11.0)
            self.assertEqual(core.gathered_windows[1]["tabs"][0]["first_session"]["status"], "waiting")
            self.assertEqual(core.own_status_value_by_session_id, {"%1": "waiting"})
            self.assertEqual(core.session_tracking_by_session_id["%1"]["status_change_time"], 11.0)


class SessionInFrontTests(unittest.TestCase):
    def test_the_tmux_tab_shown_by_the_client_in_iterm2s_front_tab_is_in_front(self):
        core, iterm2, tmux = make_core()
        iterm2.front_tab_id = "t1"
        self.assertIsNone(core.find_seen_session_id())  # iTerm2 has no keyboard focus
        iterm2.has_keyboard_focus = True
        self.assertEqual(core.find_seen_session_id(), "%1")
        iterm2.gathered_windows[0]["tabs"][0]["first_session"]["job_pid"] = 777  # another program in that tab
        core.gathered_windows = asyncio.run(core.gather_every_terminals_windows())
        self.assertEqual(core.find_seen_session_id(), "s1")

    def test_a_client_inside_another_terminal_app_is_highlighted_but_not_seen_while_that_app_is_active(self):
        # Terminal's tab in front may be another than the client's, so only the highlight goes by it.
        core, iterm2, tmux = make_core()
        iterm2.gathered_windows[0]["tabs"][0]["first_session"]["job_pid"] = 777
        core.gathered_windows = asyncio.run(core.gather_every_terminals_windows())
        ancestors_by_process_id = {900: [900, 800, 60]}  # 60: Terminal
        with unittest.mock.patch.object(dashboard_core, "list_the_process_and_its_ancestor_ids",
                                        lambda process_id: ancestors_by_process_id.get(process_id, [process_id])):
            core.set_last_other_active_application_process_id(60)
            self.assertIsNone(core.find_seen_session_id())
            self.assertEqual(core.find_highlighted_session_id(), "%1")
            iterm2.front_tab_id = "t1"
            core.dashboard_is_active = True
            self.assertIsNone(core.find_seen_session_id())
            self.assertEqual(core.find_highlighted_session_id(), "%1")  # Terminal was in front before the dashboard
            core.last_other_active_application_process_id = 50  # iTerm2 itself: its own tab is in front
            ancestors_by_process_id[900] = [900, 800, 50]  # (iTerm2 without its session servers, say)
            self.assertEqual(core.find_highlighted_session_id(), "s1")

    def test_a_tab_shown_from_the_list_counts_as_seen(self):
        core, iterm2, tmux = make_core()
        iterm2.gathered_windows[0]["tabs"][0]["first_session"]["job_pid"] = 777  # its client runs in another terminal app
        core.gathered_windows = asyncio.run(core.gather_every_terminals_windows())
        core.session_tracking_by_session_id["%1"] = {"last_time_tab_was_seen": None}
        with unittest.mock.patch.object(dashboard_core.time, "time", return_value=123.0):
            asyncio.run(core.handle_focus_agent_session(ActionRequest("focus_agent_session", {"session_id": "%1"})))
        self.assertEqual(core.session_tracking_by_session_id["%1"]["last_time_tab_was_seen"], 123.0)

    def test_the_tab_a_window_shown_from_the_list_shows_counts_as_seen(self):
        core, iterm2, tmux = make_core()
        tmux.find_selected_tab_id = lambda window_id: "@1"
        tmux.focus_window = unittest.mock.AsyncMock(return_value=ActionResult(True))
        core.session_tracking_by_session_id["%1"] = {"last_time_tab_was_seen": None}
        with unittest.mock.patch.object(dashboard_core.time, "time", return_value=124.0):
            asyncio.run(core.handle_focus_window(ActionRequest("focus_window", {"window_id": "$1"})))
        self.assertEqual(core.session_tracking_by_session_id["%1"]["last_time_tab_was_seen"], 124.0)

    def test_while_the_dashboard_is_active_a_client_in_the_app_used_last_wins_over_iterm2s_front_tab(self):
        core, iterm2, tmux = make_core()  # iTerm2's tab t1 runs the client 900, showing %1
        tmux.gathered_windows.append(make_gathered_window("tmux", "$2", "@2", "%2", 102))
        tmux.clients = [TerminalClient(900, "@1"), TerminalClient(901, "@2")]  # 901: in Terminal (process 60)
        core.gathered_windows = asyncio.run(core.gather_every_terminals_windows())
        iterm2.front_tab_id = "t1"
        core.dashboard_is_active = True
        with unittest.mock.patch.object(dashboard_core, "list_the_process_and_its_ancestor_ids",
                                        lambda process_id: {901: [901, 60]}.get(process_id, [process_id])):
            core.last_other_active_application_process_id = 60
            self.assertEqual(core.find_highlighted_session_id(), "%2")
            core.last_other_active_application_process_id = 50  # iTerm2: the client in its front tab
            self.assertEqual(core.find_highlighted_session_id(), "%1")


class SnapshotOfATmuxSessionTests(unittest.TestCase):
    def test_a_tmux_session_goes_with_the_menu_bar_monitor_and_is_named_with_tmux(self):
        gathered_windows = [make_gathered_window("iTerm2", "w1", "t1", "s1", 100, (10, 10, 500, 500)),
                            make_gathered_window("tmux", "$1", "@1", "%1", 101)]
        for gathered_window in gathered_windows:
            gathered_window["title_override_format"] = gathered_window["title"] = "project"
        agent_sessions = {session_id: {"adapter": ClaudeCodeAdapter(), "process_id": process_id, "is_in_background": False,
                                       "is_stopped": False, "agent_tool_name": "Claude Code", "working_folder": "/p", "started_at": 1.0}
                          for session_id, process_id in (("s1", 100), ("%1", 101))}
        snapshot = build_computer_snapshot(gathered_windows, agent_sessions, [MENU_BAR_MONITOR, LEFT_MONITOR], [], {}, {}, {}, 600, {},
                                           None, None, "c", "Mac", 20.0)
        windows_by_monitor_id = {monitor_entry.monitor_id: monitor_entry.windows for monitor_entry in snapshot.monitors}
        self.assertEqual([window_entry.window_id for window_entry in windows_by_monitor_id["left"]], ["w1"])
        tmux_window_entry, = windows_by_monitor_id["built-in"]
        self.assertEqual((tmux_window_entry.terminal_name, tmux_window_entry.can_move_to_another_monitor,
                          tmux_window_entry.number_among_windows_with_the_same_title), ("tmux", False, 1))
        self.assertEqual(build_window_title_shown_in_the_list(tmux_window_entry), "tmux: project")
        self.assertEqual(build_window_title_shown_in_the_list(windows_by_monitor_id["left"][0]), "project")
        # tmux would undo a pause of its running agent (agent_session_pausing), so the list offers none.
        self.assertEqual([window_entries[0].agent_sessions[0].can_be_paused_while_it_runs
                          for window_entries in (windows_by_monitor_id["left"], windows_by_monitor_id["built-in"])], [True, False])

    def test_an_iterm2_window_title_has_no_terminal_prefix_and_is_numbered_when_the_title_repeats(self):
        entry = AgentSessionEntry(tab_id="t", session_id="s", tab_title="", tab_title_set_by_user=False, agent_session_name="",
                                  agent_tool_name="", working_folder="", status="idle")
        window_entry = WindowEntry("w", "project", True, (0, 0, 1, 1), 1, (entry,), number_among_windows_with_the_same_title=2)
        self.assertEqual(build_window_title_shown_in_the_list(window_entry), "project (2)")


if __name__ == "__main__":
    unittest.main()
