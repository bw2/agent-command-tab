import os
import shlex
import signal
import subprocess
import sys
import tempfile
import time
import unittest

from agent_command_tab.agent_adapters.claude_code import ClaudeCodeAdapter
from agent_command_tab.agent_adapters.codex import CONVERSATION_FILE_PATTERN, CodexAdapter
from agent_command_tab.agent_adapters.generic import GenericAdapter
from agent_command_tab.agent_process_tree import (ProcessInfo, choose_command_the_agent_is_waiting_on, compute_cpu_percent,
                                                  list_descendant_processes, read_name_the_process_was_started_as,
                                                  read_process_info, read_process_resource_usage,
                                                  read_process_tree_resource_usage)

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


class ResourceUsageTests(unittest.TestCase):
    def test_a_process_tree_counts_its_running_descendants_memory_and_cpu_time(self):
        child = subprocess.Popen(["/bin/sleep", "5"])
        try:
            own_memory, own_cpu_seconds = read_process_resource_usage(os.getpid())
            tree_memory, tree_cpu_seconds = read_process_tree_resource_usage(os.getpid())
            self.assertGreater(own_memory, 1024 ** 2)
            self.assertGreater(tree_memory, own_memory)  # the sleeping child counts too
            self.assertGreaterEqual(tree_cpu_seconds, own_cpu_seconds)
        finally:
            child.kill()
            child.wait()
        self.assertIsNone(read_process_tree_resource_usage(child.pid))  # gone

    def test_resource_totals_reach_a_command_more_than_six_levels_down(self):
        command = "/bin/sleep 5"
        for _ in range(8):  # each shell waits on a background child, so none of them is replaced by its command
            command = f"/bin/sh -c {shlex.quote(command + ' & wait')}"
        chain = subprocess.Popen(command, shell=True, start_new_session=True)
        try:
            deadline = time.time() + 5
            while time.time() < deadline and "sleep" not in [
                    process.name for process in list_descendant_processes(os.getpid(), maximum_depth=None)]:
                time.sleep(0.05)
            deepest_command = next(process for process in list_descendant_processes(os.getpid(), maximum_depth=None)
                                   if process.name == "sleep")
            self.assertNotIn(deepest_command.process_id,
                             [process.process_id for process in list_descendant_processes(os.getpid())])  # below six levels
            sleep_memory, _ = read_process_resource_usage(deepest_command.process_id)
            usages_within_six_levels = [read_process_resource_usage(process_id) for process_id in
                                        [os.getpid()] + [process.process_id for process in list_descendant_processes(os.getpid())]]
            memory_within_six_levels = sum(usage[0] for usage in usages_within_six_levels if usage is not None)
            tree_memory, _ = read_process_tree_resource_usage(os.getpid())
            # The shells below six levels and the sleep at the bottom are counted too (the six-level limit left them out).
            self.assertGreaterEqual(tree_memory - memory_within_six_levels, sleep_memory)
        finally:
            os.killpg(chain.pid, signal.SIGKILL)
            chain.wait()

    def test_a_finished_childs_cpu_time_stays_in_the_tree(self):
        _, cpu_seconds_before = read_process_tree_resource_usage(os.getpid())
        busy_loop = "import time\nend = time.process_time() + 0.3\nwhile time.process_time() < end: pass"
        subprocess.run([sys.executable, "-c", busy_loop], check=True)
        _, cpu_seconds_after = read_process_tree_resource_usage(os.getpid())
        self.assertGreaterEqual(cpu_seconds_after - cpu_seconds_before, 0.25)

    def test_cpu_percent_between_two_readings(self):
        self.assertIsNone(compute_cpu_percent(None, (5.0, 100.0)))  # the first reading
        self.assertEqual(compute_cpu_percent((5.0, 100.0), (6.5, 103.0)), 50)
        self.assertEqual(compute_cpu_percent((5.0, 100.0), (4.0, 103.0)), 0)  # a descendant that ended took its time along


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
