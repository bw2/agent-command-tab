"""The panel around the list: expand and collapse all at the top, settings and "+" at the bottom.

The same panel class is used in the dropdown (an NSPopover under the menu bar item) and in
the sidebar, each with its own list controller.
"""

import objc
from AppKit import (NSBezelStyleInline, NSButton, NSColor, NSEdgeInsetsMake, NSImage, NSMakeRect, NSMenu,
                    NSMenuItem, NSPopover, NSPopoverBehaviorTransient, NSScreen, NSScrollView, NSTextField, NSView,
                    NSViewController, NSViewHeightSizable, NSViewMaxYMargin, NSViewMinXMargin, NSViewMinYMargin,
                    NSViewWidthSizable)

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


class ListPanelController(NSViewController):
    """Holds a list controller's outline view in a scroll view, with a top and a bottom bar.

    The owner is the list controller's owner (see AgentSessionListController); the panel also
    calls its open_settings().
    """

    def initWithListController_(self, list_controller):
        self = objc.super(ListPanelController, self).initWithNibName_bundle_(None, None)
        if self is None:
            return None
        self.list_controller = list_controller
        return self

    def loadView(self):
        width, height = POPOVER_WIDTH, INITIAL_PANEL_HEIGHT
        root_view = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, width, height))
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

        # Top bar: a message line on the left, expand and collapse all on the right (R8.1).
        self.message_text_field = NSTextField.labelWithString_("")
        self.message_text_field.setFrame_(NSMakeRect(8, height - TOP_BAR_HEIGHT + 4, width - 180, 18))
        self.message_text_field.setTextColor_(NSColor.secondaryLabelColor())
        self.message_text_field.setAutoresizingMask_(NSViewMinYMargin | NSViewWidthSizable)
        root_view.addSubview_(self.message_text_field)
        x = width - 8
        for title, action_name in (("Collapse all", "collapseAll:"), ("Expand all", "expandAll:")):
            button = make_small_button(title, self, action_name)
            x -= button.frame().size.width + 6
            button.setFrameOrigin_((x, height - TOP_BAR_HEIGHT + 3))
            button.setAutoresizingMask_(NSViewMinYMargin | NSViewMinXMargin)  # stay at the top right
            root_view.addSubview_(button)

        # Bottom bar: settings (R34) and a new window with an agent session (R13.1).
        settings_button = make_symbol_button("gearshape", "Settings", "Settings", self, "settingsClicked:")
        settings_button.setFrameOrigin_((6, 3))
        settings_button.setAutoresizingMask_(NSViewMaxYMargin)
        root_view.addSubview_(settings_button)
        self.new_window_button = make_symbol_button("plus", "+", "New iTerm2 window with an agent session", self,
                                                    "newWindowClicked:")
        self.new_window_button.setFrameOrigin_((34, 3))
        self.new_window_button.setAutoresizingMask_(NSViewMaxYMargin)
        root_view.addSubview_(self.new_window_button)
        self.setView_(root_view)

    def expandAll_(self, sender):
        self.list_controller.set_all_expanded(True)

    def collapseAll_(self, sender):
        self.list_controller.set_all_expanded(False)

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
