"""Installing, checking and removing the app's hooks in Claude Code's and Codex's settings (hooks_installer)."""

import json
import os
import stat
import sys
import tempfile
import unittest
import unittest.mock

from agent_command_tab import hooks_installer
from agent_command_tab.hooks_installer import (BACKUP_FILE_SUFFIX, HOOK_EVENTS_BY_AGENT_TOOL_NAME, HOOKS_ARE_INSTALLED,
                                               HOOKS_ARE_MISSING, HOOKS_AWAIT_APPROVAL, add_the_apps_hooks,
                                               build_forwarder_command, check_hooks, codex_has_approved_the_hooks,
                                               convert_event_name_to_codex_hook_state_name, install_hooks,
                                               list_positions_of_the_apps_hooks, remove_hooks, remove_the_apps_hooks)

COMMAND = build_forwarder_command("Claude Code", "/venv/bin/python", "/code/hook_event_forwarder.py")
EVENTS = HOOK_EVENTS_BY_AGENT_TOOL_NAME["Claude Code"]
# The user's own settings, as in ~/.claude/settings.json on this Mac (trimmed).
USERS_SETTINGS = {
    "model": "opus",
    "hooks": {
        "PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "rtk hook claude"}]}],
        "Stop": [{"hooks": [{"type": "command", "command": "/Users/someone/.config/iterm2/cc-status"}]}],
    },
}


class BuildForwarderCommandTests(unittest.TestCase):
    def test_paths_with_spaces_are_quoted(self):
        self.assertEqual(build_forwarder_command("Claude Code", "/My Apps/python", "/code/hook_event_forwarder.py"),
                         "'/My Apps/python' -I -S /code/hook_event_forwarder.py 'Claude Code'")


class SettingsTests(unittest.TestCase):
    def test_the_apps_hooks_come_after_the_users_own_and_replace_older_ones(self):
        settings = add_the_apps_hooks(USERS_SETTINGS, COMMAND, EVENTS)
        self.assertEqual(settings["model"], "opus")
        self.assertEqual(settings["hooks"]["Stop"][0], USERS_SETTINGS["hooks"]["Stop"][0])
        self.assertEqual(settings["hooks"]["Stop"][1],
                         {"hooks": [{"type": "command", "command": COMMAND, "async": True, "timeout": 10}]})
        self.assertEqual(settings["hooks"]["PostToolUse"][0]["matcher"], "Bash|Agent|Task")
        self.assertEqual(add_the_apps_hooks(settings, COMMAND, EVENTS), settings)  # installing again changes nothing
        moved_command = build_forwarder_command("Claude Code", "/other/python", "/code/hook_event_forwarder.py")
        moved_settings = add_the_apps_hooks(settings, moved_command, EVENTS)
        self.assertEqual([hook_group["hooks"][0]["command"] for hook_group in moved_settings["hooks"]["Stop"]],
                         ["/Users/someone/.config/iterm2/cc-status", moved_command])

    def test_removing_leaves_exactly_the_users_settings(self):
        self.assertEqual(remove_the_apps_hooks(add_the_apps_hooks(USERS_SETTINGS, COMMAND, EVENTS)), USERS_SETTINGS)
        self.assertEqual(remove_the_apps_hooks(add_the_apps_hooks({"model": "opus"}, COMMAND, EVENTS)), {"model": "opus"})
        self.assertEqual(remove_the_apps_hooks({"hooks": {"Stop": []}}), {"hooks": {"Stop": []}})  # the user's own empty list

    def test_positions_need_every_event_with_the_same_command(self):
        settings = add_the_apps_hooks(USERS_SETTINGS, COMMAND, EVENTS)
        positions = list_positions_of_the_apps_hooks(settings, COMMAND, EVENTS)
        self.assertEqual(dict((event_name, (group_index, hook_index)) for event_name, group_index, hook_index in positions),
                         {"SessionStart": (0, 0), "UserPromptSubmit": (0, 0), "Stop": (1, 0), "SubagentStop": (0, 0),
                          "SessionEnd": (0, 0), "PostToolUse": (0, 0)})
        self.assertIsNone(list_positions_of_the_apps_hooks(USERS_SETTINGS, COMMAND, EVENTS))
        self.assertIsNone(list_positions_of_the_apps_hooks(settings, "/other/python -I -S x 'Claude Code'", EVENTS))

    def test_another_link_to_the_same_python_is_the_same_command(self):
        # The app's entry point runs as .venv/bin/python3.11, `python -m ...` as .venv/bin/python, a link to it.
        folder = tempfile.mkdtemp()
        python_path, link_path = os.path.join(folder, "python3.11"), os.path.join(folder, "python")
        open(python_path, "w").close()
        os.symlink("python3.11", link_path)
        settings = add_the_apps_hooks(USERS_SETTINGS, build_forwarder_command("Claude Code", python_path, "/f.py"), EVENTS)
        self.assertIsNotNone(list_positions_of_the_apps_hooks(settings, build_forwarder_command("Claude Code", link_path, "/f.py"), EVENTS))
        self.assertIsNone(list_positions_of_the_apps_hooks(settings, build_forwarder_command("Codex", link_path, "/f.py"), EVENTS))

    def test_codex_approvals_are_found_by_position(self):
        self.assertEqual(convert_event_name_to_codex_hook_state_name("SessionEnd"), "session_end")
        positions = [("SessionEnd", 0, 0), ("Stop", 1, 0)]
        config_text = ('[hooks.state."/h/hooks.json:session_end:0:0"]\ntrusted_hash = "sha256:1"\n'
                       '[hooks.state."/h/hooks.json:stop:1:0"]\ntrusted_hash = "sha256:2"\n')
        self.assertTrue(codex_has_approved_the_hooks(config_text, "/h/hooks.json", positions))
        self.assertFalse(codex_has_approved_the_hooks(config_text, "/h/hooks.json", positions + [("SessionStart", 0, 0)]))


class FilesTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp()
        self.claude_settings_path = os.path.join(self.folder, "settings.json")
        with open(self.claude_settings_path, "w") as settings_file:
            json.dump(USERS_SETTINGS, settings_file)
        os.chmod(self.claude_settings_path, 0o600)
        self.codex_home_folder = os.path.join(self.folder, "codex")
        os.mkdir(self.codex_home_folder)
        self.codex_hooks_path = os.path.join(self.codex_home_folder, "hooks.json")
        for patch in (unittest.mock.patch.object(hooks_installer, "list_settings_file_paths", lambda agent_tool_name: {
                          "Claude Code": [self.claude_settings_path], "Codex": [self.codex_hooks_path]}[agent_tool_name]),
                      unittest.mock.patch.object(hooks_installer, "find_codex_home_folder", lambda: self.codex_home_folder)):
            patch.start()
            self.addCleanup(patch.stop)

    def read_json(self, file_path):
        with open(file_path) as json_file:
            return json.load(json_file)

    def test_install_check_and_remove(self):
        self.assertEqual(check_hooks("Claude Code"), HOOKS_ARE_MISSING)
        self.assertEqual(install_hooks("Claude Code"), [self.claude_settings_path])
        self.assertEqual(check_hooks("Claude Code"), HOOKS_ARE_INSTALLED)
        self.assertEqual(self.read_json(self.claude_settings_path + BACKUP_FILE_SUFFIX), USERS_SETTINGS)  # the file before
        self.assertEqual(stat.S_IMODE(os.stat(self.claude_settings_path).st_mode), 0o600)  # its permissions kept
        self.assertEqual(install_hooks("Claude Code"), [])  # nothing left to change
        self.assertEqual(remove_hooks("Claude Code"), [self.claude_settings_path])
        self.assertEqual(self.read_json(self.claude_settings_path), USERS_SETTINGS)
        self.assertEqual(check_hooks("Claude Code"), HOOKS_ARE_MISSING)

    def test_codex_hooks_need_its_approval(self):
        self.assertEqual(install_hooks("Codex"), [self.codex_hooks_path])  # a new hooks.json
        self.assertEqual(check_hooks("Codex"), HOOKS_AWAIT_APPROVAL)
        positions = list_positions_of_the_apps_hooks(self.read_json(self.codex_hooks_path), build_forwarder_command("Codex"),
                                                     HOOK_EVENTS_BY_AGENT_TOOL_NAME["Codex"])
        with open(os.path.join(self.codex_home_folder, "config.toml"), "w") as config_file:  # as Codex's /hooks screen writes it
            for event_name, group_index, hook_index in positions:
                config_file.write(f'[hooks.state."{self.codex_hooks_path}:{convert_event_name_to_codex_hook_state_name(event_name)}:'
                                  f'{group_index}:{hook_index}"]\ntrusted_hash = "sha256:0"\n\n')
        self.assertEqual(check_hooks("Codex"), HOOKS_ARE_INSTALLED)
        # Installing again, from another link to the same Python, leaves the approved file alone.
        with unittest.mock.patch.object(hooks_installer.sys, "executable", os.path.realpath(sys.executable)):
            self.assertEqual(install_hooks("Codex"), [])
        self.assertEqual(check_hooks("Codex"), HOOKS_ARE_INSTALLED)

    def test_a_symlinked_settings_file_stays_a_link_to_the_file_that_gets_the_hooks(self):
        linked_file_path = os.path.join(self.folder, "dotfiles_settings.json")
        os.rename(self.claude_settings_path, linked_file_path)
        os.symlink(linked_file_path, self.claude_settings_path)  # as a dotfiles manager keeps it
        install_hooks("Claude Code")
        self.assertTrue(os.path.islink(self.claude_settings_path))
        self.assertIn("hooks", self.read_json(linked_file_path))
        self.assertEqual(check_hooks("Claude Code"), HOOKS_ARE_INSTALLED)

    def test_a_file_that_isnt_a_json_object_is_left_alone(self):
        with open(self.claude_settings_path, "w") as settings_file:
            settings_file.write('{"model": "opus",')  # cut short
        with self.assertRaises(ValueError):
            install_hooks("Claude Code")
        with open(self.claude_settings_path) as settings_file:
            self.assertEqual(settings_file.read(), '{"model": "opus",')
        self.assertEqual(check_hooks("Claude Code"), HOOKS_ARE_MISSING)


if __name__ == "__main__":
    unittest.main()
