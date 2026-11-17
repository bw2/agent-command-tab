"""What a press of the global shortcut does (app_controller.choose_what_the_global_shortcut_does)."""

import unittest

from agent_command_tab.app_controller import choose_what_the_global_shortcut_does


class ChooseWhatTheGlobalShortcutDoesTests(unittest.TestCase):
    def test_the_dropdown_opens_and_closes(self):
        self.assertEqual(choose_what_the_global_shortcut_does("popup", "auto_hide", False, False, False, False), "open_dropdown")
        self.assertEqual(choose_what_the_global_shortcut_does("popup", "auto_hide", False, False, False, True), "close_dropdown")

    def test_an_auto_hiding_sidebar_slides_in_and_out(self):
        self.assertEqual(choose_what_the_global_shortcut_does("sidebar", "auto_hide", False, False, False, False), "show_sidebar")
        for sidebar_has_the_keyboard in (True, False):  # given by the shortcut, or slid in by the pointer
            self.assertEqual(choose_what_the_global_shortcut_does("sidebar", "auto_hide", True, sidebar_has_the_keyboard, False, False),
                             "hide_sidebar")

    def test_a_sidebar_set_to_stay_visible_gets_the_keyboard_or_gives_it_back_or_comes_back_if_the_menu_hid_it(self):
        for sidebar_mode in ("floating", "docked"):
            self.assertEqual(choose_what_the_global_shortcut_does("sidebar", sidebar_mode, True, False, False, False), "show_sidebar")
            self.assertEqual(choose_what_the_global_shortcut_does("sidebar", sidebar_mode, True, True, False, False),
                             "give_back_the_sidebars_keyboard")
            # Hidden by the menu bar icon's menu's Show popup or Show window.
            self.assertEqual(choose_what_the_global_shortcut_does("sidebar", sidebar_mode, False, False, False, False), "show_sidebar")

    def test_the_window_hides_only_while_it_is_in_front(self):
        self.assertEqual(choose_what_the_global_shortcut_does("window", "auto_hide", False, False, False, False), "show_window")
        self.assertEqual(choose_what_the_global_shortcut_does("window", "auto_hide", False, False, True, False), "hide_window")


if __name__ == "__main__":
    unittest.main()
