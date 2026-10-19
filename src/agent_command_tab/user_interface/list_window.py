"""The list in a regular window, for "Show as: Window" in the settings (user request, 2026-10-19).

A titled window you can move, resize and minimize, holding its own list controller and panel
(the same panel as the dropdown's and the sidebar's). It remembers its frame in the settings.
"""

import objc
from AppKit import (NSApp, NSBackingStoreBuffered, NSEvent, NSEventMaskLeftMouseDragged, NSEventMaskLeftMouseUp,
                    NSEventTrackingRunLoopMode, NSEventTypeLeftMouseUp, NSFloatingWindowLevel, NSMakeRect,
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
        self.window.setContentView_(self.list_panel_controller.view())
        self.window.setDelegate_(self)
        return self

    @objc.python_method
    def show(self, saved_frame, takes_keyboard_focus=True, stays_behind_other_windows=False):
        """Shows the window at its saved frame (x, y, width, height) when that is still on a
        connected screen, else in the middle of the main screen: in front with the keyboard, in
        front without it (the app you are typing in keeps it), or, with
        stays_behind_other_windows (at the app's start, also after each automatic restart while
        iTerm2 can't be reached), behind the other windows."""
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
    def apply_settings(self, settings):
        """Keeps the window above other apps' windows while "Always on top" is checked."""
        self.window.setLevel_(NSFloatingWindowLevel if settings["list_window_is_always_on_top"] else NSNormalWindowLevel)

    @objc.python_method
    def follow_the_pointer_until_the_button_comes_up(self, pointer_offset):
        """Moves the window with the pointer, keeping the pointer pointer_offset (x, y) from the
        window's bottom-left corner, until the mouse button comes up: the drag that detached the
        dropdown goes on in this window (user request, 2026-10-19)."""
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
