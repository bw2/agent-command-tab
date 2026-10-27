"""The settings window (R34, R35). Every change applies at once and is saved.

Settings are added here as the features that use them are built (see settings_store).
"""

import collections
import threading

import objc
from AppKit import (NSApp, NSBackingStoreBuffered, NSBox, NSBoxSeparator, NSButton,
                    NSButtonTypeSwitch, NSColor, NSControlSizeSmall, NSFont, NSMakeRect, NSNormalWindowLevel, NSPopUpButton,
                    NSScrollView, NSTextField, NSView, NSViewWidthSizable, NSWindow, NSWindowOcclusionStateVisible,
                    NSWindowStyleMaskClosable,
                    NSWindowStyleMaskResizable, NSWindowStyleMaskTitled, NSWorkspace)
from Foundation import NSURL
from PyObjCTools import AppHelper

from agent_command_tab.settings_store import (LIST_COLUMNS, SMALLEST_WINDOW_SIZES, find_launch_command_problem,
                                              is_valid_setting_value, replace_agent_tool_field)
from agent_command_tab.user_interface.control_helpers import add_popup_choices, read_popup_choice, select_popup_choice
from agent_command_tab.user_interface.screen_layout import (WindowFrameSavingController,
                                                            place_window_at_saved_frame_or_center,
                                                            read_monitors_from_screens)
from agent_command_tab.user_interface.sidebar_placement import (describe_sidebar_placement_fallback,
                                                                find_outer_screen_edges)

WINDOW_WIDTH = SMALLEST_WINDOW_SIZES["settings_window_frame"][0]  # the rows' layout width; the window only gets wider
SIDE_MARGIN = 12
# The label column. "Window Edge:" starts 47 points from the left edge, half of the 93 it started at
# when the column was 180 points wide (user request, 2026-10-25). The longest label, "Keyboard
# shortcut:" (120 points), leaves 6 points of the 126-point label field, so a narrower column clips it.
LABEL_WIDTH = 134
GAP_BEFORE_CONTROL = 8  # between a right-aligned label and its control
CONTROL_COLUMN_X = SIDE_MARGIN + LABEL_WIDTH  # where the rows' controls start
CONTROL_COLUMN_WIDTH = WINDOW_WIDTH - CONTROL_COLUMN_X - SIDE_MARGIN  # to the right margin
ROW_SPACING = 30
INDENT_UNDER_A_CHECKBOX = 20  # where a checkbox's title starts
LAUNCH_COMMAND_FIELD_WIDTH = 200  # a longer command scrolls inside the field (user request, 2026-10-25)
# The titles of the choices in settings_store (SHOW_AS_CHOICES, GLOBAL_SHORTCUT_CHOICES,
# SIDEBAR_MODES, SIDEBAR_EDGES, LIST_COLUMNS); setting_choice_titles_tests checks that each table
# names every choice.
SHOW_AS_TITLES = {"window": "Window", "sidebar": "Sidebar", "popup": "Popup"}
GLOBAL_SHORTCUT_TITLES = {"shift_command_tab": "Shift-Command-Tab", "command_fn_tab": "Command-Fn-Tab", "off": "Off"}
# System Settings > Privacy & Security > Accessibility, which the global shortcut's key filter needs.
ACCESSIBILITY_SETTINGS_URL = "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"
SIDEBAR_MODE_TITLES = {
    "auto_hide": "Auto-hide",
    "floating": "Always: Above other windows",
    "docked": "Always: Resize iTerm2 windows",
}
SIDEBAR_EDGE_TITLES = {"right": "Right", "left": "Left"}
# The tab rows' information columns, in the order the user listed them (user request, 2026-10-19).
LIST_COLUMN_TITLES = {
    "state": "Status",  # "State" until 2026-10-26 (user request)
    "state_duration": "Time Since Last Status Change",  # "State Duration" until 2026-10-26 (user request)
    # The size and the share of the context window, in one column since 2026-10-26 (user request).
    "context_tokens": "Tokens Used",
    "created_time": "Created Time",
    "existence_duration": "Time Since Created",
    "memory_usage": "Memory Usage",
    "cpu_usage": "CPU Usage",
}
# A titled section of the window, drawn as a bold title and a line to the right edge (user request, 2026-10-19).
SettingsSection = collections.namedtuple("SettingsSection", ["title"])
# The sidebar's placement note: small text across the control column, widening with the window,
# with two lines fitting in its one row. Empty, it is the gap before the next section, as the blank
# row that ends every other section is (user request, 2026-10-26: even gaps between sections).
NoteRow = collections.namedtuple("NoteRow", ["note_field"])


class FlippedView(NSView):
    """A view whose y grows downward, so rows laid out from its top stay at the top of a scroll view."""

    def isFlipped(self):
        return True


class SettingsWindowController(WindowFrameSavingController):
    """Builds the settings window and reports each change to the owner's settings_changed(settings),
    its moves and resizes to window_frame_changed("settings_window_frame", frame), and its closing
    to settings_window_closed()."""

    frame_setting_name = "settings_window_frame"

    def initWithOwner_(self, owner):
        self = objc.super(SettingsWindowController, self).init()
        if self is None:
            return None
        self.owner = owner
        self.settings = None
        self.window = None
        return self

    @objc.python_method
    def show(self, settings):
        """Opens (or brings forward) the window showing these settings, at its last frame while
        that is on a connected screen, else in the middle of the main screen."""
        self.settings = dict(settings)
        if self.window is not None:
            self.window.close()
        self.window = self.build_window()
        place_window_at_saved_frame_or_center(self.window, settings["settings_window_frame"])
        NSApp.activateIgnoringOtherApps_(True)
        self.window.makeKeyAndOrderFront_(None)

    @objc.python_method
    def is_open(self):
        return self.window is not None and bool(self.window.isVisible())

    @objc.python_method
    def set_window_level(self, window_level):
        """Puts the window at this level (above an open dropdown), or back at the normal level for None."""
        if self.window is not None:
            self.window.setLevel_(window_level if window_level is not None else NSNormalWindowLevel)

    def windowDidResize_(self, notification):
        # A scroll view doesn't widen what it holds, so the rows follow the window's width here
        # (the section lines and the sidebar's placement note then widen through their autoresizing).
        self.rows_view.setFrameSize_((self.window.contentView().contentSize().width, self.rows_view.frame().size.height))

    def windowWillClose_(self, notification):
        if notification.object() is self.window:
            # However it closes: a launch command or width being typed is saved as its field stops editing.
            self.window.makeFirstResponder_(None)
            AppHelper.callAfter(self.owner.settings_window_closed)  # once it is no longer visible

    def windowDidChangeOcclusionState_(self, notification):
        """Closes the window once no part of it shows: other windows cover it, or it is on another
        desktop, or the screen is locked (user request, 2026-10-25). Each setting is saved as it
        changes, and a field being typed in as the window closes (windowWillClose_)."""
        window = notification.object()
        if window is self.window and window.isVisible() and not window.occlusionState() & NSWindowOcclusionStateVisible:
            window.close()

    # ----- building -----

    @objc.python_method
    def build_window(self):
        # From top to bottom: (label or None, control or None, units after the control or None), a SettingsSection,
        # or a NoteRow. Every section but the last ends in a blank row (or the note), so the gaps between them match.
        rows = []
        # Shown as a popup or a window, the sidebar still appears for a few seconds from the menu bar icon's "Show the sidebar".
        # A pop-up, as the keyboard shortcut's is (radio buttons until 2026-10-26, user request).
        self.show_as_popup = self.make_popup(SHOW_AS_TITLES, self.settings["show_as"])
        self.global_shortcut_popup = self.make_popup(GLOBAL_SHORTCUT_TITLES, self.settings["global_shortcut"])
        self.global_shortcut_popup.setToolTip_("Shows the list from any app, and hides it when pressed again (a sidebar set to "
                                               "stay visible stays). While the app switcher is open (Command still held "
                                               "after Command-Tab), the keys go to the app switcher")
        # Shown only while macOS refuses the shortcut's key filter; hidden, its row is the gap below the shortcut.
        self.accessibility_settings_button = NSButton.buttonWithTitle_target_action_(
            "Needs Accessibility: Open System Settings…", self, "openAccessibilitySettingsClicked:")
        self.accessibility_settings_button.setControlSize_(NSControlSizeSmall)
        self.accessibility_settings_button.setFont_(NSFont.systemFontOfSize_(NSFont.smallSystemFontSize()))
        self.accessibility_settings_button.sizeToFit()
        self.accessibility_settings_button.setToolTip_(
            "The shortcut sees key presses before the app switcher does, which needs the Accessibility permission. "
            "Turn it on for the app that starts AgentCommandTab (iTerm2, when you start it from iTerm2), then click "
            "the menu bar icon")
        self.update_accessibility_settings_button()
        self.sidebar_mode_popup = self.make_popup(SIDEBAR_MODE_TITLES, self.settings["sidebar_mode"])
        self.edge_popup = self.make_popup(SIDEBAR_EDGE_TITLES, self.settings["sidebar_edge"])
        self.sidebar_width_field = self.make_text_field(str(int(self.settings["sidebar_width"])), 80)
        self.column_checkboxes = {column_name: self.make_checkbox(title, column_name in self.settings["shown_columns"])
                                  for column_name, title in LIST_COLUMN_TITLES.items()}
        self.edge_note = NSTextField.wrappingLabelWithString_("")
        self.edge_note.setFont_(NSFont.systemFontOfSize_(NSFont.smallSystemFontSize()))
        self.edge_note.setTextColor_(NSColor.secondaryLabelColor())
        # A General section first: "Show as", then the keyboard shortcut (user requests, 2026-10-25
        # and 2026-10-26). The accessibility button's row, empty while the shortcut has its
        # permission, is the gap before the next section, as the sidebar's note row is.
        rows += [SettingsSection("General"), ("Show as", self.show_as_popup, None),
                 ("Keyboard shortcut", self.global_shortcut_popup, None), (None, self.accessibility_settings_button, None)]
        self.list_window_always_on_top_checkbox = self.make_checkbox("Always on top", self.settings["list_window_is_always_on_top"])
        rows += [SettingsSection("Window"), (None, self.list_window_always_on_top_checkbox, None), (None, None, None)]
        rows += [SettingsSection("Sidebar"), ("Show", self.sidebar_mode_popup, None), ("Window Edge", self.edge_popup, None),
                 ("Width", self.sidebar_width_field, "points"), NoteRow(self.edge_note)]
        # The columns in a section of their own, above Harnesses (user request, 2026-10-25).
        rows.append(SettingsSection("Display Columns"))
        rows += [(None, checkbox, None) for checkbox in self.column_checkboxes.values()]
        rows.append((None, None, None))
        # Each agent tool: whether it is included, and below that, indented, its launch command (user request, 2026-10-19).
        rows.append(SettingsSection("Harnesses"))
        self.include_checkboxes = []
        self.launch_command_fields = []
        command_labels = [f"{agent_tool['agent_tool_name']} command:" for agent_tool in self.settings["agent_tools"]]
        # The command fields start together, after the longest label (user request, 2026-10-25).
        command_field_x = (INDENT_UNDER_A_CHECKBOX + 6
                           + max(NSTextField.labelWithString_(command_label).fittingSize().width for command_label in command_labels))
        for index, (agent_tool, command_label) in enumerate(zip(self.settings["agent_tools"], command_labels)):
            include_checkbox = self.make_checkbox(agent_tool["agent_tool_name"], agent_tool["is_included"])
            self.include_checkboxes.append(include_checkbox)
            launch_command_field = self.make_text_field(agent_tool["launch_command"], LAUNCH_COMMAND_FIELD_WIDTH)
            self.launch_command_fields.append(launch_command_field)
            rows += [("Use" if index == 0 else None, include_checkbox, None),  # "Include" until 2026-10-25 (user request)
                     (None, self.make_labeled_field_row(command_label, launch_command_field, command_field_x), None)]

        # Resizable, with the rows in a scroll view, so a window made shorter than its rows scrolls
        # (user request, 2026-10-19). The rows' view is flipped: rows go from the top down.
        rows_height = 12 + ROW_SPACING * len(rows) + 12
        rows_view = FlippedView.alloc().initWithFrame_(NSMakeRect(0, 0, WINDOW_WIDTH, rows_height))
        self.rows_view = rows_view
        for index, row in enumerate(rows):
            self.add_row(rows_view, row, 20 + ROW_SPACING * index)  # each control's top
        scroll_view = NSScrollView.alloc().initWithFrame_(NSMakeRect(0, 0, WINDOW_WIDTH, rows_height))
        scroll_view.setHasVerticalScroller_(True)
        scroll_view.setAutohidesScrollers_(True)
        scroll_view.setDrawsBackground_(False)
        scroll_view.setDocumentView_(rows_view)
        window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, WINDOW_WIDTH, rows_height),
            NSWindowStyleMaskTitled | NSWindowStyleMaskClosable | NSWindowStyleMaskResizable, NSBackingStoreBuffered, False)
        window.setTitle_("AgentCommandTab Settings")
        window.setReleasedWhenClosed_(False)
        window.setContentView_(scroll_view)
        window.setMinSize_(SMALLEST_WINDOW_SIZES["settings_window_frame"])
        window.setDelegate_(self)
        self.update_edge_choices()
        self.show_placement_fallback_note(self.settings)
        for launch_command_field in self.launch_command_fields:
            self.check_launch_command(launch_command_field)
        return window

    @objc.python_method
    def add_row(self, rows_view, row, top):
        """Adds one row at top (rows_view is flipped): a label in the label column, the control and
        its units; a section's title; or a note. Text sits 1 point below a control's top, which
        centers it."""
        if isinstance(row, SettingsSection):
            self.add_section_title(rows_view, row.title, top)
            return
        if isinstance(row, NoteRow):
            row.note_field.setFrame_(NSMakeRect(CONTROL_COLUMN_X, top + 2, CONTROL_COLUMN_WIDTH, ROW_SPACING - 2))
            row.note_field.setAutoresizingMask_(NSViewWidthSizable)
            rows_view.addSubview_(row.note_field)
            return
        label, control, units = row
        if label is not None:
            label_field = NSTextField.labelWithString_(label + ":")
            label_field.setAlignment_(2)  # right, in the label column
            label_field.setFrame_(NSMakeRect(SIDE_MARGIN, top + 1, LABEL_WIDTH - GAP_BEFORE_CONTROL, 18))
            rows_view.addSubview_(label_field)
        if control is not None:
            control.setFrameOrigin_((CONTROL_COLUMN_X, top))
            rows_view.addSubview_(control)
        if units is not None:
            units_field = NSTextField.labelWithString_(units)
            units_field.setFrame_(NSMakeRect(CONTROL_COLUMN_X + control.frame().size.width + 6, top + 1, 80, 18))
            rows_view.addSubview_(units_field)

    @objc.python_method
    def add_section_title(self, rows_view, title, top):
        """Draws a section's bold title at the left edge and a thin line from it to the right edge."""
        title_field = NSTextField.labelWithString_(title)
        title_field.setFont_(NSFont.boldSystemFontOfSize_(NSFont.systemFontSize()))
        title_width = title_field.fittingSize().width
        title_field.setFrame_(NSMakeRect(SIDE_MARGIN, top + 1, title_width, 18))
        rows_view.addSubview_(title_field)
        line_x = SIDE_MARGIN + title_width + 8
        line = NSBox.alloc().initWithFrame_(NSMakeRect(line_x, top + 10, WINDOW_WIDTH - SIDE_MARGIN - line_x, 1))
        line.setBoxType_(NSBoxSeparator)
        line.setAutoresizingMask_(NSViewWidthSizable)  # reaches the right edge of a wider window
        rows_view.addSubview_(line)

    @objc.python_method
    def make_labeled_field_row(self, label_text, text_field, field_x):
        """Returns a row control for the control column: a label, indented under the checkbox above
        it, then text_field at field_x from the row's left end."""
        row_view = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, CONTROL_COLUMN_WIDTH, 22))
        label_field = NSTextField.labelWithString_(label_text)
        label_field.setFrame_(NSMakeRect(INDENT_UNDER_A_CHECKBOX, 3, label_field.fittingSize().width, 18))
        row_view.addSubview_(label_field)
        text_field.setFrameOrigin_((field_x, 0))
        row_view.addSubview_(text_field)
        return row_view

    @objc.python_method
    def make_checkbox(self, title, is_checked):
        checkbox = NSButton.alloc().initWithFrame_(NSMakeRect(0, 0, CONTROL_COLUMN_WIDTH, 22))
        checkbox.setButtonType_(NSButtonTypeSwitch)
        checkbox.setTitle_(title)
        checkbox.setState_(1 if is_checked else 0)
        checkbox.setTarget_(self)
        checkbox.setAction_("controlChanged:")
        return checkbox

    @objc.python_method
    def make_popup(self, titles_by_value, selected_value):
        """Returns a pop-up of a setting's values, titled from titles_by_value, with selected_value chosen."""
        popup = NSPopUpButton.alloc().initWithFrame_pullsDown_(NSMakeRect(0, 0, 240, 24), False)
        add_popup_choices(popup, titles_by_value)
        popup.sizeToFit()  # only as wide as its longest item
        select_popup_choice(popup, selected_value)
        popup.setTarget_(self)
        popup.setAction_("controlChanged:")
        return popup

    @objc.python_method
    def make_text_field(self, text, width):
        text_field = NSTextField.alloc().initWithFrame_(NSMakeRect(0, 0, width, 22))
        text_field.setStringValue_(text)
        text_field.setTarget_(self)
        text_field.setAction_("controlChanged:")
        text_field.cell().setSendsActionOnEndEditing_(True)  # also when the field loses focus, not only on Return
        text_field.cell().setScrollable_(True)  # a text longer than the field scrolls on one line rather than wrapping
        return text_field

    # ----- reading the controls -----

    @objc.python_method
    def update_edge_choices(self):
        """Enables only the outer edges of the menu-bar monitor, where the sidebar goes (R5). When
        neither of its edges is outer (it stands between two monitors), both stay enabled: the
        saved side then picks the outer edge of the monitor nearest it (choose_sidebar_edge)."""
        screens = read_monitors_from_screens()
        outer_edges = ({edge for index, edge in find_outer_screen_edges([screen["frame"] for screen in screens]) if index == 0}
                       or {"right", "left"})
        self.edge_popup.setAutoenablesItems_(False)
        for menu_item in self.edge_popup.itemArray():
            menu_item.setEnabled_(menu_item.representedObject() in outer_edges)
        select_popup_choice(self.edge_popup, self.settings["sidebar_edge"])  # back to the saved edge once it is outer again
        if read_popup_choice(self.edge_popup) not in outer_edges:
            select_popup_choice(self.edge_popup, sorted(outer_edges)[-1])  # "right" if it is outer

    @objc.python_method
    def screen_layout_changed(self):
        """Brings an open window's edge choices and placement note up to date after a monitor was
        connected, disconnected or rearranged; does nothing before the window was first opened."""
        if self.window is None:
            return
        self.update_edge_choices()
        self.show_placement_fallback_note(self.settings)

    @objc.python_method
    def show_placement_fallback_note(self, settings):
        """Says where the sidebar is when it can't go where the settings ask (PLAN.md section 8)."""
        self.edge_note.setStringValue_(describe_sidebar_placement_fallback(settings["sidebar_edge"], read_monitors_from_screens()))

    def controlChanged_(self, sender):
        """Changes only the setting that sender controls, on top of the app's current settings, so
        a saved edge that can't be used right now (it borders another monitor) and changes made
        elsewhere (a width dragged at the sidebar's edge) are kept."""
        new_settings = dict(self.owner.settings)
        if sender is self.show_as_popup:
            new_settings["show_as"] = read_popup_choice(sender)
        elif sender is self.global_shortcut_popup:
            new_settings["global_shortcut"] = read_popup_choice(sender)
        elif sender is self.sidebar_mode_popup:
            new_settings["sidebar_mode"] = read_popup_choice(sender)
        elif sender is self.edge_popup:
            new_settings["sidebar_edge"] = read_popup_choice(sender)
        elif sender is self.sidebar_width_field:
            self.read_number_field_into_settings(sender, "sidebar_width", new_settings)
        elif sender is self.list_window_always_on_top_checkbox:
            new_settings["list_window_is_always_on_top"] = bool(sender.state())
        elif sender in self.column_checkboxes.values():
            new_settings["shown_columns"] = [column_name for column_name in LIST_COLUMNS
                                             if self.column_checkboxes[column_name].state()]
        elif sender in self.launch_command_fields:
            tool_index = self.launch_command_fields.index(sender)
            launch_command = sender.stringValue().strip() or new_settings["agent_tools"][tool_index]["launch_command"]
            sender.setStringValue_(launch_command)
            new_settings["agent_tools"] = replace_agent_tool_field(new_settings["agent_tools"], tool_index, "launch_command",
                                                                   launch_command)
            self.check_launch_command(sender)
        elif sender in self.include_checkboxes:
            new_settings["agent_tools"] = replace_agent_tool_field(
                new_settings["agent_tools"], self.include_checkboxes.index(sender), "is_included", bool(sender.state()))
        self.owner.settings_changed(new_settings)

    def openAccessibilitySettingsClicked_(self, sender):
        NSWorkspace.sharedWorkspace().openURL_(NSURL.URLWithString_(ACCESSIBILITY_SETTINGS_URL))

    @objc.python_method
    def update_accessibility_settings_button(self):
        """Shows the button that opens the Accessibility settings only while the owner's global
        shortcut lacks that permission."""
        self.accessibility_settings_button.setHidden_(not self.owner.global_shortcut_lacks_permission())

    @objc.python_method
    def check_launch_command(self, launch_command_field):
        """Checks, off the main thread, that the field's command can be run, then gives the field
        a red border and an explanation if it can't (asking the login shell can take a moment)."""
        launch_command = launch_command_field.stringValue()

        def check():
            problem = find_launch_command_problem(launch_command)
            AppHelper.callAfter(self.show_launch_command_problem, launch_command_field, launch_command, problem)
        threading.Thread(target=check, name="launch-command-check", daemon=True).start()

    @objc.python_method
    def show_launch_command_problem(self, launch_command_field, checked_launch_command, problem):
        if launch_command_field.stringValue() != checked_launch_command:
            return  # edited since; that edit's own check decides
        launch_command_field.setWantsLayer_(True)
        launch_command_field.layer().setBorderColor_(NSColor.systemRedColor().CGColor())
        launch_command_field.layer().setBorderWidth_(2 if problem else 0)
        launch_command_field.layer().setCornerRadius_(3)
        launch_command_field.setToolTip_(problem or None)

    @objc.python_method
    def read_number_field_into_settings(self, text_field, setting_name, new_settings):
        """Stores a whole number typed in text_field, or puts the current value back if it isn't valid."""
        try:
            value = int(text_field.stringValue())
        except ValueError:
            value = None
        if is_valid_setting_value(setting_name, value):
            new_settings[setting_name] = value
        else:
            text_field.setStringValue_(str(int(new_settings[setting_name])))

    @objc.python_method
    def reflect_settings(self, settings):
        """Shows settings changed elsewhere (a width dragged at the sidebar's edge) in an open
        window; does nothing before the window was first opened."""
        self.settings = dict(settings)
        if self.window is None:
            return
        select_popup_choice(self.show_as_popup, settings["show_as"])
        self.update_accessibility_settings_button()  # after a shortcut change, or once the permission was given
        self.list_window_always_on_top_checkbox.setState_(1 if settings["list_window_is_always_on_top"] else 0)
        self.sidebar_width_field.setStringValue_(str(int(settings["sidebar_width"])))
        self.show_placement_fallback_note(settings)
