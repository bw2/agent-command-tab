"""The panel around the list: the sort order, Settings, Hide Idle and expand or collapse all at the top, then the column titles; pause or resume all at the bottom right, and a note while the app's hooks are missing at the bottom left.

The same panel class is used in the dropdown (an NSPopover under the menu bar item), in the
sidebar and in the list window, each with its own list controller.
"""

import math

import objc
from AppKit import (NSBezelStyleRecessed, NSColor, NSControlSizeSmall, NSEdgeInsetsMake, NSEvent, NSFont, NSFontWeightRegular,
                    NSImage, NSImageLeading, NSImageSymbolConfiguration,
                    NSLineBreakByTruncatingTail, NSMakeRect, NSPopover, NSPopoverBehaviorTransient, NSPopUpButton, NSRectFill, NSScreen,
                    NSScrollView, NSTextField, NSView, NSViewController, NSViewHeightSizable, NSViewMaxYMargin,
                    NSViewMinXMargin, NSViewMinYMargin, NSViewWidthSizable)

from agent_command_tab.hooks_installer import HOOKS_ARE_MISSING, HOOKS_AWAIT_APPROVAL
from agent_command_tab.session_status_computation import split_agent_sessions_into_pausable_and_resumable
from agent_command_tab.user_interface.control_helpers import (ROWS_OPEN_SYMBOL_NAMES, add_popup_choices,
                                                              read_popup_choice, select_popup_choice,
                                                              show_symbol_on_button)
from agent_command_tab.user_interface.named_colors import appearance_is_dark
from agent_command_tab.user_interface.pointing_hand_button import PointingHandButton

# The Sort pop-up's titles for settings_store.LIST_SORT_ORDERS.
LIST_SORT_ORDER_TITLES = {"manual": "Manual", "activity": "Activity", "alphabet": "Alphabetically", "context_percent": "Tokens",
                          "created_time": "Created Time", "memory_usage": "Memory Usage",
                          "cpu_usage": "CPU Usage", "state_duration": "Status Change"}
POPOVER_WIDTH = 460  # before the first fit
SMALLEST_POPOVER_WIDTH = 320
LARGEST_SHARE_OF_SCREEN_WIDTH = 0.9
SPACE_FOR_SCROLLER_AND_MARGINS = 24
# Its controls sit 3 points above its bottom; the 3 points more than the 26 it had put about as
# much room above them as below them, down to the column titles (user request, 2026-10-27).
TOP_BAR_HEIGHT = 29
BOTTOM_BAR_HEIGHT = 28
SMALLEST_LIST_HEIGHT = 60
LIST_BOTTOM_PADDING = 6
SPACE_FOR_POPOVER_ARROW_AND_MARGIN = 40
INITIAL_PANEL_HEIGHT = 300
TOP_BAR_DRAG_DISTANCE_TO_DETACH = 6  # points: a shorter drag is a click
PANEL_SIDE_MARGIN = 6  # between the panel's edges and the expand or collapse, the Settings and the pause or resume buttons
EXPAND_OR_COLLAPSE_ALL_BUTTON_SIZE = (24, 22)
# The top bar's one expand or collapse button (user requests, 2026-10-19): (SF Symbol, text
# fallback, tooltip), by whether the list is fully expanded. The symbol shows the current state;
# the fallback text and the tooltip say what a click does.
EXPAND_OR_COLLAPSE_ALL_LOOKS = {False: (ROWS_OPEN_SYMBOL_NAMES[False], "Expand all", "Expand all"),
                                True: (ROWS_OPEN_SYMBOL_NAMES[True], "Collapse all", "Collapse all")}
# The bottom bar's pause or resume button, at the right (user requests, 2026-10-20 and
# 2026-10-21): (SF Symbol, text fallback, tooltip), by what a click does: pause the agent
# sessions that run, or, once all are paused, resume them.
PAUSE_OR_RESUME_ALL_LOOKS = {"pause": ("pause.circle", "Pause all", "Pause all agent sessions"),
                             "resume": ("play.circle", "Resume all", "Resume all paused agent sessions")}
# The top bar's Hide Idle / Show Idle button, left of the expand or collapse button (user
# requests, 2026-10-21): (title, tooltip), by whether idle tabs are hidden; the title says what a click does.
IDLE_TABS_BUTTON_LOOKS = {False: ("Hide Idle", "Hide the tabs whose agent is idle"),
                          True: ("Show Idle", "Show the tabs whose agent is idle again")}
GAP_BETWEEN_TOP_BAR_BUTTONS = 4
SMALLEST_SORT_POPUP_WIDTH = 60  # a few letters of its title and its arrows
HOOKS_NOTE_TOOL_TIP = ("With these hooks, the list can tell when an agent crashes: its row stays for 5 minutes, grayed "
                       "out and marked \"exited\", instead of disappearing. They also show Claude Code's background "
                       "shells and agents as yellow dots after the tab's name. Only sessions you start after "
                       "installing them are covered.")


def choose_hooks_note(hooks_status_per_agent_tool, listed_agent_tool_names):
    """Returns (the bottom bar's note about the app's hooks, or "" for none, the agent tools its
    Install Hooks button installs them for) (user request, 2026-10-27). Only tools with a session
    in the list count, so someone who never runs Codex isn't asked to install its hooks.

    Args:
        hooks_status_per_agent_tool: ComputerSnapshot.hooks_status_per_agent_tool.
        listed_agent_tool_names: The agent tools of the list's sessions.
    """
    missing_agent_tool_names = [agent_tool_name for agent_tool_name, status in hooks_status_per_agent_tool
                                if status == HOOKS_ARE_MISSING and agent_tool_name in listed_agent_tool_names]
    if missing_agent_tool_names:
        return f"No hooks for {' and '.join(missing_agent_tool_names)}", missing_agent_tool_names
    if any(status == HOOKS_AWAIT_APPROVAL and agent_tool_name in listed_agent_tool_names
           for agent_tool_name, status in hooks_status_per_agent_tool):
        return "Approve the Codex hooks: /hooks in Codex", []
    return "", []


class ListPanelView(NSView):
    """The panel's root view: tells its controller when its size changes, so the top bar fits,
    and when the top bar's empty space is dragged (the dropdown or the sidebar then turns into
    the list window; user requests, 2026-10-19 and 2026-10-22). The top bar's controls take
    their own clicks."""

    def acceptsFirstMouse_(self, event):
        return True  # the sidebar's top bar drags from the first click, though the dashboard isn't the active app

    def drawRect_(self, rect):
        # In dark mode, the top bar, the column titles and the bottom bar are on the list's own
        # background (the outline view's default, controlBackgroundColor) rather than the lighter
        # gray of the window, the popover or the sidebar around it; light mode keeps that gray
        # around the white list (user requests, 2026-10-27). AppKit redraws views when the mode changes.
        if appearance_is_dark(self.effectiveAppearance()):
            NSColor.controlBackgroundColor().set()
            NSRectFill(rect)

    def resizeSubviewsWithOldSize_(self, old_size):
        objc.super(ListPanelView, self).resizeSubviewsWithOldSize_(old_size)
        controller = getattr(self, "controller", None)
        if controller is not None:
            controller.fit_top_bar_to_width()
            controller.fit_bottom_bar_to_width()

    def mouseDown_(self, event):
        point = self.convertPoint_fromView_(event.locationInWindow(), None)
        is_on_the_top_bar = point.y >= self.bounds().size.height - TOP_BAR_HEIGHT
        self.top_bar_drag_start = NSEvent.mouseLocation() if is_on_the_top_bar else None  # screen coordinates
        if not is_on_the_top_bar:
            objc.super(ListPanelView, self).mouseDown_(event)

    def mouseDragged_(self, event):
        start = getattr(self, "top_bar_drag_start", None)
        pointer = NSEvent.mouseLocation()
        if start is not None and math.hypot(pointer.x - start.x, pointer.y - start.y) >= TOP_BAR_DRAG_DISTANCE_TO_DETACH:
            self.top_bar_drag_start = None
            self.controller.list_controller.owner.top_bar_dragged(self.controller, (start.x, start.y))


class ListPanelController(NSViewController):
    """Holds a list controller's outline view in a scroll view, with a top and a bottom bar and the
    list controller's row of column titles between the top bar and the list.

    The owner is the list controller's owner (see AgentSessionListController); the panel also
    calls its open_settings(), list_sort_order_chosen(list_sort_order, list_sort_order_is_reversed)
    (as the list controller does when a column's title is clicked),
    idle_tabs_are_hidden_chosen(idle_tabs_are_hidden),
    top_bar_dragged(list_panel_controller, start_point_on_screen) and
    install_hooks(agent_tool_names) (the bottom bar's Install Hooks button).
    """

    def initWithListController_(self, list_controller):
        self = objc.super(ListPanelController, self).initWithNibName_bundle_(None, None)
        if self is None:
            return None
        self.list_controller = list_controller
        return self

    def loadView(self):
        width, height = POPOVER_WIDTH, INITIAL_PANEL_HEIGHT
        root_view = ListPanelView.alloc().initWithFrame_(NSMakeRect(0, 0, width, height))
        scroll_view = NSScrollView.alloc().initWithFrame_(
            NSMakeRect(0, BOTTOM_BAR_HEIGHT, width, height - TOP_BAR_HEIGHT - BOTTOM_BAR_HEIGHT))
        scroll_view.setDocumentView_(self.list_controller.outline_view)
        self.list_controller.outline_view.sizeLastColumnToFit()  # a new column is only 100 points wide
        scroll_view.setHasVerticalScroller_(True)
        scroll_view.setAutohidesScrollers_(True)
        scroll_view.setAutomaticallyAdjustsContentInsets_(False)  # no blank band above the first row
        scroll_view.setContentInsets_(NSEdgeInsetsMake(0, 0, 0, 0))
        scroll_view.setDrawsBackground_(False)
        scroll_view.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
        root_view.addSubview_(scroll_view)
        # The column titles, between the top bar and the list (user request, 2026-10-26), as tall
        # as their titles need (fit_list_under_column_titles).
        column_titles_row_view = self.list_controller.column_titles_row_view
        column_titles_row_view.setFrame_(NSMakeRect(0, height - TOP_BAR_HEIGHT, width, 0))
        column_titles_row_view.setAutoresizingMask_(NSViewWidthSizable | NSViewMinYMargin)
        root_view.addSubview_(column_titles_row_view)
        self.list_controller.on_column_titles_row_height_changed = self.fit_list_under_column_titles

        # Top bar (user requests, milestone 2, 2026-10-19 to 2026-10-26): the sort order on the
        # left; Settings in the middle; at the right, Hide Idle and one button that expands or
        # collapses all (R8.1); a message line after the sort order.
        self.sort_label = NSTextField.labelWithString_("Sort:")
        self.sort_label.setFont_(NSFont.systemFontOfSize_(NSFont.smallSystemFontSize()))
        self.sort_label.sizeToFit()
        self.sort_label.setFrameOrigin_((8, height - TOP_BAR_HEIGHT + 6))
        self.sort_label.setAutoresizingMask_(NSViewMinYMargin)
        root_view.addSubview_(self.sort_label)
        self.list_sort_order_popup = NSPopUpButton.alloc().initWithFrame_pullsDown_(NSMakeRect(0, 0, 90, 20), False)
        # A gray pill like the buttons beside it, and narrower than the standard style (the inline
        # style draws no text in a pop-up).
        self.list_sort_order_popup.setBezelStyle_(NSBezelStyleRecessed)
        self.list_sort_order_popup.setControlSize_(NSControlSizeSmall)
        self.list_sort_order_popup.setFont_(NSFont.systemFontOfSize_(NSFont.smallSystemFontSize()))
        add_popup_choices(self.list_sort_order_popup, LIST_SORT_ORDER_TITLES)
        self.list_sort_order_popup.sizeToFit()
        self.sort_popup_width_for_its_widest_title = self.list_sort_order_popup.frame().size.width
        self.list_sort_order_popup.setTarget_(self)
        self.list_sort_order_popup.setAction_("listSortOrderChosen:")
        self.list_sort_order_popup.setFrameOrigin_((0, height - TOP_BAR_HEIGHT + 3))  # fit_top_bar_to_width sets x
        self.list_sort_order_popup.setAutoresizingMask_(NSViewMinYMargin)
        root_view.addSubview_(self.list_sort_order_popup)
        self.idle_tabs_button = PointingHandButton.alloc().initWithFrame_(NSMakeRect(0, 0, 70, 20))
        self.idle_tabs_button.setBezelStyle_(NSBezelStyleRecessed)  # a gray pill like the Sort pop-up's
        self.idle_tabs_button.setShowsBorderOnlyWhileMouseInside_(False)
        self.idle_tabs_button.setControlSize_(NSControlSizeSmall)
        self.idle_tabs_button.setFont_(NSFont.systemFontOfSize_(NSFont.smallSystemFontSize()))
        # As wide as the wider of its two titles, so the top bar doesn't shift when it toggles.
        idle_tabs_button_widths = []
        for title, _ in IDLE_TABS_BUTTON_LOOKS.values():
            self.idle_tabs_button.setTitle_(title)
            self.idle_tabs_button.sizeToFit()
            idle_tabs_button_widths.append(self.idle_tabs_button.frame().size.width)
        self.idle_tabs_button.setFrame_(NSMakeRect(  # fit_top_bar_to_width sets x
            0, height - TOP_BAR_HEIGHT + 3, max(idle_tabs_button_widths), self.list_sort_order_popup.frame().size.height))
        self.idle_tabs_button.setTarget_(self)
        self.idle_tabs_button.setAction_("idleTabsButtonClicked:")
        self.idle_tabs_button.setAutoresizingMask_(NSViewMinYMargin | NSViewMinXMargin)
        root_view.addSubview_(self.idle_tabs_button)
        self.update_idle_tabs_button()
        button_width, button_height = EXPAND_OR_COLLAPSE_ALL_BUTTON_SIZE
        self.expand_or_collapse_all_button = PointingHandButton.alloc().initWithFrame_(NSMakeRect(  # update_top_and_bottom_bar_buttons gives its look
            width - PANEL_SIDE_MARGIN - button_width, height - TOP_BAR_HEIGHT + 3, button_width, button_height))
        self.expand_or_collapse_all_button.setBordered_(False)
        self.expand_or_collapse_all_button.setTarget_(self)
        self.expand_or_collapse_all_button.setAction_("expandOrCollapseAllClicked:")
        self.expand_or_collapse_all_button.setAutoresizingMask_(NSViewMinYMargin | NSViewMinXMargin)  # stays at the top right
        root_view.addSubview_(self.expand_or_collapse_all_button)
        self.list_controller.on_snapshot_or_expansion_changed = self.update_top_and_bottom_bar_buttons
        self.message_text_field = NSTextField.labelWithString_("")
        self.message_text_field.setFrame_(NSMakeRect(0, height - TOP_BAR_HEIGHT + 4, 0, 18))  # fit_top_bar_to_width sets x and width
        self.message_text_field.setTextColor_(NSColor.secondaryLabelColor())
        self.message_text_field.setAutoresizingMask_(NSViewMinYMargin)
        root_view.addSubview_(self.message_text_field)

        # A Settings button with its name, easier to see than an icon (R34), in the middle of the top
        # bar, a gray pill like the controls beside it (user request, 2026-10-26; at the bottom left before).
        self.settings_button = PointingHandButton.alloc().initWithFrame_(NSMakeRect(0, 0, 80, 20))
        self.settings_button.setTitle_("Settings")
        self.settings_button.setBezelStyle_(NSBezelStyleRecessed)
        self.settings_button.setShowsBorderOnlyWhileMouseInside_(False)
        self.settings_button.setControlSize_(NSControlSizeSmall)
        self.settings_button.setFont_(NSFont.systemFontOfSize_(NSFont.smallSystemFontSize()))
        # A gear before its name (user request, 2026-10-27), as tall as the small text; the name alone where the symbol is missing.
        gear_image = NSImage.imageWithSystemSymbolName_accessibilityDescription_("gearshape", None)
        if gear_image is not None:
            self.settings_button.setImage_(gear_image.imageWithSymbolConfiguration_(
                NSImageSymbolConfiguration.configurationWithPointSize_weight_(NSFont.smallSystemFontSize(), NSFontWeightRegular)))
            self.settings_button.setImagePosition_(NSImageLeading)
        self.settings_button.setTarget_(self)
        self.settings_button.setAction_("settingsClicked:")
        self.settings_button.sizeToFit()
        self.settings_button.setFrame_(NSMakeRect(  # fit_top_bar_to_width sets x
            0, height - TOP_BAR_HEIGHT + 3, self.settings_button.frame().size.width, self.list_sort_order_popup.frame().size.height))
        self.settings_button.setAutoresizingMask_(NSViewMinYMargin)
        root_view.addSubview_(self.settings_button)

        # Bottom bar: one button that pauses or resumes all, at the right; a new window with an agent
        # session (R13.1) is the "+" row at the end of the list (user requests, 2026-10-19 to 2026-10-21).
        self.pause_or_resume_all_button = PointingHandButton.alloc().initWithFrame_(NSMakeRect(
            width - PANEL_SIDE_MARGIN - button_width, (BOTTOM_BAR_HEIGHT - button_height) / 2, button_width, button_height))
        self.pause_or_resume_all_button.setBordered_(False)
        self.pause_or_resume_all_button.setTarget_(self)
        self.pause_or_resume_all_button.setAction_("pauseOrResumeAllClicked:")
        self.pause_or_resume_all_button.setAutoresizingMask_(NSViewMaxYMargin | NSViewMinXMargin)  # stays at the bottom right
        root_view.addSubview_(self.pause_or_resume_all_button)
        # At the bottom left, while the app's hooks are missing for an agent tool with a session
        # in the list, a note and a button that installs them (user request, 2026-10-27).
        self.hooks_note_text_field = NSTextField.labelWithString_("")
        self.hooks_note_text_field.setFont_(NSFont.systemFontOfSize_(NSFont.smallSystemFontSize()))
        self.hooks_note_text_field.setTextColor_(NSColor.secondaryLabelColor())
        self.hooks_note_text_field.cell().setLineBreakMode_(NSLineBreakByTruncatingTail)
        self.hooks_note_text_field.setToolTip_(HOOKS_NOTE_TOOL_TIP)
        root_view.addSubview_(self.hooks_note_text_field)
        self.install_hooks_button = PointingHandButton.alloc().initWithFrame_(NSMakeRect(0, 0, 80, 20))
        self.install_hooks_button.setTitle_("Install Hooks")
        self.install_hooks_button.setBezelStyle_(NSBezelStyleRecessed)  # a gray pill like the top bar's buttons
        self.install_hooks_button.setShowsBorderOnlyWhileMouseInside_(False)
        self.install_hooks_button.setControlSize_(NSControlSizeSmall)
        self.install_hooks_button.setFont_(NSFont.systemFontOfSize_(NSFont.smallSystemFontSize()))
        self.install_hooks_button.sizeToFit()
        self.install_hooks_button.setTarget_(self)
        self.install_hooks_button.setAction_("installHooksClicked:")
        self.install_hooks_button.setToolTip_(HOOKS_NOTE_TOOL_TIP)
        root_view.addSubview_(self.install_hooks_button)
        self.agent_tool_names_to_install_hooks_for = []
        self.setView_(root_view)
        root_view.controller = self
        self.update_top_and_bottom_bar_buttons()
        self.fit_top_bar_to_width()
        self.fit_list_under_column_titles()

    @objc.python_method
    def fit_list_under_column_titles(self):
        """Gives the row of column titles the height its titles need (none, hidden, while no
        titled column shows) and the list the room below it."""
        bounds = self.view().bounds()
        column_titles_row_height = self.list_controller.measure_column_titles_row_height()
        list_top = bounds.size.height - TOP_BAR_HEIGHT - column_titles_row_height
        column_titles_row_view = self.list_controller.column_titles_row_view
        column_titles_row_view.setFrame_(NSMakeRect(0, list_top, bounds.size.width, column_titles_row_height))
        column_titles_row_view.setHidden_(not column_titles_row_height)  # its titles would show above a 0-high row
        self.list_controller.outline_view.enclosingScrollView().setFrame_(
            NSMakeRect(0, BOTTOM_BAR_HEIGHT, bounds.size.width, list_top - BOTTOM_BAR_HEIGHT))

    @objc.python_method
    def fit_top_bar_to_width(self):
        """Puts the expand or collapse all button at the right end, the Hide Idle button just left
        of it (user request, 2026-10-26; in the middle of the bar before) and the Settings button
        in the middle of the bar (user request, 2026-10-26), though never over the whole sort
        control on its left nor past the Hide Idle button on its right: in a narrow dropdown it
        sits just after the sort control, and in a narrow sidebar beside Hide Idle. The sort
        control fits between the panel's left edge and the Settings button: with its "Sort:" label
        when there is room, without it in a narrower one, and then with the pop-up narrowed,
        cutting its title short, until it hides below SMALLEST_SORT_POPUP_WIDTH (only in a sidebar
        or a list window narrower than 251 points, where the column titles still sort the list). The message
        line takes the room left before the buttons, so it never covers them; while it shows a
        message (iTerm2 can't be reached, so there are no rows to sort or hide) the sort control
        and the Hide Idle button step aside for it, and the Settings button moves to the right end."""
        label_x, label_width = self.sort_label.frame().origin.x, self.sort_label.frame().size.width
        popup_frame = self.list_sort_order_popup.frame()
        is_showing_a_message = bool(self.message_text_field.stringValue())
        self.idle_tabs_button.setHidden_(is_showing_a_message)
        bar_width = self.view().bounds().size.width
        right_end_x = bar_width - PANEL_SIDE_MARGIN  # then the left end of the leftmost button placed
        gap_before_the_buttons_placed = 0
        for button in (self.expand_or_collapse_all_button, self.idle_tabs_button):  # side by side from the right end
            if not button.isHidden():
                right_end_x -= gap_before_the_buttons_placed + button.frame().size.width
                button.setFrameOrigin_((right_end_x, button.frame().origin.y))
                gap_before_the_buttons_placed = GAP_BETWEEN_TOP_BAR_BUTTONS
        settings_button_width = self.settings_button.frame().size.width
        settings_button_x_at_the_right = right_end_x - gap_before_the_buttons_placed - settings_button_width
        whole_sort_control_right_end_x = label_x + label_width + 2 + self.sort_popup_width_for_its_widest_title
        right_end_x = (settings_button_x_at_the_right if is_showing_a_message
                       else min(max((bar_width - settings_button_width) / 2, whole_sort_control_right_end_x + 2),
                                settings_button_x_at_the_right))
        self.settings_button.setFrameOrigin_((right_end_x, self.settings_button.frame().origin.y))
        room_right_edge = right_end_x - 2
        popup_x_after_label = label_x + label_width + 2
        label_fits = not is_showing_a_message and popup_x_after_label + self.sort_popup_width_for_its_widest_title <= room_right_edge
        popup_x = popup_x_after_label if label_fits else label_x
        popup_width = max(min(self.sort_popup_width_for_its_widest_title, room_right_edge - popup_x), 0)
        popup_fits = not is_showing_a_message and popup_width >= SMALLEST_SORT_POPUP_WIDTH
        self.sort_label.setHidden_(not label_fits)
        self.list_sort_order_popup.setHidden_(not popup_fits)
        self.list_sort_order_popup.setFrame_(NSMakeRect(popup_x, popup_frame.origin.y, popup_width, popup_frame.size.height))
        message_x = popup_x + popup_width + 8 if popup_fits else label_x
        message_frame = self.message_text_field.frame()
        self.message_text_field.setFrame_(NSMakeRect(message_x, message_frame.origin.y,
                                                     max(right_end_x - message_x - 8, 0), message_frame.size.height))

    def expandOrCollapseAllClicked_(self, sender):
        """Collapses all monitor and window rows when the list is fully expanded, and otherwise
        expands them all (the tabs' previews stay as they are)."""
        self.list_controller.set_all_expanded(not self.list_controller.is_fully_expanded())

    def pauseOrResumeAllClicked_(self, sender):
        """Pauses every agent session that runs, or, once all are paused, resumes them all."""
        sessions_to_pause, sessions_to_resume = split_agent_sessions_into_pausable_and_resumable(
            self.list_controller.list_agent_session_entries())
        pause = bool(sessions_to_pause)
        self.list_controller.pause_or_resume_agent_sessions(
            [entry.session_id for entry in (sessions_to_pause if pause else sessions_to_resume)], pause=pause)

    @objc.python_method
    def update_top_and_bottom_bar_buttons(self):
        """Gives the expand or collapse button (top bar) and the pause or resume button (bottom
        bar) the look of the list's current state and shows each only when it applies; the top bar
        refits when its button appears or goes. Called after every snapshot and expansion change.

        The expand or collapse button shows while the list has two or more windows (user request,
        2026-10-19); the pause or resume button while it has an agent session that a click would
        pause or resume."""
        expand_or_collapse_all_button_was_hidden = self.expand_or_collapse_all_button.isHidden()
        symbol_name, fallback_title, tool_tip = EXPAND_OR_COLLAPSE_ALL_LOOKS[self.list_controller.is_fully_expanded()]
        show_symbol_on_button(self.expand_or_collapse_all_button, symbol_name, fallback_title, tool_tip)
        self.expand_or_collapse_all_button.setToolTip_(tool_tip)
        self.expand_or_collapse_all_button.setHidden_(self.list_controller.count_window_rows() < 2)
        sessions_to_pause, sessions_to_resume = split_agent_sessions_into_pausable_and_resumable(
            self.list_controller.list_agent_session_entries())
        symbol_name, fallback_title, tool_tip = PAUSE_OR_RESUME_ALL_LOOKS["pause" if sessions_to_pause else "resume"]
        show_symbol_on_button(self.pause_or_resume_all_button, symbol_name, fallback_title, tool_tip)
        self.pause_or_resume_all_button.setToolTip_(tool_tip)
        self.pause_or_resume_all_button.setHidden_(not (sessions_to_pause or sessions_to_resume))
        if self.expand_or_collapse_all_button.isHidden() != expand_or_collapse_all_button_was_hidden:
            self.fit_top_bar_to_width()
        snapshot = self.list_controller.snapshot
        note, self.agent_tool_names_to_install_hooks_for = choose_hooks_note(
            snapshot.hooks_status_per_agent_tool if snapshot is not None else (),
            {entry.agent_tool_name for entry in self.list_controller.list_agent_session_entries()})
        self.hooks_note_text_field.setStringValue_(note)
        self.fit_bottom_bar_to_width()

    @objc.python_method
    def fit_bottom_bar_to_width(self):
        """Puts the hooks note at the bottom left, then its Install Hooks button (when there is
        something to install), the note cut short before the button and the pause or resume button."""
        note = self.hooks_note_text_field.stringValue()
        shows_install_button = bool(note and self.agent_tool_names_to_install_hooks_for)
        self.hooks_note_text_field.setHidden_(not note)
        self.install_hooks_button.setHidden_(not shows_install_button)
        if not note:
            return
        right_end_x = (self.pause_or_resume_all_button.frame().origin.x - GAP_BETWEEN_TOP_BAR_BUTTONS
                       if not self.pause_or_resume_all_button.isHidden() else self.view().bounds().size.width - PANEL_SIDE_MARGIN)
        button_width, button_height = self.install_hooks_button.frame().size
        if shows_install_button:
            right_end_x -= button_width + GAP_BETWEEN_TOP_BAR_BUTTONS
        note_size = self.hooks_note_text_field.fittingSize()
        note_width = max(min(note_size.width, right_end_x - PANEL_SIDE_MARGIN), 0)
        self.hooks_note_text_field.setFrame_(NSMakeRect(PANEL_SIDE_MARGIN, (BOTTOM_BAR_HEIGHT - note_size.height) / 2,
                                                        note_width, note_size.height))
        self.install_hooks_button.setFrameOrigin_((PANEL_SIDE_MARGIN + note_width + GAP_BETWEEN_TOP_BAR_BUTTONS,
                                                   (BOTTOM_BAR_HEIGHT - button_height) / 2))

    def installHooksClicked_(self, sender):
        self.list_controller.owner.install_hooks(self.agent_tool_names_to_install_hooks_for)

    def listSortOrderChosen_(self, sender):
        self.list_controller.owner.list_sort_order_chosen(read_popup_choice(sender), False)  # in its first direction

    @objc.python_method
    def show_list_sort_order(self, list_sort_order, is_reversed):
        """Shows the rows in this order (reversed when is_reversed) and the pop-up with it chosen
        (the dropdown, the sidebar and the list window share one choice)."""
        self.view()
        select_popup_choice(self.list_sort_order_popup, list_sort_order)
        self.list_controller.set_list_sort_order(list_sort_order, is_reversed)

    def idleTabsButtonClicked_(self, sender):
        self.list_controller.owner.idle_tabs_are_hidden_chosen(not self.list_controller.idle_tabs_are_hidden)

    @objc.python_method
    def show_idle_tabs_are_hidden(self, idle_tabs_are_hidden):
        """Hides or shows the idle tabs' rows, and gives the button the title of what a click then
        does (the dropdown, the sidebar and the list window share one choice)."""
        self.view()
        self.list_controller.set_idle_tabs_are_hidden(idle_tabs_are_hidden)
        self.update_idle_tabs_button()

    @objc.python_method
    def update_idle_tabs_button(self):
        title, tool_tip = IDLE_TABS_BUTTON_LOOKS[self.list_controller.idle_tabs_are_hidden]
        self.idle_tabs_button.setTitle_(title)
        self.idle_tabs_button.setToolTip_(tool_tip)

    def settingsClicked_(self, sender):
        self.list_controller.owner.open_settings()

    @objc.python_method
    def show_message(self, message):
        """Shows one gray line at the top; the app controller uses it when iTerm2 can't be reached."""
        self.view()
        self.message_text_field.setStringValue_(message)
        self.message_text_field.setToolTip_(message)
        self.fit_top_bar_to_width()

    @objc.python_method
    def fit_size_to_rows(self):
        """Sizes the panel so every row's text shows in full when all rows are expanded, scrolling
        only when the rows are taller (or wider) than the screen allows. Collapsing or expanding
        rows keeps that size (user request, milestone 2)."""
        window = self.view().window()
        screen = (window.screen() if window is not None else None) or NSScreen.screens()[0]
        usable_frame = screen.visibleFrame()
        bars_height = TOP_BAR_HEIGHT + BOTTOM_BAR_HEIGHT + self.list_controller.measure_column_titles_row_height()  # and the column titles
        list_height = self.list_controller.measure_fully_expanded_list_height()  # the list has no spacing between rows
        largest_list_height = usable_frame.size.height - bars_height - SPACE_FOR_POPOVER_ARROW_AND_MARGIN
        list_height = min(max(list_height + LIST_BOTTOM_PADDING, SMALLEST_LIST_HEIGHT), largest_list_height)
        width = self.list_controller.measure_widest_row_width() + SPACE_FOR_SCROLLER_AND_MARGINS
        width = min(max(width, SMALLEST_POPOVER_WIDTH), usable_frame.size.width * LARGEST_SHARE_OF_SCREEN_WIDTH)
        self.setPreferredContentSize_((width, list_height + bars_height))


def build_dropdown_popover(list_panel_controller):
    """Returns a transient NSPopover (it closes when you click elsewhere) showing the panel."""
    popover = NSPopover.alloc().init()
    popover.setContentViewController_(list_panel_controller)
    popover.setBehavior_(NSPopoverBehaviorTransient)
    popover.setAnimates_(False)
    return popover
