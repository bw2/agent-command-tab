"""Pausing and resuming an agent session: the agent and every process below it.

Pause before going offline, resume once back online. Checked with real Claude Code and Codex
sessions: paused for 2 minutes mid-answer and mid-command, both carried on.

A pause sends STOP signals and a resume CONT signals; nothing is typed into the tab. The pause
stops the shell that runs the agent as a job first (and before
it the shell that runs that one, when the agent was started from a second shell typed in the
tab), then the agent, then the rest of its job (Codex's `node .../codex`, which runs the codex
program) and every process below it, which may run in process groups of their own (Bash
commands, MCP servers); not the dashboard itself, when it was started from the agent's
commands. Stopped first, a shell never sees its job stop, so it neither takes the terminal back
nor shows its prompt: the agent keeps the terminal, and its screen stays as it was. A resume
continues them the other way round, the shells last, which then find their jobs running as
before. Checked with Claude Code 2.1.319 and Codex 0.165.0 under bash in iTerm2, at their prompts and
in the middle of an answer: both carried on, and the shell's `jobs` listed nothing afterwards.

A Codex session that runs through Codex's shared background server (`codex app-server`, which
Codex 0.165 and later start, a child of launchd rather than of the tab) isn't paused: the server,
not the codex process in the tab, runs its turns and commands, and stopping the server would
pause every Codex session. Its conversation file's header tells (source "vscode", originator
"codex-tui").

A STOP of the agent alone isn't enough: its shell notices, takes the terminal back, and after a
plain CONT the agent runs as a background job and stops or fails at its next key read (a `cat`
under bash and zsh failed with "Interrupted system call"). Up to version 0.3.0 a pause
therefore pressed Ctrl+Z and a resume typed `fg` at the shell's prompt, which needed the prompt
read after the pause (seconds in a tab that isn't in front) and refused whenever something
might have been typed at it.

An agent running in a tmux pane isn't paused: tmux continues a stopped pane's own process (its
shell) at once, and zsh, finding the agent stopped, then takes the terminal back (checked with
tmux 3.6a; macOS's bash 3.2 didn't notice, but that may come down to timing). One
suspended there with Ctrl+Z is, as anywhere: only the processes below it are stopped.

Keys typed into a paused tab wait in the terminal and reach the agent once it is resumed. A
paused tab that closes (or a terminal app that quits) leaves what the pause stopped stopped, since
a stopped process can't act on the hangup: pause_has_ended notices it, and the dashboard then
continues them, so they end as they would have. An agent suspended with Ctrl+Z (by hand, or by
a pause of version 0.3.0 or earlier) has its shell in front already: a pause stops only the rest of its job
and the processes below it, and a resume leaves `fg` to be typed in the tab.
"""

import collections
import os
import signal

from agent_command_tab.agent_process_tree import (find_the_agents_job_leader_id, list_descendant_processes_and_other_users_process_ids,
                                                  list_the_process_and_its_ancestor_ids, list_the_shells_the_job_runs_under,
                                                  process_has_its_terminal, process_is_stopped, process_runs_in_a_tmux_pane,
                                                  process_terminal_has_hung_up, read_name_of_any_process, read_process_info)
from agent_command_tab.dashboard_core_api import ActionResult

PauseRecord = collections.namedtuple("PauseRecord", ["agent_process", "agent_tool_name", "shell_processes", "stopped_processes"])
PauseRecord.__doc__ = ("What a pause left behind (kept across restarts by pause_records_store): the agent's (process "
                       "ID, start time) and tool name (\"Codex\"), to list it again after a restart; the (process ID, "
                       "start time) of each shell it stopped, outermost first, none when the agent runs under no job "
                       "control shell or had been suspended with Ctrl+Z (by hand, or by a pause of version 0.3.0 or earlier); and "
                       "the (process ID, start time) of each other process it stopped: the rest of the agent's job and "
                       "the processes below the agent.")


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
    """Returns whether the paused agent runs again (fg typed by hand after a Ctrl+Z, or a CONT
    signal from elsewhere), is gone, or holds a terminal that has hung up (its tab closed, or the
    terminal app quit), judged by its own process, also after the dashboard restarted."""
    agent_process_id, started_at = pause_record.agent_process
    agent = read_process_info(agent_process_id)
    return (agent is None or agent.started_at != started_at or not process_is_stopped(agent_process_id)
            or process_terminal_has_hung_up(agent_process_id))


def continue_what_the_pause_stopped(pause_record):
    """Sends SIGCONT to what the pause stopped that is still stopped: the processes below the
    agent and the rest of its job, then the agent, then its shells, innermost first, so that each
    finds its job running; each unless its process ID now belongs to another process (a different
    start time)."""
    process_ids_to_continue = []
    for process_id, started_at in (pause_record.stopped_processes + [pause_record.agent_process]
                                   + pause_record.shell_processes[::-1]):
        process = read_process_info(process_id)
        if process is not None and process.started_at == started_at and process_is_stopped(process_id):
            process_ids_to_continue.append(process_id)
    send_signal_to_processes(process_ids_to_continue, signal.SIGCONT)


def list_processes_of_the_agents_job_to_stop(agent_process_id, job_leader_id, detached_processes=()):
    """Returns ({process ID: ProcessInfo} of the processes a pause stops besides the agent and its
    shell, the IDs of those that belong to another user, which it can't stop: a command run with
    sudo). They are the job leader, when that isn't the agent, every process below it, and the
    detached processes the agent's conversation started (launchd adopted them when their shell
    ended, and the list shows them under the agent) with the processes below them. Leaves out the dashboard's
    own process, those it runs in and those it started, so
    a dashboard started from the agent's own commands keeps running.

    Args:
        agent_process_id: The agent's process.
        job_leader_id: The leader of the agent's job (find_the_agents_job_leader_id's).
        detached_processes: The ProcessInfo of each detached process its conversation started
            (agent_process_tree.choose_detached_processes_of_the_conversation's).
    """
    dashboard_process_ids = set(list_the_process_and_its_ancestor_ids(os.getpid())) | {
        process.process_id
        for process in list_descendant_processes_and_other_users_process_ids(os.getpid(), maximum_depth=None)[0]}
    processes, other_users_process_ids = list_descendant_processes_and_other_users_process_ids(job_leader_id, maximum_depth=None)
    job_leader = read_process_info(job_leader_id) if job_leader_id != agent_process_id else None
    if job_leader is not None:
        processes = [job_leader] + processes
    for detached_process in detached_processes:
        processes_below_the_detached_process, other_users_process_ids_below_the_detached_process = \
            list_descendant_processes_and_other_users_process_ids(detached_process.process_id, maximum_depth=None)
        processes = processes + [detached_process] + processes_below_the_detached_process
        other_users_process_ids = other_users_process_ids + other_users_process_ids_below_the_detached_process
    return ({process.process_id: process for process in processes
             if process.process_id not in dashboard_process_ids and process.process_id != agent_process_id},
            other_users_process_ids)


def pause_agent_session(agent_process_id, agent_tool_name, detached_processes=(), turns_run_outside_the_tab=False):
    """Stops the agent's shells, the agent, then the rest of its job and every process below it,
    the detached processes its conversation started included (module docstring), reading the
    processes below it again until a read finds none it hasn't stopped (one not yet stopped may
    have started another). An agent suspended with Ctrl+Z already only gets the rest stopped; its
    record then holds no shell. An agent running in a tmux pane is left running, and one whose
    turns run outside the tab is left as it is, also after a Ctrl+Z.

    Args:
        agent_process_id: The agent's process.
        agent_tool_name: The agent's tool, as its row names it.
        detached_processes: The ProcessInfo of each detached process its conversation started.
        turns_run_outside_the_tab: Whether its turns and commands run in a process outside the
            tab (Codex's shared background server), which stopping the tab's processes leaves running.

    Returns:
        (ActionResult, PauseRecord), or (a failed ActionResult, None) when the agent is gone, runs
        in a tmux pane or runs its turns outside the tab. The ActionResult also fails, saying
        which, when processes below the agent run as another user (with sudo) and keep running;
        the record then lists the others.
    """
    agent = read_process_info(agent_process_id)
    job_leader_id = find_the_agents_job_leader_id(agent_process_id)
    if agent is None or job_leader_id is None:
        return ActionResult(False, "the agent's process has ended"), None
    # Its shell took the terminal back at the Ctrl+Z, and a STOP now would leave that unchanged.
    was_suspended_with_control_z = process_is_stopped(agent_process_id) and not process_has_its_terminal(agent_process_id)
    if not was_suspended_with_control_z and process_runs_in_a_tmux_pane(agent_process_id):
        return ActionResult(False, "an agent in tmux can't be paused: tmux restarts a stopped pane's shell at once, "
                                   "which would then take the terminal from the agent"), None
    if turns_run_outside_the_tab:  # also after a Ctrl+Z, which stops only the tab's client of the server
        return ActionResult(False, f"{agent_tool_name} can't be paused: its turns and commands run in its shared background "
                                   "server, outside the tab, and stopping that server would pause every session of it"), None
    shells = [] if was_suspended_with_control_z else list_the_shells_the_job_runs_under(job_leader_id)
    # Not those gone meanwhile, or run as another user (sudo -s).
    stopped_shell_ids = set(send_signal_to_processes([shell.process_id for shell in shells], signal.SIGSTOP))
    stopped_shells = [shell for shell in shells if shell.process_id in stopped_shell_ids]
    if not was_suspended_with_control_z and not send_signal_to_processes([agent_process_id], signal.SIGSTOP):
        send_signal_to_processes([shell.process_id for shell in stopped_shells[::-1]], signal.SIGCONT)
        return ActionResult(False, "the agent's process has ended"), None
    processes_to_stop, other_users_process_ids, stopped_process_ids = {}, set(), set()
    while True:  # read again after each round of STOP signals: a process not yet stopped may have started another
        processes_found, other_users_process_ids_found = list_processes_of_the_agents_job_to_stop(
            agent_process_id, job_leader_id, detached_processes)
        other_users_process_ids.update(other_users_process_ids_found)
        processes_not_yet_signaled = {process_id: process for process_id, process in processes_found.items()
                                      if process_id not in processes_to_stop}
        if not processes_not_yet_signaled:
            break
        processes_to_stop.update(processes_not_yet_signaled)
        stopped_process_ids.update(send_signal_to_processes(sorted(processes_not_yet_signaled), signal.SIGSTOP))
    names_of_processes_left_running = sorted(  # not those that ended meanwhile
        [process.name for process_id, process in processes_to_stop.items()
         if process_id not in stopped_process_ids and read_process_info(process_id) is not None]
        + [name for name in map(read_name_of_any_process, other_users_process_ids) if name])
    pause_record = PauseRecord((agent_process_id, agent.started_at), agent_tool_name,
                               [(shell.process_id, shell.started_at) for shell in stopped_shells],
                               sorted((process_id, process.started_at) for process_id, process in processes_to_stop.items()
                                      if process_id in stopped_process_ids))
    if names_of_processes_left_running:
        return ActionResult(False, "paused, but these processes below the agent run as another user (with sudo), so they "
                                   "keep running: " + ", ".join(names_of_processes_left_running)), pause_record
    return ActionResult(True), pause_record


def resume_agent_session(agent_process_id, pause_record):
    """Continues what the agent's pause stopped (continue_what_the_pause_stopped), or, with no
    record, sends a CONT signal to an agent that some other STOP signal stopped. An agent suspended
    with Ctrl+Z is left alone, since its shell has taken the terminal back: after a CONT it would
    run in the background and stop again at its next key read, and only `fg` typed in the tab
    brings it back to the front. One whose pause stopped its shells wasn't, also when a program in
    a process group of its own below it was in front (so the agent doesn't have its terminal).

    Args:
        agent_process_id: The agent's process.
        pause_record: The PauseRecord of the agent's pause (kept across dashboard restarts), or
            None for an agent stopped from elsewhere and never paused from the list.
    """
    if (not (pause_record and pause_record.shell_processes)
            and process_is_stopped(agent_process_id) and not process_has_its_terminal(agent_process_id)):
        return ActionResult(False, "the agent was suspended with Ctrl+Z, so its shell has taken the terminal back, and a "
                                   "CONT signal would leave the agent in the background; type fg in the tab")
    if pause_record is None:
        send_signal_to_processes([agent_process_id], signal.SIGCONT)
    else:
        continue_what_the_pause_stopped(pause_record)
    return ActionResult(True)
