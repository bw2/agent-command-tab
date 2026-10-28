"""The shared list of monitors, windows and agent session tabs (PLAN.md section 8, "Shared list").

Three instances exist (dropdown, sidebar and list window), each with its own NSOutlineView,
expanded rows, open previews and selection, fed the same snapshots. Main thread only.

How snapshots are applied without breaking expansion, renames or drags:
- one OutlineListItem per (kind, ID) lives as long as that ID exists, so expansion and
  selection, which NSOutlineView tracks by item identity, survive updates;
- when the rows' structure (which rows, in which order, how tall) is unchanged, the items'
  entries are replaced and only the visible rows are redrawn; otherwise the outline view is
  reloaded and the expansion and selection are restored from the controller's own records;
- while a rename or a drag is in progress, or the button is held down on a row or on a row's
  own button, snapshots are held and the newest is applied when that ends.
"""

import time

import objc
from AppKit import (NSAnimationContext, NSApp, NSAttributedString, NSBackgroundStyleNormal, NSBezierPath, NSColor, NSCursor,
                    NSDragOperationMove, NSDragOperationNone, NSEventModifierFlagControl, NSFont, NSFontAttributeName,
                    NSFontWeightBold, NSFontWeightMedium, NSFontWeightRegular, NSFontWeightSemibold, NSForegroundColorAttributeName,
                    NSGraphicsContext,
                    NSImageOnly, NSImageScaleNone,
                    NSImageView, NSInsetRect, NSLineBreakByTruncatingTail, NSLineBreakByWordWrapping,
                    NSMakeRect, NSMenu, NSMenuItem, NSMutableAttributedString, NSMutableParagraphStyle, NSOutlineView,
                    NSParagraphStyleAttributeName, NSPasteboardItem,
                    NSTableCellView, NSTableColumn, NSTableColumnAutoresizingMask, NSTableRowView,
                    NSTableViewLastColumnOnlyAutoresizingStyle, NSTableViewSelectionHighlightStyleRegular,
                    NSTableViewStylePlain, NSTextAlignmentCenter, NSTextAlignmentLeft, NSTextAlignmentRight, NSTextField,
                    NSTrackingActiveAlways, NSTrackingArea, NSTrackingCursorUpdate, NSTrackingInVisibleRect,
                    NSTrackingMouseEnteredAndExited, NSTrackingMouseMoved, NSView,
                    NSWindowStyleMaskNonactivatingPanel, NSZeroRect)
from Foundation import NSMutableIndexSet, NSObject, NSRunLoop, NSRunLoopCommonModes, NSTimer

from agent_command_tab.agent_adapters import (list_agent_tool_names_whose_sessions_can_talk_to_each_other,
                                              list_slash_commands_of_the_built_in_agent_tools)
from agent_command_tab.agent_adapters.base import COMMAND_THAT_STARTS_A_NEW_CONVERSATION
from agent_command_tab.dashboard_core_api import ActionRequest
from agent_command_tab.list_sorting import sort_agent_session_entries, sort_window_entries
from agent_command_tab.session_status_computation import (STATES_OF_AN_AGENT_WAITING_AT_ITS_PROMPT,
                                                          split_agent_sessions_into_pausable_and_resumable)
from agent_command_tab.settings_store import DEFAULT_SETTINGS
from agent_command_tab.snapshot_data_classes import iterate_agent_session_entries, replace_title_in_snapshot
from agent_command_tab.user_interface.agent_tool_icons import build_agent_tool_icons, build_text_image, describe_agent_tool_icon
from agent_command_tab.user_interface.control_helpers import ROWS_OPEN_SYMBOL_NAMES, show_symbol_on_button
from agent_command_tab.user_interface.named_colors import build_color_from_name
from agent_command_tab.user_interface.pointing_hand_button import PointingHandButton
from agent_command_tab.user_interface.row_text_rendering import (DOT_GLYPH_AND_COLOR_BY_STATE,
                                                                 LONGEST_TEXT_BY_COLUMN_FIELD_NAME, RowTextParts,
                                                                 build_agent_session_row_text_parts,
                                                                 build_child_process_row_text_parts,
                                                                 build_dot_text, build_monitor_row_text_parts,
                                                                 build_tree_connector_text,
                                                                 build_window_row_text_parts)
from agent_command_tab.user_interface.talk_to_each_other_alert import list_tab_titles_for_alert

ROW_HEIGHT = 20
PREVIEW_LINE_HEIGHT = 16  # the height of a 12-point line, so the tree's lines carry on beside the preview
PREVIEW_LINE_COUNT = 2
PREVIEW_AREA_HEIGHT = PREVIEW_LINE_HEIGHT * PREVIEW_LINE_COUNT
TAB_ROW_HEIGHT_WITH_PREVIEW = ROW_HEIGHT + PREVIEW_AREA_HEIGHT  # a tab row whose preview is open (R23)
FONT_SIZE = 12
PREVIEW_FONT_SIZE = 11
PREVIEW_BUTTON_SIZE = 16  # an expanded window row's button that opens or closes its tabs' previews
# Each row's arrow (closed ›, open ⌄) opens or closes just that row (user request, 2026-10-19);
# every row keeps the arrow's room, so names line up whether or not a row has one.
DISCLOSURE_ARROW_SLOT_WIDTH = 14
DISCLOSURE_ARROW_POINT_SIZE = 9
DISCLOSURE_ARROW_LOOKS = {False: ("chevron.right", ">", "Open"), True: ("chevron.down", "v", "Close")}  # by whether the row is open
# The button's (SF Symbol, text fallback, description), by whether all of the window's previews
# are open. The symbol shows the current state, as the top bar's expand or collapse button's
# does; the fallback text and the description say what a click does.
PREVIEW_BUTTON_LOOKS = {False: (ROWS_OPEN_SYMBOL_NAMES[False], "+", "Show the tabs' previews"),
                        True: (ROWS_OPEN_SYMBOL_NAMES[True], "−", "Hide the tabs' previews")}
ROW_LEFT_MARGIN = 8
GAP_AFTER_PREFIX = 2
NAME_FIELD_PADDING = 4
GAP_BEFORE_DETAILS = 8
# Between the columns at a tab row's right edge: 16 since 2026-10-28, 6 before, which left about 11
# points between a column's text and a next column its text fills (user request).
GAP_BETWEEN_COLUMNS = 16
ROW_RIGHT_MARGIN = 8
TEXT_FIELD_INSETS = 5  # a label's text sits a few points inside its field
# A collapsed window's state counts can grow ("9 idle" becomes "10 idle") without widening the dropdown.
ROOM_FOR_GROWING_DETAILS = "0000"
SECONDS_TO_HOLD_BEFORE_PEEKING = 0.15
SMALLEST_TAB_NAME_WIDTH = 80  # a narrower sidebar or window drops columns (COLUMN_NAMES_IN_DROP_ORDER)
# The spare room a sidebar's or list window's column keeps beyond its widest text, and how much
# narrower that text may get before the column shrinks (choose_room_for_a_columns_texts).
SPARE_CHARACTERS_IN_A_COLUMN = 1
CHARACTERS_A_COLUMN_MAY_LOSE = 2
# A tab row's columns at its right edge, left to right: settings_store.LIST_COLUMNS, which the
# settings can show or hide, and the agent tool's icon, which shows by itself when two or more
# agent tools run. Each comes with the RowTextParts field it shows (the icon column shows the
# tool's icon instead). The state is left-aligned in its column; the others are right-aligned.
# The icon, the state and its duration come first, right after the name, and CPU before memory
# (user requests, 2026-10-28).
TAB_ROW_COLUMNS = [
    ("agent_tool_icon", None),
    ("state", "state_text"),
    ("state_duration", "elapsed_time_text"),
    ("context_tokens", "context_text"),
    ("created_time", "created_time_text"),
    ("existence_duration", "existence_duration_text"),
    ("cpu_usage", "cpu_text"),
    ("memory_usage", "memory_text"),
]
LEFT_ALIGNED_COLUMN_NAMES = {"state"}
# The names shown over the columns, in a row between the top bar and the list (user request,
# 2026-10-26), with "Name" at the left. A name can be on two lines, "\n" between them, so its
# column widens only to the longer line; none is since they were shortened (user request,
# 2026-10-28). The icon column has none.
COLUMN_TITLES = {
    "context_tokens": "Tokens", "created_time": "Created", "existence_duration": "Age",  # "Time Since\nCreated" until 2026-10-28
    "memory_usage": "Memory", "cpu_usage": "CPU", "agent_tool_icon": "", "state": "Status",
    "state_duration": "Status Changed",  # "Time Since Last\nStatus Change" until 2026-10-28 (user request)
}
NAME_COLUMN_TITLE = "Name"
# The sort order (list_sorting.py) a click on a title chooses, by the title's column ("name" for
# Name; user requests, 2026-10-26). Tokens sorts by the share of the context window in use (user
# request, 2026-10-26); Created and Age both sort by when the agent started.
LIST_SORT_ORDER_BY_TITLE_NAME = {
    "name": "alphabet", "context_tokens": "context_percent", "created_time": "created_time", "existence_duration": "created_time", "memory_usage": "memory_usage",
    "cpu_usage": "cpu_usage", "state": "activity", "state_duration": "state_duration",
}
# The sorted column's title ends in an arrow, as in Finder: up while its values go from the
# smallest at the top (A, the shortest time) to the largest, down while they go from the largest
# (the fullest context window, the newest start, the most urgent state). These titles' orders start smallest first.
TITLE_NAMES_WHOSE_ORDER_STARTS_ASCENDING = {"name", "existence_duration", "state_duration"}
SORT_ARROWS_BY_IS_ASCENDING = {True: " ▴", False: " ▾"}  # a space before the arrow (user request, 2026-10-26)
COLUMN_TITLE_FONT_SIZE = 11
COLUMN_TITLE_LINE_HEIGHT = 14
COLUMN_TITLES_ROW_PADDING = 2  # above and below the titles
# A narrow sidebar or window drops columns in this order to keep room for names (the dot's color
# still shows the state). The time since the last status change goes last, since its title makes
# its column about 114 points wide, which would leave a 200-point sidebar's names unreadable.
COLUMN_NAMES_IN_DROP_ORDER = ["created_time", "existence_duration", "memory_usage", "cpu_usage", "context_tokens",
                              "agent_tool_icon", "state", "state_duration"]
SLASH_COMMANDS_IN_THE_TAB_MENU = list_slash_commands_of_the_built_in_agent_tools()
AGENT_TOOL_NAMES_WHOSE_SESSIONS_CAN_TALK_TO_EACH_OTHER = list_agent_tool_names_whose_sessions_can_talk_to_each_other()
SELECTABLE_ROW_KINDS = ("window", "tab")  # not monitor rows or the "+" row (user request, 2026-10-21)
SELECTION_OUTLINE_WIDTH = 1.5
SELECTION_OUTLINE_INSET = 1
SELECTION_OUTLINE_CORNER_RADIUS = 4
TAB_DRAG_TYPE = "org.agent-command-tab.tab-id"
WINDOW_DRAG_TYPE = "org.agent-command-tab.window-id"
COLUMN_IDENTIFIER = "main"
ROW_VIEW_IDENTIFIER = "agent-list-row"


class OutlineListItem(NSObject):
    """One row's item: kind is "monitor", "window", "tab", "process" for a sub row of an open tab
    row (one per process its agent started; user request, 2026-10-27), or "new_window" for the
    "+" row at the end of the list (it has no entry); entry is its snapshot record; key, (kind,
    item ID), finds it in the controller's records (expansion, open tab rows, items)."""

    def initWithKind_itemId_(self, kind, item_id):
        self = objc.super(OutlineListItem, self).init()
        if self is None:
            return None
        self.kind = kind
        self.item_id = item_id
        self.key = (kind, item_id)
        self.entry = None
        self.parent_item = None
        self.child_items = []
        return self


class OutlineViewWithoutDisclosureTriangles(NSOutlineView):
    """An NSOutlineView that draws no disclosure triangles of its own (each row has its own arrow
    button), and that tells its delegate when the button goes down on a row and comes back up,
    so holding a tab row can show that tab for a moment."""

    def frameOfOutlineCellAtRow_(self, row):
        return NSZeroRect

    def mouseDown_(self, event):
        is_plain_press = event.clickCount() == 1 and not event.modifierFlags() & NSEventModifierFlagControl
        if is_plain_press:
            self.delegate().row_pressed(self.rowAtPoint_(self.convertPoint_fromView_(event.locationInWindow(), None)))
        try:
            objc.super(OutlineViewWithoutDisclosureTriangles, self).mouseDown_(event)  # returns once the button is up
        finally:
            if is_plain_press:  # always, or the list would hold back every snapshot from then on
                self.delegate().row_released()


def compute_selection_outline_rect(row_bounds, is_flipped, joins_the_row_above, joins_the_row_below):
    """Returns the NSRect of the rounded rectangle a selected row strokes: its bounds
    inset by SELECTION_OUTLINE_INSET, run on past each edge it shares with a selected row above or
    below, so that the row's clip cuts off that edge and its corners and adjacent selected rows
    show one outline around them all (user request, 2026-10-21).

    Args:
        row_bounds: The row view's bounds.
        is_flipped: Whether the row view's y axis points down (the row above is then at the smaller y).
        joins_the_row_above: Whether the row above is selected too.
        joins_the_row_below: Whether the row below is selected too.
    """
    (x, y), (width, height) = NSInsetRect(row_bounds, SELECTION_OUTLINE_INSET, SELECTION_OUTLINE_INSET)
    run_on = SELECTION_OUTLINE_INSET + SELECTION_OUTLINE_CORNER_RADIUS + SELECTION_OUTLINE_WIDTH
    joins_at_smaller_y, joins_at_larger_y = ((joins_the_row_above, joins_the_row_below) if is_flipped
                                             else (joins_the_row_below, joins_the_row_above))
    if joins_at_smaller_y:
        y, height = y - run_on, height + run_on
    if joins_at_larger_y:
        height += run_on
    return NSMakeRect(x, y, width, height)


class RowViewWithSelectionOutline(NSTableRowView):
    """A row that shows it is selected with an outline in the accent color rather than a filled
    background, so its text keeps its colors; adjacent selected rows share one outline (user
    requests, 2026-10-21)."""

    def drawSelectionInRect_(self, dirty_rect):
        NSGraphicsContext.saveGraphicsState()
        NSBezierPath.clipRect_(self.bounds())
        NSColor.controlAccentColor().setStroke()
        outline = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            compute_selection_outline_rect(self.bounds(), self.isFlipped(), self.isPreviousRowSelected(),
                                           self.isNextRowSelected()),
            SELECTION_OUTLINE_CORNER_RADIUS, SELECTION_OUTLINE_CORNER_RADIUS)
        outline.setLineWidth_(SELECTION_OUTLINE_WIDTH)
        outline.stroke()
        NSGraphicsContext.restoreGraphicsState()

    def interiorBackgroundStyle(self):
        return NSBackgroundStyleNormal  # the row's labels keep the colors of an unselected row


def describe_session_count(count):
    """Returns "1 session" or "3 sessions", for menu item titles, which are in sentence case (user request, 2026-10-27)."""
    return f"{count} session{'' if count == 1 else 's'}"


def list_agent_sessions_that_take_slash_command(agent_session_entries, command):
    """Returns the agent sessions whose tool takes a slash command, whatever they are doing but
    paused (the menu of several selected rows; user request, 2026-10-21): one in the middle of a
    turn gets it when the turn ends (handle_send_slash_command_to_agent_sessions), while the core
    can't type into a suspended agent."""
    return [entry for entry in agent_session_entries if command in entry.slash_commands and entry.status != "paused"]


def tab_row_is_hidden(agent_session_entry, idle_tabs_are_hidden):
    """Whether "Hide Idle" hides the agent session's tab row (user request, 2026-10-21). It only
    hides rows: a window row still stands for its whole iTerm2 window, so the window's menu and
    the top bar's buttons act on its hidden agent sessions too."""
    return idle_tabs_are_hidden and agent_session_entry.status == "idle"


def window_row_is_hidden(window_entry, idle_tabs_are_hidden):
    """Whether "Hide Idle" hides a window row: every one of its agent sessions' rows is hidden."""
    return bool(window_entry.agent_sessions) and all(tab_row_is_hidden(entry, idle_tabs_are_hidden)
                                                     for entry in window_entry.agent_sessions)


def list_window_and_tab_item_keys(snapshot):
    """Returns the item keys of every window and agent session tab in the snapshot, also those
    whose rows Hide Idle hides, so a hidden window keeps its collapsed state and a hidden tab its
    open preview for when its row shows again."""
    return ({("window", window_entry.window_id) for monitor_entry in snapshot.monitors for window_entry in monitor_entry.windows}
            | {("tab", entry.tab_id) for _, _, entry in iterate_agent_session_entries(snapshot)})


def snapshot_shows_monitor_rows(snapshot):
    """Whether the list groups windows under monitor rows: only with two or more monitors."""
    return len(snapshot.monitors) > 1


def build_index_set(indexes):
    """Returns an NSIndexSet holding the given row indexes."""
    index_set = NSMutableIndexSet.alloc().init()
    for index in indexes:
        index_set.addIndex_(index)
    return index_set


def run_without_animation(change_rows):
    """Calls change_rows (expanding, collapsing or resizing rows) with the outline view's slide
    animation off, so the rows change at once; the animation setting is restored even if it raises."""
    NSAnimationContext.beginGrouping()
    NSAnimationContext.currentContext().setDuration_(0)
    try:
        change_rows()
    finally:
        NSAnimationContext.endGrouping()


def make_label_text_field():
    """Returns a borderless, non-editable, single-line text field."""
    text_field = NSTextField.alloc().initWithFrame_(NSMakeRect(0, 0, 10, ROW_HEIGHT))
    text_field.setBezeled_(False)
    text_field.setDrawsBackground_(False)
    text_field.setEditable_(False)
    text_field.setSelectable_(False)
    text_field.cell().setLineBreakMode_(NSLineBreakByTruncatingTail)
    text_field.cell().setUsesSingleLineMode_(True)
    return text_field


def make_multiline_label_text_field(line_count):
    """Returns a label like make_label_text_field's that wraps onto up to line_count lines, the
    last one ending in "…" when the text doesn't fit."""
    text_field = make_label_text_field()
    text_field.cell().setUsesSingleLineMode_(False)
    text_field.cell().setWraps_(True)
    text_field.cell().setLineBreakMode_(NSLineBreakByWordWrapping)
    text_field.cell().setTruncatesLastVisibleLine_(True)
    text_field.setMaximumNumberOfLines_(line_count)
    return text_field


class RowButton(PointingHandButton):
    """A button inside a row, with the pointing hand. While it is pressed, its target (the list
    controller) holds snapshots, as a press on the row itself does, so a snapshot can't reorder
    or reload the rows under it and leave its click without a row."""

    def mouseDown_(self, event):
        list_controller = self.target()
        # A double-click that starts here isn't the row's: its second click must not show the row
        # an earlier single click hit.
        list_controller.item_under_first_click = None
        list_controller.row_pressed(-1)  # held as a press on a row is, without peeking (no row)
        try:
            objc.super(RowButton, self).mouseDown_(event)  # returns once the button is up, after the action
        finally:
            list_controller.row_released()


def make_row_icon_button():
    """Returns a small borderless gray button for a row, without a tooltip, since the list's rows
    have no hover popups (user request, 2026-10-19); show_symbol_on_button gives it its icon."""
    button = RowButton.alloc().initWithFrame_(NSMakeRect(0, 0, PREVIEW_BUTTON_SIZE, PREVIEW_BUTTON_SIZE))
    button.setBordered_(False)
    button.setImagePosition_(NSImageOnly)
    button.setContentTintColor_(NSColor.secondaryLabelColor())
    return button


def choose_room_for_a_columns_texts(widest_text_width, room_before, character_width):
    """Returns the room a column keeps for its texts in the sidebar and the list window, whose width
    the user sets (user request, 2026-10-28): the widest text and SPARE_CHARACTERS_IN_A_COLUMN more,
    kept as it was while the widest text still fits and is at most CHARACTERS_A_COLUMN_MAY_LOSE
    narrower than when it was set, so a number gaining or losing a digit (9 to 10, and back)
    doesn't move the columns, while a wider text widens its column at once.

    Args:
        widest_text_width: The widest of the column's texts now, in points (0 for none).
        room_before: What this returned last time, or 0.
        character_width: One character's width in the rows' monospaced font.
    """
    if not widest_text_width:
        return 0
    rounding_slack = 0.5  # text widths are sums of character widths, give or take a fraction of a point
    if widest_text_width - rounding_slack <= room_before <= (
            widest_text_width + (SPARE_CHARACTERS_IN_A_COLUMN + CHARACTERS_A_COLUMN_MAY_LOSE) * character_width + rounding_slack):
        return room_before
    return widest_text_width + SPARE_CHARACTERS_IN_A_COLUMN * character_width


def place_columns_from_the_right(right_edge, column_widths):
    """Returns {column name: (x, width)} for the TAB_ROW_COLUMNS with a width (a hidden column's
    is 0), placed right to left from right_edge with GAP_BETWEEN_COLUMNS between them: where a tab
    row shows them, and the row of column titles their titles."""
    placed_columns = {}
    for column_name, _ in reversed(TAB_ROW_COLUMNS):
        if column_widths[column_name] > 0:
            right_edge -= column_widths[column_name]
            placed_columns[column_name] = (right_edge, column_widths[column_name])
            right_edge -= GAP_BETWEEN_COLUMNS
    return placed_columns


class ColumnTitlesRowView(NSView):
    """The row of column titles between the top bar and the list (user request, 2026-10-26):
    "Name" centered over the names (user request, 2026-10-26), and each shown column's title over it, aligned as its texts are, with its
    last line just above the first row. The list controller fills it and sets the columns' widths;
    the columns are placed as a tab row places them, from the outline view's right edge, which is
    left of this row's while the list shows a scroller that takes room. A click on a title, or
    anywhere above its column, reports the title's name ("name", or the column's) to
    on_title_clicked, which sorts the list. The pointer is the pointing hand where a click sorts
    (user request, 2026-10-26), set as PointingHandButton sets it: from a cursor rect in the
    dropdown and the list window, and from a tracking area in the sidebar, which turns cursor
    rects off."""

    def initWithOutlineView_(self, outline_view):
        self = objc.super(ColumnTitlesRowView, self).initWithFrame_(NSMakeRect(0, 0, 400, 0))
        if self is None:
            return None
        self.addTrackingArea_(NSTrackingArea.alloc().initWithRect_options_owner_userInfo_(
            NSZeroRect, NSTrackingMouseEnteredAndExited | NSTrackingMouseMoved | NSTrackingCursorUpdate
            | NSTrackingActiveAlways | NSTrackingInVisibleRect, self, None))
        self.outline_view = outline_view
        # A wrapping label too, of one line: a single-line field's text sits 2 points lower in the
        # same fixed line height, so "Name" would stand below the other titles (user request, 2026-10-27).
        self.name_title_text_field = make_multiline_label_text_field(1)
        self.column_title_text_fields = {column_name: make_multiline_label_text_field(2) for column_name, _ in TAB_ROW_COLUMNS}
        for subview in (self.name_title_text_field, *self.column_title_text_fields.values()):
            self.addSubview_(subview)
        self.column_widths = {column_name: 0 for column_name, _ in TAB_ROW_COLUMNS}  # set by the list controller
        self.room_for_the_previews_button = 0  # set by the list controller; the column at the right edge has no title
        self.on_title_clicked = lambda title_name: None  # set by the list controller
        self.title_name_under_press = None
        return self

    def acceptsFirstMouse_(self, event):
        return True  # the sidebar's titles sort from the first click, though the dashboard isn't the active app

    def hitTest_(self, point):
        # The row takes the clicks on its titles itself, rather than the labels.
        return self if objc.super(ColumnTitlesRowView, self).hitTest_(point) is not None else None

    def mouseDown_(self, event):
        self.title_name_under_press = self.find_title_name_at(self.convertPoint_fromView_(event.locationInWindow(), None))

    def mouseUp_(self, event):
        """Reports a click: the button went down and came up above the same title."""
        title_name = self.find_title_name_at(self.convertPoint_fromView_(event.locationInWindow(), None))
        if title_name is not None and title_name == self.title_name_under_press:
            self.on_title_clicked(title_name)
        self.title_name_under_press = None

    @objc.python_method
    def list_titles_that_sort(self):
        """Returns [(title name, its label's frame)] for the shown titles that sort."""
        return [(title_name, text_field.frame())
                for title_name, text_field in [("name", self.name_title_text_field), *self.column_title_text_fields.items()]
                if title_name in LIST_SORT_ORDER_BY_TITLE_NAME and not text_field.isHidden()]

    @objc.python_method
    def find_title_name_at(self, point):
        """Returns the name ("name", or the column's) of the shown title that sorts and whose
        column holds point's x, or None."""
        for title_name, frame in self.list_titles_that_sort():
            if frame.origin.x <= point.x < frame.origin.x + frame.size.width:
                return title_name
        return None

    def resetCursorRects(self):
        if not self.isHiddenOrHasHiddenAncestor():
            for _, frame in self.list_titles_that_sort():
                self.addCursorRect_cursor_(NSMakeRect(frame.origin.x, 0, frame.size.width, self.bounds().size.height),
                                           NSCursor.pointingHandCursor())

    @objc.python_method
    def show_the_pointer_at(self, point):
        """Shows the pointing hand above a title that sorts, and the arrow elsewhere in the row."""
        if not self.isHiddenOrHasHiddenAncestor() and self.find_title_name_at(point) is not None:
            NSCursor.pointingHandCursor().set()
        else:
            NSCursor.arrowCursor().set()

    def cursorUpdate_(self, event):
        self.show_the_pointer_at(self.convertPoint_fromView_(self.window().mouseLocationOutsideOfEventStream(), None))

    def mouseEntered_(self, event):
        self.show_the_pointer_at(self.convertPoint_fromView_(event.locationInWindow(), None))

    def mouseMoved_(self, event):
        self.show_the_pointer_at(self.convertPoint_fromView_(event.locationInWindow(), None))

    def mouseExited_(self, event):
        NSCursor.arrowCursor().set()

    def layout(self):
        objc.super(ColumnTitlesRowView, self).layout()
        placed_columns = place_columns_from_the_right(
            self.outline_view.frame().size.width - ROW_RIGHT_MARGIN - self.room_for_the_previews_button, self.column_widths)
        # "Name" is centered over the room the rows' names have, up to the columns.
        names_right_edge = min((column_x for column_x, _ in placed_columns.values()), default=ROW_LEFT_MARGIN) - GAP_BEFORE_DETAILS
        name_title_size = self.name_title_text_field.fittingSize()
        self.name_title_text_field.setFrame_(NSMakeRect(ROW_LEFT_MARGIN, COLUMN_TITLES_ROW_PADDING,
                                                        max(names_right_edge - ROW_LEFT_MARGIN, name_title_size.width),
                                                        name_title_size.height))
        for column_name, text_field in self.column_title_text_fields.items():
            text_field.setHidden_(column_name not in placed_columns)
            if column_name in placed_columns:
                column_x, column_width = placed_columns[column_name]
                text_field.setFrame_(NSMakeRect(column_x, COLUMN_TITLES_ROW_PADDING, column_width, text_field.fittingSize().height))
        if self.window() is not None:
            self.window().invalidateCursorRectsForView_(self)  # the hand's areas follow the titles


class AgentListRowView(NSTableCellView):
    """A row: [tree connector] [arrow] [dot] [name] [details] ... then, on a tab row and a
    process row, the TAB_ROW_COLUMNS at the right edge. The arrow opens or closes the row (a
    monitor's or window's rows below it, a tab's preview and process rows); rows without one keep its room. The list
    controller sets each column's width, 0 for a hidden column, so the columns line up from row
    to row. A tab row whose preview is open has two more lines, [tree lines] [preview], with the
    preview starting under the name. An expanded window row with previews ends in a button that
    opens or closes them all, in a column of its own at the right edge: the tab rows' columns
    end room_for_the_previews_button before it (user request, 2026-10-26)."""

    def initWithFrame_(self, frame):
        self = objc.super(AgentListRowView, self).initWithFrame_(frame)
        if self is None:
            return None
        self.prefix_text_field = make_label_text_field()  # the tree connector
        self.disclosure_button = make_row_icon_button()
        self.dot_text_field = make_label_text_field()
        self.name_text_field = make_label_text_field()
        self.details_text_field = make_label_text_field()
        icon_image_view = NSImageView.alloc().initWithFrame_(NSMakeRect(0, 0, 10, ROW_HEIGHT))
        icon_image_view.setImageScaling_(NSImageScaleNone)
        self.column_views = {column_name: make_label_text_field() if parts_field_name else icon_image_view
                             for column_name, parts_field_name in TAB_ROW_COLUMNS}
        self.column_widths = {column_name: 0 for column_name, _ in TAB_ROW_COLUMNS}  # set by the list controller
        self.room_for_the_previews_button = 0  # set by the list controller
        self.preview_connector_text_field = make_multiline_label_text_field(PREVIEW_LINE_COUNT)
        self.preview_text_field = make_multiline_label_text_field(PREVIEW_LINE_COUNT)
        self.preview_toggle_button = make_row_icon_button()
        self.new_window_button = make_row_icon_button()  # the "+" row's only content
        show_symbol_on_button(self.new_window_button, "plus", "+", "New window with an agent session")
        for subview in (self.prefix_text_field, self.disclosure_button, self.dot_text_field, self.name_text_field,
                        self.details_text_field, *self.column_views.values(), self.preview_connector_text_field,
                        self.preview_text_field, self.preview_toggle_button, self.new_window_button):
            self.addSubview_(subview)
        self.setTextField_(self.name_text_field)
        self.has_preview_line = False  # a tab row whose preview is open
        self.shows_columns = False  # tab and process rows
        self.name_runs_over_empty_columns = False  # process rows
        self.details_are_right_aligned = False  # window rows
        self.shows_disclosure_button = False  # a row that can open or close
        self.shows_preview_toggle_button = False  # an expanded window row with a preview to show
        self.tree_line_x = None  # set on the "+" row: its button is centered on the tree's vertical line
        self.look = None  # what the row shows, set by the list controller's fill_row_view
        return self

    def layout(self):
        objc.super(AgentListRowView, self).layout()
        bounds = self.bounds()
        preview_area_height = PREVIEW_AREA_HEIGHT if self.has_preview_line else 0  # at the bottom (y grows upward)
        main_line_height = bounds.size.height - preview_area_height
        text_height = self.name_text_field.fittingSize().height
        y = preview_area_height + (main_line_height - text_height) / 2
        prefix_width = self.prefix_text_field.fittingSize().width
        self.prefix_text_field.setFrame_(NSMakeRect(ROW_LEFT_MARGIN, y, prefix_width, text_height))
        arrow_x = ROW_LEFT_MARGIN + prefix_width
        self.disclosure_button.setHidden_(not self.shows_disclosure_button)
        self.disclosure_button.setFrame_(NSMakeRect(arrow_x, preview_area_height, DISCLOSURE_ARROW_SLOT_WIDTH, main_line_height))
        dot_x = arrow_x + DISCLOSURE_ARROW_SLOT_WIDTH
        dot_width = self.dot_text_field.fittingSize().width
        self.dot_text_field.setFrame_(NSMakeRect(dot_x, y, dot_width, text_height))
        name_x = dot_x + dot_width + GAP_AFTER_PREFIX
        text_right_limit = bounds.size.width - ROW_RIGHT_MARGIN  # where the name and details end; a window's details are right-aligned there
        self.new_window_button.setHidden_(self.tree_line_x is None)
        if self.tree_line_x is not None:
            self.new_window_button.setFrame_(NSMakeRect(self.tree_line_x - PREVIEW_BUTTON_SIZE / 2,
                                                        (bounds.size.height - PREVIEW_BUTTON_SIZE) / 2,
                                                        PREVIEW_BUTTON_SIZE, PREVIEW_BUTTON_SIZE))
        self.preview_toggle_button.setHidden_(not self.shows_preview_toggle_button)
        if self.shows_preview_toggle_button:  # at the right edge
            button_x = bounds.size.width - ROW_RIGHT_MARGIN - PREVIEW_BUTTON_SIZE
            self.preview_toggle_button.setFrame_(NSMakeRect(
                button_x, preview_area_height + (main_line_height - PREVIEW_BUTTON_SIZE) / 2, PREVIEW_BUTTON_SIZE, PREVIEW_BUTTON_SIZE))
            text_right_limit = button_x - GAP_BEFORE_DETAILS
        placed_columns = (place_columns_from_the_right(bounds.size.width - ROW_RIGHT_MARGIN - self.room_for_the_previews_button,
                                                       self.column_widths) if self.shows_columns else {})
        for column_name, parts_field_name in TAB_ROW_COLUMNS:
            column_view = self.column_views[column_name]
            column_view.setHidden_(column_name not in placed_columns)
            if column_name not in placed_columns:
                continue
            column_x, column_width = placed_columns[column_name]
            if parts_field_name is None:  # the icon, centered in the row's main line
                column_view.setFrame_(NSMakeRect(column_x, preview_area_height, column_width, main_line_height))
            else:
                column_view.setFrame_(NSMakeRect(column_x, y, column_width, text_height))
        # A process row's command runs on over the columns it leaves empty (Tokens, Status), up to its first one with text.
        columns_that_end_the_text = {column_name: placed_column for column_name, placed_column in placed_columns.items()
                                     if not self.name_runs_over_empty_columns or self.column_views[column_name].stringValue()}
        if columns_that_end_the_text:
            text_right_limit = min(column_x for column_x, _ in columns_that_end_the_text.values()) - GAP_BEFORE_DETAILS
        available_width = max(text_right_limit - name_x, 0)
        name_fitting_width = self.name_text_field.fittingSize().width + NAME_FIELD_PADDING
        details_fitting_width = self.details_text_field.fittingSize().width
        if self.name_text_field.isEditable():
            name_width = available_width * 0.6
        elif name_fitting_width + GAP_BEFORE_DETAILS + details_fitting_width <= available_width:
            name_width = name_fitting_width  # everything fits: never truncate the name
        else:
            name_width = min(name_fitting_width, max(available_width - GAP_BEFORE_DETAILS - details_fitting_width,
                                                     available_width * 0.6))
        self.name_text_field.setFrame_(NSMakeRect(name_x, y, name_width, text_height))
        details_x = name_x + name_width + GAP_BEFORE_DETAILS
        details_width = max(text_right_limit - details_x, 0)
        if not self.details_are_right_aligned:  # only as wide as its text, so the lights' hover text shows only over them
            details_width = min(details_fitting_width, details_width)
        self.details_text_field.setFrame_(NSMakeRect(details_x, y, details_width, text_height))
        self.preview_connector_text_field.setHidden_(not self.has_preview_line)
        self.preview_text_field.setHidden_(not self.has_preview_line)
        if self.has_preview_line:  # under the columns too, up to the right margin; a label's text starts at its top
            self.preview_connector_text_field.setFrame_(NSMakeRect(
                ROW_LEFT_MARGIN, 0, self.preview_connector_text_field.fittingSize().width, preview_area_height))
            self.preview_text_field.setFrame_(NSMakeRect(
                name_x, 0, max(bounds.size.width - ROW_RIGHT_MARGIN - name_x, 0), preview_area_height))


class AgentSessionListController(NSObject):
    """Data source, delegate and actions for one outline view.

    The owner (set with initWithOwner_) is a plain Python object with these methods:
        submit_action(action_request): forwards an ActionRequest to the core.
        tab_was_focused(list_controller): a tab row was clicked (the dropdown closes itself).
        rows_changed(list_controller): the shown rows changed outside a snapshot delivery, for
            example when the user expanded or collapsed a row (the container fits its size).
        list_agent_tool_names(): the agent tools that can be started, from the settings.
        start_agent_session_from_menu(window_id, folder, agent_tool_name): start an agent
            session in a new tab of that window, or, with window_id None, in a new window (the
            owner chooses the folder).
        confirm_closing_window(window_entry): asks the user whether to close that window;
            returns True for yes.
        ask_for_confirmation(explanation, action_name): asks "Are you sure?" before the action
            ("clear" or "kill"), unless a remembered Yes lets it run; returns True for yes.
        submit_action_and_report_failure(action_request, failure_title): like submit_action,
            and tells the user if the action failed.
        duplicate_agent_session(tab_id, folder, agent_tool_name): opens a copy of the tab's agent
            session right after it.
        find_launch_command(agent_tool_name): the tool's launch command from the settings, or "".
        start_peek(session_id), end_peek(): a tab row is held (shown for a moment), then released.
        list_sort_order_chosen(list_sort_order, list_sort_order_is_reversed): a column's title
            was clicked; the owner shows the order in every list (set_list_sort_order).
        ask_for_note_asking_agent_sessions_to_talk_to_each_other(agent_session_entries,
            left_out_agent_session_entries): shows the Talk to Each Other window; returns the
            note the user may have edited, or None for Cancel.
    """

    def initWithOwner_(self, owner):
        self = objc.super(AgentSessionListController, self).init()
        if self is None:
            return None
        self.owner = owner
        self.submit_action = owner.submit_action
        self.on_tab_focused = lambda: owner.tab_was_focused(self)
        self.on_row_count_changed = lambda: owner.rows_changed(self)
        # Called after every snapshot and expansion change; the panel sets it so its top bar's
        # buttons (expand or collapse all, pause or resume all) follow along.
        self.on_snapshot_or_expansion_changed = lambda: None
        # Called when the row of column titles needs another height; the panel sets it so the list moves under it.
        self.on_column_titles_row_height_changed = lambda: None
        self.items_by_key = {}
        self.top_level_items = []
        self.new_window_row_item = OutlineListItem.alloc().initWithKind_itemId_("new_window", "new_window")
        self.collapsed_item_keys = set()
        # Tab rows whose preview is open: one more level that opens and closes, closed at first (user request, 2026-10-19).
        self.open_preview_item_keys = set()
        self.row_structure = []
        self.snapshot = None
        self.held_snapshot = None
        self.rename_in_progress = False
        self.drag_in_progress = False
        self.row_press_in_progress = False  # the button is down on a row (row_pressed until row_released)
        self.menu_is_open = False
        self.blink_phase_is_dim = False
        self.peek_timer = None
        self.peek_in_progress = False
        self.item_under_first_click = None  # the row a single click hit, for a double-click's second click
        self.item_under_press = None  # the row the button last went down on: a drag moves only that row
        self.menu_target_item = None
        self.menu_target_agent_session_entries = []  # the agent sessions of the selected rows, for their menu
        self.applying_expansion_state = False
        self.item_whose_expansion_is_changing = None
        self.item_being_renamed = None
        self.title_before_rename = ""
        self.regular_font = NSFont.monospacedSystemFontOfSize_weight_(FONT_SIZE, NSFontWeightRegular)
        self.bold_font = NSFont.monospacedSystemFontOfSize_weight_(FONT_SIZE, NSFontWeightBold)
        # The status text, easier to read and still lighter than the front tab's bold name (user
        # request, 2026-10-28). Every weight of the font is as wide, so columns are measured in the regular one.
        self.semibold_font = NSFont.monospacedSystemFontOfSize_weight_(FONT_SIZE, NSFontWeightSemibold)
        self.preview_font = NSFont.monospacedSystemFontOfSize_weight_(PREVIEW_FONT_SIZE, NSFontWeightRegular)
        self.column_title_font = NSFont.monospacedSystemFontOfSize_weight_(COLUMN_TITLE_FONT_SIZE, NSFontWeightMedium)
        # The longer line of each column's title with a sort arrow after it: a shown column is at
        # least this wide, so sorting by another column doesn't change the columns' widths.
        self.column_title_widths = {column_name: max(self.measure_text_width(line, self.column_title_font)
                                                     for sort_arrow in SORT_ARROWS_BY_IS_ASCENDING.values()
                                                     for line in (COLUMN_TITLES[column_name] + sort_arrow).split("\n"))
                                    for column_name, _ in TAB_ROW_COLUMNS}
        self.right_aligned_paragraph_style = NSMutableParagraphStyle.alloc().init()
        self.right_aligned_paragraph_style.setAlignment_(NSTextAlignmentRight)
        self.right_aligned_paragraph_style.setLineBreakMode_(NSLineBreakByTruncatingTail)
        # Styled text overrides the field's own line breaking, so a name too long for its room (a
        # process row's command) ends in "…" rather than at the last space that fits, and the
        # preview's wrapping is set in its text too.
        self.truncating_paragraph_style = NSMutableParagraphStyle.alloc().init()
        self.truncating_paragraph_style.setLineBreakMode_(NSLineBreakByTruncatingTail)
        self.wrapping_paragraph_style = NSMutableParagraphStyle.alloc().init()
        self.wrapping_paragraph_style.setLineBreakMode_(NSLineBreakByWordWrapping)
        self.preview_connector_paragraph_style = NSMutableParagraphStyle.alloc().init()  # one tree line per preview line
        self.preview_connector_paragraph_style.setMinimumLineHeight_(PREVIEW_LINE_HEIGHT)
        self.preview_connector_paragraph_style.setMaximumLineHeight_(PREVIEW_LINE_HEIGHT)
        self.agent_tool_icons_by_name = build_agent_tool_icons()
        # Each TAB_ROW_COLUMNS column's width, 0 while it is hidden or no row has text for it (measure_right_columns).
        self.column_widths = {column_name: 0 for column_name, _ in TAB_ROW_COLUMNS}
        # In the sidebar and the list window, the room each column keeps for its texts (choose_room_for_a_columns_texts).
        self.room_for_texts_by_column_name = {}
        self.character_width = self.measure_text_width("0", self.regular_font)
        # The room the window rows' previews button takes at the right edge, in a column of its
        # own, while a tab has a preview (measure_right_columns; user request, 2026-10-26).
        self.room_for_the_previews_button = 0
        self.width_is_set_by_the_user = False  # True for the sidebar's and the list window's lists; the dropdown fits its width to the rows
        self.list_sort_order = "manual"  # set by the Sort pop-up or a column title's click (list_sorting.py)
        self.list_sort_order_is_reversed = False
        self.idle_tabs_are_hidden = False  # set by the Hide Idle / Show Idle button
        self.shown_columns = list(DEFAULT_SETTINGS["shown_columns"])  # set from the settings (settings_store.LIST_COLUMNS)
        self.outline_view = self.build_outline_view()
        self.column_titles_row_view = self.build_column_titles_row_view()
        self.fill_column_titles()
        self.refresh_timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            0.6, self, "refreshTimerFired:", None, True)
        return self

    # ----- building the view -----

    @objc.python_method
    def build_outline_view(self):
        outline_view = OutlineViewWithoutDisclosureTriangles.alloc().initWithFrame_(NSMakeRect(0, 0, 400, 300))
        column = NSTableColumn.alloc().initWithIdentifier_(COLUMN_IDENTIFIER)
        column.setResizingMask_(NSTableColumnAutoresizingMask)
        outline_view.addTableColumn_(column)
        outline_view.setOutlineTableColumn_(column)
        outline_view.setHeaderView_(None)
        outline_view.setIndentationPerLevel_(0)
        outline_view.setRowHeight_(ROW_HEIGHT)
        # The plain style has no inset margins or extra row spacing, so the rows take exactly
        # their measured size and the dropdown can be fitted to them.
        outline_view.setStyle_(NSTableViewStylePlain)
        outline_view.setIntercellSpacing_((0, 0))
        # A click selects a window or tab row, Command-click adds or removes one, Shift-click a
        # range (user request, 2026-10-21); RowViewWithSelectionOutline draws the selection.
        outline_view.setAllowsMultipleSelection_(True)
        outline_view.setSelectionHighlightStyle_(NSTableViewSelectionHighlightStyleRegular)
        # The one column follows the outline view's width; its container calls sizeLastColumnToFit once it is embedded.
        outline_view.setColumnAutoresizingStyle_(NSTableViewLastColumnOnlyAutoresizingStyle)
        outline_view.setDataSource_(self)
        outline_view.setDelegate_(self)
        outline_view.setTarget_(self)
        outline_view.setAction_("rowClicked:")
        outline_view.setDoubleAction_("rowDoubleClicked:")
        outline_view.registerForDraggedTypes_([TAB_DRAG_TYPE, WINDOW_DRAG_TYPE])
        outline_view.setDraggingSourceOperationMask_forLocal_(NSDragOperationMove, True)
        menu = NSMenu.alloc().initWithTitle_("")
        menu.setDelegate_(self)
        outline_view.setMenu_(menu)
        return outline_view

    @objc.python_method
    def build_column_titles_row_view(self):
        """Returns the row of column titles, whose clicks sort the list (column_title_clicked)."""
        row_view = ColumnTitlesRowView.alloc().initWithOutlineView_(self.outline_view)
        row_view.on_title_clicked = self.column_title_clicked
        return row_view

    @objc.python_method
    def fill_column_titles(self):
        """Puts the titles in the row of column titles, those of the columns the list is sorted by
        ending in an arrow (SORT_ARROWS_BY_IS_ASCENDING); update_column_titles_row sets the widths."""
        row_view = self.column_titles_row_view
        names_titles_alignments_and_text_fields = [("name", NAME_COLUMN_TITLE, NSTextAlignmentCenter, row_view.name_title_text_field)] + [
            (column_name, COLUMN_TITLES[column_name],
             NSTextAlignmentLeft if column_name in LEFT_ALIGNED_COLUMN_NAMES else NSTextAlignmentRight,
             row_view.column_title_text_fields[column_name]) for column_name, _ in TAB_ROW_COLUMNS]
        for title_name, title, alignment, text_field in names_titles_alignments_and_text_fields:
            if self.list_sort_order != "manual" and LIST_SORT_ORDER_BY_TITLE_NAME.get(title_name) == self.list_sort_order:
                title += SORT_ARROWS_BY_IS_ASCENDING[
                    (title_name in TITLE_NAMES_WHOSE_ORDER_STARTS_ASCENDING) != self.list_sort_order_is_reversed]
            paragraph_style = NSMutableParagraphStyle.alloc().init()
            paragraph_style.setAlignment_(alignment)
            paragraph_style.setMinimumLineHeight_(COLUMN_TITLE_LINE_HEIGHT)
            paragraph_style.setMaximumLineHeight_(COLUMN_TITLE_LINE_HEIGHT)
            text_field.setAttributedStringValue_(NSAttributedString.alloc().initWithString_attributes_(title, {
                NSFontAttributeName: self.column_title_font, NSForegroundColorAttributeName: NSColor.secondaryLabelColor(),
                NSParagraphStyleAttributeName: paragraph_style}))

    # ----- applying snapshots -----

    @objc.python_method
    def apply_snapshot(self, snapshot):
        """Shows a new snapshot (held while a rename or drag is in progress, or the button is down
        on a row: a sorted list could reorder its rows under the held button)."""
        if self.rename_in_progress or self.drag_in_progress or self.row_press_in_progress:
            self.held_snapshot = snapshot
            return
        self.snapshot = snapshot
        self.measure_right_columns()
        show_monitor_rows = snapshot_shows_monitor_rows(snapshot)
        new_top_level_items = []
        live_keys = set()
        for monitor_entry in snapshot.monitors:
            window_items = [self.update_window_item(window_entry, live_keys)
                            for window_entry in sort_window_entries(
                                monitor_entry.windows, self.list_sort_order,
                                list_ranked_agent_sessions=lambda window_entry: [
                                    entry for entry in window_entry.agent_sessions
                                    if not tab_row_is_hidden(entry, self.idle_tabs_are_hidden)],
                                is_reversed=self.list_sort_order_is_reversed)
                            if not window_row_is_hidden(window_entry, self.idle_tabs_are_hidden)]
            if show_monitor_rows:
                monitor_item = self.find_or_make_item("monitor", monitor_entry.monitor_id, monitor_entry, live_keys)
                self.set_child_items(monitor_item, window_items)
                new_top_level_items.append(monitor_item)
            else:
                for window_item in window_items:
                    window_item.parent_item = None
                new_top_level_items.extend(window_items)
        for key in set(self.items_by_key) - live_keys:
            removed_item = self.items_by_key.pop(key)
            removed_item.parent_item = None  # break the parent-child cycle so the item can be freed
            removed_item.child_items = []
        keys_with_saved_row_state = live_keys | list_window_and_tab_item_keys(snapshot)
        self.collapsed_item_keys &= keys_with_saved_row_state
        self.open_preview_item_keys &= keys_with_saved_row_state
        self.top_level_items = new_top_level_items
        new_row_structure = self.list_row_structure()
        if new_row_structure == self.row_structure:
            self.redraw_visible_rows()
        else:
            self.row_structure = new_row_structure
            self.reload_and_restore_expansion()
        self.on_snapshot_or_expansion_changed()  # a closed window may be gone

    @objc.python_method
    def update_window_item(self, window_entry, live_keys):
        window_item = self.find_or_make_item("window", window_entry.window_id, window_entry, live_keys)
        tab_items = [self.find_or_make_item("tab", agent_session_entry.tab_id, agent_session_entry, live_keys)
                     for agent_session_entry in sort_agent_session_entries(
                         window_entry.agent_sessions, self.list_sort_order, self.list_sort_order_is_reversed)
                     if not tab_row_is_hidden(agent_session_entry, self.idle_tabs_are_hidden)]
        self.set_child_items(window_item, tab_items)
        for tab_item in tab_items:
            # The process rows exist only while the tab row is open, so a closed tab's commands
            # starting and ending don't change the rows (which reloads them).
            self.set_child_items(tab_item, [
                self.find_or_make_item("process", f"{tab_item.item_id}:{child_process.process_id}", child_process, live_keys)
                for child_process in tab_item.entry.child_processes] if self.tab_row_is_open(tab_item) else [])
        return window_item

    @objc.python_method
    def set_list_sort_order(self, list_sort_order, is_reversed):
        """Shows the rows in this order from now on (one of list_sorting.LIST_SORT_ORDERS), in its
        other direction when is_reversed, with an arrow after the titles of the columns it sorts by."""
        if (list_sort_order, is_reversed) == (self.list_sort_order, self.list_sort_order_is_reversed):
            return
        self.list_sort_order = list_sort_order
        self.list_sort_order_is_reversed = is_reversed
        self.fill_column_titles()
        if self.held_snapshot is None and self.snapshot is not None:
            self.apply_snapshot(self.snapshot)  # (a held snapshot is sorted when it is applied)

    @objc.python_method
    def column_title_clicked(self, title_name):
        """Sorts by the clicked title's column, a click at a time: in its first direction, then
        the other, then back to the manual order (user requests, 2026-10-26)."""
        list_sort_order = LIST_SORT_ORDER_BY_TITLE_NAME[title_name]
        if list_sort_order != self.list_sort_order:
            self.owner.list_sort_order_chosen(list_sort_order, False)
        elif not self.list_sort_order_is_reversed:
            self.owner.list_sort_order_chosen(list_sort_order, True)
        else:
            self.owner.list_sort_order_chosen("manual", False)

    @objc.python_method
    def set_idle_tabs_are_hidden(self, idle_tabs_are_hidden):
        """Hides or shows the idle tabs' rows from now on (the Hide Idle / Show Idle button; user request, 2026-10-21)."""
        if idle_tabs_are_hidden == self.idle_tabs_are_hidden:
            return
        self.idle_tabs_are_hidden = idle_tabs_are_hidden
        if self.held_snapshot is None and self.snapshot is not None:
            self.apply_snapshot(self.snapshot)  # (a held snapshot is filtered when it is applied)
            self.on_row_count_changed()  # the dropdown fits its size to the rows again

    @objc.python_method
    def set_shown_columns(self, shown_columns):
        """Shows these information columns on tab rows from now on (names from settings_store.LIST_COLUMNS)."""
        if list(shown_columns) == self.shown_columns:
            return
        self.shown_columns = list(shown_columns)
        self.measure_right_columns()
        self.redraw_visible_rows()  # each row's look includes the column widths
        self.on_row_count_changed()  # the dropdown fits its width again

    @objc.python_method
    def find_or_make_item(self, kind, item_id, entry, live_keys):
        key = (kind, item_id)
        item = self.items_by_key.get(key)
        if item is None:
            item = OutlineListItem.alloc().initWithKind_itemId_(kind, item_id)
            self.items_by_key[key] = item
        item.entry = entry
        live_keys.add(key)
        return item

    @objc.python_method
    def set_child_items(self, parent_item, child_items):
        parent_item.child_items = child_items
        for child_item in child_items:
            child_item.parent_item = parent_item

    @objc.python_method
    def list_row_structure(self):
        """Returns the (kind, ID) keys of all items in display order, with each parent's child
        count and each row's height (measure_row_height)."""
        structure = []

        def visit(items):
            for item in items:
                structure.append((item.kind, item.item_id, len(item.child_items), self.measure_row_height(item)))
                visit(item.child_items)
        visit(self.top_level_items)
        return structure

    @objc.python_method
    def reload_and_restore_expansion(self):
        """Reloads the rows, then restores each item's expansion and the selection."""
        selected_items = self.list_selected_items()
        self.outline_view.reloadData()
        self.apply_expansion_state(self.top_level_items)
        surviving_rows = [self.outline_view.rowForItem_(item) for item in selected_items
                          if item.key in self.items_by_key]
        visible_surviving_rows = [row for row in surviving_rows if row >= 0]
        if visible_surviving_rows:
            self.outline_view.selectRowIndexes_byExtendingSelection_(build_index_set(visible_surviving_rows), False)

    @objc.python_method
    def apply_expansion_state(self, items):
        """Expands or collapses each item as recorded, parents before children.

        reloadData keeps the expansion of item objects it already knows, and NSOutlineView
        ignores expanding an item whose parent is collapsed, so both directions are set
        explicitly, top-down.
        """
        was_applying = self.applying_expansion_state
        self.applying_expansion_state = True
        try:
            for item in items:
                if not item.child_items:
                    continue
                if item.key in self.collapsed_item_keys:
                    self.outline_view.collapseItem_(item)
                else:
                    self.outline_view.expandItem_(item)
                    self.apply_expansion_state(item.child_items)
        finally:
            self.applying_expansion_state = was_applying

    # NSOutlineView reports every expansion change here, including its own Left and Right arrow
    # handling, so the controller's record always matches what is on screen. Collapsing a monitor
    # row also reports its windows collapsing; only the row that changed first (the one whose
    # "will" notification came first) is recorded, so its windows keep their own state.

    def outlineViewItemWillExpand_(self, notification):
        self.note_expansion_change_starting(notification.userInfo()["NSObject"])

    def outlineViewItemWillCollapse_(self, notification):
        self.note_expansion_change_starting(notification.userInfo()["NSObject"])

    def outlineViewItemDidExpand_(self, notification):
        self.record_expansion_change(notification.userInfo()["NSObject"], True)

    def outlineViewItemDidCollapse_(self, notification):
        self.record_expansion_change(notification.userInfo()["NSObject"], False)

    @objc.python_method
    def note_expansion_change_starting(self, item):
        if self.item_whose_expansion_is_changing is None:
            self.item_whose_expansion_is_changing = item

    @objc.python_method
    def record_expansion_change(self, item, is_expanded):
        if self.item_whose_expansion_is_changing is not None and item is not self.item_whose_expansion_is_changing:
            return  # a descendant reported along with the row that actually changed
        self.item_whose_expansion_is_changing = None
        if item.kind == "tab":
            # A tab row's process rows follow whether it is open (open_preview_item_keys); one the
            # outline view collapses itself (the Left arrow key) closes.
            if not is_expanded and not self.applying_expansion_state:
                self.set_previews_open([item], False)
            return
        key = item.key
        if is_expanded:
            self.collapsed_item_keys.discard(key)
        else:
            self.collapsed_item_keys.add(key)
        if self.applying_expansion_state:
            return
        if is_expanded:
            self.apply_expansion_state(item.child_items)  # children come back as they were
        self.redraw_visible_rows()
        self.on_row_count_changed()
        self.on_snapshot_or_expansion_changed()

    @objc.python_method
    def list_is_on_screen(self):
        """False for the closed dropdown and the hidden sidebar: their rows are redrawn when they appear."""
        window = self.outline_view.window()
        return window is not None and window.isVisible()

    @objc.python_method
    def redraw_visible_rows(self):
        """Refills the rows on screen; fill_row_view skips rows whose look didn't change."""
        if self.list_is_on_screen():
            self.fill_visible_rows()

    @objc.python_method
    def fill_visible_rows(self):
        visible_rows = self.outline_view.rowsInRect_(self.outline_view.visibleRect())
        for row in range(visible_rows.location, visible_rows.location + visible_rows.length):
            row_view = self.outline_view.viewAtColumn_row_makeIfNecessary_(0, row, False)
            item = self.outline_view.itemAtRow_(row)
            if row_view is not None and item is not None:
                self.fill_row_view(row_view, item)

    def refreshTimerFired_(self, timer):
        """Blinks working dots and keeps elapsed times current, redrawing visible rows only."""
        self.blink_phase_is_dim = not self.blink_phase_is_dim
        if self.snapshot is not None and not self.rename_in_progress and self.list_is_on_screen():
            self.measure_right_columns()  # elapsed times grow
            self.redraw_visible_rows()

    @objc.python_method
    def prepare_to_appear(self):
        """Brings the rows up to date just before the dropdown or the sidebar is shown."""
        self.measure_right_columns()
        self.fill_visible_rows()

    # ----- data source -----

    # The outline view's last row is the "+" row, which starts an agent session in a new window
    # (user request, 2026-10-19). It isn't one of top_level_items, so the list's ordering,
    # expansion and drag code never meet it.

    def outlineView_numberOfChildrenOfItem_(self, outline_view, item):
        return len(self.top_level_items) + 1 if item is None else len(item.child_items)

    def outlineView_child_ofItem_(self, outline_view, index, item):
        if item is None:
            return self.top_level_items[index] if index < len(self.top_level_items) else self.new_window_row_item
        return item.child_items[index]

    def outlineView_isItemExpandable_(self, outline_view, item):
        return bool(item.child_items)

    # ----- drawing rows -----

    def outlineView_viewForTableColumn_item_(self, outline_view, table_column, item):
        row_view = outline_view.makeViewWithIdentifier_owner_(ROW_VIEW_IDENTIFIER, self)
        if row_view is None:
            row_view = AgentListRowView.alloc().initWithFrame_(NSMakeRect(0, 0, 400, ROW_HEIGHT))
            row_view.setIdentifier_(ROW_VIEW_IDENTIFIER)
            row_view.name_text_field.setDelegate_(self)
            row_view.preview_toggle_button.setTarget_(self)
            row_view.preview_toggle_button.setAction_("previewToggleButtonClicked:")
            row_view.disclosure_button.setTarget_(self)
            row_view.disclosure_button.setAction_("disclosureButtonClicked:")
            row_view.new_window_button.setTarget_(self)
            row_view.new_window_button.setAction_("newWindowButtonClicked:")
        self.fill_row_view(row_view, item)
        return row_view

    def outlineView_heightOfRowByItem_(self, outline_view, item):
        return self.measure_row_height(item)

    def outlineView_rowViewForItem_(self, outline_view, item):
        return RowViewWithSelectionOutline.alloc().initWithFrame_(NSZeroRect)

    # ----- selection (user request, 2026-10-21) -----

    def outlineView_selectionIndexesForProposedSelection_(self, outline_view, proposed_selection_indexes):
        """Keeps only window and tab rows of a proposed selection (a Shift-click range can span a monitor row)."""
        return build_index_set(row for row in range(outline_view.numberOfRows())
                               if proposed_selection_indexes.containsIndex_(row)
                               and outline_view.itemAtRow_(row).kind in SELECTABLE_ROW_KINDS)

    @objc.python_method
    def list_selected_items(self):
        """Returns the items of the selected rows, top to bottom."""
        selected_rows = self.outline_view.selectedRowIndexes()
        return [self.outline_view.itemAtRow_(row) for row in range(self.outline_view.numberOfRows())
                if selected_rows.containsIndex_(row)]

    @objc.python_method
    def list_agent_session_entries_of_the_selected_rows(self):
        """Returns the agent sessions of the selected tab rows and of every tab of the selected
        window rows (also collapsed ones, and tabs whose rows Hide Idle hides), once each, in list order."""
        entry_by_session_id = {}
        for item in self.list_selected_items():
            agent_session_entries = (sort_agent_session_entries(item.entry.agent_sessions, self.list_sort_order,
                                                                self.list_sort_order_is_reversed)
                                     if item.kind == "window" else [item.entry])
            for entry in agent_session_entries:
                entry_by_session_id.setdefault(entry.session_id, entry)
        return list(entry_by_session_id.values())

    @objc.python_method
    def row_can_open(self, item):
        """Whether the row has an arrow: a monitor or window row (it has rows below it), or a tab
        row with a preview or processes its agent started."""
        if item.kind == "tab":
            return bool(item.entry.preview_line or item.entry.child_processes)
        return item.kind != "process" and bool(item.child_items)

    @objc.python_method
    def row_is_open(self, item):
        """Whether the row's arrow points down: an expanded monitor or window row, or an open tab row."""
        return self.tab_row_is_open(item) if item.kind == "tab" else item.key not in self.collapsed_item_keys

    @objc.python_method
    def tab_row_is_open(self, item):
        """Whether a tab row shows its preview and its process rows: it was opened, and has one of them to show."""
        return item.kind == "tab" and item.key in self.open_preview_item_keys and self.row_can_open(item)

    @objc.python_method
    def toggle_row(self, item):
        """Opens or closes just this row: a monitor's or window's rows below it, or a tab's preview and process rows."""
        if item.kind == "tab":
            self.set_previews_open([item], not self.tab_row_is_open(item))
        else:
            self.toggle_expansion(item)

    def disclosureButtonClicked_(self, sender):
        row = self.outline_view.rowForView_(sender)
        item = self.outline_view.itemAtRow_(row) if row >= 0 else None
        if item is not None and self.row_can_open(item):
            self.toggle_row(item)

    @objc.python_method
    def preview_is_open(self, item):
        """Whether a tab row shows its preview: it is open, and there is one to show."""
        return self.tab_row_is_open(item) and bool(item.entry.preview_line)

    @objc.python_method
    def measure_row_height(self, item):
        """A tab row whose preview is open is taller; list_row_structure notes each row's height,
        so a preview appearing or going away in a snapshot reloads the rows."""
        return TAB_ROW_HEIGHT_WITH_PREVIEW if self.preview_is_open(item) else ROW_HEIGHT

    @objc.python_method
    def measure_fully_expanded_list_height(self):
        """Returns the height of the rows the list has with every row expanded (list_all_items),
        with the previews that are open (counting every preview would leave the dropdown mostly
        empty while they are closed) and the "+" row."""
        return sum(self.measure_row_height(item) for item in self.list_all_items()) + ROW_HEIGHT

    @objc.python_method
    def build_is_last_child_at_each_level(self, item):
        levels = []
        while item.parent_item is not None:
            levels.append(item.parent_item.child_items[-1] is item)
            item = item.parent_item
        return list(reversed(levels))

    @objc.python_method
    def build_row_text_parts(self, item):
        is_last_child_at_each_level = self.build_is_last_child_at_each_level(item)
        if item.kind == "tab":
            return build_agent_session_row_text_parts(item.entry, is_last_child_at_each_level, time.time())
        if item.kind == "process":
            return build_child_process_row_text_parts(item.entry, is_last_child_at_each_level, time.time())
        if item.kind == "window":
            return build_window_row_text_parts(item.entry, is_last_child_at_each_level, self.row_is_open(item))
        if item.kind == "new_window":  # the "+" row draws only its button
            return RowTextParts("", "", "gray", False, "", "")
        return build_monitor_row_text_parts(item.entry)

    @objc.python_method
    def fill_row_view(self, row_view, item):
        if row_view.name_text_field.isEditable():
            return  # never overwrite text the user is typing
        parts = self.build_row_text_parts(item)
        is_in_front = item.kind == "tab" and item.entry.is_in_front
        tabs_that_open = self.list_tabs_that_open(item) if item.kind == "window" else []
        shows_preview_toggle_button = bool(tabs_that_open) and self.row_is_open(item)
        all_previews_are_open = all(self.tab_row_is_open(tab_item) for tab_item in tabs_that_open)
        shows_disclosure_button = self.row_can_open(item)
        # Everything the row's look depends on: an unchanged row is neither refilled nor laid out
        # again, which keeps the 0.6-second refresh cheap with many rows.
        look = (parts, item.kind, is_in_front, parts.dot_blinks and self.blink_phase_is_dim, self.preview_is_open(item),
                shows_disclosure_button, self.row_is_open(item), shows_preview_toggle_button, all_previews_are_open,
                tuple(self.column_widths.values()), self.room_for_the_previews_button, bool(item.child_items))
        if look == row_view.look:
            return
        row_view.look = look
        row_view.prefix_text_field.setAttributedStringValue_(NSAttributedString.alloc().initWithString_attributes_(
            parts.connector_text, {NSFontAttributeName: self.regular_font,
                                   NSForegroundColorAttributeName: NSColor.tertiaryLabelColor()}))
        row_view.shows_disclosure_button = shows_disclosure_button
        if shows_disclosure_button:
            show_symbol_on_button(row_view.disclosure_button, *DISCLOSURE_ARROW_LOOKS[self.row_is_open(item)],
                                  point_size=DISCLOSURE_ARROW_POINT_SIZE)
        dot_color = build_color_from_name(parts.dot_color_name)
        if parts.dot_blinks and self.blink_phase_is_dim:
            dot_color = dot_color.colorWithAlphaComponent_(0.25)
        row_view.dot_text_field.setAttributedStringValue_(NSAttributedString.alloc().initWithString_attributes_(
            build_dot_text(parts.dot_glyph), {NSFontAttributeName: self.regular_font, NSForegroundColorAttributeName: dot_color}))
        # Only the dot and the lights say what they mean; the row as a whole has no hover text (user requests, 2026-10-19 and 2026-10-27).
        row_view.dot_text_field.setToolTip_(parts.dot_tool_tip or None)
        row_view.details_text_field.setToolTip_(parts.details_tool_tip or None)
        # The tab in front is marked by its bold name only, in the same color as the others (blue
        # until 2026-10-26, user request). A process row's command is gray, as a preview is.
        row_view.name_text_field.setAttributedStringValue_(NSAttributedString.alloc().initWithString_attributes_(
            parts.name_text, {NSFontAttributeName: self.choose_name_font(item), NSForegroundColorAttributeName:
                              NSColor.secondaryLabelColor() if item.kind == "process" else NSColor.labelColor(),
                              NSParagraphStyleAttributeName: self.truncating_paragraph_style}))
        secondary_text_attributes = {NSFontAttributeName: self.regular_font,
                                     NSForegroundColorAttributeName: build_color_from_name(parts.details_color_name)}
        # Alignment goes in the text itself: styled text overrides a field's own alignment.
        right_aligned_secondary_text_attributes = dict(
            secondary_text_attributes, **{NSParagraphStyleAttributeName: self.right_aligned_paragraph_style})
        row_view.details_text_field.setAttributedStringValue_(NSAttributedString.alloc().initWithString_attributes_(
            parts.details_text,
            right_aligned_secondary_text_attributes if item.kind == "window" else secondary_text_attributes))
        row_view.details_are_right_aligned = item.kind == "window"
        # The columns' texts in the names' color rather than gray (user request, 2026-10-26).
        column_text_attributes = {NSFontAttributeName: self.regular_font, NSForegroundColorAttributeName: NSColor.labelColor()}
        right_aligned_column_text_attributes = dict(
            column_text_attributes, **{NSParagraphStyleAttributeName: self.right_aligned_paragraph_style})
        for column_name, parts_field_name in TAB_ROW_COLUMNS:
            column_view = row_view.column_views[column_name]
            if parts_field_name is None:
                column_view.setImage_(self.find_agent_tool_icon(parts.agent_tool_name) if parts.agent_tool_name else None)
                column_view.setToolTip_(describe_agent_tool_icon(parts.agent_tool_name) if parts.agent_tool_name else None)
            else:
                column_text = NSMutableAttributedString.alloc().initWithString_attributes_(
                    getattr(parts, parts_field_name), column_text_attributes if column_name in LEFT_ALIGNED_COLUMN_NAMES
                    else right_aligned_column_text_attributes)
                if parts_field_name == "context_text" and parts.context_fill_percent_color_name:
                    # Only the share in parentheses is colored, amber from 50% and red from 75% (user request, 2026-10-27).
                    share_start = parts.context_text.index("(")
                    column_text.addAttribute_value_range_(NSForegroundColorAttributeName,
                                                          build_color_from_name(parts.context_fill_percent_color_name),
                                                          (share_start, len(parts.context_text) - share_start))
                if parts_field_name == "state_text" and parts.state_color_name:  # user requests, 2026-10-28
                    column_text.addAttributes_range_({
                        NSForegroundColorAttributeName: build_color_from_name(parts.state_color_name),
                        NSFontAttributeName: self.semibold_font if parts.state_text_is_semibold else self.regular_font},
                        (0, len(parts.state_text)))
                column_view.setAttributedStringValue_(column_text)
        row_view.column_widths = dict(self.column_widths)
        row_view.room_for_the_previews_button = self.room_for_the_previews_button
        row_view.has_preview_line = self.preview_is_open(item)
        row_view.shows_columns = item.kind in ("tab", "process")
        row_view.name_runs_over_empty_columns = item.kind == "process"
        row_view.tree_line_x = self.measure_tree_line_x() if item.kind == "new_window" else None
        row_view.shows_preview_toggle_button = shows_preview_toggle_button
        if shows_preview_toggle_button:  # it shows whether all are open; a click then closes them all
            show_symbol_on_button(row_view.preview_toggle_button, *PREVIEW_BUTTON_LOOKS[all_previews_are_open])
        # Beside the preview, the line down to the tab's process rows under it starts too.
        preview_connector_text = parts.preview_connector_text + ("│" if item.child_items else "")
        row_view.preview_connector_text_field.setAttributedStringValue_(NSAttributedString.alloc().initWithString_attributes_(
            "\n".join([preview_connector_text] * PREVIEW_LINE_COUNT),
            {NSFontAttributeName: self.regular_font, NSForegroundColorAttributeName: NSColor.tertiaryLabelColor(),
             NSParagraphStyleAttributeName: self.preview_connector_paragraph_style}))
        row_view.preview_text_field.setAttributedStringValue_(NSAttributedString.alloc().initWithString_attributes_(
            parts.preview_text, {NSFontAttributeName: self.preview_font, NSForegroundColorAttributeName: NSColor.secondaryLabelColor(),
                                 NSParagraphStyleAttributeName: self.wrapping_paragraph_style}))
        row_view.setNeedsLayout_(True)

    @objc.python_method
    def find_agent_tool_icon(self, agent_tool_name):
        """Returns the tool's drawn icon, or its name as a small image for a tool without one."""
        if agent_tool_name not in self.agent_tool_icons_by_name:
            self.agent_tool_icons_by_name[agent_tool_name] = build_text_image(agent_tool_name, self.regular_font)
        return self.agent_tool_icons_by_name[agent_tool_name]

    @objc.python_method
    def measure_tree_line_x(self):
        """Returns the x of the tree's vertical line, the middle of a top-level row's child's
        connector's first character, "├" (a label's text starts half its insets inside the field)."""
        return ROW_LEFT_MARGIN + TEXT_FIELD_INSETS / 2 + self.measure_text_width(
            build_tree_connector_text([False])[0], self.regular_font) / 2

    @objc.python_method
    def measure_text_width(self, text, font):
        return NSAttributedString.alloc().initWithString_attributes_(text, {NSFontAttributeName: font}).size().width

    @objc.python_method
    def measure_name_x(self, connector_text, dot_glyph):
        """Returns where a row's name starts, as AgentListRowView.layout places it: after the
        left margin, the tree connector's field, the arrow's room and the dot's field (each field
        its text and TEXT_FIELD_INSETS, which is up to a point more than AppKit adds, so this
        never falls short)."""
        return (ROW_LEFT_MARGIN + self.measure_text_width(connector_text, self.regular_font) + TEXT_FIELD_INSETS
                + DISCLOSURE_ARROW_SLOT_WIDTH + self.measure_text_width(build_dot_text(dot_glyph), self.regular_font)
                + TEXT_FIELD_INSETS + GAP_AFTER_PREFIX)

    @objc.python_method
    def measure_right_columns(self):
        """Sets each TAB_ROW_COLUMNS column's width to fit its widest text and its title, or 0 when
        the settings hide it or no row has text for it (no context size known yet). In the dropdown, a shown
        column is at least as wide as LONGEST_TEXT_BY_COLUMN_FIELD_NAME's text, so the dropdown's
        width doesn't follow the numbers and states; the sidebar and the list window, whose width
        the user sets, leave that room to the names, but keep a little spare room in each column
        and shrink one only when its texts get clearly narrower (choose_room_for_a_columns_texts),
        so the columns don't move as numbers change by a digit. The icon column's width fits the widest tool
        icon, or is 0 when all agent sessions use the same tool. A narrow sidebar or window then
        drops columns in COLUMN_NAMES_IN_DROP_ORDER to keep room for names."""
        agent_session_entries = [entry for _, _, entry in iterate_agent_session_entries(self.snapshot)
                                 if not tab_row_is_hidden(entry, self.idle_tabs_are_hidden)] if self.snapshot else []
        now = time.time()
        # Process rows too, also those of closed tab rows, so opening one doesn't widen a column.
        all_parts = [build_agent_session_row_text_parts(entry, [], now) for entry in agent_session_entries] + [
            build_child_process_row_text_parts(child_process, [], now)
            for entry in agent_session_entries for child_process in entry.child_processes]
        self.room_for_the_previews_button = (PREVIEW_BUTTON_SIZE + GAP_BETWEEN_COLUMNS
                                             if any(entry.preview_line or entry.child_processes for entry in agent_session_entries)
                                             else 0)
        for column_name, parts_field_name in TAB_ROW_COLUMNS:
            if parts_field_name is None:
                agent_tool_names = {entry.agent_tool_name for entry in agent_session_entries}
                self.column_widths[column_name] = (max(self.find_agent_tool_icon(name).size().width for name in agent_tool_names)
                                                   if len(agent_tool_names) >= 2 else 0)
                continue
            # Each distinct text is measured once; many sessions share "idle" or "2m".
            texts = {getattr(parts, parts_field_name) for parts in all_parts} if column_name in self.shown_columns else set()
            if any(texts) and not self.width_is_set_by_the_user:  # the dropdown keeps its width as the texts change
                texts.add(LONGEST_TEXT_BY_COLUMN_FIELD_NAME[parts_field_name])
            widest_text_width = max((self.measure_text_width(text, self.regular_font) for text in texts if text), default=0)
            if self.width_is_set_by_the_user:  # spare room, so small changes to the numbers don't move the columns
                widest_text_width = choose_room_for_a_columns_texts(
                    widest_text_width, self.room_for_texts_by_column_name.get(column_name, 0), self.character_width)
                self.room_for_texts_by_column_name[column_name] = widest_text_width
            self.column_widths[column_name] = (TEXT_FIELD_INSETS + max(widest_text_width, self.column_title_widths[column_name])
                                               if widest_text_width else 0)
        if self.width_is_set_by_the_user:
            for column_name in COLUMN_NAMES_IN_DROP_ORDER:
                if self.measure_room_left_for_tab_names() < SMALLEST_TAB_NAME_WIDTH:
                    self.column_widths[column_name] = 0
        self.update_column_titles_row()

    @objc.python_method
    def update_column_titles_row(self):
        """Gives the row of column titles the columns' widths, and has the panel fit the list under
        it when it needs another height."""
        self.column_titles_row_view.column_widths = dict(self.column_widths)
        self.column_titles_row_view.room_for_the_previews_button = self.room_for_the_previews_button
        self.column_titles_row_view.setNeedsLayout_(True)
        if self.measure_column_titles_row_height() != self.column_titles_row_view.frame().size.height:
            self.on_column_titles_row_height_changed()

    @objc.python_method
    def measure_column_titles_row_height(self):
        """Returns the height of the row of column titles: room for the shown columns' titles, or 0
        while none shows (no tab rows, or every titled column turned off or dropped)."""
        line_counts = [COLUMN_TITLES[column_name].count("\n") + 1 for column_name, width in self.column_widths.items()
                       if width and COLUMN_TITLES[column_name]]
        return max(line_counts) * COLUMN_TITLE_LINE_HEIGHT + 2 * COLUMN_TITLES_ROW_PADDING if line_counts else 0

    @objc.python_method
    def measure_room_left_for_tab_names(self):
        """Returns the width left for a tab row's name and details beside the right-hand columns."""
        # A tab under a window, under a monitor row while there are several monitors.
        tab_levels_below_the_top = 2 if self.snapshot is not None and snapshot_shows_monitor_rows(self.snapshot) else 1
        deepest_tab_connector = build_tree_connector_text([False] * tab_levels_below_the_top)
        return (self.outline_view.bounds().size.width - self.measure_name_x(deepest_tab_connector, "●") - GAP_BEFORE_DETAILS
                - self.measure_right_columns_width() - ROW_RIGHT_MARGIN)

    @objc.python_method
    def measure_right_columns_width(self):
        """Returns the width of a tab row's right-hand columns with the gaps between them (0-width
        columns are hidden), and the previews button's column after them."""
        shown_column_widths = [width for width in self.column_widths.values() if width]
        return (sum(shown_column_widths) + GAP_BETWEEN_COLUMNS * max(len(shown_column_widths) - 1, 0)
                + self.room_for_the_previews_button)

    @objc.python_method
    def choose_name_font(self, item):
        """Window and monitor names, and the tab in front, are bold."""
        is_in_front = item.kind == "tab" and item.entry.is_in_front
        return self.bold_font if (is_in_front or item.kind in ("monitor", "window")) else self.regular_font

    @objc.python_method
    def list_all_items(self):
        """Returns every item, also those inside collapsed rows: the rows the list has when fully expanded."""
        all_items = []

        def visit(items):
            for item in items:
                all_items.append(item)
                visit(item.child_items)
        visit(self.top_level_items)
        return all_items

    @objc.python_method
    def measure_widest_row_width(self):
        """Returns the width, in points, that the widest row needs to show all its text, counting
        the rows inside collapsed windows and a window's counts as if it were collapsed, so the
        dropdown keeps one size while rows are expanded and collapsed (user request, milestone 2)."""
        measure_text_width = self.measure_text_width
        # The state dots' glyphs come from fonts other than the monospaced one and differ by a
        # fraction of a point, so each row is measured with the widest, and a state change doesn't
        # change the dropdown's width.
        widest_dot_glyph = max((dot_glyph for dot_glyph, _ in DOT_GLYPH_AND_COLOR_BY_STATE.values()),
                               key=lambda dot_glyph: measure_text_width(dot_glyph, self.regular_font))
        widest_row_width = 0
        preview_buttons_width = GAP_BEFORE_DETAILS + PREVIEW_BUTTON_SIZE
        for item in self.list_all_items():
            if item.kind == "process":
                continue  # a command, often long, is cut short to the room the other rows leave it
            if item.kind == "window":  # expanded, it ends in the preview button
                last_child_flags = self.build_is_last_child_at_each_level(item)
                parts_and_button_widths = [(build_window_row_text_parts(item.entry, last_child_flags, is_expanded),
                                            preview_buttons_width if is_expanded else 0) for is_expanded in (True, False)]
            else:
                parts_and_button_widths = [(self.build_row_text_parts(item), 0)]
            for parts, button_width in parts_and_button_widths:
                row_width = (self.measure_name_x(parts.connector_text, widest_dot_glyph if parts.dot_glyph else "")
                             + measure_text_width(parts.name_text, self.choose_name_font(item)) + NAME_FIELD_PADDING
                             + GAP_BEFORE_DETAILS + measure_text_width(parts.details_text + ROOM_FOR_GROWING_DETAILS, self.regular_font)
                             + 2 * TEXT_FIELD_INSETS + ROW_RIGHT_MARGIN + button_width)
                if parts.state_text:  # the columns at the right edge
                    row_width += GAP_BEFORE_DETAILS + self.measure_right_columns_width()
                widest_row_width = max(widest_row_width, row_width)
        return widest_row_width

    # ----- clicks -----

    @objc.python_method
    def find_clicked_item(self):
        clicked_row = self.outline_view.clickedRow()
        return self.outline_view.itemAtRow_(clicked_row) if clicked_row >= 0 else None

    def rowClicked_(self, sender):
        current_event = NSApp.currentEvent()
        if current_event is not None and current_event.clickCount() > 1:
            return  # the second click of a double-click: rowDoubleClicked_ handles it
        # A single click only selects the row, which the outline view does itself (user request,
        # 2026-10-21): a row opens and closes with its arrow (user request, 2026-10-19), and a
        # double-click shows the window or tab (user request, milestone 2). The row is remembered
        # for a double-click, should the rows move before its second click.
        self.item_under_first_click = self.find_clicked_item()

    # Pressing and holding a tab row shows that tab until the button comes up (user request,
    # milestone 2). The short wait tells a hold from a click.

    @objc.python_method
    def row_pressed(self, row):
        self.cancel_peek_timer()
        self.row_press_in_progress = True
        item = self.outline_view.itemAtRow_(row) if row >= 0 else None
        self.item_under_press = item
        if item is None or item.kind != "tab":
            return
        self.peek_timer = NSTimer.timerWithTimeInterval_target_selector_userInfo_repeats_(
            SECONDS_TO_HOLD_BEFORE_PEEKING, self, "peekTimerFired:", item, False)
        # Common modes, so it fires while the outline view tracks the held button.
        NSRunLoop.currentRunLoop().addTimer_forMode_(self.peek_timer, NSRunLoopCommonModes)

    def peekTimerFired_(self, timer):
        self.peek_timer = None
        item = timer.userInfo()
        if self.drag_in_progress or item.entry is None:
            return
        self.peek_in_progress = True
        self.owner.start_peek(item.entry.session_id)

    @objc.python_method
    def row_released(self):
        self.cancel_peek_timer()
        if self.peek_in_progress:
            self.peek_in_progress = False
            self.owner.end_peek()
        self.row_press_in_progress = False
        self.apply_held_snapshot()

    @objc.python_method
    def cancel_peek_timer(self):
        if self.peek_timer is not None:
            self.peek_timer.invalidate()
            self.peek_timer = None

    def rowDoubleClicked_(self, sender):
        # The row under the first click, not the one under the pointer now: a sorted list can
        # reorder its rows (a held snapshot is applied when the first click's button comes up),
        # which moves another row under the second click.
        first_click_item, self.item_under_first_click = self.item_under_first_click, None
        item = first_click_item or self.find_clicked_item()
        if item is not None and item.kind == "process":
            item = item.parent_item  # its tab
        if item is not None and item.kind in ("window", "tab"):
            self.show_item(item)

    @objc.python_method
    def toggle_expansion(self, item):
        """Expands or collapses a row at once, without the outline view's slide animation;
        record_expansion_change keeps the record and redraws."""
        self.finish_rename()
        if self.items_by_key.get(item.key) is not item:
            return  # finishing the rename applied a snapshot without this row
        run_without_animation(lambda: self.outline_view.collapseItem_(item) if self.outline_view.isItemExpanded_(item)
                              else self.outline_view.expandItem_(item))
        self.item_whose_expansion_is_changing = None  # in case a change was announced but not made

    @objc.python_method
    def set_all_expanded(self, expanded):
        """Expands or collapses every monitor and window row (R8.1), leaving the tabs' previews
        as they are (user request, 2026-10-19). Collapsing keeps the windows collapsed before,
        also those whose rows Hide Idle hides."""
        self.finish_rename()
        if expanded:
            self.collapsed_item_keys.clear()
        else:
            self.collapsed_item_keys |= {key for key, item in self.items_by_key.items()
                                         if item.child_items and item.kind in ("monitor", "window")}
        # Row by row rather than through reloadData, which would rebuild every row view.
        run_without_animation(lambda: self.apply_expansion_state(self.top_level_items))
        self.redraw_visible_rows()
        self.on_row_count_changed()
        self.on_snapshot_or_expansion_changed()

    @objc.python_method
    def is_fully_expanded(self):
        """Whether every monitor and window row with rows below it is expanded (the previews don't
        count). A collapsed monitor whose windows all moved away keeps its choice for later but
        hides nothing now."""
        return not any(self.items_by_key[key].child_items for key in self.collapsed_item_keys if key in self.items_by_key)

    @objc.python_method
    def count_window_rows(self):
        """Returns how many window rows the list has, also inside collapsed monitor rows."""
        return len(self.list_window_items())

    @objc.python_method
    def set_previews_open(self, tab_items, is_open):
        """Opens or closes these tab rows at once: their previews and process rows."""
        self.finish_rename()
        for tab_item in tab_items:
            if is_open:
                self.open_preview_item_keys.add(tab_item.key)
            else:
                self.open_preview_item_keys.discard(tab_item.key)
        if any(tab_item.entry.child_processes for tab_item in tab_items):
            # Their process rows come or go: the rows are built again (when the click's button
            # comes up, should a snapshot be held while it is down).
            self.apply_snapshot(self.held_snapshot or self.snapshot)
        else:
            self.apply_row_heights()
            self.redraw_visible_rows()
        self.on_row_count_changed()  # the dropdown fits its height again

    @objc.python_method
    def apply_row_heights(self):
        """Has the outline view take each row's current height (measure_row_height) at once,
        without its animation, and records the rows as shown so the next snapshot doesn't reload them."""
        self.row_structure = self.list_row_structure()
        run_without_animation(lambda: self.outline_view.noteHeightOfRowsWithIndexesChanged_(
            build_index_set(range(self.outline_view.numberOfRows()))))

    def previewToggleButtonClicked_(self, sender):
        """Closes the tab rows (previews and process rows) in the window whose row holds this
        button when all are open, and otherwise opens them all."""
        row = self.outline_view.rowForView_(sender)
        window_item = self.outline_view.itemAtRow_(row) if row >= 0 else None
        if window_item is not None and window_item.kind == "window":
            tabs_that_open = self.list_tabs_that_open(window_item)
            self.set_previews_open(tabs_that_open, not all(self.tab_row_is_open(tab_item) for tab_item in tabs_that_open))

    @objc.python_method
    def list_tabs_that_open(self, window_item):
        """Returns the window's tab items that have a preview or process rows to show."""
        return [tab_item for tab_item in window_item.child_items if self.row_can_open(tab_item)]

    # ----- renaming in place -----

    @objc.python_method
    def finish_rename(self):
        """Submits an open rename for its own row, as a click elsewhere in the list does. Called
        before rows open or close: AppKit raises an exception when a row whose name is being
        edited is collapsed away, and the list would go on holding back snapshots."""
        if self.rename_in_progress:
            self.outline_view.window().makeFirstResponder_(self.outline_view)

    @objc.python_method
    def begin_rename(self, item):
        self.finish_rename()  # then this row is found again, since finishing can apply a held snapshot
        if item.key not in self.items_by_key:
            return
        item = self.items_by_key[item.key]
        row = self.outline_view.rowForItem_(item)
        row_view = self.outline_view.viewAtColumn_row_makeIfNecessary_(0, row, True) if row >= 0 else None
        if row_view is None:
            return
        self.rename_in_progress = True
        self.item_being_renamed = item
        self.title_before_rename = item.entry.tab_title if item.kind == "tab" else item.entry.title
        name_text_field = row_view.name_text_field
        row_view.look = None  # the field is edited directly, so the row is refilled afterwards
        name_text_field.setStringValue_(self.title_before_rename)
        name_text_field.setEditable_(True)
        name_text_field.setSelectable_(True)
        name_text_field.setDrawsBackground_(True)
        name_text_field.setBezeled_(True)
        row_view.setNeedsLayout_(True)
        window = self.outline_view.window()
        if not window.styleMask() & NSWindowStyleMaskNonactivatingPanel:
            # The dropdown takes typing only while the dashboard is the active app, which it may
            # no longer be (after a peek); otherwise the new name would go to an agent's prompt.
            NSApp.activateIgnoringOtherApps_(True)
        window.makeKeyWindow()
        window.makeFirstResponder_(name_text_field)
        name_text_field.currentEditor().selectAll_(None)

    @objc.python_method
    def cancel_rename(self):
        """Ends an open rename without renaming, as Esc does (the dropdown calls it as it closes,
        and the sidebar when it loses keyboard focus, or the list would keep holding back snapshots)."""
        if self.rename_in_progress:
            self.item_being_renamed = None
            self.outline_view.window().makeFirstResponder_(self.outline_view)

    def control_textView_doCommandBySelector_(self, control, text_view, command_selector):
        # Esc cancels the rename. AppKit's calls arrive with the selector as str; direct Python calls may pass bytes.
        if command_selector in ("cancelOperation:", b"cancelOperation:"):
            self.item_being_renamed = None
            self.outline_view.window().makeFirstResponder_(self.outline_view)
            return True
        return False

    def controlTextDidEndEditing_(self, notification):
        name_text_field = notification.object()
        item = self.item_being_renamed
        new_title = name_text_field.stringValue()
        if new_title.strip() == self.title_before_rename.strip():
            item = None  # unchanged: don't turn a default name into a fixed title
        name_text_field.setEditable_(False)
        name_text_field.setSelectable_(False)
        name_text_field.setDrawsBackground_(False)
        name_text_field.setBezeled_(False)
        self.rename_in_progress = False
        self.item_being_renamed = None
        if item is not None and item.entry is not None:
            if item.kind == "tab":
                self.submit_action(ActionRequest("rename_tab", {"tab_id": item.item_id}, {"new_title": new_title}))
            else:
                self.submit_action(ActionRequest("rename_window", {"window_id": item.item_id}, {"new_title": new_title}))
        name_text_field.superview().look = None
        self.apply_held_snapshot()  # first, so a snapshot held during the rename doesn't bring the old name back
        if item is not None and self.held_snapshot is not None and new_title.strip():
            # Held again (the rename ended with a click on another row, whose button is still
            # down): it predates the rename, so it gets the new name, or it would bring the old
            # one back on release. It is kept rather than dropped, since the core sends another
            # only when something changes.
            self.held_snapshot = replace_title_in_snapshot(self.held_snapshot, item.kind, item.item_id, new_title.strip())
        renamed_item = self.items_by_key.get(item.key) if item is not None else None
        if renamed_item is not None and renamed_item.entry is not None and new_title.strip():
            # Shown at once; the next snapshot confirms it (or puts the old name back if iTerm2 refused).
            renamed_item.entry = renamed_item.entry._replace(
                **{"tab_title" if renamed_item.kind == "tab" else "title": new_title.strip()})
        self.redraw_visible_rows()

    @objc.python_method
    def apply_held_snapshot(self):
        held_snapshot, self.held_snapshot = self.held_snapshot, None
        if held_snapshot is not None:
            self.apply_snapshot(held_snapshot)
            self.on_row_count_changed()  # the dropdown didn't refit while the snapshot was held
        elif self.snapshot is not None:
            self.redraw_visible_rows()

    # ----- right-click menu -----

    def menuWillOpen_(self, menu):
        self.menu_is_open = True  # keeps the sidebar from sliding away under the menu

    def menuDidClose_(self, menu):
        self.menu_is_open = False

    def menuNeedsUpdate_(self, menu):
        menu.removeAllItems()
        menu.setAutoenablesItems_(False)
        item = self.find_clicked_item()
        if item is not None and item.kind == "process":
            item = item.parent_item  # a process row has its tab's menu
        self.menu_target_item = item
        selected_items = self.list_selected_items()
        if item is not None and len(selected_items) >= 2 and any(selected_item is item for selected_item in selected_items):
            # A right-click on one of several selected rows; a single row keeps its own menu, selected
            # or not. An "exited" row has no agent left to act on.
            self.menu_target_agent_session_entries = [entry for entry in self.list_agent_session_entries_of_the_selected_rows()
                                                      if entry.status != "exited"]
            self.add_menu_items_for_the_selected_rows(menu, self.menu_target_agent_session_entries,
                                                      any(selected_item.kind == "window" for selected_item in selected_items))
            return
        if item is None or item.kind == "new_window":
            self.add_new_window_menu_items(menu)  # empty space, or the "+" row
            return
        if item.child_items and item.kind in ("monitor", "window"):  # (a tab row opens with its arrow)
            self.add_menu_item(menu, "Collapse" if self.outline_view.isItemExpanded_(item) else "Expand",
                               "menuToggleExpansion:")
            menu.addItem_(NSMenuItem.separatorItem())
        if item.kind == "monitor":  # also when Hide Idle hides all of its window rows
            self.add_pause_and_resume_menu_items(menu, self.list_agent_session_entries_below(item))
            return
        if item.kind == "window":
            self.add_menu_item(menu, "Show window", "menuFocus:")
            self.add_menu_item(menu, "Rename", "menuRename:")
            menu.addItem_(NSMenuItem.separatorItem())
            # A new tab in this window, in the folder of its selected tab's agent session (R13).
            for agent_tool_name in self.owner.list_agent_tool_names():
                self.add_menu_item(menu, f"New tab with {agent_tool_name} session…", "menuNewAgentSessionInWindow:"
                                   ).setRepresentedObject_(agent_tool_name)
            menu.addItem_(NSMenuItem.separatorItem())
            self.add_pause_and_resume_menu_items(menu, self.list_agent_session_entries_below(item))
            if item.parent_item is not None:
                menu.addItem_(NSMenuItem.separatorItem())
                other_monitor_items = [monitor_item for monitor_item in self.top_level_items if monitor_item is not item.parent_item]
                submenu = NSMenu.alloc().initWithTitle_("Move to monitor")
                for monitor_item in other_monitor_items:
                    self.add_menu_item(submenu, monitor_item.entry.name or "Monitor", "menuMoveToMonitor:"
                                       ).setRepresentedObject_(monitor_item.item_id)
                move_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("Move to monitor", None, "")
                move_item.setSubmenu_(submenu)
                menu.addItem_(move_item)
            menu.addItem_(NSMenuItem.separatorItem())
            self.add_menu_item(menu, "Close window", "menuCloseWindow:")
            return
        if item.kind == "tab":
            self.add_menu_item(menu, "Show tab", "menuFocus:")
            self.add_menu_item(menu, "Rename", "menuRename:")
            duplicate_item = self.add_menu_item(menu, "Duplicate this tab", "menuDuplicate:")
            if item.entry.status == "exited":  # the copy would continue its conversation, found through its process
                duplicate_item.setEnabled_(False)
                duplicate_item.setToolTip_("The agent has ended")
            elif not self.owner.find_launch_command(item.entry.agent_tool_name):
                duplicate_item.setEnabled_(False)
                duplicate_item.setToolTip_(f"Settings has no launch command for {item.entry.agent_tool_name}")
            other_window_items = [window_item for window_item in self.list_window_items() if window_item is not item.parent_item]
            if other_window_items:
                submenu = NSMenu.alloc().initWithTitle_("Move to window")
                for window_item in other_window_items:
                    submenu_item = self.add_menu_item(submenu, window_item.entry.title, "menuMoveToWindow:")
                    submenu_item.setRepresentedObject_(window_item.item_id)
                move_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("Move to window", None, "")
                move_item.setSubmenu_(submenu)
                menu.addItem_(move_item)
            move_to_new_window_item = self.add_menu_item(menu, "Extract to new window", "menuMoveToNewWindow:")
            if item.parent_item is not None and item.parent_item.entry.total_tab_count < 2:
                move_to_new_window_item.setEnabled_(False)
                move_to_new_window_item.setToolTip_("This tab is its window's only tab")
            # Typed into the agent only while it waits at its prompt (R15.1; user request, 2026-10-19).
            menu.addItem_(NSMenuItem.separatorItem())
            for command in SLASH_COMMANDS_IN_THE_TAB_MENU:
                command_item = self.add_menu_item(menu, command, "menuSendSlashCommand:")
                command_item.setRepresentedObject_(command)
                if item.entry.status == "exited":
                    command_item.setEnabled_(False)
                    command_item.setToolTip_("The agent has ended")
                elif command not in item.entry.slash_commands:
                    command_item.setEnabled_(False)
                    command_item.setToolTip_(f"The dashboard can't type commands into {item.entry.agent_tool_name or 'this agent'}")
                elif item.entry.status not in STATES_OF_AN_AGENT_WAITING_AT_ITS_PROMPT:
                    command_item.setEnabled_(False)
                    # The user's wording, 2026-10-28, with the state as the row names it ("thinking", not "working").
                    shown_state = build_agent_session_row_text_parts(item.entry, [], time.time()).state_text
                    command_item.setToolTip_(f"Only available when the agent waits at its prompt (it is currently {shown_state})")
            kill_item = self.add_menu_item(menu, "Kill session", "menuKillSession:")
            if item.entry.status == "exited":
                kill_item.setEnabled_(False)
                kill_item.setToolTip_("The agent has ended")
            menu.addItem_(NSMenuItem.separatorItem())
            self.add_pause_and_resume_menu_items(menu, [item.entry])

    @objc.python_method
    def list_agent_session_entries_below(self, item):
        """Returns the agent sessions in a window row, or in all windows of a monitor row (also
        those whose rows Hide Idle hides)."""
        if item.kind == "window":
            return list(item.entry.agent_sessions)
        return [entry for window_entry in item.entry.windows for entry in window_entry.agent_sessions]

    @objc.python_method
    def list_agent_session_entries(self):
        """Returns every agent session in the list, those whose rows Hide Idle hides too (the bottom bar's pause or resume button acts on all of them)."""
        return [entry for _, _, entry in iterate_agent_session_entries(self.snapshot)] if self.snapshot else []

    @objc.python_method
    def add_pause_and_resume_menu_items(self, menu, agent_session_entries):
        """Adds "Pause session" for those of the agent sessions that run and "Resume session" for
        those that are paused, with the count when the row holds several ("Pause 3 sessions";
        user request, 2026-10-20)."""
        sessions_to_pause, sessions_to_resume = split_agent_sessions_into_pausable_and_resumable(agent_session_entries)
        for verb, entries, action_name in (("Pause", sessions_to_pause, "menuPauseSessions:"),
                                           ("Resume", sessions_to_resume, "menuResumeSessions:")):
            if not entries:
                continue
            title = f"{verb} session" if len(agent_session_entries) == 1 else f"{verb} {describe_session_count(len(entries))}"
            self.add_menu_item(menu, title, action_name).setRepresentedObject_([entry.session_id for entry in entries])

    @objc.python_method
    def add_menu_items_for_the_selected_rows(self, menu, agent_session_entries, a_window_row_is_selected):
        """The menu of two or more selected rows, acting on their agent sessions (user request,
        2026-10-21): /compact, /clear and Kill; Pause and Resume; Talk to Each Other…, last (user
        request, 2026-10-27).

        Kill says how many sessions only when a window row is selected, since that row stands for
        all of its window's tabs, hidden idle ones too; for tab rows alone the selection shows it
        (user requests, 2026-10-21)."""
        for command in SLASH_COMMANDS_IN_THE_TAB_MENU:
            agent_sessions_that_take_it = list_agent_sessions_that_take_slash_command(agent_session_entries, command)
            command_item = self.add_menu_item(
                menu, f"{command} {describe_session_count(len(agent_sessions_that_take_it))}" if agent_sessions_that_take_it else command,
                "menuSendSlashCommandToSelectedSessions:")
            command_item.setRepresentedObject_(command)
            if not agent_sessions_that_take_it:
                agent_tool_names = sorted({entry.agent_tool_name or "these agents" for entry in agent_session_entries})
                command_item.setEnabled_(False)
                command_item.setToolTip_("Only available after the paused sessions are resumed"  # the user's wording, 2026-10-28
                                         if any(command in entry.slash_commands for entry in agent_session_entries)
                                         else f"AgentCommandTab can't type commands into {' or '.join(agent_tool_names)}")
                continue
            tool_tip_lines = []
            if len(agent_sessions_that_take_it) < len(agent_session_entries):
                # The user's wording, 2026-10-28 (paused sessions, left out too, are counted as not supported).
                tool_tip_lines.append(f"Typed only into the {len(agent_sessions_that_take_it)} of "
                                      f"{len(agent_session_entries)} where this command is supported.")
            if any(entry.status not in STATES_OF_AN_AGENT_WAITING_AT_ITS_PROMPT for entry in agent_sessions_that_take_it):
                tool_tip_lines.append("A session in the middle of a turn will get this command when the turn ends")  # the user's wording, 2026-10-28
            command_item.setToolTip_("\n".join(tool_tip_lines) or None)
        self.add_menu_item(menu, f"Kill {describe_session_count(len(agent_session_entries))}" if a_window_row_is_selected
                           else "Kill sessions", "menuKillSelectedSessions:")
        menu.addItem_(NSMenuItem.separatorItem())
        self.add_pause_and_resume_menu_items(menu, agent_session_entries)
        menu.addItem_(NSMenuItem.separatorItem())
        talk_item = self.add_menu_item(menu, "Talk to each other…", "menuTalkToEachOther:")
        if len(self.list_agent_sessions_that_can_talk_to_each_other(agent_session_entries)) < 2:
            talk_item.setEnabled_(False)
            talk_item.setToolTip_(f"Select two or more {' or '.join(sorted(AGENT_TOOL_NAMES_WHOSE_SESSIONS_CAN_TALK_TO_EACH_OTHER))} "
                                  f"sessions to send a 'Talk to each other...' message.")  # the user's wording, 2026-10-28

    @objc.python_method
    def list_agent_sessions_that_can_talk_to_each_other(self, agent_session_entries):
        """Returns the agent sessions of tools whose sessions other sessions can send messages to."""
        return [entry for entry in agent_session_entries
                if entry.agent_tool_name in AGENT_TOOL_NAMES_WHOSE_SESSIONS_CAN_TALK_TO_EACH_OTHER]

    def menuTalkToEachOther_(self, sender):
        agent_session_entries = self.list_agent_sessions_that_can_talk_to_each_other(self.menu_target_agent_session_entries)
        left_out_agent_session_entries = [entry for entry in self.menu_target_agent_session_entries
                                          if entry not in agent_session_entries]
        note = self.owner.ask_for_note_asking_agent_sessions_to_talk_to_each_other(
            agent_session_entries, left_out_agent_session_entries)
        if note is None:
            return
        self.owner.submit_action_and_report_failure(
            ActionRequest("ask_agent_sessions_to_talk_to_each_other",
                          {"session_ids": [entry.session_id for entry in agent_session_entries]}, {"note": note}),
            "Not every session got the message")

    def menuSendSlashCommandToSelectedSessions_(self, sender):
        command = sender.representedObject()
        agent_session_entries = list_agent_sessions_that_take_slash_command(self.menu_target_agent_session_entries, command)
        if command == COMMAND_THAT_STARTS_A_NEW_CONVERSATION and not self.owner.ask_for_confirmation(
                f"Are you sure you want to /clear the {describe_session_count(len(agent_session_entries))} "
                f"({list_tab_titles_for_alert(agent_session_entries)})?",  # as the one-tab question, user request, 2026-10-27
                "clear"):
            return
        self.owner.submit_action_and_report_failure(
            ActionRequest("send_slash_command_to_agent_sessions",
                          {"session_ids": [entry.session_id for entry in agent_session_entries]}, {"command": command}),
            f"{command} wasn't sent to every session")

    def menuKillSelectedSessions_(self, sender):
        agent_session_entries = self.menu_target_agent_session_entries
        if self.owner.ask_for_confirmation(
                f"Kill ends the agent processes of {describe_session_count(len(agent_session_entries))} "
                f"({list_tab_titles_for_alert(agent_session_entries)}) at once, with whatever they were doing; "
                "their tabs stay open with their shells.", "kill"):
            self.owner.submit_action_and_report_failure(
                ActionRequest("end_agent_processes", {"session_ids": [entry.session_id for entry in agent_session_entries]}),
                "Not every session was ended")

    def menuPauseSessions_(self, sender):
        self.pause_or_resume_agent_sessions(list(sender.representedObject()), pause=True)

    def menuResumeSessions_(self, sender):
        self.pause_or_resume_agent_sessions(list(sender.representedObject()), pause=False)

    @objc.python_method
    def pause_or_resume_agent_sessions(self, session_ids, pause):
        """Asks the core to pause or resume these agent sessions; an alert names those that failed and why."""
        # A pause that stopped the agent can still fail for processes below it run with sudo.
        what_failed = "Not everything was paused" if pause else (
            "The session wasn't resumed" if len(session_ids) == 1 else "Not every session was resumed")
        self.owner.submit_action_and_report_failure(
            ActionRequest("pause_agent_sessions" if pause else "resume_agent_sessions", {"session_ids": session_ids}),
            what_failed)

    @objc.python_method
    def add_new_window_menu_items(self, menu):
        """Adds "New Claude Code window" and the like, one per agent tool the settings include:
        each starts that tool in a new iTerm2 window (R13.1)."""
        for agent_tool_name in self.owner.list_agent_tool_names():
            self.add_menu_item(menu, f"New {agent_tool_name} window", "menuNewWindowWithAgentSession:"
                               ).setRepresentedObject_(agent_tool_name)

    def newWindowButtonClicked_(self, sender):
        menu = NSMenu.alloc().initWithTitle_("")
        self.add_new_window_menu_items(menu)
        menu.popUpMenuPositioningItem_atLocation_inView_(None, (0, sender.frame().size.height), sender)

    @objc.python_method
    def add_menu_item(self, menu, title, action_name):
        menu_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, action_name, "")
        menu_item.setTarget_(self)
        menu.addItem_(menu_item)
        return menu_item

    @objc.python_method
    def list_window_items(self):
        """Returns the window items in the order the list shows them (monitor groups included)."""
        window_items = []
        for top_level_item in self.top_level_items:
            if top_level_item.kind == "window":
                window_items.append(top_level_item)
            else:
                window_items.extend(child_item for child_item in top_level_item.child_items if child_item.kind == "window")
        return window_items

    @objc.python_method
    def choose_selected_agent_session_of_window(self, window_item):
        """Returns the agent session in the window's selected tab, else the window's first one."""
        agent_session_entries = window_item.entry.agent_sessions
        return next((entry for entry in agent_session_entries if entry.tab_id == window_item.entry.selected_tab_id),
                    agent_session_entries[0])

    def menuFocus_(self, sender):
        self.show_item(self.menu_target_item)

    @objc.python_method
    def show_item(self, item):
        """Brings a tab to the front, or a window with the tab it already shows (even one without an agent)."""
        if item.kind == "tab":
            self.submit_action(ActionRequest("focus_agent_session", {"session_id": item.entry.session_id}))
        else:
            self.submit_action(ActionRequest("focus_window", {"window_id": item.item_id}))
        self.on_tab_focused()

    def menuNewAgentSessionInWindow_(self, sender):
        window_item = self.menu_target_item
        folder = self.choose_selected_agent_session_of_window(window_item).working_folder
        self.owner.start_agent_session_from_menu(window_item.item_id, folder, sender.representedObject())

    def menuNewWindowWithAgentSession_(self, sender):
        self.owner.start_agent_session_from_menu(None, None, sender.representedObject())

    def menuMoveToMonitor_(self, sender):
        self.submit_action(ActionRequest("move_window_to_monitor", {"window_id": self.menu_target_item.item_id},
                                         {"monitor_id": sender.representedObject()}))

    def menuToggleExpansion_(self, sender):
        self.toggle_expansion(self.menu_target_item)

    def menuSendSlashCommand_(self, sender):
        tab_item, command = self.menu_target_item, sender.representedObject()
        if command == COMMAND_THAT_STARTS_A_NEW_CONVERSATION and not self.owner.ask_for_confirmation(
                f'Are you sure you want to /clear the "{tab_item.entry.tab_title}" session?',  # user's wording, 2026-10-27
                "clear"):
            return
        self.owner.submit_action_and_report_failure(
            ActionRequest("send_slash_command", {"session_id": tab_item.entry.session_id}, {"command": command}),
            f"{command} wasn't sent")

    def menuKillSession_(self, sender):
        tab_item = self.menu_target_item
        if self.owner.ask_for_confirmation(
                f'Kill session ends the {tab_item.entry.agent_tool_name} process in "{tab_item.entry.tab_title}" at once, '
                "with whatever it was doing; the tab stays open with its shell.", "kill"):
            self.owner.submit_action_and_report_failure(
                ActionRequest("end_agent_process", {"session_id": tab_item.entry.session_id}), "The session wasn't ended")

    def menuCloseWindow_(self, sender):
        window_item = self.menu_target_item
        if self.owner.confirm_closing_window(window_item.entry):
            self.submit_action(ActionRequest("close_window", {"window_id": window_item.item_id}))

    def menuRename_(self, sender):
        self.begin_rename(self.menu_target_item)

    def menuMoveToWindow_(self, sender):
        self.submit_action(ActionRequest("move_tab", {"tab_id": self.menu_target_item.item_id},
                                         {"target_window_id": sender.representedObject(), "drop_before_tab_id": None}))

    def menuMoveToNewWindow_(self, sender):
        self.submit_action(ActionRequest("move_tab_to_new_window", {"tab_id": self.menu_target_item.item_id}))

    def menuDuplicate_(self, sender):
        tab_entry = self.menu_target_item.entry
        self.owner.duplicate_agent_session(tab_entry.tab_id, tab_entry.working_folder, tab_entry.agent_tool_name)

    # ----- drag and drop (PLAN.md section 9) -----

    def outlineView_pasteboardWriterForItem_(self, outline_view, item):
        # Dragging one of several selected rows would drag them all; only the row under the
        # pointer moves (None leaves a row out of the drag).
        if item.kind not in ("tab", "window") or item is not self.item_under_press:
            return None
        pasteboard_item = NSPasteboardItem.alloc().init()
        pasteboard_item.setString_forType_(item.item_id, TAB_DRAG_TYPE if item.kind == "tab" else WINDOW_DRAG_TYPE)
        return pasteboard_item

    def outlineView_draggingSession_willBeginAtPoint_forItems_(self, outline_view, session, point, items):
        self.drag_in_progress = True
        self.dragged_item = items[0] if items else None
        self.cancel_peek_timer()  # a drag, not a hold
        if self.peek_in_progress:  # the drag began after the hold had already shown the tab
            self.peek_in_progress = False
            self.owner.end_peek()

    def outlineView_draggingSession_endedAtPoint_operation_(self, outline_view, session, point, operation):
        self.drag_in_progress = False
        self.dragged_item = None
        self.apply_held_snapshot()

    def outlineView_validateDrop_proposedItem_proposedChildIndex_(self, outline_view, drag_info, proposed_item, child_index):
        dragged_item = getattr(self, "dragged_item", None)
        if dragged_item is None:
            return NSDragOperationNone
        if proposed_item is not None and proposed_item.kind == "process":
            proposed_item, child_index = proposed_item.parent_item, -1  # a drop on a process row is one on its tab row
        if dragged_item.kind == "tab" and self.list_sort_order != "manual":
            # Sorted rows aren't in iTerm2's tab order, so a tab can only go to the end of another window.
            window_item = proposed_item.parent_item if proposed_item is not None and proposed_item.kind == "tab" else proposed_item
            if window_item is None or window_item.kind != "window" or window_item is dragged_item.parent_item:
                return NSDragOperationNone
            outline_view.setDropItem_dropChildIndex_(window_item, -1)
            return NSDragOperationMove
        if dragged_item.kind == "tab":
            # Tabs drop between the tabs of a window: retarget drops on a tab to its window.
            if proposed_item is not None and proposed_item.kind == "tab":
                window_item = proposed_item.parent_item
                outline_view.setDropItem_dropChildIndex_(window_item, window_item.child_items.index(proposed_item))
                return NSDragOperationMove
            if proposed_item is not None and proposed_item.kind == "window":
                if child_index < 0:
                    outline_view.setDropItem_dropChildIndex_(proposed_item, len(proposed_item.child_items))
                return NSDragOperationMove
            return NSDragOperationNone
        # Windows reorder among the windows of the same group (R12), in the manual order only...
        parent_item = dragged_item.parent_item
        if self.list_sort_order == "manual":
            if proposed_item is parent_item and child_index >= 0:
                return NSDragOperationMove
            if proposed_item is not None and proposed_item.kind == "window" and proposed_item.parent_item is parent_item:
                sibling_items = parent_item.child_items if parent_item is not None else self.top_level_items
                outline_view.setDropItem_dropChildIndex_(parent_item, sibling_items.index(proposed_item))
                return NSDragOperationMove
        # ...or move to another monitor when dropped into that monitor's group (R32).
        target_monitor_item = None
        if proposed_item is not None and proposed_item.kind == "monitor":
            target_monitor_item = proposed_item
        elif proposed_item is not None and proposed_item.kind in ("window", "tab"):
            target_monitor_item = proposed_item.parent_item if proposed_item.kind == "window" else proposed_item.parent_item.parent_item
        if target_monitor_item is not None and target_monitor_item is not parent_item:
            outline_view.setDropItem_dropChildIndex_(target_monitor_item, -1)
            return NSDragOperationMove
        return NSDragOperationNone

    def outlineView_acceptDrop_item_childIndex_(self, outline_view, drag_info, target_item, child_index):
        dragged_item = getattr(self, "dragged_item", None)
        if dragged_item is None:
            return False
        if dragged_item.kind == "tab":
            sibling_items = [child_item for child_item in target_item.child_items if child_item is not dragged_item]
            original_index = target_item.child_items.index(dragged_item) if dragged_item in target_item.child_items else None
            if original_index is not None and child_index in (original_index, original_index + 1):
                return True  # dropped back where it was: moving it would shift the hidden tabs around it
            drop_index = child_index - (1 if original_index is not None and original_index < child_index else 0)
            drop_before_tab_id = sibling_items[drop_index].item_id if 0 <= drop_index < len(sibling_items) else None
            self.submit_action(ActionRequest("move_tab", {"tab_id": dragged_item.item_id},
                                             {"target_window_id": target_item.item_id,
                                              "drop_before_tab_id": drop_before_tab_id}))
            return True
        if target_item is not None and target_item.kind == "monitor" and target_item is not dragged_item.parent_item:
            self.submit_action(ActionRequest("move_window_to_monitor", {"window_id": dragged_item.item_id},
                                             {"monitor_id": target_item.item_id}))
            return True
        sibling_items = target_item.child_items if target_item is not None else self.top_level_items
        remaining_items = [sibling_item for sibling_item in sibling_items if sibling_item is not dragged_item]
        original_index = sibling_items.index(dragged_item)
        drop_index = child_index - (1 if original_index < child_index else 0)
        drop_before_window_id = remaining_items[drop_index].item_id if 0 <= drop_index < len(remaining_items) else None
        self.submit_action(ActionRequest("reorder_window_list", {"window_id": dragged_item.item_id},
                                         {"drop_before_window_id": drop_before_window_id}))
        return True
