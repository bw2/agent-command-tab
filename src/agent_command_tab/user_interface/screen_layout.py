"""Reads the monitors from AppKit as plain dicts for the core, checks saved
window frames against them, and saves the frames of the windows that keep one. Main thread only."""

import objc
from AppKit import NSIntersectsRect, NSMakeRect, NSScreen
from Foundation import NSObject


def list_screen_frames():
    """Returns each connected screen's frame as (x, y, width, height)."""
    return [(screen.frame().origin.x, screen.frame().origin.y, screen.frame().size.width, screen.frame().size.height)
            for screen in NSScreen.screens()]


def frame_is_on_a_screen(frame, screen_frames):
    """Returns whether any part of frame (x, y, width, height) lies on one of the screens, so a
    window saved on a monitor that is gone now opens where it can be seen."""
    return any(NSIntersectsRect(NSMakeRect(*frame), NSMakeRect(*screen_frame)) for screen_frame in screen_frames)


def convert_rect_to_frame_list(rect):
    """Returns an NSRect as [x, y, width, height], the form window frames are saved in."""
    return [rect.origin.x, rect.origin.y, rect.size.width, rect.size.height]


def grow_frame_to_smallest_size(frame, smallest_size):
    """Returns frame [x, y, width, height] made at least smallest_size (width, height), keeping
    its top-left corner (y grows upward), so the settings accept it when they are loaded again."""
    x, y, width, height = frame
    new_width, new_height = max(width, smallest_size[0]), max(height, smallest_size[1])
    return [x, y - (new_height - height), new_width, new_height]


def compute_list_window_content_rect_for_a_dragged_sidebar(sidebar_frame, pointer, content_size):
    """Returns the content rect [x, y, width, height] of the list window that the sidebar turns
    into when its top bar is dragged: content_size, with its top level with the sidebar's top and
    the pointer as far across it, in proportion, as across the sidebar, so the pointer stays on
    the top bar, as far below the top as before.

    Args:
        sidebar_frame: The sidebar's frame [x, y, width, height] on the screen (y grows upward).
        pointer: Where the drag started (x, y), on the sidebar's top bar.
        content_size: The list window's content size (width, height).
    """
    sidebar_x, sidebar_y, sidebar_width, sidebar_height = sidebar_frame
    width, height = content_size
    return [pointer[0] - (pointer[0] - sidebar_x) / sidebar_width * width, sidebar_y + sidebar_height - height, width, height]


class WindowFrameSavingController(NSObject):
    """The base of the controllers of windows whose frame the settings keep (the list window and
    the settings window). As the window's delegate it reports the frame to the owner's
    window_frame_changed(frame_setting_name, frame) after each move and resize; a subclass sets
    frame_setting_name (a settings_store.SMALLEST_WINDOW_SIZES name) and self.owner and self.window."""

    frame_setting_name = None

    @objc.python_method
    def report_frame(self):
        self.owner.window_frame_changed(self.frame_setting_name, convert_rect_to_frame_list(self.window.frame()))

    def windowDidMove_(self, notification):
        self.report_frame()

    def windowDidEndLiveResize_(self, notification):
        self.report_frame()


def place_window_at_saved_frame_or_center(window, saved_frame):
    """Puts a window at its saved frame [x, y, width, height] while that is still on a connected
    screen, else in the middle of the main screen."""
    if saved_frame is not None and frame_is_on_a_screen(saved_frame, list_screen_frames()):
        window.setFrame_display_(NSMakeRect(*saved_frame), True)
    else:
        window.center()


def read_monitors_from_screens():
    """Returns the monitors as plain dicts, menu-bar monitor first (NSScreen's order)."""
    monitors = []
    for screen in NSScreen.screens():
        frame = screen.frame()
        visible_frame = screen.visibleFrame()
        monitors.append({
            "monitor_id": str(screen.deviceDescription()["NSScreenNumber"]),
            "name": screen.localizedName(),
            "frame": (frame.origin.x, frame.origin.y, frame.size.width, frame.size.height),
            "visible_frame": (visible_frame.origin.x, visible_frame.origin.y, visible_frame.size.width,
                              visible_frame.size.height),
        })
    return monitors
