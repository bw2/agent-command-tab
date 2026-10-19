import json
import os
import tempfile
import unittest

from llm_sidebar_dashboard.settings_store import (DEFAULT_SETTINGS, find_launch_command_problem, load_settings,
                                                  merge_settings_with_defaults, resolve_launch_command_paths,
                                                  save_settings)


class MergeSettingsTests(unittest.TestCase):
    def test_valid_values_are_kept_and_invalid_or_unknown_ones_dropped(self):
        settings = merge_settings_with_defaults({
            "sidebar_is_on": True, "icon_color": "chartreuse", "sidebar_width": 5000, "sidebar_edge": "left",
            "agent_tools": [{"agent_tool_name": "Aider", "launch_command": " "}], "something_else": 1})
        self.assertTrue(settings["sidebar_is_on"])
        self.assertEqual(settings["sidebar_edge"], "left")
        self.assertEqual(settings["icon_color"], DEFAULT_SETTINGS["icon_color"])
        self.assertEqual(settings["sidebar_width"], DEFAULT_SETTINGS["sidebar_width"])
        self.assertEqual(settings["agent_tools"], DEFAULT_SETTINGS["agent_tools"])
        self.assertNotIn("something_else", settings)

    def test_a_boolean_is_not_a_width(self):
        self.assertEqual(merge_settings_with_defaults({"sidebar_width": True})["sidebar_width"], DEFAULT_SETTINGS["sidebar_width"])

    def test_defaults_are_not_shared(self):
        merge_settings_with_defaults({})["agent_tools"].append({"agent_tool_name": "x", "launch_command": "x"})
        self.assertEqual(len(DEFAULT_SETTINGS["agent_tools"]), 2)


class LoadAndSaveTests(unittest.TestCase):
    def test_round_trip_and_unreadable_file(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "nested", "settings.json")
            self.assertEqual(load_settings(path), DEFAULT_SETTINGS)
            save_settings(dict(DEFAULT_SETTINGS, sidebar_is_on=True), path)
            self.assertTrue(load_settings(path)["sidebar_is_on"])
            with open(path, "w") as settings_file:
                settings_file.write("{not json")
            self.assertEqual(load_settings(path), DEFAULT_SETTINGS)
            self.assertEqual([name for name in os.listdir(os.path.dirname(path)) if name.endswith(".tmp")], [])


class ResolveLaunchCommandPathsTests(unittest.TestCase):
    def test_bare_commands_are_resolved_and_others_kept(self):
        settings = dict(DEFAULT_SETTINGS, agent_tools=[
            {"agent_tool_name": "Claude Code", "launch_command": "claude --model opus"},
            {"agent_tool_name": "Codex", "launch_command": "/opt/codex/bin/codex"},
            {"agent_tool_name": "Missing", "launch_command": "not-installed"},
        ])
        found = {"claude": "/Users/someone/.local/bin/claude"}
        resolved = resolve_launch_command_paths(settings, found.get)
        self.assertEqual([tool["launch_command"] for tool in resolved["agent_tools"]],
                         ["/Users/someone/.local/bin/claude --model opus", "/opt/codex/bin/codex", "not-installed"])
        self.assertEqual(settings["agent_tools"][0]["launch_command"], "claude --model opus")  # input unchanged
        json.dumps(resolved)


class LaunchCommandProblemTests(unittest.TestCase):
    def check(self, launch_command, known_names=("claude",), login_shell_names=("yc",)):
        return find_launch_command_problem(launch_command, lambda name: name in known_names,
                                           lambda name: name in login_shell_names)

    def test_programs_on_path_aliases_and_paths(self):
        self.assertEqual(self.check("claude --model opus"), "")
        self.assertEqual(self.check("yc"), "")  # an alias the login shell knows
        self.assertEqual(self.check("/bin/sh -c true"), "")
        self.assertIn("command not found", self.check("not-installed"))
        self.assertIn("doesn't exist", self.check("/no/such/program"))
        self.assertEqual(self.check("   "), "No command")

    def test_environment_settings_are_skipped_and_shell_expansions_are_not_judged(self):
        self.assertEqual(self.check("ANTHROPIC_MODEL=x CLAUDE_CONFIG_DIR=~/.claude_work claude"), "")
        self.assertIn("command not found", self.check("FOO=1 not-installed"))
        self.assertEqual(self.check("$HOME/bin/claude"), "")


if __name__ == "__main__":
    unittest.main()
