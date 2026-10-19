"""The list's sort orders, chosen with the Sort pop-up at the top of the list (user request, 2026-10-19).

Sorting changes only the order the list shows. iTerm2's tab order and the window order set by
dragging rows stay as they are, so "manual" shows them again unchanged. Python's sort keeps
equal rows in their manual order.
"""

from agent_command_tab.session_status_computation import STATES_IN_URGENCY_ORDER


def rank_agent_session_by_activity(agent_session_entry):
    """Sort key: the most urgent state first (asking, error, stuck, working, done, idle), then the
    most recent state change."""
    state = agent_session_entry.status
    urgency = STATES_IN_URGENCY_ORDER.index(state) if state in STATES_IN_URGENCY_ORDER else len(STATES_IN_URGENCY_ORDER)
    return urgency, -agent_session_entry.status_since


def rank_agent_session_by_created_time(agent_session_entry):
    """Sort key: the agent session that started last first; one whose start is unknown last."""
    started_at = agent_session_entry.started_at
    return (started_at is None, -(started_at or 0))


def rank_agent_session_by_title(agent_session_entry):
    """Sort key: the tab's title, ignoring case."""
    return agent_session_entry.tab_title.casefold()


# How each sort order but "manual" (iTerm2's tab order, and the window order set by dragging)
# ranks agent sessions; settings_store takes its choices from here.
AGENT_SESSION_RANK_BY_LIST_SORT_ORDER = {
    "activity": rank_agent_session_by_activity,
    "alphabet": rank_agent_session_by_title,
    "created_time": rank_agent_session_by_created_time,
}
LIST_SORT_ORDERS = ["manual"] + list(AGENT_SESSION_RANK_BY_LIST_SORT_ORDER)


def sort_agent_session_entries(agent_session_entries, list_sort_order):
    """Returns one window's agent sessions in the list's order (one of LIST_SORT_ORDERS)."""
    if list_sort_order == "manual":
        return list(agent_session_entries)
    return sorted(agent_session_entries, key=AGENT_SESSION_RANK_BY_LIST_SORT_ORDER[list_sort_order])


def sort_window_entries(window_entries, list_sort_order):
    """Returns one monitor's windows in the list's order (one of LIST_SORT_ORDERS): alphabetically
    by their own titles, otherwise by each window's first agent session in that order."""
    if list_sort_order == "manual":
        return list(window_entries)
    if list_sort_order == "alphabet":
        return sorted(window_entries, key=lambda window_entry: window_entry.title.casefold())
    rank_agent_session = AGENT_SESSION_RANK_BY_LIST_SORT_ORDER[list_sort_order]
    no_agent_session_rank = (float("inf"), 0)
    return sorted(window_entries, key=lambda window_entry: min(
        (rank_agent_session(entry) for entry in window_entry.agent_sessions), default=no_agent_session_rank))
