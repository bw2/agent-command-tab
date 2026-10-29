"""Facts about an agent's processes, read from macOS's libproc and sysctl without starting a subprocess.

- The agent's current folder (iTerm2's `path` variable can be empty or stale).
- Whether a process still exists, and whether it is stopped (a paused agent session).
- The agent's descendant processes, to tell an agent that is waiting on a command it started
  (a pipeline, a test run) from one that is thinking (and turns "stuck" when that lasts), and the
  name that command was started as.
- The memory and CPU time the agent and all its descendants use (the Memory and CPU columns,
  user request, 2026-10-19).
"""

import collections
import ctypes
import os
import re

LIBPROC = ctypes.CDLL("/usr/lib/libproc.dylib")
LIBC = ctypes.CDLL("/usr/lib/libc.dylib")
CTL_KERN = 1
KERN_PROCARGS2 = 49  # sysctl that returns a process's argument count, executable path and arguments (what ps reads)
PROC_PIDTBSDINFO = 3  # proc_pidinfo flavor that returns struct proc_bsdinfo (refused for another user's process)
PROC_PIDT_SHORTBSDINFO = 13  # proc_pidinfo flavor that returns struct proc_bsdshortinfo, also for another user's process
STOPPED_PROCESS_STATUS = 4  # SSTOP in <sys/proc.h>, proc_bsdinfo's pbi_status of a stopped process
PROC_PIDVNODEPATHINFO = 9  # proc_pidinfo flavor that returns struct proc_vnodepathinfo
VNODE_INFO_SIZE = 152  # sizeof(struct vnode_info); the current folder's path follows it
MAXIMUM_PATH_LENGTH = 1024
MAXIMUM_CHILD_PROCESSES = 1024
# Shells and wrappers that agents run commands in: the command's name is the first process
# below them (python3 rather than the zsh that Claude Code runs it in).
SHELL_AND_WRAPPER_PROCESS_NAMES = frozenset({"bash", "zsh", "sh", "dash", "fish", "env", "login", "sandbox-exec", "timeout"})
# The names agent programs are started as. One started inside a tab's agent (a `claude -p` or
# `codex exec` run by its shell tool) fires the same user-level hooks, so it sets the tab's status too.
NAMES_OF_AGENT_PROGRAMS = frozenset({"claude", "codex"})
# A command counts as started in this turn even if it started this long before the dashboard
# noticed the turn begin.
SECONDS_OF_SLACK_AT_TURN_START = 2
# A command counts only once it has run this long, so hooks (iTerm2's cc-status runs on every
# tool call) and status line commands, which end within a second, never show as "running".
SECONDS_A_COMMAND_RUNS_BEFORE_IT_COUNTS = 2
# Claude Code's shell tool runs each command as `bash -c "source <its shell snapshot> ... && eval
# '<the command>' < /dev/null && pwd -P >| <a file>"`, a ' in the command written as '"'"', and
# without the `< /dev/null` for a command with a heredoc (code review, 2026-10-28).
CLAUDE_CODE_SHELL_TOOL_COMMAND = re.compile(r"""&& eval '((?:[^']|'"'"')*)'(?: < /dev/null)? && pwd -P >\| \S+\Z""", re.DOTALL)
# A Claude Code session's scratchpad folder, /private/tmp/claude-<user ID>/<project>/<session ID>/scratchpad,
# which a process row calls just "scratchpad" (user request, 2026-10-28), so the end of the command shows.
CLAUDE_CODE_SCRATCHPAD_PATH = re.compile(r"(?:/private)?/tmp/claude-\d+/[^/\s'\"]+/[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}/scratchpad")
LAUNCHD_PROCESS_ID = 1  # adopts a process whose parent ended

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


class ProcessShortBsdInfo(ctypes.Structure):
    """struct proc_bsdshortinfo from <sys/proc_info.h>."""

    _fields_ = [
        ("pbsi_pid", ctypes.c_uint32), ("pbsi_ppid", ctypes.c_uint32), ("pbsi_pgid", ctypes.c_uint32),
        ("pbsi_status", ctypes.c_uint32), ("pbsi_comm", ctypes.c_char * 16), ("pbsi_flags", ctypes.c_uint32),
        ("pbsi_uid", ctypes.c_uint32), ("pbsi_gid", ctypes.c_uint32), ("pbsi_ruid", ctypes.c_uint32),
        ("pbsi_rgid", ctypes.c_uint32), ("pbsi_svuid", ctypes.c_uint32), ("pbsi_svgid", ctypes.c_uint32),
        ("pbsi_rfu", ctypes.c_uint32),
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


def read_process_bsd_info(process_id):
    """Returns a process's ProcessBsdInfo, or None if it is gone."""
    bsd_info = ProcessBsdInfo()
    size = LIBPROC.proc_pidinfo(process_id, PROC_PIDTBSDINFO, ctypes.c_uint64(0), ctypes.byref(bsd_info),
                                ctypes.sizeof(bsd_info))
    return bsd_info if size == ctypes.sizeof(bsd_info) else None


def read_process_info(process_id):
    """Returns the ProcessInfo of a process, or None if it is gone or belongs to another user
    (a command run with sudo; read_name_of_any_process still reads its name)."""
    bsd_info = read_process_bsd_info(process_id)
    if bsd_info is None:
        return None
    name = (bsd_info.pbi_name or bsd_info.pbi_comm).decode(errors="replace")
    return ProcessInfo(process_id, bsd_info.pbi_ppid, name, bsd_info.pbi_start_tvsec + bsd_info.pbi_start_tvusec / 1e6)


def read_process_short_bsd_info(process_id):
    """Returns a process's ProcessShortBsdInfo, also for a process of another user, or None if it is gone."""
    short_bsd_info = ProcessShortBsdInfo()
    size = LIBPROC.proc_pidinfo(process_id, PROC_PIDT_SHORTBSDINFO, ctypes.c_uint64(0), ctypes.byref(short_bsd_info),
                                ctypes.sizeof(short_bsd_info))
    return short_bsd_info if size == ctypes.sizeof(short_bsd_info) else None


def read_name_of_any_process(process_id):
    """Returns a process's executable name (at most 16 characters), also for a process of
    another user, or "" if it is gone."""
    short_bsd_info = read_process_short_bsd_info(process_id)
    return short_bsd_info.pbsi_comm.decode(errors="replace") if short_bsd_info is not None else ""


def list_the_process_and_its_ancestor_ids(process_id):
    """Returns process_id and the IDs of its parent, the parent's parent, and so on up to launchd."""
    process_ids = []
    while process_id > 0 and process_id not in process_ids:
        process_ids.append(process_id)
        short_bsd_info = read_process_short_bsd_info(process_id)
        if short_bsd_info is None:
            break
        process_id = short_bsd_info.pbsi_ppid
    return process_ids


def process_is_stopped(process_id):
    """Returns whether a process exists and is stopped (by Ctrl+Z, SIGTSTP or SIGSTOP) until it gets a SIGCONT."""
    bsd_info = read_process_bsd_info(process_id)
    return bsd_info is not None and bsd_info.pbi_status == STOPPED_PROCESS_STATUS


def find_the_shell_running_the_agent(agent_process_id):
    """Returns the ID of the shell that runs the agent as a job: the parent of the agent's job
    leader (the process its group is named after), or None if the agent is gone."""
    agent = read_process_bsd_info(agent_process_id)
    if agent is None:
        return None
    job_leader = read_process_bsd_info(agent.pbi_pgid)
    return job_leader.pbi_ppid if job_leader is not None else agent.pbi_ppid


def list_other_stopped_jobs_of_the_agents_shell(agent_process_id):
    """Returns the IDs of the stopped processes that the shell running the agent
    (find_the_shell_running_the_agent) started besides the agent's own job: other suspended
    jobs, one of which a bare `fg` could bring back instead."""
    agent = read_process_bsd_info(agent_process_id)
    shell_process_id = find_the_shell_running_the_agent(agent_process_id)
    if agent is None or shell_process_id is None:
        return []
    other_stopped_job_process_ids = []
    for child_process_id in list_child_process_ids(shell_process_id):
        child = read_process_bsd_info(child_process_id)
        if child is not None and child.pbi_pgid != agent.pbi_pgid and child.pbi_status == STOPPED_PROCESS_STATUS:
            other_stopped_job_process_ids.append(child_process_id)
    return other_stopped_job_process_ids


def process_has_its_terminal(process_id):
    """Returns whether the process's group is its terminal's foreground group, the one that gets
    the keys, as the kernel has it right now (iTerm2's jobPid variable follows only after a while)."""
    bsd_info = read_process_bsd_info(process_id)
    return bsd_info is not None and bsd_info.e_tpgid != 0 and bsd_info.pbi_pgid == bsd_info.e_tpgid


def read_process_arguments_and_environment(process_id):
    """Returns (the arguments the process was started with, the first being its program name as
    typed, {name: value} of the environment it was started with), or ([], {}) if unknown."""
    sysctl_name = (ctypes.c_int * 3)(CTL_KERN, KERN_PROCARGS2, process_id)
    size = ctypes.c_size_t(0)
    if LIBC.sysctl(sysctl_name, 3, None, ctypes.byref(size), None, ctypes.c_size_t(0)) != 0:  # asks for the size
        return [], {}
    buffer = ctypes.create_string_buffer(size.value)
    if LIBC.sysctl(sysctl_name, 3, buffer, ctypes.byref(size), None, ctypes.c_size_t(0)) != 0:
        return [], {}
    # The argument count (an int), the executable's path, NUL padding, the arguments, the
    # environment, then an empty string before the kernel's own "name=value" strings.
    argument_count = ctypes.c_int.from_buffer_copy(buffer.raw[:ctypes.sizeof(ctypes.c_int)]).value
    _, _, arguments_and_environment = buffer.raw[ctypes.sizeof(ctypes.c_int):size.value].partition(b"\0")
    words = [word.decode(errors="replace") for word in arguments_and_environment.lstrip(b"\0").split(b"\0")]
    environment_words = words[argument_count:]
    if "" in environment_words:
        environment_words = environment_words[:environment_words.index("")]
    return words[:argument_count], dict(word.partition("=")[::2] for word in environment_words)


def read_name_the_process_was_started_as(process_id):
    """Returns the base name of the process's first argument, the program name as typed ("claude"
    rather than its executable's name, the version number "2.1.303"), or "" if unknown."""
    arguments = read_process_arguments_and_environment(process_id)[0]
    return os.path.basename(arguments[0]) if arguments else ""


def list_child_process_ids(process_id):
    buffer = (ctypes.c_int * MAXIMUM_CHILD_PROCESSES)()
    count = LIBPROC.proc_listchildpids(process_id, buffer, ctypes.sizeof(buffer))
    return [buffer[index] for index in range(max(count, 0)) if buffer[index] > 0]


def list_descendant_processes(process_id, maximum_depth=6):
    """Returns the ProcessInfo of every process below process_id (children, their children, ...),
    down to maximum_depth levels, or all of them for None. Each process is listed once, even if
    a process ID is reused while the tree is read. Another user's processes are left out
    (list_descendant_processes_and_other_users_process_ids)."""
    return list_descendant_processes_and_other_users_process_ids(process_id, maximum_depth)[0]


def list_descendant_processes_and_other_users_process_ids(process_id, maximum_depth=6):
    """Returns (list_descendant_processes's list, the IDs of the processes below process_id that
    belong to another user, such as a command run with sudo, which can't be read and whose own
    children aren't listed)."""
    descendants = []
    other_users_process_ids = []
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
                elif read_name_of_any_process(child_process_id):  # not gone meanwhile
                    other_users_process_ids.append(child_process_id)
        parent_process_ids = next_parent_process_ids
    return descendants, other_users_process_ids


def list_child_processes_of_the_agent(agent_process_id, descendant_processes, now):
    """Returns [(child, shown process)] for each process the agent started, oldest first (the sub
    rows of an open tab row; user request, 2026-10-27). Codex's own program below its node
    launcher is part of the agent, so the processes it started count instead. The shown process
    is the child itself, or for a shell or wrapper the first process started below it (the agent's
    shell tool runs `python3 ...` inside a `bash -c`), if one runs. As for the command the agent
    waits on, a child counts only once it has run SECONDS_A_COMMAND_RUNS_BEFORE_IT_COUNTS, so the
    hooks and status line commands that end within a second (iTerm2's cc-status) never show.

    Args:
        agent_process_id: The agent process (the tab's job).
        descendant_processes: ProcessInfo of every process below it (list_descendant_processes).
        now: The current time.
    """
    own_program_process_ids = {agent_process_id} | {
        process.process_id for process in descendant_processes
        if process.parent_process_id == agent_process_id and process.name in NAMES_OF_AGENT_PROGRAMS}
    process_ids_by_parent_process_id = collections.defaultdict(list)
    for process in descendant_processes:
        process_ids_by_parent_process_id[process.parent_process_id].append(process.process_id)
    process_by_id = {process.process_id: process for process in descendant_processes}

    def find_first_process_run_below(shell):
        below = []
        parent_process_ids = [shell.process_id]
        while parent_process_ids:  # every level, so a shell inside a shell is looked through too
            children = [process_by_id[process_id] for parent_process_id in parent_process_ids
                        for process_id in process_ids_by_parent_process_id[parent_process_id]]
            below.extend(child for child in children if child.name not in SHELL_AND_WRAPPER_PROCESS_NAMES)
            parent_process_ids = [child.process_id for child in children]
        return min(below, key=lambda process: process.started_at, default=None)
    children = sorted((process for process in descendant_processes if process.parent_process_id in own_program_process_ids
                       and process.process_id not in own_program_process_ids
                       and now - process.started_at >= SECONDS_A_COMMAND_RUNS_BEFORE_IT_COUNTS),
                      key=lambda process: process.started_at)
    return [(child, (find_first_process_run_below(child) if child.name in SHELL_AND_WRAPPER_PROCESS_NAMES else None) or child)
            for child in children]


def child_process_is_a_background_shell(child, shown_process, command_started_at, turn_is_under_way, turn_started_at):
    """Whether one of list_child_processes_of_the_agent's children is a command the agent's shell
    tool left running in the background: a shell or wrapper with a process running in it (not
    Codex's terminal kept open between commands, nor a helper such as an MCP server, which the
    agent starts itself), between turns, or during a turn when the command in it started before
    the turn did. A command started in the turn under way may be the one the agent waits on (the
    order the agent started them in doesn't tell), so it counts only once the turn ends (code
    review, 2026-10-27).

    Args:
        child, shown_process: One of list_child_processes_of_the_agent's pairs.
        command_started_at: When the command in the shell started: the shell's own start for a
            Claude Code shell (a wait loop from an earlier turn starts a new sleep in this one),
            else the shown process's (Codex runs a later turn's commands in a shell kept from an
            earlier turn) (code review, 2026-10-28).
        turn_is_under_way: Whether the agent is in a turn (working or asking).
        turn_started_at: When that turn began, or None while not known (then no command counts).
    """
    if child.name not in SHELL_AND_WRAPPER_PROCESS_NAMES or shown_process is child:
        return False
    if not turn_is_under_way:
        return True
    return turn_started_at is not None and command_started_at < turn_started_at - SECONDS_OF_SLACK_AT_TURN_START


def build_command_text(arguments, executable_name):
    """Returns how a sub row names a process: its program's base name and its arguments, on one
    line ("python3 -m unittest"), or its executable's name when its arguments can't be read. A
    Claude Code scratchpad folder in them is just "scratchpad" (CLAUDE_CODE_SCRATCHPAD_PATH)."""
    if not arguments:
        return executable_name
    return shorten_scratchpad_paths(" ".join([os.path.basename(arguments[0])] + arguments[1:]).replace("\n", " "))


def shorten_scratchpad_paths(text):
    return CLAUDE_CODE_SCRATCHPAD_PATH.sub("scratchpad", text)


def find_the_command_a_claude_code_shell_runs(arguments):
    """Returns the command Claude Code's shell tool gave a `bash -c` (CLAUDE_CODE_SHELL_TOOL_COMMAND),
    as the agent wrote it, or None for another process. A sub row names such a shell by it (user
    request, 2026-10-28): a background wait loop then reads as the loop, not as the `sleep 30` it
    runs at that moment."""
    if len(arguments) != 3 or arguments[1] != "-c":
        return None
    match = CLAUDE_CODE_SHELL_TOOL_COMMAND.search(arguments[2])
    return match.group(1).replace("'\"'\"'", "'") if match else None


def list_detached_processes(earliest_started_at):
    """Returns [(ProcessInfo, arguments, current folder or None)] for this user's processes that
    launchd adopted (their parent ended) and that started at or after earliest_started_at. A job an
    agent started with `nohup ... &` or `setsid`, whose shell then ended, is one of them: no longer
    below the agent, so list_descendant_processes misses it."""
    detached_processes = []
    for process_id in list_child_process_ids(LAUNCHD_PROCESS_ID):
        process = read_process_info(process_id)  # None for another user's process
        if process is not None and process.started_at >= earliest_started_at:
            detached_processes.append((process, read_process_arguments_and_environment(process_id)[0],
                                       read_process_current_folder(process_id)))
    return detached_processes


def choose_detached_processes_of_the_conversation(detached_processes, conversation_id, agent_started_at):
    """Returns [(ProcessInfo, arguments)] for the detached processes (list_detached_processes) that
    an agent's conversation started, oldest first (user request, 2026-10-28): started after the
    agent, with the conversation's ID in their arguments or current folder, as every path in a
    Claude Code session's scratchpad folder has it. A job run from elsewhere isn't found."""
    return sorted(((process, arguments) for process, arguments, current_folder in detached_processes
                   if conversation_id and process.started_at >= agent_started_at
                   and (conversation_id in " ".join(arguments) or conversation_id in (current_folder or ""))),
                  key=lambda process_and_arguments: process_and_arguments[0].started_at)


def read_child_processes(agent_process_id, turn_is_under_way, turn_started_at, reads_resource_usage, now,
                         detached_processes_of_the_conversation=()):
    """Returns [{"child": ProcessInfo, "command_text", "command_started_at", "is_background_shell",
    "is_detached", "resource_usage": (memory in bytes, CPU seconds) of the child and the processes
    below it, or None}] for each process the agent started (list_child_processes_of_the_agent),
    then for each detached one (choose_detached_processes_of_the_conversation), which runs in the background.
    A Claude Code shell is named by its command and dated by its own start; another child by the
    command shown and that command's start (a shell Codex keeps between turns is older).

    Args:
        agent_process_id: The agent process.
        turn_is_under_way, turn_started_at: See child_process_is_a_background_shell.
        reads_resource_usage: Whether to read each child's memory and CPU time (else None).
        now: The current time.
        detached_processes_of_the_conversation: choose_detached_processes_of_the_conversation's list.
    """
    child_processes = []
    for child, shown_process in list_child_processes_of_the_agent(agent_process_id, list_descendant_processes(agent_process_id), now):
        claude_code_shell_command = find_the_command_a_claude_code_shell_runs(read_process_arguments_and_environment(child.process_id)[0])
        command_started_at = child.started_at if claude_code_shell_command else shown_process.started_at
        child_processes.append({
            "child": child,
            "command_text": (shorten_scratchpad_paths(" ".join(claude_code_shell_command.split())) if claude_code_shell_command
                             else build_command_text(read_process_arguments_and_environment(shown_process.process_id)[0],
                                                     shown_process.name)),
            "command_started_at": command_started_at,
            "is_background_shell": child_process_is_a_background_shell(child, shown_process, command_started_at,
                                                                       turn_is_under_way, turn_started_at),
            "is_detached": False,
            "resource_usage": read_process_tree_resource_usage(child.process_id) if reads_resource_usage else None})
    for process, arguments in detached_processes_of_the_conversation:
        child_processes.append({
            "child": process, "command_text": build_command_text(arguments, process.name), "command_started_at": process.started_at,
            "is_background_shell": True, "is_detached": True,
            "resource_usage": read_process_tree_resource_usage(process.process_id) if reads_resource_usage else None})
    return sorted(child_processes, key=lambda child_process: child_process["child"].started_at)  # oldest first, detached ones too


def read_process_details(process_id):
    """Returns what a sub row's Info shows about its process (user request, 2026-10-29), or None
    if it is gone: {"process_id", "command_line": its arguments as ps shows them, "agent_command":
    the command in it as the agent wrote it, for a Claude Code shell, else None, "working_folder"
    ("" if unknown), "started_at", "subprocesses": [{"process_id", "command_text"
    (build_command_text's)}] of the processes below it, oldest first (user request, 2026-10-29),
    and "memory_bytes" and "cpu_seconds" (read_process_tree_resource_usage's), its and theirs
    together}. The command line keeps its line breaks, as a command with a heredoc has them."""
    process = read_process_info(process_id)
    resource_usage = read_process_tree_resource_usage(process_id)
    if process is None or resource_usage is None:
        return None
    arguments = read_process_arguments_and_environment(process_id)[0]
    processes_below = sorted(list_descendant_processes(process_id, maximum_depth=None), key=lambda process_below: process_below.started_at)
    return {
        "process_id": process_id,
        "command_line": " ".join(arguments) or process.name,
        "agent_command": find_the_command_a_claude_code_shell_runs(arguments),
        "working_folder": read_process_current_folder(process_id),
        "started_at": process.started_at,
        "subprocesses": [
            {"process_id": process_below.process_id,
             "command_text": build_command_text(read_process_arguments_and_environment(process_below.process_id)[0], process_below.name)}
            for process_below in processes_below],
        "memory_bytes": resource_usage[0],
        "cpu_seconds": resource_usage[1],
    }


def choose_helper_process_ids(descendant_processes):
    """Returns the IDs of the processes running below an agent between its turns (MCP servers, for
    example), which choose_command_the_agent_is_waiting_on never takes for a command, nor anything
    below them. Shells and wrappers (Codex keeps a terminal open between commands) and the agent's
    own program (Codex's below its node launcher) are left out, since a later turn's commands run
    below them."""
    return frozenset(process.process_id for process in descendant_processes
                     if process.name not in SHELL_AND_WRAPPER_PROCESS_NAMES and process.name not in NAMES_OF_AGENT_PROGRAMS)


def an_agent_program_runs_through_a_shell_below(descendant_processes, read_started_as_name):
    """Returns whether one of the processes (all below an agent) is an agent program
    (NAMES_OF_AGENT_PROGRAMS) started through a shell or wrapper below the agent, as the agent's
    shell tool starts a `claude -p` or `codex exec`. The agent's own parts aren't started through
    one (Codex's program below its node launcher).

    Args:
        descendant_processes: ProcessInfo of every process below the agent process.
        read_started_as_name: Returns the name a process was started as
            (read_name_the_process_was_started_as); asked only about processes below a shell.
    """
    process_by_id = {process.process_id: process for process in descendant_processes}

    def runs_below_a_shell(process):
        visited_process_ids = {process.process_id}  # a process ID reused while the tree was read can't loop
        parent = process_by_id.get(process.parent_process_id)
        while parent is not None and parent.process_id not in visited_process_ids:
            if parent.name in SHELL_AND_WRAPPER_PROCESS_NAMES:
                return True
            visited_process_ids.add(parent.process_id)
            parent = process_by_id.get(parent.parent_process_id)
        return False
    return any(runs_below_a_shell(process) and read_started_as_name(process.process_id) in NAMES_OF_AGENT_PROGRAMS
               for process in descendant_processes)


def choose_command_the_agent_is_waiting_on(descendant_processes, turn_started_at, helper_process_names, now,
                                           helper_process_ids=frozenset(), read_process_arguments=lambda process_id: []):
    """Returns (the command's process, started at) for the command an agent started in this turn
    and is still running, or None when it runs none (it is thinking or writing).

    Not commands, and nor is anything below them: the tool's own helpers (Claude Code's
    caffeinate, Codex's code mode host), processes that were already running while the session
    was idle (MCP servers, from the second turn on), processes older than the turn, and Claude
    Code shells older than the turn: each runs one command, so one from an earlier turn is a
    command left in the background, and what it starts now (a wait loop's next `sleep 30`) isn't
    this turn's (code review, 2026-10-28; Codex runs new commands in a terminal it keeps, so its
    shells aren't told apart this way). A shell with nothing running in it doesn't count either,
    nor does a command younger than SECONDS_A_COMMAND_RUNS_BEFORE_IT_COUNTS.

    Args:
        descendant_processes: ProcessInfo of every process below the agent process.
        turn_started_at: When the agent's current turn began.
        helper_process_names: Executable names of the agent tool's own helper processes.
        now: The current time.
        helper_process_ids: IDs of processes seen running while the session was idle.
        read_process_arguments: Returns a process's arguments (read_process_arguments_and_environment's
            first part); asked only about shells older than the turn.
    """
    excluded_process_ids = set(helper_process_ids) | {process.process_id for process in descendant_processes
                                                      if process.name in helper_process_names}
    excluded_process_ids |= {process.process_id for process in descendant_processes
                             if process.name in SHELL_AND_WRAPPER_PROCESS_NAMES
                             and process.started_at < turn_started_at - SECONDS_OF_SLACK_AT_TURN_START
                             and find_the_command_a_claude_code_shell_runs(read_process_arguments(process.process_id))}
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
