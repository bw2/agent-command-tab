"""The menu bar item: a colored "C" followed by the number of asking agent sessions (R1 to R3)."""

import objc
from AppKit import (NSApp, NSAttributedString, NSColor, NSEventMaskLeftMouseDown, NSEventMaskLeftMouseUp,
                    NSEventMaskRightMouseUp, NSEventTypeLeftMouseDown, NSEventTypeRightMouseUp, NSFont,
                    NSFontAttributeName, NSFontWeightBold, NSForegroundColorAttributeName, NSMenu, NSMenuItem,
                    NSMinYEdge, NSMutableAttributedString, NSStatusBar, NSVariableStatusItemLength, NSWorkspace)
from Foundation import NSObject, NSProcessInfo

from agent_command_tab.session_status_computation import pick_most_urgent_state
from agent_command_tab.snapshot_data_classes import iterate_agent_session_entries
from agent_command_tab.user_interface.named_colors import build_color_from_name
from agent_command_tab.user_interface.row_text_rendering import DOT_GLYPH_AND_COLOR_BY_STATE

# The icon color of the most urgent state: the rows' dot color for that state, otherwise green.
ICON_COLOR_NAME_BY_MOST_URGENT_STATE = {state: DOT_GLYPH_AND_COLOR_BY_STATE[state][1]
                                        for state in ("asking", "error", "stuck")}


def count_asking_agent_sessions(snapshot):
    """Returns how many agent sessions in the snapshot are asking."""
    return sum(1 for _, _, agent_session_entry in iterate_agent_session_entries(snapshot)
               if agent_session_entry.status == "asking")


def choose_icon_color_name(snapshot):
    """Returns the icon's color: light blue while any agent session is asking, else red if any has
    an error, else orange if any is stuck, otherwise green."""
    states = [agent_session_entry.status for _, _, agent_session_entry in iterate_agent_session_entries(snapshot)]
    return ICON_COLOR_NAME_BY_MOST_URGENT_STATE.get(pick_most_urgent_state(states), "green")


def choose_what_a_click_on_the_icon_does_to_the_dropdown(dropdown_is_shown, dropdown_closed_at, button_pressed_at):
    """Returns "close", "open" or "nothing" for a click on the menu bar icon, at its mouse-up.

    The dropdown closes by itself as the mouse button goes down outside it, on the icon too,
    before the click's mouse-up arrives. A dropdown that closed during this click stays closed,
    rather than opening again, so every click toggles it (user request, 2026-10-28); before, a
    slow click opened it again, and only a quick one, while it was still closing, closed it.

    Args:
        dropdown_is_shown: Whether the dropdown is open (or still closing).
        dropdown_closed_at: When it last began to close, in seconds since the Mac started (an
            event's timestamp), or 0.
        button_pressed_at: The click's mouse-down timestamp, or 0 when that wasn't seen (the
            click then opens it, as before).
    """
    if dropdown_is_shown:
        return "close"
    if button_pressed_at and dropdown_closed_at >= button_pressed_at:
        return "nothing"
    return "open"


class MenuBarStatusItemController(NSObject):
    """Owns the NSStatusItem. A left click shows the list (the owner decides how: toggle_popover
    for the dropdown); a right click shows a small menu.

    The owner (the app controller) has menu_bar_icon_clicked(), show_the_list_as(show_as_choice),
    open_settings(), quit() and note_application_in_front_before_dropdown(application).
    """

    def initWithPopover_owner_(self, popover, owner):
        self = objc.super(MenuBarStatusItemController, self).init()
        if self is None:
            return None
        self.popover = popover
        self.owner = owner
        self.asking_count = 0
        self.icon_color_name = "green"
        self.has_connection_problem = False
        # For choose_what_a_click_on_the_icon_does_to_the_dropdown: event timestamps, 0 until seen.
        self.dropdown_closed_at = 0
        self.left_button_pressed_at = 0
        self.status_item = NSStatusBar.systemStatusBar().statusItemWithLength_(NSVariableStatusItemLength)
        button = self.status_item.button()
        button.setTarget_(self)
        button.setAction_("statusItemClicked:")
        # The mouse-down only notes when the click began; the mouse-up acts.
        button.sendActionOn_(NSEventMaskLeftMouseDown | NSEventMaskLeftMouseUp | NSEventMaskRightMouseUp)
        self.redraw()
        return self

    @objc.python_method
    def show_snapshot(self, snapshot):
        """Shows how many agent sessions are asking, in the color of the most urgent agent session."""
        self.asking_count = count_asking_agent_sessions(snapshot)
        self.icon_color_name = choose_icon_color_name(snapshot)
        self.has_connection_problem = False
        self.redraw()

    @objc.python_method
    def show_connection_problem(self):
        """Draws the "C" in gray while iTerm2 can't be reached."""
        self.has_connection_problem = True
        self.redraw()

    @objc.python_method
    def redraw(self):
        """Draws the "C" in the most urgent agent session's color (gray during a connection problem),
        plus the asking count in light blue (the color of "asking" everywhere)."""
        letter_color = (NSColor.secondaryLabelColor() if self.has_connection_problem
                        else build_color_from_name(self.icon_color_name, NSColor.systemGreenColor))
        font = NSFont.menuBarFontOfSize_(0)
        title = NSMutableAttributedString.alloc().init()
        title.appendAttributedString_(NSAttributedString.alloc().initWithString_attributes_(
            "C", {NSFontAttributeName: NSFont.systemFontOfSize_weight_(font.pointSize(), NSFontWeightBold),
                  NSForegroundColorAttributeName: letter_color}))
        if self.asking_count and not self.has_connection_problem:
            title.appendAttributedString_(NSAttributedString.alloc().initWithString_attributes_(
                f" {self.asking_count}", {NSFontAttributeName: font, NSForegroundColorAttributeName: build_color_from_name(
                    DOT_GLYPH_AND_COLOR_BY_STATE["asking"][1])}))
        self.status_item.button().setAttributedTitle_(title)

    def statusItemClicked_(self, sender):
        event = NSApp.currentEvent()
        if event.type() == NSEventTypeLeftMouseDown:
            self.left_button_pressed_at = event.timestamp()
        elif event.type() == NSEventTypeRightMouseUp:
            self.show_right_click_menu()
        else:
            self.owner.menu_bar_icon_clicked()

    @objc.python_method
    def note_dropdown_closing(self):
        """The dropdown began to close (the app controller is its delegate): noted as the time of
        the event that closed it, often the mouse-down of a click on the icon, so it is in the
        same clock as the click; without an event (a timer), as the time since the Mac started,
        which event timestamps count too."""
        event = NSApp.currentEvent()
        self.dropdown_closed_at = event.timestamp() if event is not None else NSProcessInfo.processInfo().systemUptime()

    @objc.python_method
    def find_icon_frame_on_screen(self):
        """Returns the icon's frame in screen coordinates (its status bar window's frame)."""
        return self.status_item.button().window().frame()

    @objc.python_method
    def highlight(self, is_highlighted):
        """Highlights the icon as a drop target while the list window is dragged onto it."""
        if bool(self.status_item.button().isHighlighted()) != is_highlighted:
            self.status_item.button().highlight_(is_highlighted)

    @objc.python_method
    def toggle_popover(self):
        """Opens the dropdown, or closes it when it is open or closed during this click."""
        what_to_do = choose_what_a_click_on_the_icon_does_to_the_dropdown(
            self.popover.isShown(), self.dropdown_closed_at, self.left_button_pressed_at)
        if what_to_do == "close":
            self.popover.performClose_(None)
        elif what_to_do == "open":
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
        # Each way to show the list, whatever "Show as" is, without changing it (user request,
        # 2026-10-28; "Show the sidebar" alone until then), in sentence case as the other menus are.
        for title, action_name in (("Show popup", "showPopupClicked:"), ("Show sidebar", "showSidebarClicked:"),
                                   ("Show window", "showWindowClicked:"), (None, None), ("Settings…", "settingsClicked:"),
                                   (None, None), ("Quit AgentCommandTab", "quitClicked:")):
            if title is None:
                menu.addItem_(NSMenuItem.separatorItem())
                continue
            menu_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, action_name, "")
            menu_item.setTarget_(self)
            menu.addItem_(menu_item)
        self.status_item.popUpStatusItemMenu_(menu)

    def showPopupClicked_(self, sender):
        self.owner.show_the_list_as("popup")

    def showSidebarClicked_(self, sender):
        self.owner.show_the_list_as("sidebar")

    def showWindowClicked_(self, sender):
        self.owner.show_the_list_as("window")

    def settingsClicked_(self, sender):
        self.owner.open_settings()

    def quitClicked_(self, sender):
        self.owner.quit()
