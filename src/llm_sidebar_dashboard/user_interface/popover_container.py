"""The panel around the list: expand and collapse all, the sort order and settings at the top, "+" at the bottom.

The same panel class is used in the dropdown (an NSPopover under the menu bar item) and in
the sidebar, each with its own list controller.
"""

import objc
from AppKit import (NSBezelStyleInline, NSBezelStyleRecessed, NSButton, NSColor, NSControlSizeSmall, NSEdgeInsetsMake, NSFont, NSImage,
                    NSMakeRect, NSMenu, NSMenuItem, NSPopover, NSPopoverBehaviorTransient, NSPopUpButton, NSScreen,
                    NSScrollView, NSTextField, NSView, NSViewController, NSViewHeightSizable, NSViewMaxYMargin,
                    NSViewMinXMargin, NSViewMinYMargin, NSViewWidthSizable)

# "Created" rather than "Created Time" keeps the pop-up narrow enough for a 292-point sidebar.
LIST_SORT_ORDER_TITLES = {"manual": "Manual", "activity": "Activity", "alphabet": "Alphabet", "created_time": "Created"}
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


def make_small_button(title, target, action_name):
    """Returns a small borderless text button."""
    button = NSButton.alloc().initWithFrame_(NSMakeRect(0, 0, 60, 20))
    button.setTitle_(title)
    button.setBezelStyle_(NSBezelStyleInline)
    button.setTarget_(target)
    button.setAction_(action_name)
    button.sizeToFit()
    return button


def make_symbol_button(symbol_name, fallback_title, tool_tip, target, action_name):
    """Returns a borderless button showing an SF Symbol (or a text fallback)."""
    button = NSButton.alloc().initWithFrame_(NSMakeRect(0, 0, 24, 22))
    image = NSImage.imageWithSystemSymbolName_accessibilityDescription_(symbol_name, tool_tip)
    if image is not None:
        button.setImage_(image)
    else:
        button.setTitle_(fallback_title)
    button.setBordered_(False)
    button.setToolTip_(tool_tip)
    button.setTarget_(target)
    button.setAction_(action_name)
    return button


class ListPanelView(NSView):
    """The panel's root view: tells its controller when its size changes, so the top bar fits."""

    def resizeSubviewsWithOldSize_(self, old_size):
        objc.super(ListPanelView, self).resizeSubviewsWithOldSize_(old_size)
        controller = getattr(self, "controller", None)
        if controller is not None:
            controller.fit_top_bar_to_width()


class ListPanelController(NSViewController):
    """Holds a list controller's outline view in a scroll view, with a top and a bottom bar.

    The owner is the list controller's owner (see AgentSessionListController); the panel also
    calls its open_settings() and list_sort_order_chosen(list_sort_order).
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

        # Top bar (user request, milestone 2): expand and collapse all and the sort order on the
        # left (R8.1), the settings button on the right (R34), and a message line between them.
        x = 8
        for title, action_name in (("Expand all", "expandAll:"), ("Collapse all", "collapseAll:")):
            button = make_small_button(title, self, action_name)
            button.setFrameOrigin_((x, height - TOP_BAR_HEIGHT + 3))
            button.setAutoresizingMask_(NSViewMinYMargin)  # stays at the top left
            root_view.addSubview_(button)
            x += button.frame().size.width + 6
        self.sort_label = NSTextField.labelWithString_("Sort:")
        self.sort_label.setFont_(NSFont.systemFontOfSize_(NSFont.smallSystemFontSize()))
        self.sort_label.sizeToFit()
        self.sort_label.setFrameOrigin_((x, height - TOP_BAR_HEIGHT + 6))
        self.sort_label.setAutoresizingMask_(NSViewMinYMargin)
        root_view.addSubview_(self.sort_label)
        self.list_sort_order_popup = NSPopUpButton.alloc().initWithFrame_pullsDown_(NSMakeRect(0, 0, 90, 20), False)
        # A gray pill like the buttons beside it, and narrower than the standard style (the inline
        # style draws no text in a pop-up).
        self.list_sort_order_popup.setBezelStyle_(NSBezelStyleRecessed)
        self.list_sort_order_popup.setControlSize_(NSControlSizeSmall)
        self.list_sort_order_popup.setFont_(NSFont.systemFontOfSize_(NSFont.smallSystemFontSize()))
        self.list_sort_order_popup.addItemsWithTitles_(list(LIST_SORT_ORDER_TITLES.values()))
        self.list_sort_order_popup.sizeToFit()
        self.list_sort_order_popup.setTarget_(self)
        self.list_sort_order_popup.setAction_("listSortOrderChosen:")
        self.list_sort_order_popup.setFrameOrigin_((0, height - TOP_BAR_HEIGHT + 3))  # fit_top_bar_to_width sets x
        self.list_sort_order_popup.setAutoresizingMask_(NSViewMinYMargin)
        root_view.addSubview_(self.list_sort_order_popup)
        self.settings_button = make_symbol_button("gearshape", "Settings", "Settings", self, "settingsClicked:")
        self.settings_button.setFrameOrigin_((width - 6 - self.settings_button.frame().size.width, height - TOP_BAR_HEIGHT + 3))
        self.settings_button.setAutoresizingMask_(NSViewMinYMargin | NSViewMinXMargin)  # stays at the top right
        root_view.addSubview_(self.settings_button)
        self.message_text_field = NSTextField.labelWithString_("")
        self.message_text_field.setFrame_(NSMakeRect(0, height - TOP_BAR_HEIGHT + 4, 0, 18))  # fit_top_bar_to_width sets x and width
        self.message_text_field.setTextColor_(NSColor.secondaryLabelColor())
        self.message_text_field.setAutoresizingMask_(NSViewMinYMargin)
        root_view.addSubview_(self.message_text_field)

        # Bottom bar: a new window with an agent session (R13.1).
        self.new_window_button = make_symbol_button("plus", "+", "New iTerm2 window with an agent session", self,
                                                    "newWindowClicked:")
        self.new_window_button.setFrameOrigin_((6, 3))
        self.new_window_button.setAutoresizingMask_(NSViewMaxYMargin)
        root_view.addSubview_(self.new_window_button)
        self.setView_(root_view)
        root_view.controller = self
        self.fit_top_bar_to_width()

    @objc.python_method
    def fit_top_bar_to_width(self):
        """Fits the sort control between "Collapse all" and the settings button: with its "Sort:"
        label when there is room, without it in a narrow sidebar, and hidden when even the pop-up
        doesn't fit (the dropdown, at least 320 points wide, always shows both). The message line
        takes the room left before the settings button, so it never covers that button; while it
        shows a message (iTerm2 can't be reached, so there are no rows to sort) the sort control
        steps aside for it."""
        label_x, label_width = self.sort_label.frame().origin.x, self.sort_label.frame().size.width
        popup_frame = self.list_sort_order_popup.frame()
        settings_button_x = self.settings_button.frame().origin.x
        room_right_edge = settings_button_x - 2
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
                                                     max(settings_button_x - message_x - 8, 0), message_frame.size.height))

    def expandAll_(self, sender):
        self.list_controller.set_all_expanded(True)

    def collapseAll_(self, sender):
        self.list_controller.set_all_expanded(False)

    def listSortOrderChosen_(self, sender):
        chosen_title = sender.titleOfSelectedItem()
        self.list_controller.owner.list_sort_order_chosen(
            next(list_sort_order for list_sort_order, title in LIST_SORT_ORDER_TITLES.items() if title == chosen_title))

    @objc.python_method
    def show_list_sort_order(self, list_sort_order):
        """Shows the rows in this order and the pop-up with it chosen (the dropdown and the sidebar share one choice)."""
        self.view()
        self.list_sort_order_popup.selectItemWithTitle_(LIST_SORT_ORDER_TITLES[list_sort_order])
        self.list_controller.set_list_sort_order(list_sort_order)

    def settingsClicked_(self, sender):
        self.list_controller.owner.open_settings()

    def newWindowClicked_(self, sender):
        menu = NSMenu.alloc().initWithTitle_("")
        for agent_tool_name in self.list_controller.owner.list_agent_tool_names():
            menu_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                f"New iTerm2 window with {agent_tool_name} session…", "newWindowMenuItemChosen:", "")
            menu_item.setTarget_(self)
            menu_item.setRepresentedObject_(agent_tool_name)
            menu.addItem_(menu_item)
        menu.popUpMenuPositioningItem_atLocation_inView_(None, (0, sender.frame().size.height), sender)

    def newWindowMenuItemChosen_(self, sender):
        self.list_controller.owner.start_agent_session_from_menu(None, None, sender.representedObject())

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
        outline_view = self.list_controller.outline_view
        window = self.view().window()
        screen = (window.screen() if window is not None else None) or NSScreen.screens()[0]
        usable_frame = screen.visibleFrame()
        bars_height = TOP_BAR_HEIGHT + BOTTOM_BAR_HEIGHT
        row_count_when_fully_expanded = len(self.list_controller.list_all_items())
        list_height = row_count_when_fully_expanded * (outline_view.rowHeight() + outline_view.intercellSpacing().height)
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
