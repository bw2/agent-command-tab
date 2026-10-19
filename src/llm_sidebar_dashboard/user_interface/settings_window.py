"""The settings window (R34, R35). Every change applies at once and is saved.

Settings are added here as the features that use them are built (see settings_store).
"""

import collections
import threading

import objc
from AppKit import (NSApp, NSBackingStoreBuffered, NSBox, NSBoxSeparator, NSButton, NSButtonTypeRadio,
                    NSButtonTypeSwitch, NSColor, NSFont, NSMakeRect, NSNormalWindowLevel, NSPopUpButton, NSScrollView,
                    NSTextField, NSView, NSViewWidthSizable, NSWindow, NSWindowStyleMaskClosable, NSWindowStyleMaskResizable,
                    NSWindowStyleMaskTitled)
from Foundation import NSObject
from PyObjCTools import AppHelper

from llm_sidebar_dashboard.settings_store import (ICON_COLOR_NAMES, LIST_COLUMNS, SMALLEST_WINDOW_SIZES,
                                                  find_launch_command_problem, is_valid_setting_value)
from llm_sidebar_dashboard.user_interface.control_helpers import add_popup_choices, read_popup_choice, select_popup_choice
from llm_sidebar_dashboard.user_interface.screen_layout import (convert_rect_to_frame_list,
                                                                place_window_at_saved_frame_or_center,
                                                                read_monitors_from_screens)
from llm_sidebar_dashboard.user_interface.sidebar_placement import (describe_sidebar_placement_fallback,
                                                                    find_outer_screen_edges)

WINDOW_WIDTH = SMALLEST_WINDOW_SIZES["settings_window_frame"][0]  # the rows' layout width; the window only gets wider
SIDE_MARGIN = 12
LABEL_WIDTH = 180  # the label column; a longer label starts at the left edge, with its control after it
GAP_BEFORE_CONTROL = 8  # between a right-aligned label and its control
CONTROL_COLUMN_X = SIDE_MARGIN + LABEL_WIDTH  # where the rows' controls start
CONTROL_COLUMN_WIDTH = WINDOW_WIDTH - CONTROL_COLUMN_X - SIDE_MARGIN  # to the right margin
ROW_SPACING = 30
GAP_BETWEEN_RADIO_BUTTONS = 16
INDENT_UNDER_A_CHECKBOX = 20  # where a checkbox's title starts
# The titles of the choices in settings_store (SHOW_AS_CHOICES, SIDEBAR_MODES, SIDEBAR_EDGES,
# LIST_COLUMNS); setting_choice_titles_tests checks that each table names every choice.
SHOW_AS_TITLES = {"window": "Window", "sidebar": "Sidebar", "popup": "Popup"}
SIDEBAR_MODE_TITLES = {
    "auto_hide": "Auto-hide",
    "floating": "Always: Above other windows",
    "docked": "Always: Resize iTerm2 windows",
}
SIDEBAR_EDGE_TITLES = {"right": "Right", "left": "Left"}
# The tab rows' information columns, in the order the user listed them (user request, 2026-10-19).
LIST_COLUMN_TITLES = {
    "state": "State",
    "state_duration": "State Duration",
    "context_tokens": "Used Token Count",
    "created_time": "Created Time",
    "existence_duration": "Time Since Created",
    "memory_usage": "Memory Usage",
    "cpu_usage": "CPU Usage",
}
# A titled section of the window, drawn as a bold title and a line to the right edge (user request, 2026-10-19).
SettingsSection = collections.namedtuple("SettingsSection", ["title"])


class FlippedView(NSView):
    """A view whose y grows downward, so rows laid out from its top stay at the top of a scroll view."""

    def isFlipped(self):
        return True


class SettingsWindowController(NSObject):
    """Builds the settings window and reports each change to the owner's settings_changed(settings),
    its moves and resizes to window_frame_changed("settings_window_frame", frame), and its closing
    to settings_window_closed()."""

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

    @objc.python_method
    def report_frame(self):
        self.owner.window_frame_changed("settings_window_frame", convert_rect_to_frame_list(self.window.frame()))

    def windowDidMove_(self, notification):
        self.report_frame()

    def windowDidEndLiveResize_(self, notification):
        self.report_frame()

    def windowDidResize_(self, notification):
        # A scroll view doesn't widen what it holds, so the rows follow the window's width here
        # (their fields and lines then widen through their autoresizing).
        self.rows_view.setFrameSize_((self.window.contentView().contentSize().width, self.rows_view.frame().size.height))

    def windowWillClose_(self, notification):
        if notification.object() is self.window:
            AppHelper.callAfter(self.owner.settings_window_closed)  # once it is no longer visible

    # ----- building -----

    @objc.python_method
    def build_window(self):
        # From top to bottom: (label or None, control or None, units after the control or None), or a SettingsSection.
        rows = []
        # Shown as a popup or a window, the sidebar still appears for a few seconds from the menu bar icon's "Show the sidebar".
        self.show_as_radio_buttons = [self.make_radio_button(title, show_as == self.settings["show_as"])
                                      for show_as, title in SHOW_AS_TITLES.items()]
        show_as_radio_group = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, 10, 22))  # one group: same view, same action
        x = 0
        for radio_button in self.show_as_radio_buttons:
            radio_button.setFrameOrigin_((x, 0))
            show_as_radio_group.addSubview_(radio_button)
            x += radio_button.frame().size.width + GAP_BETWEEN_RADIO_BUTTONS
        show_as_radio_group.setFrameSize_((x, 22))
        self.sidebar_mode_popup = self.make_popup(SIDEBAR_MODE_TITLES, self.settings["sidebar_mode"])
        self.edge_popup = self.make_popup(SIDEBAR_EDGE_TITLES, self.settings["sidebar_edge"])
        self.sidebar_width_field = self.make_text_field(str(int(self.settings["sidebar_width"])), 80)
        self.icon_color_popup = self.make_popup({color_name: color_name.capitalize() for color_name in ICON_COLOR_NAMES},
                                                self.settings["icon_color"])
        self.icon_color_popup.setToolTip_("Auto: light blue while an agent session is asking, red if one has an error, "
                                          "orange if one is stuck, otherwise green")
        self.asking_count_checkbox = self.make_checkbox(
            "Show number of agents waiting for my answer", self.settings["icon_shows_asking_count"])
        self.stuck_minutes_field = self.make_text_field(str(self.settings["stuck_minutes"]), 80)
        self.column_checkboxes = {column_name: self.make_checkbox(title, column_name in self.settings["shown_columns"])
                                  for column_name, title in LIST_COLUMN_TITLES.items()}
        self.edge_note = NSTextField.wrappingLabelWithString_("")
        self.edge_note.setFont_(NSFont.systemFontOfSize_(NSFont.smallSystemFontSize()))
        self.edge_note.setTextColor_(NSColor.secondaryLabelColor())
        rows += [("Show as", show_as_radio_group, None), (None, None, None),
                 ("Menu icon", self.icon_color_popup, None), (None, self.asking_count_checkbox, None), (None, None, None)]
        rows += [("Columns" if index == 0 else None, checkbox, None) for index, checkbox in enumerate(self.column_checkboxes.values())]
        rows.append((None, None, None))
        self.list_window_always_on_top_checkbox = self.make_checkbox("Always on top", self.settings["list_window_is_always_on_top"])
        rows += [SettingsSection("Window"), (None, self.list_window_always_on_top_checkbox, None), (None, None, None)]
        rows += [SettingsSection("Sidebar"), ("Show", self.sidebar_mode_popup, None), ("Window Edge", self.edge_popup, None),
                 ("Width", self.sidebar_width_field, "points"),
                 (None, self.edge_note, None), (None, None, None)]  # the note may take two lines
        # Each agent tool: whether it is included, and below that, indented, its launch command (user request, 2026-10-19).
        rows.append(SettingsSection("Harnesses"))
        self.include_checkboxes = []
        self.launch_command_fields = []
        for index, agent_tool in enumerate(self.settings["agent_tools"]):
            include_checkbox = self.make_checkbox(agent_tool["agent_tool_name"], agent_tool["is_included"])
            self.include_checkboxes.append(include_checkbox)
            launch_command_field = self.make_text_field(agent_tool["launch_command"], 10)
            self.launch_command_fields.append(launch_command_field)
            rows += [("Include" if index == 0 else None, include_checkbox, None),
                     (None, self.make_labeled_field_row(f"{agent_tool['agent_tool_name']} command:", launch_command_field), None)]
        rows += [(None, None, None), ("Agent is stuck if it thinks for more than", self.stuck_minutes_field, "minutes"),
                 (None, None, None)]

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
        window.setTitle_("LLM Sidebar Dashboard Settings")
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
        """Adds one row at top (rows_view is flipped): a label in the label column (or, when too
        long for it, from the left edge with the control after it), the control and its units;
        or a section's title. Text sits 1 point below a control's top, which centers it."""
        if isinstance(row, SettingsSection):
            self.add_section_title(rows_view, row.title, top)
            return
        label, control, units = row
        control_x = CONTROL_COLUMN_X
        if label is not None:
            label_field = NSTextField.labelWithString_(label + ":")
            label_width = label_field.fittingSize().width
            if label_width <= LABEL_WIDTH - GAP_BEFORE_CONTROL:
                label_field.setAlignment_(2)  # right, in the label column
                label_field.setFrame_(NSMakeRect(SIDE_MARGIN, top + 1, LABEL_WIDTH - GAP_BEFORE_CONTROL, 18))
            else:  # too long for the column: the row reads as one sentence from the left edge
                label_field.setFrame_(NSMakeRect(SIDE_MARGIN, top + 1, label_width, 18))
                control_x = SIDE_MARGIN + label_width + 6
            rows_view.addSubview_(label_field)
        if control is not None:
            control.setFrameOrigin_((control_x, top))
            if control is self.edge_note:  # reaches down into the empty row below it, and widens with the window
                control.setFrame_(NSMakeRect(CONTROL_COLUMN_X, top + 2, CONTROL_COLUMN_WIDTH, ROW_SPACING + 16))
                control.setAutoresizingMask_(NSViewWidthSizable)
            rows_view.addSubview_(control)
        if units is not None:
            units_field = NSTextField.labelWithString_(units)
            units_field.setFrame_(NSMakeRect(control_x + control.frame().size.width + 6, top + 1, 80, 18))
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
    def make_labeled_field_row(self, label_text, text_field):
        """Returns a row control for the control column: a label, then text_field reaching to the
        window's right margin, indented under the checkbox above it."""
        row_width = CONTROL_COLUMN_WIDTH
        row_view = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, row_width, 22))
        row_view.setAutoresizingMask_(NSViewWidthSizable)  # the field widens with the window
        label_field = NSTextField.labelWithString_(label_text)
        label_width = label_field.fittingSize().width
        label_field.setFrame_(NSMakeRect(INDENT_UNDER_A_CHECKBOX, 3, label_width, 18))
        row_view.addSubview_(label_field)
        field_x = INDENT_UNDER_A_CHECKBOX + label_width + 6
        text_field.setFrame_(NSMakeRect(field_x, 0, row_width - field_x, 22))
        text_field.setAutoresizingMask_(NSViewWidthSizable)
        row_view.addSubview_(text_field)
        return row_view

    @objc.python_method
    def make_radio_button(self, title, is_on):
        radio_button = NSButton.alloc().initWithFrame_(NSMakeRect(0, 0, 100, 22))
        radio_button.setButtonType_(NSButtonTypeRadio)
        radio_button.setTitle_(title)
        radio_button.setState_(1 if is_on else 0)
        radio_button.setTarget_(self)
        radio_button.setAction_("controlChanged:")
        radio_button.sizeToFit()
        return radio_button

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
        if read_popup_choice(self.edge_popup) not in outer_edges:
            select_popup_choice(self.edge_popup, sorted(outer_edges)[-1])  # "right" if it is outer

    @objc.python_method
    def show_placement_fallback_note(self, settings):
        """Says where the sidebar is when it can't go where the settings ask (PLAN.md section 8)."""
        self.edge_note.setStringValue_(describe_sidebar_placement_fallback(settings["sidebar_edge"], read_monitors_from_screens()))

    def controlChanged_(self, sender):
        """Changes only the setting that sender controls, on top of the app's current settings, so
        a saved edge that can't be used right now (it borders another monitor) and changes made
        elsewhere (a width dragged at the sidebar's edge) are kept."""
        new_settings = dict(self.owner.settings)
        if sender in self.show_as_radio_buttons:
            new_settings["show_as"] = list(SHOW_AS_TITLES)[self.show_as_radio_buttons.index(sender)]
        elif sender is self.sidebar_mode_popup:
            new_settings["sidebar_mode"] = read_popup_choice(sender)
        elif sender is self.edge_popup:
            new_settings["sidebar_edge"] = read_popup_choice(sender)
        elif sender is self.sidebar_width_field:
            self.read_number_field_into_settings(sender, "sidebar_width", new_settings)
        elif sender is self.stuck_minutes_field:
            self.read_number_field_into_settings(sender, "stuck_minutes", new_settings)
        elif sender is self.icon_color_popup:
            new_settings["icon_color"] = read_popup_choice(sender)
        elif sender is self.list_window_always_on_top_checkbox:
            new_settings["list_window_is_always_on_top"] = bool(sender.state())
        elif sender is self.asking_count_checkbox:
            new_settings["icon_shows_asking_count"] = bool(sender.state())
        elif sender in self.column_checkboxes.values():
            new_settings["shown_columns"] = [column_name for column_name in LIST_COLUMNS
                                             if self.column_checkboxes[column_name].state()]
        elif sender in self.launch_command_fields:
            tool_index = self.launch_command_fields.index(sender)
            agent_tools = [dict(agent_tool) for agent_tool in new_settings["agent_tools"]]
            launch_command = sender.stringValue().strip() or agent_tools[tool_index]["launch_command"]
            sender.setStringValue_(launch_command)
            agent_tools[tool_index]["launch_command"] = launch_command
            new_settings["agent_tools"] = agent_tools
            self.check_launch_command(sender)
        elif sender in self.include_checkboxes:
            tool_index = self.include_checkboxes.index(sender)
            agent_tools = [dict(agent_tool) for agent_tool in new_settings["agent_tools"]]
            agent_tools[tool_index]["is_included"] = bool(sender.state())
            new_settings["agent_tools"] = agent_tools
        self.owner.settings_changed(new_settings)

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
        for show_as, radio_button in zip(SHOW_AS_TITLES, self.show_as_radio_buttons):
            radio_button.setState_(1 if show_as == settings["show_as"] else 0)
        self.list_window_always_on_top_checkbox.setState_(1 if settings["list_window_is_always_on_top"] else 0)
        self.sidebar_width_field.setStringValue_(str(int(settings["sidebar_width"])))
        self.show_placement_fallback_note(settings)
