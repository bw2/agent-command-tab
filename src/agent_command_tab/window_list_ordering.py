"""The order of windows in the list: stable while running, changed only by the user."""


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
    """Returns the window order with one window moved, as when its row is dragged.

    Args:
        window_order: Window IDs in list order.
        moved_window_id: The dragged window.
        drop_before_window_id: The window it was dropped before, or None for the end of the list.

    Returns:
        A list of window IDs; unchanged when moved_window_id is unknown.
    """
    if moved_window_id not in window_order:
        return list(window_order)
    return move_id_before_another(window_order, moved_window_id, drop_before_window_id)


def move_id_before_another(ids, moved_id, drop_before_id):
    """Returns the IDs with moved_id just before drop_before_id, or at the end when that isn't
    among them (None, say); unchanged for an ID dropped before itself. moved_id may be new to the
    IDs (a tab moved from another window). The window list and a window's tabs share it (code
    review, 2026-10-30: the tabs' copy moved a tab dropped before itself to the end)."""
    if moved_id == drop_before_id:
        return list(ids)
    remaining_ids = [item_id for item_id in ids if item_id != moved_id]
    insert_position = remaining_ids.index(drop_before_id) if drop_before_id in remaining_ids else len(remaining_ids)
    return remaining_ids[:insert_position] + [moved_id] + remaining_ids[insert_position:]
