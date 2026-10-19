"""The settings window (R34, R35). Every change applies at once and is saved.

Settings are added here as the features that use them are built (see settings_store).
"""

import threading

import objc
from AppKit import (NSApp, NSBackingStoreBuffered, NSButton, NSButtonTypeSwitch, NSColor, NSFont, NSMakeRect,
                    NSPopUpButton, NSTextField, NSWindow, NSWindowStyleMaskClosable, NSWindowStyleMaskTitled)
from Foundation import NSObject
from PyObjCTools import AppHelper

from llm_sidebar_dashboard.settings_store import (ICON_COLOR_NAMES, LIST_COLUMNS, find_launch_command_problem,
                                                  is_valid_setting_value)
from llm_sidebar_dashboard.user_interface.screen_layout import read_monitors_from_screens
from llm_sidebar_dashboard.user_interface.sidebar_placement import (describe_sidebar_placement_fallback,
                                                                    find_outer_screen_edges)

WINDOW_WIDTH = 540
LABEL_WIDTH = 180  # fits "Claude Code command:"
ROW_SPACING = 30
SIDEBAR_MODE_TITLES = {
    "auto_hide": "Auto-hide",
    "floating": "Always: Above other windows",
    "docked": "Always: Resize iTerm2 windows",
}
# The tab rows' information columns, in the order the user listed them (user request, 2026-10-19).
LIST_COLUMN_TITLES = {
    "state": "State",
    "state_duration": "State Duration",
    "context_tokens": "Used Token Count",
    "created_time": "Created Time",
    "existence_duration": "Existence Duration",
}


class SettingsWindowController(NSObject):
    """Builds the settings window and reports each change to the owner's settings_changed(settings)."""

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
        """Opens (or brings forward) the window showing these settings."""
        self.settings = dict(settings)
        if self.window is not None:
            self.window.close()
        self.window = self.build_window()
        self.window.center()
        NSApp.activateIgnoringOtherApps_(True)
        self.window.makeKeyAndOrderFront_(None)

    # ----- building -----

    @objc.python_method
    def build_window(self):
        rows = []  # (label or None, control or None, units after the control or None) from top to bottom
        # Turned off, the sidebar still appears for a few seconds from the menu bar icon's "Show the sidebar".
        self.sidebar_is_on_checkbox = self.make_checkbox("Enable the sidebar at the edge of the screen",
                                                         self.settings["sidebar_is_on"])
        self.sidebar_mode_popup = self.make_popup(list(SIDEBAR_MODE_TITLES.values()), SIDEBAR_MODE_TITLES[self.settings["sidebar_mode"]])
        self.edge_popup =self.make_popup(["right", "left"], self.settings["sidebar_edge"])
        self.sidebar_width_field = self.make_text_field(str(int(self.settings["sidebar_width"])), 80)
        self.icon_color_popup = self.make_popup([color_name.capitalize() for color_name in ICON_COLOR_NAMES],
                                                self.settings["icon_color"].capitalize())
        self.icon_color_popup.setToolTip_("Auto: light blue while an agent session is asking, red if one has an error, "
                                          "orange if one is stuck, otherwise green")
        self.asking_count_checkbox = self.make_checkbox(
            "Show number of agents waiting for my answer", self.settings["icon_shows_asking_count"])
        self.stuck_minutes_field = self.make_text_field(str(self.settings["stuck_minutes"]), 80)
        self.column_checkboxes = {column_name: self.make_checkbox(title, column_name in self.settings["shown_columns"])
                                  for column_name, title in LIST_COLUMN_TITLES.items()}
        rows += [("Sidebar", self.sidebar_is_on_checkbox, None), ("Show", self.sidebar_mode_popup, None),
                 ("Edge", self.edge_popup, None),
                 ("Width", self.sidebar_width_field, "points"), (None, None, None),
                 ("Menu icon", self.icon_color_popup, None), (None, self.asking_count_checkbox, None), (None, None, None)]
        rows += [("Columns" if index == 0 else None, checkbox, None) for index, checkbox in enumerate(self.column_checkboxes.values())]
        rows += [(None, None, None), ("Agent is stuck if it thinks for more than", self.stuck_minutes_field, "minutes"),
                 (None, None, None)]
        self.launch_command_fields = []
        for agent_tool in self.settings["agent_tools"]:
            launch_command_field = self.make_text_field(agent_tool["launch_command"], WINDOW_WIDTH - LABEL_WIDTH - 40)
            self.launch_command_fields.append(launch_command_field)
            rows.append((f"{agent_tool['agent_tool_name']} command", launch_command_field, None))
        self.edge_note = NSTextField.wrappingLabelWithString_("")
        self.edge_note.setFont_(NSFont.systemFontOfSize_(NSFont.smallSystemFontSize()))
        self.edge_note.setTextColor_(NSColor.secondaryLabelColor())
        rows += [(None, self.edge_note, None), (None, None, None)]  # the note may take two lines

        height = 24 + ROW_SPACING * len(rows)
        window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, WINDOW_WIDTH, height), NSWindowStyleMaskTitled | NSWindowStyleMaskClosable,
            NSBackingStoreBuffered, False)
        window.setTitle_("LLM Sidebar Dashboard Settings")
        window.setReleasedWhenClosed_(False)
        content_view = window.contentView()
        y = height - 12 - ROW_SPACING
        for label, control, units in rows:
            control_x = LABEL_WIDTH + 12
            if label is not None:
                label_field = NSTextField.labelWithString_(label + ":")
                label_width = label_field.fittingSize().width
                if label_width <= LABEL_WIDTH - 8:
                    label_field.setAlignment_(2)  # right, in the label column
                    label_field.setFrame_(NSMakeRect(12, y + 3, LABEL_WIDTH - 8, 18))
                else:  # too long for the column: the row reads as one sentence from the left edge
                    label_field.setFrame_(NSMakeRect(12, y + 3, label_width, 18))
                    control_x = 12 + label_width + 6
                content_view.addSubview_(label_field)
            if control is not None:
                control.setFrameOrigin_((control_x, y))
                if control is self.edge_note:  # reaches down into the empty row below it
                    control.setFrame_(NSMakeRect(LABEL_WIDTH + 12, y - ROW_SPACING + 4, WINDOW_WIDTH - LABEL_WIDTH - 24,
                                                 ROW_SPACING + 16))
                content_view.addSubview_(control)
            if units is not None:
                units_field = NSTextField.labelWithString_(units)
                units_field.setFrame_(NSMakeRect(control_x + control.frame().size.width + 6, y + 3, 80, 18))
                content_view.addSubview_(units_field)
            y -= ROW_SPACING
        self.update_edge_choices()
        self.show_placement_fallback_note(self.settings)
        for launch_command_field in self.launch_command_fields:
            self.check_launch_command(launch_command_field)
        return window

    @objc.python_method
    def make_checkbox(self, title, is_checked):
        checkbox = NSButton.alloc().initWithFrame_(NSMakeRect(0, 0, WINDOW_WIDTH - LABEL_WIDTH - 24, 22))
        checkbox.setButtonType_(NSButtonTypeSwitch)
        checkbox.setTitle_(title)
        checkbox.setState_(1 if is_checked else 0)
        checkbox.setTarget_(self)
        checkbox.setAction_("controlChanged:")
        return checkbox

    @objc.python_method
    def make_popup(self, titles, selected_title):
        popup = NSPopUpButton.alloc().initWithFrame_pullsDown_(NSMakeRect(0, 0, 240, 24), False)
        popup.addItemsWithTitles_(titles)
        popup.sizeToFit()  # only as wide as its longest item
        if selected_title in titles:
            popup.selectItemWithTitle_(selected_title)
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
            menu_item.setEnabled_(menu_item.title() in outer_edges)
        if self.edge_popup.titleOfSelectedItem() not in outer_edges:
            self.edge_popup.selectItemWithTitle_(sorted(outer_edges)[-1])  # "right" if it is outer

    @objc.python_method
    def show_placement_fallback_note(self, settings):
        """Says where the sidebar is when it can't go where the settings ask (PLAN.md section 8)."""
        self.edge_note.setStringValue_(describe_sidebar_placement_fallback(settings["sidebar_edge"], read_monitors_from_screens()))

    def controlChanged_(self, sender):
        """Changes only the setting that sender controls, on top of the app's current settings, so
        a saved edge that can't be used right now (it borders another monitor) and changes made
        elsewhere (a width dragged at the sidebar's edge) are kept."""
        new_settings = dict(self.owner.settings)
        if sender is self.sidebar_is_on_checkbox:
            new_settings["sidebar_is_on"] = bool(sender.state())
        elif sender is self.sidebar_mode_popup:
            selected_mode_title = sender.titleOfSelectedItem()
            new_settings["sidebar_mode"] = next(mode for mode, title in SIDEBAR_MODE_TITLES.items() if title == selected_mode_title)
        elif sender is self.edge_popup:
            new_settings["sidebar_edge"] = sender.titleOfSelectedItem()
        elif sender is self.sidebar_width_field:
            self.read_number_field_into_settings(sender, "sidebar_width", new_settings)
        elif sender is self.stuck_minutes_field:
            self.read_number_field_into_settings(sender, "stuck_minutes", new_settings)
        elif sender is self.icon_color_popup:
            new_settings["icon_color"] = ICON_COLOR_NAMES[sender.indexOfSelectedItem()]
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
        self.sidebar_is_on_checkbox.setState_(1 if settings["sidebar_is_on"] else 0)
        self.sidebar_width_field.setStringValue_(str(int(settings["sidebar_width"])))
        self.show_placement_fallback_note(settings)
