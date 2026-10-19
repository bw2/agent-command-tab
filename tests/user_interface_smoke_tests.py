"""Builds the list from a sample snapshot in an off-screen window (PLAN.md section 17, UI smoke test).

Set LLM_SIDEBAR_DASHBOARD_SMOKE_TEST_IMAGE=/path/to/file.png to also save what the list looks like.
"""

import os
import time
import unittest
import unittest.mock

from AppKit import (NSApplication, NSApplicationActivationPolicyAccessory, NSBackingStoreBuffered,
                    NSBitmapImageFileTypePNG, NSColorSpace, NSDragOperationMove, NSDragOperationNone, NSMakeRect,
                    NSMenu, NSParagraphStyleAttributeName, NSTextAlignmentRight, NSWindow, NSWindowStyleMaskBorderless)
from Foundation import NSIndexSet

from llm_sidebar_dashboard.settings_store import DEFAULT_SETTINGS, ICON_COLOR_NAMES
from llm_sidebar_dashboard.snapshot_data_classes import AgentSessionEntry, ComputerSnapshot, MonitorEntry, WindowEntry
from llm_sidebar_dashboard.user_interface.popover_container import (BOTTOM_BAR_HEIGHT, POPOVER_WIDTH, TOP_BAR_HEIGHT,
                                                                    ListPanelController)
from llm_sidebar_dashboard.user_interface.agent_session_outline_view import (ROW_HEIGHT, TAB_ROW_HEIGHT_WITH_PREVIEW,
                                                                             AgentSessionListController)
from llm_sidebar_dashboard.user_interface.menu_bar_status_item import ICON_COLORS_BY_NAME, choose_automatic_icon_color_name
from llm_sidebar_dashboard.user_interface.settings_window import SettingsWindowController
from llm_sidebar_dashboard.user_interface.sidebar_panel import (SidebarController,
                                                                allow_pointer_changes_while_another_app_is_in_front)
from llm_sidebar_dashboard.user_interface.sidebar_placement import SidebarPlacement


class RecordingOwner:
    """Stands in for the app controller and records what the list asks it to do."""

    def __init__(self):
        self.submitted_actions = []
        self.started_agent_sessions = []
        self.windows_asked_about_closing = []
        self.answer_to_closing_window = False
        self.peeks = []
        self.chosen_list_sort_orders = []

    def submit_action(self, action_request):
        self.submitted_actions.append(action_request)

    def tab_was_focused(self, list_controller):
        pass

    def rows_changed(self, list_controller):
        pass

    def list_agent_tool_names(self):
        return ["Claude Code", "Codex"]

    def start_agent_session_from_menu(self, window_id, folder, agent_tool_name):
        self.started_agent_sessions.append((window_id, folder, agent_tool_name))

    def find_launch_command(self, agent_tool_name):
        return {"Claude Code": "claude", "Codex": "codex"}.get(agent_tool_name, "")

    def start_peek(self, session_id):
        self.peeks.append(("start", session_id))

    def end_peek(self):
        self.peeks.append(("end",))

    def confirm_closing_window(self, window_entry):
        self.windows_asked_about_closing.append(window_entry.window_id)
        return self.answer_to_closing_window

    def list_sort_order_chosen(self, list_sort_order):
        self.chosen_list_sort_orders.append(list_sort_order)


def make_entry(tab_id, title, status, tool="Claude Code", is_in_front=False):
    return AgentSessionEntry(tab_id=tab_id, session_id=f"s{tab_id}", tab_title=title, tab_title_set_by_user=False,
                             agent_session_name=title, agent_tool_name=tool, working_folder="/x", status=status,
                             status_since=0.0, is_in_front=is_in_front)


def make_sample_snapshot(second_window_tabs=2):
    window_one = WindowEntry("w1", "llm-sidebar-dashboard", False, (0, 0, 800, 600), None, 4, (
        make_entry("1", "planning", "working", is_in_front=True),
        make_entry("2", "phase0-tests", "asking"),
        make_entry("3", "codex-review", "done", tool="Codex"),
    ))
    window_two = WindowEntry("w2", "tandem-repeats", False, (0, 0, 800, 600), None, 2, tuple(
        make_entry(str(10 + index), f"analysis-{index}", "idle") for index in range(second_window_tabs)))
    monitor = MonitorEntry("1", "Built-in", (0, 0, 2000, 1300), (0, 0, 2000, 1260), (window_one, window_two))
    return ComputerSnapshot("c", "Test Mac", 1.0, (monitor,))


def make_two_monitor_snapshot(one_monitor_snapshot):
    window_one, window_two = one_monitor_snapshot.monitors[0].windows
    return one_monitor_snapshot._replace(monitors=(
        one_monitor_snapshot.monitors[0]._replace(windows=(window_one,)),
        MonitorEntry("2", "Studio Display", (2000, 0, 2560, 1440), (2000, 0, 2560, 1400), (window_two,)),
    ))


class UserInterfaceSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        NSApplication.sharedApplication().setActivationPolicy_(NSApplicationActivationPolicyAccessory)

    def setUp(self):
        self.owner = RecordingOwner()
        self.submitted_actions = self.owner.submitted_actions
        self.list_controller = AgentSessionListController.alloc().initWithOwner_(self.owner)
        self.list_controller.refresh_timer.invalidate()
        # The real dropdown panel, so the test covers its layout (for example the column width).
        self.dropdown_panel_controller = ListPanelController.alloc().initWithListController_(self.list_controller)
        self.window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(-4000, -4000, POPOVER_WIDTH, 260), NSWindowStyleMaskBorderless, NSBackingStoreBuffered, False)
        self.window.setContentView_(self.dropdown_panel_controller.view())
        self.window.orderFrontRegardless()  # "on screen" for the list, though far outside every monitor

    def tearDown(self):
        self.window.orderOut_(None)

    def test_rows_follow_the_snapshot_and_keep_their_items(self):
        self.list_controller.apply_snapshot(make_sample_snapshot())
        outline_view = self.list_controller.outline_view
        self.assertEqual(outline_view.numberOfRows(), 7)  # 2 windows + 5 tabs, all expanded
        first_tab_item = outline_view.itemAtRow_(1)
        self.assertEqual(first_tab_item.kind, "tab")
        self.list_controller.toggle_expansion(outline_view.itemAtRow_(4))  # collapse the second window
        self.assertEqual(outline_view.numberOfRows(), 5)
        self.list_controller.apply_snapshot(make_sample_snapshot(second_window_tabs=3))  # structure changes
        self.assertEqual(outline_view.numberOfRows(), 5)  # the second window stays collapsed
        self.assertIs(outline_view.itemAtRow_(1), first_tab_item)  # same item object, so selection survives

    def test_collapse_all_and_expand_all(self):
        self.list_controller.apply_snapshot(make_sample_snapshot())
        self.list_controller.set_all_expanded(False)
        self.assertEqual(self.list_controller.outline_view.numberOfRows(), 2)
        self.list_controller.set_all_expanded(True)
        self.assertEqual(self.list_controller.outline_view.numberOfRows(), 7)

    def test_two_monitors_start_fully_expanded(self):
        self.list_controller.apply_snapshot(make_two_monitor_snapshot(make_sample_snapshot()))
        self.assertEqual(self.list_controller.outline_view.numberOfRows(), 9)  # 2 monitors, 2 windows, 5 tabs

    def test_collapsing_a_monitor_leaves_its_windows_own_state_alone(self):
        self.list_controller.apply_snapshot(make_two_monitor_snapshot(make_sample_snapshot()))
        outline_view = self.list_controller.outline_view
        self.list_controller.toggle_expansion(outline_view.itemAtRow_(0))
        self.assertEqual(self.list_controller.collapsed_item_keys, {("monitor", "1")})
        self.list_controller.apply_snapshot(make_two_monitor_snapshot(make_sample_snapshot(second_window_tabs=3)))
        self.list_controller.toggle_expansion(outline_view.itemAtRow_(0))
        self.assertEqual(outline_view.numberOfRows(), 10)  # everything expanded again, w2 now has 3 tabs

    def test_collapsing_through_the_outline_view_itself_is_recorded(self):
        # NSOutlineView's own Left arrow handling calls collapseItem_ like this.
        self.list_controller.apply_snapshot(make_sample_snapshot())
        outline_view = self.list_controller.outline_view
        outline_view.collapseItem_(outline_view.itemAtRow_(0))
        self.assertIn(("window", "w1"), self.list_controller.collapsed_item_keys)
        self.assertEqual(self.list_controller.build_row_text_parts(outline_view.itemAtRow_(0)).details_text,
                         "1 asking, 1 working, 1 done")
        self.list_controller.apply_snapshot(make_sample_snapshot(second_window_tabs=3))
        self.assertEqual(outline_view.numberOfRows(), 5)  # w1 stays collapsed: 2 windows + 3 tabs of w2

    def test_selection_survives_a_structural_reload(self):
        self.list_controller.apply_snapshot(make_sample_snapshot())
        outline_view = self.list_controller.outline_view
        selected_item = outline_view.itemAtRow_(5)
        outline_view.selectRowIndexes_byExtendingSelection_(NSIndexSet.indexSetWithIndex_(5), False)
        self.list_controller.apply_snapshot(make_sample_snapshot(second_window_tabs=3))
        self.assertIs(outline_view.itemAtRow_(outline_view.selectedRow()), selected_item)

    def test_dropping_a_tab_back_in_place_moves_nothing(self):
        self.list_controller.apply_snapshot(make_sample_snapshot())
        outline_view = self.list_controller.outline_view
        window_item = outline_view.itemAtRow_(0)
        self.list_controller.dragged_item = window_item.child_items[1]
        for child_index in (1, 2):
            self.list_controller.outlineView_acceptDrop_item_childIndex_(outline_view, None, window_item, child_index)
        self.assertEqual(self.submitted_actions, [])
        self.list_controller.outlineView_acceptDrop_item_childIndex_(outline_view, None, window_item, 0)
        self.assertEqual(self.submitted_actions[-1].arguments["drop_before_tab_id"], "1")

    def test_sort_orders_change_only_the_order_of_the_rows(self):
        self.list_controller.apply_snapshot(make_sample_snapshot())
        outline_view = self.list_controller.outline_view

        def list_first_window_tab_titles():
            return [outline_view.itemAtRow_(row).entry.tab_title for row in range(1, 4)]
        self.assertEqual(list_first_window_tab_titles(), ["planning", "phase0-tests", "codex-review"])
        self.list_controller.set_list_sort_order("activity")  # asking, working, done
        self.assertEqual(list_first_window_tab_titles(), ["phase0-tests", "planning", "codex-review"])
        self.list_controller.set_list_sort_order("alphabet")
        self.assertEqual(list_first_window_tab_titles(), ["codex-review", "phase0-tests", "planning"])
        self.list_controller.set_list_sort_order("manual")  # iTerm2's own order, unchanged
        self.assertEqual(list_first_window_tab_titles(), ["planning", "phase0-tests", "codex-review"])

    def test_sorted_rows_allow_moves_to_another_window_or_monitor_but_no_reordering(self):
        self.list_controller.apply_snapshot(make_sample_snapshot())
        self.list_controller.set_list_sort_order("alphabet")
        outline_view = self.list_controller.outline_view
        first_window_item, second_window_item = self.list_controller.top_level_items
        self.list_controller.dragged_item = first_window_item.child_items[0]
        self.assertEqual(self.list_controller.outlineView_validateDrop_proposedItem_proposedChildIndex_(
            outline_view, None, first_window_item, 1), NSDragOperationNone)  # within its own window
        self.assertEqual(self.list_controller.outlineView_validateDrop_proposedItem_proposedChildIndex_(
            outline_view, None, second_window_item.child_items[0], -1), NSDragOperationMove)  # into the other window
        self.list_controller.outlineView_acceptDrop_item_childIndex_(outline_view, None, second_window_item, -1)
        self.assertEqual((self.submitted_actions[-1].action, self.submitted_actions[-1].arguments),
                         ("move_tab", {"target_window_id": "w2", "drop_before_tab_id": None}))  # to its end
        self.list_controller.dragged_item = second_window_item
        self.assertEqual(self.list_controller.outlineView_validateDrop_proposedItem_proposedChildIndex_(
            outline_view, None, None, 0), NSDragOperationNone)  # windows keep the order set in Manual

    def test_shown_columns_follow_the_settings(self):
        snapshot = make_sample_snapshot()
        window_one = snapshot.monitors[0].windows[0]
        started_entries = tuple(entry._replace(started_at=time.time() - 3700) for entry in window_one.agent_sessions)
        snapshot = snapshot._replace(monitors=(snapshot.monitors[0]._replace(
            windows=(window_one._replace(agent_sessions=started_entries), snapshot.monitors[0].windows[1])),))
        self.list_controller.apply_snapshot(snapshot)
        self.window.contentView().layoutSubtreeIfNeeded()
        row_view = self.list_controller.outline_view.viewAtColumn_row_makeIfNecessary_(0, 1, True)
        self.assertEqual(self.list_controller.created_time_column_width, 0)  # off by default
        self.list_controller.set_shown_columns(["created_time", "existence_duration", "state_duration"])
        row_view.layoutSubtreeIfNeeded()
        self.assertEqual([not view.isHidden() for view in (row_view.created_time_text_field, row_view.existence_duration_text_field,
                                                            row_view.state_text_field, row_view.elapsed_time_text_field)],
                         [True, True, False, True])
        self.assertEqual(row_view.existence_duration_text_field.stringValue(), "1h 1m")
        self.list_controller.set_shown_columns([])
        row_view.layoutSubtreeIfNeeded()
        self.assertTrue(row_view.elapsed_time_text_field.isHidden())
        # Only the tool icon column is left: it shows by itself, since Claude Code and Codex both run.
        self.assertEqual(self.list_controller.measure_right_columns_width(), self.list_controller.icon_column_width)

    def test_top_bar_drops_the_sort_label_then_the_sort_popup_when_narrow(self):
        panel = self.dropdown_panel_controller
        # Narrowed and widened again, as dragging the sidebar's edge does; 320 is the dropdown's
        # smallest width and 200 the sidebar's (180 can't happen, but checks the last step).
        for width, label_shown, popup_shown in ((460, True, True), (320, True, True), (220, True, True), (210, False, True),
                                                (200, False, True), (180, False, False), (460, True, True)):
            self.window.setContentSize_((width, 260))
            self.assertEqual((not panel.sort_label.isHidden(), not panel.list_sort_order_popup.isHidden()),
                             (label_shown, popup_shown), width)
            right_buttons_x = panel.expand_or_collapse_all_button.frame().origin.x  # then the settings button
            if popup_shown:
                popup_frame = panel.list_sort_order_popup.frame()
                self.assertLessEqual(popup_frame.origin.x + popup_frame.size.width, right_buttons_x)
            message_frame = panel.message_text_field.frame()
            self.assertTrue(message_frame.size.width == 0 or message_frame.origin.x + message_frame.size.width <= right_buttons_x,
                            width)  # never covers the buttons
        self.assertGreater(panel.message_text_field.frame().size.width, 100)  # room again for a connection message
        self.window.setContentSize_((320, 260))
        panel.show_message("iTerm2 can't be reached; retrying")  # the sort control steps aside for it
        self.assertTrue(panel.list_sort_order_popup.isHidden())
        self.assertGreater(panel.message_text_field.frame().size.width, 100)
        panel.show_message("")
        self.assertFalse(panel.list_sort_order_popup.isHidden())

    def test_sort_popup_reports_the_choice_to_the_owner(self):
        self.dropdown_panel_controller.list_sort_order_popup.selectItemWithTitle_("Activity")
        self.dropdown_panel_controller.listSortOrderChosen_(self.dropdown_panel_controller.list_sort_order_popup)
        self.assertEqual(self.owner.chosen_list_sort_orders, ["activity"])
        self.dropdown_panel_controller.show_list_sort_order("alphabet")
        self.assertEqual(self.dropdown_panel_controller.list_sort_order_popup.titleOfSelectedItem(), "Alphabetically")
        self.assertEqual(self.list_controller.list_sort_order, "alphabet")

    def test_window_menu_offers_new_agent_sessions_in_the_window_folder(self):
        self.list_controller.apply_snapshot(make_sample_snapshot())
        window_item = self.list_controller.outline_view.itemAtRow_(0)
        self.list_controller.menu_target_item = window_item
        menu_item = type("MenuItemStandIn", (), {"representedObject": lambda self: "Codex"})()
        self.list_controller.menuNewAgentSessionInWindow_(menu_item)
        self.assertEqual(self.owner.started_agent_sessions, [("w1", "/x", "Codex")])

    def build_right_click_menu_titles(self, row):
        """Returns the menu's item titles, with "---" for separators, as if the row were right-clicked."""
        item = self.list_controller.outline_view.itemAtRow_(row)
        self.list_controller.find_clicked_item = lambda: item
        menu = NSMenu.alloc().initWithTitle_("")
        self.list_controller.menuNeedsUpdate_(menu)
        return ["---" if menu_item.isSeparatorItem() else menu_item.title() for menu_item in menu.itemArray()]

    def test_window_and_tab_right_click_menus(self):
        self.list_controller.apply_snapshot(make_sample_snapshot())
        self.assertEqual(self.build_right_click_menu_titles(0),
                         ["Collapse", "---", "Show window", "Rename", "---", "New tab with Claude Code session…",
                          "New tab with Codex session…", "---", "Close window"])
        self.assertEqual(self.build_right_click_menu_titles(1),
                         ["Show tab", "Rename", "Duplicate this tab", "Move to window", "Extract to new window"])

    def test_double_click_on_a_window_shows_it_without_renaming_or_collapsing(self):
        self.list_controller.apply_snapshot(make_sample_snapshot())
        window_item = self.list_controller.outline_view.itemAtRow_(0)
        self.list_controller.find_clicked_item = lambda: window_item
        self.list_controller.rowClicked_(None)
        self.assertEqual(self.list_controller.collapsed_item_keys, set())  # a single click doesn't collapse
        self.list_controller.rowDoubleClicked_(None)
        self.assertEqual([(request.action, request.target) for request in self.submitted_actions],
                         [("focus_window", {"window_id": "w1"})])  # with the tab it already shows
        self.assertFalse(self.list_controller.rename_in_progress)
        self.assertEqual(self.list_controller.collapsed_item_keys, set())

    def test_single_click_on_a_tab_does_nothing_and_double_click_shows_it(self):
        self.list_controller.apply_snapshot(make_sample_snapshot())
        tab_item = self.list_controller.outline_view.itemAtRow_(1)
        self.list_controller.find_clicked_item = lambda: tab_item
        self.list_controller.rowClicked_(None)
        self.assertEqual(self.submitted_actions, [])
        self.list_controller.rowDoubleClicked_(None)
        self.assertEqual([(request.action, request.target) for request in self.submitted_actions],
                         [("focus_agent_session", {"session_id": "s1"})])

    def test_unchanged_rows_are_not_refilled_and_hidden_lists_are_not_redrawn(self):
        self.list_controller.apply_snapshot(make_sample_snapshot())
        self.window.contentView().layoutSubtreeIfNeeded()
        row_view = self.list_controller.outline_view.viewAtColumn_row_makeIfNecessary_(0, 5, False)  # an idle tab
        filled_look = row_view.look
        row_view.name_text_field.setStringValue_("changed behind the list's back")
        self.list_controller.redraw_visible_rows()
        self.assertEqual(row_view.name_text_field.stringValue(), "changed behind the list's back")  # skipped
        self.window.orderOut_(None)
        row_view.look = None
        self.list_controller.redraw_visible_rows()
        self.assertEqual(row_view.name_text_field.stringValue(), "changed behind the list's back")  # hidden: skipped
        self.list_controller.prepare_to_appear()
        self.assertEqual((row_view.name_text_field.stringValue(), row_view.look), ("analysis-0", filled_look))

    def test_a_confirmed_rename_shows_the_new_name_at_once(self):
        self.list_controller.apply_snapshot(make_sample_snapshot())
        self.window.contentView().layoutSubtreeIfNeeded()
        tab_item = self.list_controller.outline_view.itemAtRow_(1)
        self.list_controller.begin_rename(tab_item)
        row_view = self.list_controller.outline_view.viewAtColumn_row_makeIfNecessary_(0, 1, False)
        row_view.name_text_field.setStringValue_("new name")
        notification = type("NotificationStandIn", (), {"object": lambda self: row_view.name_text_field})()
        self.list_controller.controlTextDidEndEditing_(notification)
        self.assertEqual(row_view.name_text_field.stringValue(), "new name")
        self.assertEqual(tab_item.entry.tab_title, "new name")
        self.assertEqual(self.submitted_actions[-1].action, "rename_tab")

    def test_holding_a_tab_row_peeks_and_releasing_ends_it_but_a_click_does_not(self):
        self.list_controller.apply_snapshot(make_sample_snapshot())
        self.list_controller.row_pressed(1)
        self.list_controller.row_released()  # a click: released before the hold time
        self.assertEqual(self.owner.peeks, [])
        self.list_controller.row_pressed(1)
        self.list_controller.peekTimerFired_(self.list_controller.peek_timer)  # held long enough
        self.list_controller.row_released()
        self.assertEqual(self.owner.peeks, [("start", "s1"), ("end",)])
        self.list_controller.row_pressed(0)  # a window row doesn't peek
        self.assertIsNone(self.list_controller.peek_timer)
        self.list_controller.row_released()

    def test_a_double_click_shows_the_tab_under_its_first_click_even_if_the_rows_moved(self):
        self.list_controller.apply_snapshot(make_sample_snapshot())
        outline_view = self.list_controller.outline_view
        first_click_item, other_item = outline_view.itemAtRow_(1), outline_view.itemAtRow_(2)
        self.list_controller.find_clicked_item = lambda: first_click_item
        self.list_controller.rowClicked_(None)  # the first click
        self.list_controller.find_clicked_item = lambda: other_item  # the rows moved under the pointer
        self.list_controller.rowDoubleClicked_(None)
        self.assertEqual((self.submitted_actions[-1].action, self.submitted_actions[-1].target),
                         ("focus_agent_session", {"session_id": first_click_item.entry.session_id}))

    def test_rows_dont_reorder_under_a_held_button(self):
        # Activity order: a peek marks a "done" tab as seen ("idle"), which would move its row.
        self.list_controller.set_list_sort_order("activity")
        self.list_controller.apply_snapshot(make_sample_snapshot())
        outline_view = self.list_controller.outline_view
        held_item = outline_view.itemAtRow_(1)
        self.list_controller.row_pressed(1)
        snapshot = make_sample_snapshot()
        window_one = snapshot.monitors[0].windows[0]
        seen_entries = tuple(entry._replace(status="idle") if entry.tab_id == held_item.item_id else entry
                             for entry in window_one.agent_sessions)
        self.list_controller.apply_snapshot(snapshot._replace(monitors=(snapshot.monitors[0]._replace(
            windows=(window_one._replace(agent_sessions=seen_entries), snapshot.monitors[0].windows[1])),)))
        self.assertIs(outline_view.itemAtRow_(1), held_item)  # held back while the button is down
        self.list_controller.row_released()
        self.assertIsNot(outline_view.itemAtRow_(1), held_item)  # applied on release

    def test_a_narrow_sidebar_drops_the_context_then_the_icon_column_to_keep_room_for_names(self):
        snapshot = make_sample_snapshot()
        window_one = snapshot.monitors[0].windows[0]
        window_one = window_one._replace(agent_sessions=tuple(entry._replace(context_tokens=40_546) for entry in window_one.agent_sessions))
        snapshot = snapshot._replace(monitors=(snapshot.monitors[0]._replace(windows=(window_one, snapshot.monitors[0].windows[1])),))
        self.list_controller.width_is_set_by_the_user = True
        self.window.setContentSize_((560, 260))
        self.list_controller.apply_snapshot(snapshot)
        self.assertGreater(self.list_controller.context_column_width, 0)
        self.assertGreater(self.list_controller.icon_column_width, 0)
        self.window.setContentSize_((200, 260))  # the narrowest sidebar
        self.window.contentView().layoutSubtreeIfNeeded()
        self.list_controller.measure_right_columns()
        self.assertEqual((self.list_controller.context_column_width, self.list_controller.icon_column_width), (0, 0))

    def test_duplicate_is_greyed_out_for_a_tool_without_a_launch_command(self):
        snapshot = make_sample_snapshot()
        window_two = snapshot.monitors[0].windows[1]
        window_two = window_two._replace(agent_sessions=(window_two.agent_sessions[0]._replace(agent_tool_name="aider"),)
                                         + window_two.agent_sessions[1:])
        self.list_controller.apply_snapshot(snapshot._replace(monitors=(snapshot.monitors[0]._replace(
            windows=(snapshot.monitors[0].windows[0], window_two)),)))
        item = self.list_controller.outline_view.itemAtRow_(5)  # the "aider" tab
        self.list_controller.find_clicked_item = lambda: item
        menu = NSMenu.alloc().initWithTitle_("")
        self.list_controller.menuNeedsUpdate_(menu)
        duplicate_item = next(menu_item for menu_item in menu.itemArray() if menu_item.title() == "Duplicate this tab")
        self.assertFalse(duplicate_item.isEnabled())

    def test_closing_the_dropdown_cancels_an_open_rename_so_the_list_keeps_updating(self):
        self.list_controller.apply_snapshot(make_sample_snapshot())
        self.window.contentView().layoutSubtreeIfNeeded()
        self.list_controller.begin_rename(self.list_controller.outline_view.itemAtRow_(1))
        self.assertTrue(self.list_controller.rename_in_progress)
        self.list_controller.cancel_rename()
        self.assertFalse(self.list_controller.rename_in_progress)
        self.assertEqual(self.submitted_actions, [])  # nothing was renamed

    def test_expand_or_collapse_from_the_right_click_menu(self):
        self.list_controller.apply_snapshot(make_sample_snapshot())
        self.list_controller.menu_target_item = self.list_controller.outline_view.itemAtRow_(0)
        self.list_controller.menuToggleExpansion_(None)
        self.assertIn(("window", "w1"), self.list_controller.collapsed_item_keys)
        self.assertEqual(self.build_right_click_menu_titles(0)[0], "Expand")

    def test_close_window_only_after_the_user_says_yes(self):
        self.list_controller.apply_snapshot(make_sample_snapshot())
        self.list_controller.menu_target_item = self.list_controller.outline_view.itemAtRow_(0)
        self.list_controller.menuCloseWindow_(None)
        self.assertEqual((self.owner.windows_asked_about_closing, self.submitted_actions), (["w1"], []))
        self.owner.answer_to_closing_window = True
        self.list_controller.menuCloseWindow_(None)
        self.assertEqual([(request.action, request.target) for request in self.submitted_actions],
                         [("close_window", {"window_id": "w1"})])

    def test_new_agent_session_uses_the_folder_of_the_windows_selected_tab(self):
        # A background window (nothing in front) whose selected tab is its second agent session.
        window = WindowEntry("w3", "two projects", False, (0, 0, 800, 600), None, 2, (
            make_entry("31", "photos", "idle")._replace(working_folder="/code/photos"),
            make_entry("32", "other", "idle")._replace(working_folder="/tmp/other-project"),
        ), selected_tab_id="32")
        monitor = MonitorEntry("1", "Built-in", (0, 0, 2000, 1300), (0, 0, 2000, 1260), (window,))
        self.list_controller.apply_snapshot(ComputerSnapshot("c", "Test Mac", 1.0, (monitor,)))
        self.list_controller.menu_target_item = self.list_controller.outline_view.itemAtRow_(0)
        menu_item = type("MenuItemStandIn", (), {"representedObject": lambda self: "Claude Code"})()
        self.list_controller.menuNewAgentSessionInWindow_(menu_item)
        self.assertEqual(self.owner.started_agent_sessions, [("w3", "/tmp/other-project", "Claude Code")])

    def test_dropping_a_window_on_another_monitor_moves_it_there(self):
        self.list_controller.apply_snapshot(make_two_monitor_snapshot(make_sample_snapshot()))
        outline_view = self.list_controller.outline_view
        first_window_item = outline_view.itemAtRow_(1)
        second_monitor_item = self.list_controller.top_level_items[1]
        self.list_controller.dragged_item = first_window_item
        self.list_controller.outlineView_acceptDrop_item_childIndex_(outline_view, None, second_monitor_item, -1)
        self.assertEqual(self.submitted_actions[-1].action, "move_window_to_monitor")
        self.assertEqual(self.submitted_actions[-1].arguments, {"monitor_id": "2"})

    def test_dropdown_fits_its_size_to_all_rows(self):
        long_name_snapshot = make_sample_snapshot()
        window_one = long_name_snapshot.monitors[0].windows[0]
        long_entry = window_one.agent_sessions[0]._replace(tab_title="2026-05-27-longfellow-labs-llc-8b-with-a-long-name")
        long_name_snapshot = long_name_snapshot._replace(monitors=(long_name_snapshot.monitors[0]._replace(windows=(
            window_one._replace(agent_sessions=(long_entry,) + window_one.agent_sessions[1:]),
            long_name_snapshot.monitors[0].windows[1])),))
        self.list_controller.apply_snapshot(long_name_snapshot)
        self.dropdown_panel_controller.fit_size_to_rows()
        width, height = self.dropdown_panel_controller.preferredContentSize()
        self.assertGreater(width, self.list_controller.measure_widest_row_width())
        self.assertGreaterEqual(height, self.list_controller.measure_fully_expanded_list_height() + TOP_BAR_HEIGHT
                                + BOTTOM_BAR_HEIGHT)
        # Laid out at that size, the long name and every row's details show in full.
        self.window.setContentSize_((width, height))
        self.window.contentView().layoutSubtreeIfNeeded()
        row_view = self.list_controller.outline_view.viewAtColumn_row_makeIfNecessary_(0, 1, True)
        row_view.layoutSubtreeIfNeeded()
        name_text_field = row_view.name_text_field
        self.assertGreaterEqual(name_text_field.frame().size.width, name_text_field.fittingSize().width)
        details_text_field = row_view.details_text_field
        self.assertGreaterEqual(details_text_field.frame().size.width, details_text_field.fittingSize().width)

    def apply_snapshot_with_previews(self):
        """Applies the sample snapshot with previews on both tabs of the second window and the first tab of the first."""
        snapshot = make_sample_snapshot()
        previews = {"1": "Bash: python3 -m unittest", "10": "You: compare the calls", "11": "All tests pass."}
        snapshot = snapshot._replace(monitors=(snapshot.monitors[0]._replace(windows=tuple(
            window._replace(agent_sessions=tuple(entry._replace(preview_line=previews.get(entry.tab_id, ""))
                                                 for entry in window.agent_sessions))
            for window in snapshot.monitors[0].windows)),))
        self.list_controller.apply_snapshot(snapshot)
        return self.list_controller.outline_view

    def click_row_arrow(self, row):
        outline_view = self.list_controller.outline_view
        row_view = outline_view.viewAtColumn_row_makeIfNecessary_(0, row, True)
        with unittest.mock.patch.object(outline_view, "rowForView_", return_value=row):
            self.list_controller.disclosureButtonClicked_(row_view.disclosure_button)

    def lay_out_row_view(self, row):
        row_view = self.list_controller.outline_view.viewAtColumn_row_makeIfNecessary_(0, row, True)
        row_view.layoutSubtreeIfNeeded()
        return row_view

    def test_the_arrow_on_a_tab_opens_its_preview_under_the_name_and_closes_it_again(self):
        outline_view = self.apply_snapshot_with_previews()
        self.assertEqual(outline_view.rectOfRow_(1).size.height, ROW_HEIGHT)  # closed at first
        self.window.contentView().layoutSubtreeIfNeeded()
        arrow = self.lay_out_row_view(1).disclosure_button
        self.assertFalse(arrow.isHidden())
        self.assertEqual(arrow.image().accessibilityDescription(), "Open")
        self.assertTrue(self.lay_out_row_view(2).disclosure_button.isHidden())  # no preview: no arrow, names still line up
        self.assertEqual(self.lay_out_row_view(1).name_text_field.frame().origin.x,
                         self.lay_out_row_view(2).name_text_field.frame().origin.x)
        self.click_row_arrow(1)
        self.assertEqual([outline_view.rectOfRow_(row).size.height for row in range(3)],
                         [ROW_HEIGHT, TAB_ROW_HEIGHT_WITH_PREVIEW, ROW_HEIGHT])
        self.assertEqual(self.submitted_actions, [])  # opening doesn't show the tab
        self.window.setContentSize_((POPOVER_WIDTH + 200, 400))
        self.window.contentView().layoutSubtreeIfNeeded()
        row_view = outline_view.viewAtColumn_row_makeIfNecessary_(0, 1, True)
        row_view.layoutSubtreeIfNeeded()
        preview_frame = row_view.preview_text_field.frame()
        self.assertEqual(row_view.preview_text_field.stringValue(), "Bash: python3 -m unittest")
        self.assertFalse(row_view.preview_text_field.isHidden())
        self.assertEqual(row_view.preview_text_field.maximumNumberOfLines(), 2)
        self.assertEqual(preview_frame.origin.x, row_view.name_text_field.frame().origin.x)  # starts under the name
        self.assertLessEqual(preview_frame.origin.y + preview_frame.size.height, row_view.name_text_field.frame().origin.y)
        self.assertEqual(row_view.preview_connector_text_field.stringValue(), "│  \n│  ")  # the window's tree line carries on
        self.assertEqual(self.list_controller.measure_fully_expanded_list_height(), 6 * ROW_HEIGHT + TAB_ROW_HEIGHT_WITH_PREVIEW)
        self.assertEqual(self.lay_out_row_view(1).disclosure_button.image().accessibilityDescription(), "Close")
        self.click_row_arrow(1)
        self.assertEqual(outline_view.rectOfRow_(1).size.height, ROW_HEIGHT)

    def test_a_single_click_on_a_row_opens_and_closes_nothing(self):
        outline_view = self.apply_snapshot_with_previews()
        for row in (0, 1):
            item = outline_view.itemAtRow_(row)
            self.list_controller.find_clicked_item = lambda: item
            self.list_controller.rowClicked_(None)
        self.assertEqual((outline_view.numberOfRows(), outline_view.rectOfRow_(1).size.height), (7, ROW_HEIGHT))

    def test_the_arrow_on_a_window_collapses_just_that_window(self):
        outline_view = self.apply_snapshot_with_previews()
        self.click_row_arrow(5)  # a preview in the second window
        self.click_row_arrow(4)  # the second window
        self.assertEqual((outline_view.numberOfRows(), self.list_controller.collapsed_item_keys), (5, {("window", "w2")}))
        self.click_row_arrow(4)
        self.assertEqual(outline_view.rectOfRow_(5).size.height, TAB_ROW_HEIGHT_WITH_PREVIEW)  # comes back as it was

    def test_expand_all_and_collapse_all_leave_the_previews_as_they_are(self):
        outline_view = self.apply_snapshot_with_previews()
        self.click_row_arrow(1)  # opens one preview
        self.list_controller.set_all_expanded(False)
        self.assertEqual(outline_view.numberOfRows(), 2)
        self.list_controller.set_all_expanded(True)
        self.assertEqual([outline_view.rectOfRow_(row).size.height for row in (1, 5, 6)],
                         [TAB_ROW_HEIGHT_WITH_PREVIEW, ROW_HEIGHT, ROW_HEIGHT])

    def test_the_top_bar_has_one_button_that_expands_or_collapses_all(self):
        panel = self.dropdown_panel_controller
        self.list_controller.apply_snapshot(make_sample_snapshot())
        button = panel.expand_or_collapse_all_button
        self.assertLess(button.frame().origin.x, panel.settings_button.frame().origin.x)  # beside the settings button
        self.assertEqual(button.toolTip(), "Collapse all")  # fully expanded: a click collapses
        panel.expandOrCollapseAllClicked_(button)
        self.assertEqual((self.list_controller.outline_view.numberOfRows(), button.toolTip()), (2, "Expand all"))
        self.list_controller.toggle_expansion(self.list_controller.outline_view.itemAtRow_(0))  # one window expanded
        self.assertEqual(button.toolTip(), "Expand all")
        panel.expandOrCollapseAllClicked_(button)
        self.assertEqual((self.list_controller.outline_view.numberOfRows(), button.toolTip()), (7, "Collapse all"))

    def test_an_expanded_window_row_has_a_button_that_opens_or_closes_its_tabs_previews(self):
        outline_view = self.apply_snapshot_with_previews()
        self.window.contentView().layoutSubtreeIfNeeded()
        lay_out_row_view = self.lay_out_row_view
        window_row_view = lay_out_row_view(4)  # tandem-repeats: both of its tabs have previews
        button = window_row_view.preview_toggle_button
        self.assertFalse(button.isHidden())
        self.assertLessEqual(window_row_view.name_text_field.frame().origin.x + window_row_view.name_text_field.frame().size.width,
                             button.frame().origin.x)
        self.assertEqual(button.image().accessibilityDescription(), "Show the tabs' previews")
        self.click_row_arrow(5)  # one open: the button still opens the rest
        with unittest.mock.patch.object(outline_view, "rowForView_", return_value=4):
            self.list_controller.previewToggleButtonClicked_(button)
            self.assertEqual([outline_view.rectOfRow_(row).size.height for row in (1, 5, 6)],
                             [ROW_HEIGHT, TAB_ROW_HEIGHT_WITH_PREVIEW, TAB_ROW_HEIGHT_WITH_PREVIEW])
            self.assertEqual(lay_out_row_view(4).preview_toggle_button.image().accessibilityDescription(),
                             "Hide the tabs' previews")
            self.list_controller.previewToggleButtonClicked_(button)
        self.assertEqual([outline_view.rectOfRow_(row).size.height for row in (5, 6)], [ROW_HEIGHT, ROW_HEIGHT])
        self.list_controller.toggle_expansion(outline_view.itemAtRow_(4))  # collapsed: no button
        self.assertTrue(lay_out_row_view(4).preview_toggle_button.isHidden())
        self.assertTrue(lay_out_row_view(1).preview_toggle_button.isHidden())  # a tab row
        self.list_controller.apply_snapshot(make_sample_snapshot())  # no previews at all: nothing to open
        self.assertTrue(lay_out_row_view(0).preview_toggle_button.isHidden())

    def lay_out_tab_row_views(self, snapshot):
        self.list_controller.apply_snapshot(snapshot)
        self.window.setContentSize_((POPOVER_WIDTH + 200, 260))
        self.window.contentView().layoutSubtreeIfNeeded()
        row_views = []
        for row in range(self.list_controller.outline_view.numberOfRows()):
            if self.list_controller.outline_view.itemAtRow_(row).kind == "tab":
                row_view = self.list_controller.outline_view.viewAtColumn_row_makeIfNecessary_(0, row, True)
                row_view.layoutSubtreeIfNeeded()
                row_views.append(row_view)
        return row_views

    def test_tool_icon_state_and_time_columns_line_up_when_two_tools_run(self):
        row_views = self.lay_out_tab_row_views(make_sample_snapshot())  # Claude Code and Codex
        column_views_by_name = {"icon": [row_view.icon_image_view for row_view in row_views],
                                "state": [row_view.state_text_field for row_view in row_views],
                                "time": [row_view.elapsed_time_text_field for row_view in row_views]}
        for column_name, views in column_views_by_name.items():
            self.assertEqual(len({(view.frame().origin.x, view.frame().size.width) for view in views}), 1, column_name)
            self.assertTrue(all(not view.isHidden() for view in views), column_name)
        icon_frame, state_frame, time_frame = (views[0].frame() for views in column_views_by_name.values())
        self.assertLess(icon_frame.origin.x + icon_frame.size.width, state_frame.origin.x)
        self.assertLess(state_frame.origin.x + state_frame.size.width, time_frame.origin.x)
        for column_name in ("state", "time"):
            views = column_views_by_name[column_name]
            self.assertGreaterEqual(views[0].frame().size.width, max(view.fittingSize().width for view in views) - 1)

        # Alignment is in the text itself (styled text overrides a field's alignment): state left, time right.
        def read_text_alignment(text_field):
            attributed_text = text_field.attributedStringValue()
            paragraph_style = attributed_text.attribute_atIndex_effectiveRange_(NSParagraphStyleAttributeName, 0, None)[0]
            return paragraph_style.alignment() if paragraph_style is not None else None
        self.assertEqual(read_text_alignment(row_views[0].elapsed_time_text_field), NSTextAlignmentRight)
        self.assertNotEqual(read_text_alignment(row_views[0].state_text_field), NSTextAlignmentRight)
        self.list_controller.toggle_expansion(self.list_controller.outline_view.itemAtRow_(0))  # only a collapsed window has counts
        window_row_view = self.list_controller.outline_view.viewAtColumn_row_makeIfNecessary_(0, 0, True)
        self.assertEqual(window_row_view.details_text_field.stringValue(), "1 asking, 1 working, 1 done")
        self.assertEqual(read_text_alignment(window_row_view.details_text_field), NSTextAlignmentRight)

    def test_no_icon_column_when_every_agent_session_uses_the_same_tool(self):
        snapshot = make_sample_snapshot()
        window_one = snapshot.monitors[0].windows[0]
        claude_only_window = window_one._replace(agent_sessions=window_one.agent_sessions[:2])
        snapshot = snapshot._replace(monitors=(snapshot.monitors[0]._replace(
            windows=(claude_only_window, snapshot.monitors[0].windows[1])),))
        row_views = self.lay_out_tab_row_views(snapshot)
        self.assertEqual(self.list_controller.icon_column_width, 0)
        self.assertTrue(all(row_view.icon_image_view.isHidden() for row_view in row_views))

    def test_dropdown_keeps_its_fully_expanded_size_when_rows_collapse(self):
        self.list_controller.apply_snapshot(make_sample_snapshot())
        self.dropdown_panel_controller.fit_size_to_rows()
        fully_expanded_size = tuple(self.dropdown_panel_controller.preferredContentSize())
        self.list_controller.set_all_expanded(False)
        self.dropdown_panel_controller.fit_size_to_rows()
        self.assertEqual(tuple(self.dropdown_panel_controller.preferredContentSize()), fully_expanded_size)

    def test_rows_draw_and_render_to_an_image(self):
        self.list_controller.apply_snapshot(make_sample_snapshot())
        content_view = self.window.contentView()
        content_view.layoutSubtreeIfNeeded()
        row_view = self.list_controller.outline_view.viewAtColumn_row_makeIfNecessary_(0, 1, True)
        self.assertGreater(row_view.frame().size.width, POPOVER_WIDTH - 60)  # the column fills the dropdown
        self.assertEqual(row_view.name_text_field.stringValue(), "planning")
        self.assertEqual(row_view.state_text_field.stringValue(), "working")
        self.assertIsNone(row_view.toolTip())  # no hover popups on rows (user request, 2026-10-19)
        self.assertTrue(row_view.prefix_text_field.stringValue().startswith("├─ "))
        image_path = os.environ.get("LLM_SIDEBAR_DASHBOARD_SMOKE_TEST_IMAGE")
        if image_path:
            bitmap = content_view.bitmapImageRepForCachingDisplayInRect_(content_view.bounds())
            content_view.cacheDisplayInRect_toBitmapImageRep_(content_view.bounds(), bitmap)
            bitmap.representationUsingType_properties_(NSBitmapImageFileTypePNG, {}).writeToFile_atomically_(image_path, True)


class AutomaticIconColorTests(unittest.TestCase):
    def snapshot_with_states(self, states):
        window = WindowEntry("w1", "work", False, (0, 0, 800, 600), None, len(states), tuple(
            make_entry(str(index), f"tab-{index}", state) for index, state in enumerate(states)))
        return ComputerSnapshot("c", "Test Mac", 1.0, (MonitorEntry("1", "Built-in", (0, 0, 2000, 1300),
                                                                    (0, 0, 2000, 1260), (window,)),))

    def test_most_urgent_agent_session_picks_the_color(self):
        for states, expected_color_name in (
                (["idle", "stuck", "asking", "error"], "light_blue"),
                (["working", "stuck", "error"], "red"),
                (["working", "stuck", "done"], "orange"),
                (["working", "idle", "done", "unknown"], "green"),
                ([], "green")):
            self.assertEqual(choose_automatic_icon_color_name(self.snapshot_with_states(states)), expected_color_name)

    def test_every_icon_color_choice_looks_different(self):
        # Current macOS makes system purple a magenta and system pink nearly its red.
        components_by_name = {}
        for color_name in ICON_COLOR_NAMES[1:]:  # all but "auto"
            color = ICON_COLORS_BY_NAME[color_name]().colorUsingColorSpace_(NSColorSpace.sRGBColorSpace())
            components_by_name[color_name] = (color.redComponent() * 255, color.greenComponent() * 255, color.blueComponent() * 255)
        for first_name, first in components_by_name.items():
            for second_name, second in components_by_name.items():
                if first_name < second_name:
                    distance = sum((a - b) ** 2 for a, b in zip(first, second)) ** 0.5
                    self.assertGreater(distance, 60, f"{first_name} and {second_name} look alike")


class SidebarWidthDragTests(unittest.TestCase):
    def setUp(self):
        self.owner = RecordingOwner()
        self.owner.saved_widths = []
        self.owner.sidebar_width_changed = self.owner.saved_widths.append
        self.controller = SidebarController.alloc().initWithOwner_(self.owner)
        self.controller.list_controller.refresh_timer.invalidate()
        # Set up by hand rather than through apply_settings, so no window appears on screen.
        self.controller.settings = dict(DEFAULT_SETTINGS, sidebar_is_on=True, sidebar_mode="floating")
        self.controller.screens = [{"name": "Built-in", "frame": (0, 0, 1500, 1000), "visible_frame": (0, 0, 1500, 970)}]
        self.controller.placement = SidebarPlacement(0, "right", True)
        self.controller.is_shown = True
        self.controller.window.setFrame_display_(NSMakeRect(1100, 0, 400, 970), False)
        self.controller.move_width_drag_strip_to_inner_edge("right")

    def test_dragging_the_inner_edge_left_widens_the_sidebar_and_saves_the_width(self):
        self.assertEqual(self.controller.width_drag_strip.frame().origin.x, 0)
        self.controller.start_width_drag(1100)
        self.controller.continue_width_drag(950)
        self.assertTrue(self.controller.something_belonging_to_the_sidebar_is_open())
        frame = self.controller.window.frame()
        self.assertEqual((frame.origin.x, frame.size.width), (950, 550))
        self.controller.finish_width_drag()
        self.assertEqual(self.owner.saved_widths, [550])
        self.assertFalse(self.controller.width_drag_is_in_progress())

    def test_a_click_without_movement_saves_nothing(self):
        self.controller.start_width_drag(1100)
        self.controller.finish_width_drag()
        self.assertEqual(self.owner.saved_widths, [])

    def test_shown_from_the_menu_it_stays_for_the_whole_five_seconds(self):
        self.controller.keep_shown_until = time.time() + 4
        self.controller.hideTimerFired_(None)
        self.assertTrue(self.controller.is_shown)
        self.assertIsNotNone(self.controller.hide_timer)  # checks again when the 5 seconds are up
        self.controller.cancel_hide_timer()

    def test_a_turned_off_sidebar_is_parked_just_past_its_edge(self):
        # So "Show the sidebar" slides it in from that edge. (The shown case isn't tested here:
        # it would put a real window on the screen.)
        self.owner.reserve_screen_area = lambda area: None
        self.controller.settings = dict(self.controller.settings, sidebar_is_on=False)
        self.controller.is_shown = False
        self.controller.update_placement()  # the real screens: the sidebar stays hidden
        self.assertFalse(self.controller.is_shown or self.controller.window.isVisible())
        # The parked frame for both edges, on this test's own screen (setUp: 1500 wide at x 0, sidebar 400 wide).
        for edge, parked_x in (("right", 1500), ("left", -400)):
            self.controller.screens = [{"name": "Built-in", "frame": (0, 0, 1500, 1000), "visible_frame": (0, 0, 1500, 970)}]
            self.controller.placement = SidebarPlacement(0, edge, True)
            self.controller.set_shown(False, animate=False)
            self.assertEqual(self.controller.window.frame().origin.x, parked_x, edge)

    def test_a_click_in_another_window_cancels_an_open_rename_so_the_list_keeps_updating(self):
        self.assertIs(self.controller.window.delegate(), self.controller)
        self.controller.window.setFrame_display_(NSMakeRect(-4000, -4000, 400, 600), False)
        self.controller.window.orderFrontRegardless()  # "on screen" for the list, though far outside every monitor
        self.addCleanup(self.controller.window.orderOut_, None)
        list_controller = self.controller.list_controller
        list_controller.apply_snapshot(make_sample_snapshot())
        self.controller.window.contentView().layoutSubtreeIfNeeded()
        list_controller.begin_rename(list_controller.outline_view.itemAtRow_(1))
        self.assertTrue(list_controller.rename_in_progress)
        self.controller.windowDidResignKey_(None)
        self.assertFalse(list_controller.rename_in_progress)
        self.assertEqual(self.owner.submitted_actions, [])  # nothing was renamed

    def test_the_window_server_lets_the_strip_change_the_pointer_while_another_app_is_in_front(self):
        self.assertTrue(allow_pointer_changes_while_another_app_is_in_front())
        self.assertFalse(self.controller.window.areCursorRectsEnabled())  # they would reset the arrows

    def test_strip_sits_on_the_right_side_of_a_left_edge_sidebar(self):
        self.controller.move_width_drag_strip_to_inner_edge("left")
        strip_frame = self.controller.width_drag_strip.frame()
        self.assertEqual(strip_frame.origin.x + strip_frame.size.width, self.controller.window.contentView().bounds().size.width)


class SettingsRecordingOwner:
    """Stands in for the app controller: holds the settings and records each change."""

    def __init__(self, settings):
        self.settings = settings
        self.changed_settings = []

    def settings_changed(self, new_settings):
        self.settings = new_settings
        self.changed_settings.append(new_settings)


class SettingsWindowSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        NSApplication.sharedApplication().setActivationPolicy_(NSApplicationActivationPolicyAccessory)

    def setUp(self):
        self.owner = SettingsRecordingOwner(dict(DEFAULT_SETTINGS))
        self.controller = SettingsWindowController.alloc().initWithOwner_(self.owner)
        self.controller.settings = dict(self.owner.settings)
        self.controller.window = self.controller.build_window()  # built without showing it

    def test_changing_one_control_keeps_changes_made_elsewhere(self):
        self.owner.settings = dict(self.owner.settings, sidebar_width=555)  # dragged at the sidebar's edge meanwhile
        self.controller.stuck_minutes_field.setStringValue_("25")
        self.controller.controlChanged_(self.controller.stuck_minutes_field)
        self.assertEqual(self.owner.settings["stuck_minutes"], 25)
        self.assertEqual(self.owner.settings["sidebar_width"], 555)

    def test_both_edges_stay_choosable_when_the_menu_bar_monitor_stands_between_two_others(self):
        three_monitors_in_a_row = [
            {"name": "Built-in", "frame": (0, 0, 1500, 1000), "visible_frame": (0, 0, 1500, 970)},
            {"name": "Dell", "frame": (-1920, 0, 1920, 1080), "visible_frame": (-1920, 0, 1920, 1080)},
            {"name": "Studio", "frame": (1500, -200, 2500, 1400), "visible_frame": (1500, -200, 2500, 1400)}]
        with unittest.mock.patch("llm_sidebar_dashboard.user_interface.settings_window.read_monitors_from_screens",
                                 return_value=three_monitors_in_a_row):
            self.controller.update_edge_choices()
        self.assertEqual([menu_item.isEnabled() for menu_item in self.controller.edge_popup.itemArray()], [True, True])

    def test_window_edge_popup_shows_capitalized_titles_and_saves_the_edge_name(self):
        self.assertEqual(self.controller.edge_popup.itemTitles(), ["Right", "Left"])
        self.assertEqual(self.controller.edge_popup.titleOfSelectedItem(), "Right")  # the default edge
        with unittest.mock.patch("llm_sidebar_dashboard.user_interface.settings_window.read_monitors_from_screens",
                                 return_value=[{"name": "Built-in", "frame": (0, 0, 1500, 1000),
                                                "visible_frame": (0, 0, 1500, 970)}]):
            self.controller.edge_popup.selectItemWithTitle_("Left")
            self.controller.controlChanged_(self.controller.edge_popup)
        self.assertEqual(self.owner.settings["sidebar_edge"], "left")

    def test_column_checkboxes_choose_the_shown_columns_in_their_left_to_right_order(self):
        checkboxes = self.controller.column_checkboxes
        self.assertEqual([title for title in (checkbox.title() for checkbox in checkboxes.values())],
                         ["State", "State Duration", "Used Token Count", "Created Time", "Time Since Created"])
        checkboxes["state"].setState_(0)
        self.controller.controlChanged_(checkboxes["state"])
        checkboxes["created_time"].setState_(1)
        self.controller.controlChanged_(checkboxes["created_time"])
        self.assertEqual(self.owner.settings["shown_columns"], ["context_tokens", "created_time", "state_duration"])

    def test_text_fields_save_when_they_lose_focus_too(self):
        self.assertTrue(self.controller.stuck_minutes_field.cell().sendsActionOnEndEditing())

    def test_invalid_number_puts_the_current_value_back(self):
        self.controller.sidebar_width_field.setStringValue_("5")
        self.controller.controlChanged_(self.controller.sidebar_width_field)
        self.assertEqual(self.owner.settings["sidebar_width"], DEFAULT_SETTINGS["sidebar_width"])
        self.assertEqual(self.controller.sidebar_width_field.stringValue(), str(DEFAULT_SETTINGS["sidebar_width"]))

    def test_changes_made_elsewhere_show_in_the_open_window_and_are_kept(self):
        self.owner.settings = dict(self.owner.settings, sidebar_is_on=True, sidebar_width=650)
        self.controller.reflect_settings(self.owner.settings)
        self.assertEqual(self.controller.sidebar_is_on_checkbox.state(), 1)
        self.assertEqual(self.controller.sidebar_width_field.stringValue(), "650")
        self.controller.asking_count_checkbox.setState_(0)
        self.controller.controlChanged_(self.controller.asking_count_checkbox)
        self.assertEqual((self.owner.settings["sidebar_is_on"], self.owner.settings["sidebar_width"]), (True, 650))
        self.assertFalse(self.owner.settings["icon_shows_asking_count"])

    def test_icon_color_popup_offers_auto_first_and_stores_lowercase_names(self):
        self.assertEqual(self.controller.icon_color_popup.titleOfSelectedItem(), "Auto")
        self.controller.icon_color_popup.selectItemWithTitle_("Orange")
        self.controller.controlChanged_(self.controller.icon_color_popup)
        self.assertEqual(self.owner.settings["icon_color"], "orange")

    def test_reflecting_settings_before_the_window_was_opened_does_nothing(self):
        controller = SettingsWindowController.alloc().initWithOwner_(self.owner)
        controller.reflect_settings(dict(self.owner.settings, sidebar_width=700))
        self.assertIsNone(controller.window)


if __name__ == "__main__":
    unittest.main()
