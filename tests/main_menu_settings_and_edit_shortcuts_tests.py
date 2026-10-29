"""The hidden main menu that gives the dashboard its Command-key shortcuts: Settings, and the
editing shortcuts of its text fields (app_controller.build_main_menu_with_the_settings_and_edit_shortcuts)."""

import unittest

from AppKit import NSEventModifierFlagCommand, NSEventModifierFlagShift
from Foundation import NSObject

from agent_command_tab.app_controller import build_main_menu_with_the_settings_and_edit_shortcuts


class SettingsTarget(NSObject):
    def settingsShortcutPressed_(self, sender):
        self.pressed = True


class MainMenuShortcutsTests(unittest.TestCase):
    def test_command_comma_and_command_period_open_the_settings(self):  # user request, 2026-10-29
        settings_target = SettingsTarget.alloc().init()
        app_menu = build_main_menu_with_the_settings_and_edit_shortcuts(settings_target).itemArray()[0].submenu()
        self.assertEqual(
            [(menu_item.action(), menu_item.keyEquivalent(), menu_item.keyEquivalentModifierMask()) for menu_item in app_menu.itemArray()],
            [("settingsShortcutPressed:", ",", NSEventModifierFlagCommand), ("settingsShortcutPressed:", ".", NSEventModifierFlagCommand)])
        self.assertTrue(all(menu_item.target() is settings_target for menu_item in app_menu.itemArray()))

    def test_the_edit_menu_has_the_standard_shortcuts_for_the_field_being_edited(self):
        main_menu = build_main_menu_with_the_settings_and_edit_shortcuts(SettingsTarget.alloc().init())
        edit_menu = main_menu.itemArray()[1].submenu()
        self.assertEqual(
            [(menu_item.action(), menu_item.keyEquivalent(), menu_item.keyEquivalentModifierMask()) for menu_item in edit_menu.itemArray()],
            [("undo:", "z", NSEventModifierFlagCommand), ("redo:", "z", NSEventModifierFlagCommand | NSEventModifierFlagShift),
             ("cut:", "x", NSEventModifierFlagCommand), ("copy:", "c", NSEventModifierFlagCommand),
             ("paste:", "v", NSEventModifierFlagCommand), ("selectAll:", "a", NSEventModifierFlagCommand)])
        self.assertTrue(all(menu_item.target() is None for menu_item in edit_menu.itemArray()))  # to the field being edited


if __name__ == "__main__":
    unittest.main()
