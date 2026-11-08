"""The Saved sessions window (user request, 2026-11-06), which the menu bar icon's "Restore
sessions…" opens: the saved sessions (saved_agent_sessions_store) under the windows they were
saved from, to restore, rename or delete.

Like the settings window it keeps its frame (saved_sessions_window_frame), Esc closes it, and the
app controller puts it above an always-on-top list window. Its rows can't be dragged. Several
rows can be selected, and a saved window's row stands for all of its saved sessions. A double
click on a name, Return on a selected row, or Rename in a row's right-click menu renames it;
Delete (in the menu, the button, or the Delete key) deletes the selected rows; the Restore button
restores them and closes the window, and the owner takes those restored out of the list. The
owner keeps the saved list and its file.
"""

import os
import time

import objc
from AppKit import (NSApp, NSBackingStoreBuffered, NSButton, NSCarriageReturnCharacter, NSDeleteCharacter,
                    NSDeleteFunctionKey, NSEnterCharacter, NSEventTypeKeyDown, NSMakeRect, NSMenu, NSOutlineView, NSScrollView, NSTableColumn, NSText, NSViewHeightSizable, NSViewMaxYMargin,
                    NSViewMinXMargin, NSViewWidthSizable, NSWindow, NSWindowStyleMaskClosable, NSWindowStyleMaskResizable,
                    NSWindowStyleMaskTitled)
from Foundation import NSIndexSet, NSMutableIndexSet, NSNotFound, NSObject

from agent_command_tab.saved_agent_sessions_store import find_saved_window_key
from agent_command_tab.settings_store import SMALLEST_WINDOW_SIZES
from agent_command_tab.user_interface.agent_session_outline_view import ESCAPE_CHARACTER, RENAME_KEY
from agent_command_tab.user_interface.control_helpers import is_a_command_press_of_letter
from agent_command_tab.user_interface.screen_layout import WindowFrameSavingController, place_window_at_saved_frame_or_center
from agent_command_tab.user_interface.settings_window import is_a_plain_press_of

WINDOW_TITLE = "Saved sessions"  # the user's name for them, 2026-11-06 ("Don't call them checkpoints")
# Each column's title and starting width; the name column holds the tree and is the one to rename in.
COLUMN_TITLE_AND_WIDTH_BY_COLUMN_NAME = {"name": ("Name", 230), "agent_tool_name": ("Agent", 100),
                                         "folder": ("Folder", 240), "saved_at": ("Saved", 130)}
WINDOW_SIZE = (720, 360)
BUTTON_BAR_HEIGHT = 44
SIDE_MARGIN = 12
GAP_BETWEEN_BUTTONS = 8


def format_saved_time(saved_at):
    """Returns when something was saved as "2026-11-06 07:52", in local time."""
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(saved_at))


def shorten_home_folder(folder, home_folder):
    """Returns the folder with the home folder written as "~"."""
    if folder == home_folder or folder.startswith(home_folder + "/"):
        return "~" + folder[len(home_folder):]
    return folder


def build_saved_row_text(kind, saved_window, saved_session, column_name, home_folder):
    """Returns what a row shows in a column: a saved session's name, agent tool, folder and when it
    was saved; a saved window's name and when its latest session was saved."""
    if kind == "window":
        if column_name == "name":
            return saved_window["name"]
        if column_name == "saved_at":
            return format_saved_time(max(saved_session["saved_at"] for saved_session in saved_window["saved_sessions"]))
        return ""
    if column_name == "folder":
        return shorten_home_folder(saved_session["folder"], home_folder)
    if column_name == "saved_at":
        return format_saved_time(saved_session["saved_at"])
    return saved_session[column_name]


class SavedItem(NSObject):
    """One row: a saved window (kind "window"; its saved sessions' rows are child_items) or a saved
    session (kind "session"). key names it across reloads: "window" followed by the saved window's
    find_saved_window_key, or ("session", conversation ID)."""


def make_saved_item(kind, saved_window, saved_session=None, child_items=()):
    item = SavedItem.alloc().init()
    item.kind = kind
    item.saved_window = saved_window
    item.saved_session = saved_session
    item.child_items = list(child_items)
    item.key = ("window", *find_saved_window_key(saved_window)) if kind == "window" else ("session", saved_session["conversation_id"])
    return item


class SavedSessionsOutlineView(NSOutlineView):
    """The tree of saved windows and sessions: Delete or Forward Delete deletes the selected rows,
    and Return or Enter renames the selected row, as in Finder (user request, 2026-11-06: not a
    restore), as do Command-R and Command-Shift-R, as in the list (user request, 2026-11-07)."""

    def keyDown_(self, event):
        if is_a_plain_press_of(event, {NSDeleteCharacter, NSDeleteFunctionKey}):
            self.delegate().delete_items(self.delegate().list_selected_items())
        elif (is_a_plain_press_of(event, {NSCarriageReturnCharacter, NSEnterCharacter})
              or is_a_command_press_of_letter(RENAME_KEY, event.charactersIgnoringModifiers(), event.modifierFlags())):
            self.delegate().rename_the_selected_row()
        else:
            objc.super(SavedSessionsOutlineView, self).keyDown_(event)


class SavedSessionsWindow(NSWindow):
    """Esc closes the window as its close button does, except while a name is being renamed,
    where Esc puts the old name back (SavedSessionsWindowController.control_textView_doCommandBySelector_)."""

    def sendEvent_(self, event):
        if (event.type() == NSEventTypeKeyDown and is_a_plain_press_of(event, {ESCAPE_CHARACTER})
                and not isinstance(self.firstResponder(), NSText)):
            self.performClose_(None)
            return
        objc.super(SavedSessionsWindow, self).sendEvent_(event)


class SavedSessionsWindowController(WindowFrameSavingController):
    """Builds the Saved sessions window and reports what the user does in it to the owner (the app
    controller): restore_saved_agent_sessions(window keys, conversation IDs),
    delete_saved_items(window keys, conversation IDs), rename_saved_window(window key, new name),
    rename_saved_session(conversation ID, new name), and window_frame_changed(
    "saved_sessions_window_frame", frame) after each move and resize."""

    frame_setting_name = "saved_sessions_window_frame"

    def initWithOwner_(self, owner):
        self = objc.super(SavedSessionsWindowController, self).init()
        if self is None:
            return None
        self.owner = owner
        self.window = None
        self.window_items = []
        self.menu_target_items = []
        self.menu_clicked_item = None
        return self

    @objc.python_method
    def show(self, saved_windows, saved_frame):
        """Opens the window listing these saved windows, at its last frame while that is on a
        connected screen, else in the middle of the main screen; brings an open one forward."""
        if not self.is_open():
            self.window = self.build_window()
            place_window_at_saved_frame_or_center(self.window, saved_frame)
        self.show_saved_windows(saved_windows)
        NSApp.activateIgnoringOtherApps_(True)
        self.window.makeKeyAndOrderFront_(None)

    @objc.python_method
    def show_saved_windows(self, saved_windows):
        """Lists these saved windows, each opened to show its sessions, keeping the selection of
        the rows still listed; closes the window when nothing is saved any more."""
        if self.window is None:
            return
        if not saved_windows:
            self.close_if_open()
            return
        selected_keys = {item.key for item in self.list_selected_items()}
        self.window_items = [make_saved_item("window", saved_window, child_items=[
            make_saved_item("session", saved_window, saved_session) for saved_session in saved_window["saved_sessions"]])
            for saved_window in saved_windows]
        # The Agent column only while the saved sessions are of two or more tools (user request, 2026-11-06).
        agent_tool_names = {saved_session["agent_tool_name"] for saved_window in saved_windows
                            for saved_session in saved_window["saved_sessions"]}
        self.outline_view.tableColumnWithIdentifier_("agent_tool_name").setHidden_(len(agent_tool_names) < 2)
        self.outline_view.reloadData()
        self.outline_view.expandItem_expandChildren_(None, True)
        rows_to_select = NSMutableIndexSet.indexSet()
        for row in range(self.outline_view.numberOfRows()):
            if self.outline_view.itemAtRow_(row).key in selected_keys:
                rows_to_select.addIndex_(row)
        self.outline_view.selectRowIndexes_byExtendingSelection_(rows_to_select, False)
        self.update_buttons()

    @objc.python_method
    def list_selected_items(self):
        """Returns the items of the selected rows, top to bottom."""
        if self.window is None:
            return []
        selected_rows = self.outline_view.selectedRowIndexes()
        items = []
        row = selected_rows.firstIndex()
        while row != NSNotFound:
            items.append(self.outline_view.itemAtRow_(row))
            row = selected_rows.indexGreaterThanIndex_(row)
        return items

    @objc.python_method
    def split_items_into_window_keys_and_conversation_ids(self, items):
        return ({find_saved_window_key(item.saved_window) for item in items if item.kind == "window"},
                {item.saved_session["conversation_id"] for item in items if item.kind == "session"})

    @objc.python_method
    def restore_selected_rows(self):
        """Restores the selected rows' saved sessions and closes the window, so the restored tabs show.
        A name being renamed is kept first, so the restore gives it to the tab."""
        self.window.makeFirstResponder_(None)
        selected_items = self.list_selected_items()
        if not selected_items:
            return
        self.owner.restore_saved_agent_sessions(*self.split_items_into_window_keys_and_conversation_ids(selected_items))
        self.close_if_open()

    @objc.python_method
    def delete_items(self, items):
        if items:
            self.owner.delete_saved_items(*self.split_items_into_window_keys_and_conversation_ids(items))

    @objc.python_method
    def update_buttons(self):
        has_selection = bool(self.list_selected_items())
        self.restore_button.setEnabled_(has_selection)
        self.delete_button.setEnabled_(has_selection)

    # ----- building -----

    @objc.python_method
    def build_window(self):
        width, height = WINDOW_SIZE
        window = SavedSessionsWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, width, height), NSWindowStyleMaskTitled | NSWindowStyleMaskClosable | NSWindowStyleMaskResizable,
            NSBackingStoreBuffered, False)
        window.setTitle_(WINDOW_TITLE)
        window.setReleasedWhenClosed_(False)
        window.setMinSize_(SMALLEST_WINDOW_SIZES["saved_sessions_window_frame"])
        window.setDelegate_(self)
        scroll_view = NSScrollView.alloc().initWithFrame_(NSMakeRect(0, BUTTON_BAR_HEIGHT, width, height - BUTTON_BAR_HEIGHT))
        scroll_view.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
        scroll_view.setHasVerticalScroller_(True)
        scroll_view.setAutohidesScrollers_(True)
        outline_view = SavedSessionsOutlineView.alloc().initWithFrame_(scroll_view.bounds())
        for column_name, (title, column_width) in COLUMN_TITLE_AND_WIDTH_BY_COLUMN_NAME.items():
            column = NSTableColumn.alloc().initWithIdentifier_(column_name)
            column.setTitle_(title)
            column.setWidth_(column_width)
            column.setEditable_(column_name == "name")
            outline_view.addTableColumn_(column)
        outline_view.setOutlineTableColumn_(outline_view.tableColumns()[0])
        outline_view.setAllowsMultipleSelection_(True)
        # No drag: the data source writes no rows to a pasteboard (user request, 2026-11-06).
        outline_view.setDataSource_(self)
        outline_view.setDelegate_(self)
        menu = NSMenu.alloc().initWithTitle_("")
        menu.setDelegate_(self)  # menuNeedsUpdate_ builds it for the row clicked
        outline_view.setMenu_(menu)
        scroll_view.setDocumentView_(outline_view)
        window.contentView().addSubview_(scroll_view)
        self.outline_view = outline_view
        self.restore_button = self.add_button(window, "Restore", "restoreClicked:", width - SIDE_MARGIN)
        self.delete_button = self.add_button(window, "Delete", "deleteClicked:",
                                             self.restore_button.frame().origin.x - GAP_BETWEEN_BUTTONS)
        self.restore_button.setToolTip_("Resumes the selected rows' sessions; a window's row stands for all of its sessions")
        window.setInitialFirstResponder_(outline_view)
        return window

    @objc.python_method
    def add_button(self, window, title, action_name, right_edge):
        """Adds a button to the bottom bar, its right edge at right_edge; it stays at the bottom right."""
        button = NSButton.buttonWithTitle_target_action_(title, self, action_name)
        button.sizeToFit()
        button_size = button.frame().size
        button.setFrameOrigin_((right_edge - button_size.width, (BUTTON_BAR_HEIGHT - button_size.height) / 2))
        button.setAutoresizingMask_(NSViewMinXMargin | NSViewMaxYMargin)
        window.contentView().addSubview_(button)
        return button

    def restoreClicked_(self, sender):
        self.restore_selected_rows()

    def deleteClicked_(self, sender):
        self.delete_items(self.list_selected_items())

    def windowWillClose_(self, notification):
        if notification.object() is self.window:
            self.window.makeFirstResponder_(None)  # a name being renamed is kept

    def control_textView_doCommandBySelector_(self, control, text_view, command_selector):
        # Esc ends a rename without renaming. AppKit's calls arrive with the selector as str; direct Python calls may pass bytes.
        if command_selector in ("cancelOperation:", b"cancelOperation:"):
            self.outline_view.abortEditing()
            self.window.makeFirstResponder_(self.outline_view)
            return True
        return False

    # ----- the outline view's data source and delegate -----

    def outlineView_numberOfChildrenOfItem_(self, outline_view, item):
        return len(self.window_items) if item is None else len(item.child_items)

    def outlineView_child_ofItem_(self, outline_view, index, item):
        return (self.window_items if item is None else item.child_items)[index]

    def outlineView_isItemExpandable_(self, outline_view, item):
        return item is not None and item.kind == "window"

    def outlineView_objectValueForTableColumn_byItem_(self, outline_view, table_column, item):
        return build_saved_row_text(item.kind, item.saved_window, item.saved_session, table_column.identifier(),
                                    os.path.expanduser("~"))

    def outlineView_shouldEditTableColumn_item_(self, outline_view, table_column, item):
        return table_column.identifier() == "name"

    def outlineView_setObjectValue_forTableColumn_byItem_(self, outline_view, value, table_column, item):
        """A rename: an empty name, or the same one, changes nothing."""
        if table_column.identifier() != "name":
            return
        new_name = str(value or "").strip()
        current_name = item.saved_window["name"] if item.kind == "window" else item.saved_session["name"]
        if not new_name or new_name == current_name:
            return
        if item.kind == "window":
            self.owner.rename_saved_window(find_saved_window_key(item.saved_window), new_name)
        else:
            self.owner.rename_saved_session(item.saved_session["conversation_id"], new_name)

    def outlineViewSelectionDidChange_(self, notification):
        self.update_buttons()

    # ----- right-click menu -----

    @objc.python_method
    def find_clicked_item(self):
        """Returns the item of the row right-clicked, or None (empty space)."""
        clicked_row = self.outline_view.clickedRow()
        return self.outline_view.itemAtRow_(clicked_row) if clicked_row >= 0 else None

    def menuNeedsUpdate_(self, menu):
        """Rename for the row clicked, and Delete for it, or for every selected row when it is one of them."""
        menu.removeAllItems()
        self.menu_clicked_item = self.find_clicked_item()
        if self.menu_clicked_item is None:
            self.menu_target_items = []
            return
        selected_items = self.list_selected_items()
        self.menu_target_items = (selected_items if any(item is self.menu_clicked_item for item in selected_items)
                                  else [self.menu_clicked_item])
        for title, action_name in (("Rename", "menuRename:"), ("Delete", "menuDelete:")):
            menu.addItemWithTitle_action_keyEquivalent_(title, action_name, "").setTarget_(self)

    def menuRename_(self, sender):
        self.start_renaming(self.menu_clicked_item)

    @objc.python_method
    def rename_the_selected_row(self):
        """Starts renaming the selected row (Return, Command-R or Command-Shift-R), when one row alone is selected, as Finder does."""
        selected_items = self.list_selected_items()
        if len(selected_items) == 1:
            self.start_renaming(selected_items[0])

    @objc.python_method
    def start_renaming(self, item):
        """Selects the item's row alone and puts its name into editing, wherever a header drag moved the Name column."""
        row = self.outline_view.rowForItem_(item)
        if row < 0:
            return
        self.outline_view.selectRowIndexes_byExtendingSelection_(NSIndexSet.indexSetWithIndex_(row), False)
        self.outline_view.editColumn_row_withEvent_select_(self.outline_view.columnWithIdentifier_("name"), row, None, True)

    def menuDelete_(self, sender):
        self.delete_items(self.menu_target_items)
