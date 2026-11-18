"""The list in a regular window, for "Show as: Window" in the settings.

A titled window you can move, resize and minimize, holding its own list controller and panel
(the same panel as the dropdown's and the sidebar's). It remembers its frame in the settings.
"""

import objc
from AppKit import (NSApp, NSBackingStoreBuffered, NSEvent, NSEventMaskLeftMouseDragged, NSEventMaskLeftMouseUp,
                    NSEventTrackingRunLoopMode, NSEventTypeLeftMouseUp, NSFloatingWindowLevel, NSIntersectsRect, NSMakeRect,
                    NSNormalWindowLevel, NSWindow, NSWindowStyleMaskClosable, NSWindowStyleMaskMiniaturizable,
                    NSWindowStyleMaskResizable, NSWindowStyleMaskTitled)
from Foundation import NSDate
from PyObjCTools import AppHelper

from agent_command_tab.settings_store import SMALLEST_WINDOW_SIZES
from agent_command_tab.user_interface.agent_session_outline_view import AgentSessionListController
from agent_command_tab.user_interface.popover_container import ListPanelController
from agent_command_tab.user_interface.screen_layout import (WindowFrameSavingController,
                                                            place_window_at_saved_frame_or_center)

FIRST_LIST_WINDOW_SIZE = (480, 560)


class ListWindowController(WindowFrameSavingController):
    """Owns the list window, its own list controller and panel.

    The owner is the app controller (see AgentSessionListController for the methods it has); the
    window also calls its window_frame_changed("list_window_frame", frame) after it is moved or resized,
    list_window_drag_started(), list_window_is_being_dragged() and list_window_drag_ended() around
    a drag (dropped on the menu bar icon, the window turns back into the dropdown), and
    list_window_closed() after its close button hides it.
    """

    frame_setting_name = "list_window_frame"

    def initWithOwner_(self, owner):
        self = objc.super(ListWindowController, self).init()
        if self is None:
            return None
        self.owner = owner
        self.list_controller = AgentSessionListController.alloc().initWithOwner_(owner)
        self.list_controller.width_is_set_by_the_user = True  # a narrow window drops columns, as the sidebar does
        self.list_panel_controller = ListPanelController.alloc().initWithListController_(self.list_controller)
        self.window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, *FIRST_LIST_WINDOW_SIZE),
            NSWindowStyleMaskTitled | NSWindowStyleMaskClosable | NSWindowStyleMaskMiniaturizable | NSWindowStyleMaskResizable,
            NSBackingStoreBuffered, False)
        self.window.setTitle_("AgentCommandTab")
        self.window.setReleasedWhenClosed_(False)  # closing only hides it; the menu bar icon brings it back
        self.window.setMinSize_(SMALLEST_WINDOW_SIZES["list_window_frame"])
        self.window.setAllowsToolTipsWhenApplicationIsInactive_(True)  # hover texts while another app is in front
        self.window.setContentView_(self.list_panel_controller.view())
        self.window.setDelegate_(self)
        return self

    @objc.python_method
    def show(self, saved_frame, takes_keyboard_focus=True, stays_behind_other_windows=False):
        """Shows the window at its saved frame (x, y, width, height) when that is still on a
        connected screen, else in the middle of the main screen: in front with the keyboard, in
        front without it (the app you are typing in keeps it), or, with
        stays_behind_other_windows (at the app's start, also after each automatic restart while
        iTerm2 can't be reached), behind the other windows. "Always on top" counts over that:
        macOS orders a window only among those of its level, so one always on top (apply_settings)
        goes behind the other windows of that level and stays above every normal window."""
        if not self.window.isVisible():
            place_window_at_saved_frame_or_center(self.window, saved_frame)
            self.list_controller.prepare_to_appear()  # the list skips redraws while hidden
        if stays_behind_other_windows:
            self.window.orderBack_(None)
        elif takes_keyboard_focus:
            NSApp.activateIgnoringOtherApps_(True)
            self.window.makeKeyAndOrderFront_(None)
        else:
            self.window.orderFrontRegardless()

    @objc.python_method
    def hide(self):
        self.window.orderOut_(None)

    @objc.python_method
    def is_in_front(self):
        """Whether the window shows in front of other windows: it is on the desktop (Space) in
        view (a minimized window isn't visible), and it has the keyboard, or is always on top
        with none of the app's other windows over it."""
        return (self.window.isVisible() and self.window.isOnActiveSpace()
                and (self.window.isKeyWindow()
                     or (self.window.level() > NSNormalWindowLevel and not self.is_under_another_window_of_the_app())))

    @objc.python_method
    def is_under_another_window_of_the_app(self):
        """Whether another of the app's titled windows lies in front of the list window and over
        some part of it: the settings window and the Saved sessions window open at the level of a
        list window that is always on top, in front of it. NSApp.orderedWindows lists the app's
        windows from the front one back, closed ones too, without its panels (the sidebar, the
        dropdown, tooltips); the untitled ones among them are macOS's own, for the menu bar icon
        and for open menus, and don't count."""
        list_window_frame = self.window.frame()
        for window in NSApp.orderedWindows():
            if window is self.window:
                return False
            if (window.styleMask() & NSWindowStyleMaskTitled and window.isVisible() and window.isOnActiveSpace()
                    and NSIntersectsRect(window.frame(), list_window_frame)):
                return True
        return False

    @objc.python_method
    def apply_settings(self, settings):
        """Keeps the window above other apps' windows while "Always on top" is checked."""
        self.window.setLevel_(NSFloatingWindowLevel if settings["list_window_is_always_on_top"] else NSNormalWindowLevel)

    @objc.python_method
    def follow_the_pointer_until_the_button_comes_up(self, pointer_offset):
        """Moves the window with the pointer, keeping the pointer pointer_offset (x, y) from the
        window's bottom-left corner, until the mouse button comes up: the drag that detached the
        dropdown goes on in this window."""
        while True:
            event = NSApp.nextEventMatchingMask_untilDate_inMode_dequeue_(
                NSEventMaskLeftMouseDragged | NSEventMaskLeftMouseUp, NSDate.distantFuture(), NSEventTrackingRunLoopMode, True)
            pointer = NSEvent.mouseLocation()
            self.window.setFrameOrigin_((pointer.x - pointer_offset[0], pointer.y - pointer_offset[1]))
            self.owner.list_window_is_being_dragged()
            if event.type() == NSEventTypeLeftMouseUp:
                break
        self.report_frame()
        self.owner.list_window_drag_ended()

    def windowWillMove_(self, notification):
        # Its title bar is being dragged: the owner watches whether it is dropped on the menu bar icon.
        self.owner.list_window_drag_started()

    def windowWillClose_(self, notification):
        AppHelper.callAfter(self.owner.list_window_closed)  # once it is no longer visible

    def windowDidResignKey_(self, notification):
        # A click in another app ends an open rename without renaming, as in the sidebar; the list
        # would stop updating while the field stays open.
        self.list_controller.cancel_rename()
        # The search box lets go of the keyboard too, as in the sidebar. Left with it (after Return
        # showed a tab the search found, or Esc hid the window), it would take the keys the next
        # time the window has the keyboard, and the rows' shortcuts would type into it.
        self.window.makeFirstResponder_(self.list_controller.outline_view)
