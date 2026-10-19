"""The full iTerm2 tab order after a tab row is dragged (PLAN.md section 9).

The list hides tabs without an agent, but iTerm2 needs every tab of a window in its new order.
Hidden tabs keep their place relative to their neighbors.
"""


def compute_full_tab_order_after_move(full_tab_ids, moved_tab_id, drop_before_visible_tab_id):
    """Returns every tab ID of the target window in its new order.

    Args:
        full_tab_ids: All tab IDs of the target window in iTerm2's order, hidden tabs included.
            For a move from another window, moved_tab_id is not among them yet.
        moved_tab_id: The dragged tab.
        drop_before_visible_tab_id: The listed tab it was dropped before, or None for after the
            window's last tab.

    Returns:
        A list of tab IDs that contains moved_tab_id exactly once.
    """
    remaining_tab_ids = [tab_id for tab_id in full_tab_ids if tab_id != moved_tab_id]
    if drop_before_visible_tab_id in remaining_tab_ids:
        insert_position = remaining_tab_ids.index(drop_before_visible_tab_id)
    else:
        insert_position = len(remaining_tab_ids)
    return remaining_tab_ids[:insert_position] + [moved_tab_id] + remaining_tab_ids[insert_position:]
