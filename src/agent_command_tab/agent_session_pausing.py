"""Pausing and resuming an agent session: the agent and every process below it (user request, 2026-10-20).

Pause before going offline, resume once back online. Checked with real Claude Code and Codex
sessions on 2026-10-20: paused for 2 minutes mid-answer and mid-command, both carried on.

A plain SIGSTOP is not enough. The shell notices its job stopped and takes the terminal back, so
after a plain SIGCONT the agent runs as a background job and stops again at its next key read.
So a pause presses Ctrl+Z, as you would (Claude Code and Codex then tidy up their screen and
suspend), and stops every process below the agent, which may run in their own process groups
(Bash commands, MCP servers) that Ctrl+Z alone leaves running; not the dashboard itself, when it
was started from the agent's commands. A resume types `fg` at the shell's
prompt, which gives the agent the terminal back, then continues the processes below it.

`fg` is typed only when that is safe: the shell that runs the agent must be in front (not a
shell or tmux started at its prompt meanwhile, whose prompt may look the same), its cursor line
must look exactly as it did right after the pause (so nothing was typed there since, which `fg`
would join and run), and no other job may be suspended in that shell (a bare `fg` could bring
back that one instead). Ctrl+U goes
first, clearing what the shell's line held already right after the pause, so `fg` runs alone:
keys typed while the pause ran, or a reply the terminal sent to the agent's question about the
cursor position that reached the shell instead (seen live on 2026-10-20 as "22;3;1R").
"""

import asyncio
import collections
import os
import signal

import iterm2

from agent_command_tab.agent_process_tree import (find_the_shell_running_the_agent,
                                                  list_descendant_processes_and_other_users_process_ids,
                                                  list_other_stopped_jobs_of_the_agents_shell,
                                                  list_the_process_and_its_ancestor_ids, process_has_its_terminal,
                                                  process_is_stopped, read_name_of_any_process, read_process_info)
from agent_command_tab.dashboard_core_api import ActionResult

CONTROL_Z = "\x1a"
CONTROL_U = "\x15"  # clears the shell's input line (bash, zsh, fish)
SECONDS_TO_WAIT_FOR_CONTROL_Z = 2
SECONDS_TO_WAIT_FOR_SIGTSTP = 1
SECONDS_TO_WAIT_FOR_THE_SHELL_PROMPT = 2
SECONDS_TO_WAIT_FOR_FG = 3
SECONDS_BETWEEN_CHECKS = 0.1

PauseRecord = collections.namedtuple(
    "PauseRecord", ["agent_process", "agent_tool_name", "text_around_the_cursor", "stopped_processes"])
PauseRecord.__doc__ = ("What a pause left behind (kept across restarts by pause_records_store): the agent's (process "
                       "ID, start time) and tool name (\"Codex\"), to list it again after a restart; the shell's "
                       "cursor line right after the pause, as (text before the cursor, "
                       "text after it, which holds a right-side prompt), or None when no prompt showed or the agent "
                       "had been suspended by hand; and the (process ID, start time) of each process it stopped "
                       "below the agent.")


def send_signal_to_processes(process_ids, signal_number):
    """Sends a signal to each process, skipping those that have ended meanwhile and those this
    user may not signal (a command the agent ran with sudo); returns the IDs of those it reached."""
    signaled_process_ids = []
    for process_id in process_ids:
        try:
            os.kill(process_id, signal_number)
        except (ProcessLookupError, PermissionError):
            continue
        signaled_process_ids.append(process_id)
    return signaled_process_ids


def pause_has_ended(pause_record):
    """Returns whether the paused agent runs again (fg typed by hand) or is gone (its tab
    closed), judged by its own process, also after the dashboard restarted."""
    agent_process_id, started_at = pause_record.agent_process
    agent = read_process_info(agent_process_id)
    return agent is None or agent.started_at != started_at or not process_is_stopped(agent_process_id)


def continue_processes_stopped_at_pause(stopped_processes):
    """Sends SIGCONT to each process a pause stopped that is still stopped, unless its process
    ID now belongs to another process (a different start time).

    Args:
        stopped_processes: A PauseRecord's stopped_processes.
    """
    process_ids_to_continue = []
    for process_id, started_at in stopped_processes:
        process = read_process_info(process_id)
        if process is not None and process.started_at == started_at and process_is_stopped(process_id):
            process_ids_to_continue.append(process_id)
    send_signal_to_processes(process_ids_to_continue, signal.SIGCONT)


async def wait_until(condition, seconds):
    """Returns whether condition() became true within seconds, checking every SECONDS_BETWEEN_CHECKS."""
    for _ in range(round(seconds / SECONDS_BETWEEN_CHECKS)):
        if condition():
            return True
        await asyncio.sleep(SECONDS_BETWEEN_CHECKS)
    return condition()


async def read_text_around_the_cursor(session):
    """Returns (text before the cursor, text after it) on the cursor's line, joining the screen
    rows a long line wraps over and leaving out empty cells, or None when the cursor isn't on screen."""
    contents = await session.async_get_screen_contents()
    cursor = contents.cursor_coord
    cursor_row = cursor.y - contents.windowed_coord_range.coordRange.start.y
    if not 0 <= cursor_row < contents.number_of_lines:
        return None
    first_row = cursor_row
    while first_row > 0 and not contents.line(first_row - 1).hard_eol:
        first_row -= 1
    cursor_line = contents.line(cursor_row)
    cells_before_the_cursor = []
    for x in range(cursor.x):
        try:
            cells_before_the_cursor.append(cursor_line.string_at(x))
        except IndexError:  # past the cells written on this row
            break
    text_on_the_row_before_the_cursor = "".join(cells_before_the_cursor)
    text_before = "".join(contents.line(row).string for row in range(first_row, cursor_row)) + text_on_the_row_before_the_cursor
    text_after = cursor_line.string[len(text_on_the_row_before_the_cursor):]
    row = cursor_row
    while not contents.line(row).hard_eol and row + 1 < contents.number_of_lines:
        row += 1
        text_after += contents.line(row).string
    return text_before.replace("\x00", ""), text_after.replace("\x00", "")


async def read_the_shell_prompt(session, agent_process_id):
    """Returns the cursor line once the shell has drawn its prompt, waiting up to
    SECONDS_TO_WAIT_FOR_THE_SHELL_PROMPT for a slow prompt, or None. The prompt counts as drawn
    once the shell has taken the terminal back from the agent and the cursor line, not empty,
    reads the same twice in a row (until then the agent's own input line may still show)."""
    previous_text_around_the_cursor = None
    for _ in range(round(SECONDS_TO_WAIT_FOR_THE_SHELL_PROMPT / SECONDS_BETWEEN_CHECKS)):
        await asyncio.sleep(SECONDS_BETWEEN_CHECKS)
        if process_has_its_terminal(agent_process_id):
            continue
        try:
            text_around_the_cursor = await read_text_around_the_cursor(session)
        except iterm2.RPCException:
            return None  # the tab closed meanwhile; the pause's record still lists what it stopped
        if (text_around_the_cursor is not None and text_around_the_cursor[0].strip()
                and text_around_the_cursor == previous_text_around_the_cursor):
            return text_around_the_cursor
        previous_text_around_the_cursor = text_around_the_cursor
    return None


def list_processes_below_the_agent_to_stop(agent_process_id):
    """Returns ({process ID: ProcessInfo} of the processes below the agent a pause stops, the IDs
    of those below it that belong to another user, which it can't stop: a command run with sudo).
    Leaves out the dashboard's own process, those it runs in and those it started, so a
    dashboard started from the agent's own commands keeps running."""
    dashboard_process_ids = set(list_the_process_and_its_ancestor_ids(os.getpid())) | {
        process.process_id
        for process in list_descendant_processes_and_other_users_process_ids(os.getpid(), maximum_depth=None)[0]}
    processes_below, other_users_process_ids_below = list_descendant_processes_and_other_users_process_ids(
        agent_process_id, maximum_depth=None)
    return ({process.process_id: process for process in processes_below if process.process_id not in dashboard_process_ids},
            other_users_process_ids_below)


async def pause_agent_session(session, agent_process_id, agent_tool_name):
    """Suspends the agent like Ctrl+Z, then stops every process below it.

    An agent that doesn't take Ctrl+Z as "suspend" (an older Claude Code took it as undo) gets
    SIGTSTP, the signal Ctrl+Z sends in an ordinary terminal, and SIGSTOP, which nothing can
    ignore, if that doesn't stop it either. An agent suspended by hand already only gets the
    processes below it stopped (Ctrl+Z leaves those in their own process groups running); its
    record then holds no prompt, since something may have been typed at the shell meanwhile.

    Args:
        session: The tab's iterm2.Session.
        agent_process_id: The agent's process.
        agent_tool_name: The agent's tool, as its row names it.

    Returns:
        (ActionResult, PauseRecord), or (a failed ActionResult, None) when the agent is gone. The
        ActionResult also fails, saying which, when processes below the agent run as another user
        (with sudo) and keep running; the record then lists the others.
    """
    agent = read_process_info(agent_process_id)
    if agent is None:
        return ActionResult(False, "the agent's process has ended"), None
    was_suspended_by_hand = process_is_stopped(agent_process_id)
    processes_below, other_users_process_ids_below = list_processes_below_the_agent_to_stop(agent_process_id)
    if not was_suspended_by_hand:
        await session.async_send_text(CONTROL_Z, suppress_broadcast=True)
        if not await wait_until(lambda: process_is_stopped(agent_process_id), SECONDS_TO_WAIT_FOR_CONTROL_Z):
            send_signal_to_processes([agent_process_id], signal.SIGTSTP)
            if not await wait_until(lambda: process_is_stopped(agent_process_id), SECONDS_TO_WAIT_FOR_SIGTSTP):
                send_signal_to_processes([agent_process_id], signal.SIGSTOP)
        processes_started_meanwhile, other_users_process_ids_started_meanwhile = list_processes_below_the_agent_to_stop(
            agent_process_id)
        processes_below.update(processes_started_meanwhile)
        other_users_process_ids_below = sorted(set(other_users_process_ids_below) | set(other_users_process_ids_started_meanwhile))
    stopped_process_ids = set(send_signal_to_processes(sorted(processes_below), signal.SIGSTOP))
    names_of_processes_left_running = sorted(  # not those that ended meanwhile
        [process.name for process_id, process in processes_below.items()
         if process_id not in stopped_process_ids and read_process_info(process_id) is not None]
        + [name for name in map(read_name_of_any_process, other_users_process_ids_below) if name])
    text_around_the_cursor = None if was_suspended_by_hand else await read_the_shell_prompt(session, agent_process_id)
    pause_record = PauseRecord((agent_process_id, agent.started_at), agent_tool_name, text_around_the_cursor, sorted(
        (process_id, process.started_at) for process_id, process in processes_below.items() if process_id in stopped_process_ids))
    if names_of_processes_left_running:
        return ActionResult(False, "paused, but these processes below the agent run as another user (with sudo), so they "
                                   "keep running: " + ", ".join(names_of_processes_left_running)), pause_record
    return ActionResult(True), pause_record


async def find_reason_not_to_type_fg(session, agent_process_id, pause_record):
    """Returns why typing `fg` into the tab isn't safe now, or "" when it is (module docstring).
    Checked while everything is still stopped, so no output moves the prompt meanwhile."""
    if pause_record is None or pause_record.text_around_the_cursor is None:
        return ("the dashboard didn't see the shell's prompt after a pause (the agent was suspended with Ctrl+Z by "
                "hand, or no prompt showed), so it can't tell whether something is typed there; type fg in the tab")
    shell_process_id = find_the_shell_running_the_agent(agent_process_id)
    if shell_process_id is None or not process_has_its_terminal(shell_process_id):
        return ("another program is in front of the shell that runs the agent (a shell or tmux started in the tab, say), "
                "so fg would go to it; leave that program and resume again")
    if list_other_stopped_jobs_of_the_agents_shell(agent_process_id):
        return "another job is suspended in the tab's shell, so fg might bring back that one; type jobs, then fg %N, in the tab"
    if await read_text_around_the_cursor(session) != pause_record.text_around_the_cursor:
        return ("the tab's shell line has changed since the pause (something typed there, or the prompt redrawn), "
                "so typing fg could run something else; check the line and type fg there")
    return ""


async def resume_agent_session(session, agent_process_id, pause_record):
    """Types `fg` at the shell's prompt, after Ctrl+U (module docstring), which continues the
    agent with the terminal back, then continues the processes the pause stopped below it. Types
    nothing unless that is safe (find_reason_not_to_type_fg).

    Args:
        session: The tab's iterm2.Session.
        agent_process_id: The agent's process.
        pause_record: The PauseRecord of the agent's pause (kept across dashboard restarts), or
            None for an agent suspended with Ctrl+Z by hand and never paused from the list.
    """
    if process_is_stopped(agent_process_id):
        if process_has_its_terminal(agent_process_id):
            # The shell didn't take the terminal back (a SIGSTOP it didn't notice): a SIGCONT is enough.
            send_signal_to_processes([agent_process_id], signal.SIGCONT)
        else:
            reason_not_to_type_fg = await find_reason_not_to_type_fg(session, agent_process_id, pause_record)
            if reason_not_to_type_fg:
                return ActionResult(False, reason_not_to_type_fg)
            await session.async_send_text(CONTROL_U + "fg\r", suppress_broadcast=True)
            if not await wait_until(lambda: not process_is_stopped(agent_process_id), SECONDS_TO_WAIT_FOR_FG):
                return ActionResult(False, "fg didn't bring the agent back; type jobs, then fg %N, in the tab")
    if pause_record is not None:
        continue_processes_stopped_at_pause(pause_record.stopped_processes)
    return ActionResult(True)
