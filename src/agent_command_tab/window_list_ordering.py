"""The order of windows in the list: stable while running, changed only by the user (R11, R12)."""


def update_window_list_order(previous_window_order, current_window_ids):
    """Returns the new window order after windows opened or closed.

    Known windows keep their previous order, closed windows are dropped, and new windows go on
    top (in the order given). With no previous order (at startup), the given order is used.

    Args:
        previous_window_order: Window IDs in list order, from the last update.
        current_window_ids: IDs of the windows that exist now, in iTerm2's order.

    Returns:
        A list of window IDs.
    """
    if not previous_window_order:
        return list(current_window_ids)
    current_window_id_set = set(current_window_ids)
    kept_window_ids = [window_id for window_id in previous_window_order if window_id in current_window_id_set]
    known_window_id_set = set(previous_window_order)
    new_window_ids = [window_id for window_id in current_window_ids if window_id not in known_window_id_set]
    return new_window_ids + kept_window_ids


def move_window_in_window_list_order(window_order, moved_window_id, drop_before_window_id):
    """Returns the window order with one window moved, as when its row is dragged (R12).

    Args:
        window_order: Window IDs in list order.
        moved_window_id: The dragged window.
        drop_before_window_id: The window it was dropped before, or None for the end of the list.

    Returns:
        A list of window IDs; unchanged when moved_window_id is unknown.
    """
    if moved_window_id not in window_order or moved_window_id == drop_before_window_id:
        return list(window_order)
    remaining_window_ids = [window_id for window_id in window_order if window_id != moved_window_id]
    if drop_before_window_id in remaining_window_ids:
        insert_position = remaining_window_ids.index(drop_before_window_id)
    else:
        insert_position = len(remaining_window_ids)
    return remaining_window_ids[:insert_position] + [moved_window_id] + remaining_window_ids[insert_position:]
