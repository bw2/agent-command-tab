"""The full iTerm2 tab order after a tab row is dragged.

The list hides tabs without an agent, but iTerm2 needs every tab of a window in its new order.
Hidden tabs keep their place relative to their neighbors.
"""

from agent_command_tab.window_list_ordering import move_id_before_another


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
    return move_id_before_another(full_tab_ids, moved_tab_id, drop_before_visible_tab_id)
