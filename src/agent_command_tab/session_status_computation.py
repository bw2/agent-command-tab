"""Agent session states, their urgency order, and how a session's state is worked out (PLAN.md section 6).

A state comes from iTerm2's `status` variable plus the core's readings of the session: whether
the permission dialog is still on screen, a screen fingerprint (for "stuck"), and the command
the agent runs (agent_process_tree). Hook events are added as a further source in phase 2.
"""

# "paused": the agent process is stopped (Pause from the list, or Ctrl+Z) until it is resumed.
# "background": idle, but with background processes it started still running (user request,
# 2026-10-28; iterm2_snapshot_builder.session_has_background_work), so it isn't taken for idle.
STATES_IN_URGENCY_ORDER = ["asking", "error", "stuck", "working", "background", "done", "idle", "paused", "unknown", "exited"]
# Between turns: the agent waits at its prompt, so /compact and /clear can be typed into it.
STATES_OF_AN_AGENT_WAITING_AT_ITS_PROMPT = frozenset({"idle", "done", "background"})
# During a turn, as iTerm2's status shows it: a permission prompt in between doesn't end the turn.
STATES_OF_A_TURN_UNDER_WAY = frozenset({"working", "asking"})

# A Claude Code session's own report in `claude agents --json`, for a tab for which iTerm2 has no
# status at all, neither its `status` variable nor what its tab bar shows (for example without
# iTerm2's Claude Code integration hooks; user request, 2026-10-21).
CLAUDE_AGENTS_STATUSES_TO_STATES = {"idle": "idle", "busy": "working", "waiting": "asking"}

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


def status_values_put_the_agent_in_the_same_state(first_status_value, second_status_value):
    """Returns whether two iTerm2 status values give the same state, idle and done counting as
    one (both wait at the prompt; whether a turn just ended is tracked apart)."""
    def at_prompt_or_state(status_value):
        state = map_iterm2_status_value_to_state(status_value)
        return "at its prompt" if state in STATES_OF_AN_AGENT_WAITING_AT_ITS_PROMPT else state
    return at_prompt_or_state(first_status_value) == at_prompt_or_state(second_status_value)


def status_change_ends_a_turn(previous_state, new_state):
    """Returns whether iTerm2's status changing from previous_state to new_state (as mapped by
    map_iterm2_status_value_to_state) ends a turn: done, or idle after working or asking (Codex
    reports only idle when its turn ends)."""
    return new_state == "done" or (new_state == "idle" and previous_state in STATES_OF_A_TURN_UNDER_WAY)


def track_status_value(session_tracking, iterm2_status_value, now, screen_reading=None, ends_no_turn=False):
    """Returns a session's tracking updated for its iTerm2 status value read now.

    The tracking records when the value last changed, when the current turn started (working
    after anything but working or asking: a permission prompt in between doesn't start a new
    turn, unless it was answered No or Esc, which ended the turn) and when the last turn ended
    (status_change_ends_a_turn). The turn's end is kept when the status changes again, so done
    lasts until the tab is seen. A session first seen as done ended its turn by then.

    Args:
        session_tracking: The session's tracking so far, or None for a session seen for the
            first time; it is not changed.
        iterm2_status_value: iTerm2's `status` variable for the session's first pane.
        now: The time of the reading.
        screen_reading: The session's screen reading (compute_session_status_from_sources), or
            None: a permission prompt that a check since it appeared found answered No or Esc
            ended the turn, though iTerm2 still says waiting, so working next is the turn the
            next prompt starts (code review, 2026-10-28).
        ends_no_turn: Whether the value replaces one that may belong to no turn at all, so a
            change from working to it records no turn end (Claude Code's own idle over a working
            that iTerm2 set without a turn; code review, 2026-10-28).

    Returns:
        session_tracking itself when the value is unchanged, else a new dict with "status_value",
        "status_change_time", "last_time_tab_was_seen", and once known "turn_started_at" and
        "last_turn_ended_at".
    """
    new_state = map_iterm2_status_value_to_state(iterm2_status_value)
    if session_tracking is None:
        tracking = {"status_value": iterm2_status_value, "status_change_time": now, "last_time_tab_was_seen": None}
        if status_change_ends_a_turn(None, new_state):
            tracking["last_turn_ended_at"] = now
        return tracking
    if session_tracking["status_value"] == iterm2_status_value:
        return session_tracking
    previous_state = map_iterm2_status_value_to_state(session_tracking["status_value"])
    tracking = dict(session_tracking, status_value=iterm2_status_value, status_change_time=now)
    turn_was_interrupted = (permission_dialog_was_answered(previous_state, session_tracking["status_change_time"], screen_reading)
                            and bool(screen_reading.get("turn_was_interrupted")))
    if new_state == "working" and (previous_state not in STATES_OF_A_TURN_UNDER_WAY or turn_was_interrupted):
        tracking["turn_started_at"] = now
    if status_change_ends_a_turn(previous_state, new_state) and not ends_no_turn:
        tracking["last_turn_ended_at"] = now
    return tracking


def compute_session_status_from_sources(iterm2_status_value, iterm2_status_change_time, last_turn_ended_at,
                                        last_time_tab_was_seen, tab_is_seen_now, screen_reading, stuck_threshold_seconds,
                                        now):
    """Returns (state, reason) for one agent session.

    - "asking" ends when a screen check made after the status changed no longer finds the
      tool's permission dialog: Claude Code reports nothing when a prompt is answered, and
      iTerm2's status stays "waiting" until the approved tool finishes (Phase 0).
    - "stuck" is working with no screen change (progress noise masked) for the threshold while
      running no command of its own: an agent waiting on a long command it started is working.
    - "done" means the last turn ended after the tab was last seen (selected in the key iTerm2
      window while iTerm2 was the active app), whether the status now says done or idle, so it
      lasts until the tab is seen; a turn that ended while the tab was seen counts as idle.

    Args:
        iterm2_status_value: iTerm2's `status` variable for the session's first pane.
        iterm2_status_change_time: When that value was first observed (seconds since the epoch).
        last_turn_ended_at: When the status last changed in a way that ends a turn
            (status_change_ends_a_turn), or None if it hasn't since the session was first seen;
            track_status_value sets it for a session first seen as done.
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
    if state in STATES_OF_AN_AGENT_WAITING_AT_ITS_PROMPT and last_turn_ended_at is not None:
        if tab_is_seen_now or (last_time_tab_was_seen is not None and last_time_tab_was_seen >= last_turn_ended_at):
            return "idle", "turn finished and seen"
        return "done", "turn finished"
    return state, ""


def pick_most_urgent_state(states):
    """Returns the most urgent of the given states, or "unknown" when there are none."""
    ranked_states = [state for state in STATES_IN_URGENCY_ORDER if state in set(states)]
    return ranked_states[0] if ranked_states else "unknown"


def split_agent_sessions_into_pausable_and_resumable(agent_session_entries):
    """Returns (the agent sessions a pause would stop, the paused ones a resume would continue).
    An agent suspended with Ctrl+Z by hand is in both, since a pause still stops the processes
    below it; an agent whose process has exited is in neither."""
    return ([entry for entry in agent_session_entries if entry.status != "exited"
             and not (entry.status == "paused" and entry.is_paused_with_the_processes_below_it)],
            [entry for entry in agent_session_entries if entry.status == "paused"])


def summarize_state_counts_in_urgency_order(states):
    """Returns text such as "1 asking, 2 working" for a window's or hidden group's sessions."""
    return ", ".join(f"{states.count(state)} {state}" for state in STATES_IN_URGENCY_ORDER if state in states)
