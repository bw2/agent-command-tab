"""The snapshot that the core publishes to every view.

Plain, immutable records that convert to JSON, so that a later web page, phone app or second
computer can receive exactly what the menu bar list receives. They are named tuples (not data
classes, which would need type annotations), so equal contents compare equal, which the views
use to redraw only the rows that changed. Frames are (x, y, width, height) tuples in points,
with the origin at the bottom-left of the main monitor.

The fields once kept for importance levels, hover panels, automatic introductions, the startup
window order and Codex usage bars were removed when those features were dropped (user decision,
2026-10-27).
"""

import collections

# Each optional field beside its default, so a field added or moved can't take another's default.
AGENT_SESSION_ENTRY_DEFAULTS_BY_OPTIONAL_FIELD = {
    "status_reason": "", "status_since": 0.0, "preview_line": "", "context_fill_percent": None,
    "background_task_count": 0, "ended_abnormally_at": None, "is_in_front": False, "last_time_tab_was_seen": None,
    "running_tool_name": "", "running_tool_started_at": None, "context_tokens": None, "started_at": None,
    "memory_bytes": None, "cpu_percent": None, "slash_commands": (), "is_paused_with_the_processes_below_it": False,
    "child_processes": (),
}
AgentSessionEntry = collections.namedtuple(
    "AgentSessionEntry",
    ["tab_id", "session_id", "tab_title", "tab_title_set_by_user", "agent_session_name", "agent_tool_name",
     "working_folder", "status"] + list(AGENT_SESSION_ENTRY_DEFAULTS_BY_OPTIONAL_FIELD),
    defaults=list(AGENT_SESSION_ENTRY_DEFAULTS_BY_OPTIONAL_FIELD.values()),
)
AgentSessionEntry.__doc__ = ("One iTerm2 tab whose first pane runs an agent session. last_time_tab_was_seen is when "
                             "the tab was last selected in the key iTerm2 window while iTerm2 was active, or None. "
                             "running_tool_name is the tool a working agent waits on, and running_tool_started_at "
                             "when it started (iterm2_snapshot_builder.choose_tool_the_agent_is_waiting_on): a "
                             "command it started in this turn (for example \"python3\"), or another tool open for "
                             "5 seconds or more (for example \"Agent\"), or \"\" while it thinks (\"running_command_"
                             "name\" until 2026-10-28, when other tools began to count). context_tokens is how many "
                             "tokens its context holds, from its conversation file, or None if unknown, and "
                             "context_fill_percent that as a whole percent of its context window, or None. "
                             "started_at is when the agent's process started (the Created Time column), or None. "
                             "preview_line is the gray line under the tab row: what the agent asks or runs, what "
                             "it said since the last prompt, or that prompt (session_preview_computation). "
                             "memory_bytes and cpu_percent are what the agent process and all the processes below it "
                             "use (physical memory to the megabyte; percent of one CPU core, a whole number), or "
                             "None until they are read. slash_commands are the commands the tab's menu can type "
                             "into its agent tool (its adapter's), in menu order. is_paused_with_the_processes_below_it "
                             "is True for a paused row whose pause also stopped the processes below the agent (Pause "
                             "from the list), False for an agent suspended with Ctrl+Z by hand. A row whose agent "
                             "ended without a normal session end (a crash, or killed) has the status "
                             "\"exited\" and ended_abnormally_at, when that was noticed. background_task_count is "
                             "how many background shells and background agents it runs, and "
                             "child_processes the ChildProcessEntry of each process it started, oldest first.")

ChildProcessEntry = collections.namedtuple(
    "ChildProcessEntry", ["process_id", "command_text", "started_at", "is_background_shell", "memory_bytes", "cpu_percent"])
ChildProcessEntry.__doc__ = ("One process an agent started, a sub row of its open tab row (user request, 2026-10-27): "
                             "the command it runs (agent_process_tree.list_child_processes_of_the_agent), when that "
                             "command started, whether it is a command left running in the background, and what it and the "
                             "processes below it use (None while memory and CPU use aren't read).")

WindowEntry = collections.namedtuple(
    "WindowEntry",
    ["window_id", "title", "window_title_set_by_user", "frame", "total_tab_count", "agent_sessions", "selected_tab_id",
     "number_among_windows_with_the_same_title"],
    defaults=[None, 1],
)
WindowEntry.__doc__ = ("One iTerm2 window with at least one agent session tab. total_tab_count includes the tabs "
                       "that are hidden because they run no agent. selected_tab_id is the window's own selected "
                       "tab, which may run no agent (unlike is_in_front, which marks only the key window's tab). "
                       "number_among_windows_with_the_same_title is 1 for the first window in iTerm2's order with "
                       "its title, 2 for the next one, and so on (build_window_title_shown_in_the_list).")

MonitorEntry = collections.namedtuple("MonitorEntry", ["monitor_id", "name", "frame", "visible_frame", "windows"])
MonitorEntry.__doc__ = "One monitor and the windows assigned to it, in the core's window list order."

ComputerSnapshot = collections.namedtuple(
    "ComputerSnapshot", ["computer_id", "computer_name", "taken_at", "monitors", "hooks_status_per_agent_tool"],
    defaults=[()],
)
ComputerSnapshot.__doc__ = ("Everything one computer's core knows, at one moment. hooks_status_per_agent_tool holds "
                            "(agent tool name, hooks_installer.check_hooks's status) pairs, one for each tool the "
                            "app has hooks for (user request, 2026-10-27).")


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


def rename_window_entry(window_entry, new_title):
    """Returns the window entry with a new title, for a rename the list shows before a snapshot
    from the core confirms it: numbered 1 until that snapshot numbers it among the windows with
    the new title, rather than keeping the old title's number (code review, 2026-10-30)."""
    return window_entry._replace(title=new_title, number_among_windows_with_the_same_title=1)


def replace_title_in_snapshot(snapshot, kind, item_id, new_title):
    """Returns the snapshot with one window's title (kind "window") or one tab's title (kind
    "tab") replaced, for a rename the list shows before a snapshot from the core confirms it."""

    def replace_title_in_window(window_entry):
        if kind == "window":
            return rename_window_entry(window_entry, new_title) if window_entry.window_id == item_id else window_entry
        return window_entry._replace(agent_sessions=tuple(
            entry._replace(tab_title=new_title) if entry.tab_id == item_id else entry for entry in window_entry.agent_sessions))
    return snapshot._replace(monitors=tuple(
        monitor_entry._replace(windows=tuple(replace_title_in_window(window_entry) for window_entry in monitor_entry.windows))
        for monitor_entry in snapshot.monitors))


def build_window_title_shown_in_the_list(window_entry):
    """Returns the window's title, with its number after it for the second and later windows
    with that title ("tandem-repeat-catalogs (2)"): a window opened in the same folder as
    another otherwise looks like the same window (user request, 2026-10-30)."""
    number = window_entry.number_among_windows_with_the_same_title
    return window_entry.title if number == 1 else f"{window_entry.title} ({number})"


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
