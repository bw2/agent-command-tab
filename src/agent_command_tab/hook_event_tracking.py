"""What the app's hooks (hooks_installer, hook_event_forwarder) report about each agent process.
Pure functions.

An agent process whose hooks reported anything is known to report: when it is gone without a
normal session end, it crashed or was killed. Claude Code's events also list its
background shells and agents.
"""

from agent_command_tab.agent_process_tree import NAMES_OF_AGENT_PROGRAMS, SHELL_AND_WRAPPER_PROCESS_NAMES

# A session end that leaves the process running: Claude Code's /clear ends one conversation and
# starts the next in the same process, and so does /resume (its "resume", one
# of the reasons Claude Code 2.1.311's program lists). A SessionStart after any session end also
# means the process goes on.
SESSION_END_REASONS_OF_A_PROCESS_THAT_GOES_ON = frozenset({"clear", "resume"})
# The SessionStart sources that follow those ends: the process goes on in another conversation
# ("compact" keeps the same one, "startup" is a new process).
SESSION_START_SOURCES_OF_ANOTHER_CONVERSATION_IN_THE_SAME_PROCESS = frozenset({"clear", "resume"})
# The tools whose hook events carry their list of background tasks and their prompts (hooks_installer
# registers UserPromptSubmit for them). Codex's carry neither, so its rows count the background
# shells below it and go by the status it sets itself.
AGENT_TOOL_NAMES_WHOSE_HOOKS_REPORT_TURNS_AND_BACKGROUND_TASKS = frozenset({"Claude Code"})
# A background task Claude Code lists counts while it runs (its list also holds finished ones until it reports them).
STATUSES_OF_A_RUNNING_BACKGROUND_TASK = frozenset({"running", "pending", None})
# The hook events of a tool call starting and finishing (hooks_installer registers both for Claude Code's
# Bash, Agent and Task); the main agent's, after a Stop, show its turn going on (code review, 2026-10-28).
TOOL_EVENT_NAMES = frozenset({"PreToolUse", "PostToolUse"})


def find_agent_process_id_of_hook_event(ancestor_processes):
    """Returns the ID of the agent process whose hook sent an event: the nearest process above the
    forwarder that isn't a shell (the agent runs its hooks in one), or, for Codex's program below
    its node launcher, the launcher, which is the process its tab runs. None if there is none.

    An agent started inside a tab's agent (a `claude -p` its shell tool runs) has its own process
    ID, so its events never count as the tab agent's.

    Args:
        ancestor_processes: [[process ID, executable name]], from the forwarder's parent up.
    """
    for index, (process_id, name) in enumerate(ancestor_processes):
        if name in SHELL_AND_WRAPPER_PROCESS_NAMES:
            continue
        if name in NAMES_OF_AGENT_PROGRAMS and index + 1 < len(ancestor_processes) and ancestor_processes[index + 1][1] == "node":
            return ancestor_processes[index + 1][0]
        return process_id
    return None


def list_running_background_task_ids(background_tasks):
    """Returns the IDs of the tasks in a Claude Code list that still run."""
    return {task["id"] for task in background_tasks if task.get("id") and task.get("status") in STATUSES_OF_A_RUNNING_BACKGROUND_TASK}


def apply_hook_event(hook_state, event):
    """Returns an agent process's hook state after one more event from it.

    The state is {"agent_tool_name", "session_ended_at": when a normal session end arrived (and
    no session started since), or None, "background_task_ids": the IDs of its background shells
    and agents, or None while not known (no new start, no list since the app started; always for
    Codex, whose events list none), "last_prompt_at" and "last_turn_end_at": when the last prompt
    was submitted and the last turn ended, or None, "last_main_agent_tool_event_at": when the
    main agent last started or finished a tool call the hooks report (PreToolUse, PostToolUse;
    a subagent's carry its agent_id), or None}, and once its SessionStart for a new process arrived,
    "startup_reported_at": when it did (a key only then, so the states hook_states_store saved
    before there was one still load).

    Args:
        hook_state: The state before, or None for the process's first event.
        event: An event from hook_event_forwarder.reduce_hook_payload.
    """
    hook_state = dict(hook_state or {"agent_tool_name": event.get("agent_tool_name"), "session_ended_at": None,
                                     "background_task_ids": None, "last_prompt_at": None, "last_turn_end_at": None,
                                     "last_main_agent_tool_event_at": None})
    event_name = event.get("hook_event_name")
    if event_name == "SessionStart" and event.get("source") == "startup":
        hook_state["startup_reported_at"] = event.get("sent_at")
    if event_name == "UserPromptSubmit":
        hook_state["last_prompt_at"] = event.get("sent_at")
    if event_name == "Stop":
        hook_state["last_turn_end_at"] = event.get("sent_at")
    if event_name in TOOL_EVENT_NAMES and not event.get("agent_id"):
        hook_state["last_main_agent_tool_event_at"] = event.get("sent_at")
    background_task_ids = set(hook_state["background_task_ids"]) if hook_state["background_task_ids"] is not None else None
    if (event_name == "SessionStart" and event.get("source") == "startup"
            and hook_state["agent_tool_name"] in AGENT_TOOL_NAMES_WHOSE_HOOKS_REPORT_TURNS_AND_BACKGROUND_TASKS):
        background_task_ids = set()  # a new process runs none yet (Codex's stays unknown: none of its events would list them)
    if event_name == "SessionStart":
        hook_state["session_ended_at"] = None  # a new session in the same process
    if event.get("background_tasks") is not None:  # Claude Code's Stop and SubagentStop
        background_task_ids = list_running_background_task_ids(event["background_tasks"])
    if event.get("background_task_launched_id") and background_task_ids is not None:  # (unknown stays unknown: others may run)
        background_task_ids.add(event["background_task_launched_id"])
    if event_name == "SubagentStop" and background_task_ids is not None:
        background_task_ids.discard(event.get("agent_id"))  # its own list still has it running
    hook_state["background_task_ids"] = background_task_ids
    if event_name == "SessionEnd" and event.get("reason") not in SESSION_END_REASONS_OF_A_PROCESS_THAT_GOES_ON:
        hook_state["session_ended_at"] = event.get("sent_at")
    return hook_state


def hooks_report_no_prompt_since_the_agent_started(hook_state):
    """Returns whether the hooks reported the agent process starting (its SessionStart for a new
    process) and no prompt since, which makes its idle row "new" (user request, 2026-10-30). Only
    for the tools whose hooks report prompts (AGENT_TOOL_NAMES_WHOSE_HOOKS_REPORT_TURNS_AND_BACKGROUND_TASKS);
    a process whose start came before the app recorded starts (no "startup_reported_at") isn't new."""
    return (hook_state is not None
            and hook_state.get("agent_tool_name") in AGENT_TOOL_NAMES_WHOSE_HOOKS_REPORT_TURNS_AND_BACKGROUND_TASKS
            and hook_state.get("startup_reported_at") is not None and hook_state.get("last_prompt_at") is None)


def find_when_the_hooks_ended_the_turn(hook_state, iterm2_status_change_time):
    """Returns when the agent's last turn ended (its Stop event), if that came after its last prompt
    and after iTerm2's status value last changed, else None: the row then goes by the hooks rather
    than an older "working". iTerm2's Claude Code integration keeps the status at working, with
    "1 background task running" in its tab bar, while a background task runs after the turn ended,
    and Claude Code's own report says "shell" then too (checked with Claude Code 2.1.311 on
    2026-10-27); a value set after the Stop, or a later prompt, counts again.

    A Stop that another Stop hook blocks (Claude Code's goal feature) doesn't end the turn; when
    iTerm2's value doesn't change as the turn goes on (it already said working), a tool call of
    the main agent after it tells (a background shell fires no hook, and a background agent's
    events carry its agent_id; code review, 2026-10-28), and so does Claude Code's own report
    (DashboardCore.claude_code_reports_the_turn_going_on, which the core checks before going by
    this). Only for the tools whose hooks report prompts
    (AGENT_TOOL_NAMES_WHOSE_HOOKS_REPORT_TURNS_AND_BACKGROUND_TASKS): without them a turn that
    follows right after (Codex running a message `codex queue` held) would look ended, and Codex
    sets its own status accurately anyway.

    Args:
        hook_state: The agent process's apply_hook_event state, or None.
        iterm2_status_change_time: When iTerm2's status value for the tab last changed, or -inf
            for a value already set when the dashboard first saw the tab (as after a restart).
    """
    if (hook_state is None or hook_state.get("last_turn_end_at") is None
            or hook_state.get("agent_tool_name") not in AGENT_TOOL_NAMES_WHOSE_HOOKS_REPORT_TURNS_AND_BACKGROUND_TASKS):
        return None
    turn_ended_at = hook_state["last_turn_end_at"]
    if (hook_state.get("last_prompt_at") is not None and hook_state["last_prompt_at"] >= turn_ended_at) \
            or iterm2_status_change_time > turn_ended_at:
        return None
    if hook_state.get("last_main_agent_tool_event_at") is not None and hook_state["last_main_agent_tool_event_at"] > turn_ended_at:
        return None
    return turn_ended_at
