"""Builders of what the core gathers from the terminals and the screens and what it makes of
them: a monitor, a tab in the format of a terminal backend's gather_windows, the agent session the
core finds in a tab, and a tab's row in the snapshot, for the tests of the core."""

from agent_command_tab.agent_adapters.claude_code import ClaudeCodeAdapter
from agent_command_tab.snapshot_data_classes import AgentSessionEntry

MONITOR = {"monitor_id": "1", "name": "Built-in", "frame": (0, 0, 2000, 1300), "visible_frame": (0, 0, 2000, 1260)}


def make_gathered_tab(tab_id, session_id, command_line, job_pid=None, title="", title_override_format=None,
                      **changed_first_session_fields):
    """Returns a gathered tab whose first session runs command_line in /p, as process job_pid,
    with changed_first_session_fields changed (job_folder="": a folder that couldn't be read)."""
    return {"tab_id": tab_id, "title": title, "title_override_format": title_override_format,
            "first_session": dict({"session_id": session_id, "command_line": command_line, "job_pid": job_pid, "job_folder": "/p",
                                   "job_started_at": 1.0, "status_set_in_iterm2": None, "status_set_in_iterm2_at": None,
                                   "status": None, "path": "/p"}, **changed_first_session_fields)}


def make_gathered_window(window_id, tabs, terminal_name="iTerm2", frame=None, title="", title_override_format=None):
    """Returns a gathered window holding these gathered tabs; frame None for one without a frame of its own (tmux)."""
    return {"window_id": window_id, "terminal_name": terminal_name, "frame": frame, "title": title,
            "title_override_format": title_override_format, "tabs": tabs}


def make_agent_session_entry(session_id, tab_title, **changed_fields):
    """Returns the snapshot's row of a tab (tab ID "t-<session ID>"): an idle Claude Code in /p,
    with changed_fields changed."""
    return AgentSessionEntry(**dict({
        "tab_id": f"t-{session_id}", "session_id": session_id, "tab_title": tab_title, "tab_title_set_by_user": False,
        "agent_session_name": "", "agent_tool_name": "Claude Code", "working_folder": "/p", "status": "idle"}, **changed_fields))


def make_agent_session(process_id, adapter=None, agent_tool_name="Claude Code", working_folder="/p", started_at=10.0):
    """Returns the core's record of the agent running in a tab (snapshot_builder.resolve_agent_sessions's
    format): in front in its tab and not stopped; a Claude Code unless adapter is given."""
    return {"adapter": adapter or ClaudeCodeAdapter(), "process_id": process_id, "is_in_background": False, "is_stopped": False,
            "agent_tool_name": agent_tool_name, "working_folder": working_folder, "started_at": started_at}
