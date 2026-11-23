"""The Saved sessions window, which the menu bar icon's "Restore
sessions…" opens: the saved sessions (saved_agent_sessions_store) under the windows they were
saved from, to find, sort, restore, rename or delete.

Like the settings window it keeps its frame (saved_sessions_window_frame), Esc closes it, and the
app controller puts it above an always-on-top list window. Its rows can't be dragged. Several
rows can be selected, and a saved window's row stands for all of its saved sessions. A double
click on a name, Return on a selected row, or Rename in a row's right-click menu renames it;
Delete (in the menu, the button, or the Delete key) deletes the selected rows; the Restore button
restores them and closes the window: the tab of the last one comes to the front at once, and the
owner takes those restored out of the list. The owner keeps the saved list and its file.

A search box with its "aA" checkbox, above the column titles, works as the list's does: the rows
show only the saved windows whose name holds its text, with all their sessions, the saved
sessions whose name holds it, and, dimmed, those whose conversation does, which the owner asks
the core for. While a search lists only some of a saved window's sessions, its row stands for
those. A click on a column's title sorts the rows by that column, a second click the other way,
and a third puts them back in the order they were saved in.
"""

import collections
import os
import time

import objc
from AppKit import (NSApp, NSBackingStoreBuffered, NSButton, NSCarriageReturnCharacter, NSColor, NSControlStateValueOff,
                    NSControlStateValueOn, NSDeleteCharacter,
                    NSDeleteFunctionKey, NSEnterCharacter, NSEventTypeKeyDown, NSImage, NSMakeRect, NSMenu, NSOutlineView, NSScrollView,
                    NSSearchField,
                    NSTableColumn, NSTableViewUniformColumnAutoresizingStyle, NSText, NSViewHeightSizable, NSViewMaxYMargin,
                    NSViewMinXMargin, NSViewMinYMargin, NSViewWidthSizable, NSWindow, NSWindowStyleMaskClosable,
                    NSWindowStyleMaskResizable, NSWindowStyleMaskTitled)
from Foundation import NSIndexSet, NSObject

from agent_command_tab.conversation_search_texts import text_contains
from agent_command_tab.saved_agent_sessions_store import find_saved_window_key
from agent_command_tab.settings_store import SMALLEST_WINDOW_SIZES
from agent_command_tab.user_interface.agent_session_outline_view import (ESCAPE_CHARACTER, RENAME_KEY, SEARCH_KEY, SEARCH_KEY_ALONE,
                                                                         WHAT_A_CLICK_ON_A_COLUMN_TITLE_DOES_TOOL_TIPS,
                                                                         build_index_set)
from agent_command_tab.user_interface.control_helpers import is_a_command_press_of_letter
from agent_command_tab.user_interface.popover_container import (CASE_SENSITIVE_CHECKBOX_TITLE, CASE_SENSITIVE_CHECKBOX_TOOL_TIP,
                                                                LARGEST_SEARCH_FIELD_WIDTH, SEARCH_FIELD_PLACEHOLDER)
from agent_command_tab.user_interface.screen_layout import WindowFrameSavingController, place_window_at_saved_frame_or_center
from agent_command_tab.user_interface.search_box_search import SearchBoxSearch
from agent_command_tab.user_interface.settings_window import is_a_plain_press_of

WINDOW_TITLE = "Saved sessions"
# Each column's title, starting width and smallest width; the name column holds the tree and is the
# one to rename in. The columns share the window's width (build_window), and at their smallest
# widths all four fit in the narrowest window (settings_store.SMALLEST_WINDOW_SIZES), the Agent
# column still showing "Claude Code" and the Saved column a whole date and time.
COLUMN_TITLE_STARTING_WIDTH_AND_SMALLEST_WIDTH_BY_COLUMN_NAME = {
    "name": ("Name", 230, 96), "agent_tool_name": ("Agent", 100, 84), "folder": ("Folder", 240, 80),
    "saved_at": ("Saved", 130, 120)}
# The columns whose first click sorts from the smallest (A to Z); the Saved column's first click
# sorts from the latest. A sorted column's title has AppKit's own arrow: up while its values go
# from the smallest at the top.
COLUMN_NAMES_WHOSE_ORDER_STARTS_ASCENDING = {"name", "agent_tool_name", "folder"}
SORT_INDICATOR_IMAGE_NAMES_BY_IS_ASCENDING = {True: "NSAscendingSortIndicator", False: "NSDescendingSortIndicator"}
WINDOW_SIZE = (720, 360)
SEARCH_BAR_HEIGHT = 34  # above the column titles: the search box and its "aA" checkbox
BUTTON_BAR_HEIGHT = 44
SIDE_MARGIN = 12
GAP_BETWEEN_BUTTONS = 8
SEARCH_FIELD_TOOL_TIP = ("Search the saved sessions for an exact keyword or phrase. Show only rows where it occurs inside the "
                         "name or, in dimmer gray font, where it occurs inside the conversation log (prompts, replies, "
                         "commands) (⌘-F or /)")

SavedSessionsSearch = collections.namedtuple("SavedSessionsSearch", ["text", "is_case_sensitive",
                                                                     "conversation_ids_whose_conversations_match"])
SavedSessionsSearch.__doc__ = ("A search the Saved sessions window's rows show: the search box's text (matched as one "
                               "phrase), whether case counts (the \"aA\" checkbox), and the conversation IDs of the saved "
                               "sessions whose conversation holds the text, as the core answered (a frozenset; empty "
                               "until it has).")


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


def name_holds_the_search_text(name, saved_sessions_search):
    """Whether a saved window's or a saved session's name holds the search box's text."""
    return text_contains(name, name.casefold(), saved_sessions_search.text, saved_sessions_search.is_case_sensitive)


def list_saved_sessions_whose_rows_show(saved_window, saved_sessions_search):
    """Returns the saved window's sessions whose rows a search shows: all of them without a text,
    and when the window's name holds the text (as a window the list's search finds shows all its
    tabs), else those whose name or whose conversation holds it."""
    if not saved_sessions_search.text or name_holds_the_search_text(saved_window["name"], saved_sessions_search):
        return list(saved_window["saved_sessions"])
    return [saved_session for saved_session in saved_window["saved_sessions"]
            if name_holds_the_search_text(saved_session["name"], saved_sessions_search)
            or saved_session["conversation_id"] in saved_sessions_search.conversation_ids_whose_conversations_match]


def saved_session_row_matches_only_by_its_conversation(saved_window, saved_session, saved_sessions_search):
    """Whether a search shows the saved session's row only because its conversation holds the
    text, not its name or its window's: the row is dimmed."""
    return (bool(saved_sessions_search.text) and not name_holds_the_search_text(saved_session["name"], saved_sessions_search)
            and not name_holds_the_search_text(saved_window["name"], saved_sessions_search)
            and saved_session["conversation_id"] in saved_sessions_search.conversation_ids_whose_conversations_match)


def choose_what_a_click_on_a_saved_sessions_column_title_does(column_name, sort_column_name, sort_is_reversed):
    """Returns (the column the rows are sorted by after a click on this column's title, or None for
    the order they were saved in, whether in that column's other direction, the title's hover
    text, which says so). A click at a time sorts by the column in its first direction, then the
    other, then not at all, as a click on a column title of the list does.

    Args:
        column_name: The clicked title's column.
        sort_column_name: The column the rows are sorted by now, or None.
        sort_is_reversed: Whether they are sorted in that column's other direction.
    """
    if column_name != sort_column_name:
        return column_name, False, WHAT_A_CLICK_ON_A_COLUMN_TITLE_DOES_TOOL_TIPS["sort"]
    if not sort_is_reversed:
        return column_name, True, WHAT_A_CLICK_ON_A_COLUMN_TITLE_DOES_TOOL_TIPS["reverse"]
    return None, False, WHAT_A_CLICK_ON_A_COLUMN_TITLE_DOES_TOOL_TIPS["stop"]


def rows_are_sorted_ascending(sort_column_name, sort_is_reversed):
    """Whether rows sorted by this column go from its smallest value at the top (A, the earliest time)."""
    return (sort_column_name in COLUMN_NAMES_WHOSE_ORDER_STARTS_ASCENDING) != sort_is_reversed


def list_saved_windows_and_sessions_in_the_order_shown(saved_windows, saved_sessions_search, sort_column_name, sort_is_reversed,
                                                       home_folder):
    """Returns [(saved window, its saved sessions whose rows show)], one per saved window with a
    row, in the order of the rows: the saved windows a search shows
    (list_saved_sessions_whose_rows_show), in the order they were saved in, or sorted by a column.
    Sorted, each window's sessions go by what they show in the column (the Saved column by the
    time itself), ignoring case, and the windows by what their own row shows in it (its name,
    when its latest session was saved), or, in a column where that row is empty, by their first
    session in that order. Rows that show the same keep the order they were saved in.

    Args:
        saved_windows: saved_agent_sessions_store's saved windows.
        saved_sessions_search: The SavedSessionsSearch the rows show.
        sort_column_name: The column the rows are sorted by, or None for the order they were saved in.
        sort_is_reversed: Whether they are sorted in that column's other direction.
        home_folder: The home folder, which the Folder column shows as "~".
    """
    saved_windows_and_sessions = [
        (saved_window, saved_sessions) for saved_window, saved_sessions in (
            (saved_window, list_saved_sessions_whose_rows_show(saved_window, saved_sessions_search)) for saved_window in saved_windows)
        if saved_sessions]
    if sort_column_name is None:
        return saved_windows_and_sessions
    is_descending = not rows_are_sorted_ascending(sort_column_name, sort_is_reversed)

    def find_sort_key_of_session(saved_session):
        if sort_column_name == "saved_at":
            return saved_session["saved_at"]
        return build_saved_row_text("session", None, saved_session, sort_column_name, home_folder).casefold()

    def find_sort_key_of_window(saved_window_and_sorted_sessions):
        saved_window, sorted_saved_sessions = saved_window_and_sorted_sessions
        if sort_column_name == "name":
            return saved_window["name"].casefold()
        if sort_column_name == "saved_at":
            return max(saved_session["saved_at"] for saved_session in saved_window["saved_sessions"])
        return find_sort_key_of_session(sorted_saved_sessions[0])
    return sorted(((saved_window, sorted(saved_sessions, key=find_sort_key_of_session, reverse=is_descending))
                   for saved_window, saved_sessions in saved_windows_and_sessions),
                  key=find_sort_key_of_window, reverse=is_descending)


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
    and Return or Enter renames the selected row, as in Finder (not a
    restore), as do Command-R and Command-Shift-R, as in the list. Command-F and "/" put the
    keyboard in the search box, as in the list."""

    def keyDown_(self, event):
        if is_a_plain_press_of(event, {NSDeleteCharacter, NSDeleteFunctionKey}):
            self.delegate().delete_items(self.delegate().list_selected_items())
        elif (is_a_plain_press_of(event, {NSCarriageReturnCharacter, NSEnterCharacter})
              or is_a_command_press_of_letter(RENAME_KEY, event.charactersIgnoringModifiers(), event.modifierFlags())):
            self.delegate().rename_the_selected_row()
        elif (is_a_plain_press_of(event, {SEARCH_KEY_ALONE})
              or is_a_command_press_of_letter(SEARCH_KEY, event.charactersIgnoringModifiers(), event.modifierFlags())):
            self.delegate().put_the_keyboard_in_the_search_box()
        else:
            objc.super(SavedSessionsOutlineView, self).keyDown_(event)


class SavedSessionsWindow(NSWindow):
    """Esc closes the window as its close button does, except while a name is being renamed,
    where Esc puts the old name back, and in the search box, where it empties the box first
    (SavedSessionsWindowController.control_textView_doCommandBySelector_)."""

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
    rename_saved_session(conversation ID, new name), search_is_case_sensitive_chosen(whether the
    "aA" checkbox is checked), and window_frame_changed("saved_sessions_window_frame", frame) after
    each move and resize. For its search it asks the owner's
    find_saved_agent_sessions_whose_conversations_contain(text, is_case_sensitive, on_answer),
    which answers on_answer(the conversation IDs of the saved sessions whose conversation holds
    the text, or None when the core couldn't answer)."""

    frame_setting_name = "saved_sessions_window_frame"

    def initWithOwner_(self, owner):
        self = objc.super(SavedSessionsWindowController, self).init()
        if self is None:
            return None
        self.owner = owner
        self.window = None
        self.saved_windows = []  # as the owner last listed them (show_saved_windows)
        self.window_items = []
        self.menu_target_items = []
        self.menu_clicked_item = None
        # The search box's text and "aA" choice, and the search the rows show (its shown_search, a
        # SavedSessionsSearch), which follows them once the owner has answered which conversations
        # hold the text, or after half a second with the name matches alone (the first search
        # reads every saved conversation's file), as the list's rows follow its search box.
        self.search_box_search = SearchBoxSearch(
            SavedSessionsSearch,
            lambda text, is_case_sensitive, on_answer: self.owner.find_saved_agent_sessions_whose_conversations_contain(
                text, is_case_sensitive, on_answer),
            lambda saved_sessions_search: self.show_rows())
        # The saved windows whose rows the user closed, as of the last rows that showed no search:
        # a search opens every row it shows, and the rows after it are closed as they were before it.
        self.collapsed_window_keys = set()
        self.rows_show_a_search = False
        # The column the rows are sorted by, or None for the order they were saved in, and whether
        # in its other direction (a click on a column's title); kept while the app runs.
        self.sort_column_name = None
        self.sort_is_reversed = False
        return self

    @objc.python_method
    def show(self, saved_windows, saved_frame):
        """Opens the window listing these saved windows, at its last frame while that is on a
        connected screen, else in the middle of the main screen, with an empty search box and the
        keyboard on its rows, so the arrow keys move among them; brings an open one forward as it
        is. The owner has every change of the saved windows listed in an open one
        (show_saved_windows), and listing them here again would end a rename under way there
        without renaming."""
        window_opens_now = not self.is_open()
        if window_opens_now:
            self.window = self.build_window()
            place_window_at_saved_frame_or_center(self.window, saved_frame)
            self.show_saved_windows(saved_windows)
        NSApp.activateIgnoringOtherApps_(True)
        self.window.makeKeyAndOrderFront_(None)
        if window_opens_now:
            self.window.makeFirstResponder_(self.outline_view)  # not the search box, whatever AppKit would choose

    @objc.python_method
    def show_saved_windows(self, saved_windows):
        """Lists these saved windows (show_rows); closes the window when nothing is saved any more.
        While the search box holds a text, the owner is asked again which conversations hold it:
        a session saved meanwhile may.

        A rename still under way when they come (a session saved or restored meanwhile changed
        the list) is ended first, without renaming, as Esc ends it: listed under the name being
        typed, they would end the rename with the half-typed name kept, and from inside that
        reload the owner would have the list reloaded once more, which AppKit doesn't support.
        When the owner lists them for a name it keeps, that rename has ended already."""
        if self.window is None:
            return
        if self.outline_view.currentEditor() is not None:
            self.end_the_rename_without_renaming()
        if not saved_windows:
            self.close_if_open()
            return
        self.saved_windows = saved_windows
        self.show_rows()
        self.search_box_search.ask_which_conversations_match()

    @objc.python_method
    def show_rows(self):
        """Lists the saved windows and sessions that the shown search shows, in the sort order
        (list_saved_windows_and_sessions_in_the_order_shown), keeping the selection of the rows
        still listed. Each saved window's row is opened to show its sessions unless the user
        closed it (a rename or a delete elsewhere in the list leaves it closed); a search opens
        every row it shows, and selects its first saved session's row when no selected row is
        listed, so Restore is ready for it. A rename under way is ended first, without renaming
        (see show_saved_windows)."""
        if self.outline_view.currentEditor() is not None:
            self.end_the_rename_without_renaming()
        selected_keys = {item.key for item in self.list_selected_items()}
        if not self.rows_show_a_search:
            self.collapsed_window_keys = {window_item.key for window_item in self.window_items
                                          if not self.outline_view.isItemExpanded_(window_item)}
        self.window_items = [
            make_saved_item("window", saved_window, child_items=[
                make_saved_item("session", saved_window, saved_session) for saved_session in saved_sessions])
            for saved_window, saved_sessions in list_saved_windows_and_sessions_in_the_order_shown(
                self.saved_windows, self.search_box_search.shown_search, self.sort_column_name, self.sort_is_reversed,
                os.path.expanduser("~"))]
        # The Agent column only while the saved sessions are of two or more tools.
        agent_tool_names = {saved_session["agent_tool_name"] for saved_window in self.saved_windows
                            for saved_session in saved_window["saved_sessions"]}
        self.outline_view.tableColumnWithIdentifier_("agent_tool_name").setHidden_(len(agent_tool_names) < 2)
        self.outline_view.reloadData()
        if not self.columns_were_fitted_to_the_window:
            # Together with the Agent column, the columns start wider than the window does, and
            # AppKit keeps the columns as wide as the window only from when they fit it.
            self.outline_view.sizeToFit()
            self.columns_were_fitted_to_the_window = True
        self.rows_show_a_search = bool(self.search_box_search.shown_search.text)
        for window_item in self.window_items:
            if self.rows_show_a_search or window_item.key not in self.collapsed_window_keys:
                self.outline_view.expandItem_(window_item)
        self.outline_view.selectRowIndexes_byExtendingSelection_(build_index_set(
            row for row in range(self.outline_view.numberOfRows()) if self.outline_view.itemAtRow_(row).key in selected_keys), False)
        if self.rows_show_a_search and not self.list_selected_items():
            self.move_the_selection_among_session_rows(1)  # the first one
        self.update_buttons()

    @objc.python_method
    def end_the_rename_without_renaming(self):
        """Puts back the name of the row being renamed and gives the rows the keyboard."""
        self.outline_view.abortEditing()
        self.window.makeFirstResponder_(self.outline_view)

    @objc.python_method
    def list_selected_items(self):
        """Returns the items of the selected rows, top to bottom."""
        if self.window is None:
            return []
        return [self.outline_view.itemAtRow_(row) for row in self.outline_view.selectedRowIndexes()]

    @objc.python_method
    def split_items_into_window_keys_and_conversation_ids(self, items):
        """Returns (the keys of the saved windows, the conversation IDs of the saved sessions) that
        these rows stand for: a saved session's row for itself, and a saved window's row for all
        of its sessions, by the window's key, or, while a search lists only some of them, for
        those listed, by their conversation IDs."""
        window_keys, conversation_ids = set(), set()
        for item in items:
            if item.kind == "session":
                conversation_ids.add(item.saved_session["conversation_id"])
            elif len(item.child_items) == len(item.saved_window["saved_sessions"]):
                window_keys.add(find_saved_window_key(item.saved_window))
            else:
                conversation_ids.update(child_item.saved_session["conversation_id"] for child_item in item.child_items)
        return window_keys, conversation_ids

    @objc.python_method
    def restore_selected_rows(self):
        """Restores the selected rows' saved sessions and closes the window, so the restored tabs show.
        A name being renamed is kept first, so the restore gives it to the tab. The rows to restore
        are taken before that: the rows listed again for the kept name may no longer hold the
        renamed row (a search its new name doesn't match), and then have another row selected."""
        window_keys, conversation_ids = self.split_items_into_window_keys_and_conversation_ids(self.list_selected_items())
        self.window.makeFirstResponder_(None)
        if not window_keys and not conversation_ids:
            return
        self.owner.restore_saved_agent_sessions(window_keys, conversation_ids)
        self.close_if_open()

    @objc.python_method
    def delete_items(self, items):
        """Has the owner delete these rows' saved windows and sessions. A name being renamed is kept
        first, as for a restore: the list without the deleted rows would otherwise end the rename
        without renaming (show_saved_windows)."""
        if items:
            self.window.makeFirstResponder_(self.outline_view)
            self.owner.delete_saved_items(*self.split_items_into_window_keys_and_conversation_ids(items))

    @objc.python_method
    def update_buttons(self):
        has_selection = bool(self.list_selected_items())
        self.restore_button.setEnabled_(has_selection)
        self.delete_button.setEnabled_(has_selection)

    # ----- the search -----

    @objc.python_method
    def show_search_is_case_sensitive(self, search_is_case_sensitive):
        """Checks or unchecks the "aA" checkbox and has the search count case, or not: one choice
        shared with the search boxes of the lists, which the owner keeps."""
        if self.window is not None:
            self.case_sensitive_checkbox.setState_(NSControlStateValueOn if search_is_case_sensitive else NSControlStateValueOff)
        self.search_box_search.set_is_case_sensitive(search_is_case_sensitive)

    @objc.python_method
    def put_the_keyboard_in_the_search_box(self):
        """Command-F or "/" in the rows: puts the keyboard in the search box, its text selected."""
        self.window.makeFirstResponder_(self.search_field)

    @objc.python_method
    def move_the_selection_among_session_rows(self, step):
        """Selects the saved session's row step rows below the selected row (above for a negative
        step), among the listed saved sessions' rows, and scrolls to it: Up and Down in the
        search box. With no such row selected, Down selects the first and Up the last; at either
        end the selection stays."""
        session_rows = [row for row in range(self.outline_view.numberOfRows()) if self.outline_view.itemAtRow_(row).kind == "session"]
        if not session_rows:
            return
        selected_row = self.outline_view.selectedRow()
        if selected_row in session_rows:
            row = session_rows[min(max(session_rows.index(selected_row) + step, 0), len(session_rows) - 1)]
        else:
            row = session_rows[0 if step > 0 else -1]
        self.outline_view.selectRowIndexes_byExtendingSelection_(NSIndexSet.indexSetWithIndex_(row), False)
        self.outline_view.scrollRowToVisible_(row)

    def controlTextDidChange_(self, notification):
        """Filters the rows as each letter is typed in the search box (show_the_search_box_text)."""
        if notification.object() is self.search_field:
            self.show_the_search_box_text()

    def searchFieldChanged_(self, sender):
        """The search box's own action: also sent when its button empties it, which typing doesn't do."""
        self.show_the_search_box_text()

    @objc.python_method
    def show_the_search_box_text(self):
        """Has the rows show only the saved windows whose name holds the search box's text, with
        all their sessions, the saved sessions whose name holds it, and, dimmed, those whose
        conversation does (every row for an empty box), once the owner has answered which
        conversations hold it (SearchBoxSearch)."""
        self.search_box_search.set_text(self.search_field.stringValue())

    def caseSensitiveCheckboxClicked_(self, sender):
        self.owner.search_is_case_sensitive_chosen(sender.state() == NSControlStateValueOn)

    # ----- the sort order -----

    def outlineView_didClickTableColumn_(self, outline_view, table_column):
        """A click on a column's title sorts the rows by it, a second click the other way, and a
        third puts them back in the order they were saved in."""
        self.sort_column_name, self.sort_is_reversed, _ = choose_what_a_click_on_a_saved_sessions_column_title_does(
            table_column.identifier(), self.sort_column_name, self.sort_is_reversed)
        self.show_the_sort_order_in_the_column_titles()
        self.show_rows()

    @objc.python_method
    def show_the_sort_order_in_the_column_titles(self):
        """Gives the title of the column the rows are sorted by its arrow, and each title its
        hover text, which says what the next click on it does."""
        for table_column in self.outline_view.tableColumns():
            column_name = table_column.identifier()
            _, _, tool_tip = choose_what_a_click_on_a_saved_sessions_column_title_does(
                column_name, self.sort_column_name, self.sort_is_reversed)
            table_column.setHeaderToolTip_(tool_tip)
            self.outline_view.setIndicatorImage_inTableColumn_(
                NSImage.imageNamed_(SORT_INDICATOR_IMAGE_NAMES_BY_IS_ASCENDING[
                    rows_are_sorted_ascending(column_name, self.sort_is_reversed)]) if column_name == self.sort_column_name else None,
                table_column)

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
        # At the top, above the column titles: the search box, then its "aA" checkbox. A window
        # opened again starts with an empty box, and its rows with no search.
        self.search_box_search.start_over()
        self.rows_show_a_search = False
        self.search_field = NSSearchField.alloc().initWithFrame_(NSMakeRect(0, 0, LARGEST_SEARCH_FIELD_WIDTH, 22))
        self.search_field.setPlaceholderString_(SEARCH_FIELD_PLACEHOLDER)
        self.search_field.setToolTip_(SEARCH_FIELD_TOOL_TIP)
        self.search_field.setDelegate_(self)  # controlTextDidChange_ and the keys (control_textView_doCommandBySelector_)
        self.search_field.setTarget_(self)  # its button that empties it
        self.search_field.setAction_("searchFieldChanged:")
        self.case_sensitive_checkbox = NSButton.checkboxWithTitle_target_action_(
            CASE_SENSITIVE_CHECKBOX_TITLE, self, "caseSensitiveCheckboxClicked:")
        self.case_sensitive_checkbox.setToolTip_(CASE_SENSITIVE_CHECKBOX_TOOL_TIP)
        self.case_sensitive_checkbox.setState_(
            NSControlStateValueOn if self.search_box_search.is_case_sensitive else NSControlStateValueOff)
        self.case_sensitive_checkbox.sizeToFit()
        search_bar_bottom = height - SEARCH_BAR_HEIGHT
        left = SIDE_MARGIN
        for control, control_width in ((self.search_field, LARGEST_SEARCH_FIELD_WIDTH),
                                       (self.case_sensitive_checkbox, self.case_sensitive_checkbox.frame().size.width)):
            control_height = control.fittingSize().height
            control.setFrame_(NSMakeRect(left, search_bar_bottom + round((SEARCH_BAR_HEIGHT - control_height) / 2), control_width,
                                         control_height))
            control.setAutoresizingMask_(NSViewMinYMargin)  # stays at the top left
            window.contentView().addSubview_(control)
            left += control_width + GAP_BETWEEN_BUTTONS
        scroll_view = NSScrollView.alloc().initWithFrame_(
            NSMakeRect(0, BUTTON_BAR_HEIGHT, width, search_bar_bottom - BUTTON_BAR_HEIGHT))
        scroll_view.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
        scroll_view.setHasVerticalScroller_(True)
        # Sideways too, for columns the window has no room for even at their smallest widths (a
        # column dragged wider, or a scroller that takes room).
        scroll_view.setHasHorizontalScroller_(True)
        scroll_view.setAutohidesScrollers_(True)
        outline_view = SavedSessionsOutlineView.alloc().initWithFrame_(scroll_view.bounds())
        # Every column gets narrower with the window, down to its smallest width, and wider with
        # it: AppKit's own choice squeezes the last column alone, until the Saved times are gone.
        # The Name column stays as wide as that leaves it when a row is opened: AppKit by itself
        # widens it for the opened rows, which pushes the last column out of the window.
        outline_view.setColumnAutoresizingStyle_(NSTableViewUniformColumnAutoresizingStyle)
        outline_view.setAutoresizesOutlineColumn_(False)
        for column_name, (title, starting_width, smallest_width) in COLUMN_TITLE_STARTING_WIDTH_AND_SMALLEST_WIDTH_BY_COLUMN_NAME.items():
            column = NSTableColumn.alloc().initWithIdentifier_(column_name)
            column.setTitle_(title)
            column.setWidth_(starting_width)
            column.setMinWidth_(smallest_width)
            column.setEditable_(column_name == "name")
            outline_view.addTableColumn_(column)
        outline_view.setOutlineTableColumn_(outline_view.tableColumns()[0])
        outline_view.setAllowsMultipleSelection_(True)
        # No drag: the data source writes no rows to a pasteboard.
        outline_view.setDataSource_(self)
        outline_view.setDelegate_(self)
        menu = NSMenu.alloc().initWithTitle_("")
        menu.setDelegate_(self)  # menuNeedsUpdate_ builds it for the row clicked
        outline_view.setMenu_(menu)
        scroll_view.setDocumentView_(outline_view)
        window.contentView().addSubview_(scroll_view)
        self.outline_view = outline_view
        # A window opened before left its rows: the new outline view has listed none of them, and
        # would take each for one the user closed (show_rows).
        self.window_items = []
        self.columns_were_fitted_to_the_window = False
        self.show_the_sort_order_in_the_column_titles()  # as the window opened before left it
        self.restore_button = self.add_button(window, "Restore", "restoreClicked:", width - SIDE_MARGIN)
        self.delete_button = self.add_button(window, "Delete", "deleteClicked:",
                                             self.restore_button.frame().origin.x - GAP_BETWEEN_BUTTONS)
        self.restore_button.setToolTip_("Resumes the selected rows' sessions and brings the last one's tab to the front; "
                                         "a window's row stands for all of its sessions that are listed")
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
            self.search_box_search.stop_waiting_for_the_answer()

    def control_textView_doCommandBySelector_(self, control, text_view, command_selector):
        """The search box's keys: Return gives the rows the keyboard, Up and Down move the
        selection among the saved sessions' rows, and Esc empties the box, and in an empty box
        closes the window as Esc in the rows does. In a name being renamed, Esc ends the rename
        without renaming. Other keys edit the text."""
        if control is not self.search_field:
            if command_selector == "cancelOperation:":
                self.end_the_rename_without_renaming()
                return True
            return False
        if command_selector == "insertNewline:":
            self.window.makeFirstResponder_(self.outline_view)
            return True
        if command_selector in ("moveUp:", "moveDown:"):
            self.move_the_selection_among_session_rows(-1 if command_selector == "moveUp:" else 1)
            return True
        if command_selector == "cancelOperation:":
            if self.search_field.stringValue():
                self.search_field.setStringValue_("")
                self.show_the_search_box_text()
            else:
                self.window.performClose_(None)
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

    def outlineView_willDisplayCell_forTableColumn_item_(self, outline_view, cell, table_column, item):
        """Dims the row of a saved session that the search shows only because its conversation
        holds the text. Not while the row is selected: a selected row's text is drawn in the
        selection's own color, which the cell does only with its usual text color."""
        row_is_dimmed = (item.kind == "session"
                         and saved_session_row_matches_only_by_its_conversation(item.saved_window, item.saved_session,
                                                                                self.search_box_search.shown_search)
                         and not outline_view.isRowSelected_(outline_view.rowForItem_(item)))
        cell.setTextColor_(NSColor.secondaryLabelColor() if row_is_dimmed else NSColor.controlTextColor())

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
        deletes_one_session = len(self.menu_target_items) == 1 and self.menu_target_items[0].kind == "session"
        for title, action_name, tool_tip in (
                ("Rename", "menuRename:", f"Renames this saved {self.menu_clicked_item.kind}"),
                ("Delete", "menuDelete:", "Deletes this saved session from the list; its conversation file stays on disk"
                 if deletes_one_session else "Deletes these saved sessions from the list; their conversation files stay on disk")):
            menu_item = menu.addItemWithTitle_action_keyEquivalent_(title, action_name, "")
            menu_item.setTarget_(self)
            menu_item.setToolTip_(tool_tip)

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
        """Selects the item's row alone and puts its name into editing, wherever a header drag moved the Name column.

        A rename under way ends first, its name kept. The row is looked up after that, and by the
        item's key: the owner lists the saved windows again for a name it keeps, and may have
        listed others since the item was taken from a row (while its right-click menu was open),
        so the item itself is no longer a row's, and the row number it had may be another's.
        Nothing is renamed when no row has its key any more."""
        self.window.makeFirstResponder_(self.outline_view)
        row = next((row for row in range(self.outline_view.numberOfRows())
                    if self.outline_view.itemAtRow_(row).key == item.key), None)
        if row is None:
            return
        self.outline_view.selectRowIndexes_byExtendingSelection_(NSIndexSet.indexSetWithIndex_(row), False)
        self.outline_view.editColumn_row_withEvent_select_(self.outline_view.columnWithIdentifier_("name"), row, None, True)

    def menuDelete_(self, sender):
        self.delete_items(self.menu_target_items)
