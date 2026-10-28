"""Installing, checking and removing the app's hooks in Claude Code's and Codex's settings (hooks_installer)."""

import json
import os
import stat
import sys
import tempfile
import unittest
import unittest.mock

from agent_command_tab import hooks_installer
from agent_command_tab.hooks_installer import (BACKUP_FILE_SUFFIX, HOOK_EVENTS_BY_AGENT_TOOL_NAME, HOOK_NAMES_BY_AGENT_TOOL_NAME,
                                               HOOKS_ARE_INSTALLED, HOOKS_ARE_MISSING, HOOKS_AWAIT_APPROVAL,
                                               add_the_apps_hooks, build_forwarder_command, check_hooks,
                                               codex_has_approved_the_hooks, compute_codex_approval_hash,
                                               convert_event_name_to_codex_hook_state_name, install_hooks,
                                               list_positions_of_the_apps_hooks, remove_hooks, remove_the_apps_hooks)

COMMAND = build_forwarder_command("Claude Code", "/venv/bin/python", "/code/hook_event_forwarder.py")
EVENTS = HOOK_EVENTS_BY_AGENT_TOOL_NAME["Claude Code"]
CODEX_COMMAND = build_forwarder_command("Codex", "/venv/bin/python", "/code/hook_event_forwarder.py")
CODEX_EVENTS = HOOK_EVENTS_BY_AGENT_TOOL_NAME["Codex"]
CODEX_HOOK_NAME = HOOK_NAMES_BY_AGENT_TOOL_NAME["Codex"]


def write_codex_approvals(config_file_path, hooks_file_path, settings, positions):
    """Writes config.toml as Codex's /hooks screen does once the user approves the hooks at the positions."""
    with open(config_file_path, "w") as config_file:
        for event_name, group_index, hook_index in positions:
            config_file.write(f'[hooks.state."{hooks_file_path}:{convert_event_name_to_codex_hook_state_name(event_name)}:'
                              f'{group_index}:{hook_index}"]\ntrusted_hash = '
                              f'"{compute_codex_approval_hash(event_name, settings["hooks"][event_name][group_index])}"\n\n')
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

    def test_a_python_that_finds_its_library_through_pythonhome_gets_it_in_the_command(self):
        # As inside AgentCommandTab.app, where -I would make Python ignore PYTHONHOME.
        resources_folder = "/Applications/AgentCommandTab.app/Contents/Resources"
        self.assertEqual(
            build_forwarder_command("Codex", "/Applications/AgentCommandTab.app/Contents/MacOS/python",
                                    f"{resources_folder}/lib/python3.13/agent_command_tab/hook_event_forwarder.py",
                                    python_home=resources_folder),
            f"/usr/bin/env -u PYTHONPATH PYTHONHOME={resources_folder} /Applications/AgentCommandTab.app/Contents/MacOS/python "
            f"-P -S {resources_folder}/lib/python3.13/agent_command_tab/hook_event_forwarder.py Codex")


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

    def test_only_codex_hooks_are_named(self):
        codex_settings = add_the_apps_hooks({}, CODEX_COMMAND, CODEX_EVENTS, CODEX_HOOK_NAME)
        self.assertEqual(codex_settings["hooks"]["Stop"][0]["hooks"][0]["statusMessage"], "Report to AgentCommandTab")
        self.assertNotIn("statusMessage", add_the_apps_hooks({}, COMMAND, EVENTS)["hooks"]["Stop"][0]["hooks"][0])
        # The unnamed entries installed before 2026-10-27 aren't the app's current ones, so installing replaces them.
        self.assertIsNone(list_positions_of_the_apps_hooks(add_the_apps_hooks({}, CODEX_COMMAND, CODEX_EVENTS),
                                                           CODEX_COMMAND, CODEX_EVENTS, CODEX_HOOK_NAME))

    def test_codex_approval_hashes_match_the_ones_codex_computes(self):
        # The currentHash Codex 0.165's app server gave in its hooks/list answer for these entries in a
        # hooks.json; the SessionEnd hash is of the hook with its timeout lowered to 3 seconds.
        command = build_forwarder_command("Codex", "/Users/someone/code/agent-command-tab/.venv/bin/python3.11",
                                          "/Users/someone/code/agent-command-tab/src/agent_command_tab/hook_event_forwarder.py")
        settings = add_the_apps_hooks({}, command, CODEX_EVENTS, CODEX_HOOK_NAME)
        self.assertEqual({event_name: compute_codex_approval_hash(event_name, settings["hooks"][event_name][0])
                          for event_name, _ in CODEX_EVENTS},
                         {"SessionStart": "sha256:dc397b963b34e2260a57bcbb1bf2347c40a56bb134acc371ad0535700b7e9723",
                          "Stop": "sha256:ea167a66a070ccd0b9702719b74024694040cd7c7436592103bfcd11ac2404bd",
                          "SessionEnd": "sha256:82220384c7f34da95e780dd41805395863546c94417a22412a7999da4fab1b02"})

    def test_codex_approvals_are_found_by_position_and_must_be_of_the_hook_as_it_is(self):
        self.assertEqual(convert_event_name_to_codex_hook_state_name("SessionEnd"), "session_end")
        settings = add_the_apps_hooks({}, CODEX_COMMAND, CODEX_EVENTS, CODEX_HOOK_NAME)
        positions = list_positions_of_the_apps_hooks(settings, CODEX_COMMAND, CODEX_EVENTS, CODEX_HOOK_NAME)
        config_file_path = os.path.join(tempfile.mkdtemp(), "config.toml")
        unnamed_settings = add_the_apps_hooks({}, CODEX_COMMAND, CODEX_EVENTS)  # approved before the hooks had a name
        for approved_settings, approved_positions, expected in ((settings, positions[:-1], False), (settings, positions, True),
                                                                (unnamed_settings, positions, False)):
            write_codex_approvals(config_file_path, "/h/hooks.json", approved_settings, approved_positions)
            with open(config_file_path) as config_file:
                self.assertEqual(codex_has_approved_the_hooks(config_file.read(), "/h/hooks.json", settings, positions), expected)


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
        settings = self.read_json(self.codex_hooks_path)
        positions = list_positions_of_the_apps_hooks(settings, build_forwarder_command("Codex"), CODEX_EVENTS, CODEX_HOOK_NAME)
        write_codex_approvals(os.path.join(self.codex_home_folder, "config.toml"), self.codex_hooks_path, settings, positions)
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
