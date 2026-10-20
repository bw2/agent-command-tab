"""Builds the published snapshot from plain data gathered from iTerm2 (PLAN.md section 4).

Everything here is pure: the connection thread gathers iTerm2's windows, tabs and session
variables into plain dicts (the same shape the test fixtures use), and this module turns them
into a ComputerSnapshot.

Shape of a gathered window (see DashboardCore.gather_iterm2_windows and DashboardCore.gather_iterm2_tab):
    {"window_id": str, "frame": (x, y, width, height), "title": str, "title_override_format": str or None,
     "tabs": [{"tab_id": str, "title": str, "title_override_format": str or None,
               "first_session": {"session_id": str, "command_line": str, "job_pid": int or None,
                                 "job_folder": str (the job process's current folder, or ""),
                                 "job_started_at": float or None (when the job process started),
                                 "status": str or None, "path": str}}]}
"""

import collections
import os

from agent_command_tab.agent_adapters import find_agent_tool_adapter_for_tab
from agent_command_tab.monitor_assignment import assign_windows_to_monitors_by_largest_overlap
from agent_command_tab.session_preview_computation import choose_preview_text
from agent_command_tab.session_status_computation import compute_session_status_from_sources
from agent_command_tab.snapshot_data_classes import (AgentSessionEntry, ComputerSnapshot, MonitorEntry,
                                                     WindowEntry)

PLACEHOLDER_MONITOR = {"monitor_id": "unknown", "name": "", "frame": (0, 0, 0, 0), "visible_frame": (0, 0, 0, 0)}

DisplayedState = collections.namedtuple("DisplayedState", ["state", "since", "status_change_time"])
DisplayedState.__doc__ = ("What a session's row showed in a snapshot: its state, since when, and when iTerm2's status "
                          "value had last changed then (compute_displayed_state_since).")


def build_folder_display_name(folder_path):
    """Returns the last part of a folder path ("~" for the home folder), or "" for no path."""
    if not folder_path:
        return ""
    normalized_path = os.path.normpath(folder_path)
    if normalized_path == os.path.expanduser("~"):
        return "~"
    return os.path.basename(normalized_path) or normalized_path


def resolve_agent_sessions(gathered_windows, previous_agent_sessions, specific_adapters, generic_adapter,
                           process_is_alive, process_is_stopped):
    """Returns which tabs run an agent, keeping an agent whose tab's foreground job changed while it lives.

    PLAN.md section 4: a `commandLine` change alone does not mean the agent exited (Claude Code's
    Ctrl+G opens an editor in the same terminal, and Ctrl+Z suspends the agent). Such a tab keeps
    its agent, marked as in the background, while the process recorded at detection still exists.

    Args:
        gathered_windows: Gathered window dicts (module docstring).
        previous_agent_sessions: The result of the previous call, or {}.
        specific_adapters: Adapters tried in order.
        generic_adapter: The generic adapter.
        process_is_alive: function(process ID) -> bool.
        process_is_stopped: function(process ID) -> bool (paused, or suspended with Ctrl+Z).

    Returns:
        {session_id: {"adapter": AgentToolAdapter, "process_id": int or None, "is_in_background": bool,
                      "is_stopped": bool, "agent_tool_name": str, "working_folder": the agent
                      process's current folder or "", "started_at": when the agent process started, or None}}.
    """
    agent_sessions = {}
    for gathered_window in gathered_windows:
        for gathered_tab in gathered_window["tabs"]:
            first_session = gathered_tab.get("first_session")
            if not first_session:
                continue
            session_id = first_session["session_id"]
            adapter = find_agent_tool_adapter_for_tab(first_session.get("command_line"), first_session.get("status"),
                                                      specific_adapters, generic_adapter)
            previous_agent_session = previous_agent_sessions.get(session_id)
            if adapter is not None:
                process_id = first_session.get("job_pid")
                agent_sessions[session_id] = {
                    "adapter": adapter, "process_id": process_id, "is_in_background": False,
                    "is_stopped": bool(process_id) and process_is_stopped(process_id),  # until iTerm2 sees the shell in front
                    "agent_tool_name": adapter.name_agent_tool_for_command_line(first_session.get("command_line")),
                    "working_folder": first_session.get("job_folder") or "",
                    "started_at": first_session.get("job_started_at")}
            elif (previous_agent_session is not None and previous_agent_session["process_id"]
                  and process_is_alive(previous_agent_session["process_id"])):
                agent_sessions[session_id] = dict(previous_agent_session, is_in_background=True,
                                                  is_stopped=process_is_stopped(previous_agent_session["process_id"]))
    return agent_sessions


def compute_displayed_state_since(state, raw_status_change_time, previous_displayed_state, now):
    """Returns since when the shown state holds, for the elapsed time in the row.

    Args:
        state: The state shown now.
        raw_status_change_time: When iTerm2's status value last changed.
        previous_displayed_state: The DisplayedState from the previous snapshot, or None.
        now: The current time.

    Returns:
        The previous time while the state is unchanged; the status change time when a status
        report newer than the previous snapshot's caused the change; otherwise now (for example
        "done" became "idle" when the tab was seen, also when the status had said idle since
        before then).
    """
    if previous_displayed_state is None:
        return raw_status_change_time
    if previous_displayed_state.state == state:
        return previous_displayed_state.since
    if raw_status_change_time > previous_displayed_state.status_change_time:
        return raw_status_change_time
    return now


def build_agent_session_entry(gathered_tab, agent_session, claude_agent_record, session_tracking,
                              previous_displayed_state, screen_reading, stuck_threshold_seconds, seen_session_id,
                              highlighted_session_id, now):
    """Returns the AgentSessionEntry for one agent tab.

    Args:
        gathered_tab: A gathered tab dict (module docstring).
        agent_session: The tab's entry from resolve_agent_sessions.
        claude_agent_record: The tab's entry from `claude agents --json` (matched by the agent's
            process ID), or None.
        session_tracking: {"status_change_time": float, "last_time_tab_was_seen": float or None,
            "last_turn_ended_at": float (once a turn ended), "finished_turn_cleared_at": float
            (after a /clear from the list)} for the tab's first session, or None.
        previous_displayed_state: The DisplayedState shown for this session in the previous
            snapshot, or None.
        screen_reading: The core's latest screen reading for the session, or None (see
            session_status_computation.compute_session_status_from_sources).
        stuck_threshold_seconds: The stuck time limit from the settings.
        seen_session_id: The session being seen right now (selected in the key iTerm2 window
            while iTerm2 is active), or None.
        highlighted_session_id: The session shown as in front (R41), or None.
        now: The current time.
    """
    first_session = gathered_tab["first_session"]
    session_id = first_session["session_id"]
    session_tracking = session_tracking or {}
    claude_agent_record = claude_agent_record or {}
    # PLAN.md section 5: the agent process's folder first; iTerm2's `path` can be empty or stale.
    working_folder = claude_agent_record.get("cwd") or agent_session["working_folder"] or first_session.get("path") or ""
    agent_session_name = claude_agent_record.get("name") or ""
    tab_title_set_by_user = bool(gathered_tab.get("title_override_format"))
    if tab_title_set_by_user:
        tab_title = gathered_tab.get("title") or gathered_tab["title_override_format"]
    else:
        tab_title = agent_session_name or build_folder_display_name(working_folder) or gathered_tab.get("title") or ""
    is_paused_with_the_processes_below_it = agent_session.get("is_paused_with_the_processes_below_it", False)
    if agent_session["is_stopped"]:
        # Paused from the list, or suspended with Ctrl+Z by hand, which leaves the processes the
        # agent started in their own process groups running: stopped until it is resumed.
        state = "paused"
        reason = ("paused: the agent and the processes below it are stopped until it is resumed"
                  if is_paused_with_the_processes_below_it
                  else "suspended with Ctrl+Z: the agent is stopped, but commands it started may still run")
        status_since = (previous_displayed_state.since if previous_displayed_state and previous_displayed_state.state == "paused"
                        else now)
    elif agent_session["is_in_background"]:
        # Another program (an editor opened with Ctrl+G) is in front, or a resumed agent is coming
        # back to the front; iTerm2 cleared the status, so the row keeps what it showed before,
        # except "paused", which ended when the agent was resumed.
        state, status_since = (
            (previous_displayed_state.state, previous_displayed_state.since)
            if previous_displayed_state and previous_displayed_state.state != "paused" else ("unknown", now))
        reason = "another program in front of the agent"
    else:
        # A finished turn stops waiting to be seen when the tab is seen, or when /clear cleared it.
        times_the_finished_turn_was_dealt_with = [time_ for time_ in (session_tracking.get("last_time_tab_was_seen"),
                                                                      session_tracking.get("finished_turn_cleared_at"))
                                                  if time_ is not None]
        state, reason = compute_session_status_from_sources(
            first_session.get("status"),
            session_tracking.get("status_change_time", now),
            session_tracking.get("last_turn_ended_at"),
            max(times_the_finished_turn_was_dealt_with, default=None),
            session_id == seen_session_id,
            screen_reading,
            stuck_threshold_seconds,
            now,
        )
        status_since = compute_displayed_state_since(state, session_tracking.get("status_change_time", now),
                                                     previous_displayed_state, now)
    running_command = (screen_reading or {}).get("running_command") if state == "working" else None
    return AgentSessionEntry(
        tab_id=gathered_tab["tab_id"],
        session_id=session_id,
        tab_title=tab_title,
        tab_title_set_by_user=tab_title_set_by_user,
        agent_session_name=agent_session_name,
        agent_tool_name=agent_session["agent_tool_name"],
        working_folder=working_folder,
        status=state,
        status_reason=reason,
        status_since=status_since,
        is_paused_with_the_processes_below_it=state == "paused" and is_paused_with_the_processes_below_it,
        is_in_front=session_id == highlighted_session_id,
        peer_messaging_address=agent_session_name,
        last_time_tab_was_seen=session_tracking.get("last_time_tab_was_seen"),
        running_command_name=running_command[0] if running_command else "",
        running_command_started_at=running_command[1] if running_command else None,
        preview_line=choose_preview_text(state, (screen_reading or {}).get("conversation_preview")),
        context_tokens=(screen_reading or {}).get("context_tokens"),
        memory_bytes=(screen_reading or {}).get("memory_bytes"),
        cpu_percent=(screen_reading or {}).get("cpu_percent"),
        started_at=agent_session.get("started_at"),
        slash_commands=agent_session["adapter"].slash_commands,
    )


def build_window_entry(gathered_window, agent_session_entries):
    """Returns the WindowEntry for a window that has at least one agent session.

    A window without a title of its own is named after its first agent session's folder (R9.1).
    """
    window_title_set_by_user = bool(gathered_window.get("title_override_format"))
    if window_title_set_by_user:
        title = gathered_window.get("title") or gathered_window["title_override_format"]
    else:
        title = build_folder_display_name(agent_session_entries[0].working_folder) or gathered_window.get("title") or ""
    return WindowEntry(
        window_id=gathered_window["window_id"],
        title=title,
        window_title_set_by_user=window_title_set_by_user,
        frame=tuple(gathered_window.get("frame") or (0, 0, 0, 0)),
        last_prompt_time=None,
        total_tab_count=len(gathered_window["tabs"]),
        agent_sessions=tuple(agent_session_entries),
        selected_tab_id=gathered_window.get("selected_tab_id"),
    )


def build_computer_snapshot(gathered_windows, agent_sessions, monitors, window_order, session_tracking_by_session_id,
                            displayed_state_by_session_id, screen_readings_by_session_id, stuck_threshold_seconds,
                            claude_agents_by_process_id, seen_session_id, highlighted_session_id, computer_id,
                            computer_name, now):
    """Returns the ComputerSnapshot for the gathered iTerm2 state.

    Args:
        gathered_windows: Gathered window dicts in iTerm2's order (module docstring).
        agent_sessions: The tabs that run an agent, from resolve_agent_sessions.
        monitors: Monitor dicts {"monitor_id", "name", "frame", "visible_frame"} from the main
            thread, in NSScreen's order (menu-bar monitor first); empty before the first screen
            layout message. The snapshot lists the monitor groups left to right (R31, Q4).
        window_order: Window IDs in the list's order (window_list_ordering).
        session_tracking_by_session_id: Per-session tracking dicts (see build_agent_session_entry).
        displayed_state_by_session_id: {session_id: DisplayedState} from the previous snapshot.
        screen_readings_by_session_id: The core's latest screen readings (see build_agent_session_entry).
        stuck_threshold_seconds: The stuck time limit from the settings.
        claude_agents_by_process_id: {process ID: record from `claude agents --json`}.
        seen_session_id: See build_agent_session_entry.
        highlighted_session_id: See build_agent_session_entry.
        computer_id: A stable ID for this computer.
        computer_name: The computer's name.
        now: The current time.

    Returns:
        A ComputerSnapshot. Windows without agent sessions are left out.
    """
    window_entries_by_window_id = {}
    for gathered_window in gathered_windows:
        agent_session_entries = []
        for gathered_tab in gathered_window["tabs"]:
            first_session = gathered_tab.get("first_session")
            agent_session = agent_sessions.get(first_session["session_id"]) if first_session else None
            if agent_session is None:
                continue
            agent_session_entries.append(build_agent_session_entry(
                gathered_tab, agent_session, claude_agents_by_process_id.get(agent_session["process_id"]),
                session_tracking_by_session_id.get(first_session["session_id"]),
                displayed_state_by_session_id.get(first_session["session_id"]),
                screen_readings_by_session_id.get(first_session["session_id"]), stuck_threshold_seconds,
                seen_session_id, highlighted_session_id, now))
        if agent_session_entries:
            window_entries_by_window_id[gathered_window["window_id"]] = build_window_entry(gathered_window, agent_session_entries)

    monitors = sorted(monitors, key=lambda monitor: (monitor["frame"][0], monitor["frame"][1])) or [PLACEHOLDER_MONITOR]
    monitor_index_by_window_id = assign_windows_to_monitors_by_largest_overlap(
        {window_id: window_entry.frame for window_id, window_entry in window_entries_by_window_id.items()},
        [monitor["frame"] for monitor in monitors])
    ordered_window_ids = [window_id for window_id in window_order if window_id in window_entries_by_window_id]
    ordered_window_ids += [window_id for window_id in window_entries_by_window_id if window_id not in set(window_order)]
    monitor_entries = []
    for monitor_index, monitor in enumerate(monitors):
        monitor_entries.append(MonitorEntry(
            monitor_id=monitor["monitor_id"],
            name=monitor["name"],
            frame=tuple(monitor["frame"]),
            visible_frame=tuple(monitor["visible_frame"]),
            windows=tuple(window_entries_by_window_id[window_id] for window_id in ordered_window_ids
                          if monitor_index_by_window_id.get(window_id, 0) == monitor_index),
        ))
    return ComputerSnapshot(computer_id=computer_id, computer_name=computer_name, taken_at=now,
                            monitors=tuple(monitor_entries))
