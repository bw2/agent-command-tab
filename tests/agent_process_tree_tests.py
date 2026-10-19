import os
import subprocess
import tempfile
import time
import unittest

from llm_sidebar_dashboard.agent_adapters.claude_code import ClaudeCodeAdapter
from llm_sidebar_dashboard.agent_adapters.codex import CONVERSATION_FILE_PATTERN, CodexAdapter
from llm_sidebar_dashboard.agent_adapters.generic import GenericAdapter
from llm_sidebar_dashboard.agent_process_tree import (ProcessInfo, choose_command_the_agent_is_waiting_on,
                                                      list_descendant_processes, read_name_the_process_was_started_as,
                                                      read_process_info)

TURN_STARTED_AT = 1000.0
NOW = 1100.0


def make_process(process_id, parent_process_id, name, started_at):
    return ProcessInfo(process_id, parent_process_id, name, started_at)


class CommandTheAgentIsWaitingOnTests(unittest.TestCase):
    def test_a_command_started_in_this_turn_is_named_below_its_shell(self):
        python_process = make_process(5, 4, "python3", 1030.2)
        descendants = [make_process(2, 1, "node", 10.0),  # an MCP server started with the session
                       make_process(3, 1, "caffeinate", 1000.5),  # Claude Code's own helper
                       make_process(4, 1, "zsh", 1030.0), python_process]
        self.assertEqual(choose_command_the_agent_is_waiting_on(descendants, TURN_STARTED_AT, {"caffeinate"}, NOW),
                         (python_process, 1030.0))

    def test_only_helpers_and_older_processes_means_thinking(self):
        descendants = [make_process(2, 1, "node", 10.0), make_process(3, 1, "caffeinate", 1000.5)]
        self.assertIsNone(choose_command_the_agent_is_waiting_on(descendants, TURN_STARTED_AT, {"caffeinate"}, NOW))

    def test_a_shell_with_nothing_running_in_it_is_not_a_command(self):
        # Codex keeps a terminal open between commands.
        descendants = [make_process(4, 1, "bash", 1030.0)]
        self.assertIsNone(choose_command_the_agent_is_waiting_on(descendants, TURN_STARTED_AT, frozenset(), NOW))

    def test_a_hook_that_has_run_for_under_two_seconds_is_not_a_command(self):
        # iTerm2's Claude Code integration runs cc-status (with it2 and osascript) on every tool call.
        descendants = [make_process(6, 1, "cc-status", NOW - 0.4), make_process(7, 6, "osascript", NOW - 0.3)]
        self.assertIsNone(choose_command_the_agent_is_waiting_on(descendants, TURN_STARTED_AT, {"caffeinate"}, NOW))
        self.assertEqual(choose_command_the_agent_is_waiting_on(descendants, TURN_STARTED_AT, {"caffeinate"}, NOW + 2)[0].name,
                         "cc-status")

    def test_helpers_and_everything_below_them_are_not_commands(self):
        # Codex 0.163: its code mode host and an MCP server started around the turn's start.
        pytest_process = make_process(7, 6, "pytest", 1040.1)
        descendants = [make_process(2, 1, "codex-code-mode-host", 1006.9), make_process(3, 2, "node", 1007.0),
                       make_process(4, 1, "npm", 999.7), make_process(5, 4, "node", 999.8)]
        self.assertIsNone(choose_command_the_agent_is_waiting_on(
            descendants, TURN_STARTED_AT, {"codex-code-mode-host"}, NOW, helper_process_ids={4, 5}))
        self.assertEqual(choose_command_the_agent_is_waiting_on(
            descendants + [make_process(6, 1, "zsh", 1040.0), pytest_process],
            TURN_STARTED_AT, {"codex-code-mode-host"}, NOW, helper_process_ids={4, 5}), (pytest_process, 1040.0))


class ProcessTreeReadingTests(unittest.TestCase):
    def test_this_test_process_is_below_its_parent(self):
        own_process = read_process_info(os.getpid())
        self.assertEqual(own_process.parent_process_id, os.getppid())
        self.assertLess(own_process.started_at, time.time() + 1)
        self.assertIn(os.getpid(), [process.process_id for process in list_descendant_processes(os.getppid())])

    def test_a_process_that_is_gone(self):
        self.assertIsNone(read_process_info(999999))
        self.assertEqual(read_name_the_process_was_started_as(999999), "")

    def test_a_program_started_through_a_symbolic_link_is_named_as_typed(self):
        # As ~/.local/bin/claude links to an executable named after its version.
        with tempfile.TemporaryDirectory() as folder:
            link_path = os.path.join(folder, "my-tool")
            os.symlink("/bin/sleep", link_path)
            process = subprocess.Popen([link_path, "30"])
            try:
                self.assertEqual(read_process_info(process.pid).name, "sleep")
                self.assertEqual(read_name_the_process_was_started_as(process.pid), "my-tool")
            finally:
                process.kill()
                process.wait()


class DuplicateLaunchCommandTests(unittest.TestCase):
    def test_claude_code_forks_the_conversation(self):
        self.assertEqual(ClaudeCodeAdapter().build_launch_command_for_a_copy("/Users/me/.local/bin/claude", "abc-123"),
                         "/Users/me/.local/bin/claude --resume abc-123 --fork-session")

    def test_claude_code_copy_uses_the_originals_config_folder_when_it_isnt_the_default(self):
        self.assertEqual(ClaudeCodeAdapter().build_launch_command_for_a_copy(
            "yc", "abc-123", {"sessionId": "abc-123", "config_folder": "/Users/me/.claude_work"}),
            "CLAUDE_CONFIG_DIR=/Users/me/.claude_work yc --resume abc-123 --fork-session")
        self.assertEqual(ClaudeCodeAdapter().build_launch_command_for_a_copy("yc", "abc-123", {"config_folder": None}),
                         "yc --resume abc-123 --fork-session")

    def test_codex_forks_the_conversation(self):
        self.assertEqual(CodexAdapter().build_launch_command_for_a_copy("codex", "abc-123"), "codex fork abc-123")

    def test_without_a_conversation_id_or_for_other_tools_the_copy_starts_fresh(self):
        self.assertEqual(ClaudeCodeAdapter().build_launch_command_for_a_copy("claude", None), "claude")
        self.assertEqual(GenericAdapter().build_launch_command_for_a_copy("aider", "abc"), "aider")

    def test_codex_conversation_file_names(self):
        path = "/Users/me/.codex/sessions/2026/10/18/rollout-2026-10-18T15-04-05-0199e1b2-7c3d-7a10-9f00-1234567890ab.jsonl"
        self.assertEqual(CONVERSATION_FILE_PATTERN.search(path).group(1), "0199e1b2-7c3d-7a10-9f00-1234567890ab")
        self.assertIsNone(CONVERSATION_FILE_PATTERN.search("/Users/me/.codex/config.toml"))


if __name__ == "__main__":
    unittest.main()
