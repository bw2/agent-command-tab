"""Where the sidebar goes (R5, PLAN.md section 8, "Auto-hide sidebar"). Pure functions, no AppKit.

Screens are dicts {"name": str, "frame": (x, y, width, height), "visible_frame": (...)} in
NSScreen's order, so the first one has the menu bar. Only outer edges are offered: an edge
that borders another monitor can't be touched, because the pointer slides past it.
"""

import collections

SidebarPlacement = collections.namedtuple("SidebarPlacement", ["screen_index", "edge", "is_the_saved_choice"])
SidebarPlacement.__doc__ = "The chosen screen and edge, and whether that is what the settings asked for."

EDGE_DETECTION_THICKNESS = 2


def edges_touch(first_frame, second_frame, edge_of_first):
    """Returns whether second_frame borders first_frame along first_frame's left or right edge."""
    vertical_overlap = (min(first_frame[1] + first_frame[3], second_frame[1] + second_frame[3])
                        - max(first_frame[1], second_frame[1]))
    if vertical_overlap <= 0:
        return False
    if edge_of_first == "right":
        return abs((first_frame[0] + first_frame[2]) - second_frame[0]) < 1
    return abs(first_frame[0] - (second_frame[0] + second_frame[2])) < 1


def find_outer_screen_edges(screen_frames):
    """Returns [(screen index, "left" or "right")] for every edge that no other screen borders."""
    outer_edges = []
    for screen_index, screen_frame in enumerate(screen_frames):
        for edge in ("right", "left"):
            if not any(edges_touch(screen_frame, other_frame, edge)
                       for other_index, other_frame in enumerate(screen_frames) if other_index != screen_index):
                outer_edges.append((screen_index, edge))
    return outer_edges


def choose_sidebar_edge(saved_edge, screens):
    """Returns the SidebarPlacement for the saved edge of the menu-bar monitor, or the nearest
    sensible outer edge.

    Order: the saved edge of the menu-bar monitor; its other edge; otherwise an outer edge on the
    saved edge's side, on the monitor closest to the menu-bar monitor (for example the rightmost
    monitor's right edge when three stand in a row with the menu bar on the middle one and
    "right" saved).

    Returns None when there are no screens.
    """
    if not screens:
        return None
    frames = [screen["frame"] for screen in screens]
    outer_edges = set(find_outer_screen_edges(frames))
    other_edge = "left" if saved_edge == "right" else "right"
    if (0, saved_edge) in outer_edges:
        return SidebarPlacement(0, saved_edge, True)
    if (0, other_edge) in outer_edges:
        return SidebarPlacement(0, other_edge, False)

    def rank_outer_edge(screen_index_and_edge):
        """The saved edge's side first, then the monitor closest to the menu-bar monitor."""
        screen_index, edge = screen_index_and_edge
        frame, menu_bar_frame = frames[screen_index], frames[0]
        return (edge != saved_edge, abs((frame[0] + frame[2] / 2) - (menu_bar_frame[0] + menu_bar_frame[2] / 2)))

    screen_index, edge = min(sorted(outer_edges), key=rank_outer_edge)
    return SidebarPlacement(screen_index, edge, False)


def describe_sidebar_placement_fallback(saved_edge, screens):
    """Returns a sentence for the settings window when the sidebar can't go on the saved edge
    (it borders another monitor), else ""."""
    placement = choose_sidebar_edge(saved_edge, screens)
    if placement is None or placement.is_the_saved_choice:
        return ""
    return (f"The saved edge borders another monitor, so the sidebar is on the {placement.edge} edge "
            f"of {screens[placement.screen_index]['name']}.")


def compute_sidebar_frame(visible_frame, screen_frame, edge, width, is_shown):
    """Returns the sidebar's frame: along the edge of the screen's usable area when shown, and
    just past the screen's full edge when hidden (past a Dock on that side too)."""
    x, y, usable_width, height = visible_frame
    if edge == "right":
        sidebar_x = x + usable_width - width if is_shown else screen_frame[0] + screen_frame[2]
    else:
        sidebar_x = x if is_shown else screen_frame[0] - width
    return (sidebar_x, y, width, height)


def compute_dragged_sidebar_width(width_at_start, pointer_x_at_start, pointer_x, edge, smallest_width, largest_width):
    """Returns the sidebar width while its inner edge is dragged: a sidebar on the right edge grows
    as the pointer moves left, one on the left edge as it moves right."""
    pointer_movement = pointer_x_at_start - pointer_x if edge == "right" else pointer_x - pointer_x_at_start
    return min(max(round(width_at_start + pointer_movement), smallest_width), largest_width)


def pointer_is_at_edge(pointer_location, screen_frame, edge):
    """Returns whether the pointer touches the chosen edge of the screen (its full frame)."""
    pointer_x, pointer_y = pointer_location
    x, y, width, height = screen_frame
    if not y <= pointer_y <= y + height:
        return False
    if edge == "right":
        return pointer_x >= x + width - EDGE_DETECTION_THICKNESS
    return pointer_x <= x + EDGE_DETECTION_THICKNESS - 1
