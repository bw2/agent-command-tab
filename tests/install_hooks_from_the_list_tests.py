"""The list's Install Hooks button (AppController.install_hooks; user request, 2026-10-27): it asks
first, naming the files, installs the hooks, and has the core check them again at once."""

import json
import os
import tempfile
import unittest
import unittest.mock

from AppKit import NSAlertFirstButtonReturn, NSAlertSecondButtonReturn, NSApplication, NSApplicationActivationPolicyAccessory

from agent_command_tab import app_controller as app_controller_module
from agent_command_tab import hooks_installer
from agent_command_tab.app_controller import AppController
from agent_command_tab.hooks_installer import HOOKS_ARE_INSTALLED, HOOKS_ARE_MISSING, check_hooks


class InstallHooksFromTheListTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        NSApplication.sharedApplication().setActivationPolicy_(NSApplicationActivationPolicyAccessory)

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.settings_path = os.path.join(folder.name, "settings.json")
        with open(self.settings_path, "w") as settings_file:
            json.dump({"model": "opus"}, settings_file)
        list_settings_file_paths = lambda agent_tool_name: [self.settings_path] if agent_tool_name == "Claude Code" else []
        for patch in (unittest.mock.patch.object(hooks_installer, "list_settings_file_paths", list_settings_file_paths),
                      unittest.mock.patch.object(app_controller_module, "list_settings_file_paths", list_settings_file_paths)):
            patch.start()
            self.addCleanup(patch.stop)
        self.alerts = []
        self.button_chosen = NSAlertFirstButtonReturn
        self.stand_in_app_controller = unittest.mock.Mock()

        def run_alert_in_front(alert):
            self.alerts.append(alert)
            return self.button_chosen
        self.stand_in_app_controller.run_alert_in_front = run_alert_in_front

    def test_install_asks_naming_the_files_then_installs_and_has_the_core_check_again(self):
        AppController.install_hooks(self.stand_in_app_controller, ["Claude Code"])
        (alert,) = self.alerts
        self.assertEqual(alert.messageText(), "Install the hooks for Claude Code?")
        self.assertIn(self.settings_path, alert.informativeText())
        self.assertEqual([button.title() for button in alert.buttons()], ["Install", "Cancel"])
        self.assertEqual(check_hooks("Claude Code"), HOOKS_ARE_INSTALLED)
        self.stand_in_app_controller.connection_thread.send_hooks_changed.assert_called_once_with()

    def test_cancel_changes_nothing(self):
        self.button_chosen = NSAlertSecondButtonReturn
        AppController.install_hooks(self.stand_in_app_controller, ["Claude Code"])
        self.assertEqual(check_hooks("Claude Code"), HOOKS_ARE_MISSING)
        with open(self.settings_path) as settings_file:
            self.assertEqual(json.load(settings_file), {"model": "opus"})
        self.stand_in_app_controller.connection_thread.send_hooks_changed.assert_not_called()

    def test_a_file_that_cant_be_changed_is_reported(self):
        with open(self.settings_path, "w") as settings_file:
            settings_file.write('{"model": ')  # not JSON: left alone
        AppController.install_hooks(self.stand_in_app_controller, ["Claude Code"])
        self.assertEqual([alert.messageText() for alert in self.alerts],
                         ["Install the hooks for Claude Code?", "The hooks couldn't be installed"])
        with open(self.settings_path) as settings_file:
            self.assertEqual(settings_file.read(), '{"model": ')


if __name__ == "__main__":
    unittest.main()
