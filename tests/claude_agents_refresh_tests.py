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

    def test_a_clear_typed_while_a_run_is_under_way_has_the_next_gather_run_again(self):  # code review, 2026-10-28
        core = DashboardCore(lambda snapshot: None, lambda message: None)
        core.claude_command_path = "/stand-in/claude"
        core.agent_sessions = {"s1": {"adapter": ClaudeCodeAdapter(), "process_id": 42}}
        core.process_ids_asked_about_in_claude_agents = {42}

        async def read_while_a_clear_is_typed(claude_command_path, config_folders):
            core.note_hook_event({"agent_tool_name": "Claude Code", "hook_event_name": "SessionStart", "source": "clear",
                                  "ancestor_processes": [[1042, "sh"], [42, "2.1.311"]], "sent_at": 5.0})
            core.regather_needed.clear()  # the gather it asks for finds this run under way
            return {42: {"sessionId": "before-the-clear"}}
        with unittest.mock.patch("agent_command_tab.iterm2_connection_thread.read_claude_agents_by_process_id",
                                 read_while_a_clear_is_typed):
            asyncio.run(core.refresh_claude_agents())
        self.assertTrue(core.regather_needed.is_set())  # so 42 is asked about again


if __name__ == "__main__":
    unittest.main()
