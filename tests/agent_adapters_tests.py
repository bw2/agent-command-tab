import unittest

from llm_sidebar_dashboard.agent_adapters import build_built_in_agent_tool_adapters, find_agent_tool_adapter_for_tab
from llm_sidebar_dashboard.agent_adapters.base import command_line_runs_a_shell, split_command_line_into_program_names


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
