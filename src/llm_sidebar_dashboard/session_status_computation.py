"""Agent session states, their urgency order, and how a session's state is worked out (PLAN.md section 6).

A state comes from iTerm2's `status` variable plus the core's readings of the session: whether
the permission dialog is still on screen, a screen fingerprint (for "stuck"), and the command
the agent runs (agent_process_tree). Hook events are added as a further source in phase 2.
"""

STATES_IN_URGENCY_ORDER = ["asking", "error", "stuck", "working", "done", "idle", "unknown", "exited"]

ITERM2_STATUS_WORDS_TO_STATES = {
    "idle": "idle",
    "working": "working",
    "waiting": "asking",
    "done": "done",
    "failed": "error",
    "error": "error",
}


def map_iterm2_status_value_to_state(iterm2_status_value):
    """Returns the state for a value of iTerm2's `status` variable, or None for no signal.

    The variable is fed by OSC 7501 reports (fixed words) and OSC 21337 (free text such as
    "Working"), so known words are matched case-insensitively and anything else is ignored.

    Args:
        iterm2_status_value: The variable's value, or None when unset.
    """
    if not iterm2_status_value:
        return None
    return ITERM2_STATUS_WORDS_TO_STATES.get(str(iterm2_status_value).strip().lower())


def update_permission_dialog_reading(screen_reading, dialog_is_showing, interrupted_line_count,
                                     iterm2_status_change_time, now):
    """Returns the screen reading updated with one permission dialog check of an asking session.

    The dialog counts as answered once it was seen and then went away: approved, or denied when
    a new "Interrupted" line appeared ("No" or Esc). A dialog never seen since the session
    started asking (wording the adapter doesn't recognize, or not drawn yet) keeps the session
    asking, unless a new "Interrupted" line shows that the turn ended (PLAN.md section 6: when
    the match is ambiguous, the request stays open).

    Args:
        screen_reading: The session's reading so far; it is not changed.
        dialog_is_showing: Whether this check found the adapter's permission dialog.
        interrupted_line_count: How many of the adapter's "Interrupted" lines this check found.
        iterm2_status_change_time: When iTerm2's status last changed; earlier checks belong to
            an earlier prompt and are forgotten.
        now: The time of this check.

    Returns:
        A new dict with "permission_dialog_is_showing", "permission_dialog_checked_at",
        "interrupted_line_count_while_asking", and once answered "permission_dialog_gone_since"
        and "turn_was_interrupted".
    """
    reading = dict(screen_reading)
    if reading.get("permission_dialog_checked_at", 0) < iterm2_status_change_time:  # a new prompt: forget the last one
        for field_name in ("permission_dialog_is_showing", "permission_dialog_gone_since", "turn_was_interrupted"):
            reading.pop(field_name, None)
        reading["interrupted_line_count_while_asking"] = interrupted_line_count
    if dialog_is_showing:
        reading["interrupted_line_count_while_asking"] = interrupted_line_count
        reading.pop("permission_dialog_gone_since", None)  # shown again: asking again
        reading["turn_was_interrupted"] = False
    else:
        turn_was_interrupted = interrupted_line_count > reading["interrupted_line_count_while_asking"]
        if reading.get("permission_dialog_is_showing") or turn_was_interrupted:
            reading.setdefault("permission_dialog_gone_since", now)
            reading["turn_was_interrupted"] = turn_was_interrupted
    reading["permission_dialog_is_showing"] = dialog_is_showing
    reading["permission_dialog_checked_at"] = now
    return reading


def permission_dialog_was_answered(state, iterm2_status_change_time, screen_reading):
    """Returns whether an asking session's permission dialog was answered, by a screen check made
    after the status last changed (see update_permission_dialog_reading)."""
    screen_reading = screen_reading or {}
    return (state == "asking" and screen_reading.get("permission_dialog_checked_at", 0) > iterm2_status_change_time
            and not screen_reading.get("permission_dialog_is_showing")
            and "permission_dialog_gone_since" in screen_reading)


def compute_session_status_from_sources(iterm2_status_value, iterm2_status_change_time, last_time_tab_was_seen,
                                        tab_is_seen_now, screen_reading, stuck_threshold_seconds, now):
    """Returns (state, reason) for one agent session.

    - "asking" ends when a screen check made after the status changed no longer finds the
      tool's permission dialog: Claude Code reports nothing when a prompt is answered, and
      iTerm2's status stays "waiting" until the approved tool finishes (Phase 0).
    - "stuck" is working with no screen change (progress noise masked) for the threshold while
      running no command of its own: an agent waiting on a long command it started is working.
    - "done" means the turn ended after the tab was last seen (selected in the key iTerm2
      window while iTerm2 was the active app); a turn that ended while the tab was seen counts
      as idle.

    Args:
        iterm2_status_value: iTerm2's `status` variable for the session's first pane.
        iterm2_status_change_time: When that value was first observed (seconds since the epoch).
        last_time_tab_was_seen: When the tab was last seen, or None if never.
        tab_is_seen_now: Whether the tab is being seen at this moment.
        screen_reading: {"permission_dialog_is_showing": bool, "permission_dialog_checked_at":
            float, "permission_dialog_gone_since": float, "turn_was_interrupted": bool,
            "fingerprint_changed_at": float, "running_command": (name, started at) or None,
            "command_last_seen_running_at": float} (any key may be missing), or None.
        stuck_threshold_seconds: How long a working session may show no screen change.
        now: The current time.

    Returns:
        A (state, reason) tuple; state is one of STATES_IN_URGENCY_ORDER.
    """
    state = map_iterm2_status_value_to_state(iterm2_status_value)
    screen_reading = screen_reading or {}
    if state is None:
        return "unknown", "no status report"
    working_since = iterm2_status_change_time
    if permission_dialog_was_answered(state, iterm2_status_change_time, screen_reading):
        if screen_reading.get("turn_was_interrupted"):
            # "No" or Esc ends the turn; Claude Code waits for new instructions while iTerm2 still says waiting.
            return "idle", "turn interrupted"
        state = "working"
        working_since = screen_reading["permission_dialog_gone_since"]
    if state == "working":
        if screen_reading.get("running_command"):
            return "working", f"running {screen_reading['running_command'][0]}"  # waiting on its command, never stuck
        # Stuck means thinking with nothing new on screen, so the clock also restarts when a command ends.
        quiet_since = max(screen_reading.get("fingerprint_changed_at", working_since), working_since,
                          screen_reading.get("command_last_seen_running_at", 0))
        if now - quiet_since >= stuck_threshold_seconds:
            return "stuck", f"thinking with no screen change for {max(1, round((now - quiet_since) / 60))} min"
        return "working", ""
    if state == "done":
        if tab_is_seen_now or (last_time_tab_was_seen is not None and last_time_tab_was_seen >= iterm2_status_change_time):
            return "idle", "turn finished and seen"
        return "done", "turn finished"
    return state, ""


def pick_most_urgent_state(states):
    """Returns the most urgent of the given states, or "unknown" when there are none."""
    ranked_states = [state for state in STATES_IN_URGENCY_ORDER if state in set(states)]
    return ranked_states[0] if ranked_states else "unknown"


def summarize_state_counts_in_urgency_order(states):
    """Returns text such as "1 asking, 2 working" for a window's or hidden group's sessions."""
    return ", ".join(f"{states.count(state)} {state}" for state in STATES_IN_URGENCY_ORDER if state in states)
