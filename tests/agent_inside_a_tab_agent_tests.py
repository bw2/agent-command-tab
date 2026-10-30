"""The status a tab's row goes by while an agent program runs inside the tab's agent, and after it
ends until iTerm2's status fits the tab's agent again (DashboardCore.choose_status_value_of_the_tabs_own_agent):
such an agent sets the tab's status too."""

import unittest
import unittest.mock

from agent_command_tab import iterm2_connection_thread
from agent_command_tab.agent_adapters.claude_code import ClaudeCodeAdapter
from agent_command_tab.agent_adapters.codex import CodexAdapter
from agent_command_tab.agent_adapters.generic import GenericAdapter
from agent_command_tab.iterm2_connection_thread import DashboardCore


class AgentInsideATabAgentTests(unittest.TestCase):
    def setUp(self):
        # s1 runs Claude Code, s2 Codex, s3 a tool that reports nothing of its own.
        self.core = DashboardCore(lambda snapshot: None, lambda message: None)
        self.core.agent_sessions = {"s1": {"adapter": ClaudeCodeAdapter(), "process_id": 101, "is_in_background": False},
                                    "s2": {"adapter": CodexAdapter(), "process_id": 201, "is_in_background": False},
                                    "s3": {"adapter": GenericAdapter(), "process_id": 301, "is_in_background": False}}
        self.first_sessions = {session_id: {"session_id": session_id, "status_set_in_iterm2": "working", "status": "working"}
                               for session_id in ("s1", "s2", "s3")}
        self.core.gathered_windows = [{"window_id": "w1", "tabs": [
            {"tab_id": f"t{index}", "first_session": first_session} for index, first_session in enumerate(self.first_sessions.values())]}]
        self.an_agent_runs_inside = False
        self.claude_code_status = "busy"
        self.list_descendant_processes = unittest.mock.Mock(return_value=[])
        for name, replacement in (
                ("list_descendant_processes", self.list_descendant_processes),
                ("an_agent_program_runs_through_a_shell_below", lambda processes, read_name: self.an_agent_runs_inside),
                ("read_claude_code_session_status", lambda config_folder, process_id: self.claude_code_status)):
            patch = unittest.mock.patch.object(iterm2_connection_thread, name, replacement)
            patch.start()
            self.addCleanup(patch.stop)
        self.core.update_session_tracking(1.0)

    def track(self, now, status_set_in_iterm2=None):
        """Tracks the tabs' status (after iTerm2 got a new value for all of them, if given) and
        returns the status each row goes by."""
        for first_session in self.first_sessions.values():
            first_session["status_set_in_iterm2"] = status_set_in_iterm2 or first_session["status_set_in_iterm2"]
        self.core.update_session_tracking(now)
        return {session_id: first_session["status"] for session_id, first_session in self.first_sessions.items()}

    def show_codex_turn_on_screen(self, turn_shows_on_screen):
        self.core.screen_readings_by_session_id["s2"]["turn_shows_on_screen"] = turn_shows_on_screen  # as the screen check does

    def test_a_tab_goes_by_its_own_agent_while_another_runs_inside_it(self):
        self.an_agent_runs_inside = True
        # The inner agent's turn ends: Claude Code says its own agent is busy; Codex's screen isn't checked yet.
        self.assertEqual(self.track(2.0, "done"), {"s1": "working", "s2": "working", "s3": "working"})
        self.assertNotIn("last_turn_ended_at", self.core.session_tracking_by_session_id["s1"])
        # The outer agents' turns end while the inner agent, left in the background, starts another.
        self.claude_code_status = "idle"
        self.show_codex_turn_on_screen(False)
        self.assertEqual(self.track(3.0, "working"), {"s1": "idle", "s2": "idle", "s3": "working"})
        self.assertEqual(self.core.session_tracking_by_session_id["s1"]["last_turn_ended_at"], 3.0)
        # Once the inner agent is gone and iTerm2 says what the tabs' agents say, iTerm2 counts again.
        self.an_agent_runs_inside = False
        self.assertEqual(self.track(4.0, "done"), {"s1": "done", "s2": "done", "s3": "done"})
        self.assertEqual(self.core.iterm2_status_seen_while_an_inner_agent_ran_by_session_id, {})

    def test_a_tab_first_seen_while_an_agent_runs_inside_its_agent_goes_by_its_own_agent(self):  # code review, 2026-10-29
        # The app restarted while the inner agent ran and had set idle: the check came only once
        # iTerm2's value changed, so Claude Code's busy agent showed idle meanwhile.
        self.core.session_tracking_by_session_id.clear()  # as a new core has it
        self.core.status_set_in_iterm2_and_since_by_session_id.clear()
        self.an_agent_runs_inside = True
        # Codex's screen isn't checked yet, and the third tool reports nothing: iTerm2's value counts for them.
        self.assertEqual(self.track(2.0, "idle"), {"s1": "working", "s2": "idle", "s3": "idle"})
        self.assertEqual(self.track(3.0), {"s1": "working", "s2": "idle", "s3": "idle"})

    def test_after_the_inner_agent_ends_its_last_status_counts_only_once_the_tabs_agent_agrees(self):
        self.an_agent_runs_inside = True
        self.track(2.0, "idle")  # the inner agent starts
        self.show_codex_turn_on_screen(True)
        # It ends, leaving its "idle" in iTerm2, while the tabs' agents go on with their turns.
        self.an_agent_runs_inside = False
        self.assertEqual(self.track(3.0), {"s1": "working", "s2": "working", "s3": "working"})
        # Claude Code and Codex finish their turns without setting a new value.
        self.claude_code_status = "idle"
        self.show_codex_turn_on_screen(False)
        self.assertEqual(self.track(4.0), {"s1": "idle", "s2": "idle", "s3": "working"})
        self.assertEqual(self.core.session_tracking_by_session_id["s2"]["last_turn_ended_at"], 4.0)
        # The tool without a report of its own counts again once iTerm2's value changes.
        self.assertEqual(self.track(5.0, "done"), {"s1": "done", "s2": "done", "s3": "done"})

    def test_the_processes_are_listed_only_when_the_status_set_in_iterm2_changed(self):
        # The first sighting (setUp's) counts as a change (code review, 2026-10-29).
        self.assertEqual(self.list_descendant_processes.call_count, 3)
        self.list_descendant_processes.reset_mock()
        self.core.update_session_tracking(2.0)
        self.list_descendant_processes.assert_not_called()
        self.track(3.0, "idle")
        self.assertEqual(self.list_descendant_processes.call_count, 3)


if __name__ == "__main__":
    unittest.main()
