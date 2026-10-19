"""The panel around the list: the sort order and expand or collapse all at the top, a Settings button at the bottom left.

The same panel class is used in the dropdown (an NSPopover under the menu bar item), in the
sidebar and in the list window, each with its own list controller.
"""

import math

import objc
from AppKit import (NSBezelStyleRecessed, NSBezelStyleRounded, NSColor, NSControlSizeSmall, NSEdgeInsetsMake, NSEvent, NSFont,
                    NSMakeRect, NSPopover, NSPopoverBehaviorTransient, NSPopUpButton, NSScreen,
                    NSScrollView, NSTextField, NSView, NSViewController, NSViewHeightSizable, NSViewMaxXMargin, NSViewMaxYMargin,
                    NSViewMinXMargin, NSViewMinYMargin, NSViewWidthSizable)

from agent_command_tab.user_interface.control_helpers import (ROWS_OPEN_SYMBOL_NAMES, add_popup_choices,
                                                              read_popup_choice, select_popup_choice,
                                                              show_symbol_on_button)
from agent_command_tab.user_interface.pointing_hand_button import PointingHandButton

# The Sort pop-up's titles for settings_store.LIST_SORT_ORDERS.
LIST_SORT_ORDER_TITLES = {"manual": "Manual", "activity": "Activity", "alphabet": "Alphabetically", "created_time": "Created Time"}
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
PANEL_SIDE_MARGIN = 6  # between the panel's edges and the expand or collapse button and the Settings button
EXPAND_OR_COLLAPSE_ALL_BUTTON_SIZE = (24, 22)
# The top bar's one expand or collapse button (user requests, 2026-10-19): (SF Symbol, text
# fallback, tooltip), by whether the list is fully expanded. The symbol shows the current state;
# the fallback text and the tooltip say what a click does.
EXPAND_OR_COLLAPSE_ALL_LOOKS = {False: (ROWS_OPEN_SYMBOL_NAMES[False], "Expand all", "Expand all"),
                                True: (ROWS_OPEN_SYMBOL_NAMES[True], "Collapse all", "Collapse all")}


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
    calls its open_settings(), list_sort_order_chosen(list_sort_order) and
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

        # Top bar (user requests, milestone 2 and 2026-10-19): the sort order on the left, one
        # button that expands or collapses all (R8.1) on the right, and a message line between them.
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
        self.list_sort_order_popup.setTarget_(self)
        self.list_sort_order_popup.setAction_("listSortOrderChosen:")
        self.list_sort_order_popup.setFrameOrigin_((0, height - TOP_BAR_HEIGHT + 3))  # fit_top_bar_to_width sets x
        self.list_sort_order_popup.setAutoresizingMask_(NSViewMinYMargin)
        root_view.addSubview_(self.list_sort_order_popup)
        button_width, button_height = EXPAND_OR_COLLAPSE_ALL_BUTTON_SIZE
        self.expand_or_collapse_all_button = PointingHandButton.alloc().initWithFrame_(NSMakeRect(  # update_expand_or_collapse_all_button gives its look
            width - PANEL_SIDE_MARGIN - button_width, height - TOP_BAR_HEIGHT + 3, button_width, button_height))
        self.expand_or_collapse_all_button.setBordered_(False)
        self.expand_or_collapse_all_button.setTarget_(self)
        self.expand_or_collapse_all_button.setAction_("expandOrCollapseAllClicked:")
        self.expand_or_collapse_all_button.setAutoresizingMask_(NSViewMinYMargin | NSViewMinXMargin)  # stays at the top right
        root_view.addSubview_(self.expand_or_collapse_all_button)
        self.list_controller.on_expansion_changed = self.update_expand_or_collapse_all_button
        self.message_text_field = NSTextField.labelWithString_("")
        self.message_text_field.setFrame_(NSMakeRect(0, height - TOP_BAR_HEIGHT + 4, 0, 18))  # fit_top_bar_to_width sets x and width
        self.message_text_field.setTextColor_(NSColor.secondaryLabelColor())
        self.message_text_field.setAutoresizingMask_(NSViewMinYMargin)
        root_view.addSubview_(self.message_text_field)

        # Bottom bar: a Settings button with its name, at the left, easier to see than an icon (R34);
        # a new window with an agent session (R13.1) is the "+" row at the end of the list (user
        # requests, 2026-10-19).
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
        self.setView_(root_view)
        root_view.controller = self
        self.fit_top_bar_to_width()
        self.update_expand_or_collapse_all_button()

    @objc.python_method
    def fit_top_bar_to_width(self):
        """Fits the sort control between the panel's left edge and the expand or collapse button
        at the right (or the right edge, while that button is hidden): with its "Sort:" label when
        there is room, without it in a narrow sidebar, and hidden when even the pop-up doesn't fit
        (the dropdown, at least 320 points wide, always shows both). The message line takes the
        room left before that button, so it never covers it; while it shows a message (iTerm2
        can't be reached, so there are no rows to sort) the sort control steps aside for it."""
        label_x, label_width = self.sort_label.frame().origin.x, self.sort_label.frame().size.width
        popup_frame = self.list_sort_order_popup.frame()
        right_end_x = (self.view().bounds().size.width - PANEL_SIDE_MARGIN if self.expand_or_collapse_all_button.isHidden()
                       else self.expand_or_collapse_all_button.frame().origin.x)
        room_right_edge = right_end_x - 2
        popup_x_after_label = label_x + label_width + 2
        is_showing_a_message = bool(self.message_text_field.stringValue())
        label_fits = not is_showing_a_message and popup_x_after_label + popup_frame.size.width <= room_right_edge
        popup_fits = not is_showing_a_message and label_x + popup_frame.size.width <= room_right_edge
        popup_x = popup_x_after_label if label_fits else label_x
        self.sort_label.setHidden_(not label_fits)
        self.list_sort_order_popup.setHidden_(not popup_fits)
        self.list_sort_order_popup.setFrameOrigin_((popup_x, popup_frame.origin.y))
        message_x = popup_x + popup_frame.size.width + 8 if popup_fits else label_x
        message_frame = self.message_text_field.frame()
        self.message_text_field.setFrame_(NSMakeRect(message_x, message_frame.origin.y,
                                                     max(right_end_x - message_x - 8, 0), message_frame.size.height))

    def expandOrCollapseAllClicked_(self, sender):
        """Collapses all monitor and window rows when the list is fully expanded, and otherwise
        expands them all (the tabs' previews stay as they are)."""
        self.list_controller.set_all_expanded(not self.list_controller.is_fully_expanded())

    @objc.python_method
    def update_expand_or_collapse_all_button(self):
        """Shows the expand or collapse button only while the list has two or more windows (user
        request, 2026-10-19), with the look of the list's current state; the top bar refits when
        the button appears or goes. Called after every snapshot and expansion change."""
        symbol_name, fallback_title, tool_tip = EXPAND_OR_COLLAPSE_ALL_LOOKS[self.list_controller.is_fully_expanded()]
        show_symbol_on_button(self.expand_or_collapse_all_button, symbol_name, fallback_title, tool_tip)
        self.expand_or_collapse_all_button.setToolTip_(tool_tip)
        is_shown = self.list_controller.count_window_rows() >= 2
        if self.expand_or_collapse_all_button.isHidden() == is_shown:
            self.expand_or_collapse_all_button.setHidden_(not is_shown)
            self.fit_top_bar_to_width()

    def listSortOrderChosen_(self, sender):
        self.list_controller.owner.list_sort_order_chosen(read_popup_choice(sender))

    @objc.python_method
    def show_list_sort_order(self, list_sort_order):
        """Shows the rows in this order and the pop-up with it chosen (the dropdown and the sidebar share one choice)."""
        self.view()
        select_popup_choice(self.list_sort_order_popup, list_sort_order)
        self.list_controller.set_list_sort_order(list_sort_order)

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
