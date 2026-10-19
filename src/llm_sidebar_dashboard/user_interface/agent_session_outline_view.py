"""The shared list of monitors, windows and agent session tabs (PLAN.md section 8, "Shared list").

Two instances exist (dropdown and sidebar), each with its own NSOutlineView, expanded rows and
selection, fed the same snapshots. Main thread only.

How snapshots are applied without breaking expansion, renames or drags:
- one OutlineListItem per (kind, ID) lives as long as that ID exists, so expansion and
  selection, which NSOutlineView tracks by item identity, survive updates;
- when the rows' structure (which rows, in which order) is unchanged, the items' entries are
  replaced and only the visible rows are redrawn; otherwise the outline view is reloaded and
  the expansion and selection are restored from the controller's own records;
- while a rename or a drag is in progress, snapshots are held and the newest is applied when
  it ends.
"""

import time

import objc
from AppKit import (NSAnimationContext, NSApp, NSAttributedString, NSButton, NSColor, NSDragOperationMove,
                    NSDragOperationNone, NSEventModifierFlagControl, NSFont, NSFontAttributeName, NSFontWeightBold,
                    NSFontWeightRegular, NSForegroundColorAttributeName, NSImage, NSImageOnly, NSImageScaleNone,
                    NSImageView, NSLineBreakByTruncatingTail, NSLineBreakByWordWrapping,
                    NSMakeRect, NSMenu, NSMenuItem, NSMutableAttributedString, NSMutableParagraphStyle, NSOutlineView,
                    NSParagraphStyleAttributeName, NSPasteboardItem,
                    NSTableCellView, NSTableColumn, NSTableColumnAutoresizingMask,
                    NSTableViewLastColumnOnlyAutoresizingStyle, NSTableViewSelectionHighlightStyleNone,
                    NSTableViewStylePlain, NSTextAlignmentRight, NSTextField, NSWindowStyleMaskNonactivatingPanel,
                    NSZeroRect)
from Foundation import NSMutableIndexSet, NSObject, NSRunLoop, NSRunLoopCommonModes, NSTimer

from llm_sidebar_dashboard.dashboard_core_api import ActionRequest
from llm_sidebar_dashboard.list_sorting import sort_agent_session_entries, sort_window_entries
from llm_sidebar_dashboard.settings_store import DEFAULT_SETTINGS
from llm_sidebar_dashboard.snapshot_data_classes import iterate_agent_session_entries
from llm_sidebar_dashboard.user_interface.agent_tool_icons import build_agent_tool_icons, build_text_image
from llm_sidebar_dashboard.user_interface.row_text_rendering import (build_agent_session_row_text_parts,
                                                                     build_monitor_row_text_parts,
                                                                     build_window_row_text_parts)

ROW_HEIGHT = 20
PREVIEW_LINE_HEIGHT = 16  # the height of a 12-point line, so the tree's lines carry on beside the preview
PREVIEW_LINE_COUNT = 2
PREVIEW_AREA_HEIGHT = PREVIEW_LINE_HEIGHT * PREVIEW_LINE_COUNT
TAB_ROW_HEIGHT_WITH_PREVIEW = ROW_HEIGHT + PREVIEW_AREA_HEIGHT  # a tab row whose preview is open (R23)
FONT_SIZE = 12
PREVIEW_FONT_SIZE = 11
PREVIEW_BUTTON_SIZE = 16  # an expanded window row's buttons that open and close its tabs' previews
GAP_BETWEEN_PREVIEW_BUTTONS = 4
ROW_LEFT_MARGIN = 8
GAP_AFTER_PREFIX = 2
NAME_FIELD_PADDING = 4
GAP_BEFORE_DETAILS = 8
GAP_BETWEEN_COLUMNS = 6  # between the columns at a tab row's right edge
ROW_RIGHT_MARGIN = 8
TEXT_FIELD_INSETS = 5  # a label's text sits a few points inside its field
ROOM_FOR_GROWING_DETAILS = "0000"  # elapsed times grow ("9m" becomes "10m") without a refit
SECONDS_TO_HOLD_BEFORE_PEEKING = 0.15
SMALLEST_TAB_NAME_WIDTH = 80  # a narrower sidebar drops columns: created time and time since created first, then context, icon, state
TAB_DRAG_TYPE = "org.llm-sidebar-dashboard.tab-id"
WINDOW_DRAG_TYPE = "org.llm-sidebar-dashboard.window-id"
COLUMN_IDENTIFIER = "main"
ROW_VIEW_IDENTIFIER = "agent-list-row"


def build_color_from_name(color_name):
    """Returns the system color for a RowTextParts color name."""
    return {
        "light_blue": NSColor.systemCyanColor(),
        "red": NSColor.systemRedColor(),
        "orange": NSColor.systemOrangeColor(),
        "green": NSColor.systemGreenColor(),
        "blue": NSColor.systemBlueColor(),
        "gray": NSColor.secondaryLabelColor(),
    }.get(color_name, NSColor.labelColor())


class OutlineListItem(NSObject):
    """One row's item: kind is "monitor", "window" or "tab"; entry is its snapshot record."""

    def initWithKind_itemId_(self, kind, item_id):
        self = objc.super(OutlineListItem, self).init()
        if self is None:
            return None
        self.kind = kind
        self.item_id = item_id
        self.entry = None
        self.parent_item = None
        self.child_items = []
        return self


class OutlineViewWithoutDisclosureTriangles(NSOutlineView):
    """An NSOutlineView that draws no disclosure triangles (rows are expanded by clicking them),
    and that tells its delegate when the button goes down on a row and comes back up, so holding
    a tab row can show that tab for a moment."""

    def frameOfOutlineCellAtRow_(self, row):
        return NSZeroRect

    def mouseDown_(self, event):
        is_plain_press = event.clickCount() == 1 and not event.modifierFlags() & NSEventModifierFlagControl
        if is_plain_press:
            self.delegate().row_pressed(self.rowAtPoint_(self.convertPoint_fromView_(event.locationInWindow(), None)))
        objc.super(OutlineViewWithoutDisclosureTriangles, self).mouseDown_(event)  # returns once the button is up
        if is_plain_press:
            self.delegate().row_released()


def build_index_set(indexes):
    """Returns an NSIndexSet holding the given row indexes."""
    index_set = NSMutableIndexSet.alloc().init()
    for index in indexes:
        index_set.addIndex_(index)
    return index_set


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


def make_row_icon_button(symbol_name, fallback_title, accessibility_description):
    """Returns a small borderless gray button showing an SF Symbol (or a text fallback), without a
    tooltip, since the list's rows have no hover popups (user request, 2026-10-19)."""
    button = NSButton.alloc().initWithFrame_(NSMakeRect(0, 0, PREVIEW_BUTTON_SIZE, PREVIEW_BUTTON_SIZE))
    button.setBordered_(False)
    image = NSImage.imageWithSystemSymbolName_accessibilityDescription_(symbol_name, accessibility_description)
    if image is not None:
        button.setImage_(image)
        button.setImagePosition_(NSImageOnly)
    else:
        button.setTitle_(fallback_title)
    button.setContentTintColor_(NSColor.secondaryLabelColor())
    return button


class AgentListRowView(NSTableCellView):
    """A row: [connector + dot] [name] [details] ... then, on a tab row, columns at the right
    edge: [context size] [created time] [time since created] [tool icon] [state] [state
    duration]. The list controller sets each column's width, 0 for a hidden column, so the
    columns line up from row to row. The state is left-aligned in its column; the others are
    right-aligned. A tab row whose preview is open has two more lines, [tree lines] [preview],
    with the preview starting under the name. An expanded window row ends in two buttons that
    open and close its tabs' previews."""

    def initWithFrame_(self, frame):
        self = objc.super(AgentListRowView, self).initWithFrame_(frame)
        if self is None:
            return None
        self.prefix_text_field = make_label_text_field()
        self.name_text_field = make_label_text_field()
        self.details_text_field = make_label_text_field()
        self.context_text_field = make_label_text_field()
        self.created_time_text_field = make_label_text_field()
        self.existence_duration_text_field = make_label_text_field()
        self.state_text_field = make_label_text_field()
        self.elapsed_time_text_field = make_label_text_field()
        self.icon_image_view = NSImageView.alloc().initWithFrame_(NSMakeRect(0, 0, 10, ROW_HEIGHT))
        self.icon_image_view.setImageScaling_(NSImageScaleNone)
        self.preview_connector_text_field = make_multiline_label_text_field(PREVIEW_LINE_COUNT)
        self.preview_text_field = make_multiline_label_text_field(PREVIEW_LINE_COUNT)
        self.open_previews_button = make_row_icon_button("rectangle.expand.vertical", "+", "Show the tabs' previews")
        self.close_previews_button = make_row_icon_button("rectangle.compress.vertical", "−", "Hide the tabs' previews")
        for subview in (self.prefix_text_field, self.name_text_field, self.details_text_field, self.context_text_field,
                        self.created_time_text_field, self.existence_duration_text_field, self.state_text_field,
                        self.elapsed_time_text_field, self.icon_image_view, self.preview_connector_text_field,
                        self.preview_text_field, self.open_previews_button, self.close_previews_button):
            self.addSubview_(subview)
        self.setTextField_(self.name_text_field)
        self.context_column_width = 0  # 0 while the column is hidden, or no agent session's context size is known
        self.created_time_column_width = 0
        self.existence_duration_column_width = 0
        self.icon_column_width = 0  # 0 unless two or more agent tools run
        self.state_column_width = 0
        self.elapsed_time_column_width = 0
        self.has_preview_line = False  # a tab row whose preview is open
        self.shows_preview_buttons = False  # an expanded window row
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
        name_x = ROW_LEFT_MARGIN + prefix_width + GAP_AFTER_PREFIX
        text_right_limit = bounds.size.width - ROW_RIGHT_MARGIN  # where the name and details end; a window's details are right-aligned there
        self.open_previews_button.setHidden_(not self.shows_preview_buttons)
        self.close_previews_button.setHidden_(not self.shows_preview_buttons)
        if self.shows_preview_buttons:  # open, then close, at the right edge
            button_y = preview_area_height + (main_line_height - PREVIEW_BUTTON_SIZE) / 2
            close_button_x = bounds.size.width - ROW_RIGHT_MARGIN - PREVIEW_BUTTON_SIZE
            open_button_x = close_button_x - GAP_BETWEEN_PREVIEW_BUTTONS - PREVIEW_BUTTON_SIZE
            self.close_previews_button.setFrame_(NSMakeRect(close_button_x, button_y, PREVIEW_BUTTON_SIZE, PREVIEW_BUTTON_SIZE))
            self.open_previews_button.setFrame_(NSMakeRect(open_button_x, button_y, PREVIEW_BUTTON_SIZE, PREVIEW_BUTTON_SIZE))
            text_right_limit = open_button_x - GAP_BEFORE_DETAILS
        row_has_state = bool(self.state_text_field.stringValue())  # tab rows only
        column_right_edge = bounds.size.width - ROW_RIGHT_MARGIN
        for column_view, column_width in ((self.elapsed_time_text_field, self.elapsed_time_column_width),
                                          (self.state_text_field, self.state_column_width),
                                          (self.icon_image_view, self.icon_column_width),
                                          (self.existence_duration_text_field, self.existence_duration_column_width),
                                          (self.created_time_text_field, self.created_time_column_width),
                                          (self.context_text_field, self.context_column_width)):  # right to left
            is_shown = row_has_state and column_width > 0
            column_view.setHidden_(not is_shown)
            if not is_shown:
                continue
            column_x = column_right_edge - column_width
            if column_view is self.icon_image_view:
                column_view.setFrame_(NSMakeRect(column_x, preview_area_height, column_width, main_line_height))
            else:
                column_view.setFrame_(NSMakeRect(column_x, y, column_width, text_height))
            text_right_limit = column_x - GAP_BEFORE_DETAILS
            column_right_edge = column_x - GAP_BETWEEN_COLUMNS
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
        self.details_text_field.setFrame_(NSMakeRect(details_x, y, max(text_right_limit - details_x, 0), text_height))
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
        duplicate_agent_session(tab_id, folder, agent_tool_name): opens a copy of the tab's agent
            session right after it.
        find_launch_command(agent_tool_name): the tool's launch command from the settings, or "".
        start_peek(session_id), end_peek(): a tab row is held (shown for a moment), then released.
    """

    def initWithOwner_(self, owner):
        self = objc.super(AgentSessionListController, self).init()
        if self is None:
            return None
        self.owner = owner
        self.submit_action = owner.submit_action
        self.on_tab_focused = lambda: owner.tab_was_focused(self)
        self.on_row_count_changed = lambda: owner.rows_changed(self)
        self.items_by_key = {}
        self.top_level_items = []
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
        self.item_toggled_by_first_click = None  # that row, if the click opened or closed it
        self.applying_expansion_state = False
        self.item_whose_expansion_is_changing = None
        self.item_being_renamed = None
        self.title_before_rename = ""
        self.regular_font = NSFont.monospacedSystemFontOfSize_weight_(FONT_SIZE, NSFontWeightRegular)
        self.bold_font = NSFont.monospacedSystemFontOfSize_weight_(FONT_SIZE, NSFontWeightBold)
        self.preview_font = NSFont.monospacedSystemFontOfSize_weight_(PREVIEW_FONT_SIZE, NSFontWeightRegular)
        self.right_aligned_paragraph_style = NSMutableParagraphStyle.alloc().init()
        self.right_aligned_paragraph_style.setAlignment_(NSTextAlignmentRight)
        self.right_aligned_paragraph_style.setLineBreakMode_(NSLineBreakByTruncatingTail)
        # Styled text overrides the field's own line breaking, so the preview's wrapping is set in its text too.
        self.wrapping_paragraph_style = NSMutableParagraphStyle.alloc().init()
        self.wrapping_paragraph_style.setLineBreakMode_(NSLineBreakByWordWrapping)
        self.preview_connector_paragraph_style = NSMutableParagraphStyle.alloc().init()  # one tree line per preview line
        self.preview_connector_paragraph_style.setMinimumLineHeight_(PREVIEW_LINE_HEIGHT)
        self.preview_connector_paragraph_style.setMaximumLineHeight_(PREVIEW_LINE_HEIGHT)
        self.agent_tool_icons_by_name = build_agent_tool_icons()
        self.context_column_width = 0  # 0 while the column is hidden, or no agent session's context size is known
        self.created_time_column_width = 0
        self.existence_duration_column_width = 0
        self.icon_column_width = 0  # 0 unless two or more agent tools are running
        self.width_is_set_by_the_user = False  # True for the sidebar's list; the dropdown fits its width to the rows
        self.state_column_width = 0
        self.elapsed_time_column_width = 0
        self.list_sort_order = "manual"  # set by the Sort pop-up (list_sorting.py)
        self.shown_columns = list(DEFAULT_SETTINGS["shown_columns"])  # set from the settings (settings_store.LIST_COLUMNS)
        self.outline_view = self.build_outline_view()
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
        outline_view.setSelectionHighlightStyle_(NSTableViewSelectionHighlightStyleNone)
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
        show_monitor_rows = len(snapshot.monitors) > 1
        new_top_level_items = []
        live_keys = set()
        for monitor_entry in snapshot.monitors:
            window_items = [self.update_window_item(window_entry, live_keys)
                            for window_entry in sort_window_entries(monitor_entry.windows, self.list_sort_order)]
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
        self.collapsed_item_keys &= live_keys
        self.open_preview_item_keys &= live_keys
        self.top_level_items = new_top_level_items
        new_row_structure = self.list_row_structure()
        if new_row_structure == self.row_structure:
            self.redraw_visible_rows()
        else:
            self.row_structure = new_row_structure
            self.reload_and_restore_expansion()

    @objc.python_method
    def update_window_item(self, window_entry, live_keys):
        window_item = self.find_or_make_item("window", window_entry.window_id, window_entry, live_keys)
        self.set_child_items(window_item, [self.find_or_make_item("tab", agent_session_entry.tab_id, agent_session_entry, live_keys)
                                           for agent_session_entry in sort_agent_session_entries(
                                               window_entry.agent_sessions, self.list_sort_order)])
        return window_item

    @objc.python_method
    def set_list_sort_order(self, list_sort_order):
        """Shows the rows in this order from now on ("manual", "activity", "alphabet" or "created_time")."""
        if list_sort_order == self.list_sort_order:
            return
        self.list_sort_order = list_sort_order
        if self.held_snapshot is None and self.snapshot is not None:
            self.apply_snapshot(self.snapshot)  # (a held snapshot is sorted when it is applied)

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
        selected_rows = self.outline_view.selectedRowIndexes()
        selected_items = [self.outline_view.itemAtRow_(row) for row in range(self.outline_view.numberOfRows())
                          if selected_rows.containsIndex_(row)]
        self.outline_view.reloadData()
        self.apply_expansion_state(self.top_level_items)
        surviving_rows = [self.outline_view.rowForItem_(item) for item in selected_items
                          if (item.kind, item.item_id) in self.items_by_key]
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
                if (item.kind, item.item_id) in self.collapsed_item_keys:
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
        key = (item.kind, item.item_id)
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

    def outlineView_numberOfChildrenOfItem_(self, outline_view, item):
        return len(self.top_level_items if item is None else item.child_items)

    def outlineView_child_ofItem_(self, outline_view, index, item):
        return (self.top_level_items if item is None else item.child_items)[index]

    def outlineView_isItemExpandable_(self, outline_view, item):
        return bool(item.child_items)

    # ----- drawing rows -----

    def outlineView_viewForTableColumn_item_(self, outline_view, table_column, item):
        row_view = outline_view.makeViewWithIdentifier_owner_(ROW_VIEW_IDENTIFIER, self)
        if row_view is None:
            row_view = AgentListRowView.alloc().initWithFrame_(NSMakeRect(0, 0, 400, ROW_HEIGHT))
            row_view.setIdentifier_(ROW_VIEW_IDENTIFIER)
            row_view.name_text_field.setDelegate_(self)
            row_view.open_previews_button.setTarget_(self)
            row_view.open_previews_button.setAction_("openPreviewsButtonClicked:")
            row_view.close_previews_button.setTarget_(self)
            row_view.close_previews_button.setAction_("closePreviewsButtonClicked:")
        self.fill_row_view(row_view, item)
        return row_view

    def outlineView_heightOfRowByItem_(self, outline_view, item):
        return self.measure_row_height(item)

    @objc.python_method
    def preview_is_open(self, item):
        """Whether a tab row shows its preview: it was opened, and there is one to show."""
        return item.kind == "tab" and bool(item.entry.preview_line) and (item.kind, item.item_id) in self.open_preview_item_keys

    @objc.python_method
    def measure_row_height(self, item):
        """A tab row whose preview is open is taller; list_row_structure notes each row's height,
        so a preview appearing or going away in a snapshot reloads the rows."""
        return TAB_ROW_HEIGHT_WITH_PREVIEW if self.preview_is_open(item) else ROW_HEIGHT

    @objc.python_method
    def measure_fully_expanded_list_height(self):
        """Returns the height of the rows the list has with every row expanded (list_all_items)
        and the previews that are open: counting every preview would leave the dropdown mostly
        empty while they are closed."""
        return sum(self.measure_row_height(item) for item in self.list_all_items())

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
        if item.kind == "window":
            is_expanded = (item.kind, item.item_id) not in self.collapsed_item_keys
            return build_window_row_text_parts(item.entry, is_last_child_at_each_level, is_expanded)
        return build_monitor_row_text_parts(item.entry)

    @objc.python_method
    def fill_row_view(self, row_view, item):
        if row_view.name_text_field.isEditable():
            return  # never overwrite text the user is typing
        parts = self.build_row_text_parts(item)
        is_in_front = item.kind == "tab" and item.entry.is_in_front
        shows_preview_buttons = item.kind == "window" and (item.kind, item.item_id) not in self.collapsed_item_keys
        # Everything the row's look depends on: an unchanged row is neither refilled nor laid out
        # again, which keeps the 0.6-second refresh cheap with many rows.
        look = (parts, item.kind, is_in_front, parts.dot_blinks and self.blink_phase_is_dim, self.preview_is_open(item),
                shows_preview_buttons, self.context_column_width, self.created_time_column_width,
                self.existence_duration_column_width, self.icon_column_width, self.state_column_width,
                self.elapsed_time_column_width)
        if look == row_view.look:
            return
        row_view.look = look
        prefix = NSMutableAttributedString.alloc().init()
        prefix.appendAttributedString_(NSAttributedString.alloc().initWithString_attributes_(
            parts.connector_text, {NSFontAttributeName: self.regular_font,
                                   NSForegroundColorAttributeName: NSColor.tertiaryLabelColor()}))
        dot_color = build_color_from_name(parts.dot_color_name)
        if parts.dot_blinks and self.blink_phase_is_dim:
            dot_color = dot_color.colorWithAlphaComponent_(0.25)
        prefix.appendAttributedString_(NSAttributedString.alloc().initWithString_attributes_(
            parts.dot_glyph + (" " if parts.dot_glyph else ""),
            {NSFontAttributeName: self.regular_font, NSForegroundColorAttributeName: dot_color}))
        row_view.prefix_text_field.setAttributedStringValue_(prefix)
        name_color = NSColor.controlAccentColor() if is_in_front else NSColor.labelColor()
        row_view.name_text_field.setAttributedStringValue_(NSAttributedString.alloc().initWithString_attributes_(
            parts.name_text, {NSFontAttributeName: self.choose_name_font(item), NSForegroundColorAttributeName: name_color}))
        secondary_text_attributes = {NSFontAttributeName: self.regular_font,
                                     NSForegroundColorAttributeName: NSColor.secondaryLabelColor()}
        # Alignment goes in the text itself: styled text overrides a field's own alignment.
        right_aligned_secondary_text_attributes = dict(
            secondary_text_attributes, **{NSParagraphStyleAttributeName: self.right_aligned_paragraph_style})
        row_view.details_text_field.setAttributedStringValue_(NSAttributedString.alloc().initWithString_attributes_(
            parts.details_text,
            right_aligned_secondary_text_attributes if item.kind == "window" else secondary_text_attributes))
        row_view.state_text_field.setAttributedStringValue_(NSAttributedString.alloc().initWithString_attributes_(
            parts.state_text, secondary_text_attributes))
        row_view.elapsed_time_text_field.setAttributedStringValue_(NSAttributedString.alloc().initWithString_attributes_(
            parts.elapsed_time_text, right_aligned_secondary_text_attributes))
        for text_field, text in ((row_view.context_text_field, parts.context_text),
                                 (row_view.created_time_text_field, parts.created_time_text),
                                 (row_view.existence_duration_text_field, parts.existence_duration_text)):
            text_field.setAttributedStringValue_(NSAttributedString.alloc().initWithString_attributes_(
                text, right_aligned_secondary_text_attributes))
        row_view.icon_image_view.setImage_(self.find_agent_tool_icon(parts.agent_tool_name) if parts.agent_tool_name else None)
        row_view.has_preview_line = self.preview_is_open(item)
        row_view.shows_preview_buttons = shows_preview_buttons
        row_view.preview_connector_text_field.setAttributedStringValue_(NSAttributedString.alloc().initWithString_attributes_(
            "\n".join([parts.preview_connector_text] * PREVIEW_LINE_COUNT),
            {NSFontAttributeName: self.regular_font, NSForegroundColorAttributeName: NSColor.tertiaryLabelColor(),
             NSParagraphStyleAttributeName: self.preview_connector_paragraph_style}))
        row_view.preview_text_field.setAttributedStringValue_(NSAttributedString.alloc().initWithString_attributes_(
            parts.preview_text, {NSFontAttributeName: self.preview_font, NSForegroundColorAttributeName: NSColor.secondaryLabelColor(),
                                 NSParagraphStyleAttributeName: self.wrapping_paragraph_style}))
        row_view.context_column_width = self.context_column_width
        row_view.created_time_column_width = self.created_time_column_width
        row_view.existence_duration_column_width = self.existence_duration_column_width
        row_view.icon_column_width = self.icon_column_width
        row_view.state_column_width = self.state_column_width
        row_view.elapsed_time_column_width = self.elapsed_time_column_width
        row_view.setNeedsLayout_(True)

    @objc.python_method
    def find_agent_tool_icon(self, agent_tool_name):
        """Returns the tool's drawn icon, or its name as a small image for a tool without one."""
        if agent_tool_name not in self.agent_tool_icons_by_name:
            self.agent_tool_icons_by_name[agent_tool_name] = build_text_image(agent_tool_name, self.regular_font)
        return self.agent_tool_icons_by_name[agent_tool_name]

    @objc.python_method
    def measure_text_width(self, text, font):
        return NSAttributedString.alloc().initWithString_attributes_(text, {NSFontAttributeName: font}).size().width

    @objc.python_method
    def measure_right_columns(self):
        """Sets each information column's width to fit its widest text, or 0 when the settings hide
        it or no row has text for it (no context size known yet); the icon column's fits the
        widest tool icon, or is 0 when all agent sessions use the same tool."""
        agent_session_entries = [entry for _, _, entry in iterate_agent_session_entries(self.snapshot)] if self.snapshot else []
        now = time.time()
        all_parts = [build_agent_session_row_text_parts(entry, [], now) for entry in agent_session_entries]

        def measure_column(column_name, texts):
            if column_name not in self.shown_columns:
                return 0
            # Each distinct text is measured once; many sessions share "idle" or "2m".
            widest_text_width = max((self.measure_text_width(text, self.regular_font) for text in set(texts) if text), default=0)
            return TEXT_FIELD_INSETS + widest_text_width if widest_text_width else 0
        self.context_column_width = measure_column("context_tokens", (parts.context_text for parts in all_parts))
        self.created_time_column_width = measure_column("created_time", (parts.created_time_text for parts in all_parts))
        self.existence_duration_column_width = measure_column(
            "existence_duration", (parts.existence_duration_text for parts in all_parts))
        self.state_column_width = measure_column("state", (parts.state_text for parts in all_parts))
        self.elapsed_time_column_width = measure_column("state_duration", (parts.elapsed_time_text for parts in all_parts))
        agent_tool_names = {entry.agent_tool_name for entry in agent_session_entries}
        self.icon_column_width = (max(self.find_agent_tool_icon(name).size().width for name in agent_tool_names)
                                  if len(agent_tool_names) >= 2 else 0)
        if self.width_is_set_by_the_user:
            # A narrow sidebar keeps room for names: the created time and time since created columns go
            # first, then the context column, the icons and the state words (the dot's color
            # still shows the state).
            for column_name in ("created_time_column_width", "existence_duration_column_width", "context_column_width",
                                "icon_column_width", "state_column_width"):
                if self.measure_room_left_for_tab_names() < SMALLEST_TAB_NAME_WIDTH:
                    setattr(self, column_name, 0)

    @objc.python_method
    def measure_room_left_for_tab_names(self):
        """Returns the width left for a tab row's name and details beside the right-hand columns."""
        deepest_tab_prefix = "│  ├─ ● " if self.snapshot is not None and len(self.snapshot.monitors) > 1 else "├─ ● "
        return (self.outline_view.bounds().size.width - ROW_LEFT_MARGIN - GAP_AFTER_PREFIX
                - self.measure_text_width(deepest_tab_prefix, self.regular_font) - GAP_BEFORE_DETAILS
                - self.measure_right_columns_width() - ROW_RIGHT_MARGIN)

    @objc.python_method
    def measure_right_columns_width(self):
        """Returns the width of a tab row's right-hand columns with the gaps between them (0-width columns are hidden)."""
        shown_column_widths = [width for width in (self.context_column_width, self.created_time_column_width,
                                                   self.existence_duration_column_width, self.icon_column_width,
                                                   self.state_column_width, self.elapsed_time_column_width) if width]
        return sum(shown_column_widths) + GAP_BETWEEN_COLUMNS * max(len(shown_column_widths) - 1, 0)

    @objc.python_method
    def choose_name_font(self, item):
        """Window and monitor names, and the tab in front, are bold."""
        is_in_front = item.kind == "tab" and item.entry.is_in_front
        return self.bold_font if (is_in_front or item.kind != "tab") else self.regular_font

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
        widest_row_width = 0
        preview_buttons_width = GAP_BEFORE_DETAILS + 2 * PREVIEW_BUTTON_SIZE + GAP_BETWEEN_PREVIEW_BUTTONS
        for item in self.list_all_items():
            if item.kind == "window":  # expanded, it ends in the preview buttons
                last_child_flags = self.build_is_last_child_at_each_level(item)
                parts_and_button_widths = [(build_window_row_text_parts(item.entry, last_child_flags, is_expanded),
                                            preview_buttons_width if is_expanded else 0) for is_expanded in (True, False)]
            else:
                parts_and_button_widths = [(self.build_row_text_parts(item), 0)]
            for parts, button_width in parts_and_button_widths:
                row_width = (ROW_LEFT_MARGIN + measure_text_width(parts.connector_text + parts.dot_glyph + " ", self.regular_font)
                             + GAP_AFTER_PREFIX + measure_text_width(parts.name_text, self.choose_name_font(item))
                             + NAME_FIELD_PADDING + GAP_BEFORE_DETAILS
                             + measure_text_width(parts.details_text + ROOM_FOR_GROWING_DETAILS, self.regular_font)
                             + 3 * TEXT_FIELD_INSETS + ROW_RIGHT_MARGIN + button_width)
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
        item = self.find_clicked_item()
        self.item_under_first_click = item  # for a double-click, should the rows move before its second click
        self.item_toggled_by_first_click = None
        if item is None or self.peek_in_progress:
            return  # (a tab row held long enough was shown for a moment instead)
        # At once, so it feels instant; a double-click puts the row back (rowDoubleClicked_). A tab
        # opens or closes its preview, the level below it (user request, 2026-10-19); it is shown
        # by a double-click (user request, milestone 2).
        if item.kind == "tab":
            if not item.entry.preview_line:
                return
            self.set_previews_open([item], not self.preview_is_open(item))
        else:
            self.toggle_expansion(item)
        self.item_toggled_by_first_click = item

    # Pressing and holding a tab row shows that tab until the button comes up (user request,
    # milestone 2). The short wait tells a hold from a click.

    @objc.python_method
    def row_pressed(self, row):
        self.cancel_peek_timer()
        self.row_press_in_progress = True
        item = self.outline_view.itemAtRow_(row) if row >= 0 else None
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
        # The row under the first click, not the one under the pointer now: collapsing a window
        # can scroll the list, and a sorted list can reorder its rows (a held snapshot is applied
        # when the first click's button comes up), either of which moves another row under the second click.
        first_click_item, self.item_under_first_click = self.item_under_first_click, None
        toggled_item, self.item_toggled_by_first_click = self.item_toggled_by_first_click, None
        item = first_click_item or self.find_clicked_item()
        if item is None:
            return
        if item is toggled_item and item.kind == "window":  # undoes the first click's toggle
            self.toggle_expansion(item)
        elif item is toggled_item and item.kind == "tab":
            self.set_previews_open([item], not self.preview_is_open(item))
        if item.kind in ("window", "tab"):
            self.show_item(item)

    @objc.python_method
    def toggle_expansion(self, item):
        """Expands or collapses a row at once, without the outline view's slide animation;
        record_expansion_change keeps the record and redraws."""
        NSAnimationContext.beginGrouping()
        NSAnimationContext.currentContext().setDuration_(0)
        if self.outline_view.isItemExpanded_(item):
            self.outline_view.collapseItem_(item)
        else:
            self.outline_view.expandItem_(item)
        NSAnimationContext.endGrouping()
        self.item_whose_expansion_is_changing = None  # in case a change was announced but not made

    @objc.python_method
    def set_all_expanded(self, expanded):
        """Expands or collapses every monitor and window row and opens or closes every tab's
        preview (R8.1; the previews are the level below the tabs)."""
        if expanded:
            self.collapsed_item_keys.clear()
            self.open_preview_item_keys = {key for key, item in self.items_by_key.items() if item.kind == "tab"}
        else:
            self.collapsed_item_keys = {key for key, item in self.items_by_key.items() if item.child_items}
            self.open_preview_item_keys.clear()
        # Row by row rather than through reloadData, which would rebuild every row view.
        NSAnimationContext.beginGrouping()
        NSAnimationContext.currentContext().setDuration_(0)
        self.apply_expansion_state(self.top_level_items)
        NSAnimationContext.endGrouping()
        self.apply_row_heights()
        self.redraw_visible_rows()
        self.on_row_count_changed()

    @objc.python_method
    def set_previews_open(self, tab_items, is_open):
        """Opens or closes these tab rows' previews at once."""
        for tab_item in tab_items:
            if is_open:
                self.open_preview_item_keys.add((tab_item.kind, tab_item.item_id))
            else:
                self.open_preview_item_keys.discard((tab_item.kind, tab_item.item_id))
        self.apply_row_heights()
        self.redraw_visible_rows()
        self.on_row_count_changed()  # the dropdown fits its height again

    @objc.python_method
    def apply_row_heights(self):
        """Has the outline view take each row's current height (measure_row_height) at once,
        without its animation, and records the rows as shown so the next snapshot doesn't reload them."""
        self.row_structure = self.list_row_structure()
        NSAnimationContext.beginGrouping()
        NSAnimationContext.currentContext().setDuration_(0)
        self.outline_view.noteHeightOfRowsWithIndexesChanged_(build_index_set(range(self.outline_view.numberOfRows())))
        NSAnimationContext.endGrouping()

    def openPreviewsButtonClicked_(self, sender):
        self.set_window_previews_open(sender, True)

    def closePreviewsButtonClicked_(self, sender):
        self.set_window_previews_open(sender, False)

    @objc.python_method
    def set_window_previews_open(self, button, is_open):
        """Opens or closes the previews of all tabs in the window whose row holds this button."""
        row = self.outline_view.rowForView_(button)
        window_item = self.outline_view.itemAtRow_(row) if row >= 0 else None
        if window_item is not None and window_item.kind == "window":
            self.set_previews_open(window_item.child_items, is_open)

    # ----- renaming in place -----

    @objc.python_method
    def begin_rename(self, item):
        if self.rename_in_progress:
            # Finish the open rename first (it is submitted for its own row), then find this row again,
            # since finishing can apply a held snapshot.
            self.outline_view.window().makeFirstResponder_(self.outline_view)
        if (item.kind, item.item_id) not in self.items_by_key:
            return
        item = self.items_by_key[(item.kind, item.item_id)]
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
        renamed_item = self.items_by_key.get((item.kind, item.item_id)) if item is not None else None
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
        self.menu_target_item = item
        if item is None:
            # Empty space: start an agent session in a new window (R13.1).
            for agent_tool_name in self.owner.list_agent_tool_names():
                self.add_menu_item(menu, f"New iTerm2 window with {agent_tool_name} session…", "menuNewWindowWithAgentSession:"
                                   ).setRepresentedObject_(agent_tool_name)
            return
        if item.child_items:  # monitor and window rows (tab rows have nothing to expand)
            self.add_menu_item(menu, "Collapse" if self.outline_view.isItemExpanded_(item) else "Expand",
                               "menuToggleExpansion:")
            if item.kind == "monitor":
                return
            menu.addItem_(NSMenuItem.separatorItem())
        if item.kind == "window":
            self.add_menu_item(menu, "Show window", "menuFocus:")
            self.add_menu_item(menu, "Rename", "menuRename:")
            menu.addItem_(NSMenuItem.separatorItem())
            # A new tab in this window, in the folder of its selected tab's agent session (R13).
            for agent_tool_name in self.owner.list_agent_tool_names():
                self.add_menu_item(menu, f"New tab with {agent_tool_name} session…", "menuNewAgentSessionInWindow:"
                                   ).setRepresentedObject_(agent_tool_name)
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
            if not self.owner.find_launch_command(item.entry.agent_tool_name):
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
        if item.kind not in ("tab", "window"):
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
