"""The text of each list row, in pyinstrument's style (R36 to R41). Pure functions, no AppKit.

A row reads left to right: gray tree connectors, a status dot, the name (brighter and bolder
when important), then gray details. Colors are names that the view maps to system colors.
"""

import collections
import time

from agent_command_tab.context_window_usage import format_token_count
from agent_command_tab.session_status_computation import pick_most_urgent_state, summarize_state_counts_in_urgency_order

RowTextParts = collections.namedtuple(
    "RowTextParts", ["connector_text", "dot_glyph", "dot_color_name", "dot_blinks", "name_text", "details_text",
                     "agent_tool_name", "state_text", "elapsed_time_text", "context_text",
                     "created_time_text", "existence_duration_text", "memory_text", "cpu_text", "preview_connector_text",
                     "preview_text", "details_color_name", "dot_tool_tip", "details_tool_tip",
                     "context_fill_percent_color_name", "state_color_name"],
    defaults=["", "", "", "", "", "", "", "", "", "", "gray", "", "", "", ""])
RowTextParts.__doc__ = ("The pieces of one row; dot_blinks marks a working session's dot (R26.1). A tab row ends "
                        "in columns that line up from row to row (agent_session_outline_view.TAB_ROW_COLUMNS): "
                        "context_text (the context's size and its share of the context window, "
                        "\"425k (42%)\", the share in parentheses in context_fill_percent_color_name, or in the "
                        "column's color for \"\"), created_time_text (when the agent session "
                        "started) and existence_duration_text (how long ago), memory_text and cpu_text (what the "
                        "agent and the processes it started use), the icon for agent_tool_name, state_text "
                        "(in state_color_name) and elapsed_time_text (the state's duration). Under it, a tab "
                        "row has a gray preview line "
                        "(R23): preview_connector_text continues the tree's lines, and preview_text starts under "
                        "the name. A tab row's details are its background task lights, in details_color_name. "
                        "dot_tool_tip and details_tool_tip are what the dot and the details mean, shown when the "
                        "pointer rests on them, or \"\" for none (user request, 2026-10-27).")

# A light per background shell or background agent after a tab's name (R26.2); more than
# MOST_BACKGROUND_TASK_LIGHTS show as one light and their number. After the name rather than the
# dot (as R26.2 first had it), so the names stay lined up from row to row.
BACKGROUND_TASK_LIGHT = "•"
BACKGROUND_TASK_LIGHTS_COLOR_NAME = "yellow"
MOST_BACKGROUND_TASK_LIGHTS = 4
# A child process's sub row has the light too when the agent left it running in the background,
# and a gray middle dot otherwise, the same width, so the commands line up.
CHILD_PROCESS_DOT_GLYPHS_AND_COLORS = {True: (BACKGROUND_TASK_LIGHT, BACKGROUND_TASK_LIGHTS_COLOR_NAME), False: ("·", "gray")}
CHILD_PROCESS_DOT_TOOL_TIPS = {
    True: "Running in the background: the agent launched this command in the background and went on",  # the user's wording, 2026-10-28
    False: "Not a background task: a command from the agent's current turn, or a helper the agent runs for itself",  # the user's wording, 2026-10-28
}

# Each state's dot and its color, also the menu bar icon's color for the most urgent state.
DOT_GLYPH_AND_COLOR_BY_STATE = {
    "asking": ("●", "light_blue"),  # also the asking count's color in the menu bar (user request, milestone 2)
    "error": ("✕", "red"),
    "stuck": ("◆", "orange"),
    "working": ("●", "green"),
    "done": ("✔", "green"),
    "idle": ("○", "gray"),
    "paused": ("‖", "purple"),
    "unknown": ("◌", "gray"),
    "exited": ("⊘", "gray"),
}

# The color of each state's text in the Status column: its dot's, but done is gray, in the names'
# color in dark mode, and unknown is pure black, or pure white in dark mode, so it stands out from
# done (user requests, 2026-10-28). named_colors' "_text" colors are darker in light mode, so they
# read on white.
STATE_TEXT_COLOR_NAME_BY_STATE = {
    "asking": "light_blue_text",
    "error": "red",
    "stuck": "orange_text",
    "working": "green_text",  # a tab row's "thinking" and "running"
    "done": "gray_in_light_mode_only",
    "idle": "gray",
    "paused": "purple",
    "unknown": "pure_black_or_white",
    "exited": "gray",
}

# What each state means, the dot's hover text (user request, 2026-10-27). The 10 and 5 minutes are
# iterm2_connection_thread's STUCK_THRESHOLD_SECONDS and EXITED_ROW_SECONDS.
MEANING_BY_STATE = {  # asking, idle, paused and exited in the user's wording, 2026-10-28
    "asking": "the agent is waiting for your answer to a question, or for permission to use a tool",
    "error": "the agent reported an error",
    "stuck": "the agent has been thinking for 10 minutes with nothing new on screen and no command running",
    "working": "the agent is thinking or running a command",
    "done": "the agent is done and you haven't looked at its last message yet",  # the user's wording, 2026-10-27
    "idle": "nothing new here since you last looked at this tab",
    # A plain CONT signal would leave it a background job (agent_session_pausing), so Resume types fg.
    "paused": "the agent process has been suspended (Ctrl+Z, or a STOP signal); Resume types fg to continue it",
    "unknown": "the app can't tell what the agent is doing",
    "exited": "this status appears for 5 minutes after an agent exits abnormally (a crash, or killed)",
}
# A tab row names a working agent's state for what it does: "running" while it waits on a tool,
# a command at once and any other tool after 5 seconds, otherwise "thinking" (user requests,
# 2026-10-28; iterm2_snapshot_builder.choose_tool_the_agent_is_waiting_on). Window rows, whose
# counts sum up their tabs, still say "working" for both.
MEANING_BY_WORKING_STATE_NAME = {
    "thinking": "the agent is thinking or briefly running tools",  # the user's wording, 2026-10-28
    "running": "the agent is waiting on a tool it started, such as a command, a subagent or a web fetch",
}

# The longest text each column at a tab row's right edge can get, by RowTextParts field (the font
# is monospaced, so the longest is the widest). The dropdown makes each shown column this wide,
# so its width stays put as numbers grow ("99k" to "100k", "9m" to "10m") and states change
# (user request, 2026-10-25).
LONGEST_TEXT_BY_COLUMN_FIELD_NAME = {
    # format_token_count: "425k", or "1.2M" from 999,500 up; the share is over 100% only for a
    # context larger than the window it is measured against.
    "context_text": "999k (100%)",
    "created_time_text": "12:59 PM",  # format_clock_time: "6:08 AM" today, otherwise "Oct 17"
    "existence_duration_text": "23h 59m",  # format_elapsed_time
    "memory_text": "1024M",  # format_memory_size: "1024M" just under a gigabyte, then "1.4G" up to "99.9G"
    "cpu_text": "100%",  # an agent using ten cores or more gets a wider column
    "state_text": "thinking",  # the longest state, then running and unknown
    "elapsed_time_text": "23h 59m",  # format_elapsed_time
}


def build_dot_text(dot_glyph):
    """Returns the text of a row's dot field: the glyph and a space, or "" for a row without one."""
    return dot_glyph + (" " if dot_glyph else "")


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


def format_memory_size(memory_bytes):
    """Returns a short memory size such as "512M", or "1.4G" from a gigabyte up (powers of 1024, as Activity Monitor shows)."""
    if memory_bytes >= 1024 ** 3:
        return f"{memory_bytes / 1024 ** 3:.1f}G"
    return f"{round(memory_bytes / 1024 ** 2)}M"


def format_context_text(context_tokens, context_fill_percent):
    """Returns the Tokens column's text: the context's size and its share of the context window,
    such as "134k (13%)" (user request, 2026-10-26), the size alone while the window's size isn't
    known, or "" while the context's size isn't."""
    if not context_tokens:
        return ""
    if context_fill_percent is None:
        return format_token_count(context_tokens)
    return f"{format_token_count(context_tokens)} ({context_fill_percent}%)"


def choose_context_fill_percent_color_name(context_fill_percent):
    """Returns the color of the Tokens column's share of the context window: "amber" from 50%,
    "red" from 75%, or "" below 50% or while the share isn't known, for the column's own color
    (user request, 2026-10-27)."""
    if context_fill_percent is None or context_fill_percent < 50:
        return ""
    return "amber" if context_fill_percent < 75 else "red"


def format_clock_time(timestamp, now):
    """Returns a short local time: "6:08 AM" for a time today, otherwise the day, such as "Oct 17"."""
    moment = time.localtime(timestamp)
    if time.localtime(now)[:3] == moment[:3]:
        return time.strftime("%I:%M %p", moment).lstrip("0")
    return time.strftime("%b %d", moment).replace(" 0", " ")


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


def build_tree_continuation_text(is_last_child_at_each_level):
    """Returns the gray prefix for a second line under a row: the tree's vertical lines carried
    down, for example "│  │  " under "│  ├─ ", and "   " under a last child's "└─ "."""
    return "".join("   " if is_last else "│  " for is_last in is_last_child_at_each_level)


def build_background_task_lights_text(background_task_count):
    """Returns "••" for two background tasks, "•×6" beyond MOST_BACKGROUND_TASK_LIGHTS, "" for none."""
    if background_task_count > MOST_BACKGROUND_TASK_LIGHTS:
        return f"{BACKGROUND_TASK_LIGHT}×{background_task_count}"
    return BACKGROUND_TASK_LIGHT * background_task_count


def describe_background_task_lights(background_task_count):
    """Returns the background task lights' hover text, such as "2 background tasks", or "" for none."""
    if not background_task_count:
        return ""
    return "1 background task" if background_task_count == 1 else f"{background_task_count} background tasks"


def describe_state(state):
    """Returns a state dot's hover text, such as "Done: the agent is done and you haven't looked at its last message yet"."""
    return f"{state.capitalize()}: {MEANING_BY_STATE[state]}"


def name_and_describe_tab_rows_state(agent_session_entry, state):
    """Returns a tab row's state text and its dot's hover text, such as ("running", "Running: the
    agent is waiting on a command it started"): a working agent's state is "thinking" or
    "running" (MEANING_BY_WORKING_STATE_NAME). A state the app doesn't know shows as it came, with
    the hover text of state, "unknown"."""
    if state != "working":
        return agent_session_entry.status, describe_state(state)
    working_state_name = "running" if agent_session_entry.running_tool_name else "thinking"
    return working_state_name, f"{working_state_name.capitalize()}: {MEANING_BY_WORKING_STATE_NAME[working_state_name]}"


def build_resource_usage_texts(memory_bytes, cpu_percent):
    """Returns (memory_text, cpu_text) for the Memory and CPU columns, "" while not read."""
    return (format_memory_size(memory_bytes) if memory_bytes is not None else "",
            f"{cpu_percent}%" if cpu_percent is not None else "")


def build_agent_session_row_text_parts(agent_session_entry, is_last_child_at_each_level, now):
    """Returns the RowTextParts for a tab row.

    Args:
        agent_session_entry: An AgentSessionEntry.
        is_last_child_at_each_level: See build_tree_connector_text.
        now: The current time, for the elapsed time since the status changed and since the agent session started.
    """
    state = agent_session_entry.status if agent_session_entry.status in DOT_GLYPH_AND_COLOR_BY_STATE else "unknown"
    dot_glyph, dot_color_name = DOT_GLYPH_AND_COLOR_BY_STATE[state]
    state_text, dot_tool_tip = name_and_describe_tab_rows_state(agent_session_entry, state)
    started_at = agent_session_entry.started_at
    memory_text, cpu_text = build_resource_usage_texts(agent_session_entry.memory_bytes, agent_session_entry.cpu_percent)
    return RowTextParts(
        connector_text=build_tree_connector_text(is_last_child_at_each_level),
        dot_glyph=dot_glyph,
        dot_color_name=dot_color_name,
        dot_blinks=agent_session_entry.status == "working",
        name_text=agent_session_entry.tab_title,
        details_text=build_background_task_lights_text(agent_session_entry.background_task_count),  # the preview has its own line
        details_color_name=BACKGROUND_TASK_LIGHTS_COLOR_NAME,
        dot_tool_tip=dot_tool_tip,
        details_tool_tip=describe_background_task_lights(agent_session_entry.background_task_count),
        agent_tool_name=agent_session_entry.agent_tool_name,
        # While it waits on a tool, a working agent shows "running" and the tool's time.
        state_text=state_text,
        state_color_name=STATE_TEXT_COLOR_NAME_BY_STATE[state],
        elapsed_time_text=format_elapsed_time(now - (agent_session_entry.running_tool_started_at
                                                     if agent_session_entry.running_tool_name
                                                     else agent_session_entry.status_since)),
        context_text=format_context_text(agent_session_entry.context_tokens, agent_session_entry.context_fill_percent),
        context_fill_percent_color_name=choose_context_fill_percent_color_name(agent_session_entry.context_fill_percent),
        created_time_text=format_clock_time(started_at, now) if started_at is not None else "",
        existence_duration_text=format_elapsed_time(now - started_at) if started_at is not None else "",
        memory_text=memory_text,
        cpu_text=cpu_text,
        preview_connector_text=build_tree_continuation_text(is_last_child_at_each_level),
        preview_text=agent_session_entry.preview_line,
    )


def build_child_process_row_text_parts(child_process_entry, is_last_child_at_each_level, now):
    """Returns the RowTextParts for a sub row of an open tab row (user request, 2026-10-27): the
    command the agent started, in gray, and when it started, how long ago, and its memory and CPU
    use in the tab row's columns.

    Args:
        child_process_entry: A ChildProcessEntry.
        is_last_child_at_each_level: See build_tree_connector_text.
        now: The current time.
    """
    dot_glyph, dot_color_name = CHILD_PROCESS_DOT_GLYPHS_AND_COLORS[child_process_entry.is_background_shell]
    memory_text, cpu_text = build_resource_usage_texts(child_process_entry.memory_bytes, child_process_entry.cpu_percent)
    return RowTextParts(
        connector_text=build_tree_connector_text(is_last_child_at_each_level),
        dot_glyph=dot_glyph,
        dot_color_name=dot_color_name,
        dot_blinks=False,
        dot_tool_tip=CHILD_PROCESS_DOT_TOOL_TIPS[child_process_entry.is_background_shell],
        name_text=child_process_entry.command_text,
        details_text="",
        created_time_text=format_clock_time(child_process_entry.started_at, now),
        existence_duration_text=format_elapsed_time(now - child_process_entry.started_at),
        memory_text=memory_text,
        cpu_text=cpu_text,
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
        # Its most urgent tab's state, without "The most urgent status among this window's tabs:" before it (user request, 2026-10-27).
        dot_tool_tip=describe_state(most_urgent_state),
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
