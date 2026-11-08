"""How the core runs `claude agents --json` (read_claude_agents_by_process_id) and takes in a run
(DashboardCore.refresh_claude_agents)."""

import asyncio
import contextlib
import io
import os
import tempfile
import unittest
import unittest.mock

from agent_command_tab.agent_adapters.claude_code import ClaudeCodeAdapter
from agent_command_tab.dashboard_core import DashboardCore, read_claude_agents_by_process_id
from executable_script_writing import write_executable_script
from hook_event_builders import make_event


class ClaudeAgentsRefreshTests(unittest.TestCase):
    def test_a_run_that_misses_a_running_agent_keeps_its_last_record(self):
        core = DashboardCore(lambda snapshot: None, lambda message: None)
        core.claude_command_path = "/stand-in/claude"
        core.agent_sessions = {session_id: {"adapter": ClaudeCodeAdapter(), "process_id": process_id}
                               for session_id, process_id in (("s1", 42), ("s2", 43))}
        core.claude_agents_by_process_id = {42: {"sessionId": "a"}, 43: {"sessionId": "b"}, 99: {"sessionId": "ended"}}

        async def read_with_one_config_folder_timing_out(claude_command_path, config_folders):
            return {43: {"sessionId": "b-after-clear"}}
        with unittest.mock.patch("agent_command_tab.dashboard_core.read_claude_agents_by_process_id",
                                 read_with_one_config_folder_timing_out):
            asyncio.run(core.refresh_claude_agents())
        # 42 still runs, so its record stays (its conversation would look new without it); 99's process is gone.
        self.assertEqual(core.claude_agents_by_process_id, {42: {"sessionId": "a"}, 43: {"sessionId": "b-after-clear"}})

    def test_a_clear_typed_while_a_run_is_under_way_has_the_next_gather_run_again(self):
        core = DashboardCore(lambda snapshot: None, lambda message: None)
        core.claude_command_path = "/stand-in/claude"
        core.agent_sessions = {"s1": {"adapter": ClaudeCodeAdapter(), "process_id": 42}}
        core.process_ids_asked_about_in_claude_agents = {42}

        async def read_while_a_clear_is_typed(claude_command_path, config_folders):
            core.note_hook_event(make_event("SessionStart", 42, source="clear", sent_at=5.0))
            core.regather_needed.clear()  # the gather it asks for finds this run under way
            return {42: {"sessionId": "before-the-clear"}}
        with unittest.mock.patch("agent_command_tab.dashboard_core.read_claude_agents_by_process_id",
                                 read_while_a_clear_is_typed):
            asyncio.run(core.refresh_claude_agents())
        self.assertTrue(core.regather_needed.is_set())  # so 42 is asked about again


class ReadClaudeAgentsTests(unittest.TestCase):
    def test_a_claude_installed_with_npm_runs_with_the_node_next_to_it_on_a_bare_path(self):
        # npm's claude is a `#!/usr/bin/env node` script, and an app started by iTerm2 has launchd's PATH.
        with tempfile.TemporaryDirectory() as npm_folder:
            write_executable_script(npm_folder, "node", '#!/bin/sh\necho \'[{"pid": 42, "sessionId": "s"}]\'\n')
            claude_path = write_executable_script(npm_folder, "claude", "#!/usr/bin/env node\n")
            with unittest.mock.patch.dict(os.environ, {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin"}):
                self.assertEqual(asyncio.run(read_claude_agents_by_process_id(claude_path, [None])),
                                 {42: {"pid": 42, "sessionId": "s", "config_folder": None}})

    def test_a_failed_run_is_printed_on_standard_error_and_gives_no_records(self):
        # Standard output stays clean for dump-snapshot, which prints only its JSON there.
        with tempfile.TemporaryDirectory() as folder:
            claude_path = write_executable_script(folder, "claude", "#!/bin/sh\necho '[]'\necho 'unknown command agents' >&2\nexit 1\n")
            printed_on_standard_output, printed_on_standard_error = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(printed_on_standard_output), contextlib.redirect_stderr(printed_on_standard_error):
                self.assertEqual(asyncio.run(read_claude_agents_by_process_id(claude_path, [None])), {})
        self.assertIn("exit status 1: unknown command agents", printed_on_standard_error.getvalue())
        self.assertEqual(printed_on_standard_output.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
