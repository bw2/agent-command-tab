"""The text of each list row, in pyinstrument's style (R36 to R41). Pure functions, no AppKit.

A row reads left to right: gray tree connectors, a status dot, the name (brighter and bolder
when important), then gray details. Colors are names that the view maps to system colors.
"""

import collections

from llm_sidebar_dashboard.context_window_usage import format_token_count
from llm_sidebar_dashboard.session_status_computation import pick_most_urgent_state, summarize_state_counts_in_urgency_order

RowTextParts = collections.namedtuple(
    "RowTextParts", ["connector_text", "dot_glyph", "dot_color_name", "dot_blinks", "name_text", "details_text",
                     "agent_tool_name", "state_text", "elapsed_time_text", "context_text"],
    defaults=["", "", "", ""])
RowTextParts.__doc__ = ("The pieces of one row; dot_blinks marks a working session's dot (R26.1). A tab row ends "
                        "in columns that line up from row to row: context_text (the context's size, \"425k\", "
                        "right-aligned), the icon for agent_tool_name, state_text (left-aligned) and "
                        "elapsed_time_text (right-aligned).")

DOT_GLYPH_AND_COLOR_BY_STATE = {
    "asking": ("●", "light_blue"),  # the same light blue as the menu bar icon's "C" and count (user request, milestone 2)
    "error": ("✕", "red"),
    "stuck": ("◆", "orange"),
    "working": ("●", "green"),
    "done": ("✔", "green"),
    "idle": ("○", "gray"),
    "unknown": ("◌", "gray"),
    "exited": ("⊘", "gray"),
}


def format_elapsed_time(elapsed_seconds):
    """Returns a short elapsed time such as "45s", "3m", "1h 5m" or "2d"."""
    elapsed_seconds = max(int(elapsed_seconds), 0)
    if elapsed_seconds < 60:
        return f"{elapsed_seconds}s"
    if elapsed_seconds < 3600:
        return f"{elapsed_seconds // 60}m"
    if elapsed_seconds < 86400:
        hours, minutes = divmod(elapsed_seconds // 60, 60)
        return f"{hours}h {minutes}m" if minutes else f"{hours}h"
    return f"{elapsed_seconds // 86400}d"


def build_tree_connector_text(is_last_child_at_each_level):
    """Returns the gray prefix that draws the tree, for example "│  └─ ".

    Args:
        is_last_child_at_each_level: For each level below the top, whether the row (last
            element) or its ancestor at that level is its parent's last child. Empty for
            top-level rows, which get no connector.
    """
    if not is_last_child_at_each_level:
        return ""
    ancestor_parts = ["   " if is_last else "│  " for is_last in is_last_child_at_each_level[:-1]]
    return "".join(ancestor_parts) + ("└─ " if is_last_child_at_each_level[-1] else "├─ ")


def build_agent_session_row_text_parts(agent_session_entry, is_last_child_at_each_level, now):
    """Returns the RowTextParts for a tab row.

    Args:
        agent_session_entry: An AgentSessionEntry.
        is_last_child_at_each_level: See build_tree_connector_text.
        now: The current time, for the elapsed time since the status changed.
    """
    dot_glyph, dot_color_name = DOT_GLYPH_AND_COLOR_BY_STATE.get(agent_session_entry.status, DOT_GLYPH_AND_COLOR_BY_STATE["unknown"])
    return RowTextParts(
        connector_text=build_tree_connector_text(is_last_child_at_each_level),
        dot_glyph=dot_glyph,
        dot_color_name=dot_color_name,
        dot_blinks=agent_session_entry.status == "working",
        name_text=agent_session_entry.tab_title,
        details_text=agent_session_entry.preview_line,
        agent_tool_name=agent_session_entry.agent_tool_name,
        # While it waits on a command it started, a working agent shows "running" and the command's time.
        state_text="running" if agent_session_entry.running_command_name else agent_session_entry.status,
        elapsed_time_text=format_elapsed_time(now - (agent_session_entry.running_command_started_at
                                                     if agent_session_entry.running_command_name
                                                     else agent_session_entry.status_since)),
        context_text=format_token_count(agent_session_entry.context_tokens) if agent_session_entry.context_tokens else "",
    )


def build_window_row_text_parts(window_entry, is_last_child_at_each_level, is_expanded):
    """Returns the RowTextParts for a window row.

    The dot shows the window's most urgent session state. A collapsed window counts its hidden
    sessions' states ("1 working, 2 idle", R40 without its "[3 tabs hidden]" prefix); an
    expanded one shows nothing there, since its tab rows show each state (user requests,
    milestone 2).
    """
    states = [agent_session_entry.status for agent_session_entry in window_entry.agent_sessions]
    most_urgent_state = pick_most_urgent_state(states)
    dot_glyph, dot_color_name = DOT_GLYPH_AND_COLOR_BY_STATE[most_urgent_state]
    details_text = "" if is_expanded else summarize_state_counts_in_urgency_order(states)
    return RowTextParts(
        connector_text=build_tree_connector_text(is_last_child_at_each_level),
        dot_glyph=dot_glyph,
        dot_color_name=dot_color_name,
        dot_blinks=most_urgent_state == "working",  # blinks like its working tab's dot
        name_text=window_entry.title,
        details_text=details_text,
    )


def build_monitor_row_text_parts(monitor_entry):
    """Returns the RowTextParts for a monitor group row (shown only with two or more monitors)."""
    window_count = len(monitor_entry.windows)
    return RowTextParts(
        connector_text="",
        dot_glyph="",
        dot_color_name="gray",
        dot_blinks=False,
        name_text=monitor_entry.name or "Monitor",
        details_text=f"{window_count} window" + ("" if window_count == 1 else "s"),
    )
