"""Facts about an agent's processes, read from macOS's libproc without starting a subprocess.

- The agent's current folder (PLAN.md section 5: iTerm2's `path` variable can be empty or stale).
- Whether a process still exists.
- The agent's descendant processes, to tell an agent that is waiting on a command it started
  (a pipeline, a test run) from one that is thinking (PLAN.md section 6, "stuck").
"""

import collections
import ctypes
import os

LIBPROC = ctypes.CDLL("/usr/lib/libproc.dylib")
PROC_PIDTBSDINFO = 3  # proc_pidinfo flavor that returns struct proc_bsdinfo
PROC_PIDVNODEPATHINFO = 9  # proc_pidinfo flavor that returns struct proc_vnodepathinfo
VNODE_INFO_SIZE = 152  # sizeof(struct vnode_info); the current folder's path follows it
MAXIMUM_PATH_LENGTH = 1024
MAXIMUM_CHILD_PROCESSES = 1024
# Shells and wrappers that agents run commands in: the command's name is the first process
# below them (python3 rather than the zsh that Claude Code runs it in).
SHELL_AND_WRAPPER_PROCESS_NAMES = frozenset({"bash", "zsh", "sh", "dash", "fish", "env", "login", "sandbox-exec", "timeout"})
# A command counts as started in this turn even if it started this long before the dashboard
# noticed the turn begin.
SECONDS_OF_SLACK_AT_TURN_START = 2

ProcessInfo = collections.namedtuple("ProcessInfo", ["process_id", "parent_process_id", "name", "started_at"])
ProcessInfo.__doc__ = "One process: its name and when it started (seconds since the epoch)."


class ProcessBsdInfo(ctypes.Structure):
    """struct proc_bsdinfo from <sys/proc_info.h>."""

    _fields_ = [
        ("pbi_flags", ctypes.c_uint32), ("pbi_status", ctypes.c_uint32), ("pbi_xstatus", ctypes.c_uint32),
        ("pbi_pid", ctypes.c_uint32), ("pbi_ppid", ctypes.c_uint32), ("pbi_uid", ctypes.c_uint32),
        ("pbi_gid", ctypes.c_uint32), ("pbi_ruid", ctypes.c_uint32), ("pbi_rgid", ctypes.c_uint32),
        ("pbi_svuid", ctypes.c_uint32), ("pbi_svgid", ctypes.c_uint32), ("rfu_1", ctypes.c_uint32),
        ("pbi_comm", ctypes.c_char * 16), ("pbi_name", ctypes.c_char * 32), ("pbi_nfiles", ctypes.c_uint32),
        ("pbi_pgid", ctypes.c_uint32), ("pbi_pjobc", ctypes.c_uint32), ("e_tdev", ctypes.c_uint32),
        ("e_tpgid", ctypes.c_uint32), ("pbi_nice", ctypes.c_int32), ("pbi_start_tvsec", ctypes.c_uint64),
        ("pbi_start_tvusec", ctypes.c_uint64),
    ]


def read_process_current_folder(process_id):
    """Returns a process's current folder, or "" if unknown."""
    if not process_id:
        return ""
    buffer_size = 2 * (VNODE_INFO_SIZE + MAXIMUM_PATH_LENGTH)  # current folder, then root folder
    buffer = ctypes.create_string_buffer(buffer_size)
    if LIBPROC.proc_pidinfo(process_id, PROC_PIDVNODEPATHINFO, ctypes.c_uint64(0), buffer, buffer_size) <= 0:
        return ""
    return buffer.raw[VNODE_INFO_SIZE:VNODE_INFO_SIZE + MAXIMUM_PATH_LENGTH].split(b"\0", 1)[0].decode(errors="replace")


def process_is_alive(process_id):
    """Returns whether a process with this ID exists (one owned by another user counts as alive)."""
    try:
        os.kill(process_id, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def read_process_info(process_id):
    """Returns the ProcessInfo of a process, or None if it is gone."""
    bsd_info = ProcessBsdInfo()
    size = LIBPROC.proc_pidinfo(process_id, PROC_PIDTBSDINFO, ctypes.c_uint64(0), ctypes.byref(bsd_info),
                                ctypes.sizeof(bsd_info))
    if size != ctypes.sizeof(bsd_info):
        return None
    name = (bsd_info.pbi_name or bsd_info.pbi_comm).decode(errors="replace")
    return ProcessInfo(process_id, bsd_info.pbi_ppid, name, bsd_info.pbi_start_tvsec + bsd_info.pbi_start_tvusec / 1e6)


def list_child_process_ids(process_id):
    buffer = (ctypes.c_int * MAXIMUM_CHILD_PROCESSES)()
    count = LIBPROC.proc_listchildpids(process_id, buffer, ctypes.sizeof(buffer))
    return [buffer[index] for index in range(max(count, 0)) if buffer[index] > 0]


def list_descendant_processes(process_id, maximum_depth=6):
    """Returns the ProcessInfo of every process below process_id (children, their children, ...)."""
    descendants = []
    parent_process_ids = [process_id]
    for _ in range(maximum_depth):
        next_parent_process_ids = []
        for parent_process_id in parent_process_ids:
            for child_process_id in list_child_process_ids(parent_process_id):
                child = read_process_info(child_process_id)
                if child is not None:
                    descendants.append(child)
                    next_parent_process_ids.append(child_process_id)
        if not next_parent_process_ids:
            break
        parent_process_ids = next_parent_process_ids
    return descendants


def choose_command_the_agent_is_waiting_on(descendant_processes, turn_started_at, helper_process_names,
                                           helper_process_ids=frozenset()):
    """Returns (command name, started at) for the command an agent started in this turn and is
    still running, or None when it runs none (it is thinking or writing).

    Not commands, and nor is anything below them: the tool's own helpers (Claude Code's
    caffeinate, Codex's code mode host), processes that were already running while the session
    was idle (MCP servers, from the second turn on), and processes older than the turn. A shell
    with nothing running in it doesn't count either (Codex keeps a terminal open between commands).

    Args:
        descendant_processes: ProcessInfo of every process below the agent process.
        turn_started_at: When the agent's current turn began.
        helper_process_names: Names of the agent tool's own helper processes.
        helper_process_ids: IDs of processes seen running while the session was idle.
    """
    excluded_process_ids = set(helper_process_ids) | {process.process_id for process in descendant_processes
                                                      if process.name in helper_process_names}
    found_more = True
    while found_more:  # everything below an excluded process is excluded too
        below_excluded = {process.process_id for process in descendant_processes
                          if process.parent_process_id in excluded_process_ids} - excluded_process_ids
        excluded_process_ids |= below_excluded
        found_more = bool(below_excluded)
    parent_process_ids = {process.parent_process_id for process in descendant_processes}
    started_in_this_turn = sorted(
        (process for process in descendant_processes
         if process.process_id not in excluded_process_ids
         and process.started_at >= turn_started_at - SECONDS_OF_SLACK_AT_TURN_START
         and not (process.name in SHELL_AND_WRAPPER_PROCESS_NAMES and process.process_id not in parent_process_ids)),
        key=lambda process: process.started_at)
    if not started_in_this_turn:
        return None
    named_process = next((process for process in started_in_this_turn if process.name not in SHELL_AND_WRAPPER_PROCESS_NAMES),
                         started_in_this_turn[0])
    return named_process.name, started_in_this_turn[0].started_at
