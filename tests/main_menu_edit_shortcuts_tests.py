"""The hidden main menu that gives the dashboard's text fields their Command-key editing shortcuts
(app_controller.build_main_menu_with_the_edit_shortcuts)."""

import unittest

from AppKit import NSEventModifierFlagCommand, NSEventModifierFlagShift

from agent_command_tab.app_controller import build_main_menu_with_the_edit_shortcuts


class MainMenuEditShortcutsTests(unittest.TestCase):
    def test_the_edit_menu_has_the_standard_shortcuts_for_the_field_being_edited(self):
        main_menu = build_main_menu_with_the_edit_shortcuts()
        edit_menu = main_menu.itemArray()[0].submenu()
        self.assertEqual(
            [(menu_item.action(), menu_item.keyEquivalent(), menu_item.keyEquivalentModifierMask()) for menu_item in edit_menu.itemArray()],
            [("undo:", "z", NSEventModifierFlagCommand), ("redo:", "z", NSEventModifierFlagCommand | NSEventModifierFlagShift),
             ("cut:", "x", NSEventModifierFlagCommand), ("copy:", "c", NSEventModifierFlagCommand),
             ("paste:", "v", NSEventModifierFlagCommand), ("selectAll:", "a", NSEventModifierFlagCommand)])
        self.assertTrue(all(menu_item.target() is None for menu_item in edit_menu.itemArray()))  # to the field being edited


if __name__ == "__main__":
    unittest.main()
