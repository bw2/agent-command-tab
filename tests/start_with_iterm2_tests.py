"""The AppleScript in iTerm2's AutoLaunch folder that starts the app whenever iTerm2 starts (start_with_iterm2),
and the paths that survive upgrades (installed_copy_paths)."""

import os
import plistlib
import shutil
import subprocess
import tempfile
import unittest

from agent_command_tab.installed_copy_paths import find_python_of_this_copy, replace_homebrew_version_folder_with_its_stable_link
from agent_command_tab.start_with_iterm2 import (ITERM2_SCRIPTS_FOLDER, build_autolaunch_script_source,
                                                 build_command_that_starts_this_copy_of_the_app, escape_for_applescript_string,
                                                 find_iterm2_autolaunch_folder, read_program_of_autolaunch_script,
                                                 update_autolaunch_script_to_match_setting)

HOMEBREW_PYTHON = "/opt/homebrew/Cellar/agent-command-tab/0.1.0/libexec/bin/python"


class HomebrewStablePathTests(unittest.TestCase):
    def test_a_version_folder_is_replaced_by_its_opt_link_while_that_exists(self):
        self.assertEqual(replace_homebrew_version_folder_with_its_stable_link(HOMEBREW_PYTHON, lambda path: True),
                         "/opt/homebrew/opt/agent-command-tab/libexec/bin/python")
        self.assertEqual(replace_homebrew_version_folder_with_its_stable_link(  # Homebrew on Intel Macs
            "/usr/local/Cellar/agent-command-tab/0.2.0_1/libexec/lib/python3.13/site-packages/agent_command_tab/x.py",
            lambda path: True),
            "/usr/local/opt/agent-command-tab/libexec/lib/python3.13/site-packages/agent_command_tab/x.py")

    def test_other_paths_and_a_missing_opt_link_are_kept(self):
        self.assertEqual(replace_homebrew_version_folder_with_its_stable_link(HOMEBREW_PYTHON, lambda path: False), HOMEBREW_PYTHON)
        for path in ("/Users/someone/.venv/bin/python3.13", "/Library/Frameworks/Python.framework/Versions/3.13/bin/python3.13"):
            self.assertEqual(replace_homebrew_version_folder_with_its_stable_link(path, lambda path: True), path)

    def test_the_real_file_system_is_checked(self):
        with tempfile.TemporaryDirectory() as folder:
            version_folder = os.path.join(folder, "Cellar", "agent-command-tab", "0.1.0", "bin")
            os.makedirs(version_folder)
            os.makedirs(os.path.join(folder, "opt"))
            path = os.path.join(version_folder, "agent-command-tab")
            with open(path, "w"):
                pass
            self.assertEqual(replace_homebrew_version_folder_with_its_stable_link(path), path)  # no opt link yet
            os.symlink(os.path.dirname(version_folder), os.path.join(folder, "opt", "agent-command-tab"))
            self.assertEqual(replace_homebrew_version_folder_with_its_stable_link(path),
                             os.path.join(folder, "opt", "agent-command-tab", "bin", "agent-command-tab"))


class PythonOfThisCopyTests(unittest.TestCase):
    def test_a_venv_is_named_by_its_bin_python_whatever_python_version_made_it(self):
        # The hooks named .venv/bin/python3.11, gone once the venv was made again with Python 3.13 (2026-10-28).
        self.assertEqual(find_python_of_this_copy("/code/.venv/bin/python3.11", "/code/.venv", "/Library/Frameworks/Python.framework/Versions/3.11",
                                                  lambda path: True), "/code/.venv/bin/python")
        self.assertEqual(find_python_of_this_copy(HOMEBREW_PYTHON + "3.13", "/opt/homebrew/Cellar/agent-command-tab/0.1.0/libexec",
                                                  "/opt/homebrew/opt/python@3.13/Frameworks/Python.framework/Versions/3.13",
                                                  lambda path: True), "/opt/homebrew/opt/agent-command-tab/libexec/bin/python")

    def test_a_python_outside_a_venv_is_kept(self):
        framework_python = "/Library/Frameworks/Python.framework/Versions/3.13/bin/python3.13"
        framework_prefix = "/Library/Frameworks/Python.framework/Versions/3.13"
        self.assertEqual(find_python_of_this_copy(framework_python, framework_prefix, framework_prefix, lambda path: True), framework_python)


class CommandThatStartsTheAppTests(unittest.TestCase):
    def test_a_python_runs_the_command_line_module_by_a_path_that_survives_an_upgrade(self):
        self.assertEqual(build_command_that_starts_this_copy_of_the_app("/Users/someone/.venv/bin/python"),
                         ["/Users/someone/.venv/bin/python", "-m", "agent_command_tab.command_line", "run"])
        homebrew_command = build_command_that_starts_this_copy_of_the_app(HOMEBREW_PYTHON)
        self.assertEqual(homebrew_command[1:], ["-m", "agent_command_tab.command_line", "run"])
        self.assertEqual(homebrew_command[0], replace_homebrew_version_folder_with_its_stable_link(HOMEBREW_PYTHON))


class AutoLaunchFolderTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.folder)
        self.preferences_file_path = os.path.join(self.folder, "com.googlecode.iterm2.plist")

    def find_with_preferences(self, preferences):
        with open(self.preferences_file_path, "wb") as preferences_file:
            plistlib.dump(preferences, preferences_file, fmt=plistlib.FMT_BINARY)  # as iTerm2 saves them
        return find_iterm2_autolaunch_folder(self.preferences_file_path)

    def test_the_usual_folder_unless_iterm2s_settings_name_a_usable_custom_one(self):
        usual_folder = os.path.join(ITERM2_SCRIPTS_FOLDER, "AutoLaunch")
        self.assertEqual(find_iterm2_autolaunch_folder(os.path.join(self.folder, "missing.plist")), usual_folder)
        custom_folder = os.path.join(self.folder, "my_scripts")
        os.makedirs(custom_folder)
        self.assertEqual(self.find_with_preferences({"UseCustomScriptsFolder": True, "CustomScriptsFolder": custom_folder}),
                         os.path.join(custom_folder, "AutoLaunch"))
        self.assertEqual(self.find_with_preferences({"UseCustomScriptsFolder": False, "CustomScriptsFolder": custom_folder}),
                         usual_folder)
        # iTerm2 ignores a custom folder that doesn't exist or has a space in its path.
        self.assertEqual(self.find_with_preferences({"UseCustomScriptsFolder": True, "CustomScriptsFolder": custom_folder + "_gone"}),
                         usual_folder)
        spaced_folder = os.path.join(self.folder, "my scripts")
        os.makedirs(spaced_folder)
        self.assertEqual(self.find_with_preferences({"UseCustomScriptsFolder": True, "CustomScriptsFolder": spaced_folder}),
                         usual_folder)


class AutoLaunchScriptTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.folder)
        self.autolaunch_script_path = os.path.join(self.folder, "Scripts", "AutoLaunch", "AgentCommandTab.scpt")
        self.log_file_path = os.path.join(self.folder, "agent-command-tab.log")
        self.program = os.path.join(self.folder, "python")  # this copy's program, which exists
        with open(self.program, "w"):
            pass

    def update(self, app_starts_with_iterm2, program=None):
        return update_autolaunch_script_to_match_setting(
            app_starts_with_iterm2, [program or self.program, "-m", "agent_command_tab.command_line", "run"],
            self.autolaunch_script_path, self.log_file_path)

    def test_the_script_starts_the_command_in_the_background_with_its_output_in_the_log(self):
        source = build_autolaunch_script_source(["/My Apps/python", "-m", "agent_command_tab.command_line", "run"], "/x/log")
        self.assertIn("-- program: /My Apps/python\n", source)
        self.assertIn('do shell script "\'/My Apps/python\' -m agent_command_tab.command_line run >> /x/log 2>&1 &"', source)
        self.assertEqual(escape_for_applescript_string('a "b" \\c'), 'a \\"b\\" \\\\c')

    def test_turned_on_it_adds_a_compiled_script_once_and_then_keeps_it(self):
        self.assertEqual(self.update(True), "added")
        decompiled_source = subprocess.run(["/usr/bin/osadecompile", self.autolaunch_script_path], capture_output=True, text=True,
                                           check=True).stdout
        self.assertIn(f"-- program: {self.program}", decompiled_source)
        self.assertIn(f'do shell script "{self.program} -m agent_command_tab.command_line run >> {self.log_file_path} 2>&1 &"',
                      decompiled_source)
        self.assertEqual(os.listdir(os.path.dirname(self.autolaunch_script_path)), ["AgentCommandTab.scpt"])  # no hidden file left
        # Another copy of the app (say, a pip install next to a Homebrew one) keeps the copy that added it.
        other_program = os.path.join(self.folder, "other-python")
        with open(other_program, "w"):
            pass
        self.assertEqual(self.update(True, other_program), "kept")
        self.assertEqual(read_program_of_autolaunch_script(self.autolaunch_script_path), self.program)

    def test_a_script_whose_program_is_gone_or_that_cant_be_read_is_written_again(self):
        self.update(True, os.path.join(self.folder, "uninstalled-python"))
        self.assertEqual(self.update(True), "added")
        self.assertEqual(read_program_of_autolaunch_script(self.autolaunch_script_path), self.program)
        with open(self.autolaunch_script_path, "w") as autolaunch_script_file:
            autolaunch_script_file.write("not a compiled script")
        self.assertIsNone(read_program_of_autolaunch_script(self.autolaunch_script_path))
        self.assertEqual(self.update(True), "added")
        self.assertEqual(read_program_of_autolaunch_script(self.autolaunch_script_path), self.program)

    def test_turned_off_it_removes_the_script(self):
        self.assertEqual(self.update(False), "nothing")
        self.update(True)
        self.assertEqual(self.update(False), "removed")
        self.assertFalse(os.path.exists(self.autolaunch_script_path))


if __name__ == "__main__":
    unittest.main()
