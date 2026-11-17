"""The hidden main menu that gives the dashboard its Command-key shortcuts: Settings, Close (Command-W),
and the editing shortcuts of its text fields (app_controller.build_main_menu_with_the_settings_close_and_edit_shortcuts)."""

import unittest

from AppKit import (NSBackingStoreBuffered, NSEventModifierFlagCommand, NSEventModifierFlagShift, NSMakeRect, NSPanel,
                    NSWindow, NSWindowStyleMaskBorderless, NSWindowStyleMaskClosable, NSWindowStyleMaskNonactivatingPanel,
                    NSWindowStyleMaskTitled)
from Foundation import NSObject

from agent_command_tab.app_controller import build_main_menu_with_the_settings_close_and_edit_shortcuts
from codex_sandbox_detection import REASON_FOR_SKIPPING_WINDOW_TESTS_IN_CODEX_SANDBOX, RUNNING_IN_CODEX_SANDBOX


class SettingsTarget(NSObject):
    def settingsShortcutPressed_(self, sender):
        self.pressed_key_equivalents.append(sender.keyEquivalent())


class MainMenuShortcutsTests(unittest.TestCase):
    def test_command_comma_and_command_period_open_the_settings(self):
        settings_target = SettingsTarget.alloc().init()
        settings_target.pressed_key_equivalents = []
        app_menu = build_main_menu_with_the_settings_close_and_edit_shortcuts(settings_target).itemArray()[0].submenu()
        self.assertEqual(
            [(menu_item.action(), menu_item.keyEquivalent(), menu_item.keyEquivalentModifierMask()) for menu_item in app_menu.itemArray()],
            [("settingsShortcutPressed:", ",", NSEventModifierFlagCommand), ("settingsShortcutPressed:", ".", NSEventModifierFlagCommand)])
        for menu_item in app_menu.itemArray():  # as choosing the item does
            menu_item.target().performSelector_withObject_(menu_item.action(), menu_item)
        self.assertEqual(settings_target.pressed_key_equivalents, [",", "."])

    def test_the_edit_menu_has_the_standard_shortcuts_for_the_field_being_edited(self):
        main_menu = build_main_menu_with_the_settings_close_and_edit_shortcuts(SettingsTarget.alloc().init())
        edit_menu = main_menu.itemWithTitle_("Edit").submenu()
        self.assertEqual(
            [(menu_item.action(), menu_item.keyEquivalent(), menu_item.keyEquivalentModifierMask()) for menu_item in edit_menu.itemArray()],
            [("undo:", "z", NSEventModifierFlagCommand), ("redo:", "z", NSEventModifierFlagCommand | NSEventModifierFlagShift),
             ("cut:", "x", NSEventModifierFlagCommand), ("copy:", "c", NSEventModifierFlagCommand),
             ("paste:", "v", NSEventModifierFlagCommand), ("selectAll:", "a", NSEventModifierFlagCommand)])
        self.assertTrue(all(menu_item.target() is None for menu_item in edit_menu.itemArray()))  # to the field being edited

    @unittest.skipIf(RUNNING_IN_CODEX_SANDBOX, REASON_FOR_SKIPPING_WINDOW_TESTS_IN_CODEX_SANDBOX)
    def test_command_w_closes_the_window_in_front_when_it_has_a_close_button(self):
        main_menu = build_main_menu_with_the_settings_close_and_edit_shortcuts(SettingsTarget.alloc().init())
        (close_item,) = main_menu.itemWithTitle_("File").submenu().itemArray()
        self.assertEqual((close_item.action(), close_item.keyEquivalent(), close_item.keyEquivalentModifierMask(), close_item.target()),
                         ("performClose:", "w", NSEventModifierFlagCommand, None))  # to the window in front
        # As the Settings, Saved sessions and list windows are made, and as the sidebar is.
        closable_window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(-3000, -3000, 200, 100), NSWindowStyleMaskTitled | NSWindowStyleMaskClosable, NSBackingStoreBuffered, False)
        borderless_panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(-3000, -3000, 200, 100), NSWindowStyleMaskBorderless | NSWindowStyleMaskNonactivatingPanel,
            NSBackingStoreBuffered, False)
        self.assertEqual((closable_window.validateMenuItem_(close_item), borderless_panel.validateMenuItem_(close_item)), (True, False))
        closable_window.setReleasedWhenClosed_(False)
        closable_window.orderFront_(None)
        closable_window.performSelector_withObject_(close_item.action(), close_item)  # as choosing the item does
        self.assertFalse(closable_window.isVisible())


if __name__ == "__main__":
    unittest.main()
