"""How the core takes in a run of `claude agents --json` (DashboardCore.refresh_claude_agents)."""

import asyncio
import unittest
import unittest.mock

from agent_command_tab.agent_adapters.claude_code import ClaudeCodeAdapter
from agent_command_tab.iterm2_connection_thread import DashboardCore


class ClaudeAgentsRefreshTests(unittest.TestCase):
    def test_a_run_that_misses_a_running_agent_keeps_its_last_record(self):
        core = DashboardCore(lambda snapshot: None, lambda message: None)
        core.claude_command_path = "/stand-in/claude"
        core.agent_sessions = {session_id: {"adapter": ClaudeCodeAdapter(), "process_id": process_id}
                               for session_id, process_id in (("s1", 42), ("s2", 43))}
        core.claude_agents_by_process_id = {42: {"sessionId": "a"}, 43: {"sessionId": "b"}, 99: {"sessionId": "ended"}}

        async def read_with_one_config_folder_timing_out(claude_command_path, config_folders):
            return {43: {"sessionId": "b-after-clear"}}
        with unittest.mock.patch("agent_command_tab.iterm2_connection_thread.read_claude_agents_by_process_id",
                                 read_with_one_config_folder_timing_out):
            asyncio.run(core.refresh_claude_agents())
        # 42 still runs, so its record stays (its conversation would look new without it); 99's process is gone.
        self.assertEqual(core.claude_agents_by_process_id, {42: {"sessionId": "a"}, 43: {"sessionId": "b-after-clear"}})


if __name__ == "__main__":
    unittest.main()
