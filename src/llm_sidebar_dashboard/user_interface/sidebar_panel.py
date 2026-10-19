"""The sidebar at the screen edge (R4 to R6, PLAN.md section 8, "Auto-hide sidebar").

A non-activating panel that floats above windows on every desktop, including full-screen
ones, and takes keyboard focus only for typing (a rename). Three modes (settings_store.SIDEBAR_MODES):
- auto_hide: slides in when the pointer touches the chosen edge and slides out half a second
  after the pointer leaves, unless a rename, drag or menu is open;
- floating: always visible, over the windows beneath it;
- docked: always visible, and the core keeps iTerm2 windows on that monitor out of its area
  (macOS lets no app reserve screen space for other apps, so other apps' windows can still
  reach under it).
Its width can be dragged at its inner edge, where the pointer turns into left-right arrows. A
borderless window gets no resizing edges from macOS, so a thin strip of our own does this.

No window sits on the edge while the sidebar is hidden: a global mouse-moved monitor (which
needs no Accessibility permission and only watches) tests the pointer position.
"""

import ctypes
import time

import objc
from AppKit import (NSAnimationContext, NSBackingStoreBuffered, NSCursor, NSEvent, NSEventMaskMouseMoved,
                    NSMakeRect, NSPanel, NSStatusWindowLevel, NSTrackingActiveAlways, NSTrackingArea,
                    NSTrackingCursorUpdate, NSTrackingInVisibleRect, NSTrackingMouseEnteredAndExited,
                    NSTrackingMouseMoved, NSView, NSViewHeightSizable, NSViewMaxXMargin, NSViewMinXMargin,
                    NSViewWidthSizable, NSVisualEffectBlendingModeBehindWindow, NSVisualEffectMaterialSidebar,
                    NSVisualEffectStateActive, NSVisualEffectView, NSWindowCollectionBehaviorCanJoinAllSpaces,
                    NSWindowCollectionBehaviorFullScreenAuxiliary, NSWindowCollectionBehaviorIgnoresCycle,
                    NSWindowCollectionBehaviorStationary, NSWindowStyleMaskBorderless,
                    NSWindowStyleMaskNonactivatingPanel)
from Foundation import NSObject, NSTimer

from llm_sidebar_dashboard.user_interface.agent_session_outline_view import AgentSessionListController
from llm_sidebar_dashboard.user_interface.popover_container import ListPanelController
from llm_sidebar_dashboard.user_interface.screen_layout import read_monitors_from_screens
from llm_sidebar_dashboard.user_interface.sidebar_placement import (choose_sidebar_edge, compute_dragged_sidebar_width,
                                                                    compute_sidebar_frame, pointer_is_at_edge)

SECONDS_BEFORE_HIDING = 0.5
SECONDS_SHOWN_FROM_THE_MENU = 5
SLIDE_ANIMATION_SECONDS = 0.15
SMALLEST_WIDTH = 200
LARGEST_WIDTH = 1200
WIDTH_DRAG_STRIP_THICKNESS = 6
CF_STRING_ENCODING_UTF8 = 0x08000100


def allow_pointer_changes_while_another_app_is_in_front():
    """Lets the dashboard change the pointer while another app is the active one; returns whether that worked.

    macOS ignores pointer changes from an app that isn't active, and the sidebar never makes the
    dashboard active, so the inner edge's left-right arrows would never show. This turns on the
    window server's undocumented "SetsCursorInBackground" setting, as other menu bar utilities
    do. Should a macOS version drop it, the edge still drags, only without the arrows.
    """
    try:
        core_foundation = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
        core_graphics = ctypes.CDLL("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")
        core_foundation.CFStringCreateWithCString.restype = ctypes.c_void_p
        core_foundation.CFStringCreateWithCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
        core_graphics._CGSDefaultConnection.restype = ctypes.c_int
        core_graphics.CGSSetConnectionProperty.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p]
        core_graphics.CGSSetConnectionProperty.restype = ctypes.c_int
        setting_name = core_foundation.CFStringCreateWithCString(None, b"SetsCursorInBackground", CF_STRING_ENCODING_UTF8)
        connection = core_graphics._CGSDefaultConnection()
        return core_graphics.CGSSetConnectionProperty(
            connection, connection, setting_name, ctypes.c_void_p.in_dll(core_foundation, "kCFBooleanTrue")) == 0
    except (OSError, AttributeError, ValueError):
        return False


class SidebarWindow(NSPanel):
    """A borderless, non-activating panel that may become key, so a rename field can take typing."""

    def canBecomeKeyWindow(self):
        return True

    def canBecomeMainWindow(self):
        return False


class SidebarWidthDragStripView(NSView):
    """A thin strip along the sidebar's inner edge: shows the left-right arrows pointer and drags the width.

    Its tracking area stays active while another app is in front, since the sidebar never
    activates the dashboard (allow_pointer_changes_while_another_app_is_in_front lets it set the
    pointer then). Pointer positions are read in screen coordinates, because the strip moves with
    the window while the width changes.
    """

    def initWithFrame_controller_(self, frame, controller):
        self = objc.super(SidebarWidthDragStripView, self).initWithFrame_(frame)
        if self is None:
            return None
        self.controller = controller
        self.addTrackingArea_(NSTrackingArea.alloc().initWithRect_options_owner_userInfo_(
            frame, NSTrackingMouseEnteredAndExited | NSTrackingMouseMoved | NSTrackingCursorUpdate
            | NSTrackingActiveAlways | NSTrackingInVisibleRect, self, None))
        return self

    def acceptsFirstMouse_(self, event):
        return True  # the first click starts a drag even though the dashboard isn't the active app

    def cursorUpdate_(self, event):
        NSCursor.resizeLeftRightCursor().set()

    def mouseEntered_(self, event):
        NSCursor.resizeLeftRightCursor().set()

    def mouseMoved_(self, event):
        NSCursor.resizeLeftRightCursor().set()

    def mouseExited_(self, event):
        if not self.controller.width_drag_is_in_progress():
            NSCursor.arrowCursor().set()

    def mouseDown_(self, event):
        self.controller.start_width_drag(NSEvent.mouseLocation().x)

    def mouseDragged_(self, event):
        NSCursor.resizeLeftRightCursor().set()
        self.controller.continue_width_drag(NSEvent.mouseLocation().x)

    def mouseUp_(self, event):
        self.controller.finish_width_drag()


class SidebarController(NSObject):
    """Owns the sidebar window, its own list controller, and the edge tracking.

    The owner is the app controller (see AgentSessionListController for the methods it has);
    the sidebar also calls its sidebar_width_changed(width) after the user drags the width, and
    reserve_screen_area(frame or None) whenever the docked area starts, moves or ends.
    """

    def initWithOwner_(self, owner):
        self = objc.super(SidebarController, self).init()
        if self is None:
            return None
        self.owner = owner
        self.settings = None
        self.placement = None
        self.screens = []
        self.is_shown = False
        self.hide_timer = None
        self.keep_shown_until = 0  # set by "Show the sidebar" in the menu bar item's menu
        self.shown_from_the_menu = False  # out because of that menu item (until it next hides)
        self.mouse_monitors = []
        self.width_drag_start = None  # (pointer x, width) while the inner edge is dragged
        self.list_controller = AgentSessionListController.alloc().initWithOwner_(owner)
        self.list_controller.width_is_set_by_the_user = True
        self.list_panel_controller = ListPanelController.alloc().initWithListController_(self.list_controller)
        self.window = self.build_window()
        allow_pointer_changes_while_another_app_is_in_front()  # for the width strip's left-right arrows
        return self

    @objc.python_method
    def build_window(self):
        window = SidebarWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, 320, 600), NSWindowStyleMaskBorderless | NSWindowStyleMaskNonactivatingPanel,
            NSBackingStoreBuffered, False)
        window.setFloatingPanel_(True)  # before setLevel_: it resets the level to the floating level
        window.setLevel_(NSStatusWindowLevel)
        window.setCollectionBehavior_(NSWindowCollectionBehaviorCanJoinAllSpaces | NSWindowCollectionBehaviorStationary
                                      | NSWindowCollectionBehaviorFullScreenAuxiliary | NSWindowCollectionBehaviorIgnoresCycle)
        window.setHidesOnDeactivate_(False)
        window.setBecomesKeyOnlyIfNeeded_(True)  # clicks on rows don't take keyboard focus (R6)
        window.setDelegate_(self)  # windowDidResignKey_
        window.setHasShadow_(True)
        background_view = NSVisualEffectView.alloc().initWithFrame_(NSMakeRect(0, 0, 320, 600))
        background_view.setMaterial_(NSVisualEffectMaterialSidebar)
        background_view.setBlendingMode_(NSVisualEffectBlendingModeBehindWindow)
        background_view.setState_(NSVisualEffectStateActive)
        panel_view = self.list_panel_controller.view()
        panel_view.setFrame_(background_view.bounds())
        panel_view.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
        background_view.addSubview_(panel_view)
        # Added last so it lies above the list; update_placement moves it to the inner edge.
        self.width_drag_strip = SidebarWidthDragStripView.alloc().initWithFrame_controller_(
            NSMakeRect(0, 0, WIDTH_DRAG_STRIP_THICKNESS, 600), self)
        background_view.addSubview_(self.width_drag_strip)
        window.setContentView_(background_view)
        # The list's own pointer areas reset the strip's left-right arrows to the plain arrow
        # right after the pointer enters the strip (a quick pointer often lands there only once).
        window.disableCursorRects()
        return window

    def windowDidResignKey_(self, notification):
        # A click in another window (iTerm2, say) ends an open rename without renaming, as closing
        # the dropdown does; AppKit itself leaves the field open, and the list would stop updating.
        self.list_controller.cancel_rename()

    @objc.python_method
    def move_width_drag_strip_to_inner_edge(self, edge):
        """Puts the strip on the side facing the screen's middle: left for a sidebar on the right edge."""
        bounds = self.window.contentView().bounds()
        strip_x = 0 if edge == "right" else bounds.size.width - WIDTH_DRAG_STRIP_THICKNESS
        self.width_drag_strip.setFrame_(NSMakeRect(strip_x, 0, WIDTH_DRAG_STRIP_THICKNESS, bounds.size.height))
        self.width_drag_strip.setAutoresizingMask_(
            NSViewHeightSizable | (NSViewMaxXMargin if edge == "right" else NSViewMinXMargin))

    # ----- settings and screens -----

    @objc.python_method
    def apply_settings(self, settings):
        """Shows, hides or moves the sidebar to match the settings (called on every settings change)."""
        self.settings = settings
        self.update_placement()

    @objc.python_method
    def screen_layout_changed(self):
        """Re-checks the edge when monitors are connected, disconnected or rearranged."""
        self.update_placement()

    @objc.python_method
    def update_placement(self):
        if self.settings is None:
            return
        self.screens = read_monitors_from_screens()
        self.placement = choose_sidebar_edge(self.settings["sidebar_edge"], self.screens)
        if self.placement is None:
            self.stop_tracking_the_pointer()
            self.cancel_hide_timer()
            self.window.orderOut_(None)
            self.is_shown = False
            self.owner.reserve_screen_area(None)
            return
        visible_frame = self.screens[self.placement.screen_index]["visible_frame"]
        self.move_width_drag_strip_to_inner_edge(self.placement.edge)
        if self.settings["show_as"] != "sidebar":
            # The list shows as a popup or a window: the sidebar is parked just past its edge, so
            # "Show the sidebar" slides it in from there.
            # While that keeps it out, a settings change (a width dragged meanwhile) leaves it out.
            self.stop_tracking_the_pointer()
            self.owner.reserve_screen_area(None)
            self.set_shown(self.is_shown and self.shown_from_the_menu, animate=False)
            return
        if self.settings["sidebar_mode"] == "auto_hide":
            self.start_tracking_the_pointer()
            # Keep the current visibility: a settings change (for example a width just dragged
            # with the pointer still on the sidebar) mustn't make it vanish; the pointer decides.
            self.set_shown(self.is_shown, animate=False)
        else:
            self.stop_tracking_the_pointer()
            self.cancel_hide_timer()
            self.set_shown(True, animate=False)
        # Docked: iTerm2 windows on this monitor are kept out of the sidebar's area.
        if self.settings["sidebar_mode"] == "docked":
            screen = self.screens[self.placement.screen_index]
            self.owner.reserve_screen_area({
                "frame": compute_sidebar_frame(visible_frame, screen["frame"], self.placement.edge,
                                               self.settings["sidebar_width"], True),
                "edge": self.placement.edge,
                "monitor_visible_frame": visible_frame,
            })
        else:
            self.owner.reserve_screen_area(None)

    # ----- showing and hiding -----

    @objc.python_method
    def set_shown(self, is_shown, animate=True):
        """Slides the sidebar in or out. Once out of sight it is ordered out too, which gives up
        keyboard focus (after a rename) and lets its list skip redraws while hidden."""
        screen = self.screens[self.placement.screen_index]
        frame = compute_sidebar_frame(screen["visible_frame"], screen["frame"], self.placement.edge,
                                      self.settings["sidebar_width"], is_shown)
        was_on_screen = self.window.isVisible()
        self.is_shown = is_shown
        if not is_shown:
            self.shown_from_the_menu = False
        if is_shown:
            self.window.orderFrontRegardless()
            if not was_on_screen:
                self.list_controller.prepare_to_appear()  # the list skips redraws while hidden

        def order_out_unless_shown_again():
            if not self.is_shown:
                self.window.orderOut_(None)

        if animate:
            def slide(context):
                context.setDuration_(SLIDE_ANIMATION_SECONDS)
                self.window.animator().setFrame_display_(NSMakeRect(*frame), True)
            NSAnimationContext.runAnimationGroup_completionHandler_(slide, order_out_unless_shown_again)
        else:
            self.window.setFrame_display_(NSMakeRect(*frame), True)
            order_out_unless_shown_again()

    @objc.python_method
    def show_for_a_few_seconds(self):
        """The menu bar item's "Show the sidebar" (user request, milestone 2), and a click on the
        menu bar icon while the list shows as a sidebar: shows the sidebar, also while the list
        shows as a popup or a window, for SECONDS_SHOWN_FROM_THE_MENU, then hides it once the
        pointer isn't on it. A sidebar that is always shown stays as it is."""
        if self.settings is None or self.placement is None:
            return
        if self.settings["show_as"] == "sidebar" and self.settings["sidebar_mode"] != "auto_hide":
            return
        self.keep_shown_until = time.time() + SECONDS_SHOWN_FROM_THE_MENU
        self.set_shown(True)
        self.shown_from_the_menu = True
        self.check_whether_to_hide_in(SECONDS_SHOWN_FROM_THE_MENU)

    @objc.python_method
    def something_belonging_to_the_sidebar_is_open(self):
        """A rename, drag or right-click menu keeps the sidebar in place (PLAN.md section 8)."""
        return (self.list_controller.rename_in_progress or self.list_controller.drag_in_progress
                or self.list_controller.menu_is_open or self.width_drag_is_in_progress())

    # ----- pointer tracking -----

    @objc.python_method
    def start_tracking_the_pointer(self):
        if self.mouse_monitors:
            return
        self.mouse_monitors.append(NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(
            NSEventMaskMouseMoved, lambda event: self.pointer_moved()))

        def handle_local_event(event):
            self.pointer_moved()
            return event
        self.mouse_monitors.append(NSEvent.addLocalMonitorForEventsMatchingMask_handler_(
            NSEventMaskMouseMoved, handle_local_event))

    @objc.python_method
    def stop_tracking_the_pointer(self):
        """Removes the mouse monitors; a pending hide check stays (it hides a sidebar shown from the menu)."""
        for mouse_monitor in self.mouse_monitors:
            NSEvent.removeMonitor_(mouse_monitor)
        self.mouse_monitors = []

    @objc.python_method
    def pointer_moved(self):
        if self.settings is None or self.settings["sidebar_mode"] != "auto_hide" or self.placement is None:
            return
        pointer = NSEvent.mouseLocation()
        if not self.is_shown:
            if pointer_is_at_edge((pointer.x, pointer.y), self.screens[self.placement.screen_index]["frame"],
                                  self.placement.edge):
                self.set_shown(True)
            return
        if self.pointer_is_over_sidebar():
            self.cancel_hide_timer()
        elif self.hide_timer is None:
            self.check_whether_to_hide_in(SECONDS_BEFORE_HIDING)

    @objc.python_method
    def pointer_is_over_sidebar(self):
        """Counts the strip between the sidebar and the screen's edge (a Dock on that side) as
        part of the sidebar, since the pointer that showed it touched that edge."""
        pointer = NSEvent.mouseLocation()
        frame = self.window.frame()
        screen_frame = self.screens[self.placement.screen_index]["frame"]
        left, right = frame.origin.x - 1, frame.origin.x + frame.size.width + 1
        if self.placement.edge == "right":
            right = max(right, screen_frame[0] + screen_frame[2])
        else:
            left = min(left, screen_frame[0])
        return left <= pointer.x <= right and frame.origin.y <= pointer.y <= frame.origin.y + frame.size.height

    @objc.python_method
    def check_whether_to_hide_in(self, seconds):
        self.cancel_hide_timer()
        self.hide_timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            seconds, self, "hideTimerFired:", None, False)

    def hideTimerFired_(self, timer):
        """Hides the sidebar unless it was shown from the menu less than SECONDS_SHOWN_FROM_THE_MENU
        ago, the pointer is on it, or anything of it is open; otherwise checks again later
        (without pointer tracking, a sidebar shown from the menu while turned off relies on this)."""
        self.hide_timer = None
        seconds_still_shown_from_the_menu = self.keep_shown_until - time.time()
        if seconds_still_shown_from_the_menu > 0:
            self.check_whether_to_hide_in(seconds_still_shown_from_the_menu)
        elif self.something_belonging_to_the_sidebar_is_open() or self.pointer_is_over_sidebar():
            self.check_whether_to_hide_in(SECONDS_BEFORE_HIDING)
        else:
            self.set_shown(False)

    @objc.python_method
    def cancel_hide_timer(self):
        if self.hide_timer is not None:
            self.hide_timer.invalidate()
            self.hide_timer = None

    # ----- width -----

    @objc.python_method
    def width_drag_is_in_progress(self):
        return self.width_drag_start is not None

    @objc.python_method
    def start_width_drag(self, pointer_x):
        if self.placement is not None and self.is_shown:
            self.width_drag_start = (pointer_x, self.window.frame().size.width)

    @objc.python_method
    def continue_width_drag(self, pointer_x):
        """Resizes the window under the pointer; the new width is saved when the drag ends."""
        if self.width_drag_start is None:
            return
        pointer_x_at_start, width_at_start = self.width_drag_start
        width = compute_dragged_sidebar_width(width_at_start, pointer_x_at_start, pointer_x, self.placement.edge,
                                              SMALLEST_WIDTH, LARGEST_WIDTH)
        screen = self.screens[self.placement.screen_index]
        frame = compute_sidebar_frame(screen["visible_frame"], screen["frame"], self.placement.edge, width, True)
        self.window.setFrame_display_(NSMakeRect(*frame), True)

    @objc.python_method
    def finish_width_drag(self):
        if self.width_drag_start is None:
            return
        self.width_drag_start = None
        width = round(self.window.frame().size.width)
        if width != self.settings["sidebar_width"]:
            self.owner.sidebar_width_changed(width)  # saves it; a docked sidebar then reserves the new strip
