import asyncio
import os
import tempfile
import unittest
import unittest.mock

from agent_command_tab.agent_adapters import codex
from agent_command_tab.agent_adapters import (build_built_in_agent_tool_adapters, find_agent_tool_adapter_for_tab,
                                              list_agent_tool_names_whose_conversations_can_be_resumed)
from agent_command_tab.agent_adapters.base import (command_line_is_a_shell_with_no_script_or_command, command_line_runs_a_shell,
                                                   split_command_line_into_program_names)
from agent_command_tab.agent_adapters.claude_code import ClaudeCodeAdapter
from agent_command_tab.agent_adapters.codex import CONVERSATION_FILE_PATTERN, CodexAdapter
from agent_command_tab.agent_adapters.generic import GenericAdapter
from agent_command_tab.agent_process_tree import ProcessInfo


class ResumeCommandTests(unittest.TestCase):  # Save session and Restore sessions
    def test_claude_code_resumes_in_the_conversations_config_folder(self):
        adapter = ClaudeCodeAdapter()
        self.assertEqual(adapter.build_launch_command_to_resume("yc", "abc-123"), "yc --resume abc-123")
        self.assertEqual(adapter.build_launch_command_to_resume("CLAUDE_CONFIG_DIR=~/.claude_work yc", "abc-123"),
                         "yc --resume abc-123")  # a conversation of the default folder
        self.assertEqual(adapter.build_launch_command_to_resume("yc", "abc 123", "/Users/me/.claude_work"),
                         "CLAUDE_CONFIG_DIR=/Users/me/.claude_work yc --resume 'abc 123'")

    def test_codex_resumes_with_its_subcommand(self):
        self.assertEqual(CodexAdapter().build_launch_command_to_resume("/opt/homebrew/bin/codex", "0199-ab"),
                         "/opt/homebrew/bin/codex resume 0199-ab")

    def test_codex_resumes_and_forks_with_the_conversations_codex_home(self):  # Codex finds the conversation only there
        self.assertEqual(CodexAdapter().build_launch_command_to_resume("codex", "0199-ab", None, "/Users/me/Codex Work"),
                         "CODEX_HOME='/Users/me/Codex Work' codex resume 0199-ab")
        self.assertEqual(CodexAdapter().build_launch_command_for_a_copy("codex", "0199-ab", None, "/Users/me/.codex-work"),
                         "CODEX_HOME=/Users/me/.codex-work codex fork 0199-ab")
        # In place of one the launch command sets, which would come later and win.
        self.assertEqual(CodexAdapter().build_launch_command_to_resume("RUST_LOG=info CODEX_HOME=~/.codex-work codex", "0199-ab",
                                                                       None, "/Users/me/.codex"),
                         "CODEX_HOME=/Users/me/.codex RUST_LOG=info codex resume 0199-ab")
        self.assertEqual(CodexAdapter().build_launch_command_to_resume("CODEX_HOME=~/.codex-work codex", "0199-ab"),
                         "CODEX_HOME=~/.codex-work codex resume 0199-ab")  # not known (saved before it was kept)

    def test_only_claude_code_and_codex_can_resume(self):
        self.assertEqual(list_agent_tool_names_whose_conversations_can_be_resumed(), {"Claude Code", "Codex"})


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


class CodexConversationLookupTests(unittest.TestCase):
    def test_codex_conversation_file_names(self):
        path = "/Users/me/.codex/sessions/2026/10/18/rollout-2026-10-18T15-04-05-0199e1b2-7c3d-7a10-9f00-1234567890ab.jsonl"
        self.assertEqual(CONVERSATION_FILE_PATTERN.search(path).group(1), "0199e1b2-7c3d-7a10-9f00-1234567890ab")
        self.assertIsNone(CONVERSATION_FILE_PATTERN.search("/Users/me/.codex/config.toml"))

    def test_a_codex_conversation_is_looked_for_in_the_codex_home_of_the_agents_own_environment(self):
        # Its launch command may set another CODEX_HOME than the app's ("CODEX_HOME=/x codex").
        sessions_folders = []

        def find_conversation_files(sessions_folder, *arguments, **keyword_arguments):
            sessions_folders.append(sessions_folder)
            return []
        for arguments_and_environment, expected_sessions_folder in (
                ((["codex"], {"CODEX_HOME": "/x"}), "/x/sessions"),
                ((["codex"], {}), os.path.expanduser("~/.codex/sessions")),  # the agent has none: Codex's default
                (([], {}), "/app-codex-home/sessions")):  # its environment can't be read: the app's own
            with unittest.mock.patch.object(codex, "read_process_info", return_value=ProcessInfo(301, 1, "codex", 10.0)), \
                    unittest.mock.patch.object(codex, "read_process_arguments_and_environment", return_value=arguments_and_environment), \
                    unittest.mock.patch.object(codex, "read_process_current_folder", return_value="/p"), \
                    unittest.mock.patch.object(codex, "find_codex_conversation_files", find_conversation_files), \
                    unittest.mock.patch.dict(os.environ, {"CODEX_HOME": "/app-codex-home"}):
                CodexAdapter().find_conversation_files(301)
            self.assertEqual(sessions_folders[-1], expected_sessions_folder, arguments_and_environment)


class ClaudeCodeTranscriptLookupTests(unittest.TestCase):
    def test_the_records_own_config_folder_is_searched_even_if_no_list_has_it(self):
        with tempfile.TemporaryDirectory() as folder:
            new_config_folder = os.path.join(folder, ".claude_new")  # created after the app made its list of folders
            transcript_path = os.path.join(new_config_folder, "projects", "-Users-me-code", "abc-123.jsonl")
            os.makedirs(os.path.dirname(transcript_path))
            open(transcript_path, "w").close()
            record = {"sessionId": "abc-123", "config_folder": new_config_folder}
            self.assertEqual(asyncio.run(ClaudeCodeAdapter().find_conversation_file(4242, record, [])), transcript_path)


class SplitCommandLineTests(unittest.TestCase):
    def test_login_shell_dash_is_removed(self):
        self.assertEqual(split_command_line_into_program_names("-bash --posix"), ["bash", "posix"])

    def test_full_paths_become_base_names(self):
        self.assertEqual(split_command_line_into_program_names("/opt/homebrew/bin/node /opt/homebrew/bin/codex -a never")[:2],
                         ["node", "codex"])

    def test_unbalanced_quotes_fall_back_to_spaces(self):
        self.assertEqual(split_command_line_into_program_names("claude 'unbalanced")[0], "claude")

    def test_empty_and_none(self):
        self.assertEqual(split_command_line_into_program_names(""), [])
        self.assertEqual(split_command_line_into_program_names(None), [])

    def test_shell_detection(self):
        self.assertTrue(command_line_runs_a_shell("-zsh"))
        self.assertTrue(command_line_runs_a_shell("/bin/bash -l"))
        self.assertFalse(command_line_runs_a_shell("vim notes.txt"))

    def test_a_shell_with_no_script_or_command_is_told_apart_from_one_running_something(self):
        for command_line in ("-zsh", "/bin/bash", "/bin/zsh -l", "bash --login", "zsh -il", "fish --interactive"):
            with self.subTest(command_line=command_line):
                self.assertTrue(command_line_is_a_shell_with_no_script_or_command(command_line))
        for command_line in ("/bin/bash ./job.sh", "zsh -c 'make all'", "bash -lc 'sleep 60'", "bash -l job.sh",
                             "sh -s", "bash -o vi", "vim notes.txt", "", None):
            with self.subTest(command_line=command_line):
                self.assertFalse(command_line_is_a_shell_with_no_script_or_command(command_line))


class FindAgentToolAdapterTests(unittest.TestCase):
    def setUp(self):
        self.specific_adapters, self.generic_adapter = build_built_in_agent_tool_adapters()

    def find_tool_name(self, command_line, status=None):
        adapter = find_agent_tool_adapter_for_tab(command_line, status, self.specific_adapters, self.generic_adapter)
        return adapter.name_agent_tool_for_command_line(command_line) if adapter else None

    def test_claude_code(self):
        self.assertEqual(self.find_tool_name("claude --dangerously-skip-permissions"), "Claude Code")
        self.assertEqual(self.find_tool_name("/Users/someone/.local/bin/claude"), "Claude Code")

    def test_codex_both_forms(self):
        self.assertEqual(self.find_tool_name("codex --dangerously-bypass-hook-trust -a on-request"), "Codex")
        self.assertEqual(self.find_tool_name("node /opt/homebrew/bin/codex -c 'model=\"o3\"'"), "Codex")

    def test_node_running_something_else_is_not_codex(self):
        self.assertIsNone(self.find_tool_name("node server.js"))

    def test_shell_is_never_an_agent_even_with_a_status(self):
        self.assertIsNone(self.find_tool_name("-bash --posix", status="working"))

    def test_unknown_tool_needs_a_recognized_status(self):
        self.assertIsNone(self.find_tool_name("aider --model x"))
        self.assertIsNone(self.find_tool_name("aider --model x", status="Compiling"))
        self.assertEqual(self.find_tool_name("aider --model x", status="Working"), "aider")


if __name__ == "__main__":
    unittest.main()
