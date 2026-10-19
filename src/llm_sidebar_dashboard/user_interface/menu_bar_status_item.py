"""The menu bar item: a colored "C" followed by the number of asking agent sessions (R1 to R3)."""

import objc
from AppKit import (NSApp, NSAttributedString, NSColor, NSEventMaskLeftMouseUp, NSEventMaskRightMouseUp,
                    NSEventTypeRightMouseUp, NSFont, NSFontAttributeName, NSFontWeightBold,
                    NSForegroundColorAttributeName, NSMenu, NSMenuItem, NSMinYEdge, NSMutableAttributedString,
                    NSStatusBar, NSVariableStatusItemLength, NSWorkspace)
from Foundation import NSObject

from llm_sidebar_dashboard.session_status_computation import pick_most_urgent_state
from llm_sidebar_dashboard.snapshot_data_classes import iterate_agent_session_entries

ICON_COLORS_BY_NAME = {
    "teal": NSColor.systemTealColor, "blue": NSColor.systemBlueColor, "purple": NSColor.systemPurpleColor,
    "pink": NSColor.systemPinkColor, "red": NSColor.systemRedColor, "orange": NSColor.systemOrangeColor,
    "green": NSColor.systemGreenColor, "light_blue": NSColor.systemCyanColor,
}
AUTOMATIC_ICON_COLOR_NAME_BY_MOST_URGENT_STATE = {"asking": "light_blue", "error": "red", "stuck": "orange"}


def count_asking_agent_sessions(snapshot):
    """Returns how many agent sessions in the snapshot are asking."""
    return sum(1 for _, _, agent_session_entry in iterate_agent_session_entries(snapshot)
               if agent_session_entry.status == "asking")


def choose_automatic_icon_color_name(snapshot):
    """Returns the color of the "auto" icon color setting: light blue while any agent session is
    asking, else red if any has an error, else orange if any is stuck, otherwise green."""
    states = [agent_session_entry.status for _, _, agent_session_entry in iterate_agent_session_entries(snapshot)]
    return AUTOMATIC_ICON_COLOR_NAME_BY_MOST_URGENT_STATE.get(pick_most_urgent_state(states), "green")


class MenuBarStatusItemController(NSObject):
    """Owns the NSStatusItem. A left click toggles the dropdown; a right click shows a small menu.

    The owner (the app controller) has settings, open_settings(), show_sidebar_for_a_few_seconds(),
    quit() and note_application_in_front_before_dropdown(application).
    """

    def initWithPopover_owner_(self, popover, owner):
        self = objc.super(MenuBarStatusItemController, self).init()
        if self is None:
            return None
        self.popover = popover
        self.owner = owner
        self.asking_count = 0
        self.automatic_icon_color_name = "green"
        self.has_connection_problem = False
        self.status_item = NSStatusBar.systemStatusBar().statusItemWithLength_(NSVariableStatusItemLength)
        button = self.status_item.button()
        button.setTarget_(self)
        button.setAction_("statusItemClicked:")
        button.sendActionOn_(NSEventMaskLeftMouseUp | NSEventMaskRightMouseUp)
        self.redraw()
        return self

    @objc.python_method
    def show_snapshot(self, snapshot):
        """Shows how many agent sessions are asking (when the settings ask for it) and, with the
        "auto" icon color, the color of the most urgent agent session."""
        self.asking_count = count_asking_agent_sessions(snapshot)
        self.automatic_icon_color_name = choose_automatic_icon_color_name(snapshot)
        self.has_connection_problem = False
        self.redraw()

    @objc.python_method
    def show_connection_problem(self):
        """Draws the "C" in gray while iTerm2 can't be reached."""
        self.has_connection_problem = True
        self.redraw()

    @objc.python_method
    def redraw(self):
        """Draws the "C" in the chosen color, or the most urgent agent session's color with "auto"
        (gray during a connection problem), plus the asking count in purple."""
        settings = self.owner.settings
        color_name = self.automatic_icon_color_name if settings["icon_color"] == "auto" else settings["icon_color"]
        letter_color = (NSColor.secondaryLabelColor() if self.has_connection_problem
                        else ICON_COLORS_BY_NAME.get(color_name, NSColor.systemGreenColor)())
        font = NSFont.menuBarFontOfSize_(0)
        title = NSMutableAttributedString.alloc().init()
        title.appendAttributedString_(NSAttributedString.alloc().initWithString_attributes_(
            "C", {NSFontAttributeName: NSFont.systemFontOfSize_weight_(font.pointSize(), NSFontWeightBold),
                  NSForegroundColorAttributeName: letter_color}))
        if self.asking_count and settings["icon_shows_asking_count"] and not self.has_connection_problem:
            title.appendAttributedString_(NSAttributedString.alloc().initWithString_attributes_(
                f" {self.asking_count}", {NSFontAttributeName: font, NSForegroundColorAttributeName: NSColor.systemPurpleColor()}))
        self.status_item.button().setAttributedTitle_(title)

    def statusItemClicked_(self, sender):
        if NSApp.currentEvent().type() == NSEventTypeRightMouseUp:
            self.show_right_click_menu()
        elif self.popover.isShown():
            self.popover.performClose_(None)
        else:
            self.show_popover()

    @objc.python_method
    def show_popover(self):
        self.owner.note_application_in_front_before_dropdown(NSWorkspace.sharedWorkspace().frontmostApplication())
        NSApp.activateIgnoringOtherApps_(True)  # so the list can take typing (rename)
        list_panel_controller = self.popover.contentViewController()
        list_panel_controller.list_controller.prepare_to_appear()  # the list skips redraws while closed
        list_panel_controller.fit_size_to_rows()
        button = self.status_item.button()
        self.popover.showRelativeToRect_ofView_preferredEdge_(button.bounds(), button, NSMinYEdge)

    @objc.python_method
    def show_right_click_menu(self):
        menu = NSMenu.alloc().initWithTitle_("")
        for title, action_name in (("Show the sidebar", "showSidebarClicked:"), ("Settings…", "settingsClicked:"),
                                   (None, None), ("Quit LLM Sidebar Dashboard", "quitClicked:")):
            if title is None:
                menu.addItem_(NSMenuItem.separatorItem())
                continue
            menu_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, action_name, "")
            menu_item.setTarget_(self)
            menu.addItem_(menu_item)
        self.status_item.popUpStatusItemMenu_(menu)

    def showSidebarClicked_(self, sender):
        self.owner.show_sidebar_for_a_few_seconds()

    def settingsClicked_(self, sender):
        self.owner.open_settings()

    def quitClicked_(self, sender):
        self.owner.quit()
