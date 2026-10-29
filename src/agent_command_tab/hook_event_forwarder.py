"""The command that Claude Code's and Codex's hooks run: it reads the event from standard input,
keeps the few fields the app uses, adds the processes above it (whose nearest agent sent the
event), and sends one line to the app's socket.

The installer (hooks_installer) registers it as `<python> -I -S <this file> <agent tool name>`
(build_forwarder_command): it imports only the standard library, so Python starts without its
site packages (30 to 40 ms in all, measured on 2026-10-27), and it runs in the background (the hooks are registered as
async), so agents never wait for it. It never prints, since Claude Code can feed a hook's output into the conversation,
and it never fails: an event that can't be sent (the app isn't running) is dropped.
"""

import ctypes
import json
import os
import socket
import sys
import time

HOOK_EVENTS_SOCKET_PATH = os.path.expanduser("~/Library/Application Support/agent-command-tab/hook_events.sock")
SECONDS_TO_REACH_THE_APP = 0.2
ANCESTOR_LEVELS_SENT = 6  # the shell the hook runs in, the agent, and the agent's own launcher and shell above it
LONGEST_TEXT_KEPT = 300  # characters of a text field (a reason, an ID)
MOST_BACKGROUND_TASKS_KEPT = 50
PROC_PIDT_SHORTBSDINFO = 13  # proc_pidinfo flavor of struct proc_bsdshortinfo, as in agent_process_tree


class ProcessShortBsdInfo(ctypes.Structure):
    """struct proc_bsdshortinfo from <sys/proc_info.h>."""

    _fields_ = [
        ("pbsi_pid", ctypes.c_uint32), ("pbsi_ppid", ctypes.c_uint32), ("pbsi_pgid", ctypes.c_uint32),
        ("pbsi_status", ctypes.c_uint32), ("pbsi_comm", ctypes.c_char * 16), ("pbsi_flags", ctypes.c_uint32),
        ("pbsi_uid", ctypes.c_uint32), ("pbsi_gid", ctypes.c_uint32), ("pbsi_ruid", ctypes.c_uint32),
        ("pbsi_rgid", ctypes.c_uint32), ("pbsi_svuid", ctypes.c_uint32), ("pbsi_svgid", ctypes.c_uint32),
        ("pbsi_rfu", ctypes.c_uint32),
    ]


def list_ancestor_processes(process_id, levels):
    """Returns [process ID, executable name] of process_id and the processes above it, nearest
    first, up to levels of them (fewer once one can't be read)."""
    libproc = ctypes.CDLL("/usr/lib/libproc.dylib")
    ancestors = []
    while process_id > 1 and len(ancestors) < levels:
        info = ProcessShortBsdInfo()
        if libproc.proc_pidinfo(process_id, PROC_PIDT_SHORTBSDINFO, ctypes.c_uint64(0), ctypes.byref(info),
                                ctypes.sizeof(info)) != ctypes.sizeof(info):
            break
        ancestors.append([process_id, info.pbsi_comm.decode(errors="replace")])
        process_id = info.pbsi_ppid
    return ancestors


def shorten(value):
    """Returns a string field cut to LONGEST_TEXT_KEPT characters, or None for anything else."""
    return value[:LONGEST_TEXT_KEPT] if isinstance(value, str) else None


def find_background_task_launched(payload):
    """Returns the ID of the background task a PostToolUse event's tool call started in Claude
    Code (a Bash command run in the background returns a backgroundTaskId, a background agent
    isAsync and its agentId), or None."""
    tool_response = payload.get("tool_response")
    if not isinstance(tool_response, dict):
        return None
    if tool_response.get("backgroundTaskId"):
        return shorten(tool_response["backgroundTaskId"])
    if tool_response.get("isAsync") and tool_response.get("agentId"):
        return shorten(tool_response["agentId"])
    return None


def reduce_hook_payload(payload, agent_tool_name, ancestors, now):
    """Returns the event the app receives: the payload's fields it uses, and where it came from.

    Args:
        payload: The hook's input, as the agent tool sent it.
        agent_tool_name: The name the installer passed ("Claude Code", "Codex").
        ancestors: list_ancestor_processes's list, from the hook's own parent up.
        now: When the hook started.
    """
    background_tasks = payload.get("background_tasks")
    return {
        "agent_tool_name": agent_tool_name,
        "hook_event_name": shorten(payload.get("hook_event_name")),
        "reason": shorten(payload.get("reason")),
        "source": shorten(payload.get("source")),
        "agent_id": shorten(payload.get("agent_id")),
        "background_task_launched_id": find_background_task_launched(payload),
        # Claude Code lists the session's background tasks (shells and agents) when a turn or an agent ends.
        "background_tasks": [{"id": shorten(task.get("id")), "status": shorten(task.get("status"))}
                             for task in background_tasks[:MOST_BACKGROUND_TASKS_KEPT] if isinstance(task, dict)]
        if isinstance(background_tasks, list) else None,
        "ancestor_processes": ancestors,
        "sent_at": now,
    }


def main():
    try:
        now = time.time()
        payload = json.loads(sys.stdin.read() or "{}")
        if not isinstance(payload, dict):
            return
        event = reduce_hook_payload(payload, sys.argv[1] if len(sys.argv) > 1 else "",
                                    list_ancestor_processes(os.getppid(), ANCESTOR_LEVELS_SENT), now)
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(SECONDS_TO_REACH_THE_APP)
            connection.connect(HOOK_EVENTS_SOCKET_PATH)
            connection.sendall(json.dumps(event).encode() + b"\n")
    except BaseException:  # never fail, never print: the agent goes on either way
        pass


if __name__ == "__main__":
    main()
