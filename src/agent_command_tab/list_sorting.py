"""The list's sort orders, chosen with the Sort pop-up at the top of the list (user request, 2026-10-19)
or by clicking a column's title, which also reverses them (user requests, 2026-10-26).

Sorting changes only the order the list shows. iTerm2's tab order and the window order set by
dragging rows stay as they are, so "manual" shows them again unchanged. Python's sort keeps
equal rows in their manual order.

Each rank function returns an agent session's sort key in its order's first direction (the Sort
pop-up's, and a column title's first click), or None while the value isn't known: those agent
sessions go last in either direction.
"""

from agent_command_tab.session_status_computation import STATES_IN_URGENCY_ORDER


def find_when_the_shown_state_started(agent_session_entry):
    """Returns when the state an agent session's row shows started, which its time counts from:
    while it runs a command ("running"), when the command started; otherwise its last state change."""
    if agent_session_entry.running_command_name:
        return agent_session_entry.running_command_started_at
    return agent_session_entry.status_since


def rank_agent_session_by_activity(agent_session_entry):
    """Sort key: the most urgent state first (asking, error, stuck, working, done, idle), then the
    most recent state change, as the row shows it (find_when_the_shown_state_started)."""
    state = agent_session_entry.status
    if state not in STATES_IN_URGENCY_ORDER:
        return None
    return STATES_IN_URGENCY_ORDER.index(state), -find_when_the_shown_state_started(agent_session_entry)


def rank_agent_session_by_created_time(agent_session_entry):
    """Sort key: the agent session that started last first."""
    started_at = agent_session_entry.started_at
    return None if started_at is None else -started_at


def rank_agent_session_by_title(agent_session_entry):
    """Sort key: the tab's title, ignoring case."""
    return agent_session_entry.tab_title.casefold()


def rank_agent_session_by_memory_usage(agent_session_entry):
    """Sort key: the agent session using the most memory first (with the processes below it; user
    request, 2026-10-21)."""
    memory_bytes = agent_session_entry.memory_bytes
    return None if memory_bytes is None else -memory_bytes


def rank_agent_session_by_cpu_usage(agent_session_entry):
    """Sort key: the agent session using the most CPU first (with the processes below it; user
    request, 2026-10-21)."""
    cpu_percent = agent_session_entry.cpu_percent
    return None if cpu_percent is None else -cpu_percent


def rank_agent_session_by_context_percent(agent_session_entry):
    """Sort key: the agent session whose context fills the most of its window first, rather than
    the largest context, since windows differ in size (the Tokens column; user request, 2026-10-26)."""
    context_fill_percent = agent_session_entry.context_fill_percent
    return None if context_fill_percent is None else -context_fill_percent


def rank_agent_session_by_last_state_change(agent_session_entry):
    """Sort key: the agent session whose state changed last first (user request, 2026-10-26), by
    the time its row shows (find_when_the_shown_state_started)."""
    return -find_when_the_shown_state_started(agent_session_entry)


# How each sort order but "manual" (iTerm2's tab order, and the window order set by dragging)
# ranks agent sessions; settings_store takes its choices from here.
AGENT_SESSION_RANK_BY_LIST_SORT_ORDER = {
    "activity": rank_agent_session_by_activity,
    "alphabet": rank_agent_session_by_title,
    "context_percent": rank_agent_session_by_context_percent,
    "created_time": rank_agent_session_by_created_time,
    "memory_usage": rank_agent_session_by_memory_usage,
    "cpu_usage": rank_agent_session_by_cpu_usage,
    "state_duration": rank_agent_session_by_last_state_change,
}
LIST_SORT_ORDERS = ["manual"] + list(AGENT_SESSION_RANK_BY_LIST_SORT_ORDER)


def sort_by_rank(entries, rank_entry, is_reversed):
    """Returns the entries by rank_entry's key, in the other direction when is_reversed, those it
    ranks None (not known) last either way; entries with equal keys keep their order."""
    keys_and_entries = [(rank_entry(entry), entry) for entry in entries]
    ranked_keys_and_entries = sorted(((key, entry) for key, entry in keys_and_entries if key is not None),
                                     key=lambda key_and_entry: key_and_entry[0], reverse=is_reversed)
    return [entry for _, entry in ranked_keys_and_entries] + [entry for key, entry in keys_and_entries if key is None]


def sort_agent_session_entries(agent_session_entries, list_sort_order, is_reversed=False):
    """Returns one window's agent sessions in the list's order (one of LIST_SORT_ORDERS), reversed when is_reversed."""
    if list_sort_order == "manual":
        return list(agent_session_entries)
    return sort_by_rank(agent_session_entries, AGENT_SESSION_RANK_BY_LIST_SORT_ORDER[list_sort_order], is_reversed)


def sort_window_entries(window_entries, list_sort_order, list_ranked_agent_sessions=lambda window_entry: window_entry.agent_sessions,
                        is_reversed=False):
    """Returns one monitor's windows in the list's order (one of LIST_SORT_ORDERS), reversed when
    is_reversed: alphabetically by their own titles, otherwise by each window's first agent
    session in that order, among those list_ranked_agent_sessions(window_entry) returns (all of
    them, or the ones whose rows Hide Idle doesn't hide, so a hidden tab doesn't decide where its
    window goes). A window without one, or whose first one's value isn't known, goes last."""
    if list_sort_order == "manual":
        return list(window_entries)
    if list_sort_order == "alphabet":
        return sort_by_rank(window_entries, lambda window_entry: window_entry.title.casefold(), is_reversed)
    rank_agent_session = AGENT_SESSION_RANK_BY_LIST_SORT_ORDER[list_sort_order]

    def rank_window_by_its_first_agent_session(window_entry):
        first_agent_sessions = sort_agent_session_entries(list_ranked_agent_sessions(window_entry), list_sort_order, is_reversed)[:1]
        return rank_agent_session(first_agent_sessions[0]) if first_agent_sessions else None
    return sort_by_rank(window_entries, rank_window_by_its_first_agent_session, is_reversed)
