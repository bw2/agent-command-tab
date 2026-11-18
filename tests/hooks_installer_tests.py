"""Installing, checking and removing the app's hooks in Claude Code's settings, and taking out
those earlier versions added to Codex's (hooks_installer)."""

import contextlib
import io
import json
import os
import shlex
import stat
import sys
import tempfile
import unittest
import unittest.mock

from agent_command_tab import hooks_installer
from agent_command_tab.hooks_installer import (BACKUP_FILE_SUFFIX, HOOK_EVENTS_BY_AGENT_TOOL_NAME, HOOKS_ARE_INSTALLED,
                                               HOOKS_ARE_MISSING, HOOKS_CANT_RUN, add_the_apps_hooks,
                                               build_forwarder_command, check_hooks, install_hooks, remove_hooks,
                                               remove_the_apps_hooks, settings_have_the_apps_hooks,
                                               take_out_the_hooks_only_earlier_versions_added, this_copys_hooks_can_run)
from homebrew_folder_builders import add_the_homebrew_opt_link, make_file_in_a_homebrew_version_folder

COMMAND = build_forwarder_command("Claude Code", "/venv/bin/python", "/code/hook_event_forwarder.py")
EVENTS = HOOK_EVENTS_BY_AGENT_TOOL_NAME["Claude Code"]
# A user's own settings, as in a real ~/.claude/settings.json (trimmed).
USERS_SETTINGS = {
    "model": "opus",
    "hooks": {
        "PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "rtk hook claude"}]}],
        "Stop": [{"hooks": [{"type": "command", "command": "/Users/someone/.config/iterm2/cc-status"}]}],
    },
}
# A Codex hooks.json as earlier versions left it (as in a real ~/.codex/hooks.json, trimmed): the
# user's own hooks, then the app's, named for Codex's /hooks screen.
USERS_CODEX_HOOKS = {
    "hooks": {
        "PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "rtk hook claude"}]}],
        "UserPromptSubmit": [{"hooks": [{"type": "command", "command": "date '+Current time: %m/%d/%Y %I:%M:%S %p %Z'"}]}],
    },
}
OLD_CODEX_HOOK = {"type": "command", "command": "/venv/bin/python -I -S /code/hook_event_forwarder.py Codex", "async": True,
                  "timeout": 10, "statusMessage": "Report to AgentCommandTab"}
CODEX_HOOKS_EARLIER_VERSIONS_LEFT = {"hooks": dict(USERS_CODEX_HOOKS["hooks"], **{
    event_name: [{"hooks": [OLD_CODEX_HOOK]}] for event_name in ("SessionStart", "Stop", "SessionEnd")})}


class CodexHomeFoldersTests(unittest.TestCase):
    def test_old_hooks_are_also_taken_out_of_the_codex_home_the_codex_launch_command_sets(self):
        # Earlier versions put them there too, since its sessions read their hooks only from that home.
        with tempfile.TemporaryDirectory() as folder:
            settings_file_path = os.path.join(folder, "settings.json")
            for launch_command, expected_folders in (("codex", ["/app-codex-home"]),
                                                     ("RUST_LOG=info CODEX_HOME=/x codex -s read-only", ["/app-codex-home", "/x"]),
                                                     # A "~" is expanded here: the settings' reader leaves it as written.
                                                     ("CODEX_HOME=~/.codex-work codex", ["/app-codex-home", "/home-folder/.codex-work"]),
                                                     ("CODEX_HOME=/app-codex-home codex", ["/app-codex-home"])):  # listed once
                with open(settings_file_path, "w") as settings_file:
                    json.dump({"agent_tools": [{"agent_tool_name": "Codex", "launch_command": launch_command}]}, settings_file)
                with unittest.mock.patch.dict(os.environ, {"CODEX_HOME": "/app-codex-home", "HOME": "/home-folder"}):
                    self.assertEqual(hooks_installer.list_codex_home_folders(settings_file_path), expected_folders, launch_command)


class BuildForwarderCommandTests(unittest.TestCase):
    def test_paths_with_spaces_are_quoted(self):
        self.assertEqual(build_forwarder_command("Claude Code", "/My Apps/python", "/code/hook_event_forwarder.py"),
                         "'/My Apps/python' -I -S /code/hook_event_forwarder.py 'Claude Code'")

    def test_a_homebrew_python_is_named_by_its_path_that_survives_an_upgrade(self):
        with tempfile.TemporaryDirectory() as prefix:
            python_path = make_file_in_a_homebrew_version_folder(prefix, "libexec/bin/python")
            add_the_homebrew_opt_link(prefix)
            command = build_forwarder_command("Claude Code", python_path, "/code/hook_event_forwarder.py")
        # Compared part by part: the temporary folder's path may need quoting (a space in it), which the test above covers.
        self.assertEqual(shlex.split(command), [f"{prefix}/opt/agent-command-tab/libexec/bin/python", "-I", "-S",
                                                "/code/hook_event_forwarder.py", "Claude Code"])


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

    def test_another_tools_hook_added_to_the_apps_entry_is_kept(self):
        apps_entry = add_the_apps_hooks({}, COMMAND, EVENTS)["hooks"]["SessionEnd"][0]
        other_hook = {"type": "command", "command": "/usr/local/bin/notify-done"}
        settings = add_the_apps_hooks(USERS_SETTINGS, COMMAND, EVENTS)
        # Another tool's installer appended its hook to the app's entry.
        settings["hooks"]["SessionEnd"] = [dict(apps_entry, hooks=apps_entry["hooks"] + [other_hook])]
        self.assertTrue(settings_have_the_apps_hooks(settings, COMMAND, EVENTS))  # the app's hook still runs from that entry
        self.assertFalse(settings_have_the_apps_hooks(settings, "/other/python -I -S x 'Claude Code'", EVENTS))
        self.assertEqual(remove_the_apps_hooks(settings)["hooks"]["SessionEnd"], [{"hooks": [other_hook]}])
        reinstalled_settings = add_the_apps_hooks(settings, COMMAND, EVENTS)
        self.assertEqual(reinstalled_settings["hooks"]["SessionEnd"], [{"hooks": [other_hook]}, apps_entry])
        self.assertTrue(settings_have_the_apps_hooks(reinstalled_settings, COMMAND, EVENTS))

    def test_the_apps_hooks_need_every_event_with_the_same_command(self):
        settings = add_the_apps_hooks(USERS_SETTINGS, COMMAND, EVENTS)
        self.assertTrue(settings_have_the_apps_hooks(settings, COMMAND, EVENTS))
        self.assertFalse(settings_have_the_apps_hooks(USERS_SETTINGS, COMMAND, EVENTS))
        self.assertFalse(settings_have_the_apps_hooks(settings, "/other/python -I -S x 'Claude Code'", EVENTS))
        settings_without_one_event = dict(settings, hooks={event_name: hook_groups for event_name, hook_groups
                                                           in settings["hooks"].items() if event_name != "SubagentStop"})
        self.assertFalse(settings_have_the_apps_hooks(settings_without_one_event, COMMAND, EVENTS))

    def test_another_link_to_the_same_python_is_the_same_command(self):
        # The app's entry point runs as .venv/bin/python3.11, `python -m ...` as .venv/bin/python, a link to it.
        with tempfile.TemporaryDirectory() as folder:
            python_path, link_path = os.path.join(folder, "python3.11"), os.path.join(folder, "python")
            open(python_path, "w").close()
            os.symlink("python3.11", link_path)
            settings = add_the_apps_hooks(USERS_SETTINGS, build_forwarder_command("Claude Code", python_path, "/f.py"), EVENTS)
            self.assertTrue(settings_have_the_apps_hooks(settings, build_forwarder_command("Claude Code", link_path, "/f.py"), EVENTS))
            self.assertFalse(settings_have_the_apps_hooks(settings, build_forwarder_command("Codex", link_path, "/f.py"), EVENTS))

    def test_the_named_codex_hooks_earlier_versions_added_are_the_apps(self):
        self.assertEqual(remove_the_apps_hooks(CODEX_HOOKS_EARLIER_VERSIONS_LEFT), USERS_CODEX_HOOKS)


class FilesTests(unittest.TestCase):
    def setUp(self):
        temporary_folder = tempfile.TemporaryDirectory()
        self.addCleanup(temporary_folder.cleanup)
        self.folder = temporary_folder.name
        self.claude_settings_path = os.path.join(self.folder, "settings.json")
        with open(self.claude_settings_path, "w") as settings_file:
            json.dump(USERS_SETTINGS, settings_file)
        os.chmod(self.claude_settings_path, 0o600)
        self.codex_home_folder = os.path.join(self.folder, "codex")
        os.mkdir(self.codex_home_folder)
        self.codex_hooks_path = os.path.join(self.codex_home_folder, "hooks.json")
        patch = unittest.mock.patch.object(hooks_installer, "list_settings_file_paths", lambda agent_tool_name: {
            "Claude Code": [self.claude_settings_path], "Codex": [self.codex_hooks_path]}[agent_tool_name])
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
        # The backup still holds the file from before the first change, not the app's version
        # the removal replaced.
        self.assertEqual(self.read_json(self.claude_settings_path + BACKUP_FILE_SUFFIX), USERS_SETTINGS)

    def test_the_backup_of_a_file_the_app_created_is_empty(self):
        # Without that empty backup made at install, the removal that follows would copy the app's
        # own settings.json there, as if it were the original.
        os.remove(self.claude_settings_path)
        self.assertEqual(install_hooks("Claude Code"), [self.claude_settings_path])  # a new settings.json
        self.assertEqual(self.read_json(self.claude_settings_path + BACKUP_FILE_SUFFIX), {})
        self.assertEqual(remove_hooks("Claude Code"), [self.claude_settings_path])
        self.assertEqual(self.read_json(self.claude_settings_path + BACKUP_FILE_SUFFIX), {})

    def test_installing_again_from_another_link_to_the_same_python_leaves_the_file_alone(self):
        install_hooks("Claude Code")
        with unittest.mock.patch.object(hooks_installer, "find_python_of_this_copy", lambda: os.path.realpath(sys.executable)):
            self.assertEqual(install_hooks("Claude Code"), [])
            self.assertEqual(check_hooks("Claude Code"), HOOKS_ARE_INSTALLED)

    def test_the_hooks_earlier_versions_added_to_codex_are_taken_out_and_never_installed(self):
        # Codex 0.165 and later run hooks in their shared background server, so their events reached no tab.
        self.assertNotIn("Codex", HOOK_EVENTS_BY_AGENT_TOOL_NAME)
        with open(self.codex_hooks_path, "w") as hooks_file:
            json.dump(CODEX_HOOKS_EARLIER_VERSIONS_LEFT, hooks_file)
        for expected_output in (f"agent-command-tab: took the hooks earlier versions added for Codex out of {self.codex_hooks_path}\n",
                                ""):  # nothing left to take out the next time
            printed = io.StringIO()
            with contextlib.redirect_stdout(printed):
                take_out_the_hooks_only_earlier_versions_added()
            self.assertEqual(printed.getvalue(), expected_output)
            self.assertEqual(self.read_json(self.codex_hooks_path), USERS_CODEX_HOOKS)
        self.assertEqual(self.read_json(self.codex_hooks_path + BACKUP_FILE_SUFFIX), CODEX_HOOKS_EARLIER_VERSIONS_LEFT)

    def test_a_codex_hooks_file_that_cant_be_read_is_left_alone_and_reported(self):
        with open(self.codex_hooks_path, "w") as hooks_file:
            hooks_file.write('{"hooks": ')  # cut short
        printed = io.StringIO()
        with contextlib.redirect_stdout(printed):
            take_out_the_hooks_only_earlier_versions_added()
        self.assertIn("the hooks earlier versions added for Codex couldn't be taken out", printed.getvalue())
        with open(self.codex_hooks_path) as hooks_file:
            self.assertEqual(hooks_file.read(), '{"hooks": ')

    def test_a_symlinked_settings_file_stays_a_link_to_the_file_that_gets_the_hooks(self):
        linked_file_path = os.path.join(self.folder, "dotfiles_settings.json")
        os.rename(self.claude_settings_path, linked_file_path)
        os.symlink(linked_file_path, self.claude_settings_path)  # as a dotfiles manager keeps it
        install_hooks("Claude Code")
        self.assertTrue(os.path.islink(self.claude_settings_path))
        self.assertTrue(os.path.samefile(self.claude_settings_path, linked_file_path))
        self.assertTrue(settings_have_the_apps_hooks(self.read_json(linked_file_path), build_forwarder_command("Claude Code"), EVENTS))
        self.assertEqual(check_hooks("Claude Code"), HOOKS_ARE_INSTALLED)

    def test_hooks_whose_python_or_forwarder_is_gone_cant_run(self):
        install_hooks("Claude Code")
        # A pipx environment's bin/python, a link to a Python that a Homebrew upgrade removed.
        python_link_to_a_removed_python = os.path.join(self.folder, "python")
        os.symlink(os.path.join(self.folder, "removed", "python3.13"), python_link_to_a_removed_python)
        with unittest.mock.patch.object(hooks_installer, "find_python_of_this_copy", lambda: python_link_to_a_removed_python):
            self.assertEqual(check_hooks("Claude Code"), HOOKS_CANT_RUN)
        self.assertFalse(this_copys_hooks_can_run(sys.executable, os.path.join(self.folder, "hook_event_forwarder.py")))
        self.assertTrue(this_copys_hooks_can_run(sys.executable))
        self.assertEqual(check_hooks("Claude Code"), HOOKS_ARE_INSTALLED)

    def test_a_file_that_isnt_a_json_object_is_left_alone(self):
        for file_contents in ('{"model": "opus",',  # cut short, so not JSON
                              '["opus"]', 'null'):  # JSON, but not the object that settings are
            with self.subTest(file_contents=file_contents):
                with open(self.claude_settings_path, "w") as settings_file:
                    settings_file.write(file_contents)
                with self.assertRaises(ValueError):
                    install_hooks("Claude Code")
                with open(self.claude_settings_path) as settings_file:
                    self.assertEqual(settings_file.read(), file_contents)
                self.assertEqual(check_hooks("Claude Code"), HOOKS_ARE_MISSING)


if __name__ == "__main__":
    unittest.main()
