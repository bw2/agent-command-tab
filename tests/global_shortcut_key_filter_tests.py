"""The global shortcut's key filter: which key events it holds back, and how it handles macOS
refusing or turning off the filter. No real filter is made: CoreGraphics calls are patched."""

import unittest
import unittest.mock

from agent_command_tab import global_shortcut_key_filter
from agent_command_tab.global_shortcut_key_filter import (COMMAND_FLAG, CONTROL_FLAG, EXTRA_MODIFIER_FLAG_BY_GLOBAL_SHORTCUT,
                                                          FN_FLAG, KEY_CODE_FIELD, KEY_DOWN_EVENT, KEY_UP_EVENT,
                                                          MODIFIER_KEYS_CHANGED_EVENT, OPTION_FLAG, SHIFT_FLAG,
                                                          START_KEY_FILTER_STATE, TAB_KEY_CODE, GlobalShortcutKeyFilter,
                                                          decide_what_to_do_with_key_event)
from agent_command_tab.settings_store import GLOBAL_SHORTCUT_CHOICES

LETTER_A_KEY_CODE = 0


def play_key_events(key_events, global_shortcut="shift_command_tab"):
    """Runs (event_type, key_code, flags) events through the filter's decisions, the way macOS
    would send them; returns each what_to_do and the state after the last one."""
    state = START_KEY_FILTER_STATE
    decisions = []
    for event_type, key_code, flags in key_events:
        what_to_do, state = decide_what_to_do_with_key_event(event_type, key_code, flags, False, global_shortcut, state)
        decisions.append(what_to_do)
    return decisions, state


def press_tab(flags):
    return [(KEY_DOWN_EVENT, TAB_KEY_CODE, flags), (KEY_UP_EVENT, TAB_KEY_CODE, flags)]


class KeyFilterDecisionTests(unittest.TestCase):
    def test_every_shortcut_choice_but_off_has_its_modifier_key(self):
        self.assertEqual(list(EXTRA_MODIFIER_FLAG_BY_GLOBAL_SHORTCUT) + ["off"], GLOBAL_SHORTCUT_CHOICES)

    def test_the_chosen_shortcut_is_held_back_with_its_release_and_shows_the_list(self):
        decisions, state = play_key_events(press_tab(COMMAND_FLAG | SHIFT_FLAG))
        self.assertEqual(decisions, ["hold_back_and_show_the_list", "hold_back"])
        self.assertEqual(state, START_KEY_FILTER_STATE)
        decisions, _ = play_key_events(press_tab(COMMAND_FLAG | FN_FLAG), global_shortcut="command_fn_tab")
        self.assertEqual(decisions, ["hold_back_and_show_the_list", "hold_back"])

    def test_a_key_repeat_of_the_shortcut_is_held_back_without_showing_the_list_again(self):
        what_to_do, _ = decide_what_to_do_with_key_event(KEY_DOWN_EVENT, TAB_KEY_CODE, COMMAND_FLAG | SHIFT_FLAG, True,
                                                         "shift_command_tab", START_KEY_FILTER_STATE)
        self.assertEqual(what_to_do, "hold_back")

    def test_a_held_back_press_keeps_its_repeats_held_back_after_command_is_let_go(self):
        # Shift-Command-Tab held until Tab repeats, then Command let go before Tab.
        state = START_KEY_FILTER_STATE
        decisions = []
        for event_type, flags, is_key_repeat in [(KEY_DOWN_EVENT, COMMAND_FLAG | SHIFT_FLAG, False),
                                                 (KEY_DOWN_EVENT, COMMAND_FLAG | SHIFT_FLAG, True),
                                                 (MODIFIER_KEYS_CHANGED_EVENT, SHIFT_FLAG, False),
                                                 (KEY_DOWN_EVENT, SHIFT_FLAG, True), (KEY_UP_EVENT, SHIFT_FLAG, False),
                                                 (KEY_DOWN_EVENT, SHIFT_FLAG, False)]:
            key_code = 56 if event_type == MODIFIER_KEYS_CHANGED_EVENT else TAB_KEY_CODE
            what_to_do, state = decide_what_to_do_with_key_event(event_type, key_code, flags, is_key_repeat,
                                                                 "shift_command_tab", state)
            decisions.append(what_to_do)
        self.assertEqual(decisions, ["hold_back_and_show_the_list", "hold_back", "let_through", "hold_back", "hold_back",
                                     "let_through"])

    def test_the_shortcut_goes_to_the_app_switcher_while_it_is_open(self):
        # Hold Command, Tab opens the app switcher, Shift-Tab moves back in it, then Command is let go.
        key_events = ([(MODIFIER_KEYS_CHANGED_EVENT, 55, COMMAND_FLAG)] + press_tab(COMMAND_FLAG)
                      + press_tab(COMMAND_FLAG | SHIFT_FLAG) + [(MODIFIER_KEYS_CHANGED_EVENT, 55, 0)]
                      + press_tab(COMMAND_FLAG | SHIFT_FLAG))
        decisions, _ = play_key_events(key_events)
        self.assertEqual(decisions, ["let_through"] * 6 + ["hold_back_and_show_the_list", "hold_back"])

    def test_the_other_command_tab_combination_opens_the_app_switcher_and_is_let_through(self):
        # With Shift-Command-Tab chosen, Command-Fn-Tab still opens the app switcher, so the
        # Shift-Command-Tab that follows while Command is held goes to it.
        decisions, state = play_key_events(press_tab(COMMAND_FLAG | FN_FLAG) + press_tab(COMMAND_FLAG | SHIFT_FLAG))
        self.assertEqual(decisions, ["let_through"] * 4)
        self.assertTrue(state.app_switcher_is_open)

    def test_other_keys_and_tab_with_control_or_option_are_let_through(self):
        key_events = ([(KEY_DOWN_EVENT, LETTER_A_KEY_CODE, COMMAND_FLAG | SHIFT_FLAG)] + press_tab(0) + press_tab(SHIFT_FLAG)
                      + press_tab(COMMAND_FLAG | SHIFT_FLAG | CONTROL_FLAG) + press_tab(COMMAND_FLAG | SHIFT_FLAG | OPTION_FLAG))
        decisions, state = play_key_events(key_events)
        self.assertEqual(decisions, ["let_through"] * 9)
        self.assertEqual(state, START_KEY_FILTER_STATE)

    def test_nothing_is_held_back_while_the_shortcut_is_off(self):
        decisions, _ = play_key_events(press_tab(COMMAND_FLAG | SHIFT_FLAG) + press_tab(COMMAND_FLAG | FN_FLAG),
                                       global_shortcut="off")
        self.assertEqual(decisions, ["let_through"] * 4)

    def test_a_held_back_press_has_its_release_held_back_even_after_command_is_let_go(self):
        key_events = [(KEY_DOWN_EVENT, TAB_KEY_CODE, COMMAND_FLAG | SHIFT_FLAG), (MODIFIER_KEYS_CHANGED_EVENT, 55, SHIFT_FLAG),
                      (KEY_UP_EVENT, TAB_KEY_CODE, SHIFT_FLAG), (KEY_UP_EVENT, TAB_KEY_CODE, 0)]
        decisions, _ = play_key_events(key_events)
        self.assertEqual(decisions, ["hold_back_and_show_the_list", "let_through", "hold_back", "let_through"])


class KeyFilterTests(unittest.TestCase):
    def test_a_refused_filter_lacks_permission_until_the_shortcut_is_turned_off(self):
        key_filter = GlobalShortcutKeyFilter(lambda: None)
        self.assertFalse(key_filter.lacks_permission())  # off at first
        with unittest.mock.patch.object(global_shortcut_key_filter.CORE_GRAPHICS, "CGEventTapCreate", return_value=None) as create:
            key_filter.use_global_shortcut("shift_command_tab")
            self.assertTrue(key_filter.lacks_permission())
            key_filter.use_global_shortcut("command_fn_tab")  # tries again
            self.assertEqual(create.call_count, 2)
            key_filter.use_global_shortcut("off")
            self.assertFalse(key_filter.lacks_permission())

    def test_a_filter_macos_turned_off_is_turned_back_on_lets_the_event_through_and_starts_over(self):
        key_filter = GlobalShortcutKeyFilter(lambda: None)
        key_filter.global_shortcut = "shift_command_tab"
        key_filter.tap = 1234  # stands in for a made filter
        key_filter.state = START_KEY_FILTER_STATE._replace(app_switcher_is_open=True, tab_release_is_held_back=True)
        with unittest.mock.patch.object(global_shortcut_key_filter.CORE_GRAPHICS, "CGEventTapEnable") as enable:
            self.assertEqual(key_filter.filter_key_event(None, 0xFFFFFFFE, 5678, None), 5678)
        enable.assert_called_once_with(1234, True)
        self.assertEqual(key_filter.state, START_KEY_FILTER_STATE)  # key events were missed meanwhile

    def test_turning_the_shortcut_back_on_starts_over_but_switching_choices_does_not(self):
        key_filter = GlobalShortcutKeyFilter(lambda: None)
        key_filter.global_shortcut = "shift_command_tab"
        key_filter.tap = 1234  # stands in for a made filter
        stale_state = START_KEY_FILTER_STATE._replace(tab_release_is_held_back=True)
        with unittest.mock.patch.object(global_shortcut_key_filter.CORE_GRAPHICS, "CGEventTapEnable") as enable:
            key_filter.state = stale_state
            key_filter.use_global_shortcut("command_fn_tab")
            self.assertEqual(key_filter.state, stale_state)  # on all along: the filter's thread keeps it
            key_filter.use_global_shortcut("off")
            key_filter.use_global_shortcut("shift_command_tab")
            self.assertEqual(key_filter.state, START_KEY_FILTER_STATE)
        self.assertEqual(enable.call_args_list[-2:], [unittest.mock.call(1234, False), unittest.mock.call(1234, True)])

    def test_an_error_while_deciding_lets_the_key_through(self):
        key_filter = GlobalShortcutKeyFilter(lambda: None)
        event_reader = global_shortcut_key_filter.CORE_GRAPHICS_KEEPING_THE_PYTHON_LOCK
        with unittest.mock.patch.object(event_reader, "CGEventGetIntegerValueField", return_value=0), \
                unittest.mock.patch.object(event_reader, "CGEventGetFlags", return_value=0), \
                unittest.mock.patch.object(global_shortcut_key_filter, "decide_what_to_do_with_key_event", side_effect=ValueError), \
                unittest.mock.patch("builtins.print"):
            self.assertEqual(key_filter.filter_key_event(None, KEY_DOWN_EVENT, 5678, None), 5678)

    def test_the_first_press_of_the_shortcut_calls_back_and_is_held_back(self):
        presses = []
        key_filter = GlobalShortcutKeyFilter(lambda: presses.append("pressed"))
        key_filter.global_shortcut = "shift_command_tab"
        event_reader = global_shortcut_key_filter.CORE_GRAPHICS_KEEPING_THE_PYTHON_LOCK
        with unittest.mock.patch.object(event_reader, "CGEventGetIntegerValueField",
                                        side_effect=lambda event, field: TAB_KEY_CODE if field == KEY_CODE_FIELD else 0), \
                unittest.mock.patch.object(event_reader, "CGEventGetFlags", return_value=COMMAND_FLAG | SHIFT_FLAG):
            self.assertIsNone(key_filter.filter_key_event(None, KEY_DOWN_EVENT, 5678, None))
        self.assertEqual(presses, ["pressed"])


if __name__ == "__main__":
    unittest.main()
