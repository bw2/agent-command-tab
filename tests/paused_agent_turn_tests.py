"""A paused agent's turn goes on where it was once it is resumed (DashboardCore.update_session_tracking):
the shell in front of it while it is paused has no status, which must not end or restart the turn."""

import unittest
import unittest.mock

from agent_command_tab import dashboard_core
from agent_command_tab.dashboard_core import DashboardCore
from gathered_window_builders import make_agent_session, make_gathered_tab, make_gathered_window


class PausedAgentTurnTests(unittest.TestCase):
    def test_the_turn_start_stays_across_a_pause(self):
        core = DashboardCore(lambda snapshot: None, lambda message: None)
        core.agent_sessions = {"s1": make_agent_session(101)}
        core.gathered_windows = [make_gathered_window("w1", [
            make_gathered_tab("t1", "s1", "claude", 101, status_set_in_iterm2="idle", status="idle")])]
        first_session = core.gathered_windows[0]["tabs"][0]["first_session"]
        for now, status_set_in_iterm2, is_stopped, job_pid in ((1.0, "idle", False, 101), (2.0, "working", False, 101),  # a turn starts
                                                               # paused: iTerm2's jobPid still names the agent for a while
                                                               (3.0, None, True, 101),
                                                               (3.5, None, False, 77),  # resumed, iTerm2 still sees the shell
                                                               (4.0, "working", False, 101)):  # back in front
            first_session.update(status_set_in_iterm2=status_set_in_iterm2, job_pid=job_pid)
            with unittest.mock.patch.object(dashboard_core, "process_is_stopped", return_value=is_stopped):
                core.update_session_tracking(now)
        tracking = core.session_tracking_by_session_id["s1"]
        self.assertEqual(tracking["turn_started_at"], 2.0)  # so the command it started before the pause still counts
        self.assertNotIn("last_turn_ended_at", tracking)


if __name__ == "__main__":
    unittest.main()
