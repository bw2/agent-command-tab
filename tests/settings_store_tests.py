import json
import os
import tempfile
import unittest
import unittest.mock

from agent_command_tab import settings_store
from agent_command_tab.settings_store import (DEFAULT_SETTINGS, ask_the_login_shell_whether_it_knows_command,
                                              build_core_settings, find_codex_command_path,
                                              find_launch_command_problem,
                                              list_included_agent_tool_names, list_left_out_agent_tool_names,
                                              load_settings, merge_settings_with_defaults,
                                              move_the_settings_folder_of_the_old_name, resolve_launch_command_paths,
                                              save_settings)


class MergeSettingsTests(unittest.TestCase):
    def test_valid_values_are_kept_and_invalid_or_unknown_ones_dropped(self):
        settings = merge_settings_with_defaults({
            "show_as": "window", "global_shortcut": "control_space", "sidebar_width": 5000, "sidebar_edge": "left",
            "agent_tools": [{"agent_tool_name": "Aider", "launch_command": " "}], "something_else": 1})
        self.assertEqual(settings["show_as"], "window")
        self.assertEqual(settings["sidebar_edge"], "left")
        self.assertEqual(settings["global_shortcut"], DEFAULT_SETTINGS["global_shortcut"])
        self.assertEqual(settings["sidebar_width"], DEFAULT_SETTINGS["sidebar_width"])
        self.assertEqual(settings["agent_tools"], DEFAULT_SETTINGS["agent_tools"])
        self.assertNotIn("something_else", settings)

    def test_appearance_is_auto_unless_a_known_choice_is_stored(self):
        self.assertEqual(merge_settings_with_defaults({})["appearance"], "auto")  # also for files from before the setting
        self.assertEqual(merge_settings_with_defaults({"appearance": "dark"})["appearance"], "dark")
        self.assertEqual(merge_settings_with_defaults({"appearance": "sepia"})["appearance"], "auto")

    def test_actions_that_run_without_asking_keep_only_known_actions(self):
        self.assertEqual(merge_settings_with_defaults({})["actions_that_run_without_asking"], [])
        self.assertEqual(merge_settings_with_defaults({"actions_that_run_without_asking": ["kill", "clear"]})
                         ["actions_that_run_without_asking"], ["kill", "clear"])
        self.assertEqual(merge_settings_with_defaults({"actions_that_run_without_asking": ["kill", "reboot"]})
                         ["actions_that_run_without_asking"], [])

    def test_list_sort_order_and_shown_columns(self):
        settings = merge_settings_with_defaults({"list_sort_order": "created_time", "shown_columns": ["state", "created_time"]})
        self.assertEqual((settings["list_sort_order"], settings["shown_columns"]), ("created_time", ["state", "created_time"]))
        settings = merge_settings_with_defaults({"list_sort_order": "random", "shown_columns": ["state", "favorite_color"]})
        self.assertEqual((settings["list_sort_order"], settings["shown_columns"]),
                         (DEFAULT_SETTINGS["list_sort_order"], DEFAULT_SETTINGS["shown_columns"]))
        self.assertEqual(merge_settings_with_defaults({"shown_columns": []})["shown_columns"], [])  # every column off

    def test_the_old_sidebar_checkbox_becomes_show_as(self):
        self.assertEqual(merge_settings_with_defaults({"sidebar_is_on": True})["show_as"], "sidebar")
        self.assertEqual(merge_settings_with_defaults({"sidebar_is_on": False})["show_as"], "popup")
        self.assertEqual(merge_settings_with_defaults({"sidebar_is_on": True, "show_as": "window"})["show_as"], "window")
        self.assertNotIn("sidebar_is_on", merge_settings_with_defaults({"sidebar_is_on": True}))

    def test_show_as_and_list_window_frame(self):
        self.assertEqual(merge_settings_with_defaults({"show_as": "tooltip"})["show_as"], "popup")
        self.assertEqual(merge_settings_with_defaults({"list_window_frame": [10, 20, 500, 400]})["list_window_frame"],
                         [10, 20, 500, 400])
        for bad_frame in ([10, 20, 100, 400], [10, 20, 500], "10,20,500,400", [True, 20, 500, 400]):
            self.assertIsNone(merge_settings_with_defaults({"list_window_frame": bad_frame})["list_window_frame"], bad_frame)

    def test_agent_tools_from_before_the_harnesses_section_are_included(self):
        settings = merge_settings_with_defaults({"agent_tools": [{"agent_tool_name": "Claude Code", "launch_command": "yc"},
                                                                 {"agent_tool_name": "Codex", "launch_command": "codex",
                                                                  "is_included": False}]})
        self.assertEqual([agent_tool["is_included"] for agent_tool in settings["agent_tools"]], [True, False])
        self.assertEqual(list_included_agent_tool_names(settings), ["Claude Code"])
        self.assertEqual(list_left_out_agent_tool_names(settings), ["Codex"])

    def test_core_settings_read_memory_and_cpu_only_while_their_columns_are_shown_or_sort_the_list(self):
        settings = merge_settings_with_defaults({"stuck_minutes": 4})  # a setting until 2026-10-26, now dropped
        self.assertNotIn("stuck_minutes", settings)
        self.assertEqual(build_core_settings(settings, {"codex": "/opt/homebrew/bin/codex"}.get),
                         {"left_out_agent_tool_names": [], "resource_usage_is_read": False,
                          "codex_command_path": "/opt/homebrew/bin/codex"})
        settings["shown_columns"] = ["state", "cpu_usage"]
        self.assertTrue(build_core_settings(settings)["resource_usage_is_read"])
        for list_sort_order in ("memory_usage", "cpu_usage"):  # sorted by a column that isn't shown
            settings = merge_settings_with_defaults({"list_sort_order": list_sort_order})
            self.assertTrue(build_core_settings(settings)["resource_usage_is_read"], list_sort_order)

    def test_codex_queue_runs_the_codex_of_the_codex_launch_command(self):
        def codex_command_path_for(launch_command):
            settings = merge_settings_with_defaults({"agent_tools": [
                {"agent_tool_name": "Claude Code", "launch_command": "claude", "is_included": True},
                {"agent_tool_name": "Codex", "launch_command": launch_command, "is_included": True}]})
            return find_codex_command_path(settings, {"codex": "/usr/local/bin/codex"}.get)
        self.assertEqual(codex_command_path_for("/opt/codex/bin/codex -s read-only"), "/opt/codex/bin/codex")
        self.assertEqual(codex_command_path_for("~/.local/bin/codex"), os.path.expanduser("~/.local/bin/codex"))
        self.assertEqual(codex_command_path_for("codex"), "/usr/local/bin/codex")  # a bare name, looked up on PATH
        self.assertEqual(codex_command_path_for("node /opt/codex/codex.js"), "/usr/local/bin/codex")  # a wrapper

    def test_a_boolean_is_not_a_width(self):
        self.assertEqual(merge_settings_with_defaults({"sidebar_width": True})["sidebar_width"], DEFAULT_SETTINGS["sidebar_width"])

    def test_the_settings_folder_of_the_old_name_moves_once(self):
        with tempfile.TemporaryDirectory() as folder:
            old_folder, new_folder = os.path.join(folder, "llm-sidebar-dashboard"), os.path.join(folder, "agent-command-tab")
            os.makedirs(old_folder)
            save_settings(dict(DEFAULT_SETTINGS, sidebar_width=555), os.path.join(old_folder, "settings.json"))
            move_the_settings_folder_of_the_old_name(old_folder, new_folder)
            self.assertFalse(os.path.exists(old_folder))
            self.assertEqual(load_settings(os.path.join(new_folder, "settings.json"))["sidebar_width"], 555)
            os.makedirs(old_folder)  # an old folder appearing again doesn't replace the new one
            move_the_settings_folder_of_the_old_name(old_folder, new_folder)
            self.assertTrue(os.path.exists(os.path.join(new_folder, "settings.json")))

    def test_defaults_are_not_shared(self):
        merge_settings_with_defaults({})["agent_tools"].append({"agent_tool_name": "x", "launch_command": "x"})
        self.assertEqual(len(DEFAULT_SETTINGS["agent_tools"]), 2)


class LoadAndSaveTests(unittest.TestCase):
    def test_round_trip_and_unreadable_file(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "nested", "settings.json")
            self.assertEqual(load_settings(path), DEFAULT_SETTINGS)
            save_settings(dict(DEFAULT_SETTINGS, show_as="sidebar"), path)
            self.assertEqual(load_settings(path)["show_as"], "sidebar")
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

    def test_a_program_off_the_apps_minimal_path_is_found_in_the_usual_folders(self):  # code review, 2026-10-28
        # As for an app opened from Finder, whose PATH is launchd's.
        with tempfile.TemporaryDirectory() as usual_folder, tempfile.TemporaryDirectory() as folder_on_path:
            def write_program(folder, name):
                path = os.path.join(folder, name)
                with open(path, "w") as program_file:
                    program_file.write("#!/bin/sh\n")
                os.chmod(path, 0o755)
                return path
            codex_path = write_program(usual_folder, "codex")
            write_program(usual_folder, "claude")
            claude_on_path = write_program(folder_on_path, "claude")  # a newer one the user put first
            with unittest.mock.patch.dict(os.environ, {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin"}), \
                    unittest.mock.patch.object(settings_store, "USUAL_PROGRAM_FOLDERS", ("/nowhere", usual_folder)):
                self.assertEqual(settings_store.find_program_on_path_or_in_the_usual_folders("codex"), codex_path)
                self.assertIsNone(settings_store.find_program_on_path_or_in_the_usual_folders("not-installed"))
            with unittest.mock.patch.dict(os.environ, {"PATH": f"{folder_on_path}:/usr/bin:/bin"}), \
                    unittest.mock.patch.object(settings_store, "USUAL_PROGRAM_FOLDERS", (usual_folder,)):
                self.assertEqual(settings_store.find_program_on_path_or_in_the_usual_folders("claude"), claude_on_path)  # PATH first


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

    def test_a_name_the_login_shell_did_not_answer_about_in_time_counts_as_fine(self):
        # e.g. an alias in start-up files that take longer to read than the check waits
        self.assertEqual(find_launch_command_problem("yc", lambda name: None, lambda name: None), "")

    def test_a_fish_or_tcsh_login_shell_isnt_asked(self):  # code review, 2026-10-28
        # Their syntax differs from the check's (`-i -l -c`, "$1"), so they couldn't be asked, not "not found".
        for shell in ("/opt/homebrew/bin/fish", "/bin/tcsh"):
            with unittest.mock.patch.dict(os.environ, {"SHELL": shell}), \
                    unittest.mock.patch.object(settings_store.subprocess, "run", side_effect=AssertionError("run")):
                self.assertIsNone(ask_the_login_shell_whether_it_knows_command("claude"), shell)

    def test_environment_settings_are_skipped_and_shell_expansions_are_not_judged(self):
        self.assertEqual(self.check("ANTHROPIC_MODEL=x CLAUDE_CONFIG_DIR=~/.claude_work claude"), "")
        self.assertIn("command not found", self.check("FOO=1 not-installed"))
        self.assertEqual(self.check("$HOME/bin/claude"), "")


if __name__ == "__main__":
    unittest.main()
