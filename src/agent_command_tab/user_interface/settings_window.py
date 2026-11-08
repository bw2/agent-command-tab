"""The settings window. Every change applies at once and is saved (see settings_store)."""

import collections
import threading

import objc
from AppKit import (NSApp, NSBackingStoreBuffered, NSBezierPath, NSBox, NSBoxCustom, NSBoxSeparator, NSButton,
                    NSColor, NSControlSizeMini, NSControlSizeSmall, NSEventModifierFlagCommand, NSEventModifierFlagControl,
                    NSDownArrowFunctionKey, NSEventModifierFlagOption, NSEventTypeKeyDown, NSFont, NSInsetRect, NSMakeRect, NSNoTitle,
                    NSPopUpButton, NSScreen, NSScrollView, NSSwitch, NSText, NSTextField, NSUpArrowFunctionKey, NSView,
                    NSViewMinXMargin, NSViewWidthSizable,
                    NSWindow, NSWindowOcclusionStateVisible, NSWindowStyleMaskClosable,
                    NSWindowStyleMaskResizable, NSWindowStyleMaskTitled, NSWorkspace)
from Foundation import NSURL
from PyObjCTools import AppHelper

from agent_command_tab.settings_store import (LIST_COLUMNS, SMALLEST_WINDOW_SIZES, find_launch_command_problem,
                                              is_valid_setting_value, replace_agent_tool_field)
from agent_command_tab.start_with_iterm2 import START_WITH_ITERM2_SETTING_TITLE
from agent_command_tab.user_interface.agent_session_outline_view import COLUMN_TITLES, ESCAPE_CHARACTER
from agent_command_tab.user_interface.control_helpers import add_popup_choices, read_popup_choice, select_popup_choice
from agent_command_tab.user_interface.named_colors import make_light_and_dark_mode_color_maker
from agent_command_tab.user_interface.screen_layout import (WindowFrameSavingController,
                                                            place_window_at_saved_frame_or_center,
                                                            read_monitors_from_screens)
from agent_command_tab.user_interface.sidebar_placement import (describe_sidebar_placement_fallback,
                                                                find_outer_screen_edges)

WINDOW_WIDTH = SMALLEST_WINDOW_SIZES["settings_window_frame"][0]  # the rows' layout width; the window only gets wider
# The look of macOS's System Settings: each section's rows in a rounded box under its title, each row's label at the left and its control at the right, thin lines between rows.
SIDE_MARGIN = 20  # from the window's sides to the boxes
TOP_MARGIN = 16  # above the first section's title
SECTION_TITLE_HEIGHT = 18
GAP_BELOW_SECTION_TITLE = 6
GAP_BETWEEN_SECTIONS = 20  # from a box, or the note under it, to the next section's title
# Below the last box, down to the window's bottom edge (27 below the last control).
ROOM_BELOW_THE_LAST_SECTION = 20
ROW_HEIGHT = 36
ROW_PADDING = 10  # from a box's sides to the labels and controls in it
INDENT_OF_A_ROW_THAT_CONTINUES_THE_ONE_ABOVE = 16  # a harness's command, under the harness's name
GAP_BEFORE_UNITS = 6  # between a control and its units ("points")
BOX_CORNER_RADIUS = 8
MINI_SWITCH_SIZE = (26, 15)  # what a mini NSSwitch draws (checked on macOS 27)
# The keys that work the focused control: Space and Return turn a switch
# on or off and press a button; they and the up and down arrows open a pop-up's menu.
KEYS_THAT_PRESS_A_CONTROL = frozenset({" ", "\r"})
KEYS_THAT_OPEN_A_POP_UP = KEYS_THAT_PRESS_A_CONTROL | {NSUpArrowFunctionKey, NSDownArrowFunctionKey}
NOTE_HEIGHT = 30  # the sidebar's placement note under its box: two lines of small text
LAUNCH_COMMAND_FIELD_WIDTH = 360  # a longer command scrolls inside the field
# A box's fill and border: a little darker than the window in light mode, a little lighter in dark mode.
make_section_box_fill_color = make_light_and_dark_mode_color_maker(
    lambda: NSColor.colorWithSRGBRed_green_blue_alpha_(0, 0, 0, 0.03), lambda: NSColor.colorWithSRGBRed_green_blue_alpha_(1, 1, 1, 0.04))
make_section_box_border_color = make_light_and_dark_mode_color_maker(
    lambda: NSColor.colorWithSRGBRed_green_blue_alpha_(0, 0, 0, 0.08), lambda: NSColor.colorWithSRGBRed_green_blue_alpha_(1, 1, 1, 0.08))
# The titles of the choices in settings_store (SHOW_AS_CHOICES, GLOBAL_SHORTCUT_CHOICES,
# APPEARANCE_CHOICES, SIDEBAR_MODES, SIDEBAR_EDGES), and the columns of LIST_COLUMNS in the
# settings' order; setting_choice_titles_tests checks that each table names every choice.
SHOW_AS_TITLES = {"popup": "Popup", "sidebar": "Sidebar", "window": "Window"}
GLOBAL_SHORTCUT_TITLES = {"shift_command_tab": "Shift-Command-Tab", "command_fn_tab": "Command-Fn-Tab", "off": "Off"}
APPEARANCE_TITLES = {"auto": "Auto", "dark": "Dark", "light": "Light"}
# System Settings > Privacy & Security > Accessibility, which the global shortcut's key filter needs.
ACCESSIBILITY_SETTINGS_URL = "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"
SIDEBAR_MODE_TITLES = {
    "auto_hide": "Auto-hide",
    "floating": "Always: Above other windows",
    "docked": "Always: Resize iTerm2 windows",
}
SIDEBAR_EDGE_TITLES = {"right": "Right", "left": "Left"}
# The tab rows' information columns, in the order the Columns section lists them (memory before
# CPU, unlike LIST_COLUMNS). Each is labeled with the title over its column in the list (COLUMN_TITLES).
LIST_COLUMN_NAMES_IN_SETTINGS_ORDER = ["state", "context_tokens", "created_time", "existence_duration",
                                       "memory_usage", "cpu_usage"]
# One row in a section's box: its label at the left, its control at the right, and the units after the
# control. A row that continues the one above it (a harness's command; the accessibility button under
# the shortcut) has no line above it, and its label is indented.
SettingsRow = collections.namedtuple("SettingsRow", ["label", "control", "units", "continues_the_row_above"],
                                     defaults=[None, False])
# A titled section of the window: its rows in a rounded box under its title,
# and, for the sidebar, a note in small text under the box, which takes no room while it is empty.
SettingsSection = collections.namedtuple("SettingsSection", ["title", "rows", "note_field"], defaults=[None])


class FlippedView(NSView):
    """A view whose y grows downward, so rows laid out from its top stay at the top of a scroll view."""

    def isFlipped(self):
        return True


class LaunchCommandField(NSTextField):
    """A launch command's field, outlined in red while its command can't be run
    (show_launch_command_problem). Drawn here rather than as its layer's border, whose color is a
    CGColor, which PyObjC without its Quartz package passes only as an untyped pointer, with a
    warning each time."""

    shows_a_problem = False

    def drawRect_(self, rect):
        objc.super(LaunchCommandField, self).drawRect_(rect)
        if self.shows_a_problem:
            NSColor.systemRedColor().setStroke()
            outline = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(NSInsetRect(self.bounds(), 1, 1), 3, 3)
            outline.setLineWidth_(2)
            outline.stroke()


def is_a_plain_press_of(event, keys):
    """Whether a key press is one of keys (characters, as charactersIgnoringModifiers gives them),
    without Command, Option or Control, which other shortcuts use."""
    return (event.charactersIgnoringModifiers() in keys
            and not event.modifierFlags() & (NSEventModifierFlagCommand | NSEventModifierFlagOption | NSEventModifierFlagControl))


def find_the_control_that_has_the_keyboard(window):
    """Returns the control that has a window's keyboard (a text field being typed in rather than
    the field editor that takes its keys), or None."""
    responder = window.firstResponder()
    if isinstance(responder, NSText) and responder.isFieldEditor():
        return responder.delegate()
    return responder if isinstance(responder, NSView) else None


def can_be_reached_with_tab(control):
    """Whether a settings control is in the order Tab and Shift-Tab go through: shown and enabled.
    AppKit leaves switches, pop-ups and buttons out of it unless System Settings > Keyboard >
    Keyboard navigation is on, so the settings window's own controls put themselves in."""
    return bool(control.isEnabled()) and not control.isHiddenOrHasHiddenAncestor()


class KeyboardSwitch(NSSwitch):
    """A switch that Tab and Shift-Tab reach, and that Space or Return turns on or off."""

    def canBecomeKeyView(self):
        return can_be_reached_with_tab(self)

    def keyDown_(self, event):
        if is_a_plain_press_of(event, KEYS_THAT_PRESS_A_CONTROL):
            self.setState_(0 if self.state() else 1)
            self.sendAction_to_(self.action(), self.target())
        else:
            objc.super(KeyboardSwitch, self).keyDown_(event)


class KeyboardPopUpButton(NSPopUpButton):
    """A pop-up that Tab and Shift-Tab reach, and whose menu Space, Return or an up or down arrow
    opens; the arrows and Return then choose in it, as in any menu."""

    def canBecomeKeyView(self):
        return can_be_reached_with_tab(self)

    def keyDown_(self, event):
        if is_a_plain_press_of(event, KEYS_THAT_OPEN_A_POP_UP):
            self.performClick_(None)
        else:
            objc.super(KeyboardPopUpButton, self).keyDown_(event)


class KeyboardButton(NSButton):
    """A button that Tab and Shift-Tab reach, and that Space or Return presses."""

    def canBecomeKeyView(self):
        return can_be_reached_with_tab(self)

    def keyDown_(self, event):
        if is_a_plain_press_of(event, KEYS_THAT_PRESS_A_CONTROL):
            self.performClick_(None)
        else:
            objc.super(KeyboardButton, self).keyDown_(event)


class SettingsWindow(NSWindow):
    """The settings window: Esc closes it as its close button does,
    also while a field is being typed in, whose text field would otherwise take Esc as a request
    for completions. Closing saves that field (SettingsWindowController.windowWillClose_). A
    control that Tab or Shift-Tab reaches scrolls into view when the rows are taller than the window."""

    def sendEvent_(self, event):
        if event.type() == NSEventTypeKeyDown and is_a_plain_press_of(event, {ESCAPE_CHARACTER}):
            self.performClose_(None)
            return
        objc.super(SettingsWindow, self).sendEvent_(event)

    def makeFirstResponder_(self, responder):
        made = objc.super(SettingsWindow, self).makeFirstResponder_(responder)
        if made and isinstance(responder, NSView):
            responder.scrollRectToVisible_(responder.bounds())
        return made


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
        """Opens the window showing these settings, at its last frame while that is on a connected
        screen, else in the middle of the main screen, as tall as its rows; brings an open one
        forward as it is, which every settings change reaches already (it was closed and built
        again, losing its scroll position and an edit under way)."""
        if self.is_open():
            NSApp.activateIgnoringOtherApps_(True)
            self.window.makeKeyAndOrderFront_(None)
            return
        self.settings = dict(settings)
        self.window = self.build_window()
        place_window_at_saved_frame_or_center(self.window, settings["settings_window_frame"])
        # The saved height can be from other sections, shown for another "Show as", or from older margins.
        self.fit_window_height_to_rows()
        NSApp.activateIgnoringOtherApps_(True)
        self.window.makeKeyAndOrderFront_(None)

    def windowDidResize_(self, notification):
        # A scroll view doesn't widen what it holds, so the rows follow the window's width here
        # (the boxes, the lines between rows and the sidebar's placement note then widen, and the
        # controls move with the boxes' right ends, through their autoresizing).
        self.rows_view.setFrameSize_((self.window.contentView().contentSize().width, self.rows_view.frame().size.height))

    def windowWillClose_(self, notification):
        if notification.object() is self.window:
            # However it closes: a launch command or width being typed is saved as its field stops editing.
            self.window.makeFirstResponder_(None)
            AppHelper.callAfter(self.owner.settings_window_closed)  # once it is no longer visible

    def windowDidChangeOcclusionState_(self, notification):
        """Closes the window once no part of it shows: other windows cover it, or it is on another
        desktop, or the screen is locked. Each setting is saved as it
        changes, and a field being typed in as the window closes (windowWillClose_)."""
        window = notification.object()
        if window is self.window and window.isVisible() and not window.occlusionState() & NSWindowOcclusionStateVisible:
            window.close()

    # ----- building -----

    @objc.python_method
    def build_window(self):
        # A pop-up, as the keyboard shortcut's is.
        self.show_as_popup = self.make_popup(SHOW_AS_TITLES, self.settings["show_as"])
        self.global_shortcut_popup = self.make_popup(GLOBAL_SHORTCUT_TITLES, self.settings["global_shortcut"])
        self.global_shortcut_popup.setToolTip_("Shows AgentCommandTab, and hides it when pressed again.")
        # Shown, in a row of its own under the shortcut, only while macOS refuses the shortcut's key filter.
        self.accessibility_settings_button = KeyboardButton.buttonWithTitle_target_action_(
            "Needs Accessibility: Open System Settings…", self, "openAccessibilitySettingsClicked:")
        self.accessibility_settings_button.cell().setShowsFirstResponder_(True)  # its focus ring, also without Keyboard navigation
        self.accessibility_settings_button.setControlSize_(NSControlSizeSmall)
        self.accessibility_settings_button.setFont_(NSFont.systemFontOfSize_(NSFont.smallSystemFontSize()))
        self.accessibility_settings_button.sizeToFit()
        self.accessibility_settings_button.setToolTip_(
            "The shortcut sees key presses before the app switcher does, which needs Accessibility permissions. "
            "Turn it on for the app that starts AgentCommandTab (iTerm2, when you start it from iTerm2), then click "
            "the menu bar icon")
        self.update_accessibility_settings_button()
        self.appearance_popup = self.make_popup(APPEARANCE_TITLES, self.settings["appearance"])
        self.appearance_popup.setToolTip_("Auto follows the Light or Dark setting in System Settings > Appearance")
        self.start_with_iterm2_switch = self.make_switch(self.settings["app_starts_with_iterm2"])
        self.start_with_iterm2_switch.setToolTip_("Starts AgentCommandTab whenever iTerm2 starts, through a script in "
                                                  "iTerm2's AutoLaunch folder, so it has iTerm2's permissions")
        self.sidebar_mode_popup = self.make_popup(SIDEBAR_MODE_TITLES, self.settings["sidebar_mode"])
        self.edge_popup = self.make_popup(SIDEBAR_EDGE_TITLES, self.settings["sidebar_edge"])
        self.sidebar_width_field = self.make_text_field(str(int(self.settings["sidebar_width"])), 80)
        # Switches, as System Settings has, for the on/off settings.
        self.column_switches = {column_name: self.make_switch(column_name in self.settings["shown_columns"])
                                for column_name in LIST_COLUMN_NAMES_IN_SETTINGS_ORDER}
        self.edge_note = NSTextField.wrappingLabelWithString_("")
        self.edge_note.setFont_(NSFont.systemFontOfSize_(NSFont.smallSystemFontSize()))
        self.edge_note.setTextColor_(NSColor.secondaryLabelColor())
        # A General section first: "Show as", the keyboard shortcut with its accessibility button
        # under it, then Appearance, then whether iTerm2 starts the app.
        general_section = SettingsSection("General", [
            SettingsRow("Show as", self.show_as_popup), SettingsRow("Keyboard shortcut", self.global_shortcut_popup),
            SettingsRow(None, self.accessibility_settings_button, continues_the_row_above=True),
            SettingsRow("Appearance", self.appearance_popup),
            SettingsRow(START_WITH_ITERM2_SETTING_TITLE, self.start_with_iterm2_switch)])
        self.list_window_always_on_top_switch = self.make_switch(self.settings["list_window_is_always_on_top"])
        window_section = SettingsSection("Window", [SettingsRow("Always on top", self.list_window_always_on_top_switch)])
        sidebar_section = SettingsSection("Sidebar", [
            SettingsRow("Show", self.sidebar_mode_popup), SettingsRow("Window edge", self.edge_popup),
            SettingsRow("Width", self.sidebar_width_field, "points")], self.edge_note)
        # The columns in a section of their own, above Harnesses.
        columns_section = SettingsSection("Columns", [SettingsRow(COLUMN_TITLES[column_name], column_switch)
                                                      for column_name, column_switch in self.column_switches.items()])
        # Each agent tool: whether it is used, and below that, indented, its launch command.
        self.include_switches = []
        self.launch_command_fields = []
        harness_rows = []
        for agent_tool in self.settings["agent_tools"]:
            include_switch = self.make_switch(agent_tool["is_included"])
            self.include_switches.append(include_switch)
            # Right-aligned, so the command fields start together.
            launch_command_field = self.make_text_field(agent_tool["launch_command"], LAUNCH_COMMAND_FIELD_WIDTH,
                                                        LaunchCommandField)
            self.launch_command_fields.append(launch_command_field)
            harness_rows += [SettingsRow(agent_tool["agent_tool_name"], include_switch),
                             SettingsRow("Command", launch_command_field, continues_the_row_above=True)]
        # The Window section shows only while "Show as" is Window, and the Sidebar section only while
        # it is Sidebar; the sidebar shown for a few seconds from the menu
        # bar icon's menu's "Show sidebar" still uses the Sidebar section's edge and width. Each
        # section: (the "Show as" choice it shows for, or None for every choice, the section).
        self.sections = [(None, general_section), ("window", window_section), ("sidebar", sidebar_section),
                         (None, columns_section), (None, SettingsSection("Harnesses", harness_rows))]
        self.update_edge_choices()
        self.show_placement_fallback_note(self.settings)

        # Resizable, with the rows in a scroll view, so a window made shorter than its rows scrolls.
        # The rows' view is flipped: rows go from the top down.
        rows_view = FlippedView.alloc().initWithFrame_(NSMakeRect(0, 0, WINDOW_WIDTH, 0))
        self.rows_view = rows_view
        self.lay_out_sections(self.settings["show_as"])
        rows_height = rows_view.frame().size.height
        scroll_view = NSScrollView.alloc().initWithFrame_(NSMakeRect(0, 0, WINDOW_WIDTH, rows_height))
        scroll_view.setHasVerticalScroller_(True)
        scroll_view.setAutohidesScrollers_(True)
        scroll_view.setDrawsBackground_(False)
        scroll_view.setDocumentView_(rows_view)
        window = SettingsWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, WINDOW_WIDTH, rows_height),
            NSWindowStyleMaskTitled | NSWindowStyleMaskClosable | NSWindowStyleMaskResizable, NSBackingStoreBuffered, False)
        window.setTitle_("AgentCommandTab Settings")
        window.setReleasedWhenClosed_(False)
        window.setContentView_(scroll_view)
        # Tab and Shift-Tab go through the controls from the top one, which has the keyboard when
        # the window opens.
        window.recalculateKeyViewLoop()
        window.setInitialFirstResponder_(self.show_as_popup)
        window.setMinSize_(SMALLEST_WINDOW_SIZES["settings_window_frame"])
        window.setDelegate_(self)
        for launch_command_field in self.launch_command_fields:
            self.check_launch_command(launch_command_field)
        return window

    @objc.python_method
    def find_what_the_layout_depends_on(self, show_as):
        """The things that change which rows are laid out: the "Show as" choice (which sections show),
        whether the accessibility button shows, and whether the sidebar's note says something."""
        return show_as, bool(self.accessibility_settings_button.isHidden()), not self.edge_note.stringValue()

    @objc.python_method
    def lay_out_sections(self, show_as):
        """Lays out, from the top of rows_view, every section shown for this "Show as" choice, in
        place of what is there, and makes rows_view as tall as they need."""
        for subview in list(self.rows_view.subviews()):
            subview.removeFromSuperview()
        top = TOP_MARGIN
        for shown_for_show_as, section in self.sections:
            if shown_for_show_as in (None, show_as):
                top = self.add_section(self.rows_view, section, top) + GAP_BETWEEN_SECTIONS
        self.rows_view.setFrameSize_((self.rows_view.frame().size.width,
                                      top - GAP_BETWEEN_SECTIONS + ROOM_BELOW_THE_LAST_SECTION))
        self.layout_depended_on = self.find_what_the_layout_depends_on(show_as)

    @objc.python_method
    def lay_out_sections_again_if_needed(self, show_as):
        """Lays the sections out again, and fits the window's height to them, once a section shows
        or hides, or the accessibility button or the sidebar's note does."""
        if self.find_what_the_layout_depends_on(show_as) == self.layout_depended_on:
            return
        # A width being typed is saved first, as when the window closes: its field would end
        # editing, and save, as it is taken out during the layout.
        control_that_had_the_keyboard = find_the_control_that_has_the_keyboard(self.window)
        self.window.makeFirstResponder_(None)
        self.lay_out_sections(show_as)
        self.window.recalculateKeyViewLoop()  # Tab goes through the fields shown now
        # Taken out of the window with the views it was among; the keyboard goes back to the control
        # that had it, so Tab goes on from there after "Show as" was chosen with it.
        self.window.setInitialFirstResponder_(self.show_as_popup)
        if control_that_had_the_keyboard is not None and control_that_had_the_keyboard.window() is self.window:
            self.window.makeFirstResponder_(control_that_had_the_keyboard)
        self.fit_window_height_to_rows()

    @objc.python_method
    def fit_window_height_to_rows(self):
        """Makes the window as tall as its rows, keeping its top edge and its width, but no taller
        than the room below its top on its screen, where the rows then scroll (over keeping the window's
        height)."""
        frame = self.window.frame()
        top = frame.origin.y + frame.size.height
        height = self.window.frameRectForContentRect_(
            NSMakeRect(0, 0, frame.size.width, self.rows_view.frame().size.height)).size.height
        screen = self.window.screen() or NSScreen.mainScreen()
        if screen is not None:
            height = min(height, top - screen.visibleFrame().origin.y)
        self.window.setFrame_display_(NSMakeRect(frame.origin.x, top - height, frame.size.width, height), True)

    @objc.python_method
    def add_section(self, rows_view, section, top):
        """Adds a section at top (rows_view is flipped): its bold title, its rows in a rounded box
        under it, and its note under the box while the note says something. A row whose control is
        hidden (the accessibility button) takes no room. Returns the bottom of what it added."""
        box_width = rows_view.frame().size.width - 2 * SIDE_MARGIN  # a wider window's too
        title_field = NSTextField.labelWithString_(section.title)
        title_field.setFont_(NSFont.boldSystemFontOfSize_(NSFont.systemFontSize()))
        title_field.setFrame_(NSMakeRect(SIDE_MARGIN, top, title_field.fittingSize().width, SECTION_TITLE_HEIGHT))
        rows_view.addSubview_(title_field)
        rows = [row for row in section.rows if not row.control.isHidden()]
        box_top = top + SECTION_TITLE_HEIGHT + GAP_BELOW_SECTION_TITLE
        box = NSBox.alloc().initWithFrame_(NSMakeRect(SIDE_MARGIN, box_top, box_width, ROW_HEIGHT * len(rows)))
        box.setBoxType_(NSBoxCustom)
        box.setTitlePosition_(NSNoTitle)
        box.setCornerRadius_(BOX_CORNER_RADIUS)
        box.setBorderWidth_(1)
        box.setBorderColor_(make_section_box_border_color())
        box.setFillColor_(make_section_box_fill_color())
        box.setAutoresizingMask_(NSViewWidthSizable)  # reaches the right margin of a wider window
        rows_view.addSubview_(box)
        for index, row in enumerate(rows):
            row_top = box_top + ROW_HEIGHT * index
            if index > 0 and not row.continues_the_row_above:
                line = NSBox.alloc().initWithFrame_(NSMakeRect(SIDE_MARGIN + ROW_PADDING, row_top, box_width - 2 * ROW_PADDING, 1))
                line.setBoxType_(NSBoxSeparator)
                line.setAutoresizingMask_(NSViewWidthSizable)
                rows_view.addSubview_(line)
            self.add_row(rows_view, row, row_top, SIDE_MARGIN + box_width - ROW_PADDING)
        bottom = box_top + ROW_HEIGHT * len(rows)
        if section.note_field is not None and section.note_field.stringValue():
            section.note_field.setFrame_(NSMakeRect(SIDE_MARGIN + ROW_PADDING, bottom + 4, box_width - 2 * ROW_PADDING, NOTE_HEIGHT))
            section.note_field.setAutoresizingMask_(NSViewWidthSizable)
            rows_view.addSubview_(section.note_field)
            bottom += 4 + NOTE_HEIGHT
        return bottom

    @objc.python_method
    def add_row(self, rows_view, row, top, right):
        """Adds a row's label at the left of its box and its control, then its units, ending at
        right, each centered in the row's height. The control and units stay at the box's right
        end as the window widens."""
        if row.units is not None:
            units_field = NSTextField.labelWithString_(row.units)
            units_size = units_field.fittingSize()
            right -= units_size.width
            units_field.setFrame_(NSMakeRect(right, top + round((ROW_HEIGHT - units_size.height) / 2), units_size.width,
                                             units_size.height))
            units_field.setAutoresizingMask_(NSViewMinXMargin)
            rows_view.addSubview_(units_field)
            right -= GAP_BEFORE_UNITS
        # Placed by what it draws (its alignment rect), so pop-ups, switches and fields end at the same x
        # though their frames have different margins around it. The margins are applied here, since
        # frameForAlignmentRect_ puts a control's top margin at its bottom in a flipped view.
        margins = row.control.alignmentRectInsets()
        frame_size = row.control.frame().size
        drawn_width = frame_size.width - margins.left - margins.right
        drawn_top = top + round((ROW_HEIGHT - (frame_size.height - margins.top - margins.bottom)) / 2)
        row.control.setFrameOrigin_((right - drawn_width - margins.left, drawn_top - margins.top))
        row.control.setAutoresizingMask_(NSViewMinXMargin)
        rows_view.addSubview_(row.control)
        if row.label is not None:
            label_field = NSTextField.labelWithString_(row.label)
            label_size = label_field.fittingSize()
            if isinstance(row.control, NSSwitch):
                label_top = top + round((ROW_HEIGHT - label_size.height) / 2)
            else:  # on the same baseline as the control's text
                label_top = drawn_top + row.control.firstBaselineOffsetFromTop() - label_field.firstBaselineOffsetFromTop()
            indent = INDENT_OF_A_ROW_THAT_CONTINUES_THE_ONE_ABOVE if row.continues_the_row_above else 0
            label_field.setFrame_(NSMakeRect(SIDE_MARGIN + ROW_PADDING + indent, label_top, *label_size))
            rows_view.addSubview_(label_field)

    @objc.python_method
    def make_switch(self, is_on):
        switch = KeyboardSwitch.alloc().init()
        switch.setControlSize_(NSControlSizeMini)  # the size System Settings' switches have
        # A switch reports the regular size whatever its control size, and draws a mini one in the
        # middle of that, so its frame is made to fit what it draws: it then lines up at the right.
        switch.setFrame_(switch.frameForAlignmentRect_(NSMakeRect(0, 0, *MINI_SWITCH_SIZE)))
        switch.setState_(1 if is_on else 0)
        switch.setTarget_(self)
        switch.setAction_("controlChanged:")
        return switch

    @objc.python_method
    def make_popup(self, titles_by_value, selected_value):
        """Returns a pop-up of a setting's values, titled from titles_by_value, with selected_value chosen."""
        popup = KeyboardPopUpButton.alloc().initWithFrame_pullsDown_(NSMakeRect(0, 0, 240, 24), False)
        popup.cell().setShowsFirstResponder_(True)  # its focus ring, also without Keyboard navigation
        add_popup_choices(popup, titles_by_value)
        popup.sizeToFit()  # only as wide as its longest item
        select_popup_choice(popup, selected_value)
        popup.setTarget_(self)
        popup.setAction_("controlChanged:")
        return popup

    @objc.python_method
    def make_text_field(self, text, width, text_field_class=NSTextField):
        text_field = text_field_class.alloc().initWithFrame_(NSMakeRect(0, 0, width, 22))
        text_field.setStringValue_(text)
        text_field.setTarget_(self)
        text_field.setAction_("controlChanged:")
        text_field.cell().setSendsActionOnEndEditing_(True)  # also when the field loses focus, not only on Return
        text_field.cell().setScrollable_(True)  # a text longer than the field scrolls on one line rather than wrapping
        return text_field

    # ----- reading the controls -----

    @objc.python_method
    def update_edge_choices(self):
        """Enables only the outer edges of the menu-bar monitor, where the sidebar goes. When
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
        self.lay_out_sections_again_if_needed(self.layout_depended_on[0])  # the note can appear or go

    @objc.python_method
    def show_placement_fallback_note(self, settings):
        """Says where the sidebar is when it can't go where the settings ask."""
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
        elif sender is self.appearance_popup:
            new_settings["appearance"] = read_popup_choice(sender)
        elif sender is self.start_with_iterm2_switch:
            new_settings["app_starts_with_iterm2"] = bool(sender.state())
        elif sender is self.sidebar_mode_popup:
            new_settings["sidebar_mode"] = read_popup_choice(sender)
        elif sender is self.edge_popup:
            new_settings["sidebar_edge"] = read_popup_choice(sender)
        elif sender is self.sidebar_width_field:
            self.read_number_field_into_settings(sender, "sidebar_width", new_settings)
        elif sender is self.list_window_always_on_top_switch:
            new_settings["list_window_is_always_on_top"] = bool(sender.state())
        elif sender in self.column_switches.values():
            new_settings["shown_columns"] = [column_name for column_name in LIST_COLUMNS
                                             if self.column_switches[column_name].state()]
        elif sender in self.launch_command_fields:
            tool_index = self.launch_command_fields.index(sender)
            launch_command = sender.stringValue().strip() or new_settings["agent_tools"][tool_index]["launch_command"]
            sender.setStringValue_(launch_command)
            new_settings["agent_tools"] = replace_agent_tool_field(new_settings["agent_tools"], tool_index, "launch_command",
                                                                   launch_command)
            self.check_launch_command(sender)
        elif sender in self.include_switches:
            new_settings["agent_tools"] = replace_agent_tool_field(
                new_settings["agent_tools"], self.include_switches.index(sender), "is_included", bool(sender.state()))
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
        launch_command_field.shows_a_problem = bool(problem)
        launch_command_field.setNeedsDisplay_(True)
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
        if self.window is None:  # show takes the settings when it builds the window
            return
        # A width typed but not saved yet is kept (a click on a switch doesn't end its editing);
        # the field saves it once its editing ends. A field that only has the focus, as the first
        # text field does when the window opens, shows the new width.
        width_editor = self.sidebar_width_field.currentEditor()
        width_is_being_typed = width_editor is not None and width_editor.string() != str(int(self.settings["sidebar_width"]))
        self.settings = dict(settings)
        select_popup_choice(self.show_as_popup, settings["show_as"])
        self.update_accessibility_settings_button()  # after a shortcut change, or once the permission was given
        self.list_window_always_on_top_switch.setState_(1 if settings["list_window_is_always_on_top"] else 0)
        if not width_is_being_typed:
            self.sidebar_width_field.setStringValue_(str(int(settings["sidebar_width"])))
        self.show_placement_fallback_note(settings)
        self.lay_out_sections_again_if_needed(settings["show_as"])
