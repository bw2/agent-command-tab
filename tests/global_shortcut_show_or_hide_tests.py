"""What a press of the global shortcut does (app_controller.choose_what_the_global_shortcut_does)."""

import unittest

from agent_command_tab.app_controller import choose_what_the_global_shortcut_does


class ChooseWhatTheGlobalShortcutDoesTests(unittest.TestCase):
    def test_the_dropdown_opens_and_closes(self):
        self.assertEqual(choose_what_the_global_shortcut_does("popup", "auto_hide", False, False, False), "open_dropdown")
        self.assertEqual(choose_what_the_global_shortcut_does("popup", "auto_hide", False, False, True), "close_dropdown")

    def test_an_auto_hiding_sidebar_slides_in_and_out(self):
        self.assertEqual(choose_what_the_global_shortcut_does("sidebar", "auto_hide", False, False, False), "show_sidebar")
        self.assertEqual(choose_what_the_global_shortcut_does("sidebar", "auto_hide", True, False, False), "hide_sidebar")

    def test_a_sidebar_set_to_stay_visible_stays_or_comes_back_if_the_menu_hid_it(self):
        for sidebar_mode in ("floating", "docked"):
            self.assertEqual(choose_what_the_global_shortcut_does("sidebar", sidebar_mode, True, False, False), "nothing")
            # Hidden by the menu bar icon's menu's Show popup or Show window (code review, 2026-10-28).
            self.assertEqual(choose_what_the_global_shortcut_does("sidebar", sidebar_mode, False, False, False), "show_sidebar")

    def test_the_window_hides_only_while_it_is_in_front(self):
        self.assertEqual(choose_what_the_global_shortcut_does("window", "auto_hide", False, False, False), "show_window")
        self.assertEqual(choose_what_the_global_shortcut_does("window", "auto_hide", False, True, False), "hide_window")


if __name__ == "__main__":
    unittest.main()
