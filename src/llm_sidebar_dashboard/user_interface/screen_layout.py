"""Reads the monitors from AppKit as plain dicts for the core (PLAN.md section 4). Main thread only."""

from AppKit import NSScreen


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
