"""The snapshot that the core publishes to every view (PLAN.md section 4).

Plain, immutable records that convert to JSON, so that a later web page, phone app or second
computer can receive exactly what the menu bar list receives. They are named tuples (not data
classes, which would need type annotations), so equal contents compare equal, which the views
use to redraw only the rows that changed. Frames are (x, y, width, height) tuples in points,
with the origin at the bottom-left of the main monitor.
"""

import collections

AgentSessionEntry = collections.namedtuple(
    "AgentSessionEntry",
    [
        "tab_id", "session_id", "tab_title", "tab_title_set_by_user", "agent_session_name", "agent_tool_name",
        "working_folder", "status", "status_reason", "status_since", "preview_line", "details",
        "context_fill_percent", "importance_level", "background_task_count", "last_user_interaction_time",
        "ended_abnormally_at", "is_in_front", "peer_messaging_address", "last_time_tab_was_seen",
        "running_command_name", "running_command_started_at", "context_tokens", "started_at",
    ],
    defaults=["", 0.0, "", "", None, 0, 0, None, None, False, "", None, "", None, None, None],
)
AgentSessionEntry.__doc__ = ("One iTerm2 tab whose first pane runs an agent session. last_time_tab_was_seen is when "
                             "the tab was last selected in the key iTerm2 window while iTerm2 was active, or None. "
                             "running_command_name is the command a working agent started in this turn and waits "
                             "on (for example \"python3\"), or \"\" while it thinks. context_tokens is how many "
                             "tokens its context holds, from its conversation file, or None if unknown. "
                             "started_at is when the agent's process started (the Created Time column), or None.")

WindowEntry = collections.namedtuple(
    "WindowEntry",
    ["window_id", "title", "window_title_set_by_user", "frame", "last_prompt_time", "total_tab_count", "agent_sessions",
     "selected_tab_id"],
    defaults=[None],
)
WindowEntry.__doc__ = ("One iTerm2 window with at least one agent session tab. total_tab_count includes the tabs "
                       "that are hidden because they run no agent. selected_tab_id is the window's own selected "
                       "tab, which may run no agent (unlike is_in_front, which marks only the key window's tab).")

MonitorEntry = collections.namedtuple("MonitorEntry", ["monitor_id", "name", "frame", "visible_frame", "windows"])
MonitorEntry.__doc__ = "One monitor and the windows assigned to it, in the core's window list order."

ComputerSnapshot = collections.namedtuple(
    "ComputerSnapshot",
    ["computer_id", "computer_name", "taken_at", "monitors", "codex_usage", "hooks_status_per_agent_tool"],
    defaults=[None, None],
)
ComputerSnapshot.__doc__ = "Everything one computer's core knows, at one moment."


def convert_snapshot_to_json_ready_dict(snapshot):
    """Returns the snapshot as nested dicts and lists that json.dumps accepts.

    Args:
        snapshot: A ComputerSnapshot, or any of the records above.

    Returns:
        A dict (for a record), a list (for a tuple of records), or the value itself.
    """
    if hasattr(snapshot, "_asdict"):
        return {field_name: convert_snapshot_to_json_ready_dict(value) for field_name, value in snapshot._asdict().items()}
    if isinstance(snapshot, (tuple, list)):
        return [convert_snapshot_to_json_ready_dict(value) for value in snapshot]
    return snapshot


def iterate_agent_session_entries(snapshot):
    """Yields (monitor_entry, window_entry, agent_session_entry) for every agent session.

    Args:
        snapshot: A ComputerSnapshot.
    """
    for monitor_entry in snapshot.monitors:
        for window_entry in monitor_entry.windows:
            for agent_session_entry in window_entry.agent_sessions:
                yield monitor_entry, window_entry, agent_session_entry


SECONDS_A_TAB_COUNTS_AS_RECENTLY_USED = 24 * 60 * 60


def choose_folder_for_new_agent_window(snapshot, now, home_folder):
    """Returns the folder a new window's agent session starts in (user request, milestone 2): the
    folder of the tab in front, else of the tab seen most recently within the last 24 hours,
    else the home folder.

    Args:
        snapshot: The latest ComputerSnapshot, or None before the first one.
        now: The current time.
        home_folder: The user's home folder.
    """
    agent_session_entries = [entry for _, _, entry in iterate_agent_session_entries(snapshot) if entry.working_folder] \
        if snapshot else []
    front_entry = next((entry for entry in agent_session_entries if entry.is_in_front), None)
    if front_entry is not None:
        return front_entry.working_folder
    recently_seen_entries = [entry for entry in agent_session_entries if entry.last_time_tab_was_seen is not None
                             and now - entry.last_time_tab_was_seen <= SECONDS_A_TAB_COUNTS_AS_RECENTLY_USED]
    if recently_seen_entries:
        return max(recently_seen_entries, key=lambda entry: entry.last_time_tab_was_seen).working_folder
    return home_folder
