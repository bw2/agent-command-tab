"""A paused agent's turn goes on where it was once it is resumed (DashboardCore.update_session_tracking):
the shell in front of it while it is paused has no status, which must not end or restart the turn."""

import unittest
import unittest.mock

from agent_command_tab import iterm2_connection_thread
from agent_command_tab.agent_adapters.claude_code import ClaudeCodeAdapter
from agent_command_tab.iterm2_connection_thread import DashboardCore


class PausedAgentTurnTests(unittest.TestCase):
    def test_the_turn_start_stays_across_a_pause(self):
        core = DashboardCore(lambda snapshot: None, lambda message: None)
        core.agent_sessions = {"s1": {"adapter": ClaudeCodeAdapter(), "process_id": 101, "is_in_background": False}}
        first_session = {"session_id": "s1", "status_set_in_iterm2": "idle", "status": "idle", "job_pid": 101}
        core.gathered_windows = [{"window_id": "w1", "tabs": [{"tab_id": "t1", "first_session": first_session}]}]
        agent_is_stopped = []
        patch = unittest.mock.patch.object(iterm2_connection_thread, "process_is_stopped", lambda process_id: bool(agent_is_stopped))
        patch.start()
        self.addCleanup(patch.stop)
        for now, status_set_in_iterm2, is_stopped, job_pid in ((1.0, "idle", False, 101), (2.0, "working", False, 101),  # a turn starts
                                                               # paused: iTerm2's jobPid still names the agent for a while
                                                               (3.0, None, True, 101),
                                                               (3.5, None, False, 77),  # resumed, iTerm2 still sees the shell
                                                               (4.0, "working", False, 101)):  # back in front
            agent_is_stopped[:] = [True] if is_stopped else []
            first_session.update(status_set_in_iterm2=status_set_in_iterm2, job_pid=job_pid)
            core.update_session_tracking(now)
        tracking = core.session_tracking_by_session_id["s1"]
        self.assertEqual(tracking["turn_started_at"], 2.0)  # so the command it started before the pause still counts
        self.assertNotIn("last_turn_ended_at", tracking)


if __name__ == "__main__":
    unittest.main()
