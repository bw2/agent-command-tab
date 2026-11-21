"""The list (built from a sample snapshot), its dropdown panel, the sidebar, the list window, the
settings and Saved sessions windows and the menu bar icon, and the AppController code behind them
(run on stand-in controllers). Most windows are placed far outside every monitor; the titled ones
that tests show, which macOS would pull onto a real screen, are shown there transparent.

Set AGENT_COMMAND_TAB_SMOKE_TEST_IMAGE=/path/to/file.png to also save what the list looks like.
"""

import concurrent.futures
import copy
import io
import json
import math
import os
import tempfile
import threading
import time
import unittest
import unittest.mock
import warnings

import objc
from AppKit import (NSAlertFirstButtonReturn, NSAlertSecondButtonReturn, NSApp, NSAppearance, NSAppearanceNameAqua,
                    NSAppearanceNameDarkAqua, NSApplication,
                    NSApplicationActivationPolicyAccessory, NSBackgroundStyleNormal, NSBackingStoreBuffered,
                    NSBitmapImageFileTypePNG, NSBox, NSBoxCustom, NSButton, NSColor, NSColorSpace, NSControlStateValueOff,
                    NSControlStateValueOn, NSControlTextDidChangeNotification, NSCursor, NSDragOperationMove, NSDragOperationNone, NSEvent,
                    NSDownArrowFunctionKey, NSEventModifierFlagCommand, NSEventModifierFlagControl, NSEventModifierFlagShift,
                    NSEventTypeKeyDown, NSSwitch, NSUpArrowFunctionKey,
                    NSEventTypeLeftMouseDown, NSEventTypeLeftMouseDragged,
                    NSEventTypeLeftMouseUp, NSLeftArrowFunctionKey, NSMakePoint, NSMakeRect, NSMidX, NSMidY, NSPointInRect,
                    NSRightArrowFunctionKey,
                    NSFloatingWindowLevel, NSFontAttributeName, NSForegroundColorAttributeName, NSImage,
                    NSImageScaleProportionallyUpOrDown, NSImageView, NSMenu,
                    NSNormalWindowLevel, NSPopover, NSPopoverBehaviorApplicationDefined, NSPopoverBehaviorTransient,
                    NSParagraphStyleAttributeName, NSScreen, NSTextAlignmentRight, NSTextField, NSView, NSWindow, NSWindowOcclusionStateVisible,
                    NSWindowStyleMaskBorderless, NSWindowStyleMaskResizable, NSWindowStyleMaskTitled, NSWorkspaceApplicationKey)
from Foundation import NSContainsRect, NSIndexSet, NSMaxY, NSMinY, NSNotification

from agent_command_tab.app_controller import SECONDS_BEFORE_THE_INSTALLED_AGENT_TOOLS_ARE_CHECKED_AGAIN, AppController, yield_activation_to
from agent_command_tab.command_line import RECONNECT_EXIT_STATUS
from agent_command_tab.dashboard_core_api import ActionRequest, ActionResult
from agent_command_tab.list_sorting import LIST_SORT_ORDERS
from agent_command_tab.settings_store import DEFAULT_SETTINGS, LIST_COLUMNS, SMALLEST_WINDOW_SIZES
from agent_command_tab.snapshot_data_classes import (ChildProcessEntry, ComputerSnapshot, MonitorEntry, WindowEntry,
                                                     build_window_title_shown_in_the_list)
from agent_command_tab.user_interface.popover_container import (BOTTOM_BAR_HEIGHT, CASE_SENSITIVE_CHECKBOX_TOOL_TIP,
                                                                GAP_AFTER_THE_SORT_CONTROL_AND_THE_SETTINGS_BUTTON,
                                                                GAP_BEFORE_THE_HOOKS_NOTE, GAP_BETWEEN_TOP_BAR_BUTTONS,
                                                                HOOKS_SEND_NO_EVENTS_TOOL_TIP, LARGEST_SEARCH_FIELD_WIDTH,
                                                                LIST_SORT_ORDER_TITLES,
                                                                PANEL_SIDE_MARGIN, POPOVER_WIDTH, ROW_OUTLINES_LEGEND_SQUARE_SIZE,
                                                                SMALLEST_MESSAGE_WIDTH_BESIDE_THE_CONTROLS,
                                                                SMALLEST_POPOVER_WIDTH, SMALLEST_SEARCH_FIELD_WIDTH,
                                                                SMALLEST_SORT_POPUP_WIDTH,
                                                                TOP_BAR_HEIGHT, ListPanelController)
from agent_command_tab.messages_between_agent_sessions import DEFAULT_NOTE_ASKING_AGENT_SESSIONS_TO_TALK_TO_EACH_OTHER
from agent_command_tab.saved_agent_sessions_store import rename_saved_session
from agent_command_tab.user_interface.agent_session_outline_view import (COLUMN_TITLE_LINE_HEIGHT, COLUMN_TITLES,
                                                                         COLUMN_TITLES_ROW_PADDING,
                                                                         CONVERSATION_ONLY_MATCH_ROW_ALPHA, DIM_DOT_ALPHA,
                                                                         FRONT_TAB_OUTLINE_COLOR_MAKER,
                                                                         GAP_BEFORE_DETAILS, ROW_HEIGHT, ROW_RIGHT_MARGIN,
                                                                         SECONDS_A_LIST_MAY_SELECT_A_NEW_WINDOW,
                                                                         SECONDS_TO_WAIT_FOR_A_NEW_WINDOW_TO_SHOW,
                                                                         SELECTION_OUTLINE_COLOR_MAKER, SMALLEST_TAB_NAME_WIDTH,
                                                                         TEXT_FIELD_INSETS,
                                                                         AgentSessionListController, RowButton,
                                                                         RowViewWithSelectionAndFrontTabOutlines,
                                                                         build_column_title_tool_tip, build_index_set,
                                                                         choose_room_for_a_columns_texts,
                                                                         compute_selection_outline_rect, list_moves_that_reorder)
from agent_command_tab.user_interface.agent_tool_icons import build_agent_tool_icons
from agent_command_tab.user_interface.app_icon import build_app_icon
from agent_command_tab.user_interface.control_helpers import add_popup_choices, read_popup_choice
from agent_command_tab.user_interface.named_colors import COLOR_MAKERS_BY_NAME
from agent_command_tab.user_interface.row_text_rendering import STATE_TEXT_COLOR_NAME_BY_STATE
from agent_command_tab.user_interface.talk_to_each_other_alert import build_talk_to_each_other_alert
from agent_command_tab.user_interface.list_window import ListWindowController
from agent_command_tab.user_interface.screen_layout import (compute_list_window_content_rect_for_a_dragged_sidebar,
                                                            convert_rect_to_frame_list, frame_is_on_a_screen,
                                                            grow_frame_to_smallest_size, place_window_at_saved_frame_or_center)
from agent_command_tab.user_interface.pointing_hand_button import PointingHandButton
from agent_command_tab.user_interface.menu_bar_status_item import (MenuBarStatusItemController, choose_icon_color_name,
                                                                   choose_what_a_click_on_the_icon_does_to_the_dropdown)
from agent_command_tab.user_interface.saved_sessions_window import (SavedSessionsSearch, SavedSessionsWindowController,
                                                                    list_saved_windows_and_sessions_in_the_order_shown)
from agent_command_tab.user_interface.settings_window import (GAP_BELOW_SECTION_TITLE, GAP_BETWEEN_SECTIONS,
                                                              SECTION_TITLE_HEIGHT,
                                                              KeyboardButton, KeyboardPopUpButton, SettingsWindowController)
from agent_command_tab.user_interface.settings_window import ROW_HEIGHT as SETTINGS_ROW_HEIGHT  # the list has its own
from agent_command_tab.user_interface.sidebar_panel import (SECONDS_SHOWN_AFTER_A_CLICK_OR_SHORTCUT, SidebarController,
                                                            allow_pointer_changes_while_another_app_is_in_front)
from agent_command_tab.user_interface.sidebar_placement import SidebarPlacement
from codex_sandbox_detection import REASON_FOR_SKIPPING_WINDOW_TESTS_IN_CODEX_SANDBOX, RUNNING_IN_CODEX_SANDBOX
from gathered_window_builders import make_agent_session_entry
from monitor_builders import BUILT_IN_SCREEN, DELL_SCREEN_TO_THE_LEFT, STUDIO_SCREEN_TO_THE_RIGHT
from saved_session_builders import build_saved_session_record, build_saved_window_record
from stand_in_app_controller_builders import make_stand_in_app_controller_with_real_methods


def setUpModule():
    if RUNNING_IN_CODEX_SANDBOX:
        raise unittest.SkipTest(REASON_FOR_SKIPPING_WINDOW_TESTS_IN_CODEX_SANDBOX)
    # An app without a Dock icon, as the dashboard runs, for every test's windows and menus.
    NSApplication.sharedApplication().setActivationPolicy_(NSApplicationActivationPolicyAccessory)


class RecordingOwner:
    """Stands in for the app controller and records what the list asks it to do."""

    def __init__(self):
        self.submitted_actions = []
        self.started_agent_sessions = []
        self.windows_asked_about_closing = []
        self.answer_to_closing_window = False
        self.confirmation_questions = []
        self.confirmation_action_names = []  # what a remembered Yes would be kept for (ACTIONS_THAT_ASK_FOR_CONFIRMATION)
        self.answer_to_confirmation = False
        self.peeks = []
        self.chosen_list_sort_orders = []
        self.idle_tabs_are_hidden_choices = []
        self.talk_to_each_other_requests = []  # (agent session entries, left-out entries)
        self.note_to_send = None  # what the Talk to Each Other window returns: None for Cancel
        self.recipient_titles_to_choose = None  # the tab chosen under "Send the message to:", or None for each of them
        self.hooks_install_requests = []
        self.process_info_requests = []  # (session ID, process ID)
        self.session_info_requests = []  # agent session entries
        self.contact_info_requests = []  # session IDs
        self.lists_closed_after_escape = []
        self.has_saved_sessions = False
        self.saved_sessions_window_opening_count = 0
        self.search_is_case_sensitive_choices = []
        # The list's questions to the core about conversations: [(text, is case sensitive, on_answer)];
        # a test answers one with answer_conversation_search.
        self.conversation_searches = []

    def search_is_case_sensitive_chosen(self, search_is_case_sensitive):
        self.search_is_case_sensitive_choices.append(search_is_case_sensitive)

    def find_agent_sessions_whose_conversations_contain(self, text, is_case_sensitive, on_answer):
        self.conversation_searches.append((text, is_case_sensitive, on_answer))

    def answer_conversation_search(self, session_ids):
        """Answers the oldest question the list asked about conversations, as the core would."""
        _, _, on_answer = self.conversation_searches.pop(0)
        on_answer(session_ids)

    def has_saved_agent_sessions(self):
        return self.has_saved_sessions

    def open_saved_sessions_window(self):
        self.saved_sessions_window_opening_count += 1

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

    def row_drag_began_or_ended(self):
        pass

    def confirm_closing_window(self, window_entry):
        self.windows_asked_about_closing.append(window_entry.window_id)
        return self.answer_to_closing_window

    def ask_for_confirmation(self, explanation, action_name):
        self.confirmation_questions.append(explanation)
        self.confirmation_action_names.append(action_name)
        return self.answer_to_confirmation

    def submit_action_and_report_failure(self, action_request, failure_title, after_success=None, after_failure=None):
        self.submitted_actions.append(action_request)
        self.after_success_of_the_last_action = after_success
        self.after_failure_of_the_last_action = after_failure

    def list_sort_order_chosen(self, list_sort_order, list_sort_order_is_reversed):
        self.chosen_list_sort_orders.append((list_sort_order, list_sort_order_is_reversed))

    def idle_tabs_are_hidden_chosen(self, idle_tabs_are_hidden):
        self.idle_tabs_are_hidden_choices.append(idle_tabs_are_hidden)

    def ask_for_note_asking_agent_sessions_to_talk_to_each_other(self, agent_session_entries, left_out_agent_session_entries):
        self.talk_to_each_other_requests.append((agent_session_entries, left_out_agent_session_entries))
        if self.note_to_send is None:
            return None
        return self.note_to_send, [entry for entry in agent_session_entries
                                   if self.recipient_titles_to_choose is None or entry.tab_title in self.recipient_titles_to_choose]

    def install_hooks(self, agent_tool_names):
        self.hooks_install_requests.append(list(agent_tool_names))

    def show_process_info(self, session_id, process_id):
        self.process_info_requests.append((session_id, process_id))

    def show_agent_session_info(self, agent_session_entry):
        self.session_info_requests.append(agent_session_entry)

    def copy_contact_info(self, session_id):
        self.contact_info_requests.append(session_id)

    def close_the_list_after_escape(self, list_controller):
        self.lists_closed_after_escape.append(list_controller)

    def tell_the_core_whether_the_dashboard_has_the_keyboard(self):
        pass  # the sidebar took the keyboard or gave it back; SidebarControllerTests records these

    def window_frame_changed(self, setting_name, frame):
        pass  # the list window's frame is saved by the app controller, not looked at here


def read_srgb_components(color):
    """Returns the color's red, green, blue and alpha in sRGB, each from 0 to 1."""
    srgb_color = color.colorUsingColorSpace_(NSColorSpace.sRGBColorSpace())
    return (srgb_color.redComponent(), srgb_color.greenComponent(), srgb_color.blueComponent(), srgb_color.alphaComponent())


def convert_color_components_to_0_to_255(components):
    return tuple(round(component * 255) for component in components)


def resolve_color(color, appearance_name):
    """Returns the color's red, green, blue and alpha (0 to 255) as drawn in this appearance."""
    resolved_components = []
    NSAppearance.appearanceNamed_(appearance_name).performAsCurrentDrawingAppearance_(
        lambda: resolved_components.append(read_srgb_components(color)))
    return convert_color_components_to_0_to_255(resolved_components[0])


def render_view_into_bitmap(view):
    """Draws the view, as the screen would, into a new bitmap and returns it."""
    bitmap = view.bitmapImageRepForCachingDisplayInRect_(view.bounds())
    view.cacheDisplayInRect_toBitmapImageRep_(view.bounds(), bitmap)
    return bitmap


def read_drawn_color_components(bitmap, scale, x, y_from_the_top):
    """Returns the red, green, blue and alpha (sRGB, 0 to 1) drawn at this point of the bitmap, in
    points from its top left; scale is its pixels per point."""
    return read_srgb_components(bitmap.colorAtX_y_(int(x * scale), int(y_from_the_top * scale)))


def make_entry(tab_id, title, status, tool="Claude Code", is_in_front=False):
    return make_agent_session_entry(f"s{tab_id}", title, tab_id=tab_id, agent_session_name=title, agent_tool_name=tool,
                                    status=status, status_since=0.0, is_in_front=is_in_front,
                                    slash_commands=("/compact", "/clear"))


class MenuThatRecordsWhereItWouldOpen(NSMenu):
    """A menu that records where it is asked to open, with the row its delegate builds it for,
    and stays closed: a menu that opens leaves AppKit's current event set for the tests after it."""

    def popUpMenuPositioningItem_atLocation_inView_(self, item, location, view):
        self.delegate().menuNeedsUpdate_(self)
        self.openings = getattr(self, "openings", []) + [(tuple(location), self.delegate().menu_target_item)]
        return False


class MenuThatRecordsItsOpenings(NSMenu):
    """A menu that records each time it is asked to open, as (the item to place, where, in which view), and stays
    closed: a menu that opens waits there for a choice."""

    def popUpMenuPositioningItem_atLocation_inView_(self, item, location, view):
        self.openings = getattr(self, "openings", []) + [(item, tuple(location), view)]
        return False


def make_sample_snapshot(second_window_tabs=2):
    window_one = WindowEntry("w1", "agent-command-tab", False, (0, 0, 800, 600), 4, (
        make_entry("1", "planning", "working", is_in_front=True),
        make_entry("2", "phase0-tests", "asking"),
        make_entry("3", "codex-review", "done", tool="Codex"),
    ))
    window_two = WindowEntry("w2", "tandem-repeats", False, (0, 0, 800, 600), second_window_tabs, tuple(
        make_entry(str(10 + index), f"analysis-{index}", "idle") for index in range(second_window_tabs)))
    monitor = MonitorEntry("1", "Built-in", (0, 0, 2000, 1300), (0, 0, 2000, 1260), (window_one, window_two))
    return ComputerSnapshot("c", "Test Mac", 1.0, (monitor,))


def make_sample_snapshot_with_entries_changed(window_index=None, entry_index=None, **changed_fields):
    """Returns make_sample_snapshot's snapshot with these fields changed in its entry at
    entry_index (in every entry when None) of its window at window_index (of every window when None)."""
    snapshot = make_sample_snapshot()
    return replace_windows_of_the_first_monitor(snapshot, [
        window._replace(agent_sessions=tuple(entry._replace(**changed_fields) if entry_index in (None, index) else entry
                                             for index, entry in enumerate(window.agent_sessions)))
        if window_index in (None, this_window_index) else window
        for this_window_index, window in enumerate(snapshot.monitors[0].windows)])


def make_sample_snapshot_with_changes_by_tab_id(changed_fields_by_tab_id):
    """Returns make_sample_snapshot's snapshot with each entry whose tab ID ("1", "10", ...) is in
    changed_fields_by_tab_id changed by those fields."""
    snapshot = make_sample_snapshot()
    return replace_windows_of_the_first_monitor(snapshot, [
        window._replace(agent_sessions=tuple(entry._replace(**changed_fields_by_tab_id.get(entry.tab_id, {}))
                                             for entry in window.agent_sessions))
        for window in snapshot.monitors[0].windows])


def make_two_monitor_snapshot(one_monitor_snapshot):
    window_one, window_two = one_monitor_snapshot.monitors[0].windows
    return one_monitor_snapshot._replace(monitors=(
        one_monitor_snapshot.monitors[0]._replace(windows=(window_one,)),
        MonitorEntry("2", "Studio Display", (2000, 0, 2560, 1440), (2000, 0, 2560, 1400), (window_two,)),
    ))


# A monitor far outside every monitor, so a window placed on it (a sidebar a test shows) stays out of sight.
FAR_AWAY_SCREEN = {"name": "Far away", "frame": (-4000, -4000, 1500, 1000), "visible_frame": (-4000, -4000, 1500, 970)}
COMPACT_IN_THE_MENU_OF_SEVERAL_ROWS_TOOL_TIP = ("Types /compact into each selected agent, which summarizes its conversation "
                                                "so far to free up room in its context window")


def list_menu_items_without_a_tool_tip(menu):
    """Returns the titles of the menu's items, those of its submenus' items left out, that have no tooltip."""
    return [menu_item.title() for menu_item in menu.itemArray() if not menu_item.isSeparatorItem() and not menu_item.toolTip()]


def replace_windows_of_the_first_monitor(snapshot, windows):
    """Returns the one-monitor snapshot with these windows on its monitor."""
    return snapshot._replace(monitors=(snapshot.monitors[0]._replace(windows=tuple(windows)),))


def build_right_click_menu(controller, outline_view, row):
    """Returns the menu a list's controller builds as if that row of its outline view were right-clicked."""
    item = outline_view.itemAtRow_(row)
    controller.find_clicked_item = lambda: item
    menu = NSMenu.alloc().initWithTitle_("")
    controller.menuNeedsUpdate_(menu)
    return menu


def make_actions_finish_with(stand_in_app_controller, action_result):
    """Has the stand-in app controller's submit_action return a future already done with action_result."""
    done_future = concurrent.futures.Future()
    done_future.set_result(action_result)
    stand_in_app_controller.submit_action.return_value = done_future


def run_main_thread_calls_at_once():
    """Returns a patch under which AppHelper.callAfter runs its function at once, as the main thread would run it later."""
    return unittest.mock.patch("agent_command_tab.app_controller.AppHelper.callAfter",
                               lambda function, *arguments: function(*arguments))


def type_into_the_open_rename_editor(outline_view, row, text):
    """Puts text in the editor of the row's name being renamed, as typing does, leaving the rename open."""
    outline_view.viewAtColumn_row_makeIfNecessary_(0, row, False).name_text_field.currentEditor().setString_(text)


def select_rows_of(outline_view, rows):
    outline_view.selectRowIndexes_byExtendingSelection_(build_index_set(rows), False)


def list_recipient_radio_buttons_top_first(recipient_choices):
    """Returns the Talk to Each Other window's "Send the message to:" radio buttons, top first: the list's order, then "each of"."""
    return sorted(recipient_choices.recipients_by_radio_button, key=lambda radio_button: -radio_button.frame().origin.y)


def make_key_down_event(window, characters, key_code, modifier_flags=0):
    """Returns a key press in the window, as AppKit sends it."""
    return NSEvent.keyEventWithType_location_modifierFlags_timestamp_windowNumber_context_characters_charactersIgnoringModifiers_isARepeat_keyCode_(
        NSEventTypeKeyDown, NSMakePoint(0, 0), modifier_flags, 0, window.windowNumber(), None, characters, characters, False, key_code)


def make_mouse_event(window, event_type, point_in_the_window):
    """Returns a press, drag or release (the event type) of the left mouse button at this point of the window, as AppKit sends it."""
    return NSEvent.mouseEventWithType_location_modifierFlags_timestamp_windowNumber_context_eventNumber_clickCount_pressure_(
        event_type, point_in_the_window, 0, 0, window.windowNumber(), None, 0, 1, 1.0)


class UserInterfaceSmokeTests(unittest.TestCase):
    def run(self, result=None):
        # AppKit keeps each test's window and views in the autorelease pool, which nothing empties while no event loop
        # runs: without a pool of its own for each test, every test's window would stay in memory until the last test ends.
        with objc.autorelease_pool():
            return super().run(result)

    def setUp(self):
        # A stand-in for NSApp that does nothing when asked to make the app the active one, which would take the keyboard
        # from the app the person running the tests is typing in, and passes everything else on to the real NSApp. The
        # list asks for that to rename a row in the dropdown, and the app controller before it shows an alert.
        self.application_that_is_never_made_active = unittest.mock.Mock(wraps=NSApp)
        self.application_that_is_never_made_active.activateIgnoringOtherApps_ = unittest.mock.Mock()
        for module_name in ("agent_command_tab.user_interface.agent_session_outline_view", "agent_command_tab.app_controller"):
            application_patch = unittest.mock.patch(f"{module_name}.NSApp", self.application_that_is_never_made_active)
            application_patch.start()
            self.addCleanup(application_patch.stop)
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

    def test_selection_survives_rows_changed_in_place_and_a_reload(self):
        self.list_controller.apply_snapshot(make_sample_snapshot())
        outline_view = self.list_controller.outline_view
        selected_item = outline_view.itemAtRow_(5)
        outline_view.selectRowIndexes_byExtendingSelection_(NSIndexSet.indexSetWithIndex_(5), False)
        self.list_controller.apply_snapshot(make_sample_snapshot(second_window_tabs=3))
        self.assertIs(outline_view.itemAtRow_(outline_view.selectedRow()), selected_item)
        # A tab above it goes, so the item, not the row number, stays selected.
        snapshot = make_sample_snapshot(second_window_tabs=3)
        window_one, window_two = snapshot.monitors[0].windows
        snapshot = replace_windows_of_the_first_monitor(
            snapshot, (window_one._replace(agent_sessions=window_one.agent_sessions[:2]), window_two))
        self.list_controller.apply_snapshot(snapshot)
        self.assertEqual(outline_view.selectedRow(), 4)
        self.assertIs(outline_view.itemAtRow_(4), selected_item)
        # The windows go under monitor rows, so the rows are reloaded.
        self.apply_snapshot_and_check_that_the_rows_are_reloaded(make_two_monitor_snapshot(snapshot))
        self.assertEqual([item.key for item in self.list_controller.list_selected_items()], [selected_item.key])

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

    def test_dropping_a_window_back_in_place_moves_nothing(self):
        # It moved past the windows Hide Idle hides after it.
        self.list_controller.apply_snapshot(make_sample_snapshot())
        outline_view = self.list_controller.outline_view
        self.list_controller.dragged_item = outline_view.itemAtRow_(0)
        for child_index in (0, 1):
            self.list_controller.outlineView_acceptDrop_item_childIndex_(outline_view, None, None, child_index)
        self.assertEqual(self.submitted_actions, [])
        self.list_controller.outlineView_acceptDrop_item_childIndex_(outline_view, None, None, 2)
        self.assertEqual(self.submitted_actions[-1].action, "reorder_window_list")
        self.assertIsNone(self.submitted_actions[-1].arguments["drop_before_window_id"])  # after the last window

    def test_dropping_the_last_shown_window_below_the_plus_row_moves_nothing(self):
        # AppKit counts the "+" row among the top-level rows, so a drop below it comes with an index one past
        # the windows': moving the window to the end would put it past the window Hide Idle hides after it.
        self.list_controller.apply_snapshot(make_sample_snapshot())
        self.list_controller.set_idle_tabs_are_hidden(True)  # tandem-repeats' two tabs are idle
        outline_view = self.list_controller.outline_view
        self.assertEqual(self.list_row_item_ids(), ["w1", "1", "2", "3", "new_window"])
        self.list_controller.dragged_item = outline_view.itemAtRow_(0)
        for child_index in (1, 2):  # above the "+" row, and below it
            self.assertEqual(self.list_controller.outlineView_validateDrop_proposedItem_proposedChildIndex_(
                outline_view, None, None, child_index), NSDragOperationMove)
            self.list_controller.outlineView_acceptDrop_item_childIndex_(outline_view, None, None, child_index)
        self.assertEqual(self.submitted_actions, [])

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
            outline_view, None, None, 0), NSDragOperationNone)  # windows keep the order set in Tab Order
        # With two monitors, a window still moves to the other monitor while sorted, but not within its own.
        self.list_controller.apply_snapshot(make_two_monitor_snapshot(make_sample_snapshot()))
        first_monitor_item, second_monitor_item = self.list_controller.top_level_items
        self.list_controller.dragged_item = first_monitor_item.child_items[0]
        self.assertEqual(self.list_controller.outlineView_validateDrop_proposedItem_proposedChildIndex_(
            outline_view, None, second_monitor_item, -1), NSDragOperationMove)
        self.assertEqual(self.list_controller.outlineView_validateDrop_proposedItem_proposedChildIndex_(
            outline_view, None, first_monitor_item, 0), NSDragOperationNone)

    def test_shown_columns_follow_the_settings(self):
        self.list_controller.apply_snapshot(make_sample_snapshot_with_entries_changed(0, started_at=time.time() - 3700))
        self.window.contentView().layoutSubtreeIfNeeded()
        row_view = self.list_controller.outline_view.viewAtColumn_row_makeIfNecessary_(0, 1, True)
        self.assertEqual(self.list_controller.column_widths["created_time"], 0)  # off by default
        self.list_controller.set_shown_columns(["created_time", "existence_duration"])
        row_view.layoutSubtreeIfNeeded()
        self.assertEqual([not row_view.column_views[column_name].isHidden()
                          for column_name in ("created_time", "existence_duration", "state")],
                         [True, True, False])
        self.assertEqual(row_view.column_views["existence_duration"].stringValue(), "1h 1m")
        self.list_controller.set_shown_columns([])
        row_view.layoutSubtreeIfNeeded()
        self.assertTrue(row_view.column_views["existence_duration"].isHidden())
        # Only the tool icon column is left: it shows by itself, since Claude Code and Codex both run.
        self.assertEqual(self.list_controller.measure_right_columns_width(), self.list_controller.column_widths["agent_tool_icon"])

    def test_the_bars_around_the_list_are_on_the_lists_background_in_dark_mode_and_on_the_window_in_light_mode(self):
        self.list_controller.apply_snapshot(make_sample_snapshot())
        view = self.window.contentView()
        for appearance_name in (NSAppearanceNameDarkAqua, NSAppearanceNameAqua):
            self.window.setAppearance_(NSAppearance.appearanceNamed_(appearance_name))
            view.layoutSubtreeIfNeeded()
            image = render_view_into_bitmap(view)
            scale = image.pixelsWide() / view.bounds().size.width

            def read_drawn_color(x, y_from_the_top):
                """Returns the (red, green, blue, alpha) drawn at this point of the panel."""
                return read_drawn_color_components(image, scale, x, y_from_the_top)
            # The list's background: the empty right end of the "+" row, the list's last.
            outline_view = self.list_controller.outline_view
            plus_row_rect = view.convertRect_fromView_(outline_view.rectOfRow_(outline_view.numberOfRows() - 1), outline_view)
            list_background = read_drawn_color(plus_row_rect.origin.x + plus_row_rect.size.width - 40,
                                               view.bounds().size.height - plus_row_rect.origin.y - plus_row_rect.size.height / 2)
            # 2 points in from the top bar's and the bottom bar's left ends, left of their controls. In light mode the
            # panel draws nothing there, so this test window's own background shows: nothing.
            for name, y_from_the_top in (("top bar", 2), ("bottom bar", view.bounds().size.height - 3)):
                drawn = read_drawn_color(2, y_from_the_top)
                if appearance_name == NSAppearanceNameDarkAqua:
                    for drawn_component, list_component in zip(drawn, list_background):
                        self.assertAlmostEqual(drawn_component, list_component, delta=0.02, msg=name)
                else:
                    self.assertEqual(drawn[3], 0, name)

    def test_the_name_title_stands_on_the_same_line_as_the_column_titles(self):
        self.list_controller.apply_snapshot(make_sample_snapshot_with_entries_changed(0, context_tokens=91_000, context_fill_percent=46))
        self.window.contentView().layoutSubtreeIfNeeded()
        row_view = self.list_controller.column_titles_row_view
        image = render_view_into_bitmap(row_view)
        scale = image.pixelsWide() / row_view.bounds().size.width

        def find_lowest_drawn_pixel_row(text_field):
            """Returns the lowest pixel row (counted from the top) that the field's text draws on: for a
            title without descenders, such as "Name" and "Tokens", its baseline."""
            frame = text_field.frame()
            return max(y for y in range(image.pixelsHigh())
                       for x in range(int(frame.origin.x * scale), int((frame.origin.x + frame.size.width) * scale))
                       if image.colorAtX_y_(x, y).alphaComponent() > 0.3)
        self.assertEqual(find_lowest_drawn_pixel_row(row_view.name_title_text_field),
                         find_lowest_drawn_pixel_row(row_view.column_title_text_fields["context_tokens"]))

    def test_the_context_window_share_in_parentheses_is_amber_from_50_percent_and_red_from_75_percent(self):
        self.list_controller.apply_snapshot(make_sample_snapshot_with_changes_by_tab_id({
            "1": dict(context_tokens=610_000, context_fill_percent=61), "2": dict(context_tokens=91_000, context_fill_percent=46),
            "3": dict(context_tokens=170_000, context_fill_percent=85)}))
        self.window.contentView().layoutSubtreeIfNeeded()
        # The rows of planning, phase0-tests and codex-review: light mode has a darker amber than the system yellow.
        for row, expected_share_colors in ((1, ((205, 145, 0, 255), resolve_color(NSColor.systemYellowColor(), NSAppearanceNameDarkAqua))),
                                           (2, (resolve_color(NSColor.labelColor(), NSAppearanceNameAqua),
                                                resolve_color(NSColor.labelColor(), NSAppearanceNameDarkAqua))),
                                           (3, (resolve_color(NSColor.systemRedColor(), NSAppearanceNameAqua),
                                                resolve_color(NSColor.systemRedColor(), NSAppearanceNameDarkAqua)))):
            column_text = self.lay_out_row_view(row).column_views["context_tokens"].attributedStringValue()
            share_start = column_text.string().index("(")
            size_color, share_color = (column_text.attribute_atIndex_effectiveRange_(NSForegroundColorAttributeName, index, None)[0]
                                       for index in (0, share_start))
            self.assertEqual(size_color, NSColor.labelColor())
            self.assertEqual((resolve_color(share_color, NSAppearanceNameAqua), resolve_color(share_color, NSAppearanceNameDarkAqua)),
                             expected_share_colors)

    def test_the_rows_are_on_a_very_light_gray_in_light_mode_only(self):
        outline_view = self.list_controller.outline_view
        for appearance_name in (NSAppearanceNameDarkAqua, NSAppearanceNameAqua):
            self.window.setAppearance_(NSAppearance.appearanceNamed_(appearance_name))
            self.window.contentView().layoutSubtreeIfNeeded()
            if appearance_name == NSAppearanceNameDarkAqua:
                # controlBackgroundColor itself, which AppKit doesn't fill solid: the dropdown's lighter
                # gray shows through, unlike in the bars around the list.
                self.assertEqual(outline_view.backgroundColor(), NSColor.controlBackgroundColor())
                self.assertFalse(outline_view.isOpaque())
            else:
                self.assertEqual(resolve_color(outline_view.backgroundColor(), NSAppearanceNameAqua), (247, 247, 247, 255))

    def test_the_status_text_is_in_its_states_color(self):
        self.assertLessEqual(set(STATE_TEXT_COLOR_NAME_BY_STATE.values()), set(COLOR_MAKERS_BY_NAME))
        self.list_controller.apply_snapshot(make_sample_snapshot())
        self.window.contentView().layoutSubtreeIfNeeded()
        gray, names_color = NSColor.secondaryLabelColor(), NSColor.labelColor()
        # Light mode has a darker green and blue than the system's; done is gray in light mode only.
        for row, expected_state_text, expected_light_and_dark_mode_colors in (
                (1, "thinking", ((35, 135, 61, 255), resolve_color(NSColor.systemGreenColor(), NSAppearanceNameDarkAqua))),
                (2, "asking", ((0, 129, 155, 255), resolve_color(NSColor.systemCyanColor(), NSAppearanceNameDarkAqua))),
                (3, "done", (resolve_color(gray, NSAppearanceNameAqua), resolve_color(names_color, NSAppearanceNameDarkAqua))),
                (5, "idle", ((0, 0, 0, 97), (255, 255, 255, 102)))):  # a little fainter than gray: 38% and 40%
            row_view = self.lay_out_row_view(row)
            state_text = row_view.column_views["state"].attributedStringValue()
            self.assertEqual(state_text.string(), expected_state_text)
            # Semibold, easier to read, except idle.
            self.assertEqual(state_text.attribute_atIndex_effectiveRange_(NSFontAttributeName, 0, None)[0],
                             self.list_controller.regular_font if expected_state_text == "idle" else self.list_controller.semibold_font)
            state_color = state_text.attribute_atIndex_effectiveRange_(NSForegroundColorAttributeName, 0, None)[0]
            self.assertEqual((resolve_color(state_color, NSAppearanceNameAqua), resolve_color(state_color, NSAppearanceNameDarkAqua)),
                             expected_light_and_dark_mode_colors)

    def test_column_titles_sit_over_their_columns_between_the_top_bar_and_the_list(self):
        self.list_controller.apply_snapshot(make_sample_snapshot())  # no context sizes known: no token columns
        self.window.contentView().layoutSubtreeIfNeeded()
        titles_row_view = self.list_controller.column_titles_row_view
        titles_row_view.layoutSubtreeIfNeeded()
        row_view = self.lay_out_row_view(1)
        titles_row_frame = titles_row_view.frame()
        self.assertFalse(titles_row_view.isHidden())
        # One line of titles, right under the top bar and right above the list.
        self.assertEqual(titles_row_frame.size.height, COLUMN_TITLE_LINE_HEIGHT + 2 * COLUMN_TITLES_ROW_PADDING)
        self.assertEqual(titles_row_frame.origin.y + titles_row_frame.size.height,
                         self.window.contentView().frame().size.height - TOP_BAR_HEIGHT)
        scroll_view_frame = self.list_controller.outline_view.enclosingScrollView().frame()
        self.assertEqual(scroll_view_frame.origin.y + scroll_view_frame.size.height, titles_row_frame.origin.y)
        title_fields = titles_row_view.column_title_text_fields
        self.assertEqual(titles_row_view.name_title_text_field.stringValue(), "Name")
        self.assertEqual(title_fields["state"].stringValue(), "Status")
        self.assertFalse(title_fields["state"].isHidden())
        self.assertEqual((title_fields["state"].frame().origin.x, title_fields["state"].frame().size.width),  # in the row's x, which is the panel's
                         (row_view.column_views["state"].frame().origin.x, row_view.column_views["state"].frame().size.width))
        self.assertTrue(title_fields["context_tokens"].isHidden())
        # "Name" is centered over the names' room, which ends before the leftmost column (here the tool icon's).
        name_title_frame = titles_row_view.name_title_text_field.frame()
        self.assertEqual(name_title_frame.origin.x + name_title_frame.size.width,
                         row_view.column_views["agent_tool_icon"].frame().origin.x - GAP_BEFORE_DETAILS)

    def press_and_release_on_the_column_titles_row(self, press_point_in_the_row, release_point_in_the_row):
        """Presses the mouse button at one point of the row of titles and releases it at another, as AppKit delivers
        both: to the row of titles, whatever is under the pointer."""
        titles_row_view = self.list_controller.column_titles_row_view
        for event_type, row_view_method, point_in_the_row in (
                (NSEventTypeLeftMouseDown, titles_row_view.mouseDown_, press_point_in_the_row),
                (NSEventTypeLeftMouseUp, titles_row_view.mouseUp_, release_point_in_the_row)):
            row_view_method(make_mouse_event(self.window, event_type, titles_row_view.convertPoint_toView_(point_in_the_row, None)))

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
        self.press_and_release_on_the_column_titles_row(point_in_the_row, point_in_the_row)

    def test_a_click_on_the_top_bar_leaves_no_drag_for_a_later_press_on_a_column_title_to_go_on_with(self):
        # A press on a column title passes its drags up to the panel. Were the place of an earlier press on the top bar
        # still kept then, the pointer's first movement on the title would count as the top bar dragged from that
        # place, which turns the dropdown or the sidebar into the list window.
        self.list_controller.apply_snapshot(make_sample_snapshot())
        self.window.contentView().layoutSubtreeIfNeeded()
        panel = self.dropdown_panel_controller
        top_bar_drag_starts = []
        self.owner.top_bar_dragged = lambda list_panel_controller, start_point_on_screen: top_bar_drag_starts.append(start_point_on_screen)
        # The top bar's own space, between Hide Idle and the expand button.
        idle_tabs_button_frame = panel.idle_tabs_button.frame()
        point_on_the_top_bar = NSMakePoint(
            idle_tabs_button_frame.origin.x + idle_tabs_button_frame.size.width + GAP_BETWEEN_TOP_BAR_BUTTONS / 2,
            panel.view().bounds().size.height - TOP_BAR_HEIGHT / 2)
        self.assertIs(panel.view().hitTest_(point_on_the_top_bar), panel.view())
        titles_row_view = self.list_controller.column_titles_row_view
        title_frame = titles_row_view.name_title_text_field.frame()
        point_on_a_title = titles_row_view.convertPoint_toView_(NSMakePoint(NSMidX(title_frame), NSMidY(title_frame)), None)

        def send_mouse_event(event_type, point_in_the_window):  # through the window, which chooses the view as for a real press
            self.window.sendEvent_(make_mouse_event(self.window, event_type, point_in_the_window))
        with unittest.mock.patch("agent_command_tab.user_interface.popover_container.NSEvent") as stand_in_event_class:
            stand_in_event_class.mouseLocation.return_value = NSMakePoint(500, 800)  # the pointer, on the screen
            send_mouse_event(NSEventTypeLeftMouseDown, point_on_the_top_bar)
            self.assertIsNotNone(panel.view().top_bar_drag_start)
            send_mouse_event(NSEventTypeLeftMouseUp, point_on_the_top_bar)
            stand_in_event_class.mouseLocation.return_value = NSMakePoint(400, 770)  # moved to the title, then pressed there
            send_mouse_event(NSEventTypeLeftMouseDown, point_on_a_title)
            send_mouse_event(NSEventTypeLeftMouseDragged, NSMakePoint(point_on_a_title.x + 1, point_on_a_title.y))
            send_mouse_event(NSEventTypeLeftMouseUp, NSMakePoint(point_on_a_title.x + 1, point_on_a_title.y))
            self.assertEqual(top_bar_drag_starts, [])
            # The top bar itself still drags, from where it was pressed.
            stand_in_event_class.mouseLocation.return_value = NSMakePoint(500, 800)
            send_mouse_event(NSEventTypeLeftMouseDown, point_on_the_top_bar)
            stand_in_event_class.mouseLocation.return_value = NSMakePoint(490, 800)
            send_mouse_event(NSEventTypeLeftMouseDragged, point_on_the_top_bar)
            send_mouse_event(NSEventTypeLeftMouseUp, point_on_the_top_bar)
        self.assertEqual(top_bar_drag_starts, [(500, 800)])

    def test_clicking_a_column_title_sorts_by_it_then_the_other_way_then_not_at_all(self):
        # Context sizes known for planning (467k of 1M) and codex-review (91k of 200k), not for phase0-tests.
        self.list_controller.apply_snapshot(make_sample_snapshot_with_changes_by_tab_id({
            "1": dict(context_tokens=467_000, context_fill_percent=47), "3": dict(context_tokens=91_000, context_fill_percent=46)}))
        self.window.contentView().layoutSubtreeIfNeeded()
        outline_view = self.list_controller.outline_view

        def choose_and_show(list_sort_order, list_sort_order_is_reversed):  # as the app controller does, for every list
            self.owner.chosen_list_sort_orders.append((list_sort_order, list_sort_order_is_reversed))
            self.list_controller.set_list_sort_order(list_sort_order, list_sort_order_is_reversed)
        self.owner.list_sort_order_chosen = choose_and_show
        column_widths_before = dict(self.list_controller.column_widths)
        title_fields = self.list_controller.column_titles_row_view.column_title_text_fields
        # Each title's hover text says what its column shows, then what the next click on it does.
        what_the_tokens_column_shows = "Number of tokens (and % of context window) used so far. "
        self.assertEqual(title_fields["context_tokens"].toolTip(), what_the_tokens_column_shows + "Click to sort the list by this column")
        for expected_sort, expected_tokens_title, expected_tab_titles, what_the_next_click_does in (
                (("context_percent", False), "Tokens ▾", ["planning", "codex-review", "phase0-tests"],
                 "Click to reverse-sort the list by this column"),
                (("context_percent", True), "Tokens ▴", ["codex-review", "planning", "phase0-tests"],  # unknown size last both ways
                 "Click to stop sorting the list by this column"),
                (("manual", False), "Tokens", ["planning", "phase0-tests", "codex-review"], "Click to sort the list by this column")):
            self.click_column_title("context_tokens")
            self.assertEqual(self.owner.chosen_list_sort_orders[-1], expected_sort)
            self.assertEqual(title_fields["context_tokens"].stringValue(), expected_tokens_title)
            self.assertEqual(title_fields["context_tokens"].toolTip(), what_the_tokens_column_shows + what_the_next_click_does)
            self.assertEqual([outline_view.itemAtRow_(row).entry.tab_title for row in range(1, 4)], expected_tab_titles)
            self.assertEqual(self.list_controller.column_widths, column_widths_before)  # the arrow had room already
        self.click_column_title("state")  # another title starts over in its first direction
        self.assertEqual(self.owner.chosen_list_sort_orders[-1], ("activity", False))
        self.assertEqual(title_fields["state"].stringValue(), "Status ▾")
        self.assertEqual(title_fields["state"].toolTip(), "Agent's current status such as 'thinking', 'asking', 'done', 'idle'. "
                         "Click to reverse-sort the list by this column")
        name_title_field = self.list_controller.column_titles_row_view.name_title_text_field
        self.assertEqual(name_title_field.toolTip(), "The name of the window or tab. Click to sort the list by this column")
        self.click_column_title("name")
        self.assertEqual(self.owner.chosen_list_sort_orders[-1], ("alphabet", False))
        self.assertEqual((name_title_field.stringValue(), title_fields["state"].stringValue()),
                         ("Name ▴", "Status"))  # A to Z: up, as in Finder
        self.assertEqual((name_title_field.toolTip(), title_fields["state"].toolTip()),
                         ("The name of the window or tab. Click to reverse-sort the list by this column",
                          "Agent's current status such as 'thinking', 'asking', 'done', 'idle'. Click to sort the list by this column"))
        self.assertIsNone(title_fields["agent_tool_icon"].toolTip())  # no title, and no click sorts by it

    def test_the_created_and_age_titles_which_sort_by_the_same_order_say_the_same_about_the_next_click(self):
        for list_sort_order, is_reversed, what_the_next_click_does in (
                ("manual", False, "Click to sort the list by this column"),
                ("activity", True, "Click to sort the list by this column"),  # sorted by another column
                ("created_time", False, "Click to reverse-sort the list by this column"),
                ("created_time", True, "Click to stop sorting the list by this column")):
            self.assertEqual(
                [build_column_title_tool_tip(title_name, list_sort_order, is_reversed)
                 for title_name in ("created_time", "existence_duration")],
                ["Time or date when the agent was started. " + what_the_next_click_does,
                 "Time elapsed since the agent was started. " + what_the_next_click_does], (list_sort_order, is_reversed))

    def test_the_last_msg_column_shows_the_time_since_the_last_message_and_its_title_sorts_by_it(self):
        now = time.time()
        # planning works, so its row shows no time; phase0-tests has asked for 1h 1m and codex-review has been done
        # for 2m. The second window's two tabs have no time: when their last message was written isn't known.
        self.list_controller.apply_snapshot(make_sample_snapshot_with_changes_by_tab_id({
            "1": dict(last_message_at=now - 30), "2": dict(last_message_at=now - 3700), "3": dict(last_message_at=now - 150)}))
        self.assertGreater(self.list_controller.column_widths["time_since_last_message"], 0)  # on by default
        self.window.contentView().layoutSubtreeIfNeeded()
        outline_view = self.list_controller.outline_view

        def read_time_shown_in_row(row):
            column_view = self.lay_out_row_view(row).column_views["time_since_last_message"]
            self.assertFalse(column_view.isHidden())
            return column_view.stringValue()

        def list_tab_titles_and_times_shown_in_the_first_window():
            return [(outline_view.itemAtRow_(row).entry.tab_title, read_time_shown_in_row(row)) for row in range(1, 4)]
        self.assertEqual(list_tab_titles_and_times_shown_in_the_first_window(),
                         [("planning", ""), ("phase0-tests", "1h 1m"), ("codex-review", "2m")])
        self.assertEqual([read_time_shown_in_row(row) for row in (5, 6)], ["", ""])  # the second window's tabs
        self.assertEqual(self.lay_out_row_view(0).column_views["time_since_last_message"].stringValue(), "")  # a window row

        def choose_and_show(list_sort_order, list_sort_order_is_reversed):  # as the app controller does, for every list
            self.owner.chosen_list_sort_orders.append((list_sort_order, list_sort_order_is_reversed))
            self.list_controller.set_list_sort_order(list_sort_order, list_sort_order_is_reversed)
        self.owner.list_sort_order_chosen = choose_and_show
        title_field = self.list_controller.column_titles_row_view.column_title_text_fields["time_since_last_message"]
        self.assertEqual(title_field.stringValue(), "Last Msg")
        # The shortest time first, then the longest first, the working tab last both ways, then the tab order again.
        for expected_sort, expected_title, expected_tab_titles_and_times in (
                (("time_since_last_message", False), "Last Msg ▴", [("codex-review", "2m"), ("phase0-tests", "1h 1m"), ("planning", "")]),
                (("time_since_last_message", True), "Last Msg ▾", [("phase0-tests", "1h 1m"), ("codex-review", "2m"), ("planning", "")]),
                (("manual", False), "Last Msg", [("planning", ""), ("phase0-tests", "1h 1m"), ("codex-review", "2m")])):
            self.click_column_title("time_since_last_message")
            self.assertEqual(self.owner.chosen_list_sort_orders[-1], expected_sort)
            self.assertEqual(title_field.stringValue(), expected_title)
            self.assertEqual(list_tab_titles_and_times_shown_in_the_first_window(), expected_tab_titles_and_times)
        self.list_controller.set_shown_columns(["context_tokens", "state"])  # turned off in Settings: it leaves the rows
        self.assertEqual(self.list_controller.column_widths["time_since_last_message"], 0)
        self.assertTrue(self.lay_out_row_view(2).column_views["time_since_last_message"].isHidden())

    def test_last_msg_comes_right_after_age_in_the_rows_over_them_and_in_the_sort_popup(self):
        now = time.time()
        self.list_controller.apply_snapshot(make_sample_snapshot_with_entries_changed(
            status="done", context_tokens=40_546, started_at=now - 3700, last_message_at=now - 150, cpu_percent=12,
            memory_bytes=512 * 1024 ** 2))
        self.list_controller.set_shown_columns(LIST_COLUMNS)
        self.window.setContentSize_((POPOVER_WIDTH + 400, 260))  # room for every column
        self.window.contentView().layoutSubtreeIfNeeded()

        def list_names_of_shown_views_left_to_right(views_by_column_name):
            return sorted((column_name for column_name, view in views_by_column_name.items() if not view.isHidden()),
                          key=lambda column_name: views_by_column_name[column_name].frame().origin.x)
        column_names_left_to_right = ["agent_tool_icon", "state", "context_tokens", "created_time", "existence_duration",
                                      "time_since_last_message", "cpu_usage", "memory_usage"]
        self.assertEqual(list_names_of_shown_views_left_to_right(self.lay_out_row_view(1).column_views), column_names_left_to_right)
        titles_row_view = self.list_controller.column_titles_row_view
        titles_row_view.layoutSubtreeIfNeeded()
        title_fields = titles_row_view.column_title_text_fields
        self.assertEqual(list_names_of_shown_views_left_to_right(title_fields), column_names_left_to_right)
        self.assertEqual([title_fields[column_name].stringValue() for column_name in column_names_left_to_right],
                         ["", "Status", "Tokens", "Created", "Age", "Last Msg", "CPU", "Memory"])
        self.assertEqual(list(self.dropdown_panel_controller.list_sort_order_popup.itemTitles()),
                         ["Tab Order", "Status", "Name", "Tokens", "Age", "Last Msg", "Memory", "CPU"])

    def test_a_sidebar_made_narrower_drops_last_msg_then_the_context_then_the_icon_then_the_state(self):
        # The columns shown by default, each with something to show in the first window's rows.
        snapshot = make_sample_snapshot_with_entries_changed(0, context_tokens=40_546, last_message_at=time.time() - 150)
        self.list_controller.width_is_set_by_the_user = True
        self.window.setContentSize_((560, 260))
        self.list_controller.apply_snapshot(snapshot)
        shown_column_names_as_the_sidebar_narrows = []
        for width in range(560, 198, -2):  # down to the narrowest sidebar
            self.window.setContentSize_((width, 260))
            self.window.contentView().layoutSubtreeIfNeeded()
            shown_column_names = [column_name for column_name, column_width in self.list_controller.column_widths.items() if column_width]
            if shown_column_names not in shown_column_names_as_the_sidebar_narrows:
                shown_column_names_as_the_sidebar_narrows.append(shown_column_names)
        self.assertEqual(shown_column_names_as_the_sidebar_narrows, [
            ["agent_tool_icon", "state", "context_tokens", "time_since_last_message"], ["agent_tool_icon", "state", "context_tokens"],
            ["agent_tool_icon", "state"], ["state"], []])

    def test_a_time_that_grows_in_a_column_that_is_not_shown_does_not_refill_its_row(self):
        now = time.time()
        # codex-review started 20 seconds ago and its last message is 5 seconds old: both times change every second.
        self.list_controller.apply_snapshot(make_sample_snapshot_with_changes_by_tab_id({
            "3": dict(started_at=now - 20.5, last_message_at=now - 5.5)}))
        self.list_controller.set_shown_columns(["context_tokens", "state"])  # Last Msg turned off; Age is off by default
        self.window.contentView().layoutSubtreeIfNeeded()
        row_view = self.lay_out_row_view(3)
        look_before = row_view.look
        with unittest.mock.patch("agent_command_tab.user_interface.agent_session_outline_view.time.time", return_value=now + 3):
            self.list_controller.refreshTimerFired_(None)
            self.assertIs(row_view.look, look_before)  # neither the Age nor the Last Msg column shows
            self.list_controller.set_shown_columns(["context_tokens", "state", "time_since_last_message"])
            self.assertEqual(row_view.column_views["time_since_last_message"].stringValue(), "8s")
        with unittest.mock.patch("agent_command_tab.user_interface.agent_session_outline_view.time.time", return_value=now + 4):
            self.list_controller.refreshTimerFired_(None)
            self.assertEqual(row_view.column_views["time_since_last_message"].stringValue(), "9s")  # a shown time stays current

    def test_a_press_on_a_column_title_that_comes_up_below_the_row_of_titles_sorts_nothing(self):
        self.list_controller.apply_snapshot(make_sample_snapshot())
        self.window.contentView().layoutSubtreeIfNeeded()
        title_frame = self.list_controller.column_titles_row_view.name_title_text_field.frame()
        self.press_and_release_on_the_column_titles_row(
            NSMakePoint(NSMidX(title_frame), NSMidY(title_frame)),
            NSMakePoint(NSMidX(title_frame), -40))  # dragged down over the rows, still in the title's column
        self.assertEqual(self.owner.chosen_list_sort_orders, [])
        self.click_column_title("name")  # a press that comes up on the title it went down on still sorts
        self.assertEqual(self.owner.chosen_list_sort_orders, [("alphabet", False)])

    def test_the_pointer_is_the_pointing_hand_above_the_titles_that_sort(self):
        self.list_controller.apply_snapshot(make_sample_snapshot())
        titles_row_view = self.list_controller.column_titles_row_view
        titles_row_view.layoutSubtreeIfNeeded()
        title_frames = {"name": titles_row_view.name_title_text_field.frame(),
                        **{column_name: text_field.frame() for column_name, text_field in titles_row_view.column_title_text_fields.items()}}
        for x, expected_cursor in ((title_frames["name"].origin.x + 1, NSCursor.pointingHandCursor()),
                                   (title_frames["agent_tool_icon"].origin.x + 1, NSCursor.arrowCursor()),  # no title, no sorting
                                   (title_frames["state"].origin.x - 1, NSCursor.arrowCursor()),  # between two columns
                                   (title_frames["state"].origin.x + 1, NSCursor.pointingHandCursor())):
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

    def test_the_column_titles_row_hides_while_a_search_finds_no_tab_and_comes_back_as_it_was(self):
        # During a search the columns keep the widths of every tab's texts, while the dropdown shrinks to the "+" row
        # that is left: titles placed by those widths can start left of the dropdown's edge.
        self.list_controller.apply_snapshot(make_sample_snapshot())
        titles_row_view = self.list_controller.column_titles_row_view
        titles_row_view.layoutSubtreeIfNeeded()
        status_title_field = titles_row_view.column_title_text_fields["state"]
        column_widths_before_the_search = dict(self.list_controller.column_widths)
        status_title_x_before_the_search = status_title_field.frame().origin.x
        self.type_in_the_search_box("quagga")  # in no title, and in no conversation
        self.owner.answer_conversation_search([])
        self.assertEqual(self.list_row_item_ids(), ["new_window"])
        self.assertTrue(titles_row_view.isHidden())
        self.assertEqual(titles_row_view.frame().size.height, 0)
        scroll_view_frame = self.list_controller.outline_view.enclosingScrollView().frame()
        self.assertEqual(scroll_view_frame.origin.y + scroll_view_frame.size.height,
                         self.window.contentView().frame().size.height - TOP_BAR_HEIGHT)
        self.assertEqual(self.list_controller.column_widths, column_widths_before_the_search)
        self.type_in_the_search_box("")  # every row again, at once
        titles_row_view.layoutSubtreeIfNeeded()
        self.assertFalse(titles_row_view.isHidden())
        self.assertEqual(titles_row_view.frame().size.height, COLUMN_TITLE_LINE_HEIGHT + 2 * COLUMN_TITLES_ROW_PADDING)
        self.assertFalse(status_title_field.isHidden())
        self.assertEqual(status_title_field.frame().origin.x, status_title_x_before_the_search)

    def test_memory_and_cpu_columns_show_the_agents_use(self):
        self.list_controller.apply_snapshot(make_sample_snapshot_with_entries_changed(0, 0, memory_bytes=512 * 1024 ** 2, cpu_percent=12))
        self.list_controller.set_shown_columns(["memory_usage", "cpu_usage", "state"])
        self.window.setContentSize_((POPOVER_WIDTH + 200, 260))
        self.window.contentView().layoutSubtreeIfNeeded()
        row_view = self.lay_out_row_view(1)
        state_view, cpu_view, memory_view = (
            row_view.column_views[column_name] for column_name in ("state", "cpu_usage", "memory_usage"))
        self.assertEqual((memory_view.stringValue(), cpu_view.stringValue()), ("512M", "12%"))
        # Left to right: the state right after the name, then CPU, then memory.
        self.assertLess(state_view.frame().origin.x, cpu_view.frame().origin.x)
        self.assertLess(cpu_view.frame().origin.x, memory_view.frame().origin.x)
        # A column is as wide as its title with a sort arrow, here wider than "1024M".
        self.assertGreaterEqual(self.list_controller.column_widths["memory_usage"], TEXT_FIELD_INSETS
                                + self.list_controller.measure_text_width("Memory ▾", self.list_controller.column_title_font))

    def test_top_bar_drops_the_sort_label_then_narrows_the_sort_popup_when_narrow(self):
        panel = self.dropdown_panel_controller
        # Two windows with running agents: the expand or collapse button shows, and the pause or
        # resume button once a running tab is selected.
        self.list_controller.apply_snapshot(make_sample_snapshot())
        self.select_rows(1)  # "planning", which runs
        # The widths where each step happens follow from the controls' measured widths, which differ
        # between macOS versions and Python builds: on macOS 27 the label drops below 376 points and
        # the pop-up would hide below 306, while GitHub's macOS 26 runners measure the controls a few
        # points wider or narrower.
        label_frame = panel.sort_label.frame()
        full_popup_width = panel.sort_popup_width_for_its_widest_title
        # From the gap before Hide Idle to the panel's right edge.
        width_from_the_buttons_to_the_right_edge = (
            GAP_BETWEEN_TOP_BAR_BUTTONS + panel.idle_tabs_button.frame().size.width
            + GAP_BETWEEN_TOP_BAR_BUTTONS + panel.expand_or_collapse_all_button.frame().size.width + PANEL_SIDE_MARGIN)
        # The search box at its smallest and its "aA" checkbox keep their room before the sort control's.
        width_from_the_search_box_to_the_right_edge = (
            GAP_AFTER_THE_SORT_CONTROL_AND_THE_SETTINGS_BUTTON + SMALLEST_SEARCH_FIELD_WIDTH + GAP_BETWEEN_TOP_BAR_BUTTONS
            + panel.case_sensitive_checkbox.frame().size.width + width_from_the_buttons_to_the_right_edge)
        narrowest_width_with_the_label = (label_frame.origin.x + label_frame.size.width + 2 + full_popup_width
                                          + width_from_the_search_box_to_the_right_edge)
        narrowest_width_with_the_full_popup = label_frame.origin.x + full_popup_width + width_from_the_search_box_to_the_right_edge
        narrowest_width_with_the_popup = label_frame.origin.x + SMALLEST_SORT_POPUP_WIDTH + width_from_the_search_box_to_the_right_edge
        # The label drops first, then the pop-up narrows, then it hides, which never happens in the
        # dropdown (only in a sidebar or a list window, where Command-S still opens its menu).
        self.assertGreater(narrowest_width_with_the_label, narrowest_width_with_the_full_popup)
        self.assertGreater(narrowest_width_with_the_full_popup, narrowest_width_with_the_popup)
        self.assertLessEqual(narrowest_width_with_the_popup, SMALLEST_POPOVER_WIDTH)
        # Narrowed to just above and just below each step, then to the sidebar's smallest width (200),
        # and widened again, as dragging the sidebar's edge does.
        widths = [POPOVER_WIDTH]
        for step_width in (narrowest_width_with_the_label, narrowest_width_with_the_full_popup, narrowest_width_with_the_popup):
            widths += [math.ceil(step_width), math.ceil(step_width) - 1]
        for width in widths + [200, POPOVER_WIDTH]:
            self.window.setContentSize_((width, 260))
            popup_shows = not panel.list_sort_order_popup.isHidden()
            self.assertEqual((not panel.sort_label.isHidden(), popup_shows,
                              panel.list_sort_order_popup.frame().size.width if popup_shows else None),
                             (width >= narrowest_width_with_the_label, width >= narrowest_width_with_the_popup,
                              full_popup_width - max(narrowest_width_with_the_full_popup - width, 0)
                              if width >= narrowest_width_with_the_popup else None), width)
            # Hide Idle right before the expand button, and the pop-up before Hide Idle.
            idle_tabs_button_frame = panel.idle_tabs_button.frame()
            self.assertFalse(panel.idle_tabs_button.isHidden())
            self.assertEqual(idle_tabs_button_frame.origin.x + idle_tabs_button_frame.size.width + GAP_BETWEEN_TOP_BAR_BUTTONS,
                             panel.expand_or_collapse_all_button.frame().origin.x)
            # The search box after the pop-up (at the left edge without it), at least its smallest
            # width, the checkbox after it, both before Hide Idle; the checkbox hides only in a
            # sidebar too narrow for both (200 points).
            search_frame = panel.search_field.frame()
            checkbox_frame = panel.case_sensitive_checkbox.frame()
            self.assertFalse(panel.search_field.isHidden(), width)
            self.assertEqual(panel.case_sensitive_checkbox.isHidden(), width == 200, width)
            self.assertTrue(SMALLEST_SEARCH_FIELD_WIDTH <= search_frame.size.width <= LARGEST_SEARCH_FIELD_WIDTH, width)
            if not panel.list_sort_order_popup.isHidden():
                popup_frame = panel.list_sort_order_popup.frame()
                self.assertEqual(popup_frame.origin.x + popup_frame.size.width + GAP_AFTER_THE_SORT_CONTROL_AND_THE_SETTINGS_BUTTON,
                                 search_frame.origin.x)
            else:
                self.assertEqual(search_frame.origin.x, label_frame.origin.x)
            self.assertLessEqual(
                (checkbox_frame.origin.x + checkbox_frame.size.width if not panel.case_sensitive_checkbox.isHidden()
                 else search_frame.origin.x + search_frame.size.width) + GAP_BETWEEN_TOP_BAR_BUTTONS,
                idle_tabs_button_frame.origin.x, width)
            message_frame = panel.message_text_field.frame()
            self.assertTrue(message_frame.size.width == 0
                            or message_frame.origin.x + message_frame.size.width <= idle_tabs_button_frame.origin.x,
                            width)  # never covers the buttons
            # Settings at the bottom bar's left end, and pause or resume the selected rows at its right end.
            settings_button_frame = panel.settings_button.frame()
            self.assertEqual(settings_button_frame.origin.x, PANEL_SIDE_MARGIN)
            self.assertLessEqual(settings_button_frame.origin.y + settings_button_frame.size.height, BOTTOM_BAR_HEIGHT)
            self.assertFalse(panel.pause_or_resume_selected_rows_button.isHidden())
            pause_button_frame = panel.pause_or_resume_selected_rows_button.frame()
            self.assertEqual(pause_button_frame.origin.x + pause_button_frame.size.width, width - PANEL_SIDE_MARGIN)
            self.assertLessEqual(pause_button_frame.origin.y + pause_button_frame.size.height, BOTTOM_BAR_HEIGHT)
            # Left of it the Save button, and left of that the Restore button, clear of the Settings button also in
            # the narrowest sidebar.
            save_button_frame, restore_button_frame = panel.save_sessions_button.frame(), panel.restore_sessions_button.frame()
            self.assertEqual((panel.save_sessions_button.isHidden(), panel.restore_sessions_button.isHidden()), (False, False))
            self.assertEqual(save_button_frame.origin.x + save_button_frame.size.width + GAP_BETWEEN_TOP_BAR_BUTTONS,
                             pause_button_frame.origin.x)
            self.assertEqual(restore_button_frame.origin.x + restore_button_frame.size.width + GAP_BETWEEN_TOP_BAR_BUTTONS,
                             save_button_frame.origin.x)
            self.assertEqual({restore_button_frame.origin.y, save_button_frame.origin.y}, {pause_button_frame.origin.y})
            self.assertLess(settings_button_frame.origin.x + settings_button_frame.size.width, restore_button_frame.origin.x, width)
        self.window.setContentSize_((320, 260))
        panel.show_message("iTerm2 can't be reached; retrying")  # the sort control, the search box and Hide Idle step aside for it
        self.assertTrue(panel.list_sort_order_popup.isHidden())
        self.assertTrue(panel.search_field.isHidden())
        self.assertTrue(panel.case_sensitive_checkbox.isHidden())
        self.assertTrue(panel.idle_tabs_button.isHidden())
        message_frame = panel.message_text_field.frame()  # up to the expand button
        self.assertEqual(message_frame.origin.x + message_frame.size.width + 8, panel.expand_or_collapse_all_button.frame().origin.x)
        self.assertGreater(message_frame.size.width, 150)
        panel.show_message("")
        self.assertFalse(panel.list_sort_order_popup.isHidden())
        self.assertFalse(panel.idle_tabs_button.isHidden())
        # A terminal that can't be reached while another's rows stay listed: in a wide panel the controls stay,
        self.window.setContentSize_((560, 260))
        panel.show_message("iTerm2: could not connect", sort_control_and_hide_idle_step_aside=False)
        self.assertFalse(panel.list_sort_order_popup.isHidden())
        self.assertFalse(panel.idle_tabs_button.isHidden())
        message_frame = panel.message_text_field.frame()
        self.assertLessEqual(message_frame.origin.x + message_frame.size.width + 8, panel.idle_tabs_button.frame().origin.x)
        # The search box keeps its smallest width, and the message comes after its checkbox.
        self.assertEqual(panel.search_field.frame().size.width, SMALLEST_SEARCH_FIELD_WIDTH)
        checkbox_frame = panel.case_sensitive_checkbox.frame()
        self.assertGreaterEqual(message_frame.origin.x, checkbox_frame.origin.x + checkbox_frame.size.width)
        self.assertGreaterEqual(message_frame.size.width, SMALLEST_MESSAGE_WIDTH_BESIDE_THE_CONTROLS)
        # and in a narrow one, where the message would get too little room beside them, they step aside.
        self.window.setContentSize_((200, 260))
        panel.fit_top_bar_to_width()
        self.assertTrue(panel.list_sort_order_popup.isHidden())
        self.assertTrue(panel.idle_tabs_button.isHidden())
        self.assertGreaterEqual(panel.message_text_field.frame().size.width, SMALLEST_MESSAGE_WIDTH_BESIDE_THE_CONTROLS)
        self.assertTrue(panel.search_field.isHidden())
        # A search box that holds a search stays, without its checkbox, so the list is never filtered unseen.
        panel.show_message("")
        self.window.setContentSize_((400, 260))
        self.type_in_the_search_box("planning")
        panel.show_message("iTerm2: could not connect; the other terminals' tabs are listed")
        self.assertFalse(panel.search_field.isHidden())
        self.assertTrue(panel.case_sensitive_checkbox.isHidden())
        search_field_frame = panel.search_field.frame()
        self.assertEqual(search_field_frame.size.width, SMALLEST_SEARCH_FIELD_WIDTH)
        self.assertGreaterEqual(panel.message_text_field.frame().origin.x, search_field_frame.origin.x + search_field_frame.size.width)

    def test_the_top_bar_is_laid_out_a_second_time_only_after_a_layout_that_kept_the_controls_beside_the_message(self):
        # Only such a layout can leave the message too little room that the controls can still give it by stepping aside.
        panel = self.dropdown_panel_controller
        for width, controls_step_aside, expected_layouts in ((100, True, [True]), (200, False, [False, True]), (560, False, [False])):
            self.window.setContentSize_((width, 260))
            with unittest.mock.patch.object(panel, "lay_out_top_bar", wraps=panel.lay_out_top_bar) as lay_out_top_bar:
                panel.show_message("iTerm2: could not connect", sort_control_and_hide_idle_step_aside=controls_step_aside)
            self.assertEqual([call.args[0] for call in lay_out_top_bar.call_args_list], expected_layouts, width)
            self.assertEqual(panel.message_text_field.frame().size.width < SMALLEST_MESSAGE_WIDTH_BESIDE_THE_CONTROLS, width == 100, width)

    def type_in_the_search_box(self, text):
        """Puts text in the dropdown's search box, as typing does."""
        panel = self.dropdown_panel_controller
        panel.search_field.setStringValue_(text)
        panel.controlTextDidChange_(NSNotification.notificationWithName_object_(NSControlTextDidChangeNotification, panel.search_field))

    def fire_search_answer_wait_timer(self):
        """Ends the list's wait for the core's answer to a search now, rather than after
        SECONDS_THE_ROWS_WAIT_FOR_THE_CONVERSATION_MATCHES, by firing its timer, which then won't fire a second time."""
        self.list_controller.search_box_search.answer_wait_timer.fire()

    def test_a_search_box_emptied_beside_a_message_steps_aside_for_it_and_the_rows_take_its_keyboard(self):
        # An empty box left there would keep the message's room: the top bar is fitted again when the search goes, not
        # only when the message comes.
        panel = self.dropdown_panel_controller
        self.list_controller.apply_snapshot(make_sample_snapshot())
        self.window.setContentSize_((400, 260))

        def empty_it_with_its_button():
            panel.search_field.setStringValue_("")
            panel.searchFieldChanged_(panel.search_field)
        for empty_the_search_box in (
                lambda: self.type_in_the_search_box(""),  # the last letter deleted
                empty_it_with_its_button,
                lambda: panel.control_textView_doCommandBySelector_(panel.search_field, None, "cancelOperation:"),  # Esc
                self.list_controller.clear_the_search):  # Return showed a tab the search found
            panel.show_message("")
            self.assertTrue(panel.put_the_keyboard_in_the_search_box())  # as Command-F does
            self.type_in_the_search_box("planning")
            panel.show_message("iTerm2: could not connect; the other terminals' tabs are listed")
            self.assertFalse(panel.search_field.isHidden())  # it holds a search
            empty_the_search_box()
            self.assertEqual((panel.search_field.stringValue(), self.list_controller.search_box_search.text), ("", ""))
            self.assertTrue(panel.search_field.isHidden())
            self.assertEqual(panel.message_text_field.frame().origin.x, panel.sort_label.frame().origin.x)  # from the left edge
            self.assertIs(self.window.firstResponder(), self.list_controller.outline_view)  # Esc then closes the list
        # Where the controls keep their places beside the message, an emptied box keeps its place and the keyboard.
        self.window.setContentSize_((560, 260))
        panel.show_message("iTerm2: could not connect", sort_control_and_hide_idle_step_aside=False)
        self.assertTrue(panel.put_the_keyboard_in_the_search_box())
        self.type_in_the_search_box("planning")
        self.type_in_the_search_box("")
        self.assertFalse(panel.search_field.isHidden())
        self.assertIsNotNone(panel.search_field.currentEditor())
        self.window.makeFirstResponder_(self.list_controller.outline_view)

    def row_alpha_of_tab(self, tab_id):
        row = self.list_row_item_ids().index(tab_id)
        return self.list_controller.outline_view.viewAtColumn_row_makeIfNecessary_(0, row, True).alphaValue()

    def test_the_search_shows_window_and_tab_title_matches_and_dimmed_conversation_matches_then_every_row_again(self):
        self.list_controller.apply_snapshot(make_sample_snapshot())
        size_changes = []
        self.list_controller.on_rows_size_changed = lambda: size_changes.append(self.list_row_item_ids())
        every_row = ["w1", "1", "2", "3", "w2", "10", "11", "new_window"]
        self.type_in_the_search_box("PLAN")  # case doesn't count unless "aA" is checked
        # The rows, and the dropdown's size, stay until the core has answered which conversations
        # hold the text, then change once.
        self.assertEqual((self.list_row_item_ids(), size_changes), (every_row, []))
        self.assertEqual([(text, is_case_sensitive) for text, is_case_sensitive, _ in self.owner.conversation_searches],
                         [("PLAN", False)])
        self.owner.answer_conversation_search(["s1", "s11"])  # their conversations hold "plan"
        self.assertEqual(self.list_row_item_ids(), ["w1", "1", "w2", "11", "new_window"])  # (a window without matches goes)
        self.assertEqual(size_changes, [self.list_row_item_ids()])
        self.assertEqual(self.list_controller.list_selected_items(), [self.list_controller.outline_view.itemAtRow_(1)])
        self.assertEqual((self.row_alpha_of_tab("1"), self.row_alpha_of_tab("11")), (1.0, CONVERSATION_ONLY_MATCH_ROW_ALPHA))
        # Hide Idle doesn't hide an idle tab that matches, and comes back once the search is empty,
        # at once, since an empty search needs no answer.
        self.dropdown_panel_controller.show_idle_tabs_are_hidden(True)
        self.assertEqual(self.list_row_item_ids(), ["w1", "1", "w2", "11", "new_window"])
        self.type_in_the_search_box("")
        self.assertEqual(self.list_row_item_ids(), ["w1", "1", "2", "3", "new_window"])
        self.assertEqual(self.row_alpha_of_tab("1"), 1.0)
        self.dropdown_panel_controller.show_idle_tabs_are_hidden(False)
        # A window whose title holds the text shows with all its tabs, none dimmed, also one whose
        # conversation holds it.
        self.type_in_the_search_box("tandem")
        self.owner.answer_conversation_search(["s10"])
        self.assertEqual(self.list_row_item_ids(), ["w2", "10", "11", "new_window"])
        self.assertEqual((self.row_alpha_of_tab("10"), self.row_alpha_of_tab("11")), (1.0, 1.0))
        # A text only a conversation holds selects its row once the core answers, so Return opens
        # it; until then the last text's rows stay.
        self.type_in_the_search_box("zebra")
        self.assertEqual(self.list_row_item_ids(), ["w2", "10", "11", "new_window"])
        self.owner.answer_conversation_search(["s2"])
        self.assertEqual(self.list_row_item_ids(), ["w1", "2", "new_window"])
        self.assertEqual([item.item_id for item in self.list_controller.list_selected_items()], ["2"])
        self.type_in_the_search_box("")
        self.assertEqual(self.list_row_item_ids(), every_row)
        self.assertEqual(self.owner.conversation_searches, [])  # an empty search asks nothing

    def test_a_search_the_core_is_slow_to_answer_shows_its_title_matches_meanwhile(self):
        self.list_controller.apply_snapshot(make_sample_snapshot())
        self.type_in_the_search_box("plan")
        self.assertTrue(self.list_controller.search_box_search.answer_wait_timer.isValid())
        self.fire_search_answer_wait_timer()
        self.assertEqual(self.list_row_item_ids(), ["w1", "1", "new_window"])
        self.owner.answer_conversation_search(["s11"])  # the conversation matches come with the answer
        self.assertEqual(self.list_row_item_ids(), ["w1", "1", "w2", "11", "new_window"])
        # An answer in time stops the wait.
        self.type_in_the_search_box("planning")
        wait_timer = self.list_controller.search_box_search.answer_wait_timer
        self.owner.answer_conversation_search([])
        self.assertFalse(wait_timer.isValid())
        self.assertIsNone(self.list_controller.search_box_search.answer_wait_timer)
        self.assertEqual(self.list_row_item_ids(), ["w1", "1", "new_window"])

    def test_the_aa_checkbox_makes_case_count_and_an_answer_to_an_older_search_is_dropped(self):
        panel = self.dropdown_panel_controller
        self.list_controller.apply_snapshot(make_sample_snapshot())
        self.assertEqual(panel.case_sensitive_checkbox.toolTip(), CASE_SENSITIVE_CHECKBOX_TOOL_TIP)
        panel.case_sensitive_checkbox.setState_(NSControlStateValueOn)
        panel.caseSensitiveCheckboxClicked_(panel.case_sensitive_checkbox)
        self.assertEqual(self.owner.search_is_case_sensitive_choices, [True])  # the owner saves it and shows it in every list
        panel.show_search_is_case_sensitive(True)
        self.type_in_the_search_box("Plan")
        self.owner.answer_conversation_search([])
        self.assertEqual(self.list_row_item_ids(), ["new_window"])  # "planning" has no capital P
        self.type_in_the_search_box("Pla")
        self.type_in_the_search_box("plan")  # while the core still answers for "Pla"
        self.assertEqual([(text, is_case_sensitive) for text, is_case_sensitive, _ in self.owner.conversation_searches],
                         [("Pla", True)])
        self.owner.answer_conversation_search(["s10"])  # for "Pla": left out, and "plan" asked
        self.assertEqual(self.list_row_item_ids(), ["new_window"])  # "Plan"'s rows until then
        self.assertEqual([(text, is_case_sensitive) for text, is_case_sensitive, _ in self.owner.conversation_searches],
                         [("plan", True)])
        self.owner.answer_conversation_search(["s11"])
        self.assertEqual(self.list_row_item_ids(), ["w1", "1", "w2", "11", "new_window"])
        # A snapshot whose conversation text changed asks again: conversations grow between snapshots.
        self.list_controller.apply_snapshot(make_sample_snapshot()._replace(conversation_search_text_revision=1))
        self.assertEqual([text for text, _, _ in self.owner.conversation_searches], ["plan"])
        self.owner.answer_conversation_search(["s11"])
        # A closed dropdown keeps its search but asks only once it opens again.
        self.list_controller.defers_rebuilding_while_hidden = True
        self.window.orderOut_(None)
        self.list_controller.apply_snapshot(make_sample_snapshot())
        self.assertEqual(self.owner.conversation_searches, [])
        self.window.orderFrontRegardless()
        self.list_controller.prepare_to_appear()
        self.assertEqual([text for text, _, _ in self.owner.conversation_searches], ["plan"])
        panel.show_search_is_case_sensitive(False)
        self.assertEqual(panel.case_sensitive_checkbox.state(), NSControlStateValueOff)

    def test_command_f_and_slash_reach_the_search_box_whose_keys_move_show_and_empty(self):
        panel = self.dropdown_panel_controller
        self.list_controller.apply_snapshot(make_sample_snapshot())
        outline_view = self.list_controller.outline_view
        self.assertFalse(panel.search_field.canBecomeKeyView())  # a window opening leaves the keyboard on the rows
        for characters, key_code, modifier_flags in (("f", 3, NSEventModifierFlagCommand), ("/", 44, 0)):
            self.window.makeFirstResponder_(outline_view)
            self.press_key(characters, key_code, modifier_flags)
            self.assertIsNotNone(panel.search_field.currentEditor(), characters)
        # "l" is in the titles of three tabs and of neither window; "codex-review" shows for its conversation alone.
        self.type_in_the_search_box("l")
        self.owner.answer_conversation_search(["s3"])
        self.assertEqual(self.list_row_item_ids(), ["w1", "1", "3", "w2", "10", "11", "new_window"])
        self.assertEqual(self.row_alpha_of_tab("3"), CONVERSATION_ONLY_MATCH_ROW_ALPHA)
        self.select_rows(1)
        for command, expected_tab_id in (("moveDown:", "3"), ("moveDown:", "10"), ("moveUp:", "3")):
            self.assertTrue(panel.control_textView_doCommandBySelector_(panel.search_field, None, command))
            self.assertEqual([item.item_id for item in self.list_controller.list_selected_items()], [expected_tab_id], command)
        self.assertFalse(panel.control_textView_doCommandBySelector_(panel.search_field, None, "deleteBackward:"))  # typing edits
        # Return shows the selected row's tab and, once it is in front, empties the search, which found what it was for.
        self.assertTrue(panel.control_textView_doCommandBySelector_(panel.search_field, None, "insertNewline:"))
        self.assertEqual(self.submitted_actions[-1], ActionRequest("focus_agent_session", {"session_id": "s3"}))
        self.owner.after_success_of_the_last_action(ActionResult(True))
        self.assertEqual((panel.search_field.stringValue(), self.list_controller.search_box_search.text), ("", ""))
        # Esc empties the box, then closes the list.
        self.type_in_the_search_box("codex")
        self.assertTrue(panel.control_textView_doCommandBySelector_(panel.search_field, None, "cancelOperation:"))
        self.assertEqual((panel.search_field.stringValue(), self.list_controller.search_box_search.text, self.owner.lists_closed_after_escape),
                         ("", "", []))
        self.assertTrue(panel.control_textView_doCommandBySelector_(panel.search_field, None, "cancelOperation:"))
        self.assertEqual(self.owner.lists_closed_after_escape, [self.list_controller])

    def press_return_in_the_search_box(self):
        panel = self.dropdown_panel_controller
        self.assertTrue(panel.control_textView_doCommandBySelector_(panel.search_field, None, "insertNewline:"))

    def test_return_in_the_search_box_waits_for_the_rows_of_the_text_it_holds(self):
        self.list_controller.apply_snapshot(make_sample_snapshot())
        self.type_in_the_search_box("tandem")
        self.owner.answer_conversation_search(["s10"])
        self.assertEqual([item.item_id for item in self.list_controller.list_selected_items()], ["10"])
        # The core hasn't answered for "zebra" yet: the rows, and the selected row, are still those of "tandem".
        self.type_in_the_search_box("zebra")
        self.press_return_in_the_search_box()
        self.assertEqual((self.submitted_actions, self.list_controller.search_box_search.text), ([], "zebra"))
        self.owner.answer_conversation_search(["s2"])  # only the conversation of phase0-tests holds "zebra"
        self.assertEqual(self.submitted_actions, [ActionRequest("focus_agent_session", {"session_id": "s2"})])
        self.assertFalse(self.list_controller.return_in_the_search_box_waits_for_its_rows)
        # A core slow to answer: Return shows the first tab found by its title, once the rows show the title matches alone.
        self.type_in_the_search_box("plan")
        self.press_return_in_the_search_box()
        self.assertEqual(len(self.submitted_actions), 1)
        self.fire_search_answer_wait_timer()
        self.assertEqual(self.submitted_actions[1:], [ActionRequest("focus_agent_session", {"session_id": "s1"})])
        self.owner.answer_conversation_search(["s11"])  # the late answer shows no second tab
        self.assertEqual(len(self.submitted_actions), 2)
        # With the rows of the text the box holds, Return shows the selected row at once.
        self.press_return_in_the_search_box()
        self.assertEqual(len(self.submitted_actions), 3)

    def test_a_return_with_no_row_among_the_title_matches_waits_on_for_the_cores_answer_and_no_longer(self):
        self.list_controller.apply_snapshot(make_sample_snapshot())
        self.type_in_the_search_box("zebra")  # in no title: only the conversation of phase0-tests holds it
        self.press_return_in_the_search_box()
        self.fire_search_answer_wait_timer()
        self.assertEqual((self.list_row_item_ids(), self.submitted_actions), (["new_window"], []))
        self.owner.answer_conversation_search(["s2"])
        self.assertEqual(self.submitted_actions, [ActionRequest("focus_agent_session", {"session_id": "s2"})])
        # An answer that finds no row either ends the wait: a row found for a later snapshot isn't shown.
        self.type_in_the_search_box("quagga")
        self.press_return_in_the_search_box()
        self.fire_search_answer_wait_timer()
        self.owner.answer_conversation_search([])
        self.list_controller.apply_snapshot(make_sample_snapshot()._replace(conversation_search_text_revision=1))  # asks again
        self.owner.answer_conversation_search(["s2"])  # its conversation holds "quagga" by now
        self.assertEqual([item.item_id for item in self.list_controller.list_selected_items()], ["2"])
        self.assertEqual(len(self.submitted_actions), 1)

    def test_a_return_pressed_once_the_title_matches_alone_show_no_row_waits_for_the_cores_answer_too(self):
        self.list_controller.apply_snapshot(make_sample_snapshot())
        self.type_in_the_search_box("zebra")  # in no title: only the conversation of phase0-tests holds it
        self.fire_search_answer_wait_timer()
        self.assertEqual(self.list_row_item_ids(), ["new_window"])
        self.press_return_in_the_search_box()  # the core is still answering
        self.assertEqual(self.submitted_actions, [])
        self.owner.answer_conversation_search(["s2"])
        self.assertEqual(self.submitted_actions, [ActionRequest("focus_agent_session", {"session_id": "s2"})])
        # Once the core has answered, a Return with no row to show waits for nothing: a row a later answer finds is only selected.
        self.type_in_the_search_box("quagga")
        self.owner.answer_conversation_search([])
        self.press_return_in_the_search_box()
        self.list_controller.apply_snapshot(make_sample_snapshot()._replace(conversation_search_text_revision=1))  # asks again
        self.owner.answer_conversation_search(["s2"])
        self.assertEqual([item.item_id for item in self.list_controller.list_selected_items()], ["2"])
        self.assertEqual(len(self.submitted_actions), 1)

    def test_a_search_answered_while_the_list_is_off_screen_selects_its_first_tab_row_once_the_list_appears(self):
        self.list_controller.defers_rebuilding_while_hidden = True  # as the app sets it for its three lists
        self.list_controller.apply_snapshot(make_sample_snapshot())
        self.type_in_the_search_box("plan")
        self.owner.answer_conversation_search([])
        self.assertEqual([item.item_id for item in self.list_controller.list_selected_items()], ["1"])
        # The answer for "zebra" comes while the list is off screen, which keeps the rows of "plan" until it appears.
        self.type_in_the_search_box("zebra")
        self.window.orderOut_(None)
        self.owner.answer_conversation_search(["s2"])
        self.assertEqual(self.list_row_item_ids(), ["w1", "1", "new_window"])
        self.list_controller.prepare_to_appear()  # just before it shows again, as show_popover does
        self.window.orderFrontRegardless()
        self.assertEqual(self.list_row_item_ids(), ["w1", "2", "new_window"])
        self.assertEqual([item.item_id for item in self.list_controller.list_selected_items()], ["2"])
        self.owner.answer_conversation_search(["s2"])  # the search asked again as the list appears finds the same
        self.press_return_in_the_search_box()
        self.assertEqual(self.submitted_actions, [ActionRequest("focus_agent_session", {"session_id": "s2"})])

    def test_a_snapshot_asks_the_search_again_only_when_the_conversation_text_changed(self):
        self.list_controller.apply_snapshot(make_sample_snapshot())
        self.type_in_the_search_box("zebra")  # in no title: only the conversation of phase0-tests holds it
        self.owner.answer_conversation_search(["s2"])
        # Only a state and a CPU reading changed: the core's answer still holds.
        self.list_controller.apply_snapshot(make_sample_snapshot_with_entries_changed(0, 0, status="idle", cpu_percent=12))
        self.assertEqual((self.owner.conversation_searches, self.list_row_item_ids()), ([], ["w1", "2", "new_window"]))
        # A title that comes to hold the text is found in the snapshot itself.
        self.list_controller.apply_snapshot(make_sample_snapshot_with_entries_changed(0, 0, tab_title="zebra crossing"))
        self.assertEqual((self.owner.conversation_searches, self.list_row_item_ids()), ([], ["w1", "1", "2", "new_window"]))
        # Conversation text that changed has the core asked again.
        self.list_controller.apply_snapshot(make_sample_snapshot()._replace(conversation_search_text_revision=1))
        self.assertEqual([(text, is_case_sensitive) for text, is_case_sensitive, _ in self.owner.conversation_searches],
                         [("zebra", False)])
        self.owner.answer_conversation_search(["s2", "s3"])
        self.assertEqual(self.list_row_item_ids(), ["w1", "2", "3", "new_window"])

    def test_a_search_whose_rows_show_when_a_held_button_comes_up_selects_its_first_tab_row_then(self):
        self.list_controller.apply_snapshot(make_sample_snapshot())
        self.type_in_the_search_box("plan")
        self.owner.answer_conversation_search([])
        self.assertEqual([item.item_id for item in self.list_controller.list_selected_items()], ["1"])
        # The answer for "zebra" comes while the button is down on a row, which keeps the rows of "plan" in place.
        # The row selected among them goes when the button comes up.
        self.type_in_the_search_box("zebra")
        self.list_controller.row_pressed(0)
        self.owner.answer_conversation_search(["s2"])
        self.assertEqual(self.list_row_item_ids(), ["w1", "1", "new_window"])
        self.list_controller.row_released()
        self.assertEqual(self.list_row_item_ids(), ["w1", "2", "new_window"])
        self.assertEqual([item.item_id for item in self.list_controller.list_selected_items()], ["2"])
        # A drag goes on holding a snapshot after its button's release: no row is selected among the rows it keeps in place.
        outline_view = self.list_controller.outline_view
        outline_view.deselectAll_(None)
        self.list_controller.row_pressed(0)
        self.list_controller.outlineView_draggingSession_willBeginAtPoint_forItems_(
            outline_view, None, (0, 0), [outline_view.itemAtRow_(0)])
        self.list_controller.apply_snapshot(make_sample_snapshot_with_entries_changed(0, 0, status="idle"))
        self.list_controller.row_released()  # mouseDown_ returns
        self.assertEqual(self.list_controller.list_selected_items(), [])
        self.list_controller.outlineView_draggingSession_endedAtPoint_operation_(outline_view, None, (0, 0), 0)

    def test_a_return_that_waits_in_the_search_box_is_dropped_when_the_search_changes_or_its_rows_cant_show(self):
        panel = self.dropdown_panel_controller
        self.list_controller.apply_snapshot(make_sample_snapshot())
        self.type_in_the_search_box("tandem")
        self.owner.answer_conversation_search([])
        # Typing on: the Return was for a text the box no longer holds.
        self.type_in_the_search_box("zebra")
        self.press_return_in_the_search_box()
        self.type_in_the_search_box("zebr")
        self.owner.answer_conversation_search(["s2"])  # for "zebra": left out, and "zebr" asked
        self.owner.answer_conversation_search(["s2"])
        self.assertEqual(self.list_row_item_ids(), ["w1", "2", "new_window"])
        # The "aA" checkbox changes the search too.
        self.type_in_the_search_box("zebra")
        self.press_return_in_the_search_box()
        panel.show_search_is_case_sensitive(True)
        self.owner.answer_conversation_search(["s2"])  # for "zebra" without "aA"
        self.owner.answer_conversation_search(["s2"])
        self.assertEqual((self.list_controller.search_box_search.shown_search.is_case_sensitive, self.list_row_item_ids()),
                         (True, ["w1", "2", "new_window"]))
        # A text nothing holds has no row to show, and the Return doesn't wait for a later text's rows.
        self.type_in_the_search_box("quagga")
        self.press_return_in_the_search_box()
        self.owner.answer_conversation_search([])
        self.assertEqual(self.list_row_item_ids(), ["new_window"])
        self.type_in_the_search_box("plan")
        self.owner.answer_conversation_search([])
        self.assertEqual([item.item_id for item in self.list_controller.list_selected_items()], ["1"])
        # A button held down on a row keeps the earlier text's rows in place until it comes up.
        self.type_in_the_search_box("zebra")
        self.press_return_in_the_search_box()
        self.list_controller.row_pressed(0)
        self.owner.answer_conversation_search(["s2"])
        self.list_controller.row_released()
        self.assertEqual(self.list_row_item_ids(), ["w1", "2", "new_window"])
        # The list closed before the rows came.
        self.type_in_the_search_box("plan")
        self.press_return_in_the_search_box()
        self.window.orderOut_(None)
        self.owner.answer_conversation_search([])
        self.window.orderFrontRegardless()
        self.assertEqual((self.submitted_actions, self.list_controller.return_in_the_search_box_waits_for_its_rows), ([], False))
        # A row double-clicked meanwhile is the one row shown.
        self.type_in_the_search_box("zebra")
        self.press_return_in_the_search_box()
        planning_item = self.list_controller.items_by_key[("tab", "1")]
        self.list_controller.find_clicked_item = lambda: planning_item
        self.list_controller.rowDoubleClicked_(None)
        self.owner.answer_conversation_search(["s2"])
        self.assertEqual(self.submitted_actions, [ActionRequest("focus_agent_session", {"session_id": "s1"})])

    def test_a_tab_that_couldnt_be_shown_leaves_the_list_and_its_search_as_they_are(self):
        panel = self.dropdown_panel_controller
        self.list_controller.apply_snapshot(make_sample_snapshot())
        tabs_shown = []
        self.list_controller.on_tab_focused = lambda: tabs_shown.append(True)  # the dropdown closes itself
        self.type_in_the_search_box("plan")
        self.owner.answer_conversation_search([])
        self.press_return_in_the_search_box()
        self.assertEqual(self.submitted_actions, [ActionRequest("focus_agent_session", {"session_id": "s1"})])
        # Nothing more until the core says the tab is in front, which it never does for a tab it couldn't show.
        self.assertEqual((tabs_shown, panel.search_field.stringValue(), self.list_controller.search_box_search.text), ([], "plan", "plan"))
        self.owner.after_success_of_the_last_action(ActionResult(True))
        self.assertEqual((tabs_shown, panel.search_field.stringValue(), self.list_controller.search_box_search.text), ([True], "", ""))
        # A text typed while the tab was coming to the front stays.
        self.type_in_the_search_box("plan")
        self.owner.answer_conversation_search([])
        self.press_return_in_the_search_box()
        self.type_in_the_search_box("planning")
        self.owner.after_success_of_the_last_action(ActionResult(True))
        self.assertEqual((tabs_shown, panel.search_field.stringValue(), self.list_controller.search_box_search.text),
                         ([True, True], "planning", "planning"))

    def test_hide_idle_hides_idle_tabs_and_windows_left_without_tabs_until_show_idle(self):
        panel = self.dropdown_panel_controller
        self.list_controller.apply_snapshot(make_sample_snapshot())  # the second window's two tabs are idle
        panel.idleTabsButtonClicked_(panel.idle_tabs_button)
        self.assertEqual(self.owner.idle_tabs_are_hidden_choices, [True])  # the owner saves it and shows it in every list
        panel.show_idle_tabs_are_hidden(True)
        outline_view = self.list_controller.outline_view
        self.assertEqual(self.list_row_item_ids(),
                         ["w1", "1", "2", "3", "new_window"])
        self.assertEqual((panel.idle_tabs_button.title(), panel.idle_tabs_button.toolTip()),
                         ("Show Idle", "Show tabs whose agent is idle (⌘-i)"))
        # A new snapshot is filtered too: planning becomes idle.
        self.list_controller.apply_snapshot(make_sample_snapshot_with_entries_changed(0, 0, status="idle"))
        self.assertEqual(outline_view.numberOfRows(), 4)  # w1, phase0-tests (asking), codex-review (done), "+"
        # Only rows are hidden: the window row still stands for its whole window (New tab starts in
        # the selected tab's folder, Close window counts every agent), and the hooks note still
        # counts every agent session's tool.
        self.assertEqual(len(outline_view.itemAtRow_(0).entry.agent_sessions), 3)
        self.assertEqual(len(self.list_controller.list_agent_session_entries()), 5)
        self.select_rows(0, 1)  # the window and phase0-tests: Kill counts and ends the hidden idle planning too
        menu_items_by_title = self.build_right_click_menu_items_by_title(1)
        self.owner.answer_to_confirmation = True
        self.list_controller.menuKillSelectedSessions_(menu_items_by_title["Kill 3 sessions"])
        self.assertEqual((self.submitted_actions[-1].action, self.submitted_actions[-1].target),
                         ("end_agent_processes", {"session_ids": ["s1", "s2", "s3"]}))
        panel.show_idle_tabs_are_hidden(False)
        self.assertEqual(outline_view.numberOfRows(), 8)
        self.assertEqual(panel.idle_tabs_button.title(), "Hide Idle")

    def test_command_i_presses_hide_idle_or_show_idle(self):
        panel = self.dropdown_panel_controller
        self.list_controller.apply_snapshot(make_sample_snapshot())
        for characters, modifier_flags, idle_tabs_are_hidden, expected_choices in (
                ("i", NSEventModifierFlagCommand, False, [True]),
                ("i", NSEventModifierFlagCommand, True, [True, False]),
                ("I", NSEventModifierFlagCommand | NSEventModifierFlagShift, False, [True, False])):  # Shift-Command-I is another key
            panel.show_idle_tabs_are_hidden(idle_tabs_are_hidden)
            self.window.performKeyEquivalent_(make_key_down_event(self.window, characters, 34, modifier_flags))
            self.assertEqual(self.owner.idle_tabs_are_hidden_choices, expected_choices)

    def test_a_hidden_list_rebuilds_its_rows_only_when_it_appears(self):
        outline_view = self.list_controller.outline_view
        self.list_controller.defers_rebuilding_while_hidden = True  # as the app sets it for its three lists
        self.list_controller.apply_snapshot(make_sample_snapshot())  # on screen: rebuilt at once
        self.assertEqual(outline_view.numberOfRows(), 8)
        self.window.orderOut_(None)  # the dropdown closes
        self.list_controller.set_idle_tabs_are_hidden(True)
        self.list_controller.apply_snapshot(make_sample_snapshot(second_window_tabs=3))  # the second window's tabs are idle
        self.assertEqual(outline_view.numberOfRows(), 8)  # nothing rebuilt while hidden
        self.assertTrue(self.list_controller.rows_wait_for_the_list_to_appear)
        self.list_controller.prepare_to_appear()  # just before it shows again, as show_popover does
        self.window.orderFrontRegardless()
        self.assertEqual(self.list_row_item_ids(),
                         ["w1", "1", "2", "3", "new_window"])
        self.assertFalse(self.list_controller.rows_wait_for_the_list_to_appear)

    def test_a_reload_puts_back_each_rows_own_views(self):
        self.list_controller.apply_snapshot(make_sample_snapshot())
        outline_view = self.list_controller.outline_view
        first_tab_row_view = outline_view.viewAtColumn_row_makeIfNecessary_(0, 1, True)
        first_tab_outer_row_view = outline_view.rowViewAtRow_makeIfNecessary_(1, True)
        idle_tab_row_view = outline_view.viewAtColumn_row_makeIfNecessary_(0, 5, True)  # analysis-0
        self.apply_snapshot_and_check_that_the_rows_are_reloaded(make_two_monitor_snapshot(make_sample_snapshot()))  # under monitor rows
        first_tab_row = outline_view.rowForItem_(self.list_controller.items_by_key[("tab", "1")])
        self.assertIs(outline_view.viewAtColumn_row_makeIfNecessary_(0, first_tab_row, True), first_tab_row_view)
        self.assertIs(outline_view.rowViewAtRow_makeIfNecessary_(first_tab_row, True), first_tab_outer_row_view)
        self.list_controller.apply_snapshot(make_sample_snapshot())  # reloaded again, back without monitor rows
        self.list_controller.set_idle_tabs_are_hidden(True)
        self.list_controller.set_idle_tabs_are_hidden(False)  # the hidden rows come back with their views
        self.assertIs(outline_view.viewAtColumn_row_makeIfNecessary_(0, 5, True), idle_tab_row_view)
        self.list_controller.apply_snapshot(make_sample_snapshot(second_window_tabs=1))  # analysis-1 closes
        self.assertNotIn(("tab", "11"), self.list_controller.row_views_by_item_key)
        self.assertNotIn(("tab", "11"), self.list_controller.outer_row_views_by_item_key)

    def test_hide_idle_and_show_idle_change_only_the_rows_that_go_or_come(self):
        self.list_controller.apply_snapshot(make_sample_snapshot())
        outline_view = self.list_controller.outline_view
        second_window_item = outline_view.itemAtRow_(4)  # tandem-repeats, whose two tabs are idle
        with unittest.mock.patch.object(self.list_controller, "reload_and_restore_expansion") as reload:
            self.list_controller.set_idle_tabs_are_hidden(True)
            self.assertEqual(self.list_row_item_ids(),
                             ["w1", "1", "2", "3", "new_window"])
            self.list_controller.set_idle_tabs_are_hidden(False)
            self.assertEqual(self.list_row_item_ids(),
                             ["w1", "1", "2", "3", "w2", "10", "11", "new_window"])
        self.assertFalse(reload.called)
        self.assertIs(outline_view.itemAtRow_(4), second_window_item)  # the same item, still expanded
        self.assertTrue(outline_view.isItemExpanded_(second_window_item))

    def test_hiding_a_tab_that_reorders_the_windows_shows_their_new_order(self):
        # Sorted by memory, w1 ranks first by its idle 1200M tab; once that tab is hidden, w2's 800M tab ranks it first.
        window_one = WindowEntry("w1", "one", False, (0, 0, 800, 600), 2, (
            make_entry("1", "big-idle", "idle")._replace(memory_bytes=1200), make_entry("2", "working", "working")._replace(memory_bytes=300)))
        window_two = WindowEntry("w2", "two", False, (0, 0, 800, 600), 1, (make_entry("3", "asking", "asking")._replace(memory_bytes=800),))
        self.list_controller.set_list_sort_order("memory_usage", False)
        self.list_controller.apply_snapshot(replace_windows_of_the_first_monitor(make_sample_snapshot(), (window_one, window_two)))
        outline_view = self.list_controller.outline_view
        self.assertEqual(outline_view.itemAtRow_(0).item_id, "w1")
        self.list_controller.set_idle_tabs_are_hidden(True)
        self.assertEqual(self.list_row_item_ids(),
                         ["w2", "3", "w1", "2", "new_window"])

    def test_a_sort_moves_the_rows_in_place(self):
        self.list_controller.apply_snapshot(make_sample_snapshot())
        outline_view = self.list_controller.outline_view
        first_tab_row_view = outline_view.viewAtColumn_row_makeIfNecessary_(0, 1, True)  # planning
        with unittest.mock.patch.object(self.list_controller, "reload_and_restore_expansion") as reload:
            self.list_controller.set_list_sort_order("alphabet", False)
            self.assertEqual(self.list_row_item_ids(),
                             ["w1", "3", "2", "1", "w2", "10", "11", "new_window"])
            self.list_controller.set_list_sort_order("manual", False)
            self.assertEqual(self.list_row_item_ids(),
                             ["w1", "1", "2", "3", "w2", "10", "11", "new_window"])
        self.assertFalse(reload.called)
        self.assertIs(outline_view.viewAtColumn_row_makeIfNecessary_(0, 1, True), first_tab_row_view)

    def test_rows_that_change_parent_are_reloaded(self):
        self.list_controller.apply_snapshot(make_sample_snapshot())
        outline_view = self.list_controller.outline_view
        # The windows go under monitor rows.
        self.apply_snapshot_and_check_that_the_rows_are_reloaded(make_two_monitor_snapshot(make_sample_snapshot()))
        self.assertEqual([outline_view.itemAtRow_(row).kind for row in range(outline_view.numberOfRows())],
                         ["monitor", "window", "tab", "tab", "tab", "monitor", "window", "tab", "tab", "new_window"])

    def test_a_collapsed_windows_tabs_are_hidden_and_shown_in_place(self):
        snapshot = make_sample_snapshot()
        window_one, window_two = snapshot.monitors[0].windows
        window_one = window_one._replace(agent_sessions=window_one.agent_sessions + (make_entry("4", "waiting", "idle"),))
        snapshot = replace_windows_of_the_first_monitor(snapshot, (window_one, window_two))
        self.list_controller.apply_snapshot(snapshot)
        outline_view = self.list_controller.outline_view
        first_window_item = outline_view.itemAtRow_(0)
        self.list_controller.toggle_expansion(first_window_item)  # collapsed, with an idle tab among the others
        with unittest.mock.patch.object(self.list_controller, "reload_and_restore_expansion") as reload:
            self.list_controller.set_idle_tabs_are_hidden(True)
            self.list_controller.set_idle_tabs_are_hidden(False)
            self.list_controller.set_idle_tabs_are_hidden(True)
        self.assertFalse(reload.called)
        self.list_controller.toggle_expansion(first_window_item)
        self.assertEqual(self.list_row_item_ids(),
                         ["w1", "1", "2", "3", "new_window"])

    def test_the_moves_that_reorder_rows(self):
        for items_before, items_after in ((["a", "b", "c"], ["c", "a", "b"]), (["a", "b", "c", "d"], ["d", "c", "b", "a"]),
                                          (["a", "b"], ["a", "b"]), (["a", "b", "c"], ["b", "c", "a"])):
            items = list(items_before)
            for from_index, to_index in list_moves_that_reorder(items_before, items_after):
                items.insert(to_index, items.pop(from_index))
            self.assertEqual(items, items_after)
        self.assertEqual(list_moves_that_reorder(["a", "b"], ["a", "b"]), [])

    def test_rows_that_waited_are_rebuilt_when_the_window_shows_again_by_any_way(self):
        outline_view = self.list_controller.outline_view
        self.list_controller.defers_rebuilding_while_hidden = True
        self.list_controller.apply_snapshot(make_sample_snapshot())
        self.window.orderOut_(None)  # minimized, or the app hidden
        self.list_controller.apply_snapshot(make_sample_snapshot(second_window_tabs=3))
        self.assertEqual(outline_view.numberOfRows(), 8)
        self.window.orderFrontRegardless()  # restored from the Dock: no prepare_to_appear
        self.list_controller.windowOcclusionStateChanged_(unittest.mock.Mock(object=lambda: self.window))
        self.assertEqual(outline_view.numberOfRows(), 9)
        self.assertFalse(self.list_controller.rows_wait_for_the_list_to_appear)

    def test_a_working_dot_blinks_without_its_row_being_refilled(self):
        self.list_controller.apply_snapshot(make_sample_snapshot())
        row_view = self.list_controller.outline_view.viewAtColumn_row_makeIfNecessary_(0, 1, True)  # planning, working
        look_before = row_view.look
        self.list_controller.refreshTimerFired_(None)  # the dim half of the blink
        self.assertEqual(row_view.dot_text_field.alphaValue(), DIM_DOT_ALPHA)
        self.assertIs(row_view.look, look_before)  # not refilled, so not laid out again either
        self.list_controller.refreshTimerFired_(None)
        self.assertEqual(row_view.dot_text_field.alphaValue(), 1.0)

    def test_measured_text_widths_are_kept_up_to_a_limit(self):
        font = self.list_controller.regular_font
        width = self.list_controller.measure_text_width("idle", font)
        self.assertEqual(self.list_controller.text_widths_by_text_and_font[("idle", font)], width)
        with unittest.mock.patch("agent_command_tab.user_interface.agent_session_outline_view.LARGEST_NUMBER_OF_KEPT_TEXT_WIDTHS", 2):
            for text in ("1m", "2m", "3m"):
                self.list_controller.measure_text_width(text, font)
        self.assertLessEqual(len(self.list_controller.text_widths_by_text_and_font), 2)
        self.assertEqual(self.list_controller.measure_text_width("idle", font), width)

    def test_the_column_titles_row_is_laid_out_again_only_when_the_widths_change(self):
        self.list_controller.apply_snapshot(make_sample_snapshot())
        titles_row_view = self.list_controller.column_titles_row_view
        titles_row_view.layoutSubtreeIfNeeded()
        self.list_controller.measure_right_columns()  # as the 0.6-second refresh does
        self.assertFalse(titles_row_view.needsLayout())
        self.list_controller.set_shown_columns(["context_tokens"])  # the sample has no context sizes: no column left but the icon's
        self.assertTrue(titles_row_view.needsLayout())

    def test_the_bars_buttons_are_set_again_only_when_the_bars_state_changes(self):
        self.list_controller.apply_snapshot(make_sample_snapshot())
        with unittest.mock.patch("agent_command_tab.user_interface.popover_container.show_symbol_on_button") as show_symbol:
            self.list_controller.apply_snapshot(make_sample_snapshot())  # the same state
            self.assertEqual(show_symbol.call_count, 0)
            # The expand or collapse button's look changes: both bar buttons are set again together.
            self.list_controller.set_all_expanded(False)
            self.assertEqual([call.args[0] for call in show_symbol.call_args_list],
                             [self.dropdown_panel_controller.expand_or_collapse_all_button,
                              self.dropdown_panel_controller.pause_or_resume_selected_rows_button])

    def test_hide_idle_keeps_hidden_rows_collapsed_or_with_their_process_rows_open(self):
        outline_view = self.apply_snapshot_with_processes_on_three_tabs()
        self.click_row_arrow(5)  # opens analysis-0's process rows (idle)
        self.click_row_arrow(4)  # collapses tandem-repeats (both tabs idle)
        self.dropdown_panel_controller.show_idle_tabs_are_hidden(True)
        self.assertEqual(outline_view.numberOfRows(), 5)  # w1, its three tabs, "+"
        self.dropdown_panel_controller.show_idle_tabs_are_hidden(False)
        self.assertEqual(outline_view.numberOfRows(), 6)  # tandem-repeats comes back collapsed
        self.click_row_arrow(4)
        self.assertEqual(outline_view.itemAtRow_(6).kind, "process")  # and analysis-0 open
        self.click_row_arrow(4)  # collapsed again
        self.dropdown_panel_controller.show_idle_tabs_are_hidden(True)
        self.list_controller.set_all_expanded(False)  # Collapse All: agent-command-tab, and tandem-repeats stays collapsed
        self.dropdown_panel_controller.show_idle_tabs_are_hidden(False)
        self.assertEqual(outline_view.numberOfRows(), 3)  # the two collapsed windows and "+"
        self.list_controller.set_all_expanded(True)
        self.dropdown_panel_controller.show_idle_tabs_are_hidden(True)  # tandem-repeats hidden while expanded
        self.list_controller.set_all_expanded(False)  # Collapse All collapses it too
        self.dropdown_panel_controller.show_idle_tabs_are_hidden(False)
        self.assertEqual(outline_view.numberOfRows(), 3)

    def test_collapse_all_collapses_a_window_hidden_since_it_first_appeared(self):
        self.dropdown_panel_controller.show_idle_tabs_are_hidden(True)
        self.list_controller.apply_snapshot(make_sample_snapshot())  # tandem-repeats (two idle tabs) never gets a row
        self.list_controller.set_all_expanded(False)
        self.dropdown_panel_controller.show_idle_tabs_are_hidden(False)
        self.assertEqual(self.list_controller.outline_view.numberOfRows(), 3)  # the two collapsed windows and "+"

    def test_hide_idle_leaves_a_monitor_row_its_pause_item_when_all_of_its_windows_are_hidden(self):
        self.list_controller.apply_snapshot(make_two_monitor_snapshot(make_sample_snapshot()))  # Studio Display: two idle tabs
        self.dropdown_panel_controller.show_idle_tabs_are_hidden(True)
        outline_view = self.list_controller.outline_view
        studio_display_row = next(row for row in range(outline_view.numberOfRows())
                                  if outline_view.itemAtRow_(row).key == ("monitor", "2"))
        self.assertEqual(self.build_right_click_menu_titles(studio_display_row),
                         ["Pause 2 sessions", "---", "Save 2 sessions", "Restore sessions…"])
        # Collapse All collapses that monitor too, though it has no rows below it now.
        self.list_controller.set_all_expanded(False)
        self.dropdown_panel_controller.show_idle_tabs_are_hidden(False)
        self.assertEqual([outline_view.itemAtRow_(row).key[0] for row in range(outline_view.numberOfRows())],
                         ["monitor", "monitor", "new_window"])

    def test_sort_popup_reports_the_choice_to_the_owner(self):
        self.dropdown_panel_controller.list_sort_order_popup.selectItemWithTitle_("Status")
        self.dropdown_panel_controller.listSortOrderChosen_(self.dropdown_panel_controller.list_sort_order_popup)
        self.assertEqual(self.owner.chosen_list_sort_orders, [("activity", False)])  # the pop-up's orders start in their first direction
        self.dropdown_panel_controller.show_list_sort_order("alphabet", False)
        self.assertEqual(self.dropdown_panel_controller.list_sort_order_popup.titleOfSelectedItem(), "Name")
        self.assertEqual(self.list_controller.list_sort_order, "alphabet")

    def build_right_click_menu(self, row):
        """Returns the menu the list builds as if the row were right-clicked."""
        return build_right_click_menu(self.list_controller, self.list_controller.outline_view, row)

    def build_right_click_menu_titles(self, row):
        """Returns the right-click menu's item titles, with "---" for separators."""
        return ["---" if menu_item.isSeparatorItem() else menu_item.title() for menu_item in self.build_right_click_menu(row).itemArray()]

    def build_right_click_menu_items_by_title(self, row):
        return {menu_item.title(): menu_item for menu_item in self.build_right_click_menu(row).itemArray()}

    def test_window_and_tab_right_click_menus(self):
        self.list_controller.apply_snapshot(make_sample_snapshot())
        self.assertEqual(self.build_right_click_menu_titles(0),
                         ["Collapse", "---", "Show window", "Rename", "---", "New tab with Claude Code session…",
                          "New tab with Codex session…", "---", "Pause 3 sessions", "---", "Save 3 sessions",
                          "Restore sessions…", "---", "Close window"])
        self.assertEqual(self.build_right_click_menu_titles(1),
                         ["Show tab", "Rename", "Duplicate this tab", "Move to window", "Move to new window", "---",
                          "Session info", "Copy contact info", "---",
                          "Pause session", "---", "Save session", "Restore sessions…", "---",
                          "/compact", "/clear", "Kill session"])

    def test_session_info_and_copy_contact_info_in_the_tab_menu(self):
        self.list_controller.apply_snapshot(make_sample_snapshot_with_changes_by_tab_id(
            {"10": dict(agent_tool_name="Aider"), "11": dict(status="exited")}))
        menu_items_by_title = self.build_right_click_menu_items_by_title(1)  # "planning", Claude Code
        copy_item = menu_items_by_title["Copy contact info"]
        self.assertTrue(menu_items_by_title["Session info"].isEnabled())
        self.assertEqual((copy_item.isEnabled(), copy_item.toolTip()),
                         (True, "Copies text to paste into another agent session so that it can message this one"))
        self.list_controller.menuShowSessionInfo_(menu_items_by_title["Session info"])
        self.list_controller.menuCopyContactInfo_(copy_item)
        self.assertEqual([entry.session_id for entry in self.owner.session_info_requests], ["s1"])
        self.assertEqual(self.owner.contact_info_requests, ["s1"])
        aider_menu_items_by_title = self.build_right_click_menu_items_by_title(5)
        self.assertTrue(aider_menu_items_by_title["Session info"].isEnabled())
        self.assertEqual((aider_menu_items_by_title["Copy contact info"].isEnabled(),
                          aider_menu_items_by_title["Copy contact info"].toolTip()),
                         (False, "Aider sessions can't take messages from other sessions"))
        exited_menu_items_by_title = self.build_right_click_menu_items_by_title(6)
        for title in ("Session info", "Copy contact info"):
            self.assertEqual((exited_menu_items_by_title[title].isEnabled(), exited_menu_items_by_title[title].toolTip()),
                             (False, "The agent has ended"))

    def test_save_session_in_the_row_menus(self):
        self.list_controller.apply_snapshot(make_sample_snapshot_with_changes_by_tab_id(
            {"10": dict(agent_tool_name="Aider"), "11": dict(status="exited")}))
        saved_session_ids = []
        self.owner.save_agent_sessions = saved_session_ids.append
        tab_menu_items_by_title = self.build_right_click_menu_items_by_title(1)  # "planning", Claude Code
        self.assertTrue(tab_menu_items_by_title["Save session"].isEnabled())
        self.list_controller.menuSaveSessions_(tab_menu_items_by_title["Save session"])
        window_menu_items_by_title = self.build_right_click_menu_items_by_title(0)  # a window row counts its sessions
        self.list_controller.menuSaveSessions_(window_menu_items_by_title["Save 3 sessions"])
        self.assertEqual(saved_session_ids, [["s1"], ["s1", "s2", "s3"]])
        aider_item = self.build_right_click_menu_items_by_title(5)["Save session"]
        self.assertEqual((aider_item.isEnabled(), aider_item.toolTip()),
                         (False, "Only Claude Code, Codex and agy sessions can be saved"))
        exited_item = self.build_right_click_menu_items_by_title(6)["Save session"]
        self.assertEqual((exited_item.isEnabled(), exited_item.toolTip()), (False, "The agent has ended"))
        self.select_rows(3, 5)  # codex-review and the Aider tab
        several_rows_item = self.build_right_click_menu_items_by_title(5)["Save 1 session"]  # counts only those it saves
        self.assertEqual(several_rows_item.toolTip(), "Saves 1 of the 2 sessions: only Claude Code, Codex and agy sessions "
                                                      "whose agent runs can be saved")
        self.list_controller.menuSaveSessions_(several_rows_item)
        self.assertEqual(saved_session_ids[-1], ["s3"])

    def test_restore_sessions_in_the_row_menus(self):
        self.list_controller.apply_snapshot(make_sample_snapshot_with_entries_changed(1, 1, status="exited"))
        restore_item = self.build_right_click_menu_items_by_title(1)["Restore sessions…"]  # "planning"
        self.assertEqual((restore_item.isEnabled(), restore_item.toolTip()), (False, "No sessions are saved"))
        self.owner.has_saved_sessions = True
        restore_item = self.build_right_click_menu_items_by_title(1)["Restore sessions…"]
        self.assertEqual((restore_item.isEnabled(), restore_item.toolTip()),
                         (True, "Opens the Saved sessions window, to continue sessions saved earlier"))
        self.list_controller.menuRestoreSessions_(restore_item)
        self.assertEqual(self.owner.saved_sessions_window_opening_count, 1)
        # An ended agent's tab gets no Pause item, and its Save section still starts with one separator.
        exited_tab_menu_titles = self.build_right_click_menu_titles(6)
        save_position = exited_tab_menu_titles.index("Save session")
        self.assertEqual(exited_tab_menu_titles[save_position - 2:save_position + 3],
                         ["Copy contact info", "---", "Save session", "Restore sessions…", "---"])

    def test_the_bottom_bars_save_button_saves_the_selected_rows_sessions_or_every_session_while_no_row_is_selected(self):
        panel = self.dropdown_panel_controller
        button = panel.save_sessions_button
        self.assertEqual((button.isEnabled(), button.isHidden(), button.toolTip()), (False, False, "There are no sessions to save"))
        self.assertEqual((button.title(), button.image().accessibilityDescription()), ("", "Save sessions"))  # the tray's picture
        self.list_controller.apply_snapshot(make_sample_snapshot_with_changes_by_tab_id(
            {"10": dict(agent_tool_name="Aider"), "11": dict(status="exited")}))
        saved_session_ids = []
        self.owner.save_agent_sessions = saved_session_ids.append
        so_that_they_can_be_restarted = " so that you can restart them later even after they exit"
        only_these_can_be_saved = " sessions: only Claude Code, Codex and agy sessions whose agent runs can be saved"
        for rows, expected_tool_tip, expected_session_ids in (
                # No row is selected: every session in the list that can be saved (not the Aider tab or the ended agent's).
                ((), "Save every session in the list (no row is selected)" + so_that_they_can_be_restarted
                 + ". Saves 3 of the 5" + only_these_can_be_saved, ["s1", "s2", "s3"]),
                ((2,), "Save the sessions of selected rows" + so_that_they_can_be_restarted, ["s2"]),  # phase0-tests alone
                ((0,), "Save the sessions of selected rows" + so_that_they_can_be_restarted, ["s1", "s2", "s3"]),  # a window row: its tabs
                ((3, 5), "Save the sessions of selected rows" + so_that_they_can_be_restarted  # codex-review and the Aider tab
                 + ". Saves 1 of the 2" + only_these_can_be_saved, ["s3"]),
                # Grayed out, saying why, while none of the selected rows' sessions can be saved.
                ((5,), "Only Claude Code, Codex and agy sessions can be saved", None),
                ((6,), "The agent has ended", None)):
            self.select_rows(*rows)
            self.assertEqual((button.isEnabled(), button.isHidden(), button.toolTip()),
                             (expected_session_ids is not None, False, expected_tool_tip), rows)
            if expected_session_ids is not None:
                panel.saveSessionsClicked_(button)
                self.assertEqual(saved_session_ids[-1], expected_session_ids, rows)
        self.assertEqual(len(saved_session_ids), 4)

    def test_the_bottom_bars_restore_button_opens_the_saved_sessions_window_once_something_is_saved(self):
        panel = self.dropdown_panel_controller
        button = panel.restore_sessions_button
        self.list_controller.apply_snapshot(make_sample_snapshot())
        self.assertEqual((button.isEnabled(), button.isHidden(), button.toolTip()), (False, False, "No sessions are saved"))
        self.assertEqual((button.title(), button.image().accessibilityDescription()), ("", "Restore sessions"))  # the tray's picture
        self.owner.has_saved_sessions = True
        panel.show_whether_sessions_are_saved()  # as the app controller does when the saved list changes
        self.assertEqual((button.isEnabled(), button.toolTip()), (True, "Restore saved sessions"))
        panel.restoreSessionsClicked_(button)
        self.assertEqual(self.owner.saved_sessions_window_opening_count, 1)

    def test_the_bottom_bars_settings_button_names_its_keyboard_shortcut_in_its_tooltip(self):
        self.assertEqual(self.dropdown_panel_controller.settings_button.toolTip(), "Open Settings (⌘-,)")

    def test_a_grayed_out_button_keeps_the_arrow_pointer(self):
        button = PointingHandButton.alloc().initWithFrame_(NSMakeRect(0, 0, 24, 22))
        with unittest.mock.patch("agent_command_tab.user_interface.pointing_hand_button.NSCursor") as cursor_class:
            set_the_pointing_hand = cursor_class.pointingHandCursor.return_value.set
            for is_enabled, expected_count in ((True, 2), (False, 2), (True, 4)):
                button.setEnabled_(is_enabled)
                button.mouseEntered_(None)  # the sidebar, whose window has no cursor rects
                button.cursorUpdate_(None)
                self.assertEqual(set_the_pointing_hand.call_count, expected_count, is_enabled)

    def test_pause_and_resume_in_the_row_menus_and_the_bottom_bar(self):
        self.list_controller.apply_snapshot(make_sample_snapshot_with_entries_changed(
            0, 0, status="paused", is_paused_with_the_processes_below_it=True))
        window_menu_titles = self.build_right_click_menu_titles(0)
        self.assertIn("Pause 2 sessions", window_menu_titles)
        self.assertIn("Resume 1 session", window_menu_titles)
        tab_menu_items_by_title = self.build_right_click_menu_items_by_title(1)  # "planning", paused
        self.assertNotIn("Pause session", tab_menu_items_by_title)
        # Resume leaves an agent suspended with Ctrl+Z to fg typed in its tab, which the tooltip says.
        self.assertIn("typing fg in its tab", tab_menu_items_by_title["Resume session"].toolTip())
        self.list_controller.menuResumeSessions_(tab_menu_items_by_title["Resume session"])
        self.assertEqual((self.submitted_actions[-1].action, self.submitted_actions[-1].target),
                         ("resume_agent_sessions", {"session_ids": ["s1"]}))
        # The bottom bar's button acts on the selected rows only.
        panel = self.dropdown_panel_controller
        button = panel.pause_or_resume_selected_rows_button
        self.list_controller.outline_view.deselectAll_(None)
        self.assertTrue(button.isHidden())  # nothing selected
        for rows, action, session_ids in (
                ((2,), "pause_agent_sessions", ["s2"]),  # phase0-tests alone
                ((0,), "pause_agent_sessions", ["s2", "s3"]),  # a window row: its tabs that run, not the paused planning
                ((1,), "resume_agent_sessions", ["s1"]),  # planning, paused
                ((1, 5), "pause_agent_sessions", ["s10"])):  # while one of the selected rows runs, a click pauses
            self.select_rows(*rows)
            self.assertEqual((button.isHidden(), button.toolTip()), (False, {
                "pause_agent_sessions": ("Pause the selected rows' agent processes, their shells and any subprocesses that they "
                                         "started (a STOP signal)"),
                # As the row menus' Resume items say: a CONT signal doesn't bring back an agent suspended with Ctrl+Z.
                "resume_agent_sessions": ("Resume the selected rows' paused agent processes (a CONT signal); those suspended "
                                          "with Ctrl+Z are resumed by typing fg in their tabs instead")}[action]), rows)
            panel.pauseOrResumeSelectedRowsClicked_(button)
            self.assertEqual((self.submitted_actions[-1].action, self.submitted_actions[-1].target),
                             (action, {"session_ids": session_ids}), rows)
        self.list_controller.apply_snapshot(make_sample_snapshot_with_entries_changed(
            status="paused", is_paused_with_the_processes_below_it=True))
        self.select_rows(0, 4)  # both window rows
        panel.pauseOrResumeSelectedRowsClicked_(button)
        self.assertEqual((self.submitted_actions[-1].action, self.submitted_actions[-1].target),
                         ("resume_agent_sessions", {"session_ids": ["s1", "s2", "s3", "s10", "s11"]}))
        self.assertIn("typing fg in their tabs",  # in the menu of the selected rows
                      self.build_right_click_menu_items_by_title(0)["Resume 5 sessions"].toolTip())
        self.apply_snapshot_with_child_processes()
        self.click_row_arrow(1)  # planning's process rows show below it
        self.select_rows(2)  # a process row stands for its tab's agent session, which a pause stops with it
        panel.pauseOrResumeSelectedRowsClicked_(button)
        self.assertEqual((self.submitted_actions[-1].action, self.submitted_actions[-1].target),
                         ("pause_agent_sessions", {"session_ids": ["s1"]}))
        self.list_controller.apply_snapshot(make_two_monitor_snapshot(make_sample_snapshot()))
        self.assertEqual(self.build_right_click_menu_titles(0)[-4:],  # a monitor row's menu
                         ["Pause 3 sessions", "---", "Save 3 sessions", "Restore sessions…"])
        # A monitor that shows no window has nothing to pause or save.
        snapshot = make_sample_snapshot()
        self.list_controller.apply_snapshot(snapshot._replace(monitors=snapshot.monitors + (
            MonitorEntry("2", "Studio Display", (2000, 0, 2560, 1440), (2000, 0, 2560, 1400), ()),)))
        empty_monitor_row = self.list_controller.outline_view.rowForItem_(self.list_controller.items_by_key[("monitor", "2")])
        self.assertEqual(self.build_right_click_menu_titles(empty_monitor_row), ["Restore sessions…"])

    def test_slash_commands_only_at_the_prompt_and_clear_and_kill_ask_first(self):
        self.list_controller.apply_snapshot(make_sample_snapshot())
        menu_items_by_title = self.build_right_click_menu_items_by_title(1)  # "planning" is working
        self.assertFalse(menu_items_by_title["/compact"].isEnabled())
        self.assertTrue(menu_items_by_title["Kill session"].isEnabled())
        menu_items_by_title = self.build_right_click_menu_items_by_title(5)  # analysis-0 is idle
        self.assertTrue(menu_items_by_title["/compact"].isEnabled())
        self.list_controller.menuSendSlashCommand_(menu_items_by_title["/compact"])
        self.assertEqual((self.submitted_actions[-1].action, self.submitted_actions[-1].arguments),
                         ("send_slash_command", {"command": "/compact"}))
        self.list_controller.menuSendSlashCommand_(menu_items_by_title["/clear"])  # the user says No
        self.list_controller.menuKillSession_(menu_items_by_title["Kill session"])
        self.assertEqual(len(self.owner.confirmation_questions), 2)
        self.assertEqual(self.owner.confirmation_action_names, ["clear", "kill"])
        self.assertEqual(len(self.submitted_actions), 1)
        self.owner.answer_to_confirmation = True
        self.list_controller.menuKillSession_(menu_items_by_title["Kill session"])
        self.assertEqual((self.submitted_actions[-1].action, self.submitted_actions[-1].target),
                         ("end_agent_process", {"session_id": "s10"}))
        self.list_controller.apply_snapshot(make_sample_snapshot_with_entries_changed(1, agent_tool_name="Aider", slash_commands=()))
        menu_items_by_title = self.build_right_click_menu_items_by_title(5)  # idle, but its tool takes no commands
        self.assertFalse(menu_items_by_title["/compact"].isEnabled())
        # Worded as in the menu of several rows.
        self.assertEqual(menu_items_by_title["/compact"].toolTip(), "AgentCommandTab can't type /compact into Aider")
        menu_items_by_title = self.build_right_click_menu_items_by_title(1)  # working: its row says "thinking"
        self.assertFalse(menu_items_by_title["/compact"].isEnabled())
        self.assertEqual(menu_items_by_title["/compact"].toolTip(),
                         "Only available when the agent waits at its prompt (it is currently thinking)")
        # A turn that failed on an API error leaves the agent at its prompt, though its row shows "error".
        self.list_controller.apply_snapshot(make_sample_snapshot_with_changes_by_tab_id({"1": {"status": "error"}}))
        self.assertTrue(self.build_right_click_menu_items_by_title(1)["/compact"].isEnabled())

    def select_rows(self, *rows):
        select_rows_of(self.list_controller.outline_view, rows)

    def apply_snapshot_and_check_that_the_rows_are_reloaded(self, snapshot):
        with unittest.mock.patch.object(self.list_controller, "reload_and_restore_expansion",
                                        wraps=self.list_controller.reload_and_restore_expansion) as reload:
            self.list_controller.apply_snapshot(snapshot)
        self.assertTrue(reload.called)

    def list_row_item_ids(self):
        """Returns the item ID of each row of the list, top to bottom."""
        outline_view = self.list_controller.outline_view
        return [outline_view.itemAtRow_(row).item_id for row in range(outline_view.numberOfRows())]

    def test_monitor_rows_and_the_plus_row_cant_be_selected_and_the_selection_is_an_outline(self):
        self.list_controller.apply_snapshot(make_two_monitor_snapshot(make_sample_snapshot()))
        outline_view = self.list_controller.outline_view
        self.assertTrue(outline_view.allowsMultipleSelection())
        every_row = build_index_set(range(outline_view.numberOfRows()))  # a Shift-click range over everything
        allowed_rows = self.list_controller.outlineView_selectionIndexesForProposedSelection_(outline_view, every_row)
        self.assertEqual([outline_view.itemAtRow_(row).kind for row in allowed_rows],
                         ["window", "tab", "tab", "tab", "window", "tab", "tab"])  # no monitor rows, no "+" row
        row_view = self.list_controller.outlineView_rowViewForItem_(outline_view, outline_view.itemAtRow_(1))
        self.assertIsInstance(row_view, RowViewWithSelectionAndFrontTabOutlines)
        self.assertEqual(row_view.interiorBackgroundStyle(), NSBackgroundStyleNormal)  # the text keeps its colors

    def test_reading_a_selection_looks_only_at_its_own_rows(self):
        # Each key press reads the selection, so it mustn't go over every row of a long list.
        self.list_controller.apply_snapshot(make_sample_snapshot())
        outline_view = self.list_controller.outline_view
        self.select_rows(2, 5)
        with unittest.mock.patch.object(outline_view, "itemAtRow_", wraps=outline_view.itemAtRow_) as item_at_row, \
                unittest.mock.patch.object(outline_view, "numberOfRows", wraps=outline_view.numberOfRows) as number_of_rows:
            self.assertEqual([item.item_id for item in self.list_controller.list_selected_items()], ["2", "10"])
            allowed_rows = self.list_controller.outlineView_selectionIndexesForProposedSelection_(outline_view, build_index_set([6, 7]))
        self.assertEqual(list(allowed_rows), [6])  # analysis-1, not the "+" row
        self.assertFalse(number_of_rows.called)  # no pass over every row
        self.assertEqual([call.args for call in item_at_row.call_args_list], [(2,), (5,), (6,), (7,)])  # each item read once

    def test_the_tab_in_front_has_an_outline_of_its_own_unless_it_is_inside_the_selection_outline(self):
        self.list_controller.apply_snapshot(make_sample_snapshot())  # planning (row 1) is in front
        outline_view = self.list_controller.outline_view
        self.assertEqual([row for row in range(outline_view.numberOfRows()) if self.list_controller.row_is_the_tab_in_front(row)], [1])
        content_view = self.window.contentView()

        def read_the_middle_of_the_left_edge_of_each_row(rows):
            """Returns the color (0 to 255) drawn on each row's left outline edge, halfway down."""
            content_view.layoutSubtreeIfNeeded()
            bitmap = render_view_into_bitmap(content_view)
            scale = bitmap.pixelsWide() / content_view.bounds().size.width
            colors = []
            for row in rows:
                outer_row_view = outline_view.rowViewAtRow_makeIfNecessary_(row, True)
                row_rect = content_view.convertRect_fromView_(outer_row_view.bounds(), outer_row_view)
                y_from_the_top = content_view.bounds().size.height - row_rect.origin.y - row_rect.size.height / 2
                colors.append(convert_color_components_to_0_to_255(
                    read_drawn_color_components(bitmap, scale, row_rect.origin.x + 1, y_from_the_top)))
            return colors

        def is_in_the_accent_color(edge, row_without_an_outline, appearance_name):
            """Whether a row's drawn edge is nearer the accent color than the row's own color is: the
            outline may blend with the row where it covers only part of a pixel."""
            accent_color = resolve_color(NSColor.controlAccentColor(), appearance_name)
            return math.dist(edge[:3], accent_color[:3]) < math.dist(edge[:3], row_without_an_outline[:3]) / 2

        for appearance_name, outline_is_darker_than_the_rows in ((NSAppearanceNameAqua, True), (NSAppearanceNameDarkAqua, False)):
            self.window.setAppearance_(NSAppearance.appearanceNamed_(appearance_name))
            outline_view.deselectAll_(None)
            front_tab_edge, other_tab_edge = read_the_middle_of_the_left_edge_of_each_row([1, 2])
            self.assertTrue(is_in_the_accent_color(front_tab_edge, other_tab_edge, appearance_name),
                            (appearance_name, front_tab_edge, other_tab_edge))
            # Selected, it has the selection outline instead, like another selected tab row: dark gray on
            # the light rows, white on the dark ones.
            outline_view.selectRowIndexes_byExtendingSelection_(build_index_set([1, 3]), False)
            front_tab_edge, other_tab_edge, other_selected_tab_edge = read_the_middle_of_the_left_edge_of_each_row([1, 2, 3])
            self.assertEqual(front_tab_edge, other_selected_tab_edge, appearance_name)
            brightness_difference = sum(other_selected_tab_edge[:3]) / 3 - sum(other_tab_edge[:3]) / 3
            self.assertGreater(-brightness_difference if outline_is_darker_than_the_rows else brightness_difference, 60,
                               (appearance_name, other_selected_tab_edge, other_tab_edge))
        # Once another tab is in front, the outline moves to that tab's row.
        outline_view.deselectAll_(None)
        self.list_controller.apply_snapshot(make_sample_snapshot_with_changes_by_tab_id(
            {"1": dict(is_in_front=False), "3": dict(is_in_front=True)}))
        self.assertEqual([row for row in range(outline_view.numberOfRows()) if self.list_controller.row_is_the_tab_in_front(row)], [3])
        planning_edge, codex_review_edge = read_the_middle_of_the_left_edge_of_each_row([1, 3])
        self.assertTrue(is_in_the_accent_color(codex_review_edge, planning_edge, NSAppearanceNameDarkAqua))  # as the loop left it

    def test_adjacent_selected_rows_draw_one_outline_around_them_all(self):
        bounds = NSMakeRect(0, 0, 300, 22)
        # Inset by 1 point; a side shared with a selected neighbor runs on 6.5 points past the row, where its clip cuts it off.
        self.assertEqual(tuple(compute_selection_outline_rect(bounds, True, False, False)), ((1, 1), (298, 20)))
        self.assertEqual(tuple(compute_selection_outline_rect(bounds, True, True, False)), ((1, -5.5), (298, 26.5)))
        self.assertEqual(tuple(compute_selection_outline_rect(bounds, True, False, True)), ((1, 1), (298, 26.5)))
        self.assertEqual(tuple(compute_selection_outline_rect(bounds, False, False, True)), ((1, -5.5), (298, 26.5)))
        self.list_controller.apply_snapshot(make_sample_snapshot())
        self.select_rows(1, 2, 3, 5)
        with unittest.mock.patch("sys.stderr", new_callable=io.StringIO) as printed_errors:
            render_view_into_bitmap(self.window.contentView())  # draws the selected rows' outlines
        self.assertEqual(printed_errors.getvalue(), "")  # an outline that can't be built is printed, not raised
        row_views = [self.list_controller.outline_view.rowViewAtRow_makeIfNecessary_(row, False) for row in (1, 2, 3, 5)]
        # (inside the outline, joined to the row above, joined to the row below)
        self.assertEqual([row_view.find_selection_outline_rows() for row_view in row_views],
                         [(True, False, True), (True, True, True), (True, True, False), (True, False, False)])

    def test_a_selected_tab_rows_outline_takes_in_its_process_rows(self):
        outline_view = self.apply_snapshot_with_child_processes()
        self.click_row_arrow(1)  # rows: w1, planning, its 2 processes, phase0-tests, ...
        self.select_rows(1)
        self.assertEqual([self.list_controller.row_is_inside_the_selection_outline(row) for row in range(-1, 6)],
                         [False, False, True, True, True, False, False])
        content_view = self.window.contentView()
        render_view_into_bitmap(content_view)  # draws the rows and their outline
        last_process_row_view = outline_view.rowViewAtRow_makeIfNecessary_(3, False)
        self.assertFalse(last_process_row_view.isSelected())
        self.assertEqual(last_process_row_view.find_selection_outline_rows(), (True, True, False))  # the outline's bottom edge
        # AppKit puts some rows in a clip view of its own, NSTableRowsClipView: a row there still finds the list.
        clip_view = NSView.alloc().initWithFrame_(outline_view.bounds())
        outline_view.addSubview_(clip_view)
        clip_view.addSubview_(last_process_row_view)
        self.assertEqual(last_process_row_view.find_selection_outline_rows(), (True, True, False))
        outline_view.addSubview_(last_process_row_view)
        clip_view.removeFromSuperview()
        # An exception while a row draws its outline would end the app: it is printed, and the row goes by AppKit's flags.
        self.list_controller.row_is_inside_the_selection_outline = lambda row: 1 / 0
        with unittest.mock.patch("sys.stderr", new_callable=io.StringIO) as printed_errors:
            render_view_into_bitmap(content_view)
        self.assertIn("ZeroDivisionError", printed_errors.getvalue())
        del self.list_controller.row_is_inside_the_selection_outline
        self.select_rows(2)  # a process row selected by itself has an outline of its own
        self.assertEqual([self.list_controller.row_is_inside_the_selection_outline(row) for row in range(1, 4)], [False, True, False])
        # AppKit redraws only the rows whose selection changed and their neighbors.
        redraw_count = []
        self.list_controller.redraw_selection_outlines = lambda: redraw_count.append(1)
        self.select_rows(1)
        self.assertEqual(len(redraw_count), 1)
        self.click_row_arrow(1)  # its process rows go
        self.assertGreater(len(redraw_count), 1)
        # With nothing selected, rows coming and going redraw nothing.
        self.list_controller.outline_view.deselectAll_(None)
        redraw_count.clear()
        self.click_row_arrow(1)  # its process rows come back
        self.assertEqual(redraw_count, [])

    def test_several_selected_rows_share_one_menu_that_acts_on_their_sessions(self):
        self.list_controller.apply_snapshot(make_sample_snapshot())
        self.select_rows(3, 4)  # codex-review (done) and the second window (two idle tabs)
        self.assertEqual(self.build_right_click_menu_titles(4),
                         ["Pause 3 sessions", "---", "Save 3 sessions", "Restore sessions…", "---", "/compact 3 sessions",
                          "/clear 3 sessions", "Kill 3 sessions", "---", "Talk to each other…"])
        self.assertEqual(self.build_right_click_menu_items_by_title(4)["/compact 3 sessions"].toolTip(),
                         COMPACT_IN_THE_MENU_OF_SEVERAL_ROWS_TOOL_TIP)  # all at their prompt: what it does, alone
        self.select_rows(0, 1)  # a window and one of its tabs: each session counts once
        self.assertIn("/compact 3 sessions", self.build_right_click_menu_titles(1))
        self.assertIn("Kill 3 sessions", self.build_right_click_menu_titles(1))  # a window row is selected
        self.select_rows(1, 2)  # working and asking: each gets the command when its turn ends
        menu_items_by_title = self.build_right_click_menu_items_by_title(1)
        self.assertIn("Kill sessions", menu_items_by_title)  # tab rows alone: the selection shows how many
        self.assertTrue(menu_items_by_title["/compact 2 sessions"].isEnabled())
        self.assertEqual(menu_items_by_title["/compact 2 sessions"].toolTip(), COMPACT_IN_THE_MENU_OF_SEVERAL_ROWS_TOOL_TIP
                         + "\nA session in the middle of a turn will get this command when the turn ends")
        self.assertEqual(self.build_right_click_menu_titles(5)[0], "Show tab")  # an unselected row keeps its own menu
        self.select_rows(5)
        self.assertEqual(self.build_right_click_menu_titles(5)[0], "Show tab")  # so does a single selected row

    def test_the_menu_of_several_rows_types_commands_only_into_agents_that_take_them(self):
        # The second window's two tabs take no commands: their tool has none, or they are paused (the core can't
        # type into a suspended agent, so a paused session isn't counted).
        for (fields_changed_in_the_second_window, why_a_selected_agent_is_left_out, tool_tip_when_no_selected_agent_takes_commands,
             resume_is_offered) in (
                ({"agent_tool_name": "Aider", "slash_commands": ()}, "AgentCommandTab can't type /compact into Aider",
                 "AgentCommandTab can't type /compact into Aider", False),
                ({"status": "paused"}, "A paused session is left out: nothing can be typed into it until it is resumed",
                 "Only available after the paused sessions are resumed", True)):
            with self.subTest(fields_changed_in_the_second_window):
                self.list_controller.apply_snapshot(make_sample_snapshot_with_entries_changed(1, **fields_changed_in_the_second_window))
                self.select_rows(3, 5)  # codex-review, which takes commands, and analysis-0
                compact_item = self.build_right_click_menu_items_by_title(3)["/compact 1 session"]
                self.assertEqual(compact_item.toolTip(), COMPACT_IN_THE_MENU_OF_SEVERAL_ROWS_TOOL_TIP
                                 + "\n" + why_a_selected_agent_is_left_out)
                self.list_controller.menuSendSlashCommandToSelectedSessions_(compact_item)
                action_request = self.submitted_actions.pop()  # taken out, so the other case can't pass on this one's command
                self.assertEqual((action_request.action, action_request.target, action_request.arguments),
                                 ("send_slash_command_to_agent_sessions", {"session_ids": ["s3"]}, {"command": "/compact"}))
                self.select_rows(5, 6)  # analysis-0 and analysis-1
                menu_items_by_title = self.build_right_click_menu_items_by_title(5)
                self.assertFalse(menu_items_by_title["/compact"].isEnabled())
                self.assertEqual(menu_items_by_title["/compact"].toolTip(), tool_tip_when_no_selected_agent_takes_commands)
                self.assertEqual("Resume 2 sessions" in menu_items_by_title, resume_is_offered)

    def test_a_tooltip_names_the_slash_command_a_tool_doesnt_take_apart_from_a_paused_session(self):
        # A tool can take one command and not the other, as agy takes /compact and not /clear; analysis-1 is paused too.
        self.list_controller.apply_snapshot(make_sample_snapshot_with_changes_by_tab_id({
            "10": {"agent_tool_name": "agy", "slash_commands": ("/compact",)},
            "11": {"agent_tool_name": "agy", "slash_commands": ("/compact",), "status": "paused"}}))
        menu_items_by_title = self.build_right_click_menu_items_by_title(5)  # analysis-0, idle
        self.assertTrue(menu_items_by_title["/compact"].isEnabled())
        self.assertEqual((menu_items_by_title["/clear"].isEnabled(), menu_items_by_title["/clear"].toolTip()),
                         (False, "AgentCommandTab can't type /clear into agy"))
        self.select_rows(3, 5, 6)  # codex-review, which takes both commands, and the two agy tabs
        menu_items_by_title = self.build_right_click_menu_items_by_title(3)
        # After the line saying what the command does: why the others don't get it.
        self.assertEqual(menu_items_by_title["/clear 1 session"].toolTip().split("\n")[1:],
                         ["AgentCommandTab can't type /clear into agy"])
        self.assertEqual(menu_items_by_title["/compact 2 sessions"].toolTip().split("\n")[1:],
                         ["A paused session is left out: nothing can be typed into it until it is resumed"])

    def test_the_menu_of_several_rows_that_all_exited_has_nothing_to_send_or_kill(self):
        self.list_controller.apply_snapshot(make_sample_snapshot_with_entries_changed(
            1, status="exited", slash_commands=()))
        self.select_rows(5, 6)  # both exited
        menu_items_by_title = self.build_right_click_menu_items_by_title(5)
        for title in ("/compact", "/clear", "Kill sessions"):
            self.assertFalse(menu_items_by_title[title].isEnabled(), title)
            self.assertEqual(menu_items_by_title[title].toolTip(), "The agents have ended", title)

    def test_slash_commands_and_kill_for_several_sessions_ask_first_where_one_session_would(self):
        self.list_controller.apply_snapshot(make_sample_snapshot())
        self.select_rows(1, 5)  # planning (working) and analysis-0 (idle)
        menu_items_by_title = self.build_right_click_menu_items_by_title(5)
        self.list_controller.menuSendSlashCommandToSelectedSessions_(menu_items_by_title["/compact 2 sessions"])
        self.assertEqual((self.submitted_actions[-1].action, self.submitted_actions[-1].target, self.submitted_actions[-1].arguments),
                         ("send_slash_command_to_agent_sessions", {"session_ids": ["s1", "s10"]}, {"command": "/compact"}))
        self.list_controller.menuSendSlashCommandToSelectedSessions_(menu_items_by_title["/clear 2 sessions"])  # the user says No
        self.list_controller.menuKillSelectedSessions_(menu_items_by_title["Kill sessions"])  # No again
        self.assertEqual(len(self.submitted_actions), 1)
        self.assertIn('("planning", "analysis-0")', self.owner.confirmation_questions[0])
        self.assertIn('("planning", "analysis-0")', self.owner.confirmation_questions[1])
        self.assertEqual(self.owner.confirmation_action_names, ["clear", "kill"])
        self.owner.answer_to_confirmation = True
        self.list_controller.menuKillSelectedSessions_(menu_items_by_title["Kill sessions"])
        self.assertEqual((self.submitted_actions[-1].action, self.submitted_actions[-1].target),
                         ("end_agent_processes", {"session_ids": ["s1", "s10"]}))

    def test_talk_to_each_other_sends_the_note_to_the_sessions_whose_tools_can_take_part(self):
        self.list_controller.apply_snapshot(make_sample_snapshot_with_entries_changed(1, 0, agent_tool_name="Aider"))
        self.select_rows(3, 4)  # codex-review (Codex), and analysis-0 (Aider) and analysis-1 (Claude Code)
        menu_items_by_title = self.build_right_click_menu_items_by_title(3)
        self.list_controller.menuTalkToEachOther_(menu_items_by_title["Talk to each other…"])  # Cancel
        self.assertEqual(self.submitted_actions, [])
        talking_entries, left_out_entries = self.owner.talk_to_each_other_requests[-1]
        self.assertEqual(([entry.tab_title for entry in talking_entries], [entry.tab_title for entry in left_out_entries]),
                         (["codex-review", "analysis-1"], ["analysis-0"]))
        self.owner.note_to_send = "Sync up, please."
        self.list_controller.menuTalkToEachOther_(menu_items_by_title["Talk to each other…"])
        self.assertEqual((self.submitted_actions[-1].action, self.submitted_actions[-1].target, self.submitted_actions[-1].arguments),
                         ("ask_agent_sessions_to_talk_to_each_other", {"session_ids": ["s3", "s11"]},
                          {"note": "Sync up, please.", "recipient_session_ids": ["s3", "s11"]}))  # each of them
        self.owner.recipient_titles_to_choose = {"analysis-1"}  # one of them
        self.list_controller.menuTalkToEachOther_(menu_items_by_title["Talk to each other…"])
        self.assertEqual((self.submitted_actions[-1].target, self.submitted_actions[-1].arguments),
                         ({"session_ids": ["s3", "s11"]}, {"note": "Sync up, please.", "recipient_session_ids": ["s11"]}))
        self.select_rows(3, 5)  # one Codex session and the Aider one: nobody to talk to
        self.assertFalse(self.build_right_click_menu_items_by_title(3)["Talk to each other…"].isEnabled())

    def test_the_talk_to_each_other_window_holds_the_default_note_and_names_who_gets_it(self):
        entries = [make_entry("1", "planning", "idle"), make_entry("2", "reviews", "working")]
        alert, note_text_view, recipient_choices = build_talk_to_each_other_alert(
            entries, [make_entry("3", "aider tab", "idle", tool="Aider")])
        self.assertEqual(note_text_view.string(), DEFAULT_NOTE_ASKING_AGENT_SESSIONS_TO_TALK_TO_EACH_OTHER)
        self.assertTrue(alert.informativeText().startswith("Send a message instructing these 2 agents to talk to each other."))
        self.assertIn('Left out, since their tools can\'t take messages from other sessions: "aider tab".', alert.informativeText())
        # "Send the message to:" one of them, or each of them, chosen at first.
        accessory_subviews = alert.accessoryView().subviews()
        labels = [subview.stringValue() for subview in accessory_subviews if isinstance(subview, NSTextField)]
        self.assertIn("Send the message to:", labels)
        # Everything the message adds to the note, so nothing is sent that the window doesn't mention.
        self.assertIn("The message will also include each agent's name, process ID, folder and tab title, and how to message "
                      "each of the others: for a Codex session, a command with its conversation ID and Codex home folder. "
                      "A Codex session has no way to message a Claude Code session, so the message it gets says so instead.", labels)
        radio_buttons = list_recipient_radio_buttons_top_first(recipient_choices)
        self.assertTrue(all(radio_button.window() is alert.window() for radio_button in radio_buttons))
        self.assertEqual([radio_button.title() for radio_button in radio_buttons], ["planning", "reviews", "each of the 2 agents"])
        self.assertEqual([radio_button.state() for radio_button in radio_buttons], [0, 0, 1])
        self.assertEqual(recipient_choices.list_chosen_recipients(), entries)
        radio_buttons[1].performClick_(None)  # as a click does: the others turn off
        self.assertEqual([radio_button.state() for radio_button in radio_buttons], [0, 1, 0])
        self.assertEqual(recipient_choices.list_chosen_recipients(), [entries[1]])
        send_button, cancel_button = alert.buttons()
        self.assertEqual((send_button.title(), cancel_button.title()), ("Send", "Cancel"))
        self.assertEqual(send_button.keyEquivalentModifierMask(), NSEventModifierFlagCommand)  # Return alone starts a new line
        self.assertIs(alert.window().initialFirstResponder(), note_text_view)
        self.assertFalse(note_text_view.isAutomaticDashSubstitutionEnabled())

    def test_the_talk_to_each_other_window_tells_tabs_with_the_same_title_apart(self):
        # Two sessions started in one folder have the same title: with that alone on their buttons, nothing would tell
        # which button sends the message to which session.
        entries = [make_entry("1", "project", "idle"), make_entry("2", "reviews", "working"), make_entry("3", "project", "working")]
        alert, _, recipient_choices = build_talk_to_each_other_alert(entries, [])
        radio_buttons = list_recipient_radio_buttons_top_first(recipient_choices)  # the list's order
        self.assertEqual([radio_button.title() for radio_button in radio_buttons],
                         ["project (1 of 2)", "reviews", "project (2 of 2)", "each of the 3 agents"])
        radio_buttons[2].performClick_(None)
        self.assertEqual(recipient_choices.list_chosen_recipients(), [entries[2]])

    def test_the_talk_to_each_other_window_scrolls_a_long_list_of_recipients_and_gets_no_taller_with_it(self):
        # A row per agent session made the window of a large selection taller than the screen, its buttons out of reach.
        def build_alert_and_radio_buttons(agent_session_count):
            alert, _, recipient_choices = build_talk_to_each_other_alert(
                [make_entry(str(number), f"tab {number}", "idle") for number in range(agent_session_count)], [])
            return alert, list_recipient_radio_buttons_top_first(recipient_choices)
        alert, radio_buttons = build_alert_and_radio_buttons(40)
        self.assertEqual(alert.window().frame().size.height, build_alert_and_radio_buttons(80)[0].window().frame().size.height)
        recipients_view = radio_buttons[0].superview()
        scroll_view = recipients_view.enclosingScrollView()
        self.assertLess(scroll_view.frame().size.height, recipients_view.frame().size.height)
        # The end of the list shows at first: the button chosen at first, "each of the 40 agents". Only how far up the
        # buttons are is compared, so this holds whatever width a scroll bar beside the list leaves it where macOS is
        # set to always show scroll bars.
        visible_rect = scroll_view.documentVisibleRect()
        self.assertLessEqual(NSMinY(visible_rect), NSMinY(radio_buttons[-1].frame()))
        self.assertLessEqual(NSMaxY(radio_buttons[-1].frame()), NSMaxY(visible_rect))
        self.assertGreaterEqual(NSMinY(radio_buttons[0].frame()), NSMaxY(visible_rect))  # the first one is out of sight, above
        # Still one group, of which only one is on: AppKit groups the radio buttons of one view.
        self.assertTrue(all(radio_button.superview() is recipients_view for radio_button in radio_buttons))
        radio_buttons[0].performClick_(None)
        self.assertEqual([radio_button.state() for radio_button in radio_buttons], [1] + [0] * 40)
        # A short list shows whole, and the window grows with it as before.
        radio_buttons_of_few = build_alert_and_radio_buttons(3)[1]
        scroll_view_of_few = radio_buttons_of_few[0].enclosingScrollView()
        self.assertEqual(scroll_view_of_few.frame().size.height, radio_buttons_of_few[0].superview().frame().size.height)
        self.assertLess(build_alert_and_radio_buttons(2)[0].window().frame().size.height,
                        build_alert_and_radio_buttons(3)[0].window().frame().size.height)

    def test_every_alert_the_app_runs_shows_the_apps_icon(self):  # rather than Python's rocket
        stand_in_app_controller = unittest.mock.Mock()
        stand_in_app_controller.sidebar_controller.window.level.return_value = 0
        alert = unittest.mock.Mock()
        AppController.run_alert_in_front(stand_in_app_controller, alert)
        (icon,), _ = alert.setIcon_.call_args
        self.assertEqual(tuple(icon.size()), (1024, 1024))  # app_icon.build_app_icon's
        self.application_that_is_never_made_active.activateIgnoringOtherApps_.assert_called_once_with(True)  # the alert takes the keyboard
        alert.runModal.assert_called_once()

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

    def type_new_name_during_rename(self, row, new_name):
        """Types new_name into the name field of the row being renamed; returns the row's view."""
        row_view = self.list_controller.outline_view.viewAtColumn_row_makeIfNecessary_(0, row, False)
        row_view.name_text_field.setStringValue_(new_name)
        return row_view

    def end_rename(self, row_view):
        """Ends the row's rename as AppKit does when its name field's editing ends."""
        self.list_controller.controlTextDidEndEditing_(unittest.mock.Mock(object=lambda: row_view.name_text_field))

    def begin_rename_of_the_first_sample_tab(self):
        """Shows the sample snapshot in the laid out window and begins renaming its first tab, in row 1; returns that tab's item."""
        self.list_controller.apply_snapshot(make_sample_snapshot())
        self.window.contentView().layoutSubtreeIfNeeded()
        tab_item = self.list_controller.outline_view.itemAtRow_(1)
        self.list_controller.begin_rename(tab_item)
        return tab_item

    def test_a_confirmed_rename_shows_the_new_name_at_once(self):
        tab_item = self.begin_rename_of_the_first_sample_tab()
        # The dropdown takes typing only while the app is the active one.
        self.application_that_is_never_made_active.activateIgnoringOtherApps_.assert_called_once_with(True)
        row_view = self.type_new_name_during_rename(1, "new name")
        self.end_rename(row_view)
        self.assertEqual(row_view.name_text_field.stringValue(), "new name")
        self.assertEqual(tab_item.entry.tab_title, "new name")
        self.assertEqual(self.submitted_actions[-1].action, "rename_tab")

    def test_a_refused_rename_puts_the_old_name_back(self):
        old_title = self.begin_rename_of_the_first_sample_tab().entry.tab_title
        self.end_rename(self.type_new_name_during_rename(1, "new name"))
        self.owner.after_failure_of_the_last_action()  # the terminal refused it
        self.assertEqual(self.list_controller.outline_view.itemAtRow_(1).entry.tab_title, old_title)

    def test_an_emptied_tmux_window_name_is_not_sent(self):  # a tmux session has no default name to go back to
        snapshot = make_sample_snapshot()
        window_one, window_two = snapshot.monitors[0].windows
        self.list_controller.apply_snapshot(replace_windows_of_the_first_monitor(
            snapshot, (window_one, window_two._replace(terminal_name="tmux"))))
        self.window.contentView().layoutSubtreeIfNeeded()
        window_row = 4
        window_item = self.list_controller.outline_view.itemAtRow_(window_row)
        self.list_controller.begin_rename(window_item)
        row_view = self.type_new_name_during_rename(window_row, "")
        actions_before = len(self.submitted_actions)
        self.end_rename(row_view)
        self.assertEqual(len(self.submitted_actions), actions_before)
        self.assertEqual(window_item.entry.title, "tandem-repeats")

    def test_a_renamed_window_shows_no_number_until_the_core_numbers_it_again(self):
        # "proj (2)" renamed "other" showed "other (2)".
        snapshot = make_sample_snapshot()
        window_one, window_two = snapshot.monitors[0].windows
        self.list_controller.apply_snapshot(replace_windows_of_the_first_monitor(
            snapshot, (window_one, window_two._replace(number_among_windows_with_the_same_title=2))))
        self.window.contentView().layoutSubtreeIfNeeded()
        window_row = 4
        window_item = self.list_controller.outline_view.itemAtRow_(window_row)
        self.list_controller.begin_rename(window_item)
        self.assertEqual(self.list_controller.title_before_rename, "tandem-repeats")  # the title, without its number
        self.end_rename(self.type_new_name_during_rename(window_row, "other"))
        self.assertEqual(build_window_title_shown_in_the_list(window_item.entry), "other")
        self.assertEqual(self.submitted_actions[-1].action, "rename_window")

    def test_a_rename_ended_by_a_click_on_another_row_keeps_the_new_name_after_the_release(self):
        self.begin_rename_of_the_first_sample_tab()
        # Arrives during the rename, with another tab's change: held.
        self.list_controller.apply_snapshot(make_sample_snapshot_with_entries_changed(0, 1, status="idle"))
        row_view = self.type_new_name_during_rename(1, "new name")
        self.list_controller.row_pressed(3)  # the click on another row that ends the rename
        self.end_rename(row_view)
        self.list_controller.row_released()
        self.assertEqual(self.list_controller.items_by_key[("tab", "1")].entry.tab_title, "new name")
        self.assertEqual(self.list_controller.items_by_key[("tab", "2")].entry.status, "idle")  # the held snapshot shows too

    def check_a_refused_rename_ended_by_a_click_on_another_row_puts_the_old_name_back(self, refused_before_the_release):
        # The held snapshot got the new name.
        old_title = self.begin_rename_of_the_first_sample_tab().entry.tab_title
        self.list_controller.apply_snapshot(make_sample_snapshot())  # arrives during the rename: held
        row_view = self.type_new_name_during_rename(1, "new name")
        self.list_controller.row_pressed(3)  # the click on another row that ends the rename
        self.end_rename(row_view)
        if refused_before_the_release:
            self.owner.after_failure_of_the_last_action()
        self.list_controller.row_released()
        if not refused_before_the_release:
            self.owner.after_failure_of_the_last_action()
        self.assertEqual(self.list_controller.items_by_key[("tab", "1")].entry.tab_title, old_title)

    def test_a_rename_refused_after_the_release_of_the_click_that_ended_it_puts_the_old_name_back(self):
        self.check_a_refused_rename_ended_by_a_click_on_another_row_puts_the_old_name_back(False)

    def test_a_rename_refused_before_the_release_of_the_click_that_ended_it_puts_the_old_name_back(self):
        self.check_a_refused_rename_ended_by_a_click_on_another_row_puts_the_old_name_back(True)

    def test_holding_a_tab_row_peeks_and_releasing_ends_it_but_a_click_does_not(self):
        self.list_controller.apply_snapshot(make_sample_snapshot())
        self.list_controller.row_pressed(1)
        self.list_controller.row_released()  # a click: released before the hold time
        self.assertEqual(self.owner.peeks, [])
        self.list_controller.row_pressed(1)
        self.list_controller.peek_timer.fire()  # held long enough
        self.list_controller.row_released()
        self.assertEqual(self.owner.peeks, [("start", "s1"), ("end",)])
        self.list_controller.row_pressed(0)  # a window row doesn't peek
        self.assertIsNone(self.list_controller.peek_timer)
        self.list_controller.row_released()

    def test_a_row_drag_that_starts_as_a_hold_ends_the_peek_and_tells_the_owner_when_it_begins_and_ends(self):
        # The owner keeps the dropdown open while a row is dragged in it.
        self.list_controller.apply_snapshot(make_sample_snapshot())
        outline_view = self.list_controller.outline_view
        drag_states_seen_by_the_owner = []
        self.owner.row_drag_began_or_ended = lambda: drag_states_seen_by_the_owner.append(self.list_controller.drag_in_progress)
        self.list_controller.row_pressed(1)
        self.list_controller.peek_timer.fire()  # held long enough to peek
        # The drag begins inside the outline view's mouseDown_, before the release, and ends the peek itself.
        self.list_controller.outlineView_draggingSession_willBeginAtPoint_forItems_(
            outline_view, None, (0, 0), [outline_view.itemAtRow_(1)])
        self.assertFalse(self.list_controller.peek_in_progress)
        self.assertEqual(self.owner.peeks, [("start", "s1"), ("end",)])
        self.list_controller.row_released()  # mouseDown_ returns
        self.list_controller.outlineView_draggingSession_endedAtPoint_operation_(outline_view, None, (0, 0), 0)
        self.assertEqual(self.owner.peeks, [("start", "s1"), ("end",)])  # ended once
        self.assertEqual(drag_states_seen_by_the_owner, [True, False])

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
        # Status order: the held row, the asking phase0-tests, turns idle, which would move it to the end.
        self.list_controller.set_list_sort_order("activity", False)
        self.list_controller.apply_snapshot(make_sample_snapshot())
        outline_view = self.list_controller.outline_view
        held_item = outline_view.itemAtRow_(1)
        self.assertEqual(held_item.item_id, "2")
        self.list_controller.row_pressed(1)
        self.list_controller.apply_snapshot(make_sample_snapshot_with_entries_changed(0, 1, status="idle"))
        self.assertIs(outline_view.itemAtRow_(1), held_item)  # held back while the button is down
        self.list_controller.row_released()
        self.assertIsNot(outline_view.itemAtRow_(1), held_item)  # applied on release

    def test_a_narrow_sidebar_drops_the_context_then_the_icon_column_to_keep_room_for_names(self):
        snapshot = make_sample_snapshot_with_entries_changed(0, context_tokens=40_546)
        self.list_controller.width_is_set_by_the_user = True
        self.window.setContentSize_((560, 260))
        self.list_controller.apply_snapshot(snapshot)
        self.assertGreater(self.list_controller.column_widths["context_tokens"], 0)
        self.assertGreater(self.list_controller.column_widths["agent_tool_icon"], 0)
        self.window.setContentSize_((200, 260))  # the narrowest sidebar: the columns follow at once
        self.window.contentView().layoutSubtreeIfNeeded()
        self.assertEqual((self.list_controller.column_widths["context_tokens"], self.list_controller.column_widths["agent_tool_icon"]),
                         (0, 0))
        # The rest go too, the state last, so the names stay readable.
        self.assertEqual(sum(self.list_controller.column_widths.values()), 0)
        self.assertGreaterEqual(self.list_controller.measure_room_left_for_tab_names(), SMALLEST_TAB_NAME_WIDTH)
        self.window.setContentSize_((300, 260))
        self.window.contentView().layoutSubtreeIfNeeded()
        self.assertGreater(self.list_controller.column_widths["state"], 0)  # kept while there is room for it
        # The context goes before the icon column.
        self.assertEqual(self.list_controller.column_widths["context_tokens"], 0)
        self.assertGreater(self.list_controller.column_widths["agent_tool_icon"], 0)

    def test_a_column_keeps_spare_room_so_a_digit_more_or_less_doesnt_move_it(self):
        character = 7.0
        for widest_text_width, room_before, expected_room in (
                (70, 0, 77),  # 10 characters: one spare
                (77, 77, 77),  # a digit more fills the spare room
                (70, 77, 77), (56, 77, 77),  # and two fewer than when it was set keep it
                (49, 77, 56),  # three fewer: it shrinks, keeping one spare again
                (84, 77, 91),  # wider than the room: it grows at once
                (0, 77, 0)):  # no text: the column goes
            self.assertEqual(choose_room_for_a_columns_texts(widest_text_width, room_before, character), expected_room,
                             (widest_text_width, room_before))

        def show_context_sizes(context_tokens, context_fill_percent=42):
            self.list_controller.apply_snapshot(make_sample_snapshot_with_entries_changed(
                0, context_tokens=context_tokens, context_fill_percent=context_fill_percent))
            return self.list_controller.column_widths["context_tokens"]
        self.list_controller.width_is_set_by_the_user = True  # a sidebar or the list window
        self.window.setContentSize_((700, 260))
        width = show_context_sizes(84_000)  # "84k (42%)"
        self.assertEqual([show_context_sizes(context_tokens) for context_tokens in (184_000, 84_000)], [width, width])
        self.assertGreater(show_context_sizes(1_200_000, 120), width)  # "1.2M (120%)", two characters more: wider at once

    def test_duplicate_is_greyed_out_for_a_tool_without_a_launch_command(self):
        self.list_controller.apply_snapshot(make_sample_snapshot_with_entries_changed(1, 0, agent_tool_name="aider"))
        self.assertFalse(self.build_right_click_menu_items_by_title(5)["Duplicate this tab"].isEnabled())  # the "aider" tab

    def test_closing_the_dropdown_cancels_an_open_rename_so_the_list_keeps_updating(self):
        self.begin_rename_of_the_first_sample_tab()
        self.assertTrue(self.list_controller.rename_in_progress)
        # A new name typed, so a rename submitted instead of cancelled would show.
        type_into_the_open_rename_editor(self.list_controller.outline_view, 1, "typed but cancelled")
        # The dropdown closes (through the app controller's own handler).
        app_controller = AppController.alloc().init()
        app_controller.dropdown_list_controller = self.list_controller
        app_controller.status_item_controller = unittest.mock.Mock()
        app_controller.place_the_settings_and_saved_sessions_windows_above_the_lists = lambda dropdown_is_open: None
        app_controller.popoverWillClose_(None)
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
            del self.submitted_actions[:]  # so each way of collapsing must submit its own rename
            self.begin_rename_of_the_first_sample_tab()
            type_into_the_open_rename_editor(self.list_controller.outline_view, 1, f"renamed {collapse_number}")
            collapse()  # AppKit raised an exception here while the name was being edited
            self.assertFalse(self.list_controller.rename_in_progress)
            self.assertIn(("window", "w1"), self.list_controller.collapsed_item_keys)
            self.assertEqual([(action.action, action.arguments) for action in self.submitted_actions],
                             [("rename_tab", {"new_title": f"renamed {collapse_number}"})])

    def test_opening_a_tab_row_during_a_rename_submits_the_rename_first(self):
        self.apply_snapshot_with_processes_on_three_tabs()
        self.window.contentView().layoutSubtreeIfNeeded()
        tab_item = self.list_controller.outline_view.itemAtRow_(1)
        self.list_controller.begin_rename(tab_item)
        type_into_the_open_rename_editor(self.list_controller.outline_view, 1, "renamed")
        self.list_controller.set_tab_rows_open([tab_item], True)
        self.assertFalse(self.list_controller.rename_in_progress)
        self.assertEqual((self.submitted_actions[-1].action, self.submitted_actions[-1].arguments),
                         ("rename_tab", {"new_title": "renamed"}))
        # The rows rebuilt to open it keep the new name until the core's next snapshot confirms it.
        self.assertEqual(self.list_controller.items_by_key[("tab", "1")].entry.tab_title, "renamed")
        # Refilled now that its name isn't being edited: the arrow points down.
        self.assertEqual(self.lay_out_row_view(1).disclosure_button.image().accessibilityDescription(), "Close")

    def test_every_right_click_menu_item_says_what_it_does_on_mouse_over(self):
        outline_view = self.list_controller.outline_view
        self.list_controller.apply_snapshot(make_sample_snapshot())
        self.select_rows(3, 4)  # the menu of several rows
        self.assertEqual(list_menu_items_without_a_tool_tip(self.build_right_click_menu(4)), [])
        outline_view.deselectAll_(None)
        # Monitor rows and Move to monitor, and a process row under the first tab.
        self.list_controller.apply_snapshot(make_two_monitor_snapshot(make_sample_snapshot_with_entries_changed(
            0, 0, child_processes=(ChildProcessEntry(201, "caffeinate -i -t 300", 900.0, False, None, None),))))
        self.list_controller.set_tab_rows_open([self.list_controller.items_by_key[("tab", "1")]], True)
        kinds_of_rows_clicked = set()
        for row in [-1, *range(outline_view.numberOfRows())]:  # -1: empty space
            kinds_of_rows_clicked.add(outline_view.itemAtRow_(row).kind if row >= 0 else "empty space")
            self.assertEqual(list_menu_items_without_a_tool_tip(self.build_right_click_menu(row)), [], row)
        self.assertLessEqual({"empty space", "monitor", "window", "tab", "process"}, kinds_of_rows_clicked)

    def test_expand_or_collapse_from_the_right_click_menu(self):
        self.list_controller.apply_snapshot(make_sample_snapshot())
        self.collapse_from_the_right_click_menu(0)
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

    def start_a_new_agent_session_from_the_menu_of_the_only_window(self, window, agent_tool_name):
        """Lists only this window and chooses New Agent Session with agent_tool_name in its row's menu."""
        self.list_controller.apply_snapshot(replace_windows_of_the_first_monitor(make_sample_snapshot(), (window,)))
        self.list_controller.menu_target_item = self.list_controller.outline_view.itemAtRow_(0)
        self.list_controller.menuNewAgentSessionInWindow_(unittest.mock.Mock(representedObject=lambda: agent_tool_name))

    def test_new_agent_session_uses_the_folder_of_the_windows_selected_tab(self):
        # A background window (nothing in front) whose selected tab is its second agent session.
        self.start_a_new_agent_session_from_the_menu_of_the_only_window(WindowEntry("w3", "two projects", False, (0, 0, 800, 600), 2, (
            make_entry("31", "photos", "idle")._replace(working_folder="/code/photos"),
            make_entry("32", "other", "idle")._replace(working_folder="/tmp/other-project"),
        ), selected_tab_id="32"), "Claude Code")
        self.assertEqual(self.owner.started_agent_sessions, [("w3", "/tmp/other-project", "Claude Code")])

    def test_a_new_agent_session_in_a_window_without_a_selected_tab_uses_its_first_tabs_folder(self):
        # Not the folder of the tab in front or of the last one.
        self.start_a_new_agent_session_from_the_menu_of_the_only_window(WindowEntry("w3", "three projects", False, (0, 0, 800, 600), 3, (
            make_entry("31", "photos", "idle")._replace(working_folder="/code/photos"),
            make_entry("32", "other", "idle", is_in_front=True)._replace(working_folder="/tmp/other-project"),
            make_entry("33", "notes", "idle")._replace(working_folder="/tmp/notes"),
        )), "Codex")
        self.assertEqual(self.owner.started_agent_sessions, [("w3", "/code/photos", "Codex")])

    def test_the_new_tab_and_new_window_tooltips_name_the_folders_used_when_the_first_choice_has_none(self):
        self.list_controller.apply_snapshot(make_sample_snapshot())
        self.assertEqual(self.build_right_click_menu_items_by_title(0)["New tab with Codex session…"].toolTip(),
                         "Opens Codex in a new tab of this window, in the folder of the agent in its current tab, "
                         "or of its first agent when the current tab runs none")
        self.assertEqual(self.build_right_click_menu_items_by_title(7)["New Codex window"].toolTip(),  # the "+" row's menu
                         "Opens Codex in a new window, in the folder of the tab in front, else of the tab used most recently "
                         "within a day, else your home folder")

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
            self.list_controller.apply_snapshot(make_sample_snapshot_with_entries_changed(**changed_fields))
            self.dropdown_panel_controller.fit_size_to_rows()
            return self.dropdown_panel_controller.preferredContentSize()[0]

        # "99k (9%)", "9M", "0%", "done" and "5s" grow to "100k (100%)", "1024M", "100%", "working" and "23h 59m". The
        # time since the last message, "5s" too, is one no row shows once every agent works: its column keeps its room.
        self.assertEqual(
            measure_dropdown_width(context_tokens=99_000, context_fill_percent=9, memory_bytes=9 * 1024 ** 2, cpu_percent=0,
                                   started_at=now - 5, last_message_at=now - 5, status="done"),
            measure_dropdown_width(context_tokens=100_000, context_fill_percent=100, memory_bytes=1024 ** 3 - 1, cpu_percent=100,
                                   started_at=now - 86399, last_message_at=now - 86399, status="working"))
        self.assertGreater(self.list_controller.column_widths["time_since_last_message"], 0)

    def test_dropdown_fits_its_size_to_all_rows(self):
        self.list_controller.apply_snapshot(make_sample_snapshot_with_entries_changed(
            0, 0, tab_title="a-tab-name-long-enough-to-widen-the-dropdown-past-its-default-width", background_task_count=3))
        self.dropdown_panel_controller.fit_size_to_rows()
        width, height = self.dropdown_panel_controller.preferredContentSize()
        self.assertGreater(width, self.list_controller.measure_widest_row_width())
        self.assertGreaterEqual(height, self.list_controller.measure_fully_expanded_list_height() + TOP_BAR_HEIGHT
                                + BOTTOM_BAR_HEIGHT)
        # Laid out at that size, the long name and the background task lights after it (the row's details) show in
        # full. A row short of room cuts its name short before its details: too narrow a dropdown fails the name's check.
        self.window.setContentSize_((width, height))
        self.window.contentView().layoutSubtreeIfNeeded()
        row_view = self.lay_out_row_view(1)
        name_text_field = row_view.name_text_field
        self.assertGreaterEqual(name_text_field.frame().size.width, name_text_field.fittingSize().width)
        details_text_field = row_view.details_text_field
        self.assertEqual(details_text_field.stringValue(), "•••")
        self.assertGreaterEqual(details_text_field.frame().size.width, details_text_field.fittingSize().width)

    def test_the_dropdown_opens_sized_for_the_screen_its_menu_bar_icon_is_on(self):
        # Not for the first screen, which may be larger: before the dropdown opens, its panel is in no window on the
        # icon's screen, so only the icon tells which screen's size limits it.
        self.list_controller.apply_snapshot(make_sample_snapshot_with_entries_changed(
            0, 0, tab_title="a-tab-name-long-enough-to-widen-the-dropdown-past-its-default-width"))

        def make_screen(width, height):
            return unittest.mock.Mock(visibleFrame=lambda: NSMakeRect(0, 0, width, height))
        status_item_controller = MenuBarStatusItemController.alloc().init()  # no status item of its own
        status_item_controller.owner = unittest.mock.Mock()
        status_item_controller.popover = unittest.mock.Mock(contentViewController=lambda: self.dropdown_panel_controller)
        status_item_controller.status_item = unittest.mock.Mock()
        status_item_controller.status_item.button().window().screen.return_value = make_screen(400, 300)
        with unittest.mock.patch("agent_command_tab.user_interface.menu_bar_status_item.NSApp"), \
                unittest.mock.patch("agent_command_tab.user_interface.popover_container.NSScreen",
                                    unittest.mock.Mock(screens=lambda: [make_screen(3000, 2000)])):
            status_item_controller.show_popover()
        width, height = self.dropdown_panel_controller.preferredContentSize()
        self.assertEqual(width, 360)  # the long name cut short at 90% of the icon's screen
        self.assertLess(height, 300)
        status_item_controller.popover.showRelativeToRect_ofView_preferredEdge_.assert_called_once()

    def apply_snapshot_with_processes_on_three_tabs(self):
        """Applies the sample snapshot with a process on both tabs of the second window and on the first tab of the first."""
        self.list_controller.apply_snapshot(make_sample_snapshot_with_changes_by_tab_id({
            tab_id: dict(child_processes=(ChildProcessEntry(300 + int(tab_id), "sleep 600", 900.0, False, None, None),))
            for tab_id in ("1", "10", "11")}))
        return self.list_controller.outline_view

    def test_the_columns_and_their_titles_end_at_the_right_margin(self):
        self.apply_snapshot_with_processes_on_three_tabs()
        self.window.contentView().layoutSubtreeIfNeeded()
        tab_row_view = self.lay_out_row_view(1)
        last_column_frame = tab_row_view.column_views["state"].frame()
        self.assertEqual(last_column_frame.origin.x + last_column_frame.size.width, tab_row_view.frame().size.width - ROW_RIGHT_MARGIN)
        titles_row_view = self.list_controller.column_titles_row_view
        titles_row_view.layoutSubtreeIfNeeded()
        last_title_frame = titles_row_view.column_title_text_fields["state"].frame()
        self.assertEqual(last_title_frame.origin.x + last_title_frame.size.width, last_column_frame.origin.x + last_column_frame.size.width)

    def click_row_arrow(self, row):
        """Clicks the row's arrow button itself, so the action the button is wired to runs and finds the row from the button."""
        self.list_controller.outline_view.viewAtColumn_row_makeIfNecessary_(0, row, True).disclosure_button.performClick_(None)

    def lay_out_row_view(self, row):
        row_view = self.list_controller.outline_view.viewAtColumn_row_makeIfNecessary_(0, row, True)
        row_view.layoutSubtreeIfNeeded()
        return row_view

    def test_the_arrow_on_a_tab_shows_only_while_it_has_process_rows_to_open(self):
        outline_view = self.apply_snapshot_with_processes_on_three_tabs()
        self.window.contentView().layoutSubtreeIfNeeded()
        arrow = self.lay_out_row_view(1).disclosure_button
        self.assertFalse(arrow.isHidden())
        self.assertEqual(arrow.image().accessibilityDescription(), "Open")
        self.assertTrue(self.lay_out_row_view(2).disclosure_button.isHidden())  # no processes: no arrow, names still line up
        self.assertEqual(self.lay_out_row_view(1).name_text_field.frame().origin.x,
                         self.lay_out_row_view(2).name_text_field.frame().origin.x)
        self.click_row_arrow(1)
        self.assertEqual(outline_view.numberOfRows(), 9)
        self.assertEqual(self.submitted_actions, [])  # opening doesn't show the tab
        self.assertEqual(self.list_controller.measure_fully_expanded_list_height(), 9 * ROW_HEIGHT)
        self.assertEqual(self.lay_out_row_view(1).disclosure_button.image().accessibilityDescription(), "Close")
        self.click_row_arrow(1)
        self.assertEqual(outline_view.numberOfRows(), 8)

    def test_a_single_click_on_a_row_opens_and_closes_nothing(self):
        outline_view = self.apply_snapshot_with_processes_on_three_tabs()
        for row in (0, 1):
            item = outline_view.itemAtRow_(row)
            self.list_controller.find_clicked_item = lambda: item
            self.list_controller.rowClicked_(None)
        self.assertEqual(outline_view.numberOfRows(), 8)

    def test_the_arrow_on_a_window_collapses_just_that_window(self):
        outline_view = self.apply_snapshot_with_processes_on_three_tabs()
        self.click_row_arrow(5)  # a tab in the second window
        self.click_row_arrow(4)  # the second window
        self.assertEqual((outline_view.numberOfRows(), self.list_controller.collapsed_item_keys), (6, {("window", "w2")}))
        self.click_row_arrow(4)
        self.assertEqual(outline_view.itemAtRow_(6).kind, "process")  # the tab comes back open, as it was

    def test_expand_all_and_collapse_all_leave_the_tab_rows_as_they_are(self):
        outline_view = self.apply_snapshot_with_processes_on_three_tabs()
        self.click_row_arrow(1)  # opens one tab row
        self.list_controller.set_all_expanded(False)
        self.assertEqual(outline_view.numberOfRows(), 3)  # 2 windows and the "+" row
        self.list_controller.set_all_expanded(True)
        self.assertEqual([outline_view.itemAtRow_(row).kind for row in range(outline_view.numberOfRows())],
                         ["window", "tab", "process", "tab", "tab", "window", "tab", "tab", "new_window"])

    def test_the_top_bar_has_one_button_that_expands_or_collapses_all(self):
        panel = self.dropdown_panel_controller
        self.list_controller.apply_snapshot(make_sample_snapshot())
        button = panel.expand_or_collapse_all_button
        panel_width = panel.view().bounds().size.width
        self.assertEqual(button.frame().origin.x + button.frame().size.width, panel_width - PANEL_SIDE_MARGIN)  # the top right corner
        self.assertEqual(button.toolTip(), "Collapse all (⌘-e)")  # fully expanded: a click collapses
        panel.expandOrCollapseAllClicked_(button)
        self.assertEqual((self.list_controller.outline_view.numberOfRows(), button.toolTip()), (3, "Expand all (⌘-e)"))
        self.list_controller.toggle_expansion(self.list_controller.outline_view.itemAtRow_(0))  # one window expanded
        self.assertEqual(button.toolTip(), "Expand all (⌘-e)")
        panel.expandOrCollapseAllClicked_(button)
        self.assertEqual((self.list_controller.outline_view.numberOfRows(), button.toolTip()), (8, "Collapse all (⌘-e)"))

    def test_command_e_and_command_shift_e_expand_or_collapse_all(self):
        self.list_controller.apply_snapshot(make_sample_snapshot())
        outline_view = self.list_controller.outline_view

        def press_e(characters, modifier_flags):
            return self.window.performKeyEquivalent_(make_key_down_event(self.window, characters, 14, modifier_flags))
        for characters, modifier_flags, row_count in (("e", NSEventModifierFlagCommand, 3),
                                                      ("E", NSEventModifierFlagCommand | NSEventModifierFlagShift, 8)):  # Shift makes it "E"
            self.assertTrue(press_e(characters, modifier_flags))
            self.assertEqual(outline_view.numberOfRows(), row_count, (characters, modifier_flags))
        self.assertFalse(press_e("e", NSEventModifierFlagCommand | NSEventModifierFlagControl))  # another key
        self.assertEqual(outline_view.numberOfRows(), 8)
        one_window_snapshot = make_sample_snapshot()
        self.list_controller.apply_snapshot(replace_windows_of_the_first_monitor(one_window_snapshot, one_window_snapshot.monitors[0].windows[:1]))
        self.assertFalse(press_e("e", NSEventModifierFlagCommand))  # the button is hidden with one window, and so is its key
        self.assertEqual(outline_view.numberOfRows(), 5)

    def test_command_s_and_command_shift_s_open_the_sort_popup_leaving_the_keyboard_on_the_rows(self):
        self.list_controller.apply_snapshot(make_sample_snapshot())
        popup = self.dropdown_panel_controller.list_sort_order_popup
        self.assertEqual(popup.toolTip(), "Sort the list (⌘-s)")
        first_responder = self.window.firstResponder()

        def press_s(characters, modifier_flags):
            # performClick_ would open the menu and wait in it for a choice; here it only counts.
            with unittest.mock.patch.object(popup, "performClick_") as open_the_menu:
                handled = self.window.performKeyEquivalent_(make_key_down_event(self.window, characters, 1, modifier_flags))
            return handled, open_the_menu.call_count
        for characters, modifier_flags in (("s", NSEventModifierFlagCommand),
                                           ("S", NSEventModifierFlagCommand | NSEventModifierFlagShift)):  # Shift makes it "S"
            self.assertEqual(press_s(characters, modifier_flags), (True, 1), (characters, modifier_flags))
        self.assertEqual(press_s("s", NSEventModifierFlagCommand | NSEventModifierFlagControl), (False, 0))  # another key
        self.assertIs(self.window.firstResponder(), first_responder)  # the rows keep the keyboard

    def test_command_s_opens_the_sort_menu_at_the_popups_place_in_a_list_too_narrow_to_show_the_popup(self):
        # Such a list may have dropped the column titles that sort it too: the key is then the only way left to sort it.
        panel = self.dropdown_panel_controller
        popup = panel.list_sort_order_popup
        self.list_controller.apply_snapshot(make_sample_snapshot())
        self.owner.list_sort_order_chosen = panel.show_list_sort_order  # as the app controller has every list show a chosen order
        self.assertEqual([str(menu_item.representedObject()) for menu_item in popup.menu().itemArray()], LIST_SORT_ORDERS)
        # In place of the pop-up's own menu, which would open and wait for a choice: one that records where it would
        # open, filled as the panel fills the pop-up's.
        sort_menu = MenuThatRecordsItsOpenings.alloc().initWithTitle_("")
        popup.setMenu_(sort_menu)
        add_popup_choices(popup, LIST_SORT_ORDER_TITLES)
        panel.show_list_sort_order("alphabet", False)
        self.set_dropdown_panel_width(200)  # the sidebar's smallest width
        self.assertTrue(popup.isHidden())
        first_responder = self.window.firstResponder()

        def press_s(characters, modifier_flags):
            return self.window.performKeyEquivalent_(make_key_down_event(self.window, characters, 1, modifier_flags))
        for characters, modifier_flags in (("s", NSEventModifierFlagCommand),
                                           ("S", NSEventModifierFlagCommand | NSEventModifierFlagShift)):  # Shift makes it "S"
            self.assertTrue(press_s(characters, modifier_flags), (characters, modifier_flags))
        self.assertFalse(press_s("s", NSEventModifierFlagCommand | NSEventModifierFlagControl))  # another key
        self.assertEqual(len(sort_menu.openings), 2)
        self.assertEqual(sort_menu.openings[0], sort_menu.openings[1])
        # The chosen order's item at the left end of the top bar, where the pop-up is while it shows.
        item_placed, (x, y), view = sort_menu.openings[0]
        self.assertEqual((item_placed.title(), view), ("Name", panel.view()))
        panel_size = panel.view().bounds().size
        self.assertEqual(x, panel.sort_label.frame().origin.x)
        self.assertTrue(panel_size.height - TOP_BAR_HEIGHT < y <= panel_size.height, y)
        self.assertIs(self.window.firstResponder(), first_responder)  # the rows keep the keyboard
        # An order chosen in that menu sorts the list, in its first direction, and is the pop-up's choice once it shows again.
        sort_menu.performActionForItemAtIndex_(sort_menu.indexOfItemWithRepresentedObject_("context_percent"))
        self.assertEqual((self.list_controller.list_sort_order, self.list_controller.list_sort_order_is_reversed, read_popup_choice(popup)),
                         ("context_percent", False, "context_percent"))
        # Wide enough for the pop-up again, the key is the pop-up's own.
        self.set_dropdown_panel_width(POPOVER_WIDTH)
        self.assertFalse(popup.isHidden())
        with unittest.mock.patch.object(popup, "performClick_") as open_the_popups_menu:
            self.assertTrue(press_s("s", NSEventModifierFlagCommand))
        self.assertEqual((open_the_popups_menu.call_count, len(sort_menu.openings)), (1, 2))

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
        outline_view = self.apply_snapshot_with_processes_on_three_tabs()
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
        self.assertEqual(plus_button.toolTip(), "Start an agent session in a new window")
        self.assertTrue(self.lay_out_row_view(1).new_window_button.isHidden())  # only on the "+" row
        tab_row_view = self.lay_out_row_view(plus_row - 1)  # the last tab: "└─ "
        connector_frame = tab_row_view.prefix_text_field.frame()
        plus_center_x = plus_button.frame().origin.x + plus_button.frame().size.width / 2
        self.assertLess(abs(plus_center_x - self.list_controller.measure_tree_line_x()), 0.5)
        self.assertLess(connector_frame.origin.x, plus_center_x)
        self.assertLess(plus_center_x, connector_frame.origin.x + connector_frame.size.width / 3)  # under the "└"
        self.assertEqual(self.build_right_click_menu_titles(plus_row), ["New Claude Code window", "New Codex window"])
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
        one_window_snapshot = replace_windows_of_the_first_monitor(one_window_snapshot, one_window_snapshot.monitors[0].windows[:1])
        self.list_controller.apply_snapshot(one_window_snapshot)
        self.assertTrue(button.isHidden())
        self.window.setContentSize_((320, 260))
        # Without the expand button, Hide Idle is at the right end, and the pop-up before it.
        idle_tabs_button_frame = panel.idle_tabs_button.frame()
        self.assertEqual(idle_tabs_button_frame.origin.x + idle_tabs_button_frame.size.width, 320 - PANEL_SIDE_MARGIN)
        popup_frame = panel.list_sort_order_popup.frame()
        self.assertLessEqual(popup_frame.origin.x + popup_frame.size.width + GAP_BETWEEN_TOP_BAR_BUTTONS, idle_tabs_button_frame.origin.x)
        # A message (iTerm2 can't be reached) takes the room up to the right end, with the Hide Idle button hidden.
        panel.show_message("iTerm2 can't be reached")
        message_frame = panel.message_text_field.frame()
        self.assertEqual(message_frame.origin.x + message_frame.size.width, 320 - PANEL_SIDE_MARGIN - 8)
        panel.show_message("")
        self.list_controller.apply_snapshot(make_sample_snapshot())
        self.assertFalse(button.isHidden())
        self.assertEqual(button.frame().origin.x + button.frame().size.width, panel.view().bounds().size.width - PANEL_SIDE_MARGIN)
        panel.show_message("iTerm2 can't be reached")  # then up to the expand button
        message_frame = panel.message_text_field.frame()
        self.assertEqual(message_frame.origin.x + message_frame.size.width, button.frame().origin.x - 8)
        panel.show_message("")
        self.list_controller.apply_snapshot(make_two_monitor_snapshot(make_sample_snapshot()))  # one window per monitor
        self.assertFalse(button.isHidden())

    def lay_out_tab_row_views(self, snapshot):
        self.list_controller.apply_snapshot(snapshot)
        self.window.setContentSize_((POPOVER_WIDTH + 200, 260))
        self.window.contentView().layoutSubtreeIfNeeded()
        row_views = []
        for row in range(self.list_controller.outline_view.numberOfRows()):
            if self.list_controller.outline_view.itemAtRow_(row).kind == "tab":
                row_views.append(self.lay_out_row_view(row))
        return row_views

    def test_the_tool_icons_name_their_tool_on_hover(self):
        row_views = self.lay_out_tab_row_views(make_sample_snapshot())  # planning, phase0-tests, codex-review, analysis-0, -1
        self.assertEqual([row_view.column_views["agent_tool_icon"].toolTip() for row_view in row_views],
                         ["Claude Code", "Claude Code", "OpenAI Codex", "Claude Code", "Claude Code"])

    def test_codex_shows_its_name_as_an_image_where_macos_cant_read_the_svg_of_its_logo(self):
        with unittest.mock.patch("agent_command_tab.user_interface.agent_tool_icons.build_svg_image", return_value=None):
            self.list_controller.agent_tool_icons_by_name = build_agent_tool_icons()
        self.assertNotIn("Codex", self.list_controller.agent_tool_icons_by_name)
        row_views = self.lay_out_tab_row_views(make_sample_snapshot())  # the third tab runs Codex
        name_image = row_views[2].column_views["agent_tool_icon"].image()
        self.assertGreater(name_image.size().width, name_image.size().height)  # a word, where the logo is square
        self.assertEqual(self.list_controller.column_widths["agent_tool_icon"], name_image.size().width)  # the column has room for it

    def test_tool_icon_state_and_time_columns_line_up_when_two_tools_run(self):
        snapshot = make_sample_snapshot_with_entries_changed(started_at=time.time() - 3700)  # Claude Code and Codex
        self.list_controller.set_shown_columns(["state", "existence_duration"])  # Age, a time column
        row_views = self.lay_out_tab_row_views(snapshot)
        column_views_by_name = {"icon": [row_view.column_views["agent_tool_icon"] for row_view in row_views],
                                "state": [row_view.column_views["state"] for row_view in row_views],
                                "time": [row_view.column_views["existence_duration"] for row_view in row_views]}
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
        self.assertEqual(read_text_alignment(row_views[0].column_views["existence_duration"]), NSTextAlignmentRight)
        self.assertNotEqual(read_text_alignment(row_views[0].column_views["state"]), NSTextAlignmentRight)
        self.list_controller.toggle_expansion(self.list_controller.outline_view.itemAtRow_(0))  # only a collapsed window has counts
        window_row_view = self.list_controller.outline_view.viewAtColumn_row_makeIfNecessary_(0, 0, True)
        self.assertEqual(window_row_view.details_text_field.stringValue(), "1 asking, 1 working, 1 done")
        self.assertEqual(read_text_alignment(window_row_view.details_text_field), NSTextAlignmentRight)

    def test_no_icon_column_when_every_agent_session_uses_the_same_tool(self):
        snapshot = make_sample_snapshot()
        window_one = snapshot.monitors[0].windows[0]
        claude_only_window = window_one._replace(agent_sessions=window_one.agent_sessions[:2])
        snapshot = replace_windows_of_the_first_monitor(snapshot, (claude_only_window, snapshot.monitors[0].windows[1]))
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
        self.assertEqual(row_view.column_views["state"].stringValue(), "thinking")
        self.assertIsNone(row_view.toolTip())  # no hover popups on rows
        self.assertEqual(row_view.dot_text_field.toolTip(),
                         "Thinking: the agent is thinking or briefly running tools")  # only on the dot
        self.assertIsNone(row_view.details_text_field.toolTip())  # no background tasks
        self.assertTrue(row_view.prefix_text_field.stringValue().startswith("├─ "))
        bitmap = render_view_into_bitmap(content_view)
        # Drawn: a line through the middle of the name "planning" crosses its letters, so it holds more than one color.
        name_rect = content_view.convertRect_fromView_(row_view.name_text_field.bounds(), row_view.name_text_field)
        scale = bitmap.pixelsWide() / content_view.bounds().size.width
        y_from_the_top = content_view.bounds().size.height - name_rect.origin.y - name_rect.size.height / 2
        self.assertGreater(len({read_drawn_color_components(bitmap, scale, name_rect.origin.x + x, y_from_the_top)
                                for x in range(int(name_rect.size.width))}), 1)
        image_path = os.environ.get("AGENT_COMMAND_TAB_SMOKE_TEST_IMAGE")
        if image_path:
            self.assertTrue(
                bitmap.representationUsingType_properties_(NSBitmapImageFileTypePNG, {}).writeToFile_atomically_(image_path, True),
                f"The image could not be saved to {image_path}")

    # ----- process rows, background lights, exited rows and the hooks note -----

    def apply_snapshot_with_child_processes(self, child_processes=None):
        """Applies the sample snapshot with child processes on the first tab."""
        child_processes = child_processes if child_processes is not None else (
            ChildProcessEntry(201, "caffeinate -i -t 300", 900.0, False, None, None),
            ChildProcessEntry(202, "python3 -c import time; time.sleep(600) " + "x" * 300, 950.0, True, 300 * 1024 ** 2, 12))
        self.list_controller.apply_snapshot(make_sample_snapshot_with_entries_changed(0, 0, child_processes=child_processes))
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
        self.assertEqual([row_view.dot_text_field.toolTip().split(":")[0] for row_view in process_rows],
                         ["Not a background task", "Running in the background"])
        self.assertEqual(process_rows[0].name_text_field.stringValue(), "caffeinate -i -t 300")
        self.assertTrue(process_rows[1].disclosure_button.isHidden())
        self.assertFalse(process_rows[1].column_views["state"].isHidden())  # the columns line up with the tabs'
        self.assertEqual(process_rows[1].column_views["state"].stringValue(), "")
        self.assertEqual(self.list_controller.measure_widest_row_width(), closed_width)  # a long command doesn't widen the dropdown
        # The command runs on over the columns a process row leaves empty (the default ones), up to the first with text.
        name_frame = process_rows[1].name_text_field.frame()
        self.assertGreater(name_frame.origin.x + name_frame.size.width, process_rows[1].column_views["state"].frame().origin.x)
        self.assertLessEqual(name_frame.origin.x + name_frame.size.width, process_rows[1].frame().size.width)
        self.list_controller.set_shown_columns(["context_tokens", "memory_usage", "state"])
        name_frame = self.lay_out_row_view(3).name_text_field.frame()
        self.assertLessEqual(name_frame.origin.x + name_frame.size.width, self.lay_out_row_view(3).column_views["memory_usage"].frame().origin.x)
        # Selectable alone or together, but not with a window or tab row: then its tab is selected
        # instead. A double-click shows its tab.
        def choose_selection(proposed_rows):
            return list(self.list_controller.outlineView_selectionIndexesForProposedSelection_(
                outline_view, build_index_set(proposed_rows)))
        self.assertEqual(choose_selection([2, 3]), [2, 3])
        self.assertEqual(choose_selection([2, 3, 5]), [1, 5])
        self.assertEqual(choose_selection([0, 1, 2]), [0, 1])
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

    def test_a_process_rows_menu_has_only_info_and_kill_and_kill_asks_first(self):
        self.apply_snapshot_with_child_processes()
        self.click_row_arrow(1)
        self.assertEqual(self.build_right_click_menu_titles(3), ["Info", "---", "Kill"])
        menu_items_by_title = self.build_right_click_menu_items_by_title(3)
        self.list_controller.menuShowProcessInfo_(menu_items_by_title["Info"])
        self.assertEqual(self.owner.process_info_requests, [("s1", 202)])
        self.list_controller.menuKillProcess_(menu_items_by_title["Kill"])  # the user says No
        self.assertEqual(self.owner.confirmation_action_names, ["kill_process"])  # remembered apart from Kill session's "kill"
        self.assertTrue(self.owner.confirmation_questions[0].startswith(
            'Kill ends "python3 -c import time; time.sleep(600) xxx'))
        self.assertIn("…\" (process 202) and every process it started", self.owner.confirmation_questions[0])  # a long command is cut
        self.assertEqual(self.submitted_actions, [])
        self.owner.answer_to_confirmation = True
        self.list_controller.menuKillProcess_(menu_items_by_title["Kill"])
        self.assertEqual([(request.action, request.target, request.arguments) for request in self.submitted_actions],
                         [("end_child_process", {"session_id": "s1"}, {"process_id": 202})])
        # A right-click on one of several selected process rows acts on all of them.
        self.list_controller.outline_view.selectRowIndexes_byExtendingSelection_(build_index_set([2, 3]), False)
        menu_items_by_title = self.build_right_click_menu_items_by_title(3)
        self.assertEqual(list(menu_items_by_title), ["Info", "", "Kill 2 processes"])  # "" is the separator
        self.assertFalse(menu_items_by_title["Info"].isEnabled())
        self.list_controller.menuKillProcess_(menu_items_by_title["Kill 2 processes"])
        self.assertTrue(self.owner.confirmation_questions[-1].startswith('Kill ends 2 processes ("caffeinate -i -t 300", "python3'))
        self.assertEqual([request.arguments["process_id"] for request in self.submitted_actions[1:]], [201, 202])

    def test_kill_and_info_still_work_when_a_process_ends_while_the_question_or_menu_is_open(self):
        self.apply_snapshot_with_child_processes()
        self.click_row_arrow(1)
        self.select_rows(2, 3)
        menu_items_by_title = self.build_right_click_menu_items_by_title(3)

        def answer_yes_after_caffeinate_ended(explanation, action_name):
            self.apply_snapshot_with_child_processes(child_processes=(
                ChildProcessEntry(202, "python3 -c import time; time.sleep(600)", 950.0, True, None, None),))
            return True
        self.owner.ask_for_confirmation = answer_yes_after_caffeinate_ended
        self.list_controller.menuKillProcess_(menu_items_by_title["Kill 2 processes"])
        self.assertEqual([(request.target, request.arguments) for request in self.submitted_actions],
                         [({"session_id": "s1"}, {"process_id": 201}), ({"session_id": "s1"}, {"process_id": 202})])
        self.apply_snapshot_with_child_processes()  # both processes again
        self.list_controller.outline_view.deselectAll_(None)
        self.assertEqual(self.build_right_click_menu_titles(3), ["Info", "---", "Kill"])  # python3, whose row goes as the menu is open
        self.apply_snapshot_with_child_processes(child_processes=())
        self.list_controller.menuShowProcessInfo_(None)
        self.assertEqual(self.owner.process_info_requests, [("s1", 202)])

    def press_key(self, characters, key_code, modifier_flags=0):
        """Sends a key press to the list as AppKit does."""
        outline_view = self.list_controller.outline_view
        outline_view.keyDown_(make_key_down_event(outline_view.window(), characters, key_code, modifier_flags))

    def test_return_shows_the_selected_tab_or_window_and_esc_closes_the_list(self):
        self.apply_snapshot_with_child_processes()
        self.click_row_arrow(1)  # rows: w1, planning, its 2 processes, phase0-tests, codex-review, w2, ...
        for rows, characters, key_code, expected_target in (
                ([1], "\r", 36, {"session_id": "s1"}),
                ([0], "\x03", 76, {"window_id": "w1"}),  # the number pad's Enter
                ([3], "\r", 36, {"session_id": "s1"}),  # a process row shows its tab
                ([4, 5], "\r", 36, {"session_id": "s3"})):  # of several, the last one selected
            self.select_rows(*rows)
            self.press_key(characters, key_code)
            self.assertEqual(self.submitted_actions[-1].target, expected_target, rows)
        action_count = len(self.submitted_actions)
        self.press_key("\r", 36, NSEventModifierFlagCommand)  # left to AppKit
        self.assertEqual(len(self.submitted_actions), action_count)
        self.select_rows(1)  # with several selected, Esc would first keep only the last one
        self.press_key("\x1b", 53)
        self.assertEqual(self.owner.lists_closed_after_escape, [self.list_controller])

    def test_esc_first_keeps_only_the_last_selected_row_then_closes_the_list(self):
        self.list_controller.apply_snapshot(make_sample_snapshot())
        self.select_rows(1, 2, 3)
        self.press_key("\x1b", 53)
        self.assertEqual(self.list_controller.list_selected_items(), [self.list_controller.outline_view.itemAtRow_(3)])
        self.assertEqual(self.owner.lists_closed_after_escape, [])
        self.press_key("\x1b", 53)
        self.assertEqual(self.owner.lists_closed_after_escape, [self.list_controller])

    def test_control_return_opens_the_selected_rows_menu_at_its_tokens_column(self):
        self.list_controller.apply_snapshot(make_sample_snapshot_with_entries_changed(0, context_tokens=91_000, context_fill_percent=46))
        self.window.contentView().layoutSubtreeIfNeeded()
        outline_view = self.list_controller.outline_view
        tab_row_view = self.lay_out_row_view(1)
        self.assertFalse(tab_row_view.column_views["context_tokens"].isHidden())
        tokens_frame = outline_view.convertRect_fromView_(tab_row_view.column_views["context_tokens"].frame(), tab_row_view)
        self.assertTrue(NSPointInRect(self.list_controller.find_where_the_keyboard_opens_a_rows_menu(1), tokens_frame))
        # A window row, which shows no tokens: across from the tab rows' tokens.
        self.assertEqual(self.list_controller.find_where_the_keyboard_opens_a_rows_menu(0),
                         (NSMidX(tokens_frame), NSMidY(outline_view.rectOfRow_(0))))
        # macOS has its context menu key, Control-Return, from 15. AppKit sends it to showContextMenuForSelection_, never to keyDown_.
        if outline_view.respondsToSelector_("contextMenuKeyDown:"):
            menu = MenuThatRecordsWhereItWouldOpen.alloc().initWithTitle_("")
            menu.setDelegate_(self.list_controller)
            outline_view.setMenu_(menu)
            self.select_rows(1)
            outline_view.contextMenuKeyDown_(make_key_down_event(outline_view.window(), "\r", 36, NSEventModifierFlagControl))
            self.assertEqual(menu.openings,
                             [(self.list_controller.find_where_the_keyboard_opens_a_rows_menu(1), outline_view.itemAtRow_(1))])
            self.assertEqual(self.list_controller.row_whose_menu_the_keyboard_opened, -1)  # a right-click's menu finds its own row again
        self.list_controller.set_shown_columns([])  # Tokens hidden: the middle of the row
        row_rect = outline_view.rectOfRow_(1)
        self.assertEqual(self.list_controller.find_where_the_keyboard_opens_a_rows_menu(1), (NSMidX(row_rect), NSMidY(row_rect)))

    def test_delete_kills_what_the_selected_rows_show_asking_first(self):
        self.apply_snapshot_with_child_processes()
        self.click_row_arrow(1)
        self.owner.answer_to_confirmation = True
        for rows, characters, key_code in (([1], "\x7f", 51), ([6], "", 117), ([2, 3], "\x7f", 51)):
            self.select_rows(*rows)
            self.press_key(characters, key_code)
        # Not Kill session's "kill", and processes apart from sessions.
        self.assertEqual(self.owner.confirmation_action_names,
                         ["kill_with_the_delete_key", "kill_with_the_delete_key", "kill_process_with_the_delete_key"])
        self.assertEqual([(request.action, request.target, request.arguments) for request in self.submitted_actions],
                         [("end_agent_process", {"session_id": "s1"}, None),
                          ("end_agent_processes", {"session_ids": ["s10", "s11"]}, None),  # a window row: all its tabs
                          ("end_child_process", {"session_id": "s1"}, {"process_id": 201}),
                          ("end_child_process", {"session_id": "s1"}, {"process_id": 202})])

    def test_left_on_process_rows_selects_their_tab_and_typing_a_name_selects_its_row(self):
        self.apply_snapshot_with_child_processes()
        self.click_row_arrow(1)
        self.select_rows(3)
        self.press_key(NSLeftArrowFunctionKey, 123)
        self.assertEqual([item.entry.tab_title for item in self.list_controller.list_selected_items()], ["planning"])
        self.assertEqual(self.list_controller.outline_view.numberOfRows(), 10)  # the tab row stays open
        for characters, key_code in (("t", 17), ("a", 0)):
            self.press_key(characters, key_code)
        self.assertEqual([item.entry.title for item in self.list_controller.list_selected_items()], ["tandem-repeats"])

    def test_typing_a_name_reads_the_names_of_window_and_tab_rows_without_building_their_other_texts(self):
        outline_view = self.apply_snapshot_with_child_processes()
        self.click_row_arrow(1)  # planning's two process rows show
        with unittest.mock.patch.object(self.list_controller, "build_row_text_parts") as build_row_text_parts:
            names = [self.list_controller.outlineView_typeSelectStringForTableColumn_item_(outline_view, None, outline_view.itemAtRow_(row))
                     for row in range(outline_view.numberOfRows())]
        self.assertFalse(build_row_text_parts.called)  # it formats a row's state, times and resource use too
        self.assertEqual(names, ["agent-command-tab", "planning", None, None, "phase0-tests", "codex-review",
                                 "tandem-repeats", "analysis-0", "analysis-1", None])  # no process rows, nor the "+" row

    def test_the_first_asking_tab_or_else_the_previous_tab_is_selected_opening_its_collapsed_window(self):
        self.list_controller.apply_snapshot(make_sample_snapshot_with_entries_changed(1, 0, status="asking"))
        outline_view = self.list_controller.outline_view
        self.list_controller.toggle_expansion(outline_view.itemAtRow_(0))  # collapses agent-command-tab
        self.select_rows(2)  # analysis-0, which asks too, but below phase0-tests
        self.list_controller.select_the_first_asking_tab_or_else_the_previous_tab()  # planning is in front, but agents ask
        self.assertTrue(outline_view.isItemExpanded_(outline_view.itemAtRow_(0)))
        self.assertEqual([item.entry.tab_title for item in self.list_controller.list_selected_items()], ["phase0-tests"])

        def apply_snapshot_where_no_agent_asks(tab_id_in_front, last_time_tab_was_seen_by_tab_id):
            snapshot = make_sample_snapshot()
            self.list_controller.apply_snapshot(replace_windows_of_the_first_monitor(snapshot, (
                window_entry._replace(agent_sessions=tuple(
                    entry._replace(status="idle", is_in_front=entry.tab_id == tab_id_in_front,
                                   last_time_tab_was_seen=last_time_tab_was_seen_by_tab_id.get(entry.tab_id))
                    for entry in window_entry.agent_sessions))
                for window_entry in snapshot.monitors[0].windows)))

        def select_and_list_the_selected_tab_titles():
            self.list_controller.select_the_first_asking_tab_or_else_the_previous_tab()
            return [item.entry.tab_title for item in self.list_controller.list_selected_items()]
        planning_row = outline_view.rowForItem_(self.list_controller.items_by_key[("tab", "1")])
        # No other tab was seen in front since the app started: the tab in front.
        apply_snapshot_where_no_agent_asks("11", {"11": 300.0})  # analysis-1
        tandem_repeats_window_item = self.list_controller.items_by_key[("window", "w2")]
        self.list_controller.toggle_expansion(tandem_repeats_window_item)  # collapses tandem-repeats
        self.select_rows(planning_row)
        self.assertEqual(select_and_list_the_selected_tab_titles(), ["analysis-1"])
        self.assertTrue(outline_view.isItemExpanded_(tandem_repeats_window_item))
        # Else the tab seen in front last before the one in front, so Return goes back to it.
        apply_snapshot_where_no_agent_asks("11", {"1": 100.0, "3": 200.0, "11": 300.0})
        self.assertEqual(select_and_list_the_selected_tab_titles(), ["codex-review"])
        # Shown, codex-review is in front, and the shortcut then selects analysis-1, to come back.
        apply_snapshot_where_no_agent_asks("3", {"1": 100.0, "3": 400.0, "11": 300.0})
        self.assertEqual(select_and_list_the_selected_tab_titles(), ["analysis-1"])
        # While another app is in front, no tab is, so it is the tab used last.
        apply_snapshot_where_no_agent_asks(None, {"1": 100.0, "3": 400.0, "11": 300.0})
        self.assertEqual(select_and_list_the_selected_tab_titles(), ["codex-review"])
        # The selection stays as it is while no tab is in front and none was seen in front.
        apply_snapshot_where_no_agent_asks(None, {})
        self.select_rows(planning_row)
        self.assertEqual(select_and_list_the_selected_tab_titles(), ["planning"])

    def test_a_window_the_plus_row_opened_is_selected_and_scrolled_to_once_it_shows(self):
        # In the folder of another window, it looked like that window, and nothing pointed to it.
        snapshot = make_sample_snapshot(second_window_tabs=12)
        new_window = WindowEntry("w3", "tandem-repeats", False, (0, 0, 800, 600), 1, (make_entry("30", "new-session", "idle"),),
                                 number_among_windows_with_the_same_title=2)
        with_the_new_window = replace_windows_of_the_first_monitor(snapshot, snapshot.monitors[0].windows + (new_window,))
        outline_view = self.list_controller.outline_view
        self.list_controller.apply_snapshot(snapshot)
        self.select_rows(1)
        self.list_controller.select_window_once_it_shows("w3")
        self.list_controller.apply_snapshot(snapshot)  # its agent hasn't started yet
        self.assertEqual([item.entry.tab_title for item in self.list_controller.list_selected_items()], ["planning"])
        self.list_controller.apply_snapshot(with_the_new_window)
        self.assertEqual([item.entry.tab_title for item in self.list_controller.list_selected_items()], ["new-session"])
        self.assertGreater(outline_view.visibleRect().origin.y, 0)  # scrolled down to it, at the end of the list
        self.assertTrue(NSContainsRect(outline_view.visibleRect(), outline_view.rectOfRow_(outline_view.selectedRow())))
        self.select_rows(1)
        self.list_controller.apply_snapshot(with_the_new_window._replace(taken_at=2.0))  # only once
        self.assertEqual([item.entry.tab_title for item in self.list_controller.list_selected_items()], ["planning"])
        # While Hide Idle hides its row, nothing is selected; once the row shows, it is (its tab
        # was "idle" until the hooks' report of its start made it "new").
        self.list_controller.set_idle_tabs_are_hidden(True)
        self.list_controller.apply_snapshot(snapshot)
        self.list_controller.select_window_once_it_shows("w3")
        self.list_controller.apply_snapshot(with_the_new_window)
        self.assertEqual([item.entry.tab_title for item in self.list_controller.list_selected_items()], ["planning"])
        self.list_controller.set_idle_tabs_are_hidden(False)
        self.assertEqual([item.entry.tab_title for item in self.list_controller.list_selected_items()], ["new-session"])
        self.select_rows(1)
        # Nor once the wait is over: its launch command failed, and an agent was started there by
        # hand later.
        self.list_controller.apply_snapshot(snapshot)
        self.list_controller.select_window_once_it_shows("w3")
        with unittest.mock.patch("agent_command_tab.user_interface.agent_session_outline_view.time.time",
                                 return_value=time.time() + SECONDS_TO_WAIT_FOR_A_NEW_WINDOW_TO_SHOW + 1):
            self.list_controller.apply_snapshot(snapshot)
        self.list_controller.apply_snapshot(with_the_new_window)
        self.assertEqual([item.entry.tab_title for item in self.list_controller.list_selected_items()], ["planning"])

    def test_a_hidden_list_selects_a_window_that_showed_in_time_when_it_appears_later(self):
        # The wait for the window ended when the list next appeared, so the dropdown, closed as
        # "+" is used, selected nothing once reopened a minute later.
        snapshot = make_sample_snapshot()
        new_window = WindowEntry("w3", "tandem-repeats", False, (0, 0, 800, 600), 1, (make_entry("30", "new-session", "new"),))
        self.list_controller.defers_rebuilding_while_hidden = True  # as the app sets it for its three lists
        self.list_controller.apply_snapshot(snapshot)
        self.window.orderOut_(None)  # the dropdown closes
        self.list_controller.select_window_once_it_shows("w3")
        self.list_controller.apply_snapshot(replace_windows_of_the_first_monitor(snapshot, snapshot.monitors[0].windows + (new_window,)))
        with unittest.mock.patch("agent_command_tab.user_interface.agent_session_outline_view.time.time",
                                 return_value=time.time() + SECONDS_TO_WAIT_FOR_A_NEW_WINDOW_TO_SHOW + 60):
            self.list_controller.prepare_to_appear()
        self.window.orderFrontRegardless()
        self.assertEqual([item.entry.tab_title for item in self.list_controller.list_selected_items()], ["new-session"])
        # Not hours later, though: the selection the user left there since stays.
        self.select_rows(1)
        self.window.orderOut_(None)
        self.list_controller.select_window_once_it_shows("w3")
        self.list_controller.apply_snapshot(self.list_controller.snapshot._replace(taken_at=3.0))  # it shows at once
        with unittest.mock.patch("agent_command_tab.user_interface.agent_session_outline_view.time.time",
                                 return_value=time.time() + SECONDS_A_LIST_MAY_SELECT_A_NEW_WINDOW + 1):
            self.list_controller.prepare_to_appear()
        self.window.orderFrontRegardless()
        self.assertEqual([item.entry.tab_title for item in self.list_controller.list_selected_items()], ["planning"])

    def test_hide_idle_keeps_a_new_tab_so_a_window_the_plus_row_opened_is_selected(self):
        snapshot = make_sample_snapshot()
        new_window = WindowEntry("w3", "tandem-repeats", False, (0, 0, 800, 600), 1, (make_entry("30", "new-session", "new"),),
                                 number_among_windows_with_the_same_title=2)
        self.list_controller.set_idle_tabs_are_hidden(True)
        self.list_controller.apply_snapshot(snapshot)
        self.list_controller.select_window_once_it_shows("w3")
        self.list_controller.apply_snapshot(replace_windows_of_the_first_monitor(snapshot, snapshot.monitors[0].windows + (new_window,)))
        self.assertEqual(self.list_row_item_ids(),
                         ["w1", "1", "2", "3", "w3", "30", "new_window"])  # tandem-repeats' idle tabs are hidden
        self.assertEqual([item.entry.tab_title for item in self.list_controller.list_selected_items()], ["new-session"])

    def test_move_to_window_tells_windows_with_the_same_title_apart(self):
        snapshot = make_sample_snapshot()
        window_one, window_two = snapshot.monitors[0].windows
        self.list_controller.apply_snapshot(replace_windows_of_the_first_monitor(snapshot, (
            window_one, window_two, window_two._replace(window_id="w3", number_among_windows_with_the_same_title=2,
                                                       agent_sessions=(make_entry("30", "new-session", "idle"),)))))
        submenu = self.build_right_click_menu_items_by_title(1)["Move to window"].submenu()
        self.assertEqual([menu_item.title() for menu_item in submenu.itemArray()], ["tandem-repeats", "tandem-repeats (2)"])

    def test_move_to_window_offers_the_windows_whose_rows_hide_idle_or_the_search_hides_too(self):
        self.list_controller.apply_snapshot(make_sample_snapshot())
        self.list_controller.set_idle_tabs_are_hidden(True)  # tandem-repeats' two tabs are idle
        self.assertEqual(self.list_row_item_ids(), ["w1", "1", "2", "3", "new_window"])
        move_items = self.build_right_click_menu_items_by_title(1)["Move to window"].submenu().itemArray()
        self.assertEqual([menu_item.title() for menu_item in move_items], ["tandem-repeats"])
        self.list_controller.menuMoveToWindow_(move_items[0])
        self.assertEqual((self.submitted_actions[-1].action, self.submitted_actions[-1].target, self.submitted_actions[-1].arguments),
                         ("move_tab", {"tab_id": "1"}, {"target_window_id": "w2", "drop_before_tab_id": None}))
        self.list_controller.set_idle_tabs_are_hidden(False)
        self.type_in_the_search_box("analysis-0")
        self.owner.answer_conversation_search([])
        self.assertEqual(self.list_row_item_ids(), ["w2", "10", "new_window"])
        move_items = self.build_right_click_menu_items_by_title(1)["Move to window"].submenu().itemArray()
        self.assertEqual([menu_item.title() for menu_item in move_items], ["agent-command-tab"])

    def test_a_tmux_session_has_no_monitor_to_move_to_and_takes_no_iterm2_tab(self):
        snapshot = make_two_monitor_snapshot(make_sample_snapshot())
        tmux_window = snapshot.monitors[1].windows[0]._replace(terminal_name="tmux", can_move_to_another_monitor=False)
        self.list_controller.apply_snapshot(snapshot._replace(monitors=(
            snapshot.monitors[0], snapshot.monitors[1]._replace(windows=(tmux_window,)))))
        outline_view = self.list_controller.outline_view
        row_by_item_id = {outline_view.itemAtRow_(row).item_id: row for row in range(outline_view.numberOfRows())}
        self.assertIn("Move to monitor", self.build_right_click_menu_titles(row_by_item_id["w1"]))
        self.assertNotIn("Move to monitor", self.build_right_click_menu_titles(row_by_item_id["w2"]))
        self.assertNotIn("Move to window", self.build_right_click_menu_titles(row_by_item_id["1"]))  # the only other window is tmux's
        self.assertEqual(self.list_controller.build_row_text_parts(outline_view.itemAtRow_(row_by_item_id["w2"])).name_text,
                         "tmux: tandem-repeats")

    def test_r_command_r_and_command_shift_r_rename_the_one_selected_tab_or_window_row(self):
        self.list_controller.apply_snapshot(make_sample_snapshot())
        for row, characters, modifier_flags, renamed_title in (
                (1, "r", 0, "planning"), (0, "r", NSEventModifierFlagCommand, "agent-command-tab"),
                (1, "R", NSEventModifierFlagCommand | NSEventModifierFlagShift, "planning"),  # Shift makes it "R"
                (0, "r", NSEventModifierFlagCommand | NSEventModifierFlagShift, "agent-command-tab")):
            self.select_rows(row)
            self.press_key(characters, 15, modifier_flags)
            self.assertTrue(self.list_controller.rename_in_progress, (characters, modifier_flags))
            self.assertEqual(self.list_controller.title_before_rename, renamed_title)
            self.list_controller.cancel_rename()
        self.select_rows(1)
        for characters, modifier_flags in (("R", NSEventModifierFlagShift), ("r", NSEventModifierFlagCommand | NSEventModifierFlagControl)):
            self.list_controller.last_name_letter_typed_at = 0
            self.press_key(characters, 15, modifier_flags)  # Shift-R alone types a name's letter; Control-Command-R is another key
            self.assertFalse(self.list_controller.rename_in_progress, (characters, modifier_flags))
        self.select_rows(1, 2)
        self.press_key("r", 15)  # with several rows selected, nothing is renamed
        self.assertFalse(self.list_controller.rename_in_progress)
        # Right after another letter, R goes on selecting a row by name.
        self.select_rows(1)
        self.list_controller.last_name_letter_typed_at = 0
        self.press_key("t", 17)
        self.press_key("r", 15)
        self.assertFalse(self.list_controller.rename_in_progress)
        self.press_key("r", 15, NSEventModifierFlagCommand)  # Command-R still renames
        self.assertTrue(self.list_controller.rename_in_progress)
        self.list_controller.cancel_rename()

    def test_the_right_and_left_arrow_keys_open_and_close_a_selected_tab_row(self):
        outline_view = self.apply_snapshot_with_child_processes()
        tab_item = outline_view.itemAtRow_(1)
        outline_view.selectRowIndexes_byExtendingSelection_(build_index_set([1]), False)
        for function_key, key_code, row_count in ((NSRightArrowFunctionKey, 124, 10), (NSLeftArrowFunctionKey, 123, 8),
                                                  (NSRightArrowFunctionKey, 124, 10)):
            self.press_key(function_key, key_code)
            self.assertEqual(outline_view.numberOfRows(), row_count, function_key)
            self.assertEqual(self.list_controller.list_selected_items(), [tab_item])  # still selected

    def test_process_rows_come_once_the_arrows_button_is_up(self):
        outline_view = self.apply_snapshot_with_child_processes()
        self.list_controller.row_press_in_progress = True  # as RowButton.mouseDown_ sets it around the click's action
        self.click_row_arrow(1)
        self.assertEqual(outline_view.numberOfRows(), 8)  # held while the button is down
        self.list_controller.row_released()
        self.assertEqual(outline_view.numberOfRows(), 10)

    def test_background_tasks_light_up_after_the_tab_name(self):
        self.list_controller.apply_snapshot(make_sample_snapshot_with_entries_changed(0, 0, background_task_count=3))
        self.window.contentView().layoutSubtreeIfNeeded()
        row_view = self.lay_out_row_view(1)
        self.assertEqual(row_view.details_text_field.stringValue(), "•••")
        self.assertGreater(row_view.details_text_field.frame().origin.x, row_view.name_text_field.frame().origin.x)
        self.assertEqual(self.lay_out_row_view(2).name_text_field.frame().origin.x, row_view.name_text_field.frame().origin.x)
        # Their hover text shows over the lights only, not over the empty room up to the columns.
        self.assertEqual(row_view.details_text_field.toolTip(), "3 background tasks")
        self.assertEqual(row_view.details_text_field.frame().size.width, row_view.details_text_field.fittingSize().width)

    def test_an_exited_row_is_gray_and_its_menu_has_nothing_to_send_or_kill(self):
        self.list_controller.apply_snapshot(make_sample_snapshot_with_entries_changed(
            0, 0, status="exited", slash_commands=()))
        self.window.contentView().layoutSubtreeIfNeeded()
        row_view = self.lay_out_row_view(1)
        self.assertEqual((row_view.dot_text_field.stringValue(), row_view.column_views["state"].stringValue()), ("⊘ ", "exited"))
        state_color = row_view.column_views["state"].attributedStringValue().attribute_atIndex_effectiveRange_(
            NSForegroundColorAttributeName, 0, None)[0]
        for appearance_name in (NSAppearanceNameAqua, NSAppearanceNameDarkAqua):
            self.assertEqual(resolve_color(state_color, appearance_name), resolve_color(NSColor.secondaryLabelColor(), appearance_name))
        menu = self.build_right_click_menu(1)
        disabled_titles = [menu_item.title() for menu_item in menu.itemArray() if not menu_item.isEnabled() and menu_item.title()]
        self.assertTrue({"/compact", "/clear", "Kill session", "Duplicate this tab"} <= set(disabled_titles))
        self.assertEqual(menu.itemWithTitle_("Kill session").toolTip(), "The agent has ended")
        self.assertEqual(menu.itemWithTitle_("Duplicate this tab").toolTip(), "The agent has ended")
        self.assertTrue(menu.itemWithTitle_("Rename").isEnabled())

    def test_the_bottom_bar_notes_missing_hooks_for_the_agent_tools_listed(self):
        panel = self.dropdown_panel_controller
        snapshot = make_sample_snapshot()  # lists Claude Code sessions, the only agent tool the core reports a hooks status for
        for hooks_status_per_agent_tool, expected_note, shows_button in (
                ((("Claude Code", "missing"),), "Claude Code hooks not installed yet", True),
                ((("Claude Code", "installed"),), "", False),
                # Hooks that don't work, which installing them again wouldn't fix.
                ((("Claude Code", "can't run"),), "Claude Code hooks can't run: reinstall AgentCommandTab", False),
                ((("Claude Code", "send no events"),), "No events from the Claude Code hooks", False)):
            self.list_controller.apply_snapshot(snapshot._replace(hooks_status_per_agent_tool=hooks_status_per_agent_tool))
            self.assertEqual((panel.hooks_note_text_field.stringValue(), panel.hooks_note_text_field.isHidden(),
                              panel.install_hooks_button.isHidden()), (expected_note, not expected_note, not shows_button))
        self.assertEqual(panel.hooks_note_text_field.toolTip(), HOOKS_SEND_NO_EVENTS_TOOL_TIP)  # says what to check
        self.list_controller.apply_snapshot(snapshot._replace(hooks_status_per_agent_tool=(("Claude Code", "missing"),)))
        self.select_rows(1)  # "planning", which runs: the pause button shows at the bottom right
        self.assertFalse(panel.pause_or_resume_selected_rows_button.isHidden())
        note_frame, button_frame = panel.hooks_note_text_field.frame(), panel.install_hooks_button.frame()
        settings_button_frame = panel.settings_button.frame()  # at the bottom left, then the note, then its button
        self.assertEqual(settings_button_frame.origin.x, PANEL_SIDE_MARGIN)
        self.assertEqual(note_frame.origin.x, settings_button_frame.origin.x + settings_button_frame.size.width + GAP_BEFORE_THE_HOOKS_NOTE)
        self.assertLess(note_frame.origin.x + note_frame.size.width, button_frame.origin.x)
        self.assertLessEqual(button_frame.origin.x + button_frame.size.width, panel.restore_sessions_button.frame().origin.x)
        self.assertLess(button_frame.origin.y + button_frame.size.height, BOTTOM_BAR_HEIGHT)
        panel.installHooksClicked_(panel.install_hooks_button)
        self.assertEqual(self.owner.hooks_install_requests, [["Claude Code"]])
        # In the narrowest sidebar the button doesn't fit before the pause or resume button, so it
        # is left out rather than covering that button.
        panel_view = panel.view()
        wide_size = panel_view.frame().size
        panel_view.setFrameSize_((200, wide_size.height))
        self.assertTrue(panel.install_hooks_button.isHidden())
        panel_view.setFrameSize_(wide_size)
        self.assertFalse(panel.install_hooks_button.isHidden())
        only_codex_sessions = make_sample_snapshot_with_entries_changed(agent_tool_name="Codex")._replace(
            hooks_status_per_agent_tool=(("Claude Code", "missing"),))
        self.list_controller.apply_snapshot(only_codex_sessions)  # no Claude Code session listed: nothing to say about its hooks
        self.assertTrue(panel.hooks_note_text_field.isHidden())

    # ----- the bottom bar's legend of the rows' outlines -----

    def set_dropdown_panel_width(self, width):
        """Makes the dropdown's panel this wide, as dragging a sidebar's or a list window's edge does, laid out at once."""
        self.window.setContentSize_((width, 260))
        self.window.contentView().layoutSubtreeIfNeeded()

    def test_the_legend_starts_as_far_after_the_settings_button_as_the_search_box_after_the_sort_popup(self):
        panel = self.dropdown_panel_controller
        legend_view = panel.row_outlines_legend_view
        self.list_controller.apply_snapshot(make_sample_snapshot())
        self.window.contentView().layoutSubtreeIfNeeded()
        for view in (panel.list_sort_order_popup, panel.search_field, panel.settings_button, legend_view):
            self.assertFalse(view.isHidden())
        popup_frame, settings_button_frame = panel.list_sort_order_popup.frame(), panel.settings_button.frame()
        legend_frame = legend_view.frame()
        gap_before_the_search_box = panel.search_field.frame().origin.x - (popup_frame.origin.x + popup_frame.size.width)
        gap_before_the_legend = legend_frame.origin.x - (settings_button_frame.origin.x + settings_button_frame.size.width)
        self.assertEqual(gap_before_the_legend, gap_before_the_search_box)
        self.assertEqual(gap_before_the_legend, GAP_AFTER_THE_SORT_CONTROL_AND_THE_SETTINGS_BUTTON)
        # It is as tall as the bottom bar, and draws its texts on the baseline of the Settings button's title, where
        # AppKit says that one is.
        self.assertEqual((legend_frame.origin.y, legend_frame.size.height), (0, BOTTOM_BAR_HEIGHT))
        self.assertEqual(legend_view.text_baseline_y, settings_button_frame.origin.y + panel.settings_button.alignmentRectInsets().bottom
                         + panel.settings_button.lastBaselineOffsetFromBottom())

    def test_the_legend_shows_whole_or_not_at_all_and_never_under_the_buttons_at_the_right(self):
        panel = self.dropdown_panel_controller
        legend_view, pause_or_resume_button = panel.row_outlines_legend_view, panel.pause_or_resume_selected_rows_button
        self.list_controller.apply_snapshot(make_sample_snapshot())  # its hooks are installed: no note
        legend_frame = legend_view.frame()
        # The narrowest panel whose bottom bar has room for all of the legend before the three buttons at the right
        # (Restore, the leftmost, then Save, then pause or resume).
        narrowest_width_with_the_legend = math.ceil(
            legend_frame.origin.x + legend_frame.size.width
            + 3 * (GAP_BETWEEN_TOP_BAR_BUTTONS + pause_or_resume_button.frame().size.width) + PANEL_SIDE_MARGIN)
        # Narrowed to that width, to a point less, to the sidebar's smallest width (200), and widened again.
        for width in (POPOVER_WIDTH, narrowest_width_with_the_legend, narrowest_width_with_the_legend - 1, 200, POPOVER_WIDTH):
            self.set_dropdown_panel_width(width)
            # The pause or resume button's place stays free while that button is hidden (no row selected), so the legend
            # doesn't come and go as rows are selected ("planning", row 1, runs).
            for selected_rows in ((), (1,)):
                self.select_rows(*selected_rows)
                self.assertEqual(pause_or_resume_button.isHidden(), not selected_rows)
                self.assertEqual(legend_view.isHidden(), width < narrowest_width_with_the_legend, (width, selected_rows))
                self.assertEqual(tuple(legend_view.frame().size), tuple(legend_frame.size), width)  # never cut short
                if not legend_view.isHidden():
                    self.assertLessEqual(legend_frame.origin.x + legend_frame.size.width + GAP_BETWEEN_TOP_BAR_BUTTONS,
                                         panel.restore_sessions_button.frame().origin.x, width)

    def test_a_hooks_note_follows_the_legend_where_both_fit_whole_and_takes_its_place_where_they_dont(self):
        panel = self.dropdown_panel_controller
        legend_view, note_field = panel.row_outlines_legend_view, panel.hooks_note_text_field
        install_hooks_button, pause_or_resume_button = panel.install_hooks_button, panel.pause_or_resume_selected_rows_button
        legend_frame, settings_button_frame = legend_view.frame(), panel.settings_button.frame()
        legend_right_end_x = legend_frame.origin.x + legend_frame.size.width
        # A note with its Install Hooks button, and one without (hooks that are installed but send nothing).
        for hooks_status, has_install_hooks_button in (("missing", True), ("send no events", False)):
            self.list_controller.apply_snapshot(
                make_sample_snapshot()._replace(hooks_status_per_agent_tool=(("Claude Code", hooks_status),)))
            note_width = note_field.fittingSize().width
            narrowest_width_with_both = math.ceil(
                legend_right_end_x + GAP_BEFORE_THE_HOOKS_NOTE + note_width
                + (GAP_BETWEEN_TOP_BAR_BUTTONS + install_hooks_button.frame().size.width if has_install_hooks_button else 0)
                + 3 * (GAP_BETWEEN_TOP_BAR_BUTTONS + pause_or_resume_button.frame().size.width) + PANEL_SIDE_MARGIN)
            # Room for both: the legend, then the whole note, then its button, before the three buttons at the right.
            self.set_dropdown_panel_width(narrowest_width_with_both)
            self.assertEqual((legend_view.isHidden(), note_field.isHidden(), install_hooks_button.isHidden()),
                             (False, False, not has_install_hooks_button), hooks_status)
            note_frame = note_field.frame()
            self.assertEqual((note_frame.origin.x, note_frame.size.width), (legend_right_end_x + GAP_BEFORE_THE_HOOKS_NOTE, note_width))
            if has_install_hooks_button:
                button_frame = install_hooks_button.frame()
                self.assertEqual(button_frame.origin.x, note_frame.origin.x + note_width + GAP_BETWEEN_TOP_BAR_BUTTONS)
                self.assertLessEqual(button_frame.origin.x + button_frame.size.width + GAP_BETWEEN_TOP_BAR_BUTTONS,
                                     panel.restore_sessions_button.frame().origin.x)
            # A point narrower, the legend, which only explains, hides, and the note, which asks for something to be
            # done, takes its place after the Settings button, still whole.
            self.set_dropdown_panel_width(narrowest_width_with_both - 1)
            self.assertEqual((legend_view.isHidden(), note_field.isHidden(), install_hooks_button.isHidden()),
                             (True, False, not has_install_hooks_button), hooks_status)
            note_frame = note_field.frame()
            self.assertEqual((note_frame.origin.x, note_frame.size.width),
                             (settings_button_frame.origin.x + settings_button_frame.size.width + GAP_BEFORE_THE_HOOKS_NOTE, note_width))

    def test_the_legend_pairs_the_colors_of_the_rows_two_outlines_with_current_tab_and_selected(self):
        panel = self.dropdown_panel_controller
        legend_view = panel.row_outlines_legend_view
        self.list_controller.apply_snapshot(make_sample_snapshot())  # planning (row 1) is the current tab
        outline_view = self.list_controller.outline_view
        self.select_rows(3)  # codex-review: row 1 has the current tab's outline, row 3 the selection's, row 2 none
        # The squares take their colors from the makers the rows stroke their outlines with,
        (current_tab_color_maker, current_tab_square_x), (selected_color_maker, selected_square_x) = (
            legend_view.square_color_makers_and_their_x)
        self.assertIs(current_tab_color_maker, FRONT_TAB_OUTLINE_COLOR_MAKER)
        self.assertIs(selected_color_maker, SELECTION_OUTLINE_COLOR_MAKER)
        # each right before the text of what it marks,
        (title, title_x), (current_tab_text, current_tab_text_x), (selected_text, selected_text_x) = legend_view.texts_and_their_x
        self.assertEqual([text.string() for text in (title, current_tab_text, selected_text)], ["Legend:", "= current tab", "= selected"])
        self.assertTrue(title_x < current_tab_square_x < current_tab_text_x < selected_square_x < selected_text_x)
        content_view = self.window.contentView()
        content_view.layoutSubtreeIfNeeded()
        self.assertFalse(legend_view.isHidden())
        # and are drawn in the colors the rows' outlines are drawn in, in light and in dark mode: read halfway down the
        # left edge of each square and of each row (given from the panel's bottom left).
        legend_frame = legend_view.frame()
        points_on_the_left_edges = [(legend_frame.origin.x + square_x + 0.5,
                                     legend_frame.origin.y + legend_view.square_y + ROW_OUTLINES_LEGEND_SQUARE_SIZE / 2)
                                    for square_x in (current_tab_square_x, selected_square_x)]
        for row in (1, 2, 3):
            row_view = outline_view.rowViewAtRow_makeIfNecessary_(row, True)
            row_rect = content_view.convertRect_fromView_(row_view.bounds(), row_view)
            points_on_the_left_edges.append((row_rect.origin.x + 1, row_rect.origin.y + row_rect.size.height / 2))
        for appearance_name in (NSAppearanceNameAqua, NSAppearanceNameDarkAqua):
            self.window.setAppearance_(NSAppearance.appearanceNamed_(appearance_name))
            content_view.layoutSubtreeIfNeeded()
            bitmap = render_view_into_bitmap(content_view)
            scale = bitmap.pixelsWide() / content_view.bounds().size.width
            current_tab_square_edge, selected_square_edge, current_tab_row_edge, row_without_an_outline, selected_row_edge = (
                convert_color_components_to_0_to_255(
                    read_drawn_color_components(bitmap, scale, x, content_view.bounds().size.height - y))[:3]
                for x, y in points_on_the_left_edges)
            # Each square is far nearer its own outline, as a row draws it, than the other outline or a row without
            # one (a row's outline may blend with the row where it covers only part of a pixel).
            for square_edge, its_outline_on_a_row, other_edges in (
                    (current_tab_square_edge, current_tab_row_edge, (selected_row_edge, row_without_an_outline)),
                    (selected_square_edge, selected_row_edge, (current_tab_row_edge, row_without_an_outline))):
                for other_edge in other_edges:
                    self.assertLess(math.dist(square_edge, its_outline_on_a_row), math.dist(square_edge, other_edge) / 2,
                                    (appearance_name, square_edge, its_outline_on_a_row, other_edge))


class RememberMyChoiceTests(unittest.TestCase):
    """The "Remember my choice" checkbox of the "Are you sure?" alerts."""

    def ask_whether_to_kill(self, settings, button_chosen, remember_my_choice_is_checked):
        """Runs AppController.ask_for_confirmation before a Kill, with a stand-in app controller whose
        alert is answered with this button and the checkbox in this state; returns (the answer,
        the alerts shown, the settings each change saved)."""
        shown_alerts, saved_settings = [], []

        def answer_alert(alert):
            shown_alerts.append(alert)
            alert.suppressionButton().setState_(1 if remember_my_choice_is_checked else 0)
            return button_chosen
        stand_in_app_controller = unittest.mock.Mock(settings=settings, run_alert_in_front=answer_alert,
                                                     settings_changed=saved_settings.append)
        answer = AppController.ask_for_confirmation(stand_in_app_controller, "Kill ends the agent processes.", "kill")
        return answer, shown_alerts, saved_settings

    def test_a_remembered_yes_lets_that_action_run_without_asking_from_then_on(self):
        answer, shown_alerts, saved_settings = self.ask_whether_to_kill(dict(DEFAULT_SETTINGS), NSAlertSecondButtonReturn, True)
        self.assertTrue(answer)
        self.assertEqual(shown_alerts[0].suppressionButton().title(), "Remember my choice")
        self.assertEqual(saved_settings[0]["actions_that_run_without_asking"], ["kill"])
        answer, shown_alerts, _ = self.ask_whether_to_kill(saved_settings[0], NSAlertFirstButtonReturn, False)
        self.assertEqual((answer, shown_alerts), (True, []))  # Yes, without asking
        stand_in_app_controller = unittest.mock.Mock(settings=saved_settings[0], run_alert_in_front=lambda alert: NSAlertFirstButtonReturn)
        self.assertFalse(AppController.ask_for_confirmation(stand_in_app_controller, "Clear?", "clear"))  # other actions still ask

    def test_a_no_or_a_yes_without_the_checkbox_is_not_remembered(self):
        for button_chosen, remember_my_choice_is_checked, expected_answer in (
                (NSAlertFirstButtonReturn, True, False), (NSAlertSecondButtonReturn, False, True)):
            answer, shown_alerts, saved_settings = self.ask_whether_to_kill(
                dict(DEFAULT_SETTINGS), button_chosen, remember_my_choice_is_checked)
            self.assertEqual((answer, len(shown_alerts), saved_settings), (expected_answer, 1, []))


class CloseWindowConfirmationTests(unittest.TestCase):
    """What the "Are you sure?" alert says before a window is closed."""

    def ask_about_closing_a_window(self, total_tab_count, states, left_out_agent_tool_names=()):
        """Runs AppController.confirm_closing_window, with a stand-in app controller whose settings
        leave these agent tools out of the list, for the window "work" of total_tab_count tabs
        whose agent rows show these states; returns what it asks with."""
        stand_in_app_controller = unittest.mock.Mock(settings=dict(DEFAULT_SETTINGS, agent_tools=[
            dict(agent_tool, is_included=agent_tool["agent_tool_name"] not in left_out_agent_tool_names)
            for agent_tool in DEFAULT_SETTINGS["agent_tools"]]))
        window_entry = WindowEntry("w1", "work", False, (0, 0, 800, 600), total_tab_count, tuple(
            make_entry(str(index), f"tab-{index}", state) for index, state in enumerate(states)))
        AppController.confirm_closing_window(stand_in_app_controller, window_entry)
        stand_in_app_controller.ask_for_confirmation.assert_called_once()
        explanation, action_name = stand_in_app_controller.ask_for_confirmation.call_args.args
        self.assertEqual(action_name, "close_window")
        return explanation

    def test_an_agent_that_has_ended_isnt_counted_as_running(self):
        self.assertEqual(self.ask_about_closing_a_window(2, ["working", "idle"]),
                         'Closing "work" ends the 2 agent sessions running in it.')
        # The tab of an "exited" row has only its shell left: it is one of the window's other tabs.
        self.assertEqual(self.ask_about_closing_a_window(3, ["working", "exited"]),
                         'Closing "work" ends the 1 agent session running in it and closes its 2 other tabs.')
        self.assertEqual(self.ask_about_closing_a_window(1, ["exited"]),
                         'Closing "work" closes its 1 tab, where no listed agent session is running anymore.')

    def test_the_sessions_of_agent_tools_left_out_of_the_list_are_said_to_end_too(self):
        # Their tabs are among the window's other tabs, since the window's rows don't hold them.
        ends_those_left_out_too = " session in the window ends too: Settings > Harnesses leaves those out of the list."
        self.assertEqual(self.ask_about_closing_a_window(3, ["working"], left_out_agent_tool_names=("Codex",)),
                         'Closing "work" ends the 1 agent session running in it and closes its 2 other tabs. Any Codex'
                         + ends_those_left_out_too)
        self.assertEqual(self.ask_about_closing_a_window(1, ["exited"], left_out_agent_tool_names=("Codex", "agy")),
                         'Closing "work" closes its 1 tab, where no listed agent session is running anymore. Any Codex or agy'
                         + ends_those_left_out_too)
        # Every tab has a running row: none is left for a session the list doesn't show.
        self.assertEqual(self.ask_about_closing_a_window(2, ["working", "idle"], left_out_agent_tool_names=("Codex",)),
                         'Closing "work" ends the 2 agent sessions running in it.')


class AutomaticIconColorTests(unittest.TestCase):
    def snapshot_with_states(self, states):
        window = WindowEntry("w1", "work", False, (0, 0, 800, 600), len(states), tuple(
            make_entry(str(index), f"tab-{index}", state) for index, state in enumerate(states)))
        return replace_windows_of_the_first_monitor(make_sample_snapshot(), (window,))

    def test_most_urgent_agent_session_picks_the_color(self):
        for states, expected_color_name in (
                (["idle", "working", "asking", "error"], "light_blue"),
                (["working", "error", "done"], "red"),
                (["working", "idle", "done", "unknown"], "green"),
                ([], "green")):
            self.assertEqual(choose_icon_color_name(self.snapshot_with_states(states)), expected_color_name)


class MenuBarIconClickAndDropdownTests(unittest.TestCase):
    """What a click on the menu bar icon, the global shortcut and Esc do to the dropdown, the
    sidebar and the list window, and who gets the keyboard back."""

    def test_every_click_toggles_the_dropdown_even_one_that_closed_it_on_the_way_down(self):
        for dropdown_is_shown, dropdown_closed_at, button_pressed_at, expected in (
                (True, 0, 100.0, "close"),  # a quick click: still closing at the mouse-up
                (False, 100.02, 100.0, "nothing"),  # a slow one: it closed at this click's mouse-down
                (False, 90.0, 100.0, "open"),  # closed earlier, by a click elsewhere
                (False, 0, 100.0, "open"),  # never open yet
                (False, 100.02, 0, "open")):  # the mouse-down wasn't seen: as before
            self.assertEqual(choose_what_a_click_on_the_icon_does_to_the_dropdown(
                dropdown_is_shown, dropdown_closed_at, button_pressed_at), expected,
                (dropdown_is_shown, dropdown_closed_at, button_pressed_at))

    def test_a_press_of_the_icon_without_the_mouse_opens_the_dropdown_whatever_an_earlier_click_left(self):
        # The accessibility "Press" action, which VoiceOver sends, has no event of its own: the current event is None
        # before the app's first event, else the last event the app took (here a key). Without the mouse-down a click
        # begins with, the time of an earlier click's mouse-down would keep the press from opening a dropdown that
        # closed since that click.
        status_item_controller = MenuBarStatusItemController.alloc().init()  # no status item of its own
        status_item_controller.owner = unittest.mock.Mock(menu_bar_icon_clicked=status_item_controller.toggle_popover)
        status_item_controller.popover = unittest.mock.Mock(isShown=lambda: False)
        status_item_controller.dropdown_closed_at = 100.02
        for current_event, opens_the_dropdown in (
                (None, True), (unittest.mock.Mock(type=lambda: NSEventTypeKeyDown), True),
                # A click's own mouse-up still leaves closed the dropdown that its mouse-down closed.
                (unittest.mock.Mock(type=lambda: NSEventTypeLeftMouseUp), False)):
            status_item_controller.left_button_pressed_at = 100.0  # the mouse-down of a click
            with unittest.mock.patch("agent_command_tab.user_interface.menu_bar_status_item.NSApp",
                                     unittest.mock.Mock(currentEvent=lambda: current_event)), \
                    unittest.mock.patch.object(status_item_controller, "show_popover") as show_popover:
                status_item_controller.statusItemClicked_(None)
            self.assertEqual(show_popover.call_count, int(opens_the_dropdown), current_event)

    def test_a_click_closes_settings_and_slides_an_auto_hiding_sidebar_in_or_out_and_leaves_one_that_stays_visible(self):
        calls = []

        class StandInAppController:
            sidebar_controller = unittest.mock.Mock(show_for_a_few_seconds=lambda: calls.append("show"),
                                                    hide_now=lambda: calls.append("hide"),
                                                    show_again=lambda: calls.append("show for good"))
            # Closed first, so a dropdown it kept open closes too.
            settings_window_controller = unittest.mock.Mock(close_if_open=lambda: calls.append("close settings"))

            application_in_front_before_dropdown = "iTerm2"

            def __init__(self):
                self.dropdown_is_shown = True  # one the menu's Show popup opened
                self.popover = unittest.mock.Mock(isShown=lambda: self.dropdown_is_shown)

            def retry_the_global_shortcut_if_it_lacks_permission(self):
                pass

            def close_dropdown(self):
                calls.append("close dropdown")
                self.dropdown_is_shown = False

            def give_activation_to(self, application):
                calls.append(f"give the keyboard to {application}")

            def close_a_dropdown_the_menu_showed(self):
                return AppController.close_a_dropdown_the_menu_showed(self)

            def show_the_sidebar(self):
                AppController.show_the_sidebar(self)

            def show_the_list_window(self):
                calls.append("show window")
        stand_in_app_controller = StandInAppController()
        # One that stays visible stays, or comes back if the menu's Show popup or Show window hid it.
        for sidebar_mode, sidebar_is_shown, expected_call in (("auto_hide", False, "show"), ("auto_hide", True, "hide"),
                                                              ("floating", True, "show for good"),
                                                              ("docked", False, "show for good")):
            stand_in_app_controller.settings = {"show_as": "sidebar", "sidebar_mode": sidebar_mode}
            stand_in_app_controller.sidebar_controller.is_shown = sidebar_is_shown
            stand_in_app_controller.dropdown_is_shown = True
            calls.clear()
            AppController.menu_bar_icon_clicked(stand_in_app_controller)
            self.assertEqual(calls, ["close settings", "close dropdown", expected_call], sidebar_mode)
        stand_in_app_controller.settings = {"show_as": "window", "sidebar_mode": "auto_hide"}
        stand_in_app_controller.dropdown_is_shown = True
        calls.clear()
        AppController.menu_bar_icon_clicked(stand_in_app_controller)
        self.assertEqual(calls, ["close settings", "close dropdown", "show window"])
        # The global shortcut does so too, and makes the app that had the keyboard before the
        # dropdown active again, since the sidebar never makes the dashboard active; the sidebar
        # then takes the keyboard, for Return, until it gives it back.
        stand_in_app_controller.settings = {"show_as": "sidebar", "sidebar_mode": "auto_hide"}
        stand_in_app_controller.sidebar_controller.is_shown = False
        stand_in_app_controller.sidebar_controller.has_the_keyboard = False
        stand_in_app_controller.sidebar_controller.take_the_keyboard = lambda: calls.append("take the keyboard")
        stand_in_app_controller.list_window_controller = unittest.mock.Mock(is_in_front=lambda: False)
        stand_in_app_controller.dropdown_is_shown = True
        calls.clear()
        AppController.global_shortcut_pressed(stand_in_app_controller)
        self.assertEqual(calls, ["close dropdown", "give the keyboard to iTerm2", "show", "take the keyboard"])
        # The sidebar it shows has the first agent asking a question selected,
        # or while none asks, the previous tab.
        stand_in_app_controller.sidebar_controller.list_controller.select_the_first_asking_tab_or_else_the_previous_tab.assert_called_once_with()
        # A sidebar that stays visible gets the keyboard, and pressed again, gives it back.
        stand_in_app_controller.settings = {"show_as": "sidebar", "sidebar_mode": "floating"}
        stand_in_app_controller.sidebar_controller.is_shown = True
        stand_in_app_controller.sidebar_controller.give_back_the_keyboard = lambda: calls.append("give back the keyboard")
        for sidebar_has_the_keyboard, expected_calls in ((False, ["take the keyboard"]), (True, ["give back the keyboard"])):
            stand_in_app_controller.sidebar_controller.has_the_keyboard = sidebar_has_the_keyboard
            calls.clear()
            AppController.global_shortcut_pressed(stand_in_app_controller)
            self.assertEqual(calls, expected_calls, sidebar_has_the_keyboard)

    def test_a_row_dragged_in_the_dropdown_keeps_it_open_when_the_dashboard_stops_being_the_active_app(self):
        # A drag usually starts as a hold, so it ends a peek right away, and macOS told the
        # dashboard that the peek had made it inactive only after that peek was over, which closed
        # the dropdown in the middle of the drag.
        for drag_in_progress, settings_window_is_open, expected_behavior, closes_the_dropdown in (
                (True, False, NSPopoverBehaviorApplicationDefined, False),
                (False, False, NSPopoverBehaviorTransient, True),
                (False, True, NSPopoverBehaviorApplicationDefined, True)):  # Settings keeps it open only for clicks in the dashboard
            stand_in_app_controller = make_stand_in_app_controller_with_real_methods(
                ["a_peek_or_a_drag_keeps_the_dropdown_open"], peek_in_progress=None, peek_waiting_for_put_back=None)
            stand_in_app_controller.dropdown_list_controller.drag_in_progress = drag_in_progress
            stand_in_app_controller.settings_window_controller.is_open.return_value = settings_window_is_open
            AppController.update_dropdown_closing_behavior(stand_in_app_controller)
            stand_in_app_controller.popover.setBehavior_.assert_called_once_with(expected_behavior)
            # .callable: the Python function behind the AppKit method, which won't take a stand-in as self.
            AppController.dashboardResignedActive_.callable(stand_in_app_controller, None)
            self.assertEqual(stand_in_app_controller.close_dropdown.called, closes_the_dropdown)

    def test_esc_in_the_sidebar_gives_the_keyboard_back(self):
        # (SidebarController.give_back_the_keyboard also slides out one that hides by itself.)
        stand_in_app_controller = unittest.mock.Mock()
        AppController.close_the_list_after_escape(stand_in_app_controller, stand_in_app_controller.sidebar_controller.list_controller)
        stand_in_app_controller.sidebar_controller.give_back_the_keyboard.assert_called_once_with()
        stand_in_app_controller.give_activation_to.assert_called_once_with(stand_in_app_controller.last_other_active_application)

    def test_a_tab_shown_from_the_sidebar_with_the_keyboard_gives_the_keyboard_back(self):
        # So the shortcut, pressed again, shows it with the keyboard rather than hiding it.
        for sidebar_has_the_keyboard in (True, False):
            stand_in_app_controller = unittest.mock.Mock()
            stand_in_app_controller.sidebar_controller.has_the_keyboard = sidebar_has_the_keyboard
            AppController.tab_was_focused(stand_in_app_controller, stand_in_app_controller.sidebar_controller.list_controller)
            self.assertEqual(stand_in_app_controller.sidebar_controller.give_back_the_keyboard.called, sidebar_has_the_keyboard)

    def test_another_app_made_active_takes_the_keyboard_from_the_sidebar_but_the_one_it_came_from_doesnt(self):
        def make_application(process_id):
            application = unittest.mock.Mock()
            application.processIdentifier.return_value = process_id
            return application
        for activated_process_id, gives_back in ((70, True), (50, False), (os.getpid(), False)):  # 50: the app it had the keyboard from
            stand_in_app_controller = make_stand_in_app_controller_with_real_methods(
                ["note_application_unless_it_is_the_dashboard"], peek_in_progress=None, peek_waiting_for_put_back=None)
            stand_in_app_controller.last_other_active_application = make_application(50)
            stand_in_app_controller.sidebar_controller.has_the_keyboard = True
            notification = unittest.mock.Mock()
            notification.userInfo.return_value = {NSWorkspaceApplicationKey: make_application(activated_process_id)}
            AppController.anotherApplicationBecameActive_.callable(stand_in_app_controller, notification)
            self.assertEqual(stand_in_app_controller.sidebar_controller.give_back_the_keyboard.called, gives_back, activated_process_id)

    def test_a_dropdown_opened_over_the_dashboards_own_window_gives_the_keyboard_to_the_last_other_app(self):
        # With the list window that Show popup hid in front, or the settings window that a click on
        # the icon closed, the dashboard itself was the frontmost app, and Esc gave the keyboard to
        # it with no window left; a settings, Saved sessions or list window still open gets it back.
        for application_in_front_is_the_dashboard, open_window, gets_the_last_other_app in (
                (True, None, True), (True, "settings", False), (True, "saved sessions", False), (True, "list", False),
                (False, None, False)):
            stand_in_app_controller = unittest.mock.Mock()
            stand_in_app_controller.settings_window_controller.is_open.return_value = open_window == "settings"
            stand_in_app_controller.saved_sessions_window_controller.is_open.return_value = open_window == "saved sessions"
            stand_in_app_controller.list_window_controller.window.isVisible.return_value = open_window == "list"
            application_in_front = unittest.mock.Mock()
            application_in_front.processIdentifier.return_value = os.getpid() if application_in_front_is_the_dashboard else 1
            AppController.note_application_in_front_before_dropdown(stand_in_app_controller, application_in_front)
            self.assertIs(stand_in_app_controller.application_in_front_before_dropdown,
                          stand_in_app_controller.last_other_active_application if gets_the_last_other_app
                          else application_in_front)


class AgentSessionStartsInTheAppTests(unittest.TestCase):
    """New tab, New window and Duplicate: a failure is reported, a new window is selected once it
    shows, and the follow-up of a successful action."""

    def test_new_tab_new_window_and_duplicate_report_a_failure(self):
        # They failed silently, a tab's folder deleted since, say.
        stand_in_app_controller = unittest.mock.Mock(latest_snapshot_lock=threading.Lock(), latest_snapshot=None)
        AppController.duplicate_agent_session(stand_in_app_controller, "t1", "/gone", "Claude Code")
        AppController.start_agent_session_from_menu(stand_in_app_controller, "w1", "/gone", "Claude Code")
        AppController.start_agent_session_from_menu(stand_in_app_controller, None, None, "Claude Code")
        self.assertEqual([(call.args[0].action, call.args[1])
                          for call in stand_in_app_controller.submit_action_and_report_failure.call_args_list],
                         [("duplicate_agent_session", "The tab wasn't duplicated"), ("start_agent_session", "The agent wasn't started"),
                          ("start_agent_session", "The agent wasn't started")])
        stand_in_app_controller.submit_action.assert_not_called()

    def test_a_new_window_is_selected_in_every_list_once_it_shows(self):
        stand_in_app_controller = unittest.mock.Mock(latest_snapshot_lock=threading.Lock(), latest_snapshot=None)
        AppController.start_agent_session_from_menu(stand_in_app_controller, None, None, "Claude Code")
        self.assertIs(stand_in_app_controller.submit_action_and_report_failure.call_args.args[2],
                      stand_in_app_controller.select_the_new_window_once_it_shows)
        list_controllers = [unittest.mock.Mock(), unittest.mock.Mock(), unittest.mock.Mock()]
        stand_in_app_controller.list_all_list_controllers.return_value = list_controllers
        AppController.select_the_new_window_once_it_shows(stand_in_app_controller, ActionResult(True, details={"window_id": "w9"}))
        for list_controller in list_controllers:
            list_controller.select_window_once_it_shows.assert_called_once_with("w9")

    def test_after_success_runs_only_once_the_action_succeeded(self):
        for action_result, runs_after_success in ((ActionResult(True), True), (ActionResult(False, "the folder is gone"), False)):
            stand_in_app_controller = unittest.mock.Mock()
            make_actions_finish_with(stand_in_app_controller, action_result)
            after_success = unittest.mock.Mock()
            with run_main_thread_calls_at_once():
                AppController.submit_action_and_report_failure(stand_in_app_controller, ActionRequest("start_agent_session", {}),
                                                               "The agent wasn't started", after_success)
            self.assertEqual(after_success.call_args_list, [unittest.mock.call(action_result)] if runs_after_success else [])
            self.assertEqual(stand_in_app_controller.report_action_failure.call_args_list,
                             [] if runs_after_success else [unittest.mock.call(action_result, "The agent wasn't started")])


class AgentToolIconTests(unittest.TestCase):
    def test_codex_shows_the_openai_logo_in_the_names_color(self):
        if NSImage.alloc().initWithData_(b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1"><path d="M0 0h1v1z"/></svg>') is None:
            self.skipTest("this macOS can't read SVG, so Codex has no logo and the list shows its name as an image")
        logo = build_agent_tool_icons()["Codex"]
        self.assertEqual(tuple(logo.size()), (12, 12))
        for appearance_name in (NSAppearanceNameAqua, NSAppearanceNameDarkAqua):
            # Drawn at four times its size, so that its lines are several pixels wide on any display.
            image_view = NSImageView.alloc().initWithFrame_(NSMakeRect(0, 0, 48, 48))
            image_view.setImageScaling_(NSImageScaleProportionallyUpOrDown)
            image_view.setImage_(logo)
            image_view.setAppearance_(NSAppearance.appearanceNamed_(appearance_name))
            bitmap = render_view_into_bitmap(image_view)
            colors = [convert_color_components_to_0_to_255(read_srgb_components(bitmap.colorAtX_y_(x, y)))
                      for x in range(bitmap.pixelsWide()) for y in range(bitmap.pixelsHigh())]
            drawn_colors = [color for color in colors if color[3] > 127]  # more than half covered
            self.assertGreater(len(drawn_colors), len(colors) / 5, appearance_name)
            self.assertLess(len(drawn_colors), len(colors) / 2, appearance_name)  # the logo's shape, not the whole square
            # Inside its lines the logo has labelColor, the names' color, as translucent as that is (a
            # translucent black in light mode and a translucent white in dark mode), not laid over
            # the black the SVG is drawn in.
            color_inside_the_lines = max(set(drawn_colors), key=drawn_colors.count)  # the most common one
            for drawn_component, expected_component in zip(color_inside_the_lines,
                                                           resolve_color(NSColor.labelColor(), appearance_name)):
                self.assertAlmostEqual(drawn_component, expected_component, delta=2, msg=appearance_name)

    def test_the_app_icon_is_a_command_key_with_a_status_dot_in_each_loop(self):
        icon = build_app_icon()
        self.assertEqual(tuple(icon.size()), (1024, 1024))
        image_view = NSImageView.alloc().initWithFrame_(NSMakeRect(0, 0, 128, 128))  # a dialog's 64 points on a Retina screen
        image_view.setImageScaling_(NSImageScaleProportionallyUpOrDown)
        image_view.setImage_(icon)
        bitmap = render_view_into_bitmap(image_view)
        scale = bitmap.pixelsWide() / 1024

        def read_color(x, y_from_the_top):
            return convert_color_components_to_0_to_255(read_drawn_color_components(bitmap, scale, x, y_from_the_top))
        # The loops' centers are 204 points from the middle; the top left is 716 points up, 308 from the top.
        top_left, top_right, bottom_left, bottom_right = (read_color(x, y) for x, y in ((308, 308), (716, 308), (308, 716), (716, 716)))
        self.assertTrue(top_left[0] > 200 and top_left[1] > 150 and top_left[2] < 80, top_left)  # amber
        self.assertTrue(top_right[2] > 200 and top_right[0] < 120, top_right)  # blue
        self.assertTrue(abs(bottom_left[0] - bottom_left[2]) < 20 and 100 < bottom_left[0] < 180, bottom_left)  # gray
        self.assertTrue(bottom_right[1] > 160 and bottom_right[0] < 120, bottom_right)  # green
        self.assertEqual(read_color(20, 20)[3], 0)  # outside the rounded square: clear


class SidebarControllerTests(unittest.TestCase):
    """The sidebar: dragging its width, showing and hiding it at its edge, and dragging its top bar."""

    def setUp(self):
        self.owner = RecordingOwner()
        self.owner.saved_widths = []
        self.owner.sidebar_width_changed = self.owner.saved_widths.append
        self.controller = SidebarController.alloc().initWithOwner_(self.owner)
        self.controller.list_controller.refresh_timer.invalidate()
        # Set up by hand rather than through apply_settings, so no window appears on screen.
        self.controller.settings = dict(DEFAULT_SETTINGS, show_as="sidebar", sidebar_mode="floating")
        self.controller.screens = [BUILT_IN_SCREEN]
        self.controller.placement = SidebarPlacement(0, "right", True)
        self.controller.is_shown = True
        self.controller.window.setFrame_display_(NSMakeRect(1100, 0, 400, 970), False)
        self.controller.move_width_drag_strip_to_inner_edge("right")
        self.owner.reserve_screen_area = lambda area: None
        # Whatever a test shows or starts goes away after it (each does nothing when there is nothing to undo).
        self.addCleanup(self.controller.window.orderOut_, None)
        self.addCleanup(self.controller.watch_for_clicks_in_other_apps, False)
        self.addCleanup(self.controller.stop_tracking_the_pointer)
        self.addCleanup(self.controller.cancel_hide_timer)

    def patch_screens_to_one_far_away(self):
        """Returns a patch under which the sidebar finds one screen, far outside every monitor, so
        a sidebar shown then stays out of sight."""
        return unittest.mock.patch("agent_command_tab.user_interface.sidebar_panel.read_monitors_from_screens",
                                   lambda: [FAR_AWAY_SCREEN])

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

    def test_the_edge_shows_an_auto_hiding_sidebar_only_beside_it(self):
        # In the menu bar's band or a bottom Dock's, the pointer was never over the sidebar it
        # showed, which hid again and again.
        self.controller.settings = dict(self.controller.settings, sidebar_mode="auto_hide")
        self.controller.screens = [{"name": "Built-in", "frame": (0, 0, 1500, 1000), "visible_frame": (0, 60, 1500, 910)}]
        self.controller.is_shown = False
        shown = []
        self.controller.set_shown = shown.append
        for pointer_y, shows in ((990, False), (30, False), (500, True)):  # the menu bar, the Dock, beside the sidebar
            shown.clear()
            with unittest.mock.patch("agent_command_tab.user_interface.sidebar_panel.NSEvent") as stand_in_event_class:
                stand_in_event_class.mouseLocation.return_value = NSMakePoint(1499.5, pointer_y)
                self.controller.pointer_moved()
            self.assertEqual(shown, [True] if shows else [], pointer_y)

    def test_a_click_without_movement_saves_nothing(self):
        self.controller.start_width_drag(1100)
        self.controller.finish_width_drag()
        self.assertEqual(self.owner.saved_widths, [])

    def test_a_sidebar_wider_than_its_monitor_has_room_for_is_narrower_there_and_keeps_its_width_in_the_settings(self):
        # At its 1200 points on a monitor 1000 wide, a docked sidebar's area would begin left of the monitor and leave
        # the terminal windows beside it less than no width.
        narrow_screen = {"name": "Narrow", "frame": (-4000, -4000, 1000, 800), "visible_frame": (-4000, -4000, 1000, 780)}
        reserved_areas = []
        self.owner.reserve_screen_area = reserved_areas.append

        def read_x_and_width():
            frame = self.controller.window.frame()
            return frame.origin.x, frame.size.width
        with unittest.mock.patch("agent_command_tab.user_interface.sidebar_panel.read_monitors_from_screens", lambda: [narrow_screen]):
            self.controller.apply_settings(dict(self.controller.settings, sidebar_mode="docked", sidebar_width=1200))
            self.assertEqual(reserved_areas[-1]["frame"], (-3600, -4000, 600, 780))  # 400 points stay the terminal windows'
            self.assertEqual(read_x_and_width(), (-3600, 600))
            # A click on its inner edge doesn't save that width over the settings' wider one, kept for a wider monitor,
            self.controller.start_width_drag(-3600)
            self.controller.finish_width_drag()
            self.assertEqual(self.owner.saved_widths, [])
            # and the edge drags it no wider than the monitor has room for.
            self.controller.start_width_drag(-3600)
            self.controller.continue_width_drag(-3950)
            self.assertEqual(read_x_and_width(), (-3600, 600))
            self.controller.continue_width_drag(-3500)
            self.controller.finish_width_drag()
            self.assertEqual(self.owner.saved_widths, [500])
            # One that lies over the windows fills the monitor at most.
            self.controller.apply_settings(dict(self.controller.settings, sidebar_mode="floating"))
            self.assertEqual(read_x_and_width(), (-4000, 1000))
            self.assertIsNone(reserved_areas[-1])
            # So does the one "Show sidebar" slides in while the list shows as a popup, though the sidebar's mode in the
            # settings is still the docked one: it keeps no room for the windows it lies over.
            self.controller.apply_settings(dict(self.controller.settings, show_as="popup", sidebar_mode="docked"))
            self.controller.set_shown(True, animate=False)  # as "Show sidebar" does, without sliding
            self.assertEqual(read_x_and_width(), (-4000, 1000))
            self.assertIsNone(reserved_areas[-1])

    def test_a_visible_sidebar_changed_to_auto_hide_is_checked_for_hiding_though_the_pointer_doesnt_move(self):
        # Changed in Settings with the keyboard, the pointer already off the sidebar: without a check started here, it
        # would stay in until the pointer next moves.
        with self.patch_screens_to_one_far_away():
            self.controller.apply_settings(dict(self.controller.settings, sidebar_mode="floating"))
            self.assertTrue(self.controller.is_shown)
            self.assertIsNone(self.controller.hide_timer)
            with unittest.mock.patch.object(self.controller, "pointer_is_over_sidebar", return_value=False):
                self.controller.apply_settings(dict(self.controller.settings, sidebar_mode="auto_hide"))
            self.assertTrue(self.controller.is_shown)  # until the check, which leaves it in while a rename or a menu is open in it
            self.assertIsNotNone(self.controller.hide_timer)
            # With the pointer on it (its width just dragged), the pointer starts the check as it leaves.
            self.controller.cancel_hide_timer()
            with unittest.mock.patch.object(self.controller, "pointer_is_over_sidebar", return_value=True):
                self.controller.apply_settings(dict(self.controller.settings, sidebar_width=450))
            self.assertTrue(self.controller.is_shown)
            self.assertIsNone(self.controller.hide_timer)

    def test_the_shortcut_slides_it_out_at_once_even_while_it_was_to_stay_a_few_seconds(self):
        self.controller.keep_shown_until = time.time() + 4
        self.controller.check_whether_to_hide_in(4)
        self.controller.hide_now()
        self.assertFalse(self.controller.is_shown)
        self.assertIsNone(self.controller.hide_timer)
        self.assertEqual(self.controller.keep_shown_until, 0)

    def test_a_sidebar_that_hides_by_itself_watches_for_clicks_in_other_apps_only_while_it_is_in(self):
        # A screen far outside every monitor, so the sidebar shown here stays out of sight.
        self.controller.screens = [FAR_AWAY_SCREEN]
        for settings_change, hides_by_itself in (({"show_as": "sidebar", "sidebar_mode": "auto_hide"}, True),
                                                 ({"show_as": "popup", "sidebar_mode": "floating"}, True),  # "Show sidebar"
                                                 ({"show_as": "sidebar", "sidebar_mode": "floating"}, False),
                                                 ({"show_as": "sidebar", "sidebar_mode": "docked"}, False)):
            self.controller.settings = dict(self.controller.settings, **settings_change)
            self.controller.set_shown(True, animate=False)
            self.assertEqual(self.controller.click_monitor is not None, hides_by_itself, settings_change)
            self.controller.hide_now(animate=False)
            self.assertIsNone(self.controller.click_monitor)
            self.assertFalse(self.controller.window.isVisible())  # gone at once, without sliding

    def test_the_keyboard_the_shortcut_gives_it_goes_to_its_list_and_keeps_it_in_until_given_back(self):
        self.controller.screens = [FAR_AWAY_SCREEN]
        # The core is told, so it goes on marking the tab you came from as the one in front.
        keyboard_reports = []
        self.owner.tell_the_core_whether_the_dashboard_has_the_keyboard = lambda: keyboard_reports.append(self.controller.has_the_keyboard)
        for sidebar_mode, hides_by_itself in (("auto_hide", True), ("floating", False), ("docked", False)):
            self.controller.settings = dict(self.controller.settings, sidebar_mode=sidebar_mode)
            self.controller.set_shown(True, animate=False)
            keyboard_reports.clear()
            self.controller.take_the_keyboard()
            self.assertTrue(self.controller.has_the_keyboard, sidebar_mode)
            self.assertEqual(keyboard_reports, [True], sidebar_mode)
            self.assertIs(self.controller.window.firstResponder(), self.controller.list_controller.outline_view)  # Return, the arrows, Esc
            self.assertTrue(self.controller.something_belonging_to_the_sidebar_is_open())  # an auto-hiding one stays in
            self.assertIsNotNone(self.controller.click_monitor)  # a click in another app takes the keyboard
            with unittest.mock.patch.object(self.controller, "hide_now") as hide_now:
                self.controller.give_back_the_keyboard()
            self.assertFalse(self.controller.has_the_keyboard)
            self.assertEqual(keyboard_reports, [True, False], sidebar_mode)
            self.assertEqual(hide_now.called, hides_by_itself, sidebar_mode)
            if not hides_by_itself:  # it stays, having left the screen for an instant, which gives the keyboard back
                self.assertTrue(self.controller.window.isVisible())
                self.assertFalse(self.controller.window.isKeyWindow())
                self.assertIsNone(self.controller.click_monitor)
            self.controller.hide_now(animate=False)

    def test_the_search_box_taking_the_keyboard_keeps_the_sidebar_in_until_another_app_is_clicked(self):
        self.controller.screens = [FAR_AWAY_SCREEN]
        self.controller.set_shown(True, animate=False)  # a floating sidebar, which stays visible
        keyboard_reports = []
        self.owner.tell_the_core_whether_the_dashboard_has_the_keyboard = lambda: keyboard_reports.append(self.controller.has_the_keyboard)
        self.assertTrue(self.controller.window.makeFirstResponder_(self.controller.list_panel_controller.search_field))  # as a click does
        self.assertTrue(self.controller.has_the_keyboard)
        self.assertEqual(keyboard_reports, [True])
        self.assertTrue(self.controller.something_belonging_to_the_sidebar_is_open())  # an auto-hiding one would stay in
        self.assertIsNotNone(self.controller.click_monitor)
        self.controller.another_app_was_clicked()
        self.assertFalse(self.controller.has_the_keyboard)
        self.assertEqual(keyboard_reports, [True, False])
        # The box let go of the keyboard too, so a second click in it gives the sidebar the keyboard again.
        self.assertIs(self.controller.window.firstResponder(), self.controller.list_controller.outline_view)
        self.assertTrue(self.controller.window.makeFirstResponder_(self.controller.list_panel_controller.search_field))
        self.assertTrue(self.controller.has_the_keyboard)
        self.controller.hide_now(animate=False)

    def test_a_search_box_that_hides_while_it_has_the_keyboard_leaves_the_keyboard_to_the_sidebars_rows(self):
        # Emptied beside a message the other controls stepped aside for, the box steps aside too. The sidebar's panel
        # makes the list its first responder only in the sidebar's own way, so the panel asks the sidebar to.
        self.controller.screens = [FAR_AWAY_SCREEN]
        self.controller.set_shown(True, animate=False)
        panel = self.controller.list_panel_controller
        self.assertTrue(panel.put_the_keyboard_in_the_search_box())  # as Command-F does
        panel.search_field.setStringValue_("planning")
        panel.searchFieldChanged_(panel.search_field)
        panel.show_message("iTerm2 can't be reached; retrying")
        self.assertFalse(panel.search_field.isHidden())  # it holds a search
        self.controller.list_controller.clear_the_search()
        self.assertTrue(panel.search_field.isHidden())
        self.assertIs(self.controller.window.firstResponder(), self.controller.list_controller.outline_view)
        self.assertTrue(self.controller.has_the_keyboard)  # Return, the arrows and Esc now go to the rows
        self.controller.hide_now(animate=False)

    def test_giving_the_keyboard_back_ends_an_open_rename_without_renaming(self):
        self.controller.screens = [FAR_AWAY_SCREEN]
        self.controller.set_shown(True, animate=False)
        list_controller = self.controller.list_controller
        list_controller.apply_snapshot(make_sample_snapshot())
        self.controller.window.contentView().layoutSubtreeIfNeeded()
        self.controller.take_the_keyboard()
        list_controller.begin_rename(list_controller.outline_view.itemAtRow_(1))  # R pressed after the shortcut
        list_controller.outline_view.viewAtColumn_row_makeIfNecessary_(0, 1, False).name_text_field.setStringValue_("half-typ")
        self.controller.give_back_the_keyboard()  # another app became active
        self.assertFalse(list_controller.rename_in_progress)
        self.assertEqual([action_request.action for action_request in self.owner.submitted_actions if "rename" in action_request.action], [])
        self.controller.hide_now(animate=False)

    def test_a_rename_begun_from_a_rows_menu_gives_a_sidebar_that_stays_visible_the_keyboard_until_it_is_given_back(self):
        # The rename makes the sidebar the key window without the shortcut having given it the keyboard. Not noted
        # as having it, the sidebar left the core told that the dashboard had no keyboard, and didn't watch for the
        # click in another app that takes the keyboard from it.
        self.controller.screens = [FAR_AWAY_SCREEN]
        self.controller.set_shown(True, animate=False)  # a floating sidebar, which stays visible
        keyboard_reports = []
        self.owner.tell_the_core_whether_the_dashboard_has_the_keyboard = lambda: keyboard_reports.append(self.controller.has_the_keyboard)
        list_controller = self.controller.list_controller
        list_controller.apply_snapshot(make_sample_snapshot())
        self.controller.window.contentView().layoutSubtreeIfNeeded()
        build_right_click_menu(list_controller, list_controller.outline_view, 1)
        list_controller.menuRename_(None)
        self.assertTrue(list_controller.rename_in_progress)
        self.assertTrue(self.controller.has_the_keyboard)
        self.assertEqual(keyboard_reports, [True])
        self.assertIsNotNone(self.controller.click_monitor)
        type_into_the_open_rename_editor(list_controller.outline_view, 1, "typed but cancelled")
        self.controller.another_app_was_clicked()  # ends the rename without renaming, as giving the keyboard back does
        self.assertFalse(list_controller.rename_in_progress)
        self.assertEqual(self.owner.submitted_actions, [])
        self.assertFalse(self.controller.has_the_keyboard)
        self.assertEqual(keyboard_reports, [True, False])
        self.assertIsNone(self.controller.click_monitor)
        # A rename ended with Return leaves the sidebar the key window, its rows taking the keys: it still has the
        # keyboard, until it gives it back.
        list_controller.menuRename_(None)
        self.controller.window.sendEvent_(make_key_down_event(self.controller.window, "\r", 36))
        self.assertFalse(list_controller.rename_in_progress)
        self.assertIs(self.controller.window.firstResponder(), list_controller.outline_view)
        self.assertTrue(self.controller.has_the_keyboard)
        self.controller.give_back_the_keyboard()
        self.assertEqual(keyboard_reports, [True, False, True, False])
        # A sidebar that hides by itself is left as it was: it slides out once the rename has ended and the pointer
        # has left, which gives the keyboard back; noted as having the keyboard, it would stay in instead.
        self.controller.settings = dict(self.controller.settings, sidebar_mode="auto_hide")
        list_controller.menuRename_(None)
        self.assertTrue(list_controller.rename_in_progress)
        self.assertFalse(self.controller.has_the_keyboard)
        self.controller.hide_now(animate=False)

    def test_a_click_in_another_app_takes_the_keyboard_from_a_sidebar_that_stays_visible(self):
        self.controller.screens = [FAR_AWAY_SCREEN]
        self.controller.set_shown(True, animate=False)
        self.controller.take_the_keyboard()
        self.controller.another_app_was_clicked()
        self.assertFalse(self.controller.has_the_keyboard)
        self.assertTrue(self.controller.is_shown)
        self.assertIsNone(self.controller.click_monitor)

    def test_once_slid_out_it_no_longer_has_the_keyboard(self):
        self.controller.screens = [FAR_AWAY_SCREEN]
        self.controller.settings = dict(self.controller.settings, sidebar_mode="auto_hide")
        self.controller.set_shown(True, animate=False)
        self.controller.take_the_keyboard()
        self.controller.hide_now(animate=False)
        self.assertFalse(self.controller.has_the_keyboard)

    def test_its_top_bar_dragged_turns_it_into_the_list_window_at_the_windows_last_size(self):
        # With Show as: Sidebar, settings_changed shows the window. With Show as: Window already
        # ("Show sidebar" from the menu while the window was closed), top_bar_dragged has to.
        self.controller.screens = [FAR_AWAY_SCREEN]
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

            def close_dropdown(self):
                pass

            def place_the_settings_and_saved_sessions_windows_above_the_lists(self, dropdown_is_open):
                self.settings_window_placements += 1
        stand_in_app_controller = StandInAppController()
        stand_in_app_controller.list_window_controller = list_window_controller
        # Never shown, the window takes the size it was built with (480 by 560 inside the frame); then its saved size.
        saved_frame_rect = list_window_controller.window.frameRectForContentRect_(NSMakeRect(0, 0, 300, 400))
        saved_frame = (saved_frame_rect.origin.x, saved_frame_rect.origin.y, saved_frame_rect.size.width,
                       saved_frame_rect.size.height)
        for show_as_before, show_call_count, list_window_frame, (content_width, content_height) in (
                ("sidebar", 0, None, (480, 560)), ("sidebar", 0, saved_frame, (300, 400)), ("window", 1, saved_frame, (300, 400))):
            stand_in_app_controller.settings = self.controller.settings = dict(
                self.controller.settings, show_as=show_as_before, list_window_frame=list_window_frame)
            stand_in_app_controller.settings_window_placements = 0
            self.controller.set_shown(True, animate=False)  # 400 wide at x -2900, its top at y -3030
            shown_frames.clear()
            AppController.top_bar_dragged(stand_in_app_controller, self.controller.list_panel_controller, (-2700, -3040))
            self.assertFalse(self.controller.is_shown or self.controller.window.isVisible(), show_as_before)
            self.assertEqual(stand_in_app_controller.settings["show_as"], "window")
            window_frame = stand_in_app_controller.settings["list_window_frame"]
            # The window itself is there too, also when it was open already.
            frame = list_window_controller.window.frame()
            self.assertEqual([frame.origin.x, frame.origin.y, frame.size.width, frame.size.height], window_frame, show_as_before)
            self.assertEqual(shown_frames, [window_frame] * show_call_count, show_as_before)
            self.assertEqual(stand_in_app_controller.settings_window_placements, show_call_count, show_as_before)
            content_rect = list_window_controller.window.contentRectForFrameRect_(NSMakeRect(*window_frame))
            # The pointer halfway across it and 10 below its top.
            self.assertEqual((content_rect.origin.x, content_rect.origin.y + content_rect.size.height,
                              content_rect.size.width, content_rect.size.height),
                             (-2700 - content_width / 2, -3030, content_width, content_height), show_as_before)
        self.assertEqual(len(followed_pointer_offsets), 3)

    def test_shown_by_a_click_or_the_shortcut_it_stays_for_the_whole_five_seconds(self):
        self.controller.settings = dict(self.controller.settings, sidebar_mode="auto_hide")
        shown = []
        self.controller.set_shown = shown.append  # the window itself doesn't move
        self.controller.pointer_is_over_sidebar = lambda: False

        def at_time(now, action):
            with unittest.mock.patch("agent_command_tab.user_interface.sidebar_panel.time.time", return_value=now):
                action()
        at_time(100.0, self.controller.show_for_a_few_seconds)
        self.assertEqual((shown, self.controller.keep_shown_until), ([True], 100.0 + SECONDS_SHOWN_AFTER_A_CLICK_OR_SHORTCUT))
        at_time(99.9 + SECONDS_SHOWN_AFTER_A_CLICK_OR_SHORTCUT, self.controller.hide_timer.fire)
        self.assertEqual(shown, [True])  # not yet, though the pointer is elsewhere
        self.assertIsNotNone(self.controller.hide_timer)  # checks again when the time is up
        at_time(100.1 + SECONDS_SHOWN_AFTER_A_CLICK_OR_SHORTCUT, self.controller.hide_timer.fire)
        self.assertEqual(shown, [True, False])

    def test_a_sidebar_the_menu_hid_stays_hidden_through_settings_changes_until_its_mode_changes(self):
        reserved_areas = []
        self.owner.reserve_screen_area = reserved_areas.append
        self.controller.settings = dict(self.controller.settings, show_as="sidebar", sidebar_mode="docked")
        self.controller.hide_until_shown_again()  # the menu's Show window
        self.assertEqual(reserved_areas, [None])  # the docked sidebar's room is given back at once
        self.controller.apply_settings(dict(self.controller.settings, list_sort_order="activity"))  # a sort order chosen in the window
        self.assertFalse(self.controller.is_shown or self.controller.window.isVisible())
        self.assertEqual(set(reserved_areas), {None})  # no room kept for it
        self.assertTrue(self.controller.is_kept_hidden)
        # Show sidebar (or a click on the icon) brings it back for good, taking its room again.
        with self.patch_screens_to_one_far_away():
            self.controller.show_again()
        self.assertFalse(self.controller.is_kept_hidden)
        self.assertTrue(self.controller.is_shown)
        self.assertIsInstance(reserved_areas[-1], dict)
        # Hidden again, it is no longer kept hidden once its mode changes.
        self.controller.hide_until_shown_again()
        with self.patch_screens_to_one_far_away():
            self.controller.apply_settings(dict(self.controller.settings, sidebar_mode="auto_hide"))
        self.assertFalse(self.controller.is_kept_hidden)

    def test_a_sidebar_the_menu_showed_then_shown_for_good_hides_when_show_as_changes_away(self):
        with self.patch_screens_to_one_far_away():
            self.controller.apply_settings(dict(self.controller.settings, show_as="popup", sidebar_mode="floating"))
            self.controller.show_for_a_few_seconds()  # the menu's Show sidebar
            self.assertTrue(self.controller.is_shown)
            self.controller.apply_settings(dict(self.controller.settings, show_as="sidebar"))  # chosen in Settings
            self.assertTrue(self.controller.is_shown)
            self.controller.apply_settings(dict(self.controller.settings, show_as="popup"))  # and then changed back
        self.assertFalse(self.controller.is_shown)

    def test_a_sidebar_shown_for_a_few_seconds_keeps_a_hide_check_when_show_as_changes_away(self):
        with self.patch_screens_to_one_far_away():
            self.controller.apply_settings(dict(self.controller.settings, show_as="sidebar", sidebar_mode="auto_hide"))
            self.controller.show_for_a_few_seconds()  # a click on the menu bar icon or the shortcut
            self.controller.cancel_hide_timer()  # as the pointer moving onto it does
            self.controller.apply_settings(dict(self.controller.settings, show_as="popup"))  # chosen in Settings with the keyboard
        self.assertTrue(self.controller.is_shown)
        self.assertIsNotNone(self.controller.hide_timer)  # it still slides out once the pointer is off it

    def test_a_sidebar_not_chosen_in_show_as_is_parked_just_past_its_edge(self):
        # So the menu's "Show sidebar" slides it in from that edge. (The shown case isn't tested
        # here: it would put a real window on the screen.)
        self.controller.settings = dict(self.controller.settings, show_as="popup")
        self.controller.is_shown = False
        self.controller.update_placement()  # the real screens: the sidebar stays hidden
        self.assertFalse(self.controller.is_shown or self.controller.window.isVisible())
        # The parked frame for both edges, on this test's own screen (setUp: 1500 wide at x 0, sidebar 400 wide).
        for edge, parked_x in (("right", 1500), ("left", -400)):
            self.controller.screens = [BUILT_IN_SCREEN]
            self.controller.placement = SidebarPlacement(0, edge, True)
            self.controller.set_shown(False, animate=False)
            self.assertEqual(self.controller.window.frame().origin.x, parked_x, edge)

    def test_a_click_in_another_window_cancels_an_open_rename_so_the_list_keeps_updating(self):
        self.assertIs(self.controller.window.delegate(), self.controller)
        self.controller.window.setFrame_display_(NSMakeRect(-4000, -4000, 400, 600), False)
        self.controller.window.orderFrontRegardless()  # "on screen" for the list, though far outside every monitor
        list_controller = self.controller.list_controller
        list_controller.apply_snapshot(make_sample_snapshot())
        self.controller.window.contentView().layoutSubtreeIfNeeded()
        list_controller.begin_rename(list_controller.outline_view.itemAtRow_(1))
        self.assertTrue(list_controller.rename_in_progress)
        # A new name typed, so a rename submitted instead of cancelled would show.
        type_into_the_open_rename_editor(list_controller.outline_view, 1, "typed but cancelled")
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
    """Stands in for the app controller: holds the settings as each change leaves them."""

    def __init__(self, settings):
        self.settings = settings
        self.shortcut_lacks_permission = False
        # The agent_tools settings whose launch commands the settings window asked to have checked; the tests
        # answer with show_launch_command_problems themselves, as the app controller does once its check is done.
        self.agent_tools_asked_to_be_checked = []

    def settings_changed(self, new_settings):
        self.settings = new_settings

    def find_installed_agent_tools(self, agent_tools):
        self.agent_tools_asked_to_be_checked.append(agent_tools)

    def global_shortcut_lacks_permission(self):
        return self.shortcut_lacks_permission

    def window_frame_changed(self, setting_name, frame):
        pass

    def settings_window_closed(self):
        pass


class ListWindowSmokeTests(unittest.TestCase):
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

    def test_the_search_box_gives_the_keyboard_to_the_rows_once_the_window_no_longer_has_it(self):
        # Left in the box (after Return showed a tab the search found, or Esc hid the window), the keyboard was still
        # there the next time the window had it, and the rows' shortcuts typed into the box.
        controller = ListWindowController.alloc().initWithOwner_(RecordingOwner())
        controller.list_controller.refresh_timer.invalidate()
        self.assertTrue(controller.list_panel_controller.put_the_keyboard_in_the_search_box())  # as Command-F or "/" does
        self.assertIsNotNone(controller.list_panel_controller.search_field.currentEditor())
        controller.windowDidResignKey_(None)  # as AppKit tells it when another window, or another app, takes the keyboard
        self.assertIs(controller.window.firstResponder(), controller.list_controller.outline_view)

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
                                                   isKeyWindow=lambda: is_key_window, level=lambda: level,
                                                   frame=lambda: NSMakeRect(100, 100, 480, 560))
            with unittest.mock.patch("agent_command_tab.user_interface.list_window.NSApp",
                                     unittest.mock.Mock(orderedWindows=lambda: [controller.window])):  # the app's only window
                self.assertEqual(controller.is_in_front(), is_in_front, (is_visible, is_on_active_space, is_key_window, level))

    def test_an_always_on_top_window_under_another_of_the_apps_windows_is_not_in_front(self):
        # The settings window and the Saved sessions window open at its level, in front of it: counted as in front
        # under them, it would have "Show window" do nothing and the shortcut hide a list that is out of sight.
        controller = ListWindowController.alloc().initWithOwner_(RecordingOwner())
        controller.list_controller.refresh_timer.invalidate()
        controller.window = unittest.mock.Mock(isVisible=lambda: True, isOnActiveSpace=lambda: True, isKeyWindow=lambda: False,
                                               level=lambda: NSFloatingWindowLevel, frame=lambda: NSMakeRect(100, 100, 480, 560))

        def make_other_window(frame, is_titled=True, is_visible=True, is_on_active_space=True):
            return unittest.mock.Mock(frame=lambda: NSMakeRect(*frame), isVisible=lambda: is_visible,
                                      isOnActiveSpace=lambda: is_on_active_space,
                                      styleMask=lambda: NSWindowStyleMaskTitled if is_titled else NSWindowStyleMaskBorderless)
        frame_over_it, frame_beside_it = (300, 300, 720, 360), (700, 100, 720, 360)
        for case, windows_front_to_back, is_in_front in (
                ("over it", [make_other_window(frame_over_it), controller.window], False),
                ("behind it", [controller.window, make_other_window(frame_over_it)], True),
                ("in front of it, but beside it", [make_other_window(frame_beside_it), controller.window], True),
                ("closed", [make_other_window(frame_over_it, is_visible=False), controller.window], True),
                ("on another desktop", [make_other_window(frame_over_it, is_on_active_space=False), controller.window], True),
                ("an open menu's window", [make_other_window(frame_over_it, is_titled=False), controller.window], True)):
            with unittest.mock.patch("agent_command_tab.user_interface.list_window.NSApp",
                                     unittest.mock.Mock(orderedWindows=lambda: windows_front_to_back)):
                self.assertEqual(controller.is_in_front(), is_in_front, case)


class PopUpThatRecordsPresses(KeyboardPopUpButton):
    """A pop-up that, pressed, adds "pop-up" to recorded_presses (a list its test gives it) and stays closed: a real
    menu would wait for a choice."""

    def performClick_(self, sender):
        self.recorded_presses.append("pop-up")


class ButtonThatRecordsPresses(KeyboardButton):
    """A button that, pressed, adds "button" to recorded_presses (a list its test gives it)."""

    def performClick_(self, sender):
        self.recorded_presses.append("button")


class SettingsWindowSmokeTests(unittest.TestCase):
    def setUp(self):
        # One monitor whatever the Mac running the tests has connected, since the edge choices and the placement note follow the
        # monitors; tests of other arrangements patch it again.
        monitors_patch = unittest.mock.patch(
            "agent_command_tab.user_interface.settings_window.read_monitors_from_screens",
            return_value=[BUILT_IN_SCREEN])
        monitors_patch.start()
        self.addCleanup(monitors_patch.stop)
        self.owner = SettingsRecordingOwner(dict(DEFAULT_SETTINGS))
        self.controller = SettingsWindowController.alloc().initWithOwner_(self.owner)
        self.controller.settings = dict(self.owner.settings)
        self.controller.window = self.controller.build_window()  # built without showing it

    def show_the_window_transparent(self):
        """Opens the settings window as the app does, but transparent, since it shows on a real screen (macOS would pull
        a titled window there from anywhere else), and without making the app the active one; it is hidden again after
        the test."""
        with unittest.mock.patch("agent_command_tab.user_interface.settings_window.NSApp"), \
                unittest.mock.patch("agent_command_tab.user_interface.settings_window.place_window_at_saved_frame_or_center",
                                    lambda window, frame: window.setAlphaValue_(0.0)):
            self.controller.show(dict(self.owner.settings))
        self.addCleanup(self.controller.window.orderOut_, None)

    def test_the_window_opens_with_the_keyboard_on_show_as(self):
        # Left to AppKit, it went to the first text field: with Keyboard navigation off, AppKit
        # passes over pop-ups for the window's initial first responder.
        self.show_the_window_transparent()
        self.assertIs(self.controller.window.firstResponder(), self.controller.show_as_popup)

    def test_showing_an_open_window_brings_it_forward_as_it_is(self):
        # It was closed and built again, losing its scroll position and an edit under way.
        open_window = self.controller.window
        with unittest.mock.patch.object(self.controller, "is_open", return_value=True), \
                unittest.mock.patch.object(open_window, "makeKeyAndOrderFront_") as bring_forward, \
                unittest.mock.patch("agent_command_tab.user_interface.settings_window.NSApp"):
            self.controller.show(dict(self.owner.settings))
        self.assertIs(self.controller.window, open_window)
        bring_forward.assert_called_once_with(None)

    def test_every_control_fits_in_the_narrowest_window(self):
        # In every "Show as" layout, with the accessibility button shown too, each control ends inside
        # the narrowest window and starts right of its row's label: one too wide would grow leftward
        # over it.
        narrowest_width = SMALLEST_WINDOW_SIZES["settings_window_frame"][0]
        self.assertEqual(self.controller.window.minSize().width, narrowest_width)
        self.owner.shortcut_lacks_permission = True
        for show_as in ("popup", "window", "sidebar"):
            self.owner.settings = dict(self.owner.settings, show_as=show_as)
            self.controller.reflect_settings(self.owner.settings)
            subviews = self.controller.rows_view.subviews()
            labels = [subview for subview in subviews if isinstance(subview, NSTextField) and not subview.isEditable()]
            for subview in subviews:
                self.assertLessEqual(subview.frame().origin.x + subview.frame().size.width, narrowest_width, (show_as, subview))
                if isinstance(subview, NSBox) or subview in labels:
                    continue
                for label in self.list_labels_left_of_control(subview):
                    self.assertLessEqual(label.frame().origin.x + label.frame().size.width, subview.frame().origin.x,
                                         (show_as, label.stringValue()))

    def test_changing_one_control_keeps_changes_made_elsewhere(self):
        self.owner.settings = dict(self.owner.settings, sidebar_width=555)  # dragged at the sidebar's edge meanwhile
        self.controller.list_window_always_on_top_switch.setState_(1)
        self.controller.controlChanged_(self.controller.list_window_always_on_top_switch)
        self.assertTrue(self.owner.settings["list_window_is_always_on_top"])
        self.assertEqual(self.owner.settings["sidebar_width"], 555)

    def test_the_start_with_iterm2_switch_shows_and_saves_the_setting(self):
        self.assertEqual(self.controller.start_with_iterm2_switch.state(), 1)  # on by default
        self.assertIn(self.controller.start_with_iterm2_switch, self.controller.rows_view.subviews())  # in the General section
        self.assertEqual(self.find_label_of_control(self.controller.start_with_iterm2_switch),
                         "Launch AgentCommandTab whenever iTerm2 is started")
        self.controller.start_with_iterm2_switch.setState_(0)
        self.controller.controlChanged_(self.controller.start_with_iterm2_switch)
        self.assertFalse(self.owner.settings["app_starts_with_iterm2"])

    def test_both_edges_stay_choosable_when_the_menu_bar_monitor_stands_between_two_others(self):
        three_monitors_in_a_row = [BUILT_IN_SCREEN, DELL_SCREEN_TO_THE_LEFT, STUDIO_SCREEN_TO_THE_RIGHT]
        with unittest.mock.patch("agent_command_tab.user_interface.settings_window.read_monitors_from_screens",
                                 return_value=three_monitors_in_a_row):
            self.controller.update_edge_choices()
        self.assertEqual([menu_item.isEnabled() for menu_item in self.controller.edge_popup.itemArray()], [True, True])

    def check_that_the_popup_offers_these_titles_and_saves_the_choice(self, popup, titles, default_title, chosen_title,
                                                                      setting_name, expected_value):
        self.assertEqual(popup.itemTitles(), titles)
        self.assertEqual(popup.titleOfSelectedItem(), default_title)
        popup.selectItemWithTitle_(chosen_title)
        self.controller.controlChanged_(popup)
        self.assertEqual(self.owner.settings[setting_name], expected_value)

    def test_window_edge_popup_shows_capitalized_titles_and_saves_the_edge_name(self):
        self.check_that_the_popup_offers_these_titles_and_saves_the_choice(
            self.controller.edge_popup, ["Right", "Left"], "Right", "Left", "sidebar_edge", "left")

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

    def test_close_if_open_closes_only_an_open_window(self):  # for a click on the menu bar icon
        for is_visible in (True, False):
            window = unittest.mock.Mock()
            window.isVisible.return_value = is_visible
            self.controller.window = window
            self.controller.close_if_open()
            self.assertEqual(window.close.called, is_visible)

    def test_the_settings_window_opens_clear_of_a_shown_sidebar_and_stays_at_the_normal_level(self):
        # A stand-in app controller, as in the top bar drag test: only the windows this looks at.
        # The sidebar is 400 wide at the right edge of a 1500-point monitor; the settings window
        # (540 wide) reaches into it. Beside a 1200-wide sidebar it stays where it is rather than
        # being made 300 wide.
        for sidebar_is_shown, sidebar_width, expected_frames in ((True, 400, [(560, 200, 540, 600)]), (False, 400, []),
                                                                 (True, 1200, [])):
            levels, frames = [], []
            stand_in_app_controller = make_stand_in_app_controller_with_real_methods(
                ["move_window_clear_of_the_sidebar", "place_the_settings_and_saved_sessions_windows_above_the_lists"],
                settings={"list_window_is_always_on_top": False})
            stand_in_app_controller.list_window_controller.window.isVisible.return_value = False
            sidebar_controller = stand_in_app_controller.sidebar_controller
            sidebar_controller.window.isVisible.return_value = sidebar_is_shown
            sidebar_controller.window.frame.return_value = NSMakeRect(1500 - sidebar_width, 0, sidebar_width, 970)
            sidebar_controller.placement = SidebarPlacement(0, "right", True)
            sidebar_controller.screens = [BUILT_IN_SCREEN]
            settings_window = stand_in_app_controller.settings_window_controller.window
            settings_window.frame.return_value = NSMakeRect(900, 200, 540, 600)
            settings_window.setFrame_display_.side_effect = lambda rect, display: frames.append(
                (rect.origin.x, rect.origin.y, rect.size.width, rect.size.height))
            stand_in_app_controller.settings_window_controller.set_window_level = levels.append
            stand_in_app_controller.settings_window_controller.frame_setting_name = "settings_window_frame"
            stand_in_app_controller.popover.isShown.return_value = False
            AppController.open_settings(stand_in_app_controller)
            self.assertEqual(frames, expected_frames)
            self.assertEqual(levels, [None])  # the normal level, so other apps' windows can cover it and close it

    def test_open_settings_and_saved_sessions_windows_move_clear_of_a_sidebar_made_wider(self):
        for changed_setting, moves in (({"sidebar_width": 550}, True), ({"shown_columns": ["state"]}, False)):
            stand_in_app_controller = unittest.mock.Mock(settings=dict(DEFAULT_SETTINGS, show_as="sidebar"))
            stand_in_app_controller.settings_window_controller.is_open.return_value = True
            stand_in_app_controller.saved_sessions_window_controller.is_open.return_value = True
            with unittest.mock.patch("agent_command_tab.app_controller.save_settings"):
                AppController.settings_changed(stand_in_app_controller, dict(stand_in_app_controller.settings, **changed_setting))
            self.assertEqual([call.args for call in stand_in_app_controller.move_window_clear_of_the_sidebar.call_args_list],
                             [(stand_in_app_controller.settings_window_controller,),
                              (stand_in_app_controller.saved_sessions_window_controller,)]
                             if moves else [], changed_setting)

    def test_the_windows_above_the_lists_are_placed_again_when_the_list_window_shows_with_settings_closed(self):
        # An open Saved sessions window, which an always-on-top list window would otherwise cover.
        stand_in_app_controller = unittest.mock.Mock(settings=dict(DEFAULT_SETTINGS, show_as="popup",
                                                                   list_window_is_always_on_top=True))
        stand_in_app_controller.settings_window_controller.is_open.return_value = False
        with unittest.mock.patch("agent_command_tab.app_controller.save_settings"):
            AppController.settings_changed(stand_in_app_controller, dict(stand_in_app_controller.settings, show_as="window"))
        stand_in_app_controller.list_window_controller.show.assert_called_once()
        stand_in_app_controller.place_the_settings_and_saved_sessions_windows_above_the_lists.assert_called_once()

    def test_a_change_whose_save_fails_is_still_applied_so_the_windows_show_what_the_settings_say(self):
        # A full disk: "Show as: Window" still shows the list window, rather than leaving the app
        # with settings that say Window and no window, which choosing Window again wouldn't show either.
        stand_in_app_controller = unittest.mock.Mock(settings=dict(DEFAULT_SETTINGS, show_as="popup"), settings_save_is_scheduled=True)
        stand_in_app_controller.settings_window_controller.is_open.return_value = False
        new_settings = dict(stand_in_app_controller.settings, show_as="window")
        with unittest.mock.patch("agent_command_tab.app_controller.save_settings", side_effect=OSError(28, "No space left on device")), \
                unittest.mock.patch("sys.stdout", new_callable=io.StringIO) as printed:
            AppController.settings_changed(stand_in_app_controller, new_settings)
        self.assertEqual(stand_in_app_controller.settings, new_settings)
        stand_in_app_controller.list_window_controller.show.assert_called_once()
        stand_in_app_controller.apply_settings_to_the_core_and_every_list.assert_called_once()
        stand_in_app_controller.settings_window_controller.reflect_settings.assert_called_once_with(new_settings)
        self.assertIn("couldn't save the settings ([Errno 28] No space left on device)", printed.getvalue())
        self.assertTrue(stand_in_app_controller.settings_save_is_scheduled)  # a moved window's frame is still waiting to be written

    def test_a_moved_windows_frame_that_cant_be_saved_is_printed_so_quitting_goes_on(self):
        stand_in_app_controller = unittest.mock.Mock(settings=dict(DEFAULT_SETTINGS), settings_save_is_scheduled=True)
        with unittest.mock.patch("agent_command_tab.app_controller.save_settings", side_effect=OSError(28, "No space left on device")), \
                unittest.mock.patch("sys.stdout", new_callable=io.StringIO) as printed:
            AppController.save_settings_if_scheduled(stand_in_app_controller)  # raised the OSError
        self.assertIn("couldn't save the settings ([Errno 28] No space left on device)", printed.getvalue())
        self.assertFalse(stand_in_app_controller.settings_save_is_scheduled)

    def test_the_saved_sessions_window_opens_clear_of_a_shown_sidebar(self):
        stand_in_app_controller = unittest.mock.Mock(settings=dict(DEFAULT_SETTINGS), saved_windows=[])
        AppController.open_saved_sessions_window(stand_in_app_controller)
        stand_in_app_controller.move_window_clear_of_the_sidebar.assert_called_once_with(
            stand_in_app_controller.saved_sessions_window_controller)

    def test_the_core_and_the_sidebar_get_settings_only_when_their_part_changed(self):
        stand_in_app_controller = unittest.mock.Mock(core_settings_sent_last=None)
        with unittest.mock.patch("agent_command_tab.app_controller.build_core_settings", side_effect=lambda settings: {"x": 1}):
            AppController.apply_settings_to_the_core_and_every_list(stand_in_app_controller, dict(DEFAULT_SETTINGS))
            AppController.apply_settings_to_the_core_and_every_list(stand_in_app_controller, dict(DEFAULT_SETTINGS),
                                                                    sidebar_settings_changed=False)
        self.assertEqual(stand_in_app_controller.connection_thread.send_core_settings.call_count, 1)
        self.assertEqual(stand_in_app_controller.sidebar_controller.apply_settings.call_count, 1)
        self.assertEqual(stand_in_app_controller.list_window_controller.apply_settings.call_count, 2)

    def test_the_autolaunch_script_follows_only_a_change_of_start_with_iterm2(self):
        for changed_setting, applied_values in (({"app_starts_with_iterm2": False}, [False]), ({"shown_columns": ["state"]}, [])):
            stand_in_app_controller = unittest.mock.Mock(settings=dict(DEFAULT_SETTINGS))
            with unittest.mock.patch("agent_command_tab.app_controller.save_settings"):
                AppController.settings_changed(stand_in_app_controller, dict(stand_in_app_controller.settings, **changed_setting))
            self.assertEqual([call.args[0] for call in stand_in_app_controller.apply_start_with_iterm2_setting.call_args_list],
                             applied_values, changed_setting)

    def show_the_sidebar_section(self):
        """Chooses "Show as: Sidebar" as the app controller does once the pop-up changed, so the
        Sidebar section, with the width field, is laid out (it shows only for Sidebar)."""
        self.owner.settings = dict(self.owner.settings, show_as="sidebar")
        self.controller.reflect_settings(self.owner.settings)

    def test_a_number_being_typed_is_saved_when_the_window_closes(self):
        self.show_the_sidebar_section()
        window = self.controller.window  # built, not shown: no close button to click, so its close notification is sent
        window.makeFirstResponder_(self.controller.sidebar_width_field)
        self.controller.sidebar_width_field.currentEditor().setString_("555")
        self.controller.windowWillClose_(unittest.mock.Mock(object=lambda: window))
        self.assertEqual(self.owner.settings["sidebar_width"], 555)

    def list_labels_left_of_control(self, control):
        """Returns the labels (text fields that can't be edited) at the left of control in its row."""
        def find_middle(view):
            return view.frame().origin.y + view.frame().size.height / 2
        return [subview for subview in self.controller.rows_view.subviews()
                if isinstance(subview, NSTextField) and not subview.isEditable()
                and subview.frame().origin.x < control.frame().origin.x
                and abs(find_middle(subview) - find_middle(control)) < SETTINGS_ROW_HEIGHT / 2]

    def find_label_of_control(self, control):
        """Returns the text of the label at the left of control's row."""
        (label,) = self.list_labels_left_of_control(control)
        return label.stringValue()

    def test_column_switches_choose_the_shown_columns_in_their_left_to_right_order(self):
        switches = self.controller.column_switches
        # Each labeled with its title over the list's columns, then what the column shows.
        self.assertEqual([self.find_label_of_control(column_switch) for column_switch in switches.values()],
                         ["Status: agent's current status such as 'thinking', 'asking', 'done', 'idle'",
                          "Tokens: number of tokens (and % of context window) used so far",
                          "Created: time or date when the agent was started",
                          "Age: time elapsed since the agent was started",
                          "Last Msg: time elapsed since the last message",
                          "Memory: current memory usage by an agent and any subprocesses it started",
                          "CPU: current CPU usage by an agent and any subprocesses it started"])
        self.assertEqual([column_switch.state() for column_switch in switches.values()], [1, 1, 0, 0, 1, 0, 0])  # the defaults
        switches["state"].setState_(0)
        self.controller.controlChanged_(switches["state"])
        for column_name in ("created_time", "memory_usage", "cpu_usage"):
            switches[column_name].setState_(1)
            self.controller.controlChanged_(switches[column_name])
        # The list's order, CPU before memory, not the switches' order.
        self.assertEqual(self.owner.settings["shown_columns"],
                         ["context_tokens", "created_time", "time_since_last_message", "cpu_usage", "memory_usage"])
        switches["time_since_last_message"].setState_(0)  # on by default, and turned off like the others
        self.controller.controlChanged_(switches["time_since_last_message"])
        self.assertEqual(self.owner.settings["shown_columns"], ["context_tokens", "created_time", "cpu_usage", "memory_usage"])

    def test_each_column_is_described_after_its_title_in_a_lighter_color_and_cut_short_where_the_window_is_too_narrow(self):
        def check_the_column_labels(message, descriptions_fit=None):
            for column_name, column_switch in self.controller.column_switches.items():
                (label,) = self.list_labels_left_of_control(column_switch)
                text = label.attributedStringValue()
                title_length = len(COLUMN_TITLES[column_name])
                self.assertEqual(text.string()[title_length:title_length + 2], ": ", (message, column_name))
                self.assertEqual([text.attribute_atIndex_effectiveRange_(NSForegroundColorAttributeName, index, None)[0]
                                  for index in (0, title_length - 1, title_length, text.length() - 1)],
                                 [NSColor.labelColor(), NSColor.labelColor(), NSColor.secondaryLabelColor(),
                                  NSColor.secondaryLabelColor()], (message, column_name))
                # The label reaches up to 10 points before its switch, however long its text is.
                self.assertEqual(column_switch.frame().origin.x - (label.frame().origin.x + label.frame().size.width), 10,
                                 (message, column_name))
                if descriptions_fit is not None:
                    self.assertEqual(label.cell().cellSize().width <= label.frame().size.width, descriptions_fit,
                                     (message, column_name))
                self.assertTrue(label.allowsExpansionToolTips(), (message, column_name))  # cut short, its tooltip shows it in full
        # The longest description (Memory's) is a little too long for the narrowest window, and ends in "…" there.
        check_the_column_labels("the narrowest window")
        narrowest_width = self.controller.window.contentView().frame().size.width
        self.controller.window.setContentSize_((narrowest_width + 160, 300))
        check_the_column_labels("a wider window", descriptions_fit=True)
        # Narrower than the window can get: each description is cut short with "…" rather than drawn over its switch.
        self.controller.rows_view.setFrameSize_((200, self.controller.rows_view.frame().size.height))
        check_the_column_labels("rows narrower than any window", descriptions_fit=False)

    def test_text_fields_save_when_they_lose_focus_too(self):
        self.assertTrue(self.controller.sidebar_width_field.cell().sendsActionOnEndEditing())

    def test_tab_and_shift_tab_go_through_every_control_from_top_to_bottom(self):
        # Also with System Settings > Keyboard > Keyboard navigation off, when AppKit's own switches,
        # pop-ups and buttons stay out of it.
        window = self.controller.window

        def go_around(find_the_next_control):
            """The controls Tab (or Shift-Tab) goes through, as AppKit takes them from the window's
            key view loop, from the one that has the keyboard when the window opens."""
            controls = [window.initialFirstResponder()]
            while find_the_next_control(controls[-1]) not in controls and len(controls) < 100:
                controls.append(find_the_next_control(controls[-1]))
            return controls
        shown_controls = sorted(
            (view for view in self.controller.rows_view.subviews() if not view.isHidden()
             and (isinstance(view, (NSSwitch, NSButton)) or (isinstance(view, NSTextField) and view.isEditable()))),
            key=lambda view: (view.frame().origin.y, view.frame().origin.x))  # the rows' view is flipped: the top row first
        self.assertGreater(len([control for control in shown_controls if isinstance(control, NSSwitch)]), 5)
        self.assertEqual(go_around(lambda control: control.nextValidKeyView()), shown_controls)
        # Shift-Tab: from the top control round to the bottom one, and up from there.
        self.assertEqual(go_around(lambda control: control.previousValidKeyView()), shown_controls[:1] + shown_controls[:0:-1])
        # After a layout for another "Show as" too, which shows the sidebar's pop-ups and its width field.
        # The keyboard stays on "Show as", as when Sidebar was chosen with it, so Tab goes on from there.
        window.makeFirstResponder_(self.controller.show_as_popup)
        self.controller.reflect_settings(dict(self.owner.settings, show_as="sidebar"))
        self.assertIs(window.firstResponder(), self.controller.show_as_popup)
        self.assertIn(self.controller.sidebar_width_field, go_around(lambda control: control.nextValidKeyView()))

    def make_key_press(self, characters, key_code):
        return make_key_down_event(self.controller.window, characters, key_code)

    def test_space_or_return_turns_the_focused_switch_on_or_off(self):
        switch = self.controller.start_with_iterm2_switch
        self.controller.window.makeFirstResponder_(switch)
        switch.keyDown_(self.make_key_press(" ", 49))
        self.assertFalse(self.owner.settings["app_starts_with_iterm2"])
        switch.keyDown_(self.make_key_press("\r", 36))
        self.assertTrue(self.owner.settings["app_starts_with_iterm2"])

    def test_keys_open_a_focused_pop_ups_menu_and_press_a_focused_button(self):
        pressed = []
        popup = PopUpThatRecordsPresses.alloc().initWithFrame_pullsDown_(NSMakeRect(0, 0, 100, 24), False)
        button = ButtonThatRecordsPresses.alloc().initWithFrame_(NSMakeRect(0, 0, 100, 24))
        popup.recorded_presses = button.recorded_presses = pressed
        for characters, key_code in ((" ", 49), ("\r", 36), (NSUpArrowFunctionKey, 126), (NSDownArrowFunctionKey, 125)):
            popup.keyDown_(self.make_key_press(characters, key_code))
        for characters, key_code in ((" ", 49), ("\r", 36)):
            button.keyDown_(self.make_key_press(characters, key_code))
        self.assertEqual(pressed, ["pop-up"] * 4 + ["button"] * 2)

    def test_invalid_number_puts_the_current_value_back(self):
        self.controller.sidebar_width_field.setStringValue_("5")
        self.controller.controlChanged_(self.controller.sidebar_width_field)
        self.assertEqual(self.owner.settings["sidebar_width"], DEFAULT_SETTINGS["sidebar_width"])
        self.assertEqual(self.controller.sidebar_width_field.stringValue(), str(DEFAULT_SETTINGS["sidebar_width"]))

    def test_changes_made_elsewhere_show_in_the_open_window(self):
        # That they are kept: test_changing_one_control_keeps_changes_made_elsewhere.
        self.owner.settings = dict(self.owner.settings, show_as="sidebar", sidebar_width=650)
        self.controller.reflect_settings(self.owner.settings)
        self.assertEqual(self.controller.show_as_popup.titleOfSelectedItem(), "Sidebar")
        self.assertEqual(self.controller.sidebar_width_field.stringValue(), "650")

    def test_a_width_being_typed_survives_a_click_on_another_control(self):
        self.show_the_sidebar_section()
        width_field = self.controller.sidebar_width_field
        self.assertTrue(self.controller.window.makeFirstResponder_(width_field))
        width_field.currentEditor().setString_("500")  # typed, without Return or Tab
        self.controller.column_switches["created_time"].setState_(1)
        self.controller.controlChanged_(self.controller.column_switches["created_time"])
        self.controller.reflect_settings(self.owner.settings)  # as the app controller does after every change
        self.assertEqual(width_field.currentEditor().string(), "500")
        self.controller.window.makeFirstResponder_(None)  # editing ends, as a click elsewhere does
        self.assertEqual(self.owner.settings["sidebar_width"], 500)

    def test_a_width_field_that_only_has_the_focus_follows_a_width_dragged_at_the_sidebar(self):
        self.show_the_sidebar_section()
        width_field = self.controller.sidebar_width_field
        self.assertTrue(self.controller.window.makeFirstResponder_(width_field))  # as when Tab moves to it: it only has the focus
        self.owner.settings = dict(self.owner.settings, sidebar_width=555)  # dragged at the sidebar's edge
        self.controller.reflect_settings(self.owner.settings)
        self.assertEqual(width_field.currentEditor().string(), "555")
        self.controller.window.makeFirstResponder_(None)  # the window closes
        self.assertEqual(self.owner.settings["sidebar_width"], 555)  # not the 400 it showed before the drag

    def test_quitting_and_exiting_to_reconnect_save_a_width_being_typed_first(self):
        self.show_the_sidebar_section()
        width_field = self.controller.sidebar_width_field
        # Quitting gives the docked sidebar's room back first; exiting to reconnect doesn't.
        for exit_the_app, typed_width, steps_before_exiting, exit_status in (
                (AppController.quit, 500, ["save a moved window's frame", "give room back"], 0),
                (AppController.exit_now_with_reconnect_status, 510, ["save a moved window's frame"], RECONNECT_EXIT_STATUS)):
            self.assertTrue(self.controller.window.makeFirstResponder_(width_field))
            width_field.currentEditor().setString_(str(typed_width))  # typed, without Return or Tab
            steps = []
            # The real save_settings_before_exiting, which Ctrl+C and logout (applicationWillTerminate_) also go through.
            stand_in_app_controller = make_stand_in_app_controller_with_real_methods(
                ["save_settings_before_exiting"], settings_window_controller=self.controller,
                save_settings_if_scheduled=lambda: steps.append("save a moved window's frame"),
                give_room_back_before_exiting=lambda: steps.append("give room back"))
            with unittest.mock.patch("os._exit", lambda status: steps.append(f"exit {status}")):
                exit_the_app(stand_in_app_controller)
            self.assertEqual(self.owner.settings["sidebar_width"], typed_width, exit_the_app)
            self.assertEqual(steps, steps_before_exiting + [f"exit {exit_status}"], exit_the_app)

    def test_the_show_as_popup_offers_the_three_ways_and_saves_the_choice(self):
        self.check_that_the_popup_offers_these_titles_and_saves_the_choice(
            self.controller.show_as_popup, ["Popup", "Sidebar", "Window"], "Popup", "Window", "show_as", "window")

    def test_always_on_top_keeps_the_list_window_above_other_windows(self):
        self.owner.settings = dict(self.owner.settings, show_as="window")  # the Window section shows only then
        self.controller.reflect_settings(self.owner.settings)
        always_on_top_switch = self.controller.list_window_always_on_top_switch
        self.assertEqual((self.find_label_of_control(always_on_top_switch), always_on_top_switch.state()), ("Always on top", 0))
        always_on_top_switch.setState_(1)
        self.controller.controlChanged_(always_on_top_switch)
        self.assertTrue(self.owner.settings["list_window_is_always_on_top"])
        list_window_controller = ListWindowController.alloc().initWithOwner_(RecordingOwner())
        list_window_controller.list_controller.refresh_timer.invalidate()
        list_window_controller.apply_settings(self.owner.settings)
        self.assertEqual(list_window_controller.window.level(), NSFloatingWindowLevel)
        list_window_controller.apply_settings(dict(self.owner.settings, list_window_is_always_on_top=False))
        self.assertEqual(list_window_controller.window.level(), NSNormalWindowLevel)

    def test_esc_closes_the_window_also_while_a_field_is_being_typed_in(self):
        window = self.controller.window
        window.setAlphaValue_(0.0)  # shown on a real screen, but transparent (SavedSessionsWindowSmokeTests.setUp)
        self.addCleanup(window.orderOut_, None)
        window.orderFront_(None)
        window.makeFirstResponder_(self.controller.launch_command_fields[0])
        self.assertTrue(window.isVisible())
        window.sendEvent_(make_key_down_event(window, "\x1b", 53))
        self.assertFalse(window.isVisible())

    def test_settings_are_grouped_in_boxes_under_section_titles_in_a_resizable_scrolling_window(self):
        rows_view = self.controller.rows_view  # flipped: y grows downward
        titles_and_labels = ["General", "Show as", "Keyboard shortcut", "Appearance", "Window", "Always on top", "Sidebar", "Show",
                             "Window edge", "Width", "Columns", "Harnesses"]
        # The Window section only for "Show as: Window", the Sidebar section only for Sidebar.
        for show_as, titles_and_labels_left_out in (("popup", ["Window", "Always on top", "Sidebar", "Show", "Window edge", "Width"]),
                                                    ("window", ["Sidebar", "Show", "Window edge", "Width"]),
                                                    ("sidebar", ["Window", "Always on top"])):
            self.owner.settings = dict(self.owner.settings, show_as=show_as)
            self.controller.reflect_settings(self.owner.settings)
            texts_top_to_bottom = [subview.stringValue() for subview in sorted(
                (subview for subview in rows_view.subviews() if isinstance(subview, NSTextField)),
                key=lambda subview: subview.frame().origin.y)]
            self.assertEqual([text for text in texts_top_to_bottom if text in titles_and_labels],
                             [text for text in titles_and_labels if text not in titles_and_labels_left_out], show_as)
            # A rounded box per section: General, the Window or Sidebar one, Columns and Harnesses.
            boxes = [subview for subview in rows_view.subviews() if isinstance(subview, NSBox) and subview.boxType() == NSBoxCustom]
            self.assertEqual(len(boxes), 3 if show_as == "popup" else 4, show_as)
        self.assertTrue(self.controller.window.styleMask() & NSWindowStyleMaskResizable)
        self.assertIs(self.controller.window.contentView().documentView(), rows_view)
        width_before = rows_view.frame().size.width
        self.controller.window.setContentSize_((self.controller.window.contentView().frame().size.width + 160, 300))
        # The rows follow the window's width: the whole of it, or what a scroll bar beside them leaves where macOS is
        # set to always show scroll bars (at 300 points high the rows scroll).
        self.assertEqual(rows_view.frame().size.width, self.controller.window.contentView().contentSize().width)
        self.assertGreater(rows_view.frame().size.width, width_before)

    def test_the_rows_end_20_points_below_the_last_box(self):
        # That is 27 below the last control.
        rows_view = self.controller.rows_view
        last_box_bottom = max(subview.frame().origin.y + subview.frame().size.height for subview in rows_view.subviews()
                              if isinstance(subview, NSBox) and subview.boxType() == NSBoxCustom)
        self.assertEqual(rows_view.frame().size.height - last_box_bottom, 20)
        last_command_field = self.controller.launch_command_fields[-1]
        self.assertEqual(rows_view.frame().size.height - (last_command_field.frame().origin.y + last_command_field.frame().size.height),
                         27)

    def test_a_command_that_cant_run_is_outlined_in_red_without_a_warning(self):
        # The layer's border took a CGColor, an untyped pointer without Quartz, with a warning each time.
        command_field = self.controller.launch_command_fields[0]
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            for problem, tool_tip in (("nosuchprogram: command not found", "nosuchprogram: command not found"), ("", None)):
                self.controller.show_launch_command_problems({command_field.stringValue(): problem})
                self.assertEqual((command_field.shows_a_problem, command_field.toolTip()), (bool(problem), tool_tip))
                render_view_into_bitmap(command_field)  # draws the outline

    def test_the_harnesses_note_says_no_launch_command_can_be_run_only_while_every_one_cant(self):
        claude_command, codex_command, agy_command = [command_field.stringValue()
                                                      for command_field in self.controller.launch_command_fields]
        # agy alone still runs: its field and the others' red outlines say enough.
        self.controller.show_launch_command_problems(
            {claude_command: "claude: command not found", codex_command: "codex: command not found", agy_command: ""})
        self.assertIsNone(self.controller.harness_note.superview())
        height_without_the_note = self.controller.rows_view.frame().size.height
        # The note says what was checked: that no command can be run, not that no tool is installed (one may be
        # installed elsewhere than a mistyped path says).
        self.controller.show_launch_command_problems(
            {claude_command: "claude: command not found", codex_command: "codex: command not found",
             agy_command: "~/bin/agy doesn't exist"})
        self.assertEqual(self.controller.harness_note.stringValue(), "None of these launch commands can be run: Claude Code, Codex, agy")
        self.assertIs(self.controller.harness_note.superview(), self.controller.rows_view)
        self.assertGreater(self.controller.rows_view.frame().size.height, height_without_the_note)
        self.controller.show_launch_command_problems({claude_command: ""})  # one runs again
        self.assertEqual(self.controller.harness_note.stringValue(), "")
        self.assertIsNone(self.controller.harness_note.superview())

    def test_the_owner_checks_the_launch_commands_for_the_window_which_starts_no_check_of_its_own(self):
        # One check of each command serves the window and the menus that start agent sessions: a check of the window's
        # own would ask the login shell about an edited command a second time.
        self.show_the_window_transparent()
        self.assertEqual(self.owner.agent_tools_asked_to_be_checked, [self.owner.settings["agent_tools"]])  # as it opens
        claude_field = self.controller.launch_command_fields[0]
        # A command edited: the owner starts its check as it takes the new settings (AppController.settings_changed).
        claude_field.setStringValue_("~/bin/claude")
        self.controller.controlChanged_(claude_field)
        self.assertEqual(self.owner.settings["agent_tools"][0]["launch_command"], "~/bin/claude")
        self.assertEqual(len(self.owner.agent_tools_asked_to_be_checked), 1)
        # The answer about the command the field held before leaves the field as it is; the one about its own shows.
        self.controller.show_launch_command_problems({"claude": "claude: command not found", "codex": "", "agy": ""})
        self.assertFalse(claude_field.shows_a_problem)
        self.controller.show_launch_command_problems({"~/bin/claude": "~/bin/claude doesn't exist", "codex": "", "agy": ""})
        self.assertEqual((claude_field.shows_a_problem, claude_field.toolTip()), (True, "~/bin/claude doesn't exist"))
        # Entered again as it is, a command that couldn't be run is checked again (its program may be installed by
        # now); one that can be run isn't.
        self.controller.controlChanged_(claude_field)
        self.assertEqual(self.owner.agent_tools_asked_to_be_checked[1:], [self.owner.settings["agent_tools"]])
        self.controller.controlChanged_(self.controller.launch_command_fields[1])
        self.assertEqual(len(self.owner.agent_tools_asked_to_be_checked), 2)

    def test_the_command_fields_fill_their_rows_from_10_points_after_their_labels_and_scroll_a_longer_command(self):
        command_fields = self.controller.launch_command_fields
        rows_view = self.controller.rows_view
        narrowest_width = self.controller.window.contentView().frame().size.width

        def check_the_command_fields(message):
            for command_field in command_fields:
                (label,) = self.list_labels_left_of_control(command_field)
                self.assertEqual(label.stringValue(), "Command", message)
                self.assertEqual(command_field.frame().origin.x - (label.frame().origin.x + label.frame().size.width), 10, message)
                # Up to where the switches end: the window's margin of 20 points and the box's padding of 10 from the right.
                self.assertEqual(rows_view.frame().size.width - (command_field.frame().origin.x + command_field.frame().size.width),
                                 30, message)
        check_the_command_fields("the narrowest window")
        width_in_the_narrowest_window = command_fields[0].frame().size.width
        self.controller.window.setContentSize_((narrowest_width + 160, 300))
        check_the_command_fields("a wider window")
        self.assertEqual(command_fields[0].frame().size.width, width_in_the_narrowest_window + 160)  # the fields take the added width
        self.owner.settings = dict(self.owner.settings, show_as="window")  # another section shows: the rows are laid out again
        self.controller.reflect_settings(self.owner.settings)
        check_the_command_fields("a wider window, laid out again")
        self.assertTrue(all(command_field.cell().isScrollable() for command_field in command_fields))

    def check_every_section_title_starts_20_points_below_what_is_shown_above_it(self, shown_section_titles, message):
        subviews = list(self.controller.rows_view.subviews())  # flipped: y grows downward
        section_titles = [subview for subview in subviews if isinstance(subview, NSTextField)
                          and subview.stringValue() in ("General", "Window", "Sidebar", "Columns", "Harnesses")]
        self.assertEqual([section_title.stringValue() for section_title in section_titles], shown_section_titles, message)
        for section_title in section_titles[1:]:
            bottoms_above = [subview.frame().origin.y + subview.frame().size.height for subview in subviews
                             if subview.frame().origin.y < section_title.frame().origin.y]
            self.assertEqual(section_title.frame().origin.y - max(bottoms_above), 20, (message, section_title.stringValue()))

    def test_every_section_title_starts_20_points_below_what_is_shown_above_it(self):
        for show_as, shown_section_titles in (("popup", ["General", "Columns", "Harnesses"]),
                                              ("window", ["General", "Window", "Columns", "Harnesses"]),
                                              ("sidebar", ["General", "Sidebar", "Columns", "Harnesses"])):
            self.owner.settings = dict(self.owner.settings, show_as=show_as)
            self.controller.reflect_settings(self.owner.settings)
            self.check_every_section_title_starts_20_points_below_what_is_shown_above_it(shown_section_titles, show_as)

    def test_the_sidebar_note_shows_under_its_box_and_takes_room_only_while_it_says_something(self):
        self.show_the_sidebar_section()
        height_without_note = self.controller.rows_view.frame().size.height
        self.assertIsNone(self.controller.edge_note.superview())
        # A monitor right of the menu bar's: the saved right edge borders it, so the note says where the sidebar is.
        with unittest.mock.patch("agent_command_tab.user_interface.settings_window.read_monitors_from_screens",
                                 return_value=[BUILT_IN_SCREEN, STUDIO_SCREEN_TO_THE_RIGHT]):
            self.controller.screen_layout_changed()
        self.assertTrue(self.controller.edge_note.stringValue())
        self.assertIs(self.controller.edge_note.superview(), self.controller.rows_view)
        self.assertGreater(self.controller.rows_view.frame().size.height, height_without_note)
        self.check_every_section_title_starts_20_points_below_what_is_shown_above_it(
            ["General", "Sidebar", "Columns", "Harnesses"], "with the note")
        self.controller.screen_layout_changed()  # the one monitor again
        self.assertIsNone(self.controller.edge_note.superview())
        self.assertEqual(self.controller.rows_view.frame().size.height, height_without_note)

    def test_a_monitor_connected_while_the_sidebar_section_is_hidden_leaves_a_launch_command_being_typed(self):
        # The sidebar's note isn't among the rows then (Show as: Popup), so they aren't laid out again for it, which
        # would fit the window's height again for nothing.
        command_field = self.controller.launch_command_fields[0]
        self.assertTrue(self.controller.window.makeFirstResponder_(command_field))
        command_field.currentEditor().setString_("cla")  # typed so far, without Return or Tab
        rows_height = self.controller.rows_view.frame().size.height
        views_laid_out = list(self.controller.rows_view.subviews())
        with unittest.mock.patch("agent_command_tab.user_interface.settings_window.read_monitors_from_screens",
                                 return_value=[BUILT_IN_SCREEN, STUDIO_SCREEN_TO_THE_RIGHT]):  # the saved right edge borders it
            self.controller.screen_layout_changed()
            self.assertEqual(list(self.controller.rows_view.subviews()), views_laid_out)  # a layout makes new labels and boxes
            self.assertEqual(command_field.currentEditor().string(), "cla")
            self.assertEqual(self.owner.settings["agent_tools"][0]["launch_command"], "claude")
            self.assertEqual(self.controller.rows_view.frame().size.height, rows_height)
            # The note is up to date for when its section shows.
            command_field.abortEditing()
            self.show_the_sidebar_section()
        self.assertTrue(self.controller.edge_note.stringValue())
        self.assertIs(self.controller.edge_note.superview(), self.controller.rows_view)

    def test_a_note_that_goes_while_a_launch_command_is_being_typed_leaves_it_being_typed_and_unsaved(self):
        # A check of the launch commands answers at any moment. Laying the rows out again for the Harnesses note takes
        # every field out and puts it back, which ended the edit and saved the half-typed command.
        command_fields = self.controller.launch_command_fields
        self.controller.show_launch_command_problems(
            {command_field.stringValue(): "command not found" for command_field in command_fields})
        self.assertIs(self.controller.harness_note.superview(), self.controller.rows_view)
        self.assertTrue(self.controller.window.makeFirstResponder_(command_fields[0]))
        command_fields[0].currentEditor().setString_("cla")  # typed so far, without Return or Tab
        command_fields[0].currentEditor().setSelectedRange_((1, 0))  # the insertion point after the "c"
        self.controller.show_launch_command_problems({command_fields[1].stringValue(): ""})  # Codex can be run: the note goes
        self.assertIsNone(self.controller.harness_note.superview())
        self.assertEqual(self.owner.settings["agent_tools"][0]["launch_command"], "claude")
        editor = command_fields[0].currentEditor()
        self.assertEqual((editor.string(), editor.selectedRange().location, editor.selectedRange().length), ("cla", 1, 0))
        self.controller.window.makeFirstResponder_(None)  # the edit ends, as a click elsewhere ends it: saved now
        self.assertEqual(self.owner.settings["agent_tools"][0]["launch_command"], "cla")

    def test_the_end_of_a_long_command_being_typed_stays_in_view_when_a_note_goes(self):
        # A field that takes the keyboard back after the layout shows its text from the start: the insertion point of
        # a command longer than the field was out of view until the next key press.
        command_fields = self.controller.launch_command_fields
        self.controller.show_launch_command_problems(
            {command_field.stringValue(): "command not found" for command_field in command_fields})
        self.assertTrue(self.controller.window.makeFirstResponder_(command_fields[0]))
        typed_command = "/a/folder/with/a/long/name" * 6 + "/claude"
        command_fields[0].currentEditor().setString_(typed_command)
        command_fields[0].currentEditor().setSelectedRange_((len(typed_command), 0))  # typed up to its end
        self.controller.show_launch_command_problems({command_fields[1].stringValue(): ""})  # Codex can be run: the note goes
        # A window lays its views out before it draws them, and that is when AppKit lays the editor's text out and scrolls
        # to the insertion point in a Python built with the macOS 26 SDK or a later one. In a Python built with an earlier
        # SDK, AppKit has done both by now.
        self.controller.window.contentView().layoutSubtreeIfNeeded()
        editor = command_fields[0].currentEditor()
        last_character_x = editor.layoutManager().boundingRectForGlyphRange_inTextContainer_(
            (len(typed_command) - 1, 1), editor.textContainer()).origin.x
        self.assertGreater(last_character_x, command_fields[0].frame().size.width)  # the command is longer than its field
        visible_rect = editor.visibleRect()
        self.assertTrue(visible_rect.origin.x <= last_character_x <= visible_rect.origin.x + visible_rect.size.width)
        command_fields[0].abortEditing()

    def test_a_width_being_typed_is_saved_when_its_section_goes(self):
        # Its field is no longer among the rows once "Show as" isn't Sidebar, so there is nowhere to go on typing it.
        self.show_the_sidebar_section()
        width_field = self.controller.sidebar_width_field
        self.assertTrue(self.controller.window.makeFirstResponder_(width_field))
        width_field.currentEditor().setString_("555")  # typed, without Return or Tab
        self.owner.settings = dict(self.owner.settings, show_as="popup")  # chosen in the pop-up, which leaves the edit open
        self.controller.reflect_settings(self.owner.settings)
        self.assertIsNone(width_field.window())
        self.assertEqual((self.owner.settings["sidebar_width"], self.owner.settings["show_as"]), (555, "popup"))

    def test_a_check_that_answers_while_a_command_is_being_typed_shows_for_the_saved_command(self):
        # Matched with the text being typed, the answer about the saved command was dropped: typed back to that command,
        # the field showed no problem, and nothing checked it again.
        claude_field = self.controller.launch_command_fields[0]
        self.assertTrue(self.controller.window.makeFirstResponder_(claude_field))
        claude_field.currentEditor().setString_("cla")  # typed so far, without Return or Tab
        self.controller.show_launch_command_problems({"claude": "claude: command not found", "codex": "", "agy": ""})
        self.assertEqual(claude_field.currentEditor().string(), "cla")
        claude_field.currentEditor().setString_("claude")  # typed back to the saved command
        self.controller.window.makeFirstResponder_(None)  # the edit ends
        self.assertEqual(self.owner.settings["agent_tools"][0]["launch_command"], "claude")
        self.assertEqual((claude_field.shows_a_problem, claude_field.toolTip()), (True, "claude: command not found"))

    def test_tab_goes_through_the_fields_of_a_section_shown_after_the_window_opened(self):
        self.controller.window.recalculateKeyViewLoop()  # as showing the window does, here as Popup
        self.show_the_sidebar_section()
        # On to the control below it, the first column's switch.
        self.assertIs(self.controller.sidebar_width_field.nextValidKeyView(), self.controller.column_switches["state"])

    def test_the_window_fits_its_height_to_the_sections_shown_keeping_its_top_edge_and_width(self):
        window = self.controller.window  # built, not shown
        visible_frame = NSScreen.mainScreen().visibleFrame()
        left, top = visible_frame.origin.x + 50, visible_frame.origin.y + visible_frame.size.height - 50
        window.setFrame_display_(NSMakeRect(left, top - 300, 600, 300), False)
        content_heights = {}
        for show_as in ("sidebar", "popup", "window"):
            self.owner.settings = dict(self.owner.settings, show_as=show_as)
            self.controller.reflect_settings(self.owner.settings)
            frame = window.frame()
            self.assertEqual((frame.origin.x, frame.size.width, frame.origin.y + frame.size.height), (left, 600, top), show_as)
            # As tall as the rows, or as the room below the window's top, where the rows scroll.
            rows_frame_height = window.frameRectForContentRect_(
                NSMakeRect(0, 0, 600, self.controller.rows_view.frame().size.height)).size.height
            self.assertEqual(frame.size.height, min(rows_frame_height, top - visible_frame.origin.y), show_as)
            content_heights[show_as] = self.controller.rows_view.frame().size.height
        # The Sidebar section's 3 rows, the Window section's 1, each with its title and the gap after it.
        section_height_without_rows = SECTION_TITLE_HEIGHT + GAP_BELOW_SECTION_TITLE + GAP_BETWEEN_SECTIONS
        self.assertEqual((content_heights["sidebar"] - content_heights["popup"], content_heights["window"] - content_heights["popup"]),
                         (section_height_without_rows + 3 * SETTINGS_ROW_HEIGHT, section_height_without_rows + SETTINGS_ROW_HEIGHT))

    def test_the_settings_window_reports_its_frame_and_uses_a_saved_one(self):
        reported_frames = []
        self.owner.window_frame_changed = lambda setting_name, frame: reported_frames.append((setting_name, frame))
        self.controller.window.setFrame_display_(NSMakeRect(-4000, -4000, 500, 400), False)
        self.controller.report_frame()
        self.assertEqual(reported_frames[-1], ("settings_window_frame", [-4000, -4000, 500, 400]))
        self.assertFalse(self.controller.is_open())  # built but never shown
        # Opened again, it takes the saved frame (place_window_at_saved_frame_or_center, checked below).
        saved_frame = [-3900, -3900, 600, 450]
        with unittest.mock.patch("agent_command_tab.user_interface.settings_window.place_window_at_saved_frame_or_center") as place, \
                unittest.mock.patch("agent_command_tab.user_interface.settings_window.NSApp"), \
                unittest.mock.patch.object(self.controller, "build_window", return_value=self.controller.window), \
                unittest.mock.patch.object(self.controller.window, "makeKeyAndOrderFront_"):
            self.controller.show(dict(self.owner.settings, settings_window_frame=saved_frame))
        place.assert_called_once_with(self.controller.window, saved_frame)

    def test_a_saved_frame_is_taken_while_it_is_on_a_connected_screen_else_the_middle_of_the_main_one(self):
        # Here a connected screen far outside every real monitor. The window's center method (AppKit's, which puts a
        # window in the middle of a screen) is a stand-in, so the window stays off the real screens.
        with unittest.mock.patch("agent_command_tab.user_interface.screen_layout.list_screen_frames",
                                 lambda: [(-4000, -4000, 1500, 1000)]), \
                unittest.mock.patch.object(self.controller.window, "center") as center:
            place_window_at_saved_frame_or_center(self.controller.window, [-3900, -3900, 600, 450])
            self.assertEqual(convert_rect_to_frame_list(self.controller.window.frame()), [-3900, -3900, 600, 450])
            place_window_at_saved_frame_or_center(self.controller.window, [9000, 9000, 600, 450])  # a monitor that is gone
            center.assert_called_once_with()  # for the second frame alone
            self.assertEqual(convert_rect_to_frame_list(self.controller.window.frame()), [-3900, -3900, 600, 450])  # not moved there

    def test_harnesses_can_be_left_out_and_keep_their_commands(self):
        self.assertEqual([self.find_label_of_control(include_switch) for include_switch in self.controller.include_switches],
                         ["Claude Code", "Codex", "agy"])
        self.assertEqual(self.find_label_of_control(self.controller.launch_command_fields[1]), "Command")  # under "Codex"
        self.controller.include_switches[1].setState_(0)
        self.controller.controlChanged_(self.controller.include_switches[1])
        self.assertEqual([agent_tool["is_included"] for agent_tool in self.owner.settings["agent_tools"]], [True, False, True])
        self.assertEqual(self.owner.settings["agent_tools"][1]["launch_command"], "codex")

    def test_the_shortcut_popup_offers_both_combinations_and_off_and_saves_the_choice(self):
        self.check_that_the_popup_offers_these_titles_and_saves_the_choice(
            self.controller.global_shortcut_popup, ["Shift-Command-Tab", "Command-Fn-Tab", "Off"], "Shift-Command-Tab",
            "Command-Fn-Tab", "global_shortcut", "command_fn_tab")

    def test_the_appearance_popup_offers_auto_dark_and_light_below_the_shortcut_and_saves_the_choice(self):
        popup = self.controller.appearance_popup
        # In the row under the shortcut's while the accessibility button is hidden.
        self.assertEqual(popup.frame().origin.y - self.controller.global_shortcut_popup.frame().origin.y, SETTINGS_ROW_HEIGHT)
        self.check_that_the_popup_offers_these_titles_and_saves_the_choice(popup, ["Auto", "Dark", "Light"], "Auto", "Dark",
                                                                           "appearance", "dark")

    def test_the_chosen_appearance_draws_the_app_and_the_dropdown_and_auto_follows_macos(self):
        # The dropdown is set by itself: a popover takes the look of the menu bar it opens from.
        stand_in_app_controller = unittest.mock.Mock(popover=NSPopover.alloc().init())
        try:
            AppController.apply_appearance(stand_in_app_controller, "dark")
            self.assertEqual((NSApp.appearance().name(), stand_in_app_controller.popover.appearance().name()),
                             (NSAppearanceNameDarkAqua, NSAppearanceNameDarkAqua))
            AppController.apply_appearance(stand_in_app_controller, "light")
            self.assertEqual((NSApp.appearance().name(), stand_in_app_controller.popover.appearance().name()),
                             (NSAppearanceNameAqua, NSAppearanceNameAqua))
            AppController.apply_appearance(stand_in_app_controller, "auto")
            self.assertEqual((NSApp.appearance(), stand_in_app_controller.popover.appearance()), (None, None))
        finally:
            NSApp.setAppearance_(None)  # the other tests' windows follow macOS's setting

    def test_the_accessibility_button_shows_under_the_shortcut_only_while_it_lacks_the_permission(self):
        button = self.controller.accessibility_settings_button
        shortcut_popup, appearance_popup = self.controller.global_shortcut_popup, self.controller.appearance_popup
        self.assertTrue(button.isHidden())
        self.assertIsNone(button.superview())  # its row takes no room
        self.owner.shortcut_lacks_permission = True
        self.controller.reflect_settings(self.owner.settings)
        self.assertFalse(button.isHidden())
        # In a row of its own right under the shortcut's; Appearance moves a row down.
        self.assertAlmostEqual(button.frame().origin.y + button.frame().size.height / 2
                               - (shortcut_popup.frame().origin.y + shortcut_popup.frame().size.height / 2), SETTINGS_ROW_HEIGHT,
                               delta=2)
        self.assertEqual(appearance_popup.frame().origin.y - shortcut_popup.frame().origin.y, 2 * SETTINGS_ROW_HEIGHT)
        self.owner.shortcut_lacks_permission = False
        self.controller.reflect_settings(self.owner.settings)
        self.assertTrue(button.isHidden())
        self.assertEqual(appearance_popup.frame().origin.y - shortcut_popup.frame().origin.y, SETTINGS_ROW_HEIGHT)

    def test_reflecting_settings_before_the_window_was_opened_does_nothing(self):
        controller = SettingsWindowController.alloc().initWithOwner_(self.owner)
        controller.reflect_settings(dict(self.owner.settings, sidebar_width=700))
        self.assertIsNone(controller.window)


class ShowTheListAsTests(unittest.TestCase):
    def test_the_menus_show_items_hide_the_other_ways_first_and_do_nothing_for_the_way_shown(self):
        calls = []

        class StandInAppController:
            """Whichever of the dropdown, the sidebar and the window shows, and what was done to them."""

            def __init__(self, show_as, sidebar_mode, shown_list_formats):
                self.settings = {"show_as": show_as, "sidebar_mode": sidebar_mode}
                self.popover = unittest.mock.Mock(isShown=lambda: "popup" in shown_list_formats)
                self.status_item_controller = unittest.mock.Mock(show_popover=lambda: calls.append("show popup"))
                self.sidebar_controller = unittest.mock.Mock(
                    is_shown="sidebar" in shown_list_formats, show_for_a_few_seconds=lambda: calls.append("show sidebar briefly"),
                    show_again=lambda: calls.append("show sidebar for good"),
                    hide_until_shown_again=lambda: calls.append("hide sidebar until shown again"))
                # "window behind" is open but behind other windows: not in front, as the global
                # shortcut asks too, but hidden when another view is chosen.
                self.list_window_controller = unittest.mock.Mock(hide=lambda: calls.append("hide window"),
                                                                 is_in_front=lambda: "window" in shown_list_formats)
                self.list_window_controller.window.isVisible.return_value = bool({"window", "window behind"} & shown_list_formats)

            def close_dropdown(self):
                calls.append("hide popup")

            def list_window_closed(self):
                calls.append("settings window level updated")

            def hide_the_list_window(self):
                AppController.hide_the_list_window(self)

            def show_the_list_window(self):
                calls.append("show window")

            def list_the_ways_the_list_shows(self):
                return AppController.list_the_ways_the_list_shows(self)

            def show_the_sidebar(self):
                AppController.show_the_sidebar(self)
        for show_as, sidebar_mode, shown_list_formats, show_as_choice, expected_calls in (
                ("window", "auto_hide", {"window"}, "window", []),  # already showing that way: nothing
                ("window", "auto_hide", {"window behind"}, "window", ["show window"]),  # brought forward
                ("window", "auto_hide", {"window"}, "popup", ["hide window", "settings window level updated", "show popup"]),
                ("window", "auto_hide", {"window behind"}, "sidebar",
                 ["hide window", "settings window level updated", "show sidebar briefly"]),
                ("popup", "floating", {"popup"}, "sidebar", ["hide popup", "show sidebar briefly"]),
                ("sidebar", "docked", {"sidebar"}, "window", ["hide sidebar until shown again", "show window"]),
                ("sidebar", "docked", set(), "sidebar", ["show sidebar for good"]),  # back after Show window hid it
                ("popup", "auto_hide", {"popup", "window"}, "sidebar",
                 ["hide popup", "hide window", "settings window level updated", "show sidebar briefly"])):
            calls.clear()
            stand_in_app_controller = StandInAppController(show_as, sidebar_mode, shown_list_formats)
            AppController.show_the_list_as(stand_in_app_controller, show_as_choice)
            self.assertEqual(calls, expected_calls, (show_as, shown_list_formats, show_as_choice))
            self.assertEqual(stand_in_app_controller.settings["show_as"], show_as)  # never changed

    def test_the_window_dropped_on_the_menu_bar_icon_turns_into_the_dropdown(self):
        calls = []
        for show_as, expected_calls in (("window", ["show as popup", "show popup"]),
                                        # Opened by the menu's Show window: it just hides, and Show as stays.
                                        ("sidebar", ["hide window", "settings window level updated", "show popup"]),
                                        ("popup", ["hide window", "settings window level updated", "show popup"])):
            calls.clear()
            AppController.list_window_drag_ended(make_stand_in_app_controller_with_real_methods(
                ["hide_the_list_window"], settings={"show_as": show_as},
                status_item_controller=unittest.mock.Mock(show_popover=lambda: calls.append("show popup")),
                list_window_controller=unittest.mock.Mock(hide=lambda: calls.append("hide window")),
                pointer_is_on_the_menu_bar_icon=lambda: True,
                settings_changed=lambda new_settings: calls.append(f"show as {new_settings['show_as']}"),
                list_window_closed=lambda: calls.append("settings window level updated")))
            self.assertEqual(calls, expected_calls, show_as)


class YieldActivationTests(unittest.TestCase):
    def test_activation_is_handed_over_only_where_macos_has_the_hand_over(self):
        iterm2_application = object()
        for macos_has_it in (True, False):  # macOS 14 and later; older
            application = unittest.mock.Mock()
            application.respondsToSelector_.return_value = macos_has_it
            with unittest.mock.patch("agent_command_tab.app_controller.NSApp", application):
                yield_activation_to(iterm2_application)
            application.respondsToSelector_.assert_called_with("yieldActivationToApplication:")
            self.assertEqual(application.yieldActivationToApplication_.call_args_list,
                             [unittest.mock.call(iterm2_application)] if macos_has_it else [])


class SavedSessionsRecordingOwner:
    """Stands in for the app controller: records what the Saved sessions window asks of it."""

    def __init__(self):
        self.calls = []
        self.conversation_searches = []  # (text, is_case_sensitive, on_answer) of each search the window asked for

    def find_saved_agent_sessions_whose_conversations_contain(self, text, is_case_sensitive, on_answer):
        self.conversation_searches.append((text, is_case_sensitive, on_answer))

    def search_is_case_sensitive_chosen(self, search_is_case_sensitive):
        self.calls.append(("search is case sensitive", search_is_case_sensitive))

    def restore_saved_agent_sessions(self, window_keys, conversation_ids):
        self.calls.append(("restore", window_keys, conversation_ids))

    def delete_saved_items(self, window_keys, conversation_ids):
        self.calls.append(("delete", window_keys, conversation_ids))

    def rename_saved_window(self, window_key, new_name):
        self.calls.append(("rename window", window_key, new_name))

    def rename_saved_session(self, conversation_id, new_name):
        self.calls.append(("rename session", conversation_id, new_name))

    def window_frame_changed(self, setting_name, frame):
        pass


def make_saved_session_record(conversation_id, name, agent_tool_name="Claude Code", folder="/tmp/x", saved_at=None):
    return build_saved_session_record(conversation_id, folder, f"session-{conversation_id}", name, agent_tool_name=agent_tool_name,
                                      saved_at=time.mktime((2026, 11, 6, 7, 52, 0, 0, 0, -1)) if saved_at is None else saved_at)


def make_saved_windows():
    """agent-command-tab (an iTerm2 window: planning, reviews) and tmux: work (a tmux session: notes)."""
    return [build_saved_window_record([
                make_saved_session_record("a", "planning", folder=os.path.expanduser("~/code/agent-command-tab")),
                make_saved_session_record("b", "reviews", "Codex", saved_at=time.mktime((2026, 11, 5, 23, 28, 0, 0, 0, -1)))],
                "w1", "agent-command-tab", (0, 0, 800, 600)),
            dict(build_saved_window_record([make_saved_session_record("c", "notes")], "$1", "tmux: work", None,
                                           name_was_set_by_user=True),
                 terminal_name="tmux", terminal_window_opened_at=1793977852)]


class SavedSessionsWindowSmokeTests(unittest.TestCase):
    def setUp(self):
        self.owner = SavedSessionsRecordingOwner()
        self.controller = SavedSessionsWindowController.alloc().initWithOwner_(self.owner)
        self.controller.window = self.controller.build_window()  # built without showing it
        # Transparent, since the tests that show it can't keep it off the real screens: macOS pulls a
        # titled window from outside every monitor onto one.
        self.controller.window.setAlphaValue_(0.0)
        self.controller.show_saved_windows(make_saved_windows())
        self.outline_view = self.controller.outline_view

    def tearDown(self):
        self.controller.window.orderOut_(None)

    def list_row_texts(self):
        return [[self.controller.outlineView_objectValueForTableColumn_byItem_(self.outline_view, column, self.outline_view.itemAtRow_(row))
                 for column in self.outline_view.tableColumns()] for row in range(self.outline_view.numberOfRows())]

    def build_right_click_menu_titles(self, row):
        """Returns the menu's titles as if the row were right-clicked."""
        return [menu_item.title() for menu_item in build_right_click_menu(self.controller, self.outline_view, row).itemArray()]

    def select_rows(self, *rows):
        select_rows_of(self.outline_view, rows)

    def press_key(self, characters, key_code, modifier_flags=0):
        self.outline_view.keyDown_(make_key_down_event(self.controller.window, characters, key_code, modifier_flags))

    def test_the_saved_sessions_show_under_the_windows_they_were_saved_from(self):
        self.assertEqual(self.controller.window.title(), "Saved sessions")
        self.assertEqual([column.title() for column in self.outline_view.tableColumns()], ["Name", "Agent", "Folder", "Saved"])
        self.assertEqual(self.list_row_texts(), [
            ["agent-command-tab", "", "", "2026-11-06 07:52"],  # a window's latest save
            ["planning", "Claude Code", "~/code/agent-command-tab", "2026-11-06 07:52"],
            ["reviews", "Codex", "/tmp/x", "2026-11-05 23:28"],
            ["tmux: work", "", "", "2026-11-06 07:52"],
            ["notes", "Claude Code", "/tmp/x", "2026-11-06 07:52"]])

    def test_the_agent_column_shows_only_while_the_saved_sessions_are_of_two_or_more_tools(self):
        agent_column = self.outline_view.tableColumnWithIdentifier_("agent_tool_name")
        self.assertFalse(agent_column.isHidden())  # Claude Code and Codex
        saved_windows = make_saved_windows()
        saved_windows[0]["saved_sessions"][1]["agent_tool_name"] = "Claude Code"
        self.controller.show_saved_windows(saved_windows)
        self.assertTrue(agent_column.isHidden())
        self.controller.show_saved_windows(make_saved_windows())  # a Codex session saved again
        self.assertFalse(agent_column.isHidden())

    def list_names(self):
        return [row_texts[0] for row_texts in self.list_row_texts()]

    def test_a_saved_window_the_user_closed_stays_closed_when_the_list_is_shown_again(self):
        # A rename or a delete elsewhere in the list shows the list again, which mustn't open the rows the user closed.
        self.outline_view.collapseItem_(self.outline_view.itemAtRow_(0))
        self.assertEqual(self.list_names(), ["agent-command-tab", "tmux: work", "notes"])
        self.controller.show_saved_windows(rename_saved_session(make_saved_windows(), "c", "renamed notes"))
        self.assertEqual(self.list_names(), ["agent-command-tab", "tmux: work", "renamed notes"])
        # A window saved since is listed opened.
        self.controller.show_saved_windows(make_saved_windows() + [build_saved_window_record(
            [make_saved_session_record("d", "new notes")], "w9", "saved since", (0, 0, 800, 600))])
        self.assertEqual(self.list_names(), ["agent-command-tab", "tmux: work", "notes", "saved since", "new notes"])
        # The Saved sessions window opened again starts with every saved window opened, whatever the last one left.
        self.controller.window = self.controller.build_window()
        self.controller.show_saved_windows(make_saved_windows())
        self.outline_view = self.controller.outline_view
        self.assertEqual(self.list_names(), ["agent-command-tab", "planning", "reviews", "tmux: work", "notes"])

    def test_every_column_shows_in_the_narrowest_window_with_the_whole_agent_names_and_saved_times(self):
        # Left to AppKit, the columns keep their widths in a narrowed window, the Saved column out of sight, or, in a
        # window narrowed after it was wide, the Saved column alone is squeezed, down to nothing.
        scroll_view = self.outline_view.enclosingScrollView()
        self.assertTrue(scroll_view.hasHorizontalScroller())  # for columns dragged wider than the window
        narrowest_width = SMALLEST_WINDOW_SIZES["saved_sessions_window_frame"][0]
        self.assertEqual(self.controller.window.minSize().width, narrowest_width)
        columns = list(self.outline_view.tableColumns())

        def check_that_every_shown_column_shows_whole(window_width):
            frame = self.controller.window.frame()
            self.controller.window.setFrame_display_(NSMakeRect(frame.origin.x, frame.origin.y, window_width, frame.size.height), True)
            visible_rect = scroll_view.documentVisibleRect()
            for column_index, column in enumerate(columns):
                if column.isHidden():
                    continue
                column_rect = self.outline_view.rectOfColumn_(column_index)
                self.assertGreaterEqual(column_rect.origin.x, visible_rect.origin.x, (window_width, column.identifier()))
                self.assertLessEqual(column_rect.origin.x + column_rect.size.width, visible_rect.origin.x + visible_rect.size.width,
                                     (window_width, column.identifier()))
                if column.identifier() in ("agent_tool_name", "saved_at"):  # texts that mean little when cut short
                    for row in range(self.outline_view.numberOfRows()):
                        self.assertLessEqual(self.outline_view.preparedCellAtColumn_row_(column_index, row).cellSize().width,
                                             self.outline_view.frameOfCellAtColumn_row_(column_index, row).size.width,
                                             (window_width, column.identifier(), row))
        for window_width in (self.controller.window.frame().size.width, narrowest_width, 900, narrowest_width):
            check_that_every_shown_column_shows_whole(window_width)
        saved_windows = make_saved_windows()
        saved_windows[0]["saved_sessions"][1]["agent_tool_name"] = "Claude Code"  # one tool: no Agent column
        self.controller.show_saved_windows(saved_windows)
        check_that_every_shown_column_shows_whole(narrowest_width)
        self.controller.show_saved_windows(make_saved_windows())
        check_that_every_shown_column_shows_whole(narrowest_width)

    def test_rows_cant_be_dragged(self):
        for selector in ("outlineView:writeItems:toPasteboard:", "outlineView:pasteboardWriterForItem:"):
            self.assertFalse(self.controller.respondsToSelector_(selector), selector)

    def test_only_names_are_renamed_and_an_empty_or_unchanged_name_changes_nothing(self):
        name_column, agent_column = self.outline_view.tableColumns()[:2]
        window_item, session_item = self.outline_view.itemAtRow_(0), self.outline_view.itemAtRow_(1)
        self.assertTrue(self.controller.outlineView_shouldEditTableColumn_item_(self.outline_view, name_column, session_item))
        self.assertFalse(self.controller.outlineView_shouldEditTableColumn_item_(self.outline_view, agent_column, session_item))
        for new_name in ("  Plans ", "", "planning"):
            self.controller.outlineView_setObjectValue_forTableColumn_byItem_(self.outline_view, new_name, name_column, session_item)
        self.controller.outlineView_setObjectValue_forTableColumn_byItem_(self.outline_view, "Main", name_column, window_item)
        self.controller.outlineView_setObjectValue_forTableColumn_byItem_(self.outline_view, "Codex", agent_column, session_item)
        self.assertEqual(self.owner.calls, [("rename session", "a", "Plans"), ("rename window", ("iTerm2", "w1", None), "Main")])

    def test_esc_during_a_rename_puts_the_old_name_back_and_closing_then_renames_nothing(self):
        self.controller.window.orderFrontRegardless()
        self.controller.start_renaming(self.outline_view.itemAtRow_(1))
        self.outline_view.currentEditor().setString_("Cancelled name")
        self.controller.window.sendEvent_(make_key_down_event(self.controller.window, "\x1b", 53))
        self.assertEqual(self.outline_view.editedRow(), -1)
        self.assertEqual(self.list_row_texts()[1][0], "planning")
        self.controller.windowWillClose_(unittest.mock.Mock(object=lambda: self.controller.window))
        self.assertEqual(self.owner.calls, [])

    def test_restore_during_a_rename_keeps_the_new_name_first(self):  # so the restore gives the tab that name
        self.controller.window.orderFrontRegardless()
        self.controller.start_renaming(self.outline_view.itemAtRow_(1))
        self.outline_view.currentEditor().setString_("Renamed planning")
        self.controller.restore_selected_rows()
        self.assertEqual(self.owner.calls, [("rename session", "a", "Renamed planning"), ("restore", set(), {"a"})])

    def test_rename_edits_the_name_column_also_after_a_header_drag_moved_it(self):
        self.controller.window.orderFrontRegardless()
        self.outline_view.moveColumn_toColumn_(0, 2)  # Name dragged to the third place
        self.controller.start_renaming(self.outline_view.itemAtRow_(1))
        self.assertEqual(self.outline_view.tableColumns()[self.outline_view.editedColumn()].identifier(), "name")
        self.controller.window.makeFirstResponder_(None)

    def test_a_name_typed_and_ended_shows_in_its_row_once_the_owner_keeps_it(self):
        saved_windows = make_saved_windows()

        def keep_the_new_name(conversation_id, new_name):  # as the app controller does: the list is shown again at once
            self.controller.show_saved_windows(rename_saved_session(saved_windows, conversation_id, new_name))
        self.owner.rename_saved_session = keep_the_new_name
        self.controller.window.orderFrontRegardless()
        self.outline_view.editColumn_row_withEvent_select_(0, 1, None, True)
        self.outline_view.currentEditor().setString_("Plans")
        self.controller.window.makeFirstResponder_(None)  # a click elsewhere, or closing the window, ends the edit
        self.assertEqual(self.list_row_texts()[1][0], "Plans")
        self.assertEqual(self.outline_view.editedRow(), -1)

    def test_saved_windows_to_list_during_a_rename_end_it_without_renaming(self):
        # Listed under the name being typed (a session saved or restored meanwhile), they would end the rename keeping
        # the name typed so far, and the owner would have the list reloaded again from inside that reload.
        saved_windows_with_one_saved_since = make_saved_windows()
        saved_windows_with_one_saved_since[0]["saved_sessions"].insert(0, make_saved_session_record("z", "newly saved"))
        self.controller.window.orderFrontRegardless()
        self.controller.start_renaming(self.outline_view.itemAtRow_(1))
        self.outline_view.currentEditor().setString_("pla")
        self.controller.show_saved_windows(saved_windows_with_one_saved_since)
        self.assertEqual(self.outline_view.editedRow(), -1)
        self.assertEqual(self.list_names(), ["agent-command-tab", "newly saved", "planning", "reviews", "tmux: work", "notes"])
        self.assertIs(self.controller.window.firstResponder(), self.outline_view)  # the keys go to the rows again
        self.controller.window.makeFirstResponder_(None)  # as the window closing does: no name is left to keep
        self.assertEqual(self.owner.calls, [])

    def test_restore_sessions_chosen_again_brings_the_open_window_forward_with_its_rename_still_under_way(self):
        # Listed again in the open window, the same saved windows would end the rename without renaming. The tests'
        # app is not made active, and its window not the key window, in either part.
        self.controller.window.orderFrontRegardless()
        self.controller.start_renaming(self.outline_view.itemAtRow_(1))
        self.outline_view.currentEditor().setString_("Renamed planning")
        with unittest.mock.patch("agent_command_tab.user_interface.saved_sessions_window.NSApp"), \
                unittest.mock.patch.object(self.controller.window, "makeKeyAndOrderFront_") as bring_forward:
            self.controller.show(make_saved_windows(), None)  # as the app controller does for "Restore sessions…"
        bring_forward.assert_called_once_with(None)
        self.assertEqual(self.outline_view.editedRow(), 1)
        self.controller.window.makeFirstResponder_(None)  # the rename ends, as a click elsewhere ends it
        self.assertEqual(self.owner.calls, [("rename session", "a", "Renamed planning")])
        # Opened after it was closed, the window lists the saved windows it is opened with.
        self.controller.window.orderOut_(None)
        with unittest.mock.patch("agent_command_tab.user_interface.saved_sessions_window.NSApp"), \
                unittest.mock.patch.object(self.controller, "build_window", return_value=self.controller.window), \
                unittest.mock.patch.object(self.controller.window, "makeKeyAndOrderFront_"):
            self.controller.show(make_saved_windows()[1:], None)
        self.assertEqual(self.list_names(), ["tmux: work", "notes"])

    def test_rename_from_the_menu_renames_the_row_it_was_opened_on_though_the_rows_changed_since(self):
        # A session saved while the menu is open is listed above the row, which then has another number, and a new
        # item. Renamed by the number it had (while another rename was under way, whose end let the new rows in), the
        # name typed went to the row that took its place: the saved window's.
        saved_windows_with_one_saved_since = make_saved_windows()
        saved_windows_with_one_saved_since[0]["saved_sessions"].append(make_saved_session_record("z", "newly saved"))
        self.controller.window.orderFrontRegardless()
        self.controller.start_renaming(self.outline_view.itemAtRow_(1))  # "planning"
        self.outline_view.currentEditor().setString_("pla")
        self.build_right_click_menu_titles(4)  # "notes", right-clicked while "planning" is being renamed
        self.controller.show_saved_windows(saved_windows_with_one_saved_since)
        self.controller.menuRename_(None)
        self.assertEqual(self.list_names(), ["agent-command-tab", "planning", "reviews", "newly saved", "tmux: work", "notes"])
        self.assertEqual(self.outline_view.editedRow(), 5)  # "notes"
        self.outline_view.currentEditor().setString_("Notes 2")
        self.controller.window.makeFirstResponder_(None)  # the rename ends, as a click elsewhere ends it
        self.assertEqual(self.owner.calls, [("rename session", "c", "Notes 2")])
        # A row that left the list while its menu was open (its session was restored) renames nothing.
        self.build_right_click_menu_titles(5)
        self.controller.show_saved_windows(saved_windows_with_one_saved_since[:1])
        self.controller.menuRename_(None)
        self.assertEqual(self.outline_view.editedRow(), -1)
        self.assertEqual(len(self.owner.calls), 1)

    def test_delete_during_a_rename_keeps_the_new_name_first(self):  # or the list without the deleted row would end the rename unrenamed
        self.controller.window.orderFrontRegardless()
        self.controller.start_renaming(self.outline_view.itemAtRow_(1))
        self.outline_view.currentEditor().setString_("Renamed planning")
        self.controller.deleteClicked_(None)
        self.assertEqual(self.owner.calls, [("rename session", "a", "Renamed planning"), ("delete", set(), {"a"})])
        self.assertEqual(self.outline_view.editedRow(), -1)

    def test_the_right_click_menu_renames_or_deletes_and_delete_covers_the_selected_rows_it_was_opened_on(self):
        self.assertEqual(self.build_right_click_menu_titles(2), ["Rename", "Delete"])
        self.controller.menuDelete_(None)
        self.select_rows(0, 2)
        self.build_right_click_menu_titles(2)
        self.controller.menuDelete_(None)
        self.build_right_click_menu_titles(3)  # not selected: it alone
        self.controller.menuDelete_(None)
        self.assertEqual(self.owner.calls, [("delete", set(), {"b"}), ("delete", {("iTerm2", "w1", None)}, {"b"}),
                                            ("delete", {("tmux", "$1", 1793977852)}, set())])
        self.controller.window.orderFrontRegardless()
        self.build_right_click_menu_titles(4)
        self.controller.menuRename_(None)
        self.assertEqual((self.outline_view.editedRow(), self.outline_view.editedColumn()), (4, 0))

    def test_the_right_click_menu_items_say_what_they_do_on_mouse_over(self):
        menu = build_right_click_menu(self.controller, self.outline_view, 1)  # "planning", a saved session
        self.assertEqual([menu_item.toolTip() for menu_item in menu.itemArray()],
                         ["Renames this saved session", "Deletes this saved session from the list; its conversation file stays on disk"])
        menu = build_right_click_menu(self.controller, self.outline_view, 0)  # a saved window
        self.assertEqual([menu_item.toolTip() for menu_item in menu.itemArray()],
                         ["Renames this saved window", "Deletes these saved sessions from the list; their conversation files stay on disk"])

    def test_restore_and_delete_act_on_the_selected_rows_from_the_buttons_and_the_keys(self):
        self.outline_view.deselectAll_(None)
        self.assertFalse(self.controller.restore_button.isEnabled() or self.controller.delete_button.isEnabled())
        self.select_rows(1, 3)
        self.assertTrue(self.controller.restore_button.isEnabled() and self.controller.delete_button.isEnabled())
        self.controller.deleteClicked_(None)
        self.press_key("\x7f", 51)  # Delete
        self.controller.window.orderFrontRegardless()
        self.assertTrue(self.controller.is_open())
        self.controller.restoreClicked_(None)
        tmux_window_key = ("tmux", "$1", 1793977852)
        self.assertEqual(self.owner.calls, [("delete", {tmux_window_key}, {"a"})] * 2 + [("restore", {tmux_window_key}, {"a"})])
        self.assertFalse(self.controller.is_open())  # closed, so the restored tabs show

    def test_return_renames_the_selected_row_and_restores_nothing(self):
        self.controller.window.orderFrontRegardless()
        self.select_rows(1, 3)
        self.press_key("\r", 36)  # two rows selected: which to rename isn't clear, as in Finder
        self.assertEqual(self.outline_view.editedRow(), -1)
        self.select_rows(2)
        self.press_key("\r", 36)
        self.assertEqual((self.outline_view.editedRow(), self.outline_view.editedColumn()), (2, 0))
        self.assertEqual(self.owner.calls, [])  # nothing restored

    def test_command_r_and_command_shift_r_rename_the_selected_row_too(self):  # as in the list
        self.controller.window.orderFrontRegardless()
        for row, characters, modifier_flags in ((2, "r", NSEventModifierFlagCommand),
                                                (1, "R", NSEventModifierFlagCommand | NSEventModifierFlagShift)):
            self.select_rows(row)
            self.press_key(characters, 15, modifier_flags)
            self.assertEqual((self.outline_view.editedRow(), self.outline_view.editedColumn()), (row, 0), characters)
            self.outline_view.abortEditing()
        self.select_rows(2)
        self.press_key("r", 15)  # R alone types a name's first letter to select its row, as in Finder
        self.assertEqual(self.outline_view.editedRow(), -1)
        self.assertEqual(self.owner.calls, [])  # nothing renamed or restored

    def test_a_new_list_keeps_the_selection_and_esc_or_an_empty_list_closes_the_window(self):
        self.select_rows(2)  # reviews
        saved_windows = make_saved_windows()
        saved_windows[0]["saved_sessions"].insert(0, make_saved_session_record("z", "newly saved"))
        self.controller.show_saved_windows(saved_windows)
        self.assertEqual([item.key for item in self.controller.list_selected_items()], [("session", "b")])
        self.assertEqual(self.outline_view.selectedRow(), 3)
        self.controller.window.orderFrontRegardless()
        self.assertTrue(self.controller.is_open())
        self.controller.window.sendEvent_(make_key_down_event(self.controller.window, "\x1b", 53))
        self.assertFalse(self.controller.is_open())
        self.controller.window.orderFrontRegardless()
        self.assertTrue(self.controller.is_open())
        self.controller.show_saved_windows([])  # the last saved session was deleted
        self.assertFalse(self.controller.is_open())

    # ----- the search box -----

    def type_in_the_search_box(self, text):
        """Puts text in the search box as typing or its emptying button does, which tells the window."""
        self.controller.search_field.setStringValue_(text)
        self.controller.searchFieldChanged_(self.controller.search_field)

    def answer_the_last_conversation_search(self, conversation_ids):
        """Answers the search the window asked the owner for last, as the app controller does once the core has."""
        self.owner.conversation_searches[-1][2](conversation_ids)

    def search(self, text, conversation_ids_whose_conversations_match=()):
        self.type_in_the_search_box(text)
        if text:
            self.answer_the_last_conversation_search(list(conversation_ids_whose_conversations_match))

    def list_names_of_dimmed_rows(self):
        """Returns the names of the rows drawn dimmed, with no row selected (a selected row keeps its usual text color)."""
        self.outline_view.deselectAll_(None)
        dimmed_names = []
        for row in range(self.outline_view.numberOfRows()):
            cell = unittest.mock.Mock()
            item = self.outline_view.itemAtRow_(row)
            self.controller.outlineView_willDisplayCell_forTableColumn_item_(
                self.outline_view, cell, self.outline_view.tableColumnWithIdentifier_("name"), item)
            (text_color,), _ = cell.setTextColor_.call_args
            self.assertIn(text_color, (NSColor.secondaryLabelColor(), NSColor.controlTextColor()))
            if text_color == NSColor.secondaryLabelColor():
                dimmed_names.append(self.list_names()[row])
        return dimmed_names

    def test_the_search_box_and_its_checkbox_sit_above_the_column_titles_at_the_top_left(self):
        search_field, checkbox = self.controller.search_field, self.controller.case_sensitive_checkbox
        self.assertEqual((search_field.placeholderString(), checkbox.title()), ("Search", "aA"))
        self.assertIn("(⌘-F or /)", search_field.toolTip())
        self.assertEqual(checkbox.toolTip(), "Toggle whether the search is case sensitive")
        for content_size in ((720, 360), (480, 220), (900, 600)):  # as built, the narrowest, and larger
            self.controller.window.setContentSize_(content_size)
            content_height = self.controller.window.contentView().frame().size.height
            scroll_view_frame = self.outline_view.enclosingScrollView().frame()
            search_frame, checkbox_frame = search_field.frame(), checkbox.frame()
            for frame in (search_frame, checkbox_frame):
                self.assertGreaterEqual(frame.origin.y, scroll_view_frame.origin.y + scroll_view_frame.size.height, content_size)
                self.assertLessEqual(frame.origin.y + frame.size.height, content_height, content_size)
            self.assertEqual(search_frame.origin.x, 12)
            self.assertGreater(checkbox_frame.origin.x, search_frame.origin.x + search_frame.size.width)
            self.assertLessEqual(checkbox_frame.origin.x + checkbox_frame.size.width, content_size[0], content_size)
            # The rows keep the room between the search box and the buttons.
            self.assertEqual(scroll_view_frame.origin.y + scroll_view_frame.size.height, content_height - 34, content_size)
            self.assertGreater(scroll_view_frame.origin.y,
                               self.controller.restore_button.frame().origin.y + self.controller.restore_button.frame().size.height)

    def test_the_search_shows_name_matches_and_dimmed_conversation_matches_then_every_row_again(self):
        self.search("notes", conversation_ids_whose_conversations_match=["a", "c"])
        self.assertEqual(self.owner.conversation_searches[-1][:2], ("notes", False))
        # "notes" by its name, and "planning" only by its conversation, under its window, without "reviews".
        self.assertEqual(self.list_names(), ["agent-command-tab", "planning", "tmux: work", "notes"])
        self.assertEqual([item.key for item in self.controller.list_selected_items()], [("session", "a")])  # the first one found
        self.assertTrue(self.controller.restore_button.isEnabled())
        self.assertEqual(self.list_names_of_dimmed_rows(), ["planning"])
        # A saved window whose name holds the text shows all its sessions, none of them dimmed.
        self.search("TMUX", conversation_ids_whose_conversations_match=["c"])
        self.assertEqual(self.list_names(), ["tmux: work", "notes"])
        self.assertEqual(self.list_names_of_dimmed_rows(), [])
        self.search("found nowhere")
        self.assertEqual(self.list_names(), [])
        self.assertFalse(self.controller.restore_button.isEnabled() or self.controller.delete_button.isEnabled())
        searches_asked_for = len(self.owner.conversation_searches)
        self.search("")
        self.assertEqual(self.list_names(), ["agent-command-tab", "planning", "reviews", "tmux: work", "notes"])
        self.assertEqual(self.list_names_of_dimmed_rows(), [])
        self.assertEqual(len(self.owner.conversation_searches), searches_asked_for)  # an empty box needs no answer

    def test_a_selected_row_the_search_still_lists_stays_selected_and_is_not_dimmed(self):
        self.select_rows(2)  # reviews
        self.search("e", conversation_ids_whose_conversations_match=["b", "c"])  # in "reviews", "notes" and both windows' names
        self.assertEqual(self.list_names(), ["agent-command-tab", "planning", "reviews", "tmux: work", "notes"])
        self.assertEqual([item.key for item in self.controller.list_selected_items()], [("session", "b")])
        self.search("zzz", conversation_ids_whose_conversations_match=["b"])
        self.assertEqual(self.list_names(), ["agent-command-tab", "reviews"])
        cell = unittest.mock.Mock()
        self.controller.outlineView_willDisplayCell_forTableColumn_item_(
            self.outline_view, cell, self.outline_view.tableColumnWithIdentifier_("name"), self.outline_view.itemAtRow_(1))
        cell.setTextColor_.assert_called_once_with(NSColor.controlTextColor())  # selected: the selection's own text color
        self.assertEqual(self.list_names_of_dimmed_rows(), ["reviews"])

    def test_a_slow_answer_shows_the_name_matches_meanwhile_and_adds_the_conversation_matches_when_it_comes(self):
        self.type_in_the_search_box("plan")
        self.assertEqual(len(self.list_names()), 5)  # the rows wait for the answer, for half a second
        self.assertTrue(self.controller.search_box_search.answer_wait_timer.isValid())
        self.controller.search_box_search.answer_wait_timer.fire()
        self.assertEqual(self.list_names(), ["agent-command-tab", "planning"])
        self.answer_the_last_conversation_search(["c"])
        self.assertEqual(self.list_names(), ["agent-command-tab", "planning", "tmux: work", "notes"])
        self.assertIsNone(self.controller.search_box_search.answer_wait_timer)
        # An owner that couldn't answer (the core stopped) leaves the name matches alone.
        self.type_in_the_search_box("planning")
        self.answer_the_last_conversation_search(None)
        self.assertEqual(self.list_names(), ["agent-command-tab", "planning"])

    def test_one_search_is_asked_at_a_time_and_an_answer_for_an_earlier_text_is_left_out(self):
        self.type_in_the_search_box("p")
        self.type_in_the_search_box("pl")
        self.type_in_the_search_box("pla")
        self.assertEqual([search[:2] for search in self.owner.conversation_searches], [("p", False)])
        self.answer_the_last_conversation_search(["b", "c"])  # for "p": the box holds "pla" by now
        self.assertEqual(len(self.list_names()), 5)  # still the rows from before the search
        self.assertEqual([search[:2] for search in self.owner.conversation_searches], [("p", False), ("pla", False)])
        self.answer_the_last_conversation_search(["c"])
        self.assertEqual(self.list_names(), ["agent-command-tab", "planning", "tmux: work", "notes"])
        self.assertEqual(len(self.owner.conversation_searches), 2)

    def test_saved_windows_listed_during_a_search_are_searched_too(self):
        self.search("notes")
        self.assertEqual(self.list_names(), ["tmux: work", "notes"])
        self.controller.show_saved_windows(make_saved_windows() + [build_saved_window_record(
            [make_saved_session_record("d", "more notes"), make_saved_session_record("e", "plans")], "w9", "saved since",
            (0, 0, 800, 600))])
        self.assertEqual(self.list_names(), ["tmux: work", "notes", "saved since", "more notes"])  # by its name, at once
        self.assertEqual(self.owner.conversation_searches[-1][:2], ("notes", False))  # and its conversation is asked about
        self.answer_the_last_conversation_search(["e"])
        self.assertEqual(self.list_names(), ["tmux: work", "notes", "saved since", "more notes", "plans"])
        self.controller.show_saved_windows([])  # the last saved session was deleted: the window closes, search or not
        self.assertFalse(self.controller.is_open())

    def test_a_search_opens_the_rows_it_shows_and_the_rows_after_it_are_closed_as_before_it(self):
        self.outline_view.collapseItem_(self.outline_view.itemAtRow_(0))  # agent-command-tab
        self.search("planning")
        self.assertEqual(self.list_names(), ["agent-command-tab", "planning"])
        self.search("re", conversation_ids_whose_conversations_match=["c"])
        self.assertEqual(self.list_names(), ["agent-command-tab", "reviews", "tmux: work", "notes"])
        self.search("")
        self.assertEqual(self.list_names(), ["agent-command-tab", "tmux: work", "notes"])

    def test_a_saved_windows_row_stands_for_its_sessions_that_a_search_lists(self):
        self.search("reviews")
        self.assertEqual(self.list_names(), ["agent-command-tab", "reviews"])
        self.select_rows(0)  # the saved window, one of whose two sessions is listed
        self.controller.deleteClicked_(None)
        self.controller.window.orderFrontRegardless()
        self.controller.restoreClicked_(None)
        self.assertEqual(self.owner.calls, [("delete", set(), {"b"}), ("restore", set(), {"b"})])
        self.owner.calls.clear()
        self.search("agent-command")  # the window's own name: all its sessions are listed
        self.select_rows(0)
        self.controller.deleteClicked_(None)
        self.assertEqual(self.owner.calls, [("delete", {("iTerm2", "w1", None)}, set())])
        self.assertIn("a window's row stands for all of its sessions that are listed", self.controller.restore_button.toolTip())

    def test_restore_during_a_rename_restores_the_renamed_row_though_the_search_no_longer_lists_it(self):
        # Keeping the name lists the rows again, without the renamed one when the search doesn't hold its new name;
        # the rows then select the first one found, which the restore mustn't take for the row to restore.
        def keep_the_new_name(conversation_id, new_name):  # as the app controller does: the list is shown again at once
            self.owner.calls.append(("rename session", conversation_id, new_name))
            self.controller.show_saved_windows(rename_saved_session(make_saved_windows(), conversation_id, new_name))
        self.owner.rename_saved_session = keep_the_new_name
        self.controller.window.orderFrontRegardless()
        self.search("re", conversation_ids_whose_conversations_match=["c"])  # "reviews" by its name, "notes" by its conversation
        self.assertEqual(self.list_names(), ["agent-command-tab", "reviews", "tmux: work", "notes"])
        self.controller.start_renaming(self.outline_view.itemAtRow_(1))
        self.outline_view.currentEditor().setString_("Plans")
        self.controller.restoreClicked_(None)
        self.assertEqual(self.owner.calls, [("rename session", "b", "Plans"), ("restore", set(), {"b"})])
        self.assertEqual(self.list_names(), ["tmux: work", "notes"])  # what the search lists since

    def test_the_checkbox_tells_the_owner_and_the_owners_choice_has_the_search_count_case(self):
        checkbox = self.controller.case_sensitive_checkbox
        self.assertEqual(checkbox.state(), NSControlStateValueOff)
        self.search("Notes")
        self.assertEqual(self.list_names(), ["tmux: work", "notes"])
        checkbox.setState_(NSControlStateValueOn)  # a click
        self.controller.caseSensitiveCheckboxClicked_(checkbox)
        self.assertEqual(self.owner.calls, [("search is case sensitive", True)])
        self.controller.show_search_is_case_sensitive(True)  # as the app controller does for every search box
        self.assertEqual(self.owner.conversation_searches[-1][:2], ("Notes", True))
        self.answer_the_last_conversation_search(["a"])
        self.assertEqual(self.list_names(), ["agent-command-tab", "planning"])  # "notes" no longer by its name
        # A window opened again starts with the choice, and an empty box.
        self.controller.window = self.controller.build_window()
        self.assertEqual((self.controller.case_sensitive_checkbox.state(), self.controller.search_field.stringValue()),
                         (NSControlStateValueOn, ""))
        self.controller.show_saved_windows(make_saved_windows())
        self.outline_view = self.controller.outline_view
        self.assertEqual(len(self.list_names()), 5)
        # Unchecked for a controller whose window was never opened, the window is built unchecked.
        controller = SavedSessionsWindowController.alloc().initWithOwner_(self.owner)
        controller.show_search_is_case_sensitive(True)
        controller.show_search_is_case_sensitive(False)
        controller.window = controller.build_window()
        self.assertEqual(controller.case_sensitive_checkbox.state(), NSControlStateValueOff)

    def test_command_f_and_slash_put_the_keyboard_in_the_search_box_and_its_keys_work_the_rows(self):
        window, search_field = self.controller.window, self.controller.search_field
        window.orderFrontRegardless()

        def press_in_the_search_box(command_selector):
            return self.controller.control_textView_doCommandBySelector_(search_field, None, command_selector)
        for characters, key_code, modifier_flags in (("f", 3, NSEventModifierFlagCommand), ("/", 44, 0),
                                                     ("F", 3, NSEventModifierFlagCommand | NSEventModifierFlagShift)):
            window.makeFirstResponder_(self.outline_view)
            self.press_key(characters, key_code, modifier_flags)
            self.assertIs(search_field.currentEditor(), window.firstResponder(), characters)
        self.outline_view.deselectAll_(None)
        self.assertTrue(press_in_the_search_box("moveDown:"))  # the first saved session's row, not its window's
        self.assertEqual(self.outline_view.selectedRow(), 1)
        self.assertTrue(press_in_the_search_box("moveDown:"))
        self.assertTrue(press_in_the_search_box("moveDown:"))
        self.assertEqual(self.outline_view.selectedRow(), 4)  # past the tmux window's row
        self.assertTrue(press_in_the_search_box("moveDown:"))
        self.assertEqual(self.outline_view.selectedRow(), 4)  # the last one: it stays
        self.assertTrue(press_in_the_search_box("moveUp:"))
        self.assertEqual(self.outline_view.selectedRow(), 2)
        self.outline_view.deselectAll_(None)
        self.assertTrue(press_in_the_search_box("moveUp:"))
        self.assertEqual(self.outline_view.selectedRow(), 4)  # with none selected, Up takes the last
        self.assertFalse(press_in_the_search_box("moveLeft:"))  # other keys edit the text
        # Return gives the rows the keyboard, with the row the search selected, and restores nothing.
        self.search("reviews")
        self.assertTrue(press_in_the_search_box("insertNewline:"))
        self.assertIs(window.firstResponder(), self.outline_view)
        self.assertEqual([item.key for item in self.controller.list_selected_items()], [("session", "b")])
        self.assertEqual(self.owner.calls, [])
        # Esc empties the box first, then closes the window.
        self.assertTrue(press_in_the_search_box("cancelOperation:"))
        self.assertEqual((search_field.stringValue(), len(self.list_names())), ("", 5))
        self.assertTrue(self.controller.is_open())
        self.assertTrue(press_in_the_search_box("cancelOperation:"))
        self.assertFalse(self.controller.is_open())

    def test_esc_in_a_rename_still_ends_the_rename_alone_while_a_search_is_shown(self):
        self.controller.window.orderFrontRegardless()
        self.search("reviews")
        self.controller.start_renaming(self.outline_view.itemAtRow_(1))
        self.assertEqual(self.outline_view.editedRow(), 1)
        self.assertTrue(self.controller.control_textView_doCommandBySelector_(self.outline_view, None, "cancelOperation:"))
        self.assertEqual(self.outline_view.editedRow(), -1)
        self.assertTrue(self.controller.is_open())
        self.assertEqual(self.controller.search_field.stringValue(), "reviews")

    # ----- sorting by a column's title -----

    def click_column_title(self, column_name):
        self.controller.outlineView_didClickTableColumn_(self.outline_view, self.outline_view.tableColumnWithIdentifier_(column_name))

    def list_title_arrows_and_tool_tips(self):
        """Returns {column name: (the name of its title's arrow image, or None; its title's hover text)}."""
        return {str(column.identifier()): (
            (self.outline_view.indicatorImageInTableColumn_(column).name() if self.outline_view.indicatorImageInTableColumn_(column)
             is not None else None), column.headerToolTip()) for column in self.outline_view.tableColumns()}

    def test_a_click_on_a_column_title_sorts_by_it_then_the_other_way_then_as_saved(self):
        sort_tool_tip, reverse_tool_tip, stop_tool_tip = ("Click to sort the list by this column",
                                                          "Click to reverse-sort the list by this column",
                                                          "Click to stop sorting the list by this column")
        self.assertEqual(set(self.list_title_arrows_and_tool_tips().values()), {(None, sort_tool_tip)})
        self.select_rows(2)  # reviews
        self.click_column_title("name")  # A to Z: the windows by their names, and each one's sessions
        self.assertEqual(self.list_names(), ["agent-command-tab", "planning", "reviews", "tmux: work", "notes"])
        self.assertEqual(self.list_title_arrows_and_tool_tips()["name"], ("NSAscendingSortIndicator", reverse_tool_tip))
        self.click_column_title("name")
        self.assertEqual(self.list_names(), ["tmux: work", "notes", "agent-command-tab", "reviews", "planning"])
        self.assertEqual([item.key for item in self.controller.list_selected_items()], [("session", "b")])  # still selected
        self.assertEqual(self.list_title_arrows_and_tool_tips(), {
            "name": ("NSDescendingSortIndicator", stop_tool_tip), "agent_tool_name": (None, sort_tool_tip),
            "folder": (None, sort_tool_tip), "saved_at": (None, sort_tool_tip)})
        self.click_column_title("name")
        self.assertEqual(self.list_names(), ["agent-command-tab", "planning", "reviews", "tmux: work", "notes"])  # as saved
        self.assertEqual(set(self.list_title_arrows_and_tool_tips().values()), {(None, sort_tool_tip)})
        # The Saved column starts with the latest, and another title starts over in its own first direction.
        self.click_column_title("name")
        self.click_column_title("saved_at")
        self.assertEqual(self.list_title_arrows_and_tool_tips()["saved_at"], ("NSDescendingSortIndicator", reverse_tool_tip))
        self.assertEqual(self.list_title_arrows_and_tool_tips()["name"], (None, sort_tool_tip))
        self.click_column_title("saved_at")  # the earliest first: "reviews" before "planning" in its window
        self.assertEqual(self.list_names(), ["agent-command-tab", "reviews", "planning", "tmux: work", "notes"])
        self.assertEqual(self.owner.calls, [])  # the saved list itself keeps its order

    def test_the_sort_order_stays_for_a_search_a_new_list_and_a_window_opened_again(self):
        self.click_column_title("name")
        self.click_column_title("name")  # Z to A
        self.search("e", conversation_ids_whose_conversations_match=["a"])
        self.assertEqual(self.list_names(), ["tmux: work", "notes", "agent-command-tab", "reviews", "planning"])
        self.search("")
        self.controller.show_saved_windows(rename_saved_session(make_saved_windows(), "a", "a plan"))
        self.assertEqual(self.list_names(), ["tmux: work", "notes", "agent-command-tab", "reviews", "a plan"])
        self.controller.window = self.controller.build_window()
        self.controller.show_saved_windows(make_saved_windows())
        self.outline_view = self.controller.outline_view
        self.assertEqual(self.list_names(), ["tmux: work", "notes", "agent-command-tab", "reviews", "planning"])
        self.assertEqual(self.list_title_arrows_and_tool_tips()["name"][0], "NSDescendingSortIndicator")


class SavedSessionsOrderTests(unittest.TestCase):
    """list_saved_windows_and_sessions_in_the_order_shown: three saved windows, saved in this order.
    "zebra" holds a Codex session in /b saved at 30 and a Claude Code session in ~/a saved at 10;
    "Apple" holds an agy session in /c saved at 20; "mango" holds a Claude Code session in /a saved at 20."""

    def setUp(self):
        self.saved_windows = [
            build_saved_window_record([make_saved_session_record("z1", "beta", "Codex", "/b", saved_at=30),
                                       make_saved_session_record("z2", "Alpha", folder="/Users/someone/a", saved_at=10)],
                                      "w1", "zebra", None),
            build_saved_window_record([make_saved_session_record("a1", "delta", "agy", "/c", saved_at=20)], "w2", "Apple", None),
            build_saved_window_record([make_saved_session_record("m1", "gamma", folder="/a", saved_at=20)], "w3", "mango", None)]

    def list_names_in_order(self, sort_column_name, sort_is_reversed=False, text="", conversation_ids=()):
        return [(saved_window["name"], [saved_session["name"] for saved_session in saved_sessions])
                for saved_window, saved_sessions in list_saved_windows_and_sessions_in_the_order_shown(
                    self.saved_windows, SavedSessionsSearch(text, False, frozenset(conversation_ids)), sort_column_name,
                    sort_is_reversed, "/Users/someone")]

    def test_unsorted_rows_keep_the_order_they_were_saved_in(self):
        self.assertEqual(self.list_names_in_order(None), [("zebra", ["beta", "Alpha"]), ("Apple", ["delta"]), ("mango", ["gamma"])])

    def test_names_sort_ignoring_case_the_windows_by_their_own_names(self):
        self.assertEqual(self.list_names_in_order("name"), [("Apple", ["delta"]), ("mango", ["gamma"]), ("zebra", ["Alpha", "beta"])])
        self.assertEqual(self.list_names_in_order("name", sort_is_reversed=True),
                         [("zebra", ["beta", "Alpha"]), ("mango", ["gamma"]), ("Apple", ["delta"])])

    def test_the_saved_column_starts_with_the_latest_a_window_going_by_its_latest_session(self):
        # "Apple" and "mango" were both saved at 20, and keep the order they were saved in, either way.
        self.assertEqual(self.list_names_in_order("saved_at"), [("zebra", ["beta", "Alpha"]), ("Apple", ["delta"]), ("mango", ["gamma"])])
        self.assertEqual(self.list_names_in_order("saved_at", sort_is_reversed=True),
                         [("Apple", ["delta"]), ("mango", ["gamma"]), ("zebra", ["Alpha", "beta"])])

    def test_a_column_where_a_windows_row_is_empty_sorts_the_windows_by_their_first_session_in_that_order(self):
        # Agents: agy, Claude Code, then zebra's Claude Code before its Codex.
        self.assertEqual(self.list_names_in_order("agent_tool_name"),
                         [("Apple", ["delta"]), ("zebra", ["Alpha", "beta"]), ("mango", ["gamma"])])
        # Folders as shown: /a, /b (zebra's first of /b and ~/a), /c.
        self.assertEqual(self.list_names_in_order("folder"), [("mango", ["gamma"]), ("zebra", ["beta", "Alpha"]), ("Apple", ["delta"])])
        self.assertEqual(self.list_names_in_order("folder", sort_is_reversed=True),
                         [("zebra", ["Alpha", "beta"]), ("Apple", ["delta"]), ("mango", ["gamma"])])

    def test_a_search_leaves_out_rows_before_the_sort_and_a_window_goes_by_the_sessions_it_shows(self):
        # Only "beta" (its name) and "delta" (its conversation) show; zebra now goes by "beta" in /b.
        self.assertEqual(self.list_names_in_order("folder", text="bet", conversation_ids=["a1"]),
                         [("zebra", ["beta"]), ("Apple", ["delta"])])
        self.assertEqual(self.list_names_in_order(None, text="found nowhere"), [])


def make_stand_in_app_controller_for_saved_sessions(**attributes):
    """A stand-in AppController with these attributes whose saved-list and action helpers are the real ones,
    with one list panel (a Mock), which it tells when the saved list changes."""
    return make_stand_in_app_controller_with_real_methods(
        ["change_saved_windows", "submit_action_and_report_failure"],
        **{"list_all_list_panel_controllers.return_value": [unittest.mock.Mock()]}, **attributes)


def run_started_threads_at_once():
    """Returns a patch under which starting a thread runs its target at once, on the thread that starts it."""
    return unittest.mock.patch.object(threading.Thread, "start", threading.Thread.run)


class InstalledAgentToolsInTheMenusTests(unittest.TestCase):
    def test_the_menus_offer_only_the_included_tools_that_are_installed(self):
        stand_in_app_controller = make_stand_in_app_controller_with_real_methods(
            ["list_agent_tool_names", "find_installed_agent_tools", "note_installed_agent_tools"],
            settings=copy.deepcopy(DEFAULT_SETTINGS), installed_agent_tool_names=None, installed_agent_tools_checked_at=time.time())
        self.assertEqual(stand_in_app_controller.list_agent_tool_names(), ["Claude Code", "Codex", "agy"])  # not checked yet
        problem_by_launch_command = {"claude": "", "codex": "", "agy": "agy: command not found"}
        with run_started_threads_at_once(), \
                unittest.mock.patch("agent_command_tab.app_controller.find_launch_command_problem",
                                    problem_by_launch_command.get), run_main_thread_calls_at_once():
            stand_in_app_controller.find_installed_agent_tools(stand_in_app_controller.settings["agent_tools"])
        self.assertEqual(stand_in_app_controller.list_agent_tool_names(), ["Claude Code", "Codex"])  # no agy installed
        # The settings window shows the same check's answers, so it starts no check of its own.
        show_launch_command_problems = stand_in_app_controller.settings_window_controller.show_launch_command_problems
        show_launch_command_problems.assert_called_once_with(problem_by_launch_command)
        stand_in_app_controller.settings["agent_tools"][0]["is_included"] = False  # left out in Settings
        self.assertEqual(stand_in_app_controller.list_agent_tool_names(), ["Codex"])
        # An answer about launch commands changed since is dropped: the change's own check decides.
        checked_agent_tools = copy.deepcopy(stand_in_app_controller.settings["agent_tools"])
        stand_in_app_controller.settings["agent_tools"][2]["launch_command"] = "~/.local/bin/agy"
        stand_in_app_controller.note_installed_agent_tools(checked_agent_tools, {"claude": "", "codex": "", "agy": ""})
        self.assertEqual(stand_in_app_controller.list_agent_tool_names(), ["Codex"])
        show_launch_command_problems.assert_called_once()
        # A menu that opens a while after the last check has the tools checked again, and still shows that last
        # check's answer: the main thread takes the new one only after the menu's call returns. agy, installed
        # since, is in the next menu.
        stand_in_app_controller.installed_agent_tools_checked_at -= SECONDS_BEFORE_THE_INSTALLED_AGENT_TOOLS_ARE_CHECKED_AGAIN
        with run_started_threads_at_once(), \
                unittest.mock.patch("agent_command_tab.app_controller.find_launch_command_problem",
                                    {"claude": "", "codex": "", "~/.local/bin/agy": ""}.get), \
                unittest.mock.patch("agent_command_tab.app_controller.AppHelper.callAfter") as call_after:
            self.assertEqual(stand_in_app_controller.list_agent_tool_names(), ["Codex"])
            function_for_the_main_thread, *its_arguments = call_after.call_args.args
            function_for_the_main_thread(*its_arguments)
            self.assertEqual(stand_in_app_controller.list_agent_tool_names(), ["Codex", "agy"])
        call_after.assert_called_once()  # the next menu, opened right after, starts no check of its own

    def test_a_changed_launch_command_is_checked_again(self):
        for changed_agent_tool_field, value, is_checked_again in (("launch_command", "~/.local/bin/agy", True),
                                                                   ("is_included", False, False)):
            stand_in_app_controller = unittest.mock.Mock(settings=copy.deepcopy(DEFAULT_SETTINGS))
            new_settings = copy.deepcopy(DEFAULT_SETTINGS)
            new_settings["agent_tools"][2][changed_agent_tool_field] = value
            with unittest.mock.patch("agent_command_tab.app_controller.save_settings"):
                AppController.settings_changed(stand_in_app_controller, new_settings)
            self.assertEqual([call.args for call in stand_in_app_controller.find_installed_agent_tools.call_args_list],
                             [(new_settings["agent_tools"],)] if is_checked_again else [], changed_agent_tool_field)


class SavedSessionsInTheAppTests(unittest.TestCase):
    def test_restore_sessions_is_in_the_menu_bar_icons_menu_only_while_sessions_are_saved(self):
        status_item_controller = MenuBarStatusItemController.alloc().init()  # no status item of its own
        status_item_controller.owner = unittest.mock.Mock()
        for has_saved_sessions, expected_titles in (
                (False, ["Show popup", "Show sidebar", "Show window", "---", "Settings…", "---", "Quit AgentCommandTab"]),
                (True, ["Show popup", "Show sidebar", "Show window", "---", "Restore sessions…", "Settings…", "---",
                        "Quit AgentCommandTab"])):
            status_item_controller.owner.has_saved_agent_sessions.return_value = has_saved_sessions
            menu = status_item_controller.build_right_click_menu()
            self.assertEqual(["---" if menu_item.isSeparatorItem() else menu_item.title() for menu_item in menu.itemArray()],
                             expected_titles)
            self.assertEqual(list_menu_items_without_a_tool_tip(menu), [])
        status_item_controller.restoreSessionsClicked_(None)
        status_item_controller.owner.open_saved_sessions_window.assert_called_once_with()

    def test_a_search_box_gets_the_ids_the_core_found_or_none_when_it_couldnt_answer(self):
        stand_in_app_controller = make_stand_in_app_controller_with_real_methods(
            ["find_agent_sessions_whose_conversations_contain", "find_saved_agent_sessions_whose_conversations_contain",
             "ask_the_core_which_conversations_contain"], saved_windows=make_saved_windows())
        answers = []
        with run_main_thread_calls_at_once():
            # The Saved sessions window's search: every saved session goes to the core with the text.
            make_actions_finish_with(stand_in_app_controller, ActionResult(True, details={"conversation_ids": ["c"]}))
            stand_in_app_controller.find_saved_agent_sessions_whose_conversations_contain("notes", True, answers.append)
            saved_sessions = [saved_session for saved_window in make_saved_windows() for saved_session in saved_window["saved_sessions"]]
            self.assertEqual([saved_session["conversation_id"] for saved_session in saved_sessions], ["a", "b", "c"])
            self.assertEqual(stand_in_app_controller.submit_action.call_args.args[0], ActionRequest(
                "find_saved_agent_sessions_whose_conversations_contain", None,
                {"text": "notes", "is_case_sensitive": True, "saved_sessions": saved_sessions}))
            # A list's search.
            make_actions_finish_with(stand_in_app_controller, ActionResult(True, details={"session_ids": ["s1"]}))
            stand_in_app_controller.find_agent_sessions_whose_conversations_contain("notes", False, answers.append)
            self.assertEqual(stand_in_app_controller.submit_action.call_args.args[0], ActionRequest(
                "find_agent_sessions_whose_conversations_contain", None, {"text": "notes", "is_case_sensitive": False}))
            # A core that failed, or stopped before it answered: None, and the rows keep their name matches.
            make_actions_finish_with(stand_in_app_controller, ActionResult(False, "the core couldn't read them"))
            stand_in_app_controller.find_saved_agent_sessions_whose_conversations_contain("notes", False, answers.append)
            stopped_future = concurrent.futures.Future()
            stopped_future.set_exception(RuntimeError("the core stopped"))
            stand_in_app_controller.submit_action.return_value = stopped_future
            stand_in_app_controller.find_agent_sessions_whose_conversations_contain("notes", False, answers.append)
        self.assertEqual(answers, [["c"], ["s1"], None, None])

    def test_the_saved_sessions_windows_search_counts_case_as_the_lists_searches_do(self):
        list_panel_controller = unittest.mock.Mock()
        stand_in_app_controller = make_stand_in_app_controller_with_real_methods(
            ["apply_list_settings", "search_is_case_sensitive_chosen"], settings=DEFAULT_SETTINGS,
            **{"list_all_list_panel_controllers.return_value": [list_panel_controller]})
        stand_in_app_controller.search_is_case_sensitive_chosen(False)  # as it is: nothing to save
        stand_in_app_controller.settings_changed.assert_not_called()
        stand_in_app_controller.search_is_case_sensitive_chosen(True)  # the "aA" checkbox of any search box
        settings_with_the_choice = dict(DEFAULT_SETTINGS, search_is_case_sensitive=True)
        stand_in_app_controller.settings_changed.assert_called_once_with(settings_with_the_choice)
        stand_in_app_controller.apply_list_settings(settings_with_the_choice)  # which every settings change ends in
        list_panel_controller.show_search_is_case_sensitive.assert_called_once_with(True)
        stand_in_app_controller.saved_sessions_window_controller.show_search_is_case_sensitive.assert_called_once_with(True)

    def test_a_save_keeps_what_the_core_found_and_names_what_it_couldnt(self):
        with tempfile.TemporaryDirectory() as folder:
            stand_in_app_controller = make_stand_in_app_controller_for_saved_sessions(
                saved_windows=make_saved_windows()[:1], saved_agent_sessions_file_path=os.path.join(folder, "saved.json"))
            newly_saved_window = make_saved_windows()[1]
            make_actions_finish_with(stand_in_app_controller, ActionResult(
                False, '"aider": aider can\'t resume a saved conversation', details={"saved_windows": [newly_saved_window]}))
            with run_main_thread_calls_at_once():
                AppController.save_agent_sessions(stand_in_app_controller, ["s1", "s9"])
            self.assertEqual(stand_in_app_controller.submit_action.call_args.args[0],
                             ActionRequest("save_agent_sessions", {"session_ids": ["s1", "s9"]}))
            self.assertEqual(stand_in_app_controller.saved_windows, make_saved_windows())
            with open(os.path.join(folder, "saved.json")) as file:
                self.assertEqual(json.load(file), make_saved_windows())
            stand_in_app_controller.saved_sessions_window_controller.show_saved_windows.assert_called_once_with(make_saved_windows())
            # Each list's bottom bar is told, so its Restore button is clickable from now on.
            (list_panel_controller,) = stand_in_app_controller.list_all_list_panel_controllers()
            list_panel_controller.show_whether_sessions_are_saved.assert_called_once_with()
            self.assertEqual(stand_in_app_controller.report_action_failure.call_args.args[1], "Not every session was saved")

    def test_restore_sends_the_chosen_sessions_and_takes_those_it_restored_out_of_the_list(self):
        with tempfile.TemporaryDirectory() as folder:
            stand_in_app_controller = make_stand_in_app_controller_for_saved_sessions(
                saved_windows=make_saved_windows(), settings=DEFAULT_SETTINGS,
                saved_agent_sessions_file_path=os.path.join(folder, "saved.json"))
            make_actions_finish_with(stand_in_app_controller, ActionResult(
                False, '"notes": the folder /tmp/x doesn\'t exist', details={"restored_conversation_ids": ["a"]}))
            with run_main_thread_calls_at_once():
                AppController.restore_saved_agent_sessions(stand_in_app_controller, {("tmux", "$1", 1793977852)}, {"a"})
            action_request = stand_in_app_controller.submit_action.call_args.args[0]
            self.assertEqual(action_request.action, "restore_saved_agent_sessions")
            self.assertEqual([[saved_session["conversation_id"] for saved_session in saved_window["saved_sessions"]]
                              for saved_window in action_request.arguments["saved_windows"]], [["a"], ["c"]])
            self.assertEqual(action_request.arguments["launch_command_by_agent_tool_name"],
                             {"Claude Code": "claude", "Codex": "codex", "agy": "agy"})
            # The restored session leaves the list; the one that failed stays.
            self.assertEqual([[saved_session["conversation_id"] for saved_session in saved_window["saved_sessions"]]
                              for saved_window in stand_in_app_controller.saved_windows], [["b"], ["c"]])
            with open(os.path.join(folder, "saved.json")) as file:
                self.assertEqual(json.load(file), stand_in_app_controller.saved_windows)
            self.assertEqual(stand_in_app_controller.report_action_failure.call_args.args[1], "Not every session was restored")

    def test_the_tab_the_core_asks_for_during_a_restore_is_shown_as_return_on_its_row_shows_it(self):
        stand_in_app_controller = make_stand_in_app_controller_with_real_methods(
            ["receive_request_to_show_a_tab_on_background_thread", "show_tab_the_core_asked_for", "submit_action_and_report_failure"])
        make_actions_finish_with(stand_in_app_controller, ActionResult(False, "that tab no longer exists"))
        with run_main_thread_calls_at_once():
            stand_in_app_controller.receive_request_to_show_a_tab_on_background_thread("s7")
        self.assertEqual(stand_in_app_controller.submit_action.call_args.args[0],
                         ActionRequest("focus_agent_session", {"session_id": "s7"}))
        self.assertEqual(stand_in_app_controller.report_action_failure.call_args.args[1], "The tab couldn't be shown")

    def test_the_alert_after_a_restore_gives_the_keyboard_back_to_the_app_it_took_it_from(self):
        # The restored tab's terminal has the keyboard by the time the alert names what failed,
        # unless nothing was started and the dashboard still has it.
        for dashboard_is_active, expected_steps in ((False, ["alert", "keyboard back to the terminal"]), (True, ["alert"])):
            with self.subTest(dashboard_is_active=dashboard_is_active), tempfile.TemporaryDirectory() as folder:
                stand_in_app_controller = make_stand_in_app_controller_for_saved_sessions(
                    saved_windows=make_saved_windows(), settings=DEFAULT_SETTINGS, last_other_active_application="the terminal",
                    saved_agent_sessions_file_path=os.path.join(folder, "saved.json"))
                make_actions_finish_with(stand_in_app_controller, ActionResult(
                    False, '"notes": the folder /tmp/x doesn\'t exist', details={"restored_conversation_ids": ["a"]}))
                steps = []
                stand_in_app_controller.report_action_failure.side_effect = lambda action_result, failure_title: steps.append("alert")
                stand_in_app_controller.give_activation_to.side_effect = (
                    lambda application: steps.append(f"keyboard back to {application}"))
                with run_main_thread_calls_at_once(), unittest.mock.patch(
                        "agent_command_tab.app_controller.NSApp", unittest.mock.Mock(**{"isActive.return_value": dashboard_is_active})):
                    AppController.restore_saved_agent_sessions(stand_in_app_controller, set(), {"a", "c"})
                self.assertEqual(steps, expected_steps)


if __name__ == "__main__":
    unittest.main()
