"""The panel around the list: the sort order, Hide Idle and expand or collapse all at the top; a Settings button at the bottom left and pause or resume all at the bottom right.

The same panel class is used in the dropdown (an NSPopover under the menu bar item), in the
sidebar and in the list window, each with its own list controller.
"""

import math

import objc
from AppKit import (NSBezelStyleRecessed, NSBezelStyleRounded, NSColor, NSControlSizeSmall, NSEdgeInsetsMake, NSEvent, NSFont,
                    NSMakeRect, NSPopover, NSPopoverBehaviorTransient, NSPopUpButton, NSScreen,
                    NSScrollView, NSTextField, NSView, NSViewController, NSViewHeightSizable, NSViewMaxXMargin, NSViewMaxYMargin,
                    NSViewMinXMargin, NSViewMinYMargin, NSViewWidthSizable)

from agent_command_tab.session_status_computation import split_agent_sessions_into_pausable_and_resumable
from agent_command_tab.user_interface.control_helpers import (ROWS_OPEN_SYMBOL_NAMES, add_popup_choices,
                                                              read_popup_choice, select_popup_choice,
                                                              show_symbol_on_button)
from agent_command_tab.user_interface.pointing_hand_button import PointingHandButton

# The Sort pop-up's titles for settings_store.LIST_SORT_ORDERS.
LIST_SORT_ORDER_TITLES = {"manual": "Manual", "activity": "Activity", "alphabet": "Alphabetically", "created_time": "Created Time",
                          "memory_usage": "Memory Usage", "cpu_usage": "CPU Usage"}
POPOVER_WIDTH = 460  # before the first fit
SMALLEST_POPOVER_WIDTH = 320
LARGEST_SHARE_OF_SCREEN_WIDTH = 0.9
SPACE_FOR_SCROLLER_AND_MARGINS = 24
TOP_BAR_HEIGHT = 26
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


class ListPanelView(NSView):
    """The panel's root view: tells its controller when its size changes, so the top bar fits,
    and when the top bar's empty space is dragged (the dropdown then detaches into the list
    window; user request, 2026-10-19). The top bar's controls take their own clicks."""

    def resizeSubviewsWithOldSize_(self, old_size):
        objc.super(ListPanelView, self).resizeSubviewsWithOldSize_(old_size)
        controller = getattr(self, "controller", None)
        if controller is not None:
            controller.fit_top_bar_to_width()

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
    """Holds a list controller's outline view in a scroll view, with a top and a bottom bar.

    The owner is the list controller's owner (see AgentSessionListController); the panel also
    calls its open_settings(), list_sort_order_chosen(list_sort_order),
    idle_tabs_are_hidden_chosen(idle_tabs_are_hidden) and
    top_bar_dragged(list_panel_controller, start_point_on_screen).
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

        # Top bar (user requests, milestone 2, 2026-10-19 to 2026-10-21): the sort order on the
        # left; at the right, Hide Idle and one button that expands or collapses all (R8.1); a
        # message line between them.
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

        # Bottom bar: a Settings button with its name, at the left, easier to see than an icon (R34),
        # and one button that pauses or resumes all, at the right; a new window with an agent
        # session (R13.1) is the "+" row at the end of the list (user requests, 2026-10-19 to 2026-10-21).
        self.settings_button = PointingHandButton.alloc().initWithFrame_(NSMakeRect(0, 0, 80, 22))
        self.settings_button.setTitle_("Settings")
        self.settings_button.setBezelStyle_(NSBezelStyleRounded)
        self.settings_button.setControlSize_(NSControlSizeSmall)
        self.settings_button.setFont_(NSFont.systemFontOfSize_(NSFont.smallSystemFontSize()))
        self.settings_button.setTarget_(self)
        self.settings_button.setAction_("settingsClicked:")
        self.settings_button.sizeToFit()
        self.settings_button.setFrameOrigin_((PANEL_SIDE_MARGIN, (BOTTOM_BAR_HEIGHT - self.settings_button.frame().size.height) / 2))
        self.settings_button.setAutoresizingMask_(NSViewMaxYMargin | NSViewMaxXMargin)  # stays at the bottom left
        root_view.addSubview_(self.settings_button)
        self.pause_or_resume_all_button = PointingHandButton.alloc().initWithFrame_(NSMakeRect(
            width - PANEL_SIDE_MARGIN - button_width, (BOTTOM_BAR_HEIGHT - button_height) / 2, button_width, button_height))
        self.pause_or_resume_all_button.setBordered_(False)
        self.pause_or_resume_all_button.setTarget_(self)
        self.pause_or_resume_all_button.setAction_("pauseOrResumeAllClicked:")
        self.pause_or_resume_all_button.setAutoresizingMask_(NSViewMaxYMargin | NSViewMinXMargin)  # stays at the bottom right
        root_view.addSubview_(self.pause_or_resume_all_button)
        self.setView_(root_view)
        root_view.controller = self
        self.update_top_and_bottom_bar_buttons()
        self.fit_top_bar_to_width()

    @objc.python_method
    def fit_top_bar_to_width(self):
        """Puts the shown buttons at the right end (expand or collapse all rightmost, then Hide
        Idle) and fits the sort control between the panel's left edge and them: with its "Sort:"
        label when there is room, without it in a narrow sidebar, and then with the pop-up
        narrowed, cutting its title short, since it is the only place to choose the sort order
        (the dropdown, at least 320 points wide, always shows both in full). The pop-up hides only
        below SMALLEST_SORT_POPUP_WIDTH, narrower than the sidebar can be. The message line takes
        the room left before the buttons, so it never covers them; while it shows a message
        (iTerm2 can't be reached, so there are no rows to sort or hide) the sort control and the
        Hide Idle button step aside for it."""
        label_x, label_width = self.sort_label.frame().origin.x, self.sort_label.frame().size.width
        popup_frame = self.list_sort_order_popup.frame()
        is_showing_a_message = bool(self.message_text_field.stringValue())
        self.idle_tabs_button.setHidden_(is_showing_a_message)
        right_end_x = self.view().bounds().size.width - PANEL_SIDE_MARGIN
        shown_buttons = [button for button in (self.expand_or_collapse_all_button, self.idle_tabs_button)
                         if not button.isHidden()]
        for index, button in enumerate(shown_buttons):  # right to left
            right_end_x -= button.frame().size.width + (GAP_BETWEEN_TOP_BAR_BUTTONS if index else 0)
            button.setFrameOrigin_((right_end_x, button.frame().origin.y))
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

    def listSortOrderChosen_(self, sender):
        self.list_controller.owner.list_sort_order_chosen(read_popup_choice(sender))

    @objc.python_method
    def show_list_sort_order(self, list_sort_order):
        """Shows the rows in this order and the pop-up with it chosen (the dropdown and the sidebar share one choice)."""
        self.view()
        select_popup_choice(self.list_sort_order_popup, list_sort_order)
        self.list_controller.set_list_sort_order(list_sort_order)

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
        bars_height = TOP_BAR_HEIGHT + BOTTOM_BAR_HEIGHT
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
