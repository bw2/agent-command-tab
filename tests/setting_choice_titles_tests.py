"""The title tables of the settings window, the Sort pop-up and the list's columns name every
choice that settings_store accepts, so a new choice can't open a window or a row without a title;
and the settings name the agent tools as the adapters do."""

import unittest

from agent_command_tab.agent_adapters import build_built_in_agent_tool_adapters
from agent_command_tab.app_controller import APPEARANCE_NAMES_BY_CHOICE
from agent_command_tab.settings_store import (APPEARANCE_CHOICES, DEFAULT_SETTINGS, GLOBAL_SHORTCUT_CHOICES, LIST_COLUMNS,
                                              LIST_SORT_ORDERS, SHOW_AS_CHOICES, SIDEBAR_EDGES, SIDEBAR_MODES)
from agent_command_tab.user_interface.agent_session_outline_view import COLUMN_NAMES_IN_DROP_ORDER, COLUMN_TITLES, TAB_ROW_COLUMNS
from agent_command_tab.user_interface.popover_container import LIST_SORT_ORDER_TITLES
from agent_command_tab.user_interface.settings_window import (APPEARANCE_TITLES, GLOBAL_SHORTCUT_TITLES,
                                                              LIST_COLUMN_NAMES_IN_SETTINGS_ORDER, SHOW_AS_TITLES,
                                                              SIDEBAR_EDGE_TITLES, SIDEBAR_MODE_TITLES)


class SettingChoiceTitlesTests(unittest.TestCase):
    def test_every_choice_has_a_title_in_the_settings_order(self):
        for titles_by_value, choices in ((SHOW_AS_TITLES, SHOW_AS_CHOICES), (GLOBAL_SHORTCUT_TITLES, GLOBAL_SHORTCUT_CHOICES),
                                         (APPEARANCE_TITLES, APPEARANCE_CHOICES), (APPEARANCE_NAMES_BY_CHOICE, APPEARANCE_CHOICES),
                                         (SIDEBAR_MODE_TITLES, SIDEBAR_MODES),
                                         (SIDEBAR_EDGE_TITLES, SIDEBAR_EDGES), (LIST_SORT_ORDER_TITLES, LIST_SORT_ORDERS)):
            self.assertEqual(list(titles_by_value), choices)
        # The switches are in the order the user asked for, each labeled with its column's title in the list.
        self.assertEqual(sorted(LIST_COLUMN_NAMES_IN_SETTINGS_ORDER), sorted(LIST_COLUMNS))
        self.assertTrue(all(COLUMN_TITLES.get(column_name) for column_name in LIST_COLUMNS))

    def test_the_harnesses_name_the_tools_as_their_adapters_do(self):
        # The Include checkboxes leave tools out by these names (DashboardCore.set_core_settings).
        specific_adapters, _ = build_built_in_agent_tool_adapters()
        self.assertEqual([agent_tool["agent_tool_name"] for agent_tool in DEFAULT_SETTINGS["agent_tools"]],
                         [adapter.agent_tool_name for adapter in specific_adapters])

    def test_tab_rows_show_every_column_and_a_narrow_list_can_drop_them_all_the_state_duration_last(self):
        self.assertEqual([column_name for column_name, parts_field_name in TAB_ROW_COLUMNS if parts_field_name], LIST_COLUMNS)
        self.assertEqual(sorted(COLUMN_NAMES_IN_DROP_ORDER), sorted(column_name for column_name, _ in TAB_ROW_COLUMNS))
        self.assertEqual(COLUMN_NAMES_IN_DROP_ORDER[-1], "state_duration")


if __name__ == "__main__":
    unittest.main()
