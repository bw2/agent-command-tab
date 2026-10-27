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
from agent_command_tab.agent_process_tree import (ProcessInfo, an_agent_program_runs_through_a_shell_below,
                                                  build_command_text, child_process_is_a_background_shell,
                                                  choose_command_the_agent_is_waiting_on, choose_helper_process_ids,
                                                  compute_cpu_percent, list_child_processes_of_the_agent,
                                                  list_descendant_processes, read_child_processes,
                                                  read_name_the_process_was_started_as,
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


class ChildProcessTests(unittest.TestCase):
    """The sub rows of an open tab row and the background shells (user request, 2026-10-27)."""

    def test_claude_codes_children_are_named_by_the_command_run_in_each_shell(self):
        # As seen below this Mac's Claude Code on 2026-10-27: its caffeinate, a `bash -c` running a
        # background python3, and one running the command it waits on.
        caffeinate = make_process(2, 1, "caffeinate", 900.0)
        background_shell, background_python = make_process(3, 1, "bash", 1001.0), make_process(4, 3, "python3", 1001.1)
        foreground_shell, foreground_python = make_process(5, 1, "bash", 1050.0), make_process(6, 5, "python3", 1050.1)
        hook_shell, hook = make_process(7, 1, "sh", 1099.5), make_process(8, 7, "cc-status", 1099.6)  # a hook, just started
        descendants = [foreground_python, foreground_shell, background_python, background_shell, caffeinate, hook_shell, hook]
        children = list_child_processes_of_the_agent(1, descendants, NOW)
        self.assertEqual(children, [(caffeinate, caffeinate), (background_shell, background_python),
                                    (foreground_shell, foreground_python)])  # oldest first, without the hook
        # Between turns, every command left running is in the background (caffeinate is a helper, not a shell).
        self.assertEqual([child_process_is_a_background_shell(child, shown, False, None) for child, shown in children],
                         [False, True, True])
        # In a turn begun at 1040, only the command from before it is known to be: one started in
        # the turn may be the command the agent waits on, whichever came first.
        self.assertEqual([child_process_is_a_background_shell(child, shown, True, 1040.0) for child, shown in children],
                         [False, True, False])
        self.assertEqual([child_process_is_a_background_shell(child, shown, True, 1000.0) for child, shown in children],
                         [False, False, False])  # both started in the turn: neither counts until it ends
        self.assertEqual([child_process_is_a_background_shell(child, shown, True, None) for child, shown in children],
                         [False, False, False])  # when the turn began isn't known yet

    def test_codexs_own_program_below_its_node_launcher_counts_as_the_agent(self):
        codex = make_process(2, 1, "codex", 100.0)
        kept_terminal = make_process(3, 2, "zsh", 200.0)  # open between commands, with nothing running
        wrapper, build = make_process(4, 2, "zsh", 300.0), make_process(6, 5, "cargo", 300.2)
        inner_shell = make_process(5, 4, "bash", 300.1)
        descendants = [codex, kept_terminal, wrapper, inner_shell, build]
        children = list_child_processes_of_the_agent(1, descendants, NOW)
        self.assertEqual(children, [(kept_terminal, kept_terminal), (wrapper, build)])  # shells inside shells are looked through
        self.assertEqual([child_process_is_a_background_shell(child, shown, False, None) for child, shown in children],
                         [False, True])

    def test_a_command_in_a_shell_kept_from_an_earlier_turn_goes_by_its_own_start(self):
        # Codex runs a later turn's command in a shell started in an earlier one.
        codex, kept_shell, python = make_process(2, 1, "codex", 10.0), make_process(7, 2, "bash", 20.0), make_process(8, 7, "python3", 1030.0)
        ((child, shown),) = list_child_processes_of_the_agent(1, [codex, kept_shell, python], NOW)
        self.assertIs(shown, python)
        self.assertFalse(child_process_is_a_background_shell(child, shown, True, TURN_STARTED_AT))  # started in this turn
        self.assertTrue(child_process_is_a_background_shell(child, shown, True, 1100.0))  # from before the turn

    def test_a_command_is_named_by_its_program_and_arguments(self):
        self.assertEqual(build_command_text(["/opt/homebrew/bin/python3", "-m", "unittest"], "Python"), "python3 -m unittest")
        self.assertEqual(build_command_text(["bash", "-c", "echo a\necho b"], "bash"), "bash -c echo a echo b")
        self.assertEqual(build_command_text([], "caffeinate"), "caffeinate")

    def test_a_real_child_of_this_process(self):
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        try:
            until = time.time() + 10
            child_processes = []
            while time.time() < until and not any(entry["child"].process_id == child.pid for entry in child_processes):
                child_processes = read_child_processes(os.getpid(), False, None, True, time.time() + 60)  # (it counts once it has run 2 s)
            entry = next(entry for entry in child_processes if entry["child"].process_id == child.pid)
            self.assertIn("import time; time.sleep(30)", entry["command_text"])
            self.assertEqual(entry["command_started_at"], entry["child"].started_at)  # not a shell: the command itself
            self.assertFalse(entry["is_background_shell"])  # not a shell
            self.assertGreater(entry["resource_usage"][0], 0)  # its memory
        finally:
            child.kill()
            child.wait()


class HelperProcessTests(unittest.TestCase):
    def test_commands_below_codex_and_its_open_terminal_still_count_after_an_idle_recording(self):
        # Agent 1 is Codex's node launcher, with its own program (4), an MCP server (9) and an open terminal (7).
        between_turns = [make_process(4, 1, "codex", 10.0), make_process(9, 4, "node", 10.5), make_process(7, 4, "bash", 20.0)]
        helper_process_ids = choose_helper_process_ids(between_turns)
        self.assertEqual(helper_process_ids, frozenset({9}))
        python_process = make_process(8, 7, "python3", 1030.0)
        self.assertEqual(choose_command_the_agent_is_waiting_on(between_turns + [python_process], TURN_STARTED_AT,
                                                                {"codex-code-mode-host"}, NOW, helper_process_ids),
                         (python_process, 1030.0))


class AgentProgramInsideAnAgentTests(unittest.TestCase):
    def test_an_agent_program_counts_only_when_a_shell_started_it(self):
        started_as_names = {3: "claude", 5: "node", 6: "codex", 8: "python3"}
        # A `claude -p` run by Claude Code's shell tool (agent 1, its shell 2).
        self.assertTrue(an_agent_program_runs_through_a_shell_below(
            [make_process(2, 1, "zsh", 10.0), make_process(3, 2, "2.1.310", 11.0)], started_as_names.get))
        # A `codex exec` run by Codex's shell tool, below its node launcher (agent 1 is Codex's node launcher).
        self.assertTrue(an_agent_program_runs_through_a_shell_below(
            [make_process(4, 1, "codex", 10.0), make_process(7, 4, "bash", 11.0), make_process(5, 7, "node", 11.5),
             make_process(6, 5, "codex", 11.6)], started_as_names.get))
        # Codex's own program below its node launcher, and a command that isn't an agent.
        self.assertFalse(an_agent_program_runs_through_a_shell_below(
            [make_process(6, 1, "codex", 10.0), make_process(7, 6, "bash", 11.0), make_process(8, 7, "python3", 11.5)],
            started_as_names.get))


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

    def test_a_config_folder_set_in_the_launch_command_gives_way_to_the_originals(self):
        launch_command = "ANTHROPIC_MODEL=x CLAUDE_CONFIG_DIR=~/.claude_work claude"  # the shell applies the last setting
        self.assertEqual(ClaudeCodeAdapter().build_launch_command_for_a_copy(
            launch_command, "abc-123", {"config_folder": "/Users/me/.claude_personal"}),
            "CLAUDE_CONFIG_DIR=/Users/me/.claude_personal ANTHROPIC_MODEL=x claude --resume abc-123 --fork-session")
        self.assertEqual(ClaudeCodeAdapter().build_launch_command_for_a_copy(launch_command, "abc-123", {"config_folder": None}),
                         "ANTHROPIC_MODEL=x claude --resume abc-123 --fork-session")
        self.assertEqual(ClaudeCodeAdapter().build_launch_command_for_a_copy(
            "CLAUDE_CONFIG_DIR=\"$HOME/my claude\" claude --verbose", "abc-123", {"config_folder": None}),
            "claude --verbose --resume abc-123 --fork-session")

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
