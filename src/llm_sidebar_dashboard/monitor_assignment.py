"""Which monitor each window is on (R31). Frames are (x, y, width, height) in one shared coordinate system."""


def compute_overlap_area(first_frame, second_frame):
    """Returns the area where two (x, y, width, height) rectangles overlap (0 when they don't)."""
    overlap_width = min(first_frame[0] + first_frame[2], second_frame[0] + second_frame[2]) - max(first_frame[0], second_frame[0])
    overlap_height = min(first_frame[1] + first_frame[3], second_frame[1] + second_frame[3]) - max(first_frame[1], second_frame[1])
    return max(overlap_width, 0) * max(overlap_height, 0)


def frames_are_within_tolerance(first_frame, second_frame, tolerance_points):
    """Returns whether two (x, y, width, height) frames differ by at most tolerance_points in each value."""
    return all(abs(first - second) <= tolerance_points for first, second in zip(first_frame, second_frame))


def compute_squared_distance_between_centers(first_frame, second_frame):
    """Returns the squared distance between the centers of two rectangles."""
    delta_x = (first_frame[0] + first_frame[2] / 2) - (second_frame[0] + second_frame[2] / 2)
    delta_y = (first_frame[1] + first_frame[3] / 2) - (second_frame[1] + second_frame[3] / 2)
    return delta_x * delta_x + delta_y * delta_y


def compute_window_frame_on_another_monitor(window_frame, source_visible_frame, target_visible_frame):
    """Returns the frame for a window moved to another monitor (R32, PLAN.md section 10).

    The window keeps its size if it fits in the target's usable area (otherwise it shrinks to
    fit), and keeps its position relative to that area (for example centered stays centered).

    Args:
        window_frame: The window's (x, y, width, height).
        source_visible_frame: The usable area of the monitor the window is on now.
        target_visible_frame: The usable area of the monitor it moves to.

    Returns:
        An (x, y, width, height) tuple inside target_visible_frame.
    """
    target_x, target_y, target_width, target_height = target_visible_frame
    width = min(window_frame[2], target_width)
    height = min(window_frame[3], target_height)

    def place_along_one_axis(window_start, window_length, source_start, source_length, target_start, target_length, new_length):
        free_space_on_source = source_length - window_length
        fraction = (window_start - source_start) / free_space_on_source if free_space_on_source > 0 else 0.5
        fraction = min(max(fraction, 0.0), 1.0)
        return target_start + fraction * (target_length - new_length)

    x = place_along_one_axis(window_frame[0], window_frame[2], source_visible_frame[0], source_visible_frame[2],
                             target_x, target_width, width)
    y = place_along_one_axis(window_frame[1], window_frame[3], source_visible_frame[1], source_visible_frame[3],
                             target_y, target_height, height)
    return (x, y, width, height)


def compute_frame_clear_of_area(window_frame, reserved_area, sidebar_edge, monitor_visible_frame, smallest_width):
    """Returns the window frame changed so it no longer reaches into a docked sidebar's area,
    or None when it already doesn't.

    The window is fitted into the rest of the monitor's usable area: it keeps whatever part
    lies there, and if that is narrower than smallest_width, it gets that width (or the whole
    rest, if smaller) on the side away from the sidebar. It never moves past the monitor's edge.

    Args:
        window_frame: The window's (x, y, width, height).
        reserved_area: The sidebar's (x, y, width, height).
        sidebar_edge: "right" or "left", the monitor edge the sidebar is docked to.
        monitor_visible_frame: The usable area of the sidebar's monitor.
        smallest_width: The narrowest a window is made.
    """
    if compute_overlap_area(window_frame, reserved_area) <= 0:
        return None
    x, y, width, height = window_frame
    monitor_x, _, monitor_width, _ = monitor_visible_frame
    area_x, _, area_width, _ = reserved_area
    if sidebar_edge == "right":
        usable_left, usable_right = monitor_x, area_x
    else:
        usable_left, usable_right = area_x + area_width, monitor_x + monitor_width
    new_x = max(x, usable_left)
    new_width = min(x + width, usable_right) - new_x
    if new_width < smallest_width:
        new_width = min(smallest_width, usable_right - usable_left)
        new_x = usable_right - new_width if sidebar_edge == "right" else usable_left
    return (new_x, y, new_width, height)


def assign_windows_to_monitors_by_largest_overlap(window_frames_by_window_id, monitor_frames):
    """Returns {window_id: monitor index} putting each window on the monitor it overlaps most.

    A window that overlaps no monitor (for example one left off-screen after a monitor was
    unplugged) goes to the monitor whose center is nearest. Ties go to the earlier monitor.

    Args:
        window_frames_by_window_id: {window_id: (x, y, width, height)}.
        monitor_frames: Monitor frames, in the order the monitor groups are listed.

    Returns:
        A dict; empty when there are no monitors.
    """
    if not monitor_frames:
        return {}
    monitor_index_by_window_id = {}
    for window_id, window_frame in window_frames_by_window_id.items():
        overlap_areas = [compute_overlap_area(window_frame, monitor_frame) for monitor_frame in monitor_frames]
        if max(overlap_areas) > 0:
            monitor_index_by_window_id[window_id] = overlap_areas.index(max(overlap_areas))
        else:
            distances = [compute_squared_distance_between_centers(window_frame, monitor_frame) for monitor_frame in monitor_frames]
            monitor_index_by_window_id[window_id] = distances.index(min(distances))
    return monitor_index_by_window_id
