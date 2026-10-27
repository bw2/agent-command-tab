"""Builds the list from a sample snapshot in an off-screen window (PLAN.md section 17, UI smoke test).

Set AGENT_COMMAND_TAB_SMOKE_TEST_IMAGE=/path/to/file.png to also save what the list looks like.
"""

import os
import time
import unittest
import unittest.mock

from AppKit import (NSApplication, NSApplicationActivationPolicyAccessory, NSBackgroundStyleNormal, NSBackingStoreBuffered,
                    NSBitmapImageFileTypePNG, NSCursor, NSDragOperationMove, NSDragOperationNone, NSEvent,
                    NSEventModifierFlagCommand, NSEventTypeLeftMouseDown, NSEventTypeLeftMouseUp, NSMakePoint, NSMakeRect,
                    NSFloatingWindowLevel, NSMenu, NSNormalWindowLevel, NSParagraphStyleAttributeName, NSTextAlignmentRight,
                    NSTextField, NSWindow, NSWindowOcclusionStateVisible, NSWindowStyleMaskBorderless,
                    NSWindowStyleMaskResizable)
from Foundation import NSIndexSet

from agent_command_tab.app_controller import AppController
from agent_command_tab.settings_store import DEFAULT_SETTINGS, LIST_COLUMNS, SMALLEST_WINDOW_SIZES
from agent_command_tab.snapshot_data_classes import AgentSessionEntry, ChildProcessEntry, ComputerSnapshot, MonitorEntry, WindowEntry
from agent_command_tab.user_interface.popover_container import (BOTTOM_BAR_HEIGHT, GAP_BETWEEN_TOP_BAR_BUTTONS,
                                                                PANEL_SIDE_MARGIN, POPOVER_WIDTH, TOP_BAR_HEIGHT,
                                                                ListPanelController)
from agent_command_tab.messages_between_agent_sessions import DEFAULT_NOTE_ASKING_AGENT_SESSIONS_TO_TALK_TO_EACH_OTHER
from agent_command_tab.user_interface.agent_session_outline_view import (COLUMN_TITLE_LINE_HEIGHT, COLUMN_TITLES_ROW_PADDING,
                                                                         GAP_BEFORE_DETAILS, GAP_BETWEEN_COLUMNS, ROW_HEIGHT, SMALLEST_TAB_NAME_WIDTH,
                                                                         TAB_ROW_HEIGHT_WITH_PREVIEW, TEXT_FIELD_INSETS,
                                                                         AgentSessionListController, RowButton,
                                                                         RowViewWithSelectionOutline, build_index_set,
                                                                         compute_selection_outline_rect)
from agent_command_tab.user_interface.talk_to_each_other_alert import build_talk_to_each_other_alert
from agent_command_tab.user_interface.list_window import ListWindowController
from agent_command_tab.user_interface.screen_layout import (compute_list_window_content_rect_for_a_dragged_sidebar,
                                                            frame_is_on_a_screen, grow_frame_to_smallest_size)
from agent_command_tab.user_interface.pointing_hand_button import PointingHandButton
from agent_command_tab.user_interface.menu_bar_status_item import choose_icon_color_name
from agent_command_tab.user_interface.settings_window import LAUNCH_COMMAND_FIELD_WIDTH, ROW_SPACING, SettingsWindowController
from agent_command_tab.user_interface.sidebar_panel import (SidebarController,
                                                            allow_pointer_changes_while_another_app_is_in_front)
from agent_command_tab.user_interface.sidebar_placement import SidebarPlacement


class RecordingOwner:
    """Stands in for the app controller and records what the list asks it to do."""

    def __init__(self):
        self.submitted_actions = []
        self.started_agent_sessions = []
        self.windows_asked_about_closing = []
        self.answer_to_closing_window = False
        self.confirmation_questions = []
        self.answer_to_confirmation = False
        self.peeks = []
        self.chosen_list_sort_orders = []
        self.idle_tabs_are_hidden_choices = []
        self.talk_to_each_other_requests = []  # (agent session entries, left-out entries)
        self.note_to_send = None  # what the Talk to Each Other window returns: None for Cancel
        self.hooks_install_requests = []

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

    def ask_for_confirmation(self, explanation):
        self.confirmation_questions.append(explanation)
        return self.answer_to_confirmation

    def submit_action_and_report_failure(self, action_request, failure_title):
        self.submitted_actions.append(action_request)

    def list_sort_order_chosen(self, list_sort_order, list_sort_order_is_reversed):
        self.chosen_list_sort_orders.append((list_sort_order, list_sort_order_is_reversed))

    def idle_tabs_are_hidden_chosen(self, idle_tabs_are_hidden):
        self.idle_tabs_are_hidden_choices.append(idle_tabs_are_hidden)

    def ask_for_note_asking_agent_sessions_to_talk_to_each_other(self, agent_session_entries, left_out_agent_session_entries):
        self.talk_to_each_other_requests.append((agent_session_entries, left_out_agent_session_entries))
        return self.note_to_send

    def install_hooks(self, agent_tool_names):
        self.hooks_install_requests.append(list(agent_tool_names))


def make_entry(tab_id, title, status, tool="Claude Code", is_in_front=False, slash_commands=("/compact", "/clear")):
    return AgentSessionEntry(tab_id=tab_id, session_id=f"s{tab_id}", tab_title=title, tab_title_set_by_user=False,
                             agent_session_name=title, agent_tool_name=tool, working_folder="/x", status=status,
                             status_since=0.0, is_in_front=is_in_front, slash_commands=slash_commands)


def make_sample_snapshot(second_window_tabs=2):
    window_one = WindowEntry("w1", "agent-command-tab", False, (0, 0, 800, 600), 4, (
        make_entry("1", "planning", "working", is_in_front=True),
        make_entry("2", "phase0-tests", "asking"),
        make_entry("3", "codex-review", "done", tool="Codex"),
    ))
    window_two = WindowEntry("w2", "tandem-repeats", False, (0, 0, 800, 600), 2, tuple(
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
        self.assertEqual(outline_view.numberOfRows(), 8)  # 2 windows + 5 tabs, all expanded, then the "+" row
        self.assertIs(outline_view.itemAtRow_(7), self.list_controller.new_window_row_item)
        first_tab_item = outline_view.itemAtRow_(1)
        self.assertEqual(first_tab_item.kind, "tab")
        self.list_controller.toggle_expansion(outline_view.itemAtRow_(4))  # collapse the second window
        self.assertEqual(outline_view.numberOfRows(), 6)
        self.list_controller.apply_snapshot(make_sample_snapshot(second_window_tabs=3))  # structure changes
        self.assertEqual(outline_view.numberOfRows(), 6)  # the second window stays collapsed
        self.assertIs(outline_view.itemAtRow_(1), first_tab_item)  # same item object, so selection survives

    def test_collapse_all_and_expand_all(self):
        self.list_controller.apply_snapshot(make_sample_snapshot())
        self.list_controller.set_all_expanded(False)
        self.assertEqual(self.list_controller.outline_view.numberOfRows(), 3)  # 2 windows and the "+" row
        self.list_controller.set_all_expanded(True)
        self.assertEqual(self.list_controller.outline_view.numberOfRows(), 8)

    def test_two_monitors_start_fully_expanded(self):
        self.list_controller.apply_snapshot(make_two_monitor_snapshot(make_sample_snapshot()))
        self.assertEqual(self.list_controller.outline_view.numberOfRows(), 10)  # 2 monitors, 2 windows, 5 tabs, "+"

    def test_collapsing_a_monitor_leaves_its_windows_own_state_alone(self):
        self.list_controller.apply_snapshot(make_two_monitor_snapshot(make_sample_snapshot()))
        outline_view = self.list_controller.outline_view
        self.list_controller.toggle_expansion(outline_view.itemAtRow_(0))
        self.assertEqual(self.list_controller.collapsed_item_keys, {("monitor", "1")})
        self.list_controller.apply_snapshot(make_two_monitor_snapshot(make_sample_snapshot(second_window_tabs=3)))
        self.list_controller.toggle_expansion(outline_view.itemAtRow_(0))
        self.assertEqual(outline_view.numberOfRows(), 11)  # everything expanded again, w2 now has 3 tabs; then "+"

    def test_collapsing_through_the_outline_view_itself_is_recorded(self):
        # NSOutlineView's own Left arrow handling calls collapseItem_ like this.
        self.list_controller.apply_snapshot(make_sample_snapshot())
        outline_view = self.list_controller.outline_view
        outline_view.collapseItem_(outline_view.itemAtRow_(0))
        self.assertIn(("window", "w1"), self.list_controller.collapsed_item_keys)
        self.assertEqual(self.list_controller.build_row_text_parts(outline_view.itemAtRow_(0)).details_text,
                         "1 asking, 1 working, 1 done")
        self.list_controller.apply_snapshot(make_sample_snapshot(second_window_tabs=3))
        self.assertEqual(outline_view.numberOfRows(), 6)  # w1 stays collapsed: 2 windows + 3 tabs of w2 + "+"

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

        def list_first_window_tab_titles():  # in the order the list shows them
            return [item.entry.tab_title for item in self.list_controller.items_by_key[("window", "w1")].child_items]
        self.assertEqual(list_first_window_tab_titles(), ["planning", "phase0-tests", "codex-review"])
        self.list_controller.set_list_sort_order("activity", False)  # asking, working, done
        self.assertEqual(list_first_window_tab_titles(), ["phase0-tests", "planning", "codex-review"])
        self.list_controller.set_list_sort_order("alphabet", False)
        self.assertEqual(list_first_window_tab_titles(), ["codex-review", "phase0-tests", "planning"])
        self.list_controller.set_list_sort_order("alphabet", True)
        self.assertEqual(list_first_window_tab_titles(), ["planning", "phase0-tests", "codex-review"])
        self.assertEqual([item.entry.title for item in self.list_controller.top_level_items], ["tandem-repeats", "agent-command-tab"])
        self.list_controller.set_list_sort_order("manual", False)  # iTerm2's own order, unchanged
        self.assertEqual(list_first_window_tab_titles(), ["planning", "phase0-tests", "codex-review"])

    def test_sorted_rows_allow_moves_to_another_window_or_monitor_but_no_reordering(self):
        self.list_controller.apply_snapshot(make_sample_snapshot())
        self.list_controller.set_list_sort_order("alphabet", False)
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
        # With two monitors, a window still moves to the other monitor while sorted, but not within its own.
        self.list_controller.apply_snapshot(make_two_monitor_snapshot(make_sample_snapshot()))
        first_monitor_item, second_monitor_item = self.list_controller.top_level_items
        self.list_controller.dragged_item = first_monitor_item.child_items[0]
        self.assertEqual(self.list_controller.outlineView_validateDrop_proposedItem_proposedChildIndex_(
            outline_view, None, second_monitor_item, -1), NSDragOperationMove)
        self.assertEqual(self.list_controller.outlineView_validateDrop_proposedItem_proposedChildIndex_(
            outline_view, None, first_monitor_item, 0), NSDragOperationNone)

    def test_shown_columns_follow_the_settings(self):
        snapshot = make_sample_snapshot()
        window_one = snapshot.monitors[0].windows[0]
        started_entries = tuple(entry._replace(started_at=time.time() - 3700) for entry in window_one.agent_sessions)
        snapshot = snapshot._replace(monitors=(snapshot.monitors[0]._replace(
            windows=(window_one._replace(agent_sessions=started_entries), snapshot.monitors[0].windows[1])),))
        self.list_controller.apply_snapshot(snapshot)
        self.window.contentView().layoutSubtreeIfNeeded()
        row_view = self.list_controller.outline_view.viewAtColumn_row_makeIfNecessary_(0, 1, True)
        self.assertEqual(self.list_controller.column_widths["created_time"], 0)  # off by default
        self.list_controller.set_shown_columns(["created_time", "existence_duration", "state_duration"])
        row_view.layoutSubtreeIfNeeded()
        self.assertEqual([not row_view.column_views[column_name].isHidden()
                          for column_name in ("created_time", "existence_duration", "state", "state_duration")],
                         [True, True, False, True])
        self.assertEqual(row_view.column_views["existence_duration"].stringValue(), "1h 1m")
        self.list_controller.set_shown_columns([])
        row_view.layoutSubtreeIfNeeded()
        self.assertTrue(row_view.column_views["state_duration"].isHidden())
        # Only the tool icon column is left: it shows by itself, since Claude Code and Codex both run.
        self.assertEqual(self.list_controller.measure_right_columns_width(), self.list_controller.column_widths["agent_tool_icon"])

    def test_column_titles_sit_over_their_columns_between_the_top_bar_and_the_list(self):
        self.list_controller.apply_snapshot(make_sample_snapshot())  # no context sizes known: no token columns
        self.window.contentView().layoutSubtreeIfNeeded()
        titles_row_view = self.list_controller.column_titles_row_view
        titles_row_view.layoutSubtreeIfNeeded()
        row_view = self.lay_out_row_view(1)
        titles_row_frame = titles_row_view.frame()
        self.assertFalse(titles_row_view.isHidden())
        # Two lines for "Time Since Last" over "Status Change", right under the top bar and right above the list.
        self.assertEqual(titles_row_frame.size.height, 2 * COLUMN_TITLE_LINE_HEIGHT + 2 * COLUMN_TITLES_ROW_PADDING)
        self.assertEqual(titles_row_frame.origin.y + titles_row_frame.size.height,
                         self.window.contentView().frame().size.height - TOP_BAR_HEIGHT)
        scroll_view_frame = self.list_controller.outline_view.enclosingScrollView().frame()
        self.assertEqual(scroll_view_frame.origin.y + scroll_view_frame.size.height, titles_row_frame.origin.y)
        title_fields = titles_row_view.column_title_text_fields
        self.assertEqual(titles_row_view.name_title_text_field.stringValue(), "Name")
        self.assertEqual(title_fields["state_duration"].stringValue(), "Time Since Last\nStatus Change")
        for column_name in ("state", "state_duration"):  # in the row's x, which is the panel's
            self.assertFalse(title_fields[column_name].isHidden())
            self.assertEqual((title_fields[column_name].frame().origin.x, title_fields[column_name].frame().size.width),
                             (row_view.column_views[column_name].frame().origin.x, row_view.column_views[column_name].frame().size.width))
        self.assertTrue(title_fields["context_tokens"].isHidden())
        # "Name" is centered over the names' room, which ends before the leftmost column (here the tool icon's).
        name_title_frame = titles_row_view.name_title_text_field.frame()
        self.assertEqual(name_title_frame.origin.x + name_title_frame.size.width,
                         row_view.column_views["agent_tool_icon"].frame().origin.x - GAP_BEFORE_DETAILS)
        # The column is as wide as the longer line of its title, much wider than "23h 59m".
        self.assertGreaterEqual(self.list_controller.column_widths["state_duration"], TEXT_FIELD_INSETS
                                + self.list_controller.measure_text_width("Time Since Last", self.list_controller.column_title_font))

    def click_column_title(self, title_name):
        """Clicks the middle of a title, as AppKit delivers the click: to the row of titles, whatever label is under it."""
        titles_row_view = self.list_controller.column_titles_row_view
        titles_row_view.layoutSubtreeIfNeeded()
        text_field = (titles_row_view.name_title_text_field if title_name == "name"
                      else titles_row_view.column_title_text_fields[title_name])
        frame = text_field.frame()
        point_in_the_row = NSMakePoint(frame.origin.x + frame.size.width / 2, frame.origin.y + frame.size.height / 2)
        self.assertIs(titles_row_view.hitTest_(titles_row_view.convertPoint_toView_(point_in_the_row, titles_row_view.superview())),
                      titles_row_view)
        point_in_the_window = titles_row_view.convertPoint_toView_(point_in_the_row, None)
        for event_type, row_view_method in ((NSEventTypeLeftMouseDown, titles_row_view.mouseDown_),
                                            (NSEventTypeLeftMouseUp, titles_row_view.mouseUp_)):
            row_view_method(NSEvent.mouseEventWithType_location_modifierFlags_timestamp_windowNumber_context_eventNumber_clickCount_pressure_(
                event_type, point_in_the_window, 0, 0, self.window.windowNumber(), None, 0, 1, 1.0))

    def test_clicking_a_column_title_sorts_by_it_then_the_other_way_then_not_at_all(self):
        snapshot = make_sample_snapshot()
        window_one = snapshot.monitors[0].windows[0]
        # Context sizes known for planning (467k of 1M) and codex-review (91k of 200k), not for phase0-tests.
        window_one = window_one._replace(agent_sessions=(
            window_one.agent_sessions[0]._replace(context_tokens=467_000, context_fill_percent=47),
            window_one.agent_sessions[1],
            window_one.agent_sessions[2]._replace(context_tokens=91_000, context_fill_percent=46)))
        self.list_controller.apply_snapshot(snapshot._replace(monitors=(snapshot.monitors[0]._replace(
            windows=(window_one, snapshot.monitors[0].windows[1])),)))
        self.window.contentView().layoutSubtreeIfNeeded()
        outline_view = self.list_controller.outline_view

        def choose_and_show(list_sort_order, list_sort_order_is_reversed):  # as the app controller does, for every list
            self.owner.chosen_list_sort_orders.append((list_sort_order, list_sort_order_is_reversed))
            self.list_controller.set_list_sort_order(list_sort_order, list_sort_order_is_reversed)
        self.owner.list_sort_order_chosen = choose_and_show
        column_widths_before = dict(self.list_controller.column_widths)
        title_fields = self.list_controller.column_titles_row_view.column_title_text_fields
        for expected_sort, expected_tokens_title, expected_tab_titles in (
                (("context_percent", False), "Tokens ▾", ["planning", "codex-review", "phase0-tests"]),
                (("context_percent", True), "Tokens ▴", ["codex-review", "planning", "phase0-tests"]),  # unknown size last both ways
                (("manual", False), "Tokens", ["planning", "phase0-tests", "codex-review"])):
            self.click_column_title("context_tokens")
            self.assertEqual(self.owner.chosen_list_sort_orders[-1], expected_sort)
            self.assertEqual(title_fields["context_tokens"].stringValue(), expected_tokens_title)
            self.assertEqual([outline_view.itemAtRow_(row).entry.tab_title for row in range(1, 4)], expected_tab_titles)
            self.assertEqual(self.list_controller.column_widths, column_widths_before)  # the arrow had room already
        self.click_column_title("state")  # another title starts over in its first direction
        self.assertEqual(self.owner.chosen_list_sort_orders[-1], ("activity", False))
        self.assertEqual(title_fields["state"].stringValue(), "Status ▾")
        self.click_column_title("name")
        self.assertEqual(self.owner.chosen_list_sort_orders[-1], ("alphabet", False))
        self.assertEqual((self.list_controller.column_titles_row_view.name_title_text_field.stringValue(),
                          title_fields["state"].stringValue()), ("Name ▴", "Status"))  # A to Z: up, as in Finder

    def test_the_pointer_is_the_pointing_hand_above_the_titles_that_sort(self):
        self.list_controller.apply_snapshot(make_sample_snapshot())
        titles_row_view = self.list_controller.column_titles_row_view
        titles_row_view.layoutSubtreeIfNeeded()
        title_frames = {"name": titles_row_view.name_title_text_field.frame(),
                        **{column_name: text_field.frame() for column_name, text_field in titles_row_view.column_title_text_fields.items()}}
        for x, expected_cursor in ((title_frames["name"].origin.x + 1, NSCursor.pointingHandCursor()),
                                   (title_frames["agent_tool_icon"].origin.x + 1, NSCursor.arrowCursor()),  # no title, no sorting
                                   (title_frames["state"].origin.x + 1, NSCursor.pointingHandCursor()),
                                   (title_frames["state_duration"].origin.x - 1, NSCursor.arrowCursor()),  # between two columns
                                   (title_frames["state_duration"].origin.x + 1, NSCursor.pointingHandCursor())):
            titles_row_view.show_the_pointer_at(NSMakePoint(x, 1))  # near the bottom: the whole height above a column sorts
            self.assertEqual(NSCursor.currentCursor(), expected_cursor)

    def test_the_column_titles_row_hides_while_no_titled_column_shows(self):
        self.list_controller.apply_snapshot(make_sample_snapshot())
        self.list_controller.set_shown_columns([])  # only the tool icon column is left, which has no title
        titles_row_view = self.list_controller.column_titles_row_view
        self.assertTrue(titles_row_view.isHidden())
        self.assertEqual(titles_row_view.frame().size.height, 0)
        scroll_view_frame = self.list_controller.outline_view.enclosingScrollView().frame()
        self.assertEqual(scroll_view_frame.origin.y + scroll_view_frame.size.height,
                         self.window.contentView().frame().size.height - TOP_BAR_HEIGHT)
        self.list_controller.set_shown_columns(["state"])  # "Status", one line
        self.assertFalse(titles_row_view.isHidden())
        self.assertEqual(titles_row_view.frame().size.height, COLUMN_TITLE_LINE_HEIGHT + 2 * COLUMN_TITLES_ROW_PADDING)

    def test_memory_and_cpu_columns_show_the_agents_use(self):
        snapshot = make_sample_snapshot()
        window_one = snapshot.monitors[0].windows[0]
        measured_entry = window_one.agent_sessions[0]._replace(memory_bytes=512 * 1024 ** 2, cpu_percent=12)
        snapshot = snapshot._replace(monitors=(snapshot.monitors[0]._replace(windows=(
            window_one._replace(agent_sessions=(measured_entry,) + window_one.agent_sessions[1:]),
            snapshot.monitors[0].windows[1])),))
        self.list_controller.apply_snapshot(snapshot)
        self.list_controller.set_shown_columns(["memory_usage", "cpu_usage", "state"])
        self.window.setContentSize_((POPOVER_WIDTH + 200, 260))
        self.window.contentView().layoutSubtreeIfNeeded()
        row_view = self.list_controller.outline_view.viewAtColumn_row_makeIfNecessary_(0, 1, True)
        row_view.layoutSubtreeIfNeeded()
        memory_view, cpu_view, state_view = (row_view.column_views[column_name] for column_name in ("memory_usage", "cpu_usage", "state"))
        self.assertEqual((memory_view.stringValue(), cpu_view.stringValue()), ("512M", "12%"))
        self.assertLess(memory_view.frame().origin.x, cpu_view.frame().origin.x)  # left to right as in the settings
        self.assertLess(cpu_view.frame().origin.x, state_view.frame().origin.x)

    def test_top_bar_drops_the_sort_label_then_narrows_the_sort_popup_when_narrow(self):
        panel = self.dropdown_panel_controller
        # Two windows with running agents: the expand or collapse and the pause or resume buttons show.
        self.list_controller.apply_snapshot(make_sample_snapshot())
        # Narrowed and widened again, as dragging the sidebar's edge does; 320 is the dropdown's
        # smallest width, 250 the list window's and 200 the sidebar's. Only a sidebar under 231
        # points hides the pop-up (the column titles still sort the list).
        for width, label_shown, popup_shown, popup_width, settings_button_is_centered in (
                (460, True, True, 124, True), (327, True, True, 124, False), (326, False, True, 124, False),
                (320, False, True, 124, False), (265, False, True, 94, False), (250, False, True, 79, False),
                (231, False, True, 60, False), (230, False, False, 59, False), (200, False, False, 29, False),
                (460, True, True, 124, True)):
            self.window.setContentSize_((width, 260))
            self.assertEqual((not panel.sort_label.isHidden(), not panel.list_sort_order_popup.isHidden(),
                              panel.list_sort_order_popup.frame().size.width), (label_shown, popup_shown, popup_width), width)
            # Hide Idle right before the expand button; Settings in the middle of the bar when the
            # whole sort control fits on its left, and never past Hide Idle.
            idle_tabs_button_frame = panel.idle_tabs_button.frame()
            self.assertFalse(panel.idle_tabs_button.isHidden())
            self.assertEqual(idle_tabs_button_frame.origin.x + idle_tabs_button_frame.size.width + GAP_BETWEEN_TOP_BAR_BUTTONS,
                             panel.expand_or_collapse_all_button.frame().origin.x)
            settings_button_frame = panel.settings_button.frame()
            self.assertEqual(abs(settings_button_frame.origin.x + settings_button_frame.size.width / 2 - width / 2) < 0.5,
                             settings_button_is_centered, width)
            self.assertLessEqual(settings_button_frame.origin.x + settings_button_frame.size.width + GAP_BETWEEN_TOP_BAR_BUTTONS,
                                 idle_tabs_button_frame.origin.x)
            if popup_shown:
                popup_frame = panel.list_sort_order_popup.frame()
                self.assertLessEqual(popup_frame.origin.x + popup_frame.size.width, settings_button_frame.origin.x)
            message_frame = panel.message_text_field.frame()
            self.assertTrue(message_frame.size.width == 0
                            or message_frame.origin.x + message_frame.size.width <= settings_button_frame.origin.x,
                            width)  # never covers the buttons
            pause_button_frame = panel.pause_or_resume_all_button.frame()  # at the bottom bar's right end
            self.assertEqual(pause_button_frame.origin.x + pause_button_frame.size.width, width - PANEL_SIDE_MARGIN)
            self.assertLessEqual(pause_button_frame.origin.y + pause_button_frame.size.height, BOTTOM_BAR_HEIGHT)
        self.window.setContentSize_((320, 260))
        panel.show_message("iTerm2 can't be reached; retrying")  # the sort control and Hide Idle step aside for it
        self.assertTrue(panel.list_sort_order_popup.isHidden())
        self.assertTrue(panel.idle_tabs_button.isHidden())
        settings_button_frame = panel.settings_button.frame()  # moved to the right end, before the expand button
        self.assertEqual(settings_button_frame.origin.x + settings_button_frame.size.width + GAP_BETWEEN_TOP_BAR_BUTTONS,
                         panel.expand_or_collapse_all_button.frame().origin.x)
        self.assertGreater(panel.message_text_field.frame().size.width, 150)
        panel.show_message("")
        self.assertFalse(panel.list_sort_order_popup.isHidden())
        self.assertFalse(panel.idle_tabs_button.isHidden())

    def test_hide_idle_hides_idle_tabs_and_windows_left_without_tabs_until_show_idle(self):
        panel = self.dropdown_panel_controller
        self.list_controller.apply_snapshot(make_sample_snapshot())  # the second window's two tabs are idle
        panel.idleTabsButtonClicked_(panel.idle_tabs_button)
        self.assertEqual(self.owner.idle_tabs_are_hidden_choices, [True])  # the owner saves it and shows it in every list
        panel.show_idle_tabs_are_hidden(True)
        outline_view = self.list_controller.outline_view
        self.assertEqual([outline_view.itemAtRow_(row).item_id for row in range(outline_view.numberOfRows())],
                         ["w1", "1", "2", "3", "new_window"])
        self.assertEqual((panel.idle_tabs_button.title(), panel.idle_tabs_button.toolTip()),
                         ("Show Idle", "Show the tabs whose agent is idle again"))
        snapshot = make_sample_snapshot()  # a new snapshot is filtered too: planning becomes idle
        window_one, window_two = snapshot.monitors[0].windows
        window_one = window_one._replace(agent_sessions=(window_one.agent_sessions[0]._replace(status="idle"),)
                                         + window_one.agent_sessions[1:])
        self.list_controller.apply_snapshot(snapshot._replace(monitors=(snapshot.monitors[0]._replace(windows=(window_one, window_two)),)))
        self.assertEqual(outline_view.numberOfRows(), 4)  # w1, phase0-tests (asking), codex-review (done), "+"
        # Only rows are hidden: the window row still stands for its whole window (New tab starts in
        # the selected tab's folder, Close window counts every agent), and the bottom bar's pause
        # button still acts on every agent session.
        self.assertEqual(len(outline_view.itemAtRow_(0).entry.agent_sessions), 3)
        self.assertEqual(len(self.list_controller.list_agent_session_entries()), 5)
        self.select_rows(0, 1)  # the window and phase0-tests: Kill counts and ends the hidden idle planning too
        menu_items_by_title = self.build_right_click_menu_items_by_title(1)
        self.owner.answer_to_confirmation = True
        self.list_controller.menuKillSelectedSessions_(menu_items_by_title["Kill 3 Sessions"])
        self.assertEqual((self.submitted_actions[-1].action, self.submitted_actions[-1].target),
                         ("end_agent_processes", {"session_ids": ["s1", "s2", "s3"]}))
        panel.show_idle_tabs_are_hidden(False)
        self.assertEqual(outline_view.numberOfRows(), 8)
        self.assertEqual(panel.idle_tabs_button.title(), "Hide Idle")

    def test_hide_idle_keeps_hidden_rows_collapsed_or_with_their_previews_open(self):
        outline_view = self.apply_snapshot_with_previews()
        self.click_row_arrow(5)  # opens analysis-0's preview (idle)
        self.click_row_arrow(4)  # collapses tandem-repeats (both tabs idle)
        self.dropdown_panel_controller.show_idle_tabs_are_hidden(True)
        self.assertEqual(outline_view.numberOfRows(), 5)  # w1, its three tabs, "+"
        self.dropdown_panel_controller.show_idle_tabs_are_hidden(False)
        self.assertEqual(outline_view.numberOfRows(), 6)  # tandem-repeats comes back collapsed
        self.click_row_arrow(4)
        self.assertEqual(outline_view.rectOfRow_(5).size.height, TAB_ROW_HEIGHT_WITH_PREVIEW)  # and the preview open
        self.click_row_arrow(4)  # collapsed again
        self.dropdown_panel_controller.show_idle_tabs_are_hidden(True)
        self.list_controller.set_all_expanded(False)  # Collapse All: agent-command-tab, and tandem-repeats stays collapsed
        self.dropdown_panel_controller.show_idle_tabs_are_hidden(False)
        self.assertEqual(outline_view.numberOfRows(), 3)  # the two collapsed windows and "+"

    def test_hide_idle_leaves_a_monitor_row_its_pause_item_when_all_of_its_windows_are_hidden(self):
        self.list_controller.apply_snapshot(make_two_monitor_snapshot(make_sample_snapshot()))  # Studio Display: two idle tabs
        self.dropdown_panel_controller.show_idle_tabs_are_hidden(True)
        outline_view = self.list_controller.outline_view
        studio_display_row = next(row for row in range(outline_view.numberOfRows())
                                  if outline_view.itemAtRow_(row).key == ("monitor", "2"))
        self.assertEqual(self.build_right_click_menu_titles(studio_display_row), ["Pause 2 Sessions"])

    def test_sort_popup_reports_the_choice_to_the_owner(self):
        self.dropdown_panel_controller.list_sort_order_popup.selectItemWithTitle_("Activity")
        self.dropdown_panel_controller.listSortOrderChosen_(self.dropdown_panel_controller.list_sort_order_popup)
        self.assertEqual(self.owner.chosen_list_sort_orders, [("activity", False)])  # the pop-up's orders start in their first direction
        self.dropdown_panel_controller.show_list_sort_order("alphabet", False)
        self.assertEqual(self.dropdown_panel_controller.list_sort_order_popup.titleOfSelectedItem(), "Alphabetically")
        self.assertEqual(self.list_controller.list_sort_order, "alphabet")

    def test_window_menu_offers_new_agent_sessions_in_the_window_folder(self):
        self.list_controller.apply_snapshot(make_sample_snapshot())
        window_item = self.list_controller.outline_view.itemAtRow_(0)
        self.list_controller.menu_target_item = window_item
        menu_item = type("MenuItemStandIn", (), {"representedObject": lambda self: "Codex"})()
        self.list_controller.menuNewAgentSessionInWindow_(menu_item)
        self.assertEqual(self.owner.started_agent_sessions, [("w1", "/x", "Codex")])

    def build_right_click_menu(self, row):
        """Returns the menu the list builds as if the row were right-clicked."""
        item = self.list_controller.outline_view.itemAtRow_(row)
        self.list_controller.find_clicked_item = lambda: item
        menu = NSMenu.alloc().initWithTitle_("")
        self.list_controller.menuNeedsUpdate_(menu)
        return menu

    def build_right_click_menu_titles(self, row):
        """Returns the right-click menu's item titles, with "---" for separators."""
        return ["---" if menu_item.isSeparatorItem() else menu_item.title() for menu_item in self.build_right_click_menu(row).itemArray()]

    def build_right_click_menu_items_by_title(self, row):
        return {menu_item.title(): menu_item for menu_item in self.build_right_click_menu(row).itemArray()}

    def test_window_and_tab_right_click_menus(self):
        self.list_controller.apply_snapshot(make_sample_snapshot())
        self.assertEqual(self.build_right_click_menu_titles(0),
                         ["Collapse", "---", "Show window", "Rename", "---", "New tab with Claude Code session…",
                          "New tab with Codex session…", "---", "Pause 3 Sessions", "---", "Close window"])
        self.assertEqual(self.build_right_click_menu_titles(1),
                         ["Show tab", "Rename", "Duplicate this tab", "Move to window", "Extract to new window", "---",
                          "/compact", "/clear", "Kill Session", "---", "Pause Session"])

    def test_pause_and_resume_in_the_row_menus_and_the_top_bar(self):
        snapshot = make_sample_snapshot()
        window_one, window_two = snapshot.monitors[0].windows
        with_one_paused = snapshot._replace(monitors=(snapshot.monitors[0]._replace(windows=(window_one._replace(
            agent_sessions=(window_one.agent_sessions[0]._replace(status="paused", is_paused_with_the_processes_below_it=True),)
            + window_one.agent_sessions[1:]),
            window_two)),))
        self.list_controller.apply_snapshot(with_one_paused)
        window_menu_titles = self.build_right_click_menu_titles(0)
        self.assertIn("Pause 2 Sessions", window_menu_titles)
        self.assertIn("Resume 1 Session", window_menu_titles)
        tab_menu_items_by_title = self.build_right_click_menu_items_by_title(1)  # "planning", paused
        self.assertNotIn("Pause Session", tab_menu_items_by_title)
        self.list_controller.menuResumeSessions_(tab_menu_items_by_title["Resume Session"])
        self.assertEqual((self.submitted_actions[-1].action, self.submitted_actions[-1].target),
                         ("resume_agent_sessions", {"session_ids": ["s1"]}))
        panel = self.dropdown_panel_controller
        button = panel.pause_or_resume_all_button
        self.assertEqual((button.isHidden(), button.toolTip()), (False, "Pause all agent sessions"))
        panel.pauseOrResumeAllClicked_(button)  # pauses the sessions that run
        self.assertEqual((self.submitted_actions[-1].action, self.submitted_actions[-1].target),
                         ("pause_agent_sessions", {"session_ids": ["s2", "s3", "s10", "s11"]}))
        all_paused = snapshot._replace(monitors=(snapshot.monitors[0]._replace(windows=tuple(
            window._replace(agent_sessions=tuple(entry._replace(status="paused", is_paused_with_the_processes_below_it=True)
                                                 for entry in window.agent_sessions))
            for window in snapshot.monitors[0].windows)),))
        self.list_controller.apply_snapshot(all_paused)
        self.assertEqual(button.toolTip(), "Resume all paused agent sessions")
        panel.pauseOrResumeAllClicked_(button)
        self.assertEqual((self.submitted_actions[-1].action, self.submitted_actions[-1].target),
                         ("resume_agent_sessions", {"session_ids": ["s1", "s2", "s3", "s10", "s11"]}))
        self.list_controller.apply_snapshot(make_two_monitor_snapshot(snapshot))
        self.assertEqual(self.build_right_click_menu_titles(0)[-1], "Pause 3 Sessions")  # a monitor row's menu

    def test_slash_commands_only_at_the_prompt_and_clear_and_kill_ask_first(self):
        self.list_controller.apply_snapshot(make_sample_snapshot())
        menu_items_by_title = self.build_right_click_menu_items_by_title(1)  # "planning" is working
        self.assertFalse(menu_items_by_title["/compact"].isEnabled())
        self.assertTrue(menu_items_by_title["Kill Session"].isEnabled())
        menu_items_by_title = self.build_right_click_menu_items_by_title(5)  # analysis-0 is idle
        self.assertTrue(menu_items_by_title["/compact"].isEnabled())
        self.list_controller.menuSendSlashCommand_(menu_items_by_title["/compact"])
        self.assertEqual((self.submitted_actions[-1].action, self.submitted_actions[-1].arguments),
                         ("send_slash_command", {"command": "/compact"}))
        self.list_controller.menuSendSlashCommand_(menu_items_by_title["/clear"])  # the user says No
        self.list_controller.menuKillSession_(menu_items_by_title["Kill Session"])
        self.assertEqual(len(self.owner.confirmation_questions), 2)
        self.assertEqual(len(self.submitted_actions), 1)
        self.owner.answer_to_confirmation = True
        self.list_controller.menuKillSession_(menu_items_by_title["Kill Session"])
        self.assertEqual((self.submitted_actions[-1].action, self.submitted_actions[-1].target),
                         ("end_agent_process", {"session_id": "s10"}))
        snapshot = make_sample_snapshot()
        window_one, window_two = snapshot.monitors[0].windows
        window_two = window_two._replace(agent_sessions=tuple(entry._replace(agent_tool_name="Aider", slash_commands=())
                                                              for entry in window_two.agent_sessions))
        self.list_controller.apply_snapshot(snapshot._replace(monitors=(snapshot.monitors[0]._replace(
            windows=(window_one, window_two)),)))
        menu_items_by_title = self.build_right_click_menu_items_by_title(5)  # idle, but its tool takes no commands
        self.assertFalse(menu_items_by_title["/compact"].isEnabled())
        self.assertIn("Aider", menu_items_by_title["/compact"].toolTip())

    def select_rows(self, *rows):
        self.list_controller.outline_view.selectRowIndexes_byExtendingSelection_(build_index_set(rows), False)

    def test_only_window_and_tab_rows_can_be_selected_and_the_selection_is_an_outline(self):
        self.list_controller.apply_snapshot(make_two_monitor_snapshot(make_sample_snapshot()))
        outline_view = self.list_controller.outline_view
        self.assertTrue(outline_view.allowsMultipleSelection())
        every_row = build_index_set(range(outline_view.numberOfRows()))  # a Shift-click range over everything
        allowed_rows = self.list_controller.outlineView_selectionIndexesForProposedSelection_(outline_view, every_row)
        self.assertEqual([outline_view.itemAtRow_(row).kind for row in range(outline_view.numberOfRows()) if allowed_rows.containsIndex_(row)],
                         ["window", "tab", "tab", "tab", "window", "tab", "tab"])  # no monitor rows, no "+" row
        row_view = self.list_controller.outlineView_rowViewForItem_(outline_view, outline_view.itemAtRow_(1))
        self.assertIsInstance(row_view, RowViewWithSelectionOutline)
        self.assertEqual(row_view.interiorBackgroundStyle(), NSBackgroundStyleNormal)  # the text keeps its colors

    def test_adjacent_selected_rows_draw_one_outline_around_them_all(self):
        bounds = NSMakeRect(0, 0, 300, 22)
        # Inset by 1 point; a side shared with a selected neighbor runs on 6.5 points past the row, where its clip cuts it off.
        self.assertEqual(tuple(compute_selection_outline_rect(bounds, True, False, False)), ((1, 1), (298, 20)))
        self.assertEqual(tuple(compute_selection_outline_rect(bounds, True, True, False)), ((1, -5.5), (298, 26.5)))
        self.assertEqual(tuple(compute_selection_outline_rect(bounds, True, False, True)), ((1, 1), (298, 26.5)))
        self.assertEqual(tuple(compute_selection_outline_rect(bounds, False, False, True)), ((1, -5.5), (298, 26.5)))
        self.list_controller.apply_snapshot(make_sample_snapshot())
        self.select_rows(1, 2, 3, 5)
        content_view = self.window.contentView()  # draws the selected rows, which raises if the outline can't be built
        content_view.cacheDisplayInRect_toBitmapImageRep_(
            content_view.bounds(), content_view.bitmapImageRepForCachingDisplayInRect_(content_view.bounds()))
        row_views = [self.list_controller.outline_view.rowViewAtRow_makeIfNecessary_(row, False) for row in (1, 2, 3, 5)]
        self.assertEqual([(row_view.isPreviousRowSelected(), row_view.isNextRowSelected()) for row_view in row_views],
                         [(False, True), (True, True), (True, False), (False, False)])

    def test_several_selected_rows_share_one_menu_that_acts_on_their_sessions(self):
        self.list_controller.apply_snapshot(make_sample_snapshot())
        self.select_rows(3, 4)  # codex-review (done) and the second window (two idle tabs)
        self.assertEqual(self.build_right_click_menu_titles(4),
                         ["/compact 3 Sessions", "/clear 3 Sessions", "Kill 3 Sessions", "---", "Pause 3 Sessions", "---",
                          "Talk to Each Other…"])
        self.assertIsNone(self.build_right_click_menu_items_by_title(4)["/compact 3 Sessions"].toolTip())  # all at their prompt
        self.select_rows(0, 1)  # a window and one of its tabs: each session counts once
        self.assertIn("/compact 3 Sessions", self.build_right_click_menu_titles(1))
        self.assertIn("Kill 3 Sessions", self.build_right_click_menu_titles(1))  # a window row is selected
        self.select_rows(1, 2)  # working and asking: each gets the command when its turn ends (user request, 2026-10-21)
        menu_items_by_title = self.build_right_click_menu_items_by_title(1)
        self.assertIn("Kill Sessions", menu_items_by_title)  # tab rows alone: the selection shows how many
        self.assertTrue(menu_items_by_title["/compact 2 Sessions"].isEnabled())
        self.assertEqual(menu_items_by_title["/compact 2 Sessions"].toolTip(), "A session in the middle of a turn gets it when the turn ends")
        self.assertEqual(self.build_right_click_menu_titles(5)[0], "Show tab")  # an unselected row keeps its own menu
        self.select_rows(5)
        self.assertEqual(self.build_right_click_menu_titles(5)[0], "Show tab")  # so does a single selected row

    def test_the_menu_of_several_rows_types_commands_only_into_agents_that_take_them(self):
        snapshot = make_sample_snapshot()
        window_one, window_two = snapshot.monitors[0].windows
        window_two = window_two._replace(agent_sessions=tuple(entry._replace(agent_tool_name="Aider", slash_commands=())
                                                              for entry in window_two.agent_sessions))
        self.list_controller.apply_snapshot(snapshot._replace(monitors=(snapshot.monitors[0]._replace(
            windows=(window_one, window_two)),)))
        self.select_rows(3, 5)  # codex-review (Codex) and analysis-0 (Aider)
        self.assertEqual(self.build_right_click_menu_items_by_title(3)["/compact 1 Session"].toolTip(),
                         "Typed only into the 1 of 2 whose agent takes it and isn't paused")
        self.select_rows(5, 6)  # two Aider tabs
        menu_items_by_title = self.build_right_click_menu_items_by_title(5)
        self.assertFalse(menu_items_by_title["/compact"].isEnabled())
        self.assertEqual(menu_items_by_title["/compact"].toolTip(), "The dashboard can't type commands into Aider")

    def test_the_menu_of_several_rows_leaves_out_paused_sessions(self):
        # The core can't type into a suspended agent, so a paused session isn't counted.
        snapshot = make_sample_snapshot()
        window_one, window_two = snapshot.monitors[0].windows
        window_two = window_two._replace(agent_sessions=tuple(entry._replace(status="paused") for entry in window_two.agent_sessions))
        self.list_controller.apply_snapshot(snapshot._replace(monitors=(snapshot.monitors[0]._replace(
            windows=(window_one, window_two)),)))
        self.select_rows(3, 5)  # codex-review (done) and analysis-0 (paused)
        self.assertEqual(self.build_right_click_menu_items_by_title(3)["/compact 1 Session"].toolTip(),
                         "Typed only into the 1 of 2 whose agent takes it and isn't paused")
        self.select_rows(5, 6)  # both paused
        menu_items_by_title = self.build_right_click_menu_items_by_title(5)
        self.assertFalse(menu_items_by_title["/compact"].isEnabled())
        self.assertEqual(menu_items_by_title["/compact"].toolTip(), "Only after the paused sessions are resumed")
        self.assertIn("Resume 2 Sessions", menu_items_by_title)

    def test_slash_commands_and_kill_for_several_sessions_ask_first_where_one_session_would(self):
        self.list_controller.apply_snapshot(make_sample_snapshot())
        self.select_rows(1, 5)  # planning (working) and analysis-0 (idle)
        menu_items_by_title = self.build_right_click_menu_items_by_title(5)
        self.list_controller.menuSendSlashCommandToSelectedSessions_(menu_items_by_title["/compact 2 Sessions"])
        self.assertEqual((self.submitted_actions[-1].action, self.submitted_actions[-1].target, self.submitted_actions[-1].arguments),
                         ("send_slash_command_to_agent_sessions", {"session_ids": ["s1", "s10"]}, {"command": "/compact"}))
        self.list_controller.menuSendSlashCommandToSelectedSessions_(menu_items_by_title["/clear 2 Sessions"])  # the user says No
        self.list_controller.menuKillSelectedSessions_(menu_items_by_title["Kill Sessions"])  # No again
        self.assertEqual(len(self.submitted_actions), 1)
        self.assertIn('("planning", "analysis-0")', self.owner.confirmation_questions[0])
        self.assertIn('("planning", "analysis-0")', self.owner.confirmation_questions[1])
        self.owner.answer_to_confirmation = True
        self.list_controller.menuKillSelectedSessions_(menu_items_by_title["Kill Sessions"])
        self.assertEqual((self.submitted_actions[-1].action, self.submitted_actions[-1].target),
                         ("end_agent_processes", {"session_ids": ["s1", "s10"]}))

    def test_talk_to_each_other_sends_the_note_to_the_sessions_whose_tools_can_take_part(self):
        snapshot = make_sample_snapshot()
        window_one, window_two = snapshot.monitors[0].windows
        window_two = window_two._replace(agent_sessions=(window_two.agent_sessions[0]._replace(agent_tool_name="Aider"),
                                                         window_two.agent_sessions[1]))
        self.list_controller.apply_snapshot(snapshot._replace(monitors=(snapshot.monitors[0]._replace(windows=(window_one, window_two)),)))
        self.select_rows(3, 4)  # codex-review (Codex), and analysis-0 (Aider) and analysis-1 (Claude Code)
        menu_items_by_title = self.build_right_click_menu_items_by_title(3)
        self.list_controller.menuTalkToEachOther_(menu_items_by_title["Talk to Each Other…"])  # Cancel
        self.assertEqual(self.submitted_actions, [])
        talking_entries, left_out_entries = self.owner.talk_to_each_other_requests[-1]
        self.assertEqual(([entry.tab_title for entry in talking_entries], [entry.tab_title for entry in left_out_entries]),
                         (["codex-review", "analysis-1"], ["analysis-0"]))
        self.owner.note_to_send = "Sync up, please."
        self.list_controller.menuTalkToEachOther_(menu_items_by_title["Talk to Each Other…"])
        self.assertEqual((self.submitted_actions[-1].action, self.submitted_actions[-1].target, self.submitted_actions[-1].arguments),
                         ("ask_agent_sessions_to_talk_to_each_other", {"session_ids": ["s3", "s11"]}, {"note": "Sync up, please."}))
        self.select_rows(3, 5)  # one Codex session and the Aider one: nobody to talk to
        self.assertFalse(self.build_right_click_menu_items_by_title(3)["Talk to Each Other…"].isEnabled())

    def test_the_talk_to_each_other_window_holds_the_default_note_and_names_who_gets_it(self):
        entries = [make_entry("1", "planning", "idle"), make_entry("2", "reviews", "working")]
        alert, note_text_view = build_talk_to_each_other_alert(entries, [make_entry("3", "aider tab", "idle", tool="Aider")])
        self.assertEqual(note_text_view.string(), DEFAULT_NOTE_ASKING_AGENT_SESSIONS_TO_TALK_TO_EACH_OTHER)
        self.assertIn("these 2 agents to each other:\n• planning\n• reviews", alert.informativeText())
        self.assertIn('Left out, since their tools can\'t take messages from other sessions: "aider tab".', alert.informativeText())
        send_button, cancel_button = alert.buttons()
        self.assertEqual((send_button.title(), cancel_button.title()), ("Send", "Cancel"))
        self.assertEqual(send_button.keyEquivalentModifierMask(), NSEventModifierFlagCommand)  # Return alone starts a new line
        self.assertIs(alert.window().initialFirstResponder(), note_text_view)
        self.assertFalse(note_text_view.isAutomaticDashSubstitutionEnabled())
        self.assertEqual(tuple(alert.icon().size()), (1, 1))  # blank, not the app's icon

    def test_dragging_one_of_several_selected_rows_drags_only_that_row(self):
        self.list_controller.apply_snapshot(make_sample_snapshot())
        outline_view = self.list_controller.outline_view
        self.select_rows(1, 2)
        self.list_controller.row_pressed(2)
        self.addCleanup(self.list_controller.row_released)
        self.assertIsNone(self.list_controller.outlineView_pasteboardWriterForItem_(outline_view, outline_view.itemAtRow_(1)))
        self.assertIsNotNone(self.list_controller.outlineView_pasteboardWriterForItem_(outline_view, outline_view.itemAtRow_(2)))

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

    def test_single_click_on_a_tab_sends_nothing_and_double_click_shows_it(self):
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

    def test_a_rename_ended_by_a_click_on_another_row_keeps_the_new_name_after_the_release(self):
        self.list_controller.apply_snapshot(make_sample_snapshot())
        self.window.contentView().layoutSubtreeIfNeeded()
        tab_item = self.list_controller.outline_view.itemAtRow_(1)
        self.list_controller.begin_rename(tab_item)
        snapshot = make_sample_snapshot()
        window_one, window_two = snapshot.monitors[0].windows
        window_one = window_one._replace(agent_sessions=window_one.agent_sessions[:1] + (
            window_one.agent_sessions[1]._replace(status="idle"),) + window_one.agent_sessions[2:])
        # Arrives during the rename, with another tab's change: held.
        self.list_controller.apply_snapshot(snapshot._replace(monitors=(snapshot.monitors[0]._replace(
            windows=(window_one, window_two)),)))
        row_view = self.list_controller.outline_view.viewAtColumn_row_makeIfNecessary_(0, 1, False)
        row_view.name_text_field.setStringValue_("new name")
        self.list_controller.row_pressed(3)  # the click on another row that ends the rename
        notification = type("NotificationStandIn", (), {"object": lambda self: row_view.name_text_field})()
        self.list_controller.controlTextDidEndEditing_(notification)
        self.list_controller.row_released()
        self.assertEqual(self.list_controller.items_by_key[("tab", "1")].entry.tab_title, "new name")
        self.assertEqual(self.list_controller.items_by_key[("tab", "2")].entry.status, "idle")  # the held snapshot shows too

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
        self.list_controller.set_list_sort_order("activity", False)
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
        self.assertGreater(self.list_controller.column_widths["context_tokens"], 0)
        self.assertGreater(self.list_controller.column_widths["agent_tool_icon"], 0)
        self.window.setContentSize_((200, 260))  # the narrowest sidebar
        self.window.contentView().layoutSubtreeIfNeeded()
        self.list_controller.measure_right_columns()
        self.assertEqual((self.list_controller.column_widths["context_tokens"], self.list_controller.column_widths["agent_tool_icon"]),
                         (0, 0))
        # The rest go too, the time since the last status change last, so the names stay readable.
        self.assertEqual(sum(self.list_controller.column_widths.values()), 0)
        self.assertGreaterEqual(self.list_controller.measure_room_left_for_tab_names(), SMALLEST_TAB_NAME_WIDTH)
        self.window.setContentSize_((300, 260))
        self.window.contentView().layoutSubtreeIfNeeded()
        self.list_controller.measure_right_columns()
        self.assertGreater(self.list_controller.column_widths["state_duration"], 0)  # kept while there is room for it

    def test_duplicate_is_greyed_out_for_a_tool_without_a_launch_command(self):
        snapshot = make_sample_snapshot()
        window_two = snapshot.monitors[0].windows[1]
        window_two = window_two._replace(agent_sessions=(window_two.agent_sessions[0]._replace(agent_tool_name="aider"),)
                                         + window_two.agent_sessions[1:])
        self.list_controller.apply_snapshot(snapshot._replace(monitors=(snapshot.monitors[0]._replace(
            windows=(snapshot.monitors[0].windows[0], window_two)),)))
        self.assertFalse(self.build_right_click_menu_items_by_title(5)["Duplicate this tab"].isEnabled())  # the "aider" tab

    def test_closing_the_dropdown_cancels_an_open_rename_so_the_list_keeps_updating(self):
        self.list_controller.apply_snapshot(make_sample_snapshot())
        self.window.contentView().layoutSubtreeIfNeeded()
        self.list_controller.begin_rename(self.list_controller.outline_view.itemAtRow_(1))
        self.assertTrue(self.list_controller.rename_in_progress)
        # A new name typed, so a rename submitted instead of cancelled would show.
        self.list_controller.outline_view.viewAtColumn_row_makeIfNecessary_(0, 1, False).name_text_field.currentEditor() \
            .setString_("typed but cancelled")
        self.list_controller.cancel_rename()
        self.assertFalse(self.list_controller.rename_in_progress)
        self.assertEqual(self.submitted_actions, [])  # nothing was renamed

    def test_the_measured_start_of_a_name_is_where_the_row_draws_it(self):
        self.list_controller.apply_snapshot(make_two_monitor_snapshot(make_sample_snapshot()))
        self.window.contentView().layoutSubtreeIfNeeded()
        for row in range(self.list_controller.outline_view.numberOfRows() - 1):  # all but the "+" row
            row_view = self.lay_out_row_view(row)
            parts = self.list_controller.build_row_text_parts(self.list_controller.outline_view.itemAtRow_(row))
            # Never short of where the row draws it, so measured widths don't cut text.
            excess = self.list_controller.measure_name_x(parts.connector_text, parts.dot_glyph) - row_view.name_text_field.frame().origin.x
            self.assertTrue(0 <= excess <= 2.5, (row, excess))

    def collapse_from_the_right_click_menu(self, row):
        self.list_controller.menu_target_item = self.list_controller.outline_view.itemAtRow_(row)
        self.list_controller.menuToggleExpansion_(None)

    def test_collapsing_the_window_of_a_tab_being_renamed_submits_the_rename_first(self):
        for collapse_number, collapse in enumerate((lambda: self.click_row_arrow(0), lambda: self.list_controller.set_all_expanded(False),
                                                    lambda: self.collapse_from_the_right_click_menu(0)), 1):
            self.list_controller.set_all_expanded(True)
            self.list_controller.apply_snapshot(make_sample_snapshot())
            self.window.contentView().layoutSubtreeIfNeeded()
            del self.submitted_actions[:]  # so each way of collapsing must submit its own rename
            self.list_controller.begin_rename(self.list_controller.outline_view.itemAtRow_(1))
            self.list_controller.outline_view.viewAtColumn_row_makeIfNecessary_(0, 1, False).name_text_field.currentEditor() \
                .setString_(f"renamed {collapse_number}")
            collapse()  # AppKit raised an exception here while the name was being edited
            self.assertFalse(self.list_controller.rename_in_progress)
            self.assertIn(("window", "w1"), self.list_controller.collapsed_item_keys)
            self.assertEqual([(action.action, action.arguments) for action in self.submitted_actions],
                             [("rename_tab", {"new_title": f"renamed {collapse_number}"})])

    def test_opening_previews_during_a_rename_submits_the_rename_first(self):
        self.apply_snapshot_with_previews()
        self.window.contentView().layoutSubtreeIfNeeded()
        tab_item = self.list_controller.outline_view.itemAtRow_(1)
        self.list_controller.begin_rename(tab_item)
        self.list_controller.outline_view.viewAtColumn_row_makeIfNecessary_(0, 1, False).name_text_field.currentEditor() \
            .setString_("renamed")
        self.list_controller.set_previews_open([tab_item], True)
        self.assertFalse(self.list_controller.rename_in_progress)
        self.assertEqual((self.submitted_actions[-1].action, self.submitted_actions[-1].arguments),
                         ("rename_tab", {"new_title": "renamed"}))
        self.assertTrue(self.lay_out_row_view(1).has_preview_line)  # refilled now that its name isn't being edited

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
        window = WindowEntry("w3", "two projects", False, (0, 0, 800, 600), 2, (
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

    def test_dropdown_keeps_its_width_as_the_numbers_and_states_change(self):
        self.list_controller.set_shown_columns(LIST_COLUMNS)
        now = time.time()

        def measure_dropdown_width(**changed_fields):
            snapshot = make_sample_snapshot()
            monitor = snapshot.monitors[0]
            self.list_controller.apply_snapshot(snapshot._replace(monitors=(monitor._replace(windows=tuple(
                window._replace(agent_sessions=tuple(entry._replace(**changed_fields) for entry in window.agent_sessions))
                for window in monitor.windows)),)))
            self.dropdown_panel_controller.fit_size_to_rows()
            return self.dropdown_panel_controller.preferredContentSize()[0]

        # "99k (9%)", "9M", "0%", "done" and "5s" grow to "100k (100%)", "1024M", "100%", "working" and "23h 59m".
        self.assertEqual(
            measure_dropdown_width(context_tokens=99_000, context_fill_percent=9, memory_bytes=9 * 1024 ** 2, cpu_percent=0,
                                   started_at=now - 5, status="done", status_since=now - 5),
            measure_dropdown_width(context_tokens=100_000, context_fill_percent=100, memory_bytes=1024 ** 3 - 1, cpu_percent=100,
                                   started_at=now - 86399, status="working", status_since=now - 86399))

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

    def test_the_previews_button_has_a_column_of_its_own_at_the_right_edge(self):
        self.apply_snapshot_with_previews()
        self.window.contentView().layoutSubtreeIfNeeded()
        button_frame = self.lay_out_row_view(0).preview_toggle_button.frame()  # the first window's row
        last_column_frame = self.lay_out_row_view(1).column_views["state_duration"].frame()  # its first tab's
        # The tab rows' columns and their titles end a gap before the button, which has no title.
        self.assertEqual(last_column_frame.origin.x + last_column_frame.size.width + GAP_BETWEEN_COLUMNS, button_frame.origin.x)
        titles_row_view = self.list_controller.column_titles_row_view
        titles_row_view.layoutSubtreeIfNeeded()
        last_title_frame = titles_row_view.column_title_text_fields["state_duration"].frame()
        self.assertEqual(last_title_frame.origin.x + last_title_frame.size.width, last_column_frame.origin.x + last_column_frame.size.width)
        self.list_controller.apply_snapshot(make_sample_snapshot())  # no previews, so no button: the columns end at the right margin
        self.window.contentView().layoutSubtreeIfNeeded()
        last_column_frame = self.lay_out_row_view(1).column_views["state_duration"].frame()
        self.assertEqual(last_column_frame.origin.x + last_column_frame.size.width, button_frame.origin.x + button_frame.size.width)

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
        self.assertEqual(self.list_controller.measure_fully_expanded_list_height(), 7 * ROW_HEIGHT + TAB_ROW_HEIGHT_WITH_PREVIEW)
        self.assertEqual(self.lay_out_row_view(1).disclosure_button.image().accessibilityDescription(), "Close")
        self.click_row_arrow(1)
        self.assertEqual(outline_view.rectOfRow_(1).size.height, ROW_HEIGHT)

    def test_a_single_click_on_a_row_opens_and_closes_nothing(self):
        outline_view = self.apply_snapshot_with_previews()
        for row in (0, 1):
            item = outline_view.itemAtRow_(row)
            self.list_controller.find_clicked_item = lambda: item
            self.list_controller.rowClicked_(None)
        self.assertEqual((outline_view.numberOfRows(), outline_view.rectOfRow_(1).size.height), (8, ROW_HEIGHT))

    def test_the_arrow_on_a_window_collapses_just_that_window(self):
        outline_view = self.apply_snapshot_with_previews()
        self.click_row_arrow(5)  # a preview in the second window
        self.click_row_arrow(4)  # the second window
        self.assertEqual((outline_view.numberOfRows(), self.list_controller.collapsed_item_keys), (6, {("window", "w2")}))
        self.click_row_arrow(4)
        self.assertEqual(outline_view.rectOfRow_(5).size.height, TAB_ROW_HEIGHT_WITH_PREVIEW)  # comes back as it was

    def test_expand_all_and_collapse_all_leave_the_previews_as_they_are(self):
        outline_view = self.apply_snapshot_with_previews()
        self.click_row_arrow(1)  # opens one preview
        self.list_controller.set_all_expanded(False)
        self.assertEqual(outline_view.numberOfRows(), 3)  # 2 windows and the "+" row
        self.list_controller.set_all_expanded(True)
        self.assertEqual([outline_view.rectOfRow_(row).size.height for row in (1, 5, 6)],
                         [TAB_ROW_HEIGHT_WITH_PREVIEW, ROW_HEIGHT, ROW_HEIGHT])

    def test_the_top_bar_has_one_button_that_expands_or_collapses_all(self):
        panel = self.dropdown_panel_controller
        self.list_controller.apply_snapshot(make_sample_snapshot())
        button = panel.expand_or_collapse_all_button
        panel_width = panel.view().bounds().size.width
        self.assertEqual(button.frame().origin.x + button.frame().size.width, panel_width - PANEL_SIDE_MARGIN)  # the top right corner
        settings_frame = panel.settings_button.frame()  # a button with its name, in the top bar
        self.assertEqual(panel.settings_button.title(), "Settings")
        self.assertGreater(settings_frame.origin.y, panel.view().bounds().size.height - TOP_BAR_HEIGHT)
        self.assertEqual(button.toolTip(), "Collapse all")  # fully expanded: a click collapses
        panel.expandOrCollapseAllClicked_(button)
        self.assertEqual((self.list_controller.outline_view.numberOfRows(), button.toolTip()), (3, "Expand all"))
        self.list_controller.toggle_expansion(self.list_controller.outline_view.itemAtRow_(0))  # one window expanded
        self.assertEqual(button.toolTip(), "Expand all")
        panel.expandOrCollapseAllClicked_(button)
        self.assertEqual((self.list_controller.outline_view.numberOfRows(), button.toolTip()), (8, "Collapse all"))

    def test_a_collapsed_monitor_left_without_windows_doesnt_count_as_collapsed(self):
        two_monitor_snapshot = make_two_monitor_snapshot(make_sample_snapshot())
        self.list_controller.apply_snapshot(two_monitor_snapshot)
        self.list_controller.toggle_expansion(self.list_controller.outline_view.itemAtRow_(0))  # the first monitor
        self.assertFalse(self.list_controller.is_fully_expanded())
        first_monitor, second_monitor = two_monitor_snapshot.monitors
        self.list_controller.apply_snapshot(two_monitor_snapshot._replace(monitors=(  # its window moved to the other monitor
            first_monitor._replace(windows=()), second_monitor._replace(windows=first_monitor.windows + second_monitor.windows))))
        self.assertTrue(self.list_controller.is_fully_expanded())

    def test_a_snapshot_waits_while_a_row_button_is_held(self):
        outline_view = self.apply_snapshot_with_previews()
        arrow = self.lay_out_row_view(1).disclosure_button
        self.assertIsInstance(arrow, RowButton)
        self.assertIs(arrow.target(), self.list_controller)
        self.list_controller.row_press_in_progress = True  # what RowButton.mouseDown_ sets while the button is down
        self.list_controller.apply_snapshot(make_sample_snapshot(second_window_tabs=3))
        self.assertEqual(outline_view.numberOfRows(), 8)  # held
        self.list_controller.row_press_in_progress = False
        self.list_controller.apply_held_snapshot()
        self.assertEqual(outline_view.numberOfRows(), 9)

    def test_the_plus_row_ends_the_list_on_the_tree_line_and_offers_a_new_window_per_included_tool(self):
        self.list_controller.apply_snapshot(make_sample_snapshot())
        self.window.contentView().layoutSubtreeIfNeeded()
        outline_view = self.list_controller.outline_view
        plus_row = outline_view.numberOfRows() - 1
        plus_row_view = self.lay_out_row_view(plus_row)
        plus_button = plus_row_view.new_window_button
        self.assertFalse(plus_button.isHidden())
        self.assertTrue(self.lay_out_row_view(1).new_window_button.isHidden())  # only on the "+" row
        tab_row_view = self.lay_out_row_view(plus_row - 1)  # the last tab: "└─ "
        connector_frame = tab_row_view.prefix_text_field.frame()
        plus_center_x = plus_button.frame().origin.x + plus_button.frame().size.width / 2
        self.assertLess(abs(plus_center_x - self.list_controller.measure_tree_line_x()), 0.5)
        self.assertLess(connector_frame.origin.x, plus_center_x)
        self.assertLess(plus_center_x, connector_frame.origin.x + connector_frame.size.width / 3)  # under the "└"
        self.assertEqual(self.build_right_click_menu_titles(plus_row), ["New Claude Code Window", "New Codex Window"])
        # Every clickable icon shows the pointing hand.
        for button in (plus_button, tab_row_view.disclosure_button, self.dropdown_panel_controller.settings_button,
                       self.dropdown_panel_controller.expand_or_collapse_all_button):
            self.assertIsInstance(button, PointingHandButton)
            self.assertTrue(button.trackingAreas())
        self.assertEqual(outline_view.rectOfRow_(plus_row).size.height, ROW_HEIGHT)
        self.assertEqual(self.list_controller.outline_view.itemAtRow_(plus_row).kind, "new_window")

    def test_the_expand_or_collapse_button_shows_only_with_two_or_more_windows(self):
        panel = self.dropdown_panel_controller
        button = panel.expand_or_collapse_all_button
        self.assertTrue(button.isHidden())  # no windows yet
        one_window_snapshot = make_sample_snapshot()
        one_window_snapshot = one_window_snapshot._replace(monitors=(one_window_snapshot.monitors[0]._replace(
            windows=one_window_snapshot.monitors[0].windows[:1]),))
        self.list_controller.apply_snapshot(one_window_snapshot)
        self.assertTrue(button.isHidden())
        self.window.setContentSize_((320, 260))
        # Without the expand button, Hide Idle is at the right end; the 320-point dropdown has no
        # room to center Settings, so it follows the whole sort control.
        idle_tabs_button_frame = panel.idle_tabs_button.frame()
        self.assertEqual(idle_tabs_button_frame.origin.x + idle_tabs_button_frame.size.width, 320 - PANEL_SIDE_MARGIN)
        popup_frame = panel.list_sort_order_popup.frame()
        settings_button_frame = panel.settings_button.frame()
        self.assertGreater(settings_button_frame.origin.x, popup_frame.origin.x + popup_frame.size.width)
        self.assertNotAlmostEqual(settings_button_frame.origin.x + settings_button_frame.size.width / 2, 160)
        # A message (iTerm2 can't be reached) takes the room up to the Settings button, at the right
        # end, with the Hide Idle button hidden.
        panel.show_message("iTerm2 can't be reached")
        message_frame = panel.message_text_field.frame()
        settings_button_frame = panel.settings_button.frame()
        self.assertEqual(settings_button_frame.origin.x + settings_button_frame.size.width, 320 - PANEL_SIDE_MARGIN)
        self.assertEqual(message_frame.origin.x + message_frame.size.width, settings_button_frame.origin.x - 8)
        panel.show_message("")
        self.list_controller.apply_snapshot(make_sample_snapshot())
        self.assertFalse(button.isHidden())
        self.assertEqual(button.frame().origin.x + button.frame().size.width, panel.view().bounds().size.width - PANEL_SIDE_MARGIN)
        panel.show_message("iTerm2 can't be reached")  # then Settings sits right before the expand button
        message_frame = panel.message_text_field.frame()
        settings_button_frame = panel.settings_button.frame()
        self.assertEqual(settings_button_frame.origin.x + settings_button_frame.size.width + GAP_BETWEEN_TOP_BAR_BUTTONS,
                         button.frame().origin.x)
        self.assertEqual(message_frame.origin.x + message_frame.size.width, settings_button_frame.origin.x - 8)
        panel.show_message("")
        self.list_controller.apply_snapshot(make_two_monitor_snapshot(make_sample_snapshot()))  # one window per monitor
        self.assertFalse(button.isHidden())

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
        column_views_by_name = {"icon": [row_view.column_views["agent_tool_icon"] for row_view in row_views],
                                "state": [row_view.column_views["state"] for row_view in row_views],
                                "time": [row_view.column_views["state_duration"] for row_view in row_views]}
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
        self.assertEqual(read_text_alignment(row_views[0].column_views["state_duration"]), NSTextAlignmentRight)
        self.assertNotEqual(read_text_alignment(row_views[0].column_views["state"]), NSTextAlignmentRight)
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
        self.assertEqual(self.list_controller.column_widths["agent_tool_icon"], 0)
        self.assertTrue(all(row_view.column_views["agent_tool_icon"].isHidden() for row_view in row_views))

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
        self.assertEqual(row_view.column_views["state"].stringValue(), "working")
        self.assertIsNone(row_view.toolTip())  # no hover popups on rows (user request, 2026-10-19)
        self.assertTrue(row_view.prefix_text_field.stringValue().startswith("├─ "))
        image_path = os.environ.get("AGENT_COMMAND_TAB_SMOKE_TEST_IMAGE")
        if image_path:
            bitmap = content_view.bitmapImageRepForCachingDisplayInRect_(content_view.bounds())
            content_view.cacheDisplayInRect_toBitmapImageRep_(content_view.bounds(), bitmap)
            bitmap.representationUsingType_properties_(NSBitmapImageFileTypePNG, {}).writeToFile_atomically_(image_path, True)

    # ----- process rows, background lights, exited rows and the hooks note (user requests, 2026-10-27) -----

    def apply_snapshot_with_child_processes(self, child_processes=None, preview_line=""):
        """Applies the sample snapshot with child processes (and a preview, if given) on the first tab."""
        snapshot = make_sample_snapshot()
        child_processes = child_processes if child_processes is not None else (
            ChildProcessEntry(201, "caffeinate -i -t 300", 900.0, False, None, None),
            ChildProcessEntry(202, "python3 -c import time; time.sleep(600) " + "x" * 300, 950.0, True, 300 * 1024 ** 2, 12))
        window_one, window_two = snapshot.monitors[0].windows
        window_one = window_one._replace(agent_sessions=(
            window_one.agent_sessions[0]._replace(child_processes=child_processes, preview_line=preview_line),)
            + window_one.agent_sessions[1:])
        self.list_controller.apply_snapshot(snapshot._replace(monitors=(snapshot.monitors[0]._replace(windows=(window_one, window_two)),)))
        return self.list_controller.outline_view

    def test_an_open_tab_row_has_a_sub_row_for_each_process_its_agent_started(self):
        outline_view = self.apply_snapshot_with_child_processes()
        self.dropdown_panel_controller.fit_size_to_rows()
        closed_width = self.list_controller.measure_widest_row_width()
        self.assertEqual(outline_view.numberOfRows(), 8)  # closed at first
        self.window.contentView().layoutSubtreeIfNeeded()
        self.assertFalse(self.lay_out_row_view(1).disclosure_button.isHidden())  # it has processes to show
        self.click_row_arrow(1)
        self.assertEqual(outline_view.numberOfRows(), 10)
        process_rows = [self.lay_out_row_view(row) for row in (2, 3)]
        self.assertEqual([outline_view.itemAtRow_(row).kind for row in (2, 3)], ["process", "process"])
        self.assertEqual([row_view.prefix_text_field.stringValue() for row_view in process_rows], ["│  ├─ ", "│  └─ "])
        self.assertEqual([row_view.dot_text_field.stringValue() for row_view in process_rows], ["· ", "• "])  # • in the background
        self.assertEqual(process_rows[0].name_text_field.stringValue(), "caffeinate -i -t 300")
        self.assertTrue(process_rows[1].disclosure_button.isHidden())
        self.assertFalse(process_rows[1].column_views["state_duration"].isHidden())  # the columns line up with the tabs'
        self.assertEqual(process_rows[1].column_views["state"].stringValue(), "")
        self.assertEqual(self.list_controller.measure_widest_row_width(), closed_width)  # a long command doesn't widen the dropdown
        # The command runs on over the columns a process row leaves empty (the default ones), up to the first with text.
        name_frame = process_rows[1].name_text_field.frame()
        self.assertGreater(name_frame.origin.x + name_frame.size.width, process_rows[1].column_views["state"].frame().origin.x)
        self.assertLessEqual(name_frame.origin.x + name_frame.size.width, process_rows[1].frame().size.width)
        self.list_controller.set_shown_columns(["context_tokens", "memory_usage", "state", "state_duration"])
        name_frame = self.lay_out_row_view(3).name_text_field.frame()
        self.assertLessEqual(name_frame.origin.x + name_frame.size.width, self.lay_out_row_view(3).column_views["memory_usage"].frame().origin.x)
        # Not selectable; a double-click shows its tab.
        self.assertEqual(self.list_controller.outlineView_selectionIndexesForProposedSelection_(
            outline_view, build_index_set([2, 3])).count(), 0)
        process_item = outline_view.itemAtRow_(2)
        self.list_controller.find_clicked_item = lambda: process_item
        self.list_controller.rowDoubleClicked_(None)
        self.assertEqual([(request.action, request.target) for request in self.submitted_actions],
                         [("focus_agent_session", {"session_id": "s1"})])
        # A process ends while the row is open; then the row closes.
        self.apply_snapshot_with_child_processes(child_processes=(ChildProcessEntry(201, "caffeinate", 900.0, False, None, None),))
        self.assertEqual(outline_view.numberOfRows(), 9)
        self.click_row_arrow(1)
        self.assertEqual(outline_view.numberOfRows(), 8)

    def test_process_rows_come_once_the_arrows_button_is_up(self):
        outline_view = self.apply_snapshot_with_child_processes()
        self.list_controller.row_press_in_progress = True  # as RowButton.mouseDown_ sets it around the click's action
        self.click_row_arrow(1)
        self.assertEqual(outline_view.numberOfRows(), 8)  # held while the button is down
        self.list_controller.row_released()
        self.assertEqual(outline_view.numberOfRows(), 10)

    def test_the_line_down_to_the_process_rows_runs_beside_the_preview(self):
        outline_view = self.apply_snapshot_with_child_processes(preview_line="Bash: sleep 600")
        self.click_row_arrow(1)
        self.assertEqual([outline_view.rectOfRow_(row).size.height for row in (1, 2, 3)],
                         [TAB_ROW_HEIGHT_WITH_PREVIEW, ROW_HEIGHT, ROW_HEIGHT])
        self.assertEqual(self.lay_out_row_view(1).preview_connector_text_field.stringValue(), "│  │\n│  │")
        self.list_controller.set_all_expanded(False)  # collapsing all leaves the tab row open
        self.list_controller.set_all_expanded(True)
        self.assertEqual(outline_view.numberOfRows(), 10)

    def test_background_tasks_light_up_after_the_tab_name(self):
        snapshot = make_sample_snapshot()
        window_one, window_two = snapshot.monitors[0].windows
        window_one = window_one._replace(agent_sessions=(window_one.agent_sessions[0]._replace(background_task_count=3),)
                                         + window_one.agent_sessions[1:])
        self.list_controller.apply_snapshot(snapshot._replace(monitors=(snapshot.monitors[0]._replace(windows=(window_one, window_two)),)))
        self.window.contentView().layoutSubtreeIfNeeded()
        row_view = self.lay_out_row_view(1)
        self.assertEqual(row_view.details_text_field.stringValue(), "•••")
        self.assertGreater(row_view.details_text_field.frame().origin.x, row_view.name_text_field.frame().origin.x)
        self.assertEqual(self.lay_out_row_view(2).name_text_field.frame().origin.x, row_view.name_text_field.frame().origin.x)

    def test_an_exited_row_is_gray_and_its_menu_has_nothing_to_send_or_kill(self):
        snapshot = make_sample_snapshot()
        window_one, window_two = snapshot.monitors[0].windows
        exited_entry = window_one.agent_sessions[0]._replace(status="exited", slash_commands=(), ended_abnormally_at=1.0)
        window_one = window_one._replace(agent_sessions=(exited_entry,) + window_one.agent_sessions[1:])
        self.list_controller.apply_snapshot(snapshot._replace(monitors=(snapshot.monitors[0]._replace(windows=(window_one, window_two)),)))
        self.window.contentView().layoutSubtreeIfNeeded()
        row_view = self.lay_out_row_view(1)
        self.assertEqual((row_view.dot_text_field.stringValue(), row_view.column_views["state"].stringValue()), ("⊘ ", "exited"))
        tab_item = self.list_controller.outline_view.itemAtRow_(1)
        self.list_controller.find_clicked_item = lambda: tab_item
        menu = NSMenu.alloc().initWithTitle_("")
        self.list_controller.menuNeedsUpdate_(menu)
        disabled_titles = [menu_item.title() for menu_item in menu.itemArray() if not menu_item.isEnabled() and menu_item.title()]
        self.assertTrue({"/compact", "/clear", "Kill Session", "Duplicate this tab"} <= set(disabled_titles))
        self.assertEqual(menu.itemWithTitle_("Kill Session").toolTip(), "The agent has ended")
        self.assertEqual(menu.itemWithTitle_("Duplicate this tab").toolTip(), "The agent has ended")
        self.assertTrue(menu.itemWithTitle_("Rename").isEnabled())

    def test_the_bottom_bar_notes_missing_hooks_for_the_agent_tools_listed(self):
        panel = self.dropdown_panel_controller
        snapshot = make_sample_snapshot()  # Claude Code and Codex sessions
        for hooks_status_per_agent_tool, expected_note, shows_button in (
                ((("Claude Code", "missing"), ("Codex", "missing")), "No hooks for Claude Code and Codex", True),
                ((("Claude Code", "installed"), ("Codex", "needs approval")), "Approve the Codex hooks: /hooks in Codex", False),
                ((("Claude Code", "installed"), ("Codex", "installed")), "", False)):
            self.list_controller.apply_snapshot(snapshot._replace(hooks_status_per_agent_tool=hooks_status_per_agent_tool))
            self.assertEqual((panel.hooks_note_text_field.stringValue(), panel.hooks_note_text_field.isHidden(),
                              panel.install_hooks_button.isHidden()), (expected_note, not expected_note, not shows_button))
        self.list_controller.apply_snapshot(snapshot._replace(hooks_status_per_agent_tool=(("Claude Code", "missing"),)))
        note_frame, button_frame = panel.hooks_note_text_field.frame(), panel.install_hooks_button.frame()
        self.assertEqual(note_frame.origin.x, PANEL_SIDE_MARGIN)  # at the bottom left, then the button
        self.assertLess(note_frame.origin.x + note_frame.size.width, button_frame.origin.x)
        self.assertLessEqual(button_frame.origin.x + button_frame.size.width, panel.pause_or_resume_all_button.frame().origin.x)
        self.assertLess(button_frame.origin.y + button_frame.size.height, BOTTOM_BAR_HEIGHT)
        panel.installHooksClicked_(panel.install_hooks_button)
        self.assertEqual(self.owner.hooks_install_requests, [["Claude Code"]])
        only_claude_code = snapshot._replace(monitors=(snapshot.monitors[0]._replace(windows=(snapshot.monitors[0].windows[1],)),),
                                             hooks_status_per_agent_tool=(("Codex", "missing"),))
        self.list_controller.apply_snapshot(only_claude_code)  # no Codex session listed: nothing to say about its hooks
        self.assertTrue(panel.hooks_note_text_field.isHidden())


class AutomaticIconColorTests(unittest.TestCase):
    def snapshot_with_states(self, states):
        window = WindowEntry("w1", "work", False, (0, 0, 800, 600), len(states), tuple(
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
            self.assertEqual(choose_icon_color_name(self.snapshot_with_states(states)), expected_color_name)


class SidebarWidthDragTests(unittest.TestCase):
    def setUp(self):
        self.owner = RecordingOwner()
        self.owner.saved_widths = []
        self.owner.sidebar_width_changed = self.owner.saved_widths.append
        self.controller = SidebarController.alloc().initWithOwner_(self.owner)
        self.controller.list_controller.refresh_timer.invalidate()
        # Set up by hand rather than through apply_settings, so no window appears on screen.
        self.controller.settings = dict(DEFAULT_SETTINGS, show_as="sidebar", sidebar_mode="floating")
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

    def test_the_shortcut_slides_it_out_at_once_even_while_it_was_to_stay_a_few_seconds(self):
        self.controller.keep_shown_until = time.time() + 4
        self.controller.check_whether_to_hide_in(4)
        self.controller.hide_now()
        self.assertFalse(self.controller.is_shown)
        self.assertIsNone(self.controller.hide_timer)
        self.assertEqual(self.controller.keep_shown_until, 0)

    def test_a_sidebar_that_hides_by_itself_watches_for_clicks_in_other_apps_only_while_it_is_in(self):
        # A screen far outside every monitor, so the sidebar shown here stays out of sight.
        self.controller.screens = [{"name": "Far away", "frame": (-4000, -4000, 1500, 1000),
                                    "visible_frame": (-4000, -4000, 1500, 970)}]
        self.addCleanup(self.controller.watch_for_clicks_in_other_apps, False)
        self.addCleanup(self.controller.window.orderOut_, None)
        for settings_change, hides_by_itself in (({"show_as": "sidebar", "sidebar_mode": "auto_hide"}, True),
                                                 ({"show_as": "popup", "sidebar_mode": "floating"}, True),  # "Show the sidebar"
                                                 ({"show_as": "sidebar", "sidebar_mode": "floating"}, False),
                                                 ({"show_as": "sidebar", "sidebar_mode": "docked"}, False)):
            self.controller.settings = dict(self.controller.settings, **settings_change)
            self.controller.set_shown(True, animate=False)
            self.assertEqual(self.controller.click_monitor is not None, hides_by_itself, settings_change)
            self.controller.hide_now(animate=False)
            self.assertIsNone(self.controller.click_monitor)
            self.assertFalse(self.controller.window.isVisible())  # gone at once, without sliding

    def test_its_top_bar_dragged_turns_it_into_the_list_window_at_the_windows_last_size(self):
        # With Show as: Sidebar, settings_changed shows the window. With Show as: Window already
        # ("Show the sidebar" while the window was closed), top_bar_dragged has to.
        self.controller.screens = [{"name": "Far away", "frame": (-4000, -4000, 1500, 1000),
                                    "visible_frame": (-4000, -4000, 1500, 970)}]
        self.addCleanup(self.controller.watch_for_clicks_in_other_apps, False)
        list_window_controller = ListWindowController.alloc().initWithOwner_(self.owner)
        list_window_controller.list_controller.refresh_timer.invalidate()
        # Recorded rather than shown: macOS would pull the titled window from the far-away monitor onto a real one.
        shown_frames = []
        list_window_controller.show = lambda saved_frame, takes_keyboard_focus=True: shown_frames.append(saved_frame)
        followed_pointer_offsets = []
        list_window_controller.follow_the_pointer_until_the_button_comes_up = followed_pointer_offsets.append

        class StandInAppController:
            sidebar_controller = self.controller
            dropdown_panel_controller = None
            popover = unittest.mock.Mock(isShown=lambda: False)
            # Open, so the test app isn't made active, and kept in front of the window shown.
            settings_window_controller = unittest.mock.Mock(is_open=lambda: True)

            def settings_changed(self, new_settings):
                self.settings = new_settings  # shows no window, unlike the real one when "Show as" changes

            def place_the_settings_window_above_the_lists(self, dropdown_is_open):
                self.settings_window_placements += 1
        stand_in_app_controller = StandInAppController()
        stand_in_app_controller.list_window_controller = list_window_controller
        # Never shown, the window takes the size it was built with (480 by 560 inside the frame); then its saved size.
        saved_frame_rect = list_window_controller.window.frameRectForContentRect_(NSMakeRect(0, 0, 300, 400))
        saved_frame = (saved_frame_rect.origin.x, saved_frame_rect.origin.y, saved_frame_rect.size.width,
                       saved_frame_rect.size.height)
        for show_as_before, show_call_count, list_window_frame, (content_width, content_height) in (
                ("sidebar", 0, None, (480, 560)), ("window", 1, saved_frame, (300, 400))):
            stand_in_app_controller.settings = self.controller.settings = dict(
                self.controller.settings, show_as=show_as_before, list_window_frame=list_window_frame)
            stand_in_app_controller.settings_window_placements = 0
            self.controller.set_shown(True, animate=False)  # 400 wide at x -2900, its top at y -3030
            shown_frames.clear()
            AppController.top_bar_dragged(stand_in_app_controller, self.controller.list_panel_controller, (-2700, -3040))
            self.assertFalse(self.controller.is_shown or self.controller.window.isVisible(), show_as_before)
            window_frame = stand_in_app_controller.settings["list_window_frame"]
            self.assertEqual(shown_frames, [window_frame] * show_call_count, show_as_before)
            self.assertEqual(stand_in_app_controller.settings_window_placements, show_call_count, show_as_before)
            content_rect = list_window_controller.window.contentRectForFrameRect_(NSMakeRect(*window_frame))
            # The pointer halfway across it and 10 below its top.
            self.assertEqual((content_rect.origin.x, content_rect.origin.y + content_rect.size.height,
                              content_rect.size.width, content_rect.size.height),
                             (-2700 - content_width / 2, -3030, content_width, content_height), show_as_before)
        self.assertEqual(len(followed_pointer_offsets), 2)

    def test_shown_from_the_menu_it_stays_for_the_whole_five_seconds(self):
        self.controller.keep_shown_until = time.time() + 4
        self.controller.hideTimerFired_(None)
        self.assertTrue(self.controller.is_shown)
        self.assertIsNotNone(self.controller.hide_timer)  # checks again when the 5 seconds are up
        self.controller.cancel_hide_timer()

    def test_a_sidebar_not_chosen_in_show_as_is_parked_just_past_its_edge(self):
        # So "Show the sidebar" slides it in from that edge. (The shown case isn't tested here:
        # it would put a real window on the screen.)
        self.owner.reserve_screen_area = lambda area: None
        self.controller.settings = dict(self.controller.settings, show_as="popup")
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
        # A new name typed, so a rename submitted instead of cancelled would show.
        list_controller.outline_view.viewAtColumn_row_makeIfNecessary_(0, 1, False).name_text_field.currentEditor() \
            .setString_("typed but cancelled")
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
        self.shortcut_lacks_permission = False

    def settings_changed(self, new_settings):
        self.settings = new_settings
        self.changed_settings.append(new_settings)

    def global_shortcut_lacks_permission(self):
        return self.shortcut_lacks_permission

    def window_frame_changed(self, setting_name, frame):
        pass

    def settings_window_closed(self):
        pass


class ListWindowSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        NSApplication.sharedApplication().setActivationPolicy_(NSApplicationActivationPolicyAccessory)

    def test_a_saved_frame_is_used_only_while_it_is_on_a_screen(self):
        screens = [(0, 0, 1500, 1000), (1500, 0, 1920, 1080)]
        self.assertTrue(frame_is_on_a_screen((1600, 100, 500, 400), screens))
        self.assertFalse(frame_is_on_a_screen((4000, 100, 500, 400), screens))  # its monitor is gone

    def test_a_dropdown_too_short_for_the_list_window_grows_downward_when_detached(self):
        self.assertEqual(grow_frame_to_smallest_size([100, 800, 327, 148], (250, 200)), [100, 748, 327, 200])
        self.assertEqual(grow_frame_to_smallest_size([100, 800, 400, 300], (250, 200)), [100, 800, 400, 300])

    def test_a_sidebar_dragged_by_its_top_bar_turns_into_the_window_with_the_pointer_still_on_the_top_bar(self):
        # The pointer 3/4 across a 400-wide sidebar and 10 below its top: 3/4 across a 200-wide
        # window, 10 below its top.
        self.assertEqual(compute_list_window_content_rect_for_a_dragged_sidebar([1100, 0, 400, 970], (1400, 960), (200, 300)),
                         [1250, 670, 200, 300])

    def test_the_window_holds_its_own_list_and_reports_its_frame(self):
        owner = RecordingOwner()
        owner.reported_frames = []
        owner.window_frame_changed = lambda setting_name, frame: owner.reported_frames.append((setting_name, frame))
        controller = ListWindowController.alloc().initWithOwner_(owner)
        controller.list_controller.refresh_timer.invalidate()
        self.assertTrue(controller.list_controller.width_is_set_by_the_user)  # narrow, it drops columns like the sidebar
        self.assertIs(controller.window.contentView(), controller.list_panel_controller.view())
        self.assertFalse(controller.window.isReleasedWhenClosed())  # closing only hides it
        controller.window.setFrame_display_(NSMakeRect(-4000, -4000, 500, 400), False)  # off every screen: not shown
        controller.report_frame()
        self.assertEqual(owner.reported_frames[-1], ("list_window_frame", [-4000, -4000, 500, 400]))

    def test_the_window_is_in_front_for_the_shortcut_only_while_shown_with_the_keyboard_or_always_on_top(self):
        controller = ListWindowController.alloc().initWithOwner_(RecordingOwner())
        controller.list_controller.refresh_timer.invalidate()
        self.assertFalse(controller.is_in_front())  # not shown
        # A stand-in window: macOS would move a real titled window onto the user's screen to show it.
        for is_visible, is_on_active_space, is_key_window, level, is_in_front in (
                (True, True, False, NSNormalWindowLevel, False),
                (True, True, True, NSNormalWindowLevel, True),
                (True, True, False, NSFloatingWindowLevel, True),
                (True, False, False, NSFloatingWindowLevel, False),  # always on top, but on another desktop
                (False, True, True, NSFloatingWindowLevel, False)):
            controller.window = unittest.mock.Mock(isVisible=lambda: is_visible, isOnActiveSpace=lambda: is_on_active_space,
                                                   isKeyWindow=lambda: is_key_window, level=lambda: level)
            self.assertEqual(controller.is_in_front(), is_in_front, (is_visible, is_on_active_space, is_key_window, level))


class SettingsWindowSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        NSApplication.sharedApplication().setActivationPolicy_(NSApplicationActivationPolicyAccessory)

    def setUp(self):
        # One monitor whatever this Mac has connected, since the edge choices and the placement note follow the
        # monitors; tests of other arrangements patch it again.
        monitors_patch = unittest.mock.patch(
            "agent_command_tab.user_interface.settings_window.read_monitors_from_screens",
            return_value=[{"name": "Built-in", "frame": (0, 0, 1500, 1000), "visible_frame": (0, 0, 1500, 970)}])
        monitors_patch.start()
        self.addCleanup(monitors_patch.stop)
        self.owner = SettingsRecordingOwner(dict(DEFAULT_SETTINGS))
        self.controller = SettingsWindowController.alloc().initWithOwner_(self.owner)
        self.controller.settings = dict(self.owner.settings)
        self.controller.window = self.controller.build_window()  # built without showing it

    def test_every_control_fits_in_the_narrowest_window(self):
        narrowest_width = SMALLEST_WINDOW_SIZES["settings_window_frame"][0]
        self.assertEqual(self.controller.window.minSize().width, narrowest_width)
        for subview in self.controller.rows_view.subviews():
            right_edge = subview.frame().origin.x + subview.frame().size.width
            self.assertLessEqual(right_edge, narrowest_width, subview)
            for inner_view in subview.subviews():  # the radio buttons and the command rows' fields
                self.assertLessEqual(subview.frame().origin.x + inner_view.frame().origin.x + inner_view.frame().size.width,
                                     narrowest_width, inner_view)

    def test_changing_one_control_keeps_changes_made_elsewhere(self):
        self.owner.settings = dict(self.owner.settings, sidebar_width=555)  # dragged at the sidebar's edge meanwhile
        self.controller.list_window_always_on_top_checkbox.setState_(1)
        self.controller.controlChanged_(self.controller.list_window_always_on_top_checkbox)
        self.assertTrue(self.owner.settings["list_window_is_always_on_top"])
        self.assertEqual(self.owner.settings["sidebar_width"], 555)

    def test_both_edges_stay_choosable_when_the_menu_bar_monitor_stands_between_two_others(self):
        three_monitors_in_a_row = [
            {"name": "Built-in", "frame": (0, 0, 1500, 1000), "visible_frame": (0, 0, 1500, 970)},
            {"name": "Dell", "frame": (-1920, 0, 1920, 1080), "visible_frame": (-1920, 0, 1920, 1080)},
            {"name": "Studio", "frame": (1500, -200, 2500, 1400), "visible_frame": (1500, -200, 2500, 1400)}]
        with unittest.mock.patch("agent_command_tab.user_interface.settings_window.read_monitors_from_screens",
                                 return_value=three_monitors_in_a_row):
            self.controller.update_edge_choices()
        self.assertEqual([menu_item.isEnabled() for menu_item in self.controller.edge_popup.itemArray()], [True, True])

    def test_window_edge_popup_shows_capitalized_titles_and_saves_the_edge_name(self):
        self.assertEqual(self.controller.edge_popup.itemTitles(), ["Right", "Left"])
        self.assertEqual(self.controller.edge_popup.titleOfSelectedItem(), "Right")  # the default edge
        with unittest.mock.patch("agent_command_tab.user_interface.settings_window.read_monitors_from_screens",
                                 return_value=[{"name": "Built-in", "frame": (0, 0, 1500, 1000),
                                                "visible_frame": (0, 0, 1500, 970)}]):
            self.controller.edge_popup.selectItemWithTitle_("Left")
            self.controller.controlChanged_(self.controller.edge_popup)
        self.assertEqual(self.owner.settings["sidebar_edge"], "left")

    def test_the_window_closes_once_no_part_of_it_shows(self):
        for occlusion_state, is_visible, closes in ((0, True, True),  # covered, on another desktop, or the screen locked
                                                    (NSWindowOcclusionStateVisible, True, False),  # some of it shows
                                                    (0, False, False)):  # already closed, or not yet shown
            window = unittest.mock.Mock()
            window.isVisible.return_value = is_visible
            window.occlusionState.return_value = occlusion_state
            self.controller.window = window
            self.controller.windowDidChangeOcclusionState_(unittest.mock.Mock(object=lambda: window))
            self.assertEqual(window.close.called, closes)

    def test_the_settings_window_opens_clear_of_a_shown_sidebar_and_stays_at_the_normal_level(self):
        # A stand-in app controller, as in the top bar drag test: only the windows this looks at.
        # The sidebar is 400 wide at the right edge of a 1500-point monitor; the settings window
        # (540 wide) reaches into it.
        for sidebar_is_shown, expected_frames in ((True, [(560, 200, 540, 600)]), (False, [])):
            levels, frames = [], []
            stand_in_app_controller = unittest.mock.Mock(settings={"list_window_is_always_on_top": False})
            stand_in_app_controller.list_window_controller.window.isVisible.return_value = False
            sidebar_controller = stand_in_app_controller.sidebar_controller
            sidebar_controller.window.isVisible.return_value = sidebar_is_shown
            sidebar_controller.window.frame.return_value = NSMakeRect(1100, 0, 400, 970)
            sidebar_controller.placement = SidebarPlacement(0, "right", True)
            sidebar_controller.screens = [{"name": "Built-in", "frame": (0, 0, 1500, 1000), "visible_frame": (0, 0, 1500, 970)}]
            settings_window = stand_in_app_controller.settings_window_controller.window
            settings_window.frame.return_value = NSMakeRect(900, 200, 540, 600)
            settings_window.setFrame_display_.side_effect = lambda rect, display: frames.append(
                (rect.origin.x, rect.origin.y, rect.size.width, rect.size.height))
            stand_in_app_controller.settings_window_controller.set_window_level = levels.append
            AppController.move_the_settings_window_clear_of_the_sidebar(stand_in_app_controller)
            AppController.place_the_settings_window_above_the_lists(stand_in_app_controller, False)
            self.assertEqual(frames, expected_frames)
            self.assertEqual(levels, [None])  # the normal level, so other apps' windows can cover it and close it

    def test_a_number_being_typed_is_saved_when_the_window_closes(self):
        window = self.controller.window  # built, not shown: no close button to click, so its close notification is sent
        window.makeFirstResponder_(self.controller.sidebar_width_field)
        self.controller.sidebar_width_field.currentEditor().setString_("555")
        self.controller.windowWillClose_(unittest.mock.Mock(object=lambda: window))
        self.assertEqual(self.owner.settings["sidebar_width"], 555)

    def test_column_checkboxes_choose_the_shown_columns_in_their_left_to_right_order(self):
        checkboxes = self.controller.column_checkboxes
        self.assertEqual([title for title in (checkbox.title() for checkbox in checkboxes.values())],
                         ["Status", "Time Since Last Status Change", "Tokens Used", "Created Time", "Time Since Created",
                          "Memory Usage", "CPU Usage"])
        self.assertEqual([checkbox.state() for checkbox in checkboxes.values()], [1, 1, 1, 0, 0, 0, 0])  # the defaults
        checkboxes["state"].setState_(0)
        self.controller.controlChanged_(checkboxes["state"])
        checkboxes["created_time"].setState_(1)
        self.controller.controlChanged_(checkboxes["created_time"])
        self.assertEqual(self.owner.settings["shown_columns"], ["context_tokens", "created_time", "state_duration"])

    def test_text_fields_save_when_they_lose_focus_too(self):
        self.assertTrue(self.controller.sidebar_width_field.cell().sendsActionOnEndEditing())

    def test_invalid_number_puts_the_current_value_back(self):
        self.controller.sidebar_width_field.setStringValue_("5")
        self.controller.controlChanged_(self.controller.sidebar_width_field)
        self.assertEqual(self.owner.settings["sidebar_width"], DEFAULT_SETTINGS["sidebar_width"])
        self.assertEqual(self.controller.sidebar_width_field.stringValue(), str(DEFAULT_SETTINGS["sidebar_width"]))

    def test_changes_made_elsewhere_show_in_the_open_window_and_are_kept(self):
        self.owner.settings = dict(self.owner.settings, show_as="sidebar", sidebar_width=650)
        self.controller.reflect_settings(self.owner.settings)
        self.assertEqual(self.controller.show_as_popup.titleOfSelectedItem(), "Sidebar")
        self.assertEqual(self.controller.sidebar_width_field.stringValue(), "650")
        self.controller.list_window_always_on_top_checkbox.setState_(1)
        self.controller.controlChanged_(self.controller.list_window_always_on_top_checkbox)
        self.assertEqual((self.owner.settings["show_as"], self.owner.settings["sidebar_width"]), ("sidebar", 650))
        self.assertTrue(self.owner.settings["list_window_is_always_on_top"])

    def test_a_width_being_typed_survives_a_click_on_another_control(self):
        width_field = self.controller.sidebar_width_field
        self.assertTrue(self.controller.window.makeFirstResponder_(width_field))
        width_field.currentEditor().setString_("500")  # typed, without Return or Tab
        self.controller.list_window_always_on_top_checkbox.setState_(1)
        self.controller.controlChanged_(self.controller.list_window_always_on_top_checkbox)
        self.controller.reflect_settings(self.owner.settings)  # as the app controller does after every change
        self.assertEqual(width_field.currentEditor().string(), "500")
        self.controller.window.makeFirstResponder_(None)  # editing ends, as a click elsewhere does
        self.assertEqual(self.owner.settings["sidebar_width"], 500)

    def test_a_width_field_that_only_has_the_focus_follows_a_width_dragged_at_the_sidebar(self):
        width_field = self.controller.sidebar_width_field
        self.assertTrue(self.controller.window.makeFirstResponder_(width_field))  # as the first text field when the window opens
        self.owner.settings = dict(self.owner.settings, sidebar_width=555)  # dragged at the sidebar's edge
        self.controller.reflect_settings(self.owner.settings)
        self.assertEqual(width_field.currentEditor().string(), "555")
        self.controller.window.makeFirstResponder_(None)  # the window closes
        self.assertEqual(self.owner.settings["sidebar_width"], 555)  # not the 400 it showed before the drag

    def test_quit_saves_a_width_being_typed_first(self):
        width_field = self.controller.sidebar_width_field
        self.assertTrue(self.controller.window.makeFirstResponder_(width_field))
        width_field.currentEditor().setString_("500")  # typed, without Return or Tab
        steps = []
        stand_in_app_controller = unittest.mock.Mock(settings_window_controller=self.controller,
                                                     save_settings_if_scheduled=lambda: steps.append("save a moved window's frame"),
                                                     give_room_back_before_exiting=lambda: steps.append("give room back"))
        # The real one, which Ctrl+C and logout (applicationWillTerminate_) also go through.
        stand_in_app_controller.save_settings_before_exiting = lambda: AppController.save_settings_before_exiting(stand_in_app_controller)
        with unittest.mock.patch("os._exit", lambda status: steps.append(f"exit {status}")):
            AppController.quit(stand_in_app_controller)
        self.assertEqual(self.owner.settings["sidebar_width"], 500)
        self.assertEqual(steps, ["save a moved window's frame", "give room back", "exit 0"])

    def test_the_show_as_popup_offers_the_three_ways_and_saves_the_choice(self):
        self.assertEqual(self.controller.show_as_popup.itemTitles(), ["Window", "Sidebar", "Popup"])
        self.assertEqual(self.controller.show_as_popup.titleOfSelectedItem(), "Popup")  # the default
        self.controller.show_as_popup.selectItemWithTitle_("Window")
        self.controller.controlChanged_(self.controller.show_as_popup)
        self.assertEqual(self.owner.settings["show_as"], "window")

    def test_always_on_top_keeps_the_list_window_above_other_windows(self):
        checkbox = self.controller.list_window_always_on_top_checkbox
        self.assertEqual((checkbox.title(), checkbox.state()), ("Always on top", 0))
        checkbox.setState_(1)
        self.controller.controlChanged_(checkbox)
        self.assertTrue(self.owner.settings["list_window_is_always_on_top"])
        list_window_controller = ListWindowController.alloc().initWithOwner_(RecordingOwner())
        list_window_controller.list_controller.refresh_timer.invalidate()
        list_window_controller.apply_settings(self.owner.settings)
        self.assertEqual(list_window_controller.window.level(), NSFloatingWindowLevel)
        list_window_controller.apply_settings(dict(self.owner.settings, list_window_is_always_on_top=False))
        self.assertEqual(list_window_controller.window.level(), NSNormalWindowLevel)

    def test_settings_are_grouped_under_section_titles_in_a_resizable_scrolling_window(self):
        rows_view = self.controller.rows_view  # flipped: y grows downward
        texts_top_to_bottom = [subview.stringValue() for subview in sorted(
            (subview for subview in rows_view.subviews() if isinstance(subview, NSTextField)),
            key=lambda subview: subview.frame().origin.y)]
        self.assertEqual(texts_top_to_bottom[0], "General")
        for earlier, later in (("General", "Show as:"), ("Show as:", "Keyboard shortcut:"),
                               ("Keyboard shortcut:", "Window"), ("Window", "Sidebar"), ("Sidebar", "Show:"), ("Width:", "Display Columns"),
                               ("Display Columns", "Harnesses"), ("Harnesses", "Use:")):
            self.assertLess(texts_top_to_bottom.index(earlier), texts_top_to_bottom.index(later), (earlier, later))
        self.assertTrue(self.controller.window.styleMask() & NSWindowStyleMaskResizable)
        self.assertIs(self.controller.window.contentView().documentView(), rows_view)
        width_before = rows_view.frame().size.width
        self.controller.window.setContentSize_((self.controller.window.contentView().frame().size.width + 160, 300))
        self.assertEqual(rows_view.frame().size.width, width_before + 160)  # the rows follow the window's width

    def test_the_command_fields_start_together_keep_their_width_and_scroll_a_longer_command(self):
        command_fields = self.controller.launch_command_fields
        self.controller.window.setContentSize_((self.controller.window.contentView().frame().size.width + 160, 300))
        self.assertEqual({(command_field.frame().origin.x, command_field.frame().size.width) for command_field in command_fields},
                         {(command_fields[0].frame().origin.x, LAUNCH_COMMAND_FIELD_WIDTH)})
        self.assertTrue(all(command_field.cell().isScrollable() for command_field in command_fields))

    def test_every_section_starts_one_blank_row_below_the_last_thing_shown_above_it(self):
        subviews = list(self.controller.rows_view.subviews())  # flipped: y grows downward
        section_titles = [subview for subview in subviews if isinstance(subview, NSTextField)
                          and subview.stringValue() in ("Window", "Sidebar", "Display Columns", "Harnesses")]
        self.assertEqual(len(section_titles), 4)
        for section_title in section_titles:
            # The hidden accessibility button and the empty placement note show nothing.
            shown_above = [subview.frame().origin.y for subview in subviews if subview.frame().origin.y < section_title.frame().origin.y
                           and not subview.isHidden() and not (isinstance(subview, NSTextField) and not subview.stringValue())]
            # Two rows: the last thing's own row, then the blank one (a label's text sits 1 point lower than a control).
            self.assertAlmostEqual(section_title.frame().origin.y - max(shown_above), 2 * ROW_SPACING, delta=1,
                                   msg=section_title.stringValue())

    def test_the_settings_window_reports_its_frame_and_uses_a_saved_one(self):
        reported_frames = []
        self.owner.window_frame_changed = lambda setting_name, frame: reported_frames.append((setting_name, frame))
        self.controller.window.setFrame_display_(NSMakeRect(-4000, -4000, 500, 400), False)
        self.controller.report_frame()
        self.assertEqual(reported_frames[-1], ("settings_window_frame", [-4000, -4000, 500, 400]))
        self.assertFalse(self.controller.is_open())  # built but never shown

    def test_harnesses_can_be_left_out_and_keep_their_commands(self):
        self.assertEqual([checkbox.title() for checkbox in self.controller.include_checkboxes], ["Claude Code", "Codex"])
        command_field = self.controller.launch_command_fields[1]
        self.assertEqual(command_field.superview().subviews()[0].stringValue(), "Codex command:")  # indented under its checkbox
        self.controller.include_checkboxes[1].setState_(0)
        self.controller.controlChanged_(self.controller.include_checkboxes[1])
        self.assertEqual([agent_tool["is_included"] for agent_tool in self.owner.settings["agent_tools"]], [True, False])
        self.assertEqual(self.owner.settings["agent_tools"][1]["launch_command"], "codex")

    def test_the_shortcut_popup_offers_both_combinations_and_off_and_saves_the_choice(self):
        self.assertEqual(self.controller.global_shortcut_popup.itemTitles(), ["Shift-Command-Tab", "Command-Fn-Tab", "Off"])
        self.assertEqual(self.controller.global_shortcut_popup.titleOfSelectedItem(), "Shift-Command-Tab")  # the default
        self.controller.global_shortcut_popup.selectItemWithTitle_("Command-Fn-Tab")
        self.controller.controlChanged_(self.controller.global_shortcut_popup)
        self.assertEqual(self.owner.settings["global_shortcut"], "command_fn_tab")

    def test_the_accessibility_button_shows_under_the_shortcut_only_while_it_lacks_the_permission(self):
        button = self.controller.accessibility_settings_button
        self.assertTrue(button.isHidden())
        self.assertEqual(button.frame().origin.y - self.controller.global_shortcut_popup.frame().origin.y, ROW_SPACING)
        self.owner.shortcut_lacks_permission = True
        self.controller.reflect_settings(self.owner.settings)
        self.assertFalse(button.isHidden())
        self.owner.shortcut_lacks_permission = False
        self.controller.reflect_settings(self.owner.settings)
        self.assertTrue(button.isHidden())

    def test_reflecting_settings_before_the_window_was_opened_does_nothing(self):
        controller = SettingsWindowController.alloc().initWithOwner_(self.owner)
        controller.reflect_settings(dict(self.owner.settings, sidebar_width=700))
        self.assertIsNone(controller.window)


if __name__ == "__main__":
    unittest.main()
