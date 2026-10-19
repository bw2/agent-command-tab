"""Facts about an agent's processes, read from macOS's libproc and sysctl without starting a subprocess.

- The agent's current folder (PLAN.md section 5: iTerm2's `path` variable can be empty or stale).
- Whether a process still exists.
- The agent's descendant processes, to tell an agent that is waiting on a command it started
  (a pipeline, a test run) from one that is thinking (PLAN.md section 6, "stuck"), and the
  name that command was started as.
- The memory and CPU time the agent and all its descendants use (the Memory and CPU columns,
  user request, 2026-10-19).
"""

import collections
import ctypes
import os

LIBPROC = ctypes.CDLL("/usr/lib/libproc.dylib")
LIBC = ctypes.CDLL("/usr/lib/libc.dylib")
CTL_KERN = 1
KERN_PROCARGS2 = 49  # sysctl that returns a process's argument count, executable path and arguments (what ps reads)
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
# A command counts only once it has run this long, so hooks (iTerm2's cc-status runs on every
# tool call) and status line commands, which end within a second, never show as "running".
SECONDS_A_COMMAND_RUNS_BEFORE_IT_COUNTS = 2

RUSAGE_INFO_V2 = 2  # proc_pid_rusage flavor that returns struct rusage_info_v2

ProcessInfo = collections.namedtuple("ProcessInfo", ["process_id", "parent_process_id", "name", "started_at"])
ProcessInfo.__doc__ = "One process: its executable's name and when it started (seconds since the epoch)."


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


class ResourceUsageInfoV2(ctypes.Structure):
    """struct rusage_info_v2 from <sys/resource.h>; the times are in mach absolute time units."""

    _fields_ = [("ri_uuid", ctypes.c_uint8 * 16)] + [(field_name, ctypes.c_uint64) for field_name in (
        "ri_user_time", "ri_system_time", "ri_pkg_idle_wkups", "ri_interrupt_wkups", "ri_pageins", "ri_wired_size",
        "ri_resident_size", "ri_phys_footprint", "ri_proc_start_abstime", "ri_proc_exit_abstime", "ri_child_user_time",
        "ri_child_system_time", "ri_child_pkg_idle_wkups", "ri_child_interrupt_wkups", "ri_child_pageins",
        "ri_child_elapsed_abstime", "ri_diskio_bytesread", "ri_diskio_byteswritten")]


class MachTimebaseInfo(ctypes.Structure):
    """struct mach_timebase_info: mach absolute time units times numer / denom are nanoseconds."""

    _fields_ = [("numer", ctypes.c_uint32), ("denom", ctypes.c_uint32)]


def read_seconds_per_mach_time_unit():
    """Returns how many seconds one mach absolute time unit is (1 ns on Intel, 1/24 µs on Apple silicon)."""
    timebase = MachTimebaseInfo()
    LIBC.mach_timebase_info(ctypes.byref(timebase))
    return timebase.numer / timebase.denom / 1e9


SECONDS_PER_MACH_TIME_UNIT = read_seconds_per_mach_time_unit()


def read_process_resource_usage(process_id):
    """Returns (memory in bytes, CPU time in seconds) for one process: its physical footprint,
    the number Activity Monitor shows as Memory, and the user plus system time used by it and
    by the children it has already waited for (so a finished command's CPU time stays counted
    in its parent's); or None if it is gone or can't be read."""
    usage = ResourceUsageInfoV2()
    if LIBPROC.proc_pid_rusage(process_id, RUSAGE_INFO_V2, ctypes.byref(usage)) != 0:
        return None
    cpu_time = usage.ri_user_time + usage.ri_system_time + usage.ri_child_user_time + usage.ri_child_system_time
    return usage.ri_phys_footprint, cpu_time * SECONDS_PER_MACH_TIME_UNIT


def read_process_tree_resource_usage(process_id):
    """Returns (memory in bytes, CPU time in seconds) summed over a process and all its
    descendants (an agent with the commands, servers and helpers it started), or None if the
    process is gone. A descendant that ends while it is being read is left out; once its
    parent has waited for it, its CPU time is counted in the parent's."""
    own_usage = read_process_resource_usage(process_id)
    if own_usage is None:
        return None
    # Every level: agents start launchers that start agents, which start shells and commands.
    usages = [own_usage] + [read_process_resource_usage(process.process_id)
                            for process in list_descendant_processes(process_id, maximum_depth=None)]
    usages = [usage for usage in usages if usage is not None]
    return sum(memory for memory, _ in usages), sum(cpu_seconds for _, cpu_seconds in usages)


def compute_cpu_percent(previous_reading, reading):
    """Returns the share of one CPU core used between two readings, in percent (200 means two
    cores' worth), or None for the first reading. A descendant that left the tree in between
    without its parent waiting for it takes its CPU time with it, so a drop counts as 0.

    Args:
        previous_reading: (CPU seconds, read at) from the last reading, or None.
        reading: (CPU seconds, read at) now.
    """
    if previous_reading is None or reading[1] <= previous_reading[1]:
        return None
    return max(reading[0] - previous_reading[0], 0) / (reading[1] - previous_reading[1]) * 100


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


def read_name_the_process_was_started_as(process_id):
    """Returns the base name of the process's first argument, the program name as typed ("claude"
    rather than its executable's name, the version number "2.1.303"), or "" if unknown."""
    sysctl_name = (ctypes.c_int * 3)(CTL_KERN, KERN_PROCARGS2, process_id)
    size = ctypes.c_size_t(0)
    if LIBC.sysctl(sysctl_name, 3, None, ctypes.byref(size), None, ctypes.c_size_t(0)) != 0:  # asks for the size
        return ""
    buffer = ctypes.create_string_buffer(size.value)
    if LIBC.sysctl(sysctl_name, 3, buffer, ctypes.byref(size), None, ctypes.c_size_t(0)) != 0:
        return ""
    # The argument count (an int), the executable's path, NUL padding, then the arguments.
    _, _, arguments = buffer.raw[ctypes.sizeof(ctypes.c_int):size.value].partition(b"\0")
    first_argument = arguments.lstrip(b"\0").split(b"\0", 1)[0]
    return os.path.basename(first_argument.decode(errors="replace"))


def list_child_process_ids(process_id):
    buffer = (ctypes.c_int * MAXIMUM_CHILD_PROCESSES)()
    count = LIBPROC.proc_listchildpids(process_id, buffer, ctypes.sizeof(buffer))
    return [buffer[index] for index in range(max(count, 0)) if buffer[index] > 0]


def list_descendant_processes(process_id, maximum_depth=6):
    """Returns the ProcessInfo of every process below process_id (children, their children, ...),
    down to maximum_depth levels, or all of them for None. Each process is listed once, even if
    a process ID is reused while the tree is read."""
    descendants = []
    listed_process_ids = {process_id}
    parent_process_ids = [process_id]
    depth = 0
    while parent_process_ids and (maximum_depth is None or depth < maximum_depth):
        depth += 1
        next_parent_process_ids = []
        for parent_process_id in parent_process_ids:
            for child_process_id in list_child_process_ids(parent_process_id):
                if child_process_id in listed_process_ids:
                    continue
                listed_process_ids.add(child_process_id)
                child = read_process_info(child_process_id)
                if child is not None:
                    descendants.append(child)
                    next_parent_process_ids.append(child_process_id)
        parent_process_ids = next_parent_process_ids
    return descendants


def choose_command_the_agent_is_waiting_on(descendant_processes, turn_started_at, helper_process_names, now,
                                           helper_process_ids=frozenset()):
    """Returns (the command's process, started at) for the command an agent started in this turn
    and is still running, or None when it runs none (it is thinking or writing).

    Not commands, and nor is anything below them: the tool's own helpers (Claude Code's
    caffeinate, Codex's code mode host), processes that were already running while the session
    was idle (MCP servers, from the second turn on), and processes older than the turn. A shell
    with nothing running in it doesn't count either (Codex keeps a terminal open between commands),
    nor does a command younger than SECONDS_A_COMMAND_RUNS_BEFORE_IT_COUNTS.

    Args:
        descendant_processes: ProcessInfo of every process below the agent process.
        turn_started_at: When the agent's current turn began.
        helper_process_names: Executable names of the agent tool's own helper processes.
        now: The current time.
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
    if not started_in_this_turn or now - started_in_this_turn[0].started_at < SECONDS_A_COMMAND_RUNS_BEFORE_IT_COUNTS:
        return None
    named_process = next((process for process in started_in_this_turn if process.name not in SHELL_AND_WRAPPER_PROCESS_NAMES),
                         started_in_this_turn[0])
    return named_process, started_in_this_turn[0].started_at
