"""The list's sort orders, chosen with the Sort pop-up at the top of the list (user request, 2026-10-19).

Sorting changes only the order the list shows. iTerm2's tab order and the window order set by
dragging rows stay as they are, so "manual" shows them again unchanged. Python's sort keeps
equal rows in their manual order.
"""

from llm_sidebar_dashboard.session_status_computation import STATES_IN_URGENCY_ORDER


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


def sort_agent_session_entries(agent_session_entries, list_sort_order):
    """Returns one window's agent sessions in the list's order ("manual": iTerm2's tab order)."""
    if list_sort_order == "activity":
        return sorted(agent_session_entries, key=rank_agent_session_by_activity)
    if list_sort_order == "alphabet":
        return sorted(agent_session_entries, key=lambda agent_session_entry: agent_session_entry.tab_title.casefold())
    if list_sort_order == "created_time":
        return sorted(agent_session_entries, key=rank_agent_session_by_created_time)
    return list(agent_session_entries)


def sort_window_entries(window_entries, list_sort_order):
    """Returns one monitor's windows in the list's order ("manual": the order set by dragging;
    "activity" and "created_time": by each window's first agent session in that order)."""
    if list_sort_order in ("activity", "created_time"):
        rank_agent_session = rank_agent_session_by_activity if list_sort_order == "activity" else rank_agent_session_by_created_time
        no_agent_session_rank = (float("inf"), 0)
        return sorted(window_entries, key=lambda window_entry: min(
            (rank_agent_session(entry) for entry in window_entry.agent_sessions), default=no_agent_session_rank))
    if list_sort_order == "alphabet":
        return sorted(window_entries, key=lambda window_entry: window_entry.title.casefold())
    return list(window_entries)
