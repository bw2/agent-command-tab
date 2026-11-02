"""The core's background thread: the terminals' windows and tabs (through the terminal layer,
terminal_backends), the agents in them, and snapshot publishing.

Everything here runs on one asyncio event loop in a background thread; the
AppKit main thread only receives snapshots (through publish_snapshot) and sends messages and
action requests (through the thread-safe methods of DashboardCoreThread).
"""

import asyncio
import json
import os
import signal
import socket
import sys
import threading
import time

from agent_command_tab import agent_input_box_typing
from agent_command_tab.agent_process_tree import (an_agent_program_runs_through_a_shell_below,
                                                  choose_command_the_agent_is_waiting_on,
                                                  choose_detached_processes_of_the_conversation, choose_helper_process_ids,
                                                  compute_cpu_percent, list_descendant_processes, list_detached_processes,
                                                  list_the_process_and_its_ancestor_ids, process_has_its_terminal, process_is_alive,
                                                  process_is_stopped, read_child_processes,
                                                  read_name_the_process_was_started_as, read_process_arguments_and_environment,
                                                  read_process_current_folder, read_process_details, read_process_info,
                                                  read_process_tree_resource_usage)
from agent_command_tab.hook_event_tracking import (SESSION_START_SOURCES_OF_ANOTHER_CONVERSATION_IN_THE_SAME_PROCESS,
                                                   apply_hook_event, find_agent_process_id_of_hook_event,
                                                   find_when_the_hooks_ended_the_turn,
                                                   hooks_report_no_prompt_since_the_agent_started)
from agent_command_tab.hooks_installer import HOOK_EVENTS_BY_AGENT_TOOL_NAME, check_hooks
from agent_command_tab.hook_states_store import load_hook_states, save_hook_states
from agent_command_tab.agent_session_pausing import (continue_processes_stopped_at_pause, pause_agent_session,
                                                     pause_has_ended, resume_agent_session, send_signal_to_processes)
from agent_command_tab.pause_records_store import load_pause_records, save_pause_records
from agent_command_tab.settings_store import find_program_on_path_or_in_the_usual_folders
from agent_command_tab.agent_adapters import build_built_in_agent_tool_adapters
from agent_command_tab.agent_adapters.base import (COMMAND_THAT_STARTS_A_NEW_CONVERSATION, MESSAGES_THROUGH_CODEX_QUEUE,
                                                   MESSAGES_THROUGH_THE_SEND_MESSAGE_TOOL, command_line_runs_a_shell)
from agent_command_tab.agent_adapters.claude_code import ClaudeCodeAdapter, list_claude_config_folders
from agent_command_tab.agent_adapters.codex import CodexAdapter
from agent_command_tab.dashboard_core_api import ActionRegistry, ActionResult
from agent_command_tab.messages_between_agent_sessions import (SECONDS_A_MESSAGE_WAITS_FOR_AN_AGENT_AT_ITS_PROMPT,
                                                               AgentSessionForMessages,
                                                               build_message_asking_agent_session_to_talk_to_the_others)
from agent_command_tab.snapshot_builder import (SECONDS_BEFORE_AN_OPEN_TOOL_CALL_SHOWS_AS_RUNNING, DisplayedState,
                                                build_computer_snapshot, resolve_agent_sessions)
from agent_command_tab.docked_window_frames_store import (adopt_records_of_restored_windows,
                                                          load_docked_window_frames, save_docked_window_frames)
from agent_command_tab.monitor_assignment import compute_frame_clear_of_area, frames_are_within_tolerance
from agent_command_tab.screen_readings import (compute_screen_fingerprint, count_lines_matching,
                                               screen_shows_permission_dialog)
from agent_command_tab.session_status_computation import (CLAUDE_CODE_SESSION_STATUSES_TO_ITERM2_STATUS_VALUES,
                                                          STATES_OF_A_TURN_UNDER_WAY, STATES_OF_AN_AGENT_WAITING_AT_ITS_PROMPT,
                                                          map_iterm2_status_value_to_state,
                                                          permission_dialog_was_answered,
                                                          status_values_put_the_agent_in_the_same_state,
                                                          track_status_value,
                                                          update_permission_dialog_reading)
from agent_command_tab.snapshot_data_classes import ChildProcessEntry, iterate_agent_session_entries
from agent_command_tab.terminal_backends.base import TerminalConnectionLost, TerminalRequestFailed, TerminalTypingRefused
from agent_command_tab.terminal_backends.iterm2_terminal_backend import Iterm2TerminalBackend
from agent_command_tab.terminal_backends.tmux_terminal_backend import TmuxTerminalBackend
from agent_command_tab.unix_socket_listeners import another_process_listens_at
from agent_command_tab.window_list_ordering import move_window_in_window_list_order, update_window_list_order

FRAME_POLL_INTERVAL_SECONDS = 2
SCREEN_CHECK_INTERVAL_SECONDS = 2
FINGERPRINT_INTERVAL_SECONDS = 30
# When the dashboard first sees a session mid-turn, processes started this long after the agent
# itself count as commands; MCP servers and other helpers start before that.
SECONDS_AN_AGENT_TAKES_TO_START_ITS_HELPERS = 30
SECONDS_BETWEEN_HELPER_PROCESS_RECORDINGS = 30  # while a session is idle
CONTEXT_USAGE_CHECK_INTERVAL_SECONDS = 5
RESOURCE_USAGE_CHECK_INTERVAL_SECONDS = 3  # the Memory and CPU columns
# After a first reading, which gives no CPU use yet, the second follows this soon (code review, 2026-10-29).
SECONDS_BETWEEN_THE_FIRST_TWO_CPU_READINGS = 0.5
CPU_PERCENT_SHOWN_AS_IDLE = 3  # an idle agent's CPU use wavers between 0 and 2 percent
SECONDS_AN_AGENT_GETS_TO_END_BEFORE_IT_IS_KILLED = 5  # "Kill Session": SIGTERM, then SIGKILL (a sub row's "Kill" too)
SECONDS_A_PROCESS_INFO_MEASURES_CPU_USE_OVER = 0.5  # a sub row's Info
CONVERSATION_FILE_RETRY_SECONDS = 15  # a conversation file not found yet (Codex writes it at the first prompt)
CONVERSATION_FILE_RECHECK_SECONDS = 60  # a conversation file found: looked up again in case the conversation changed
CONVERSATION_FILE_READING_NAMES = ("context_tokens", "context_window_tokens", "conversation_preview")  # screen reading values read from it
# How long a working agent may think with nothing new on screen before its row shows "stuck"; no
# longer a setting (user request, 2026-10-26).
STUCK_THRESHOLD_SECONDS = 600
# How long iTerm2 must have said working before Claude Code's own "idle" counts over it without a
# turn end from the hooks: at a turn's start Claude Code's own report may lag iTerm2's for a moment
# (DashboardCore.claude_code_reports_the_turn_over).
SECONDS_BEFORE_CLAUDE_CODES_OWN_IDLE_COUNTS_OVER_WORKING = 10
SMALLEST_WINDOW_WIDTH = 400  # a docked sidebar never makes an iTerm2 window narrower than this
FRAME_TOLERANCE_POINTS = 20  # iTerm2 rounds window sizes to whole character cells
RETRY_DELAY_SECONDS = 1
CLAUDE_AGENTS_REFRESH_INTERVAL_SECONDS = 60
CLAUDE_AGENTS_TIMEOUT_SECONDS = 5
CLAUDE_AGENTS_RETRIES_FOR_A_NEW_PROCESS = 3
CLAUDE_AGENTS_RETRY_DELAY_SECONDS = 3
SNAPSHOT_COALESCING_DELAY_SECONDS = 0.1
# How often the own report of each agent in a terminal that reports no agent status (tmux) is read,
# since nothing tells the core when it changes (Claude Code's session file).
SECONDS_BETWEEN_OWN_STATUS_READINGS = 1
CODEX_QUEUE_TIMEOUT_SECONDS = 30
# How often "Talk to Each Other" checks whether a held message can be pasted (user request, 2026-10-21).
SECONDS_BETWEEN_CHECKS_FOR_AN_AGENT_AT_ITS_PROMPT = 3
# How long the row of an agent that ended without a normal session end stays, gray and "exited"
# (a fixed time rather than a setting, as for "stuck").
EXITED_ROW_SECONDS = 300
CHILD_PROCESS_CHECK_INTERVAL_SECONDS = 3  # an open tab row's sub rows and the background shells
LONGEST_COMMAND_TEXT = 500  # characters of a sub row's command kept in the snapshot
HOOKS_CHECK_INTERVAL_SECONDS = 10  # whether the agent tools' settings hold the app's hooks
LONGEST_HOOK_EVENT_LINE = 1024 ** 2
# How often a copy that left the hook events socket to another copy checks whether that one has ended.
SECONDS_BETWEEN_CHECKS_FOR_ANOTHER_HOOK_EVENTS_LISTENER = 5


def find_claude_command_path():
    """Returns the path of the `claude` command (the stable link on PATH or in the usual folders,
    not a versioned file), or None."""
    return find_program_on_path_or_in_the_usual_folders("claude")


async def read_claude_agents_by_process_id(claude_command_path, config_folders):
    """Runs `claude agents --json` once per config folder; returns {process ID: record}, each
    record with "config_folder" added (None for the default folder).

    Failures (no command, a timeout, unreadable output) give an empty result for that folder.
    """
    claude_agents_by_process_id = {}
    for config_folder in config_folders:
        environment = dict(os.environ)
        environment.pop("CLAUDE_CONFIG_DIR", None)
        if config_folder:
            environment["CLAUDE_CONFIG_DIR"] = config_folder
        process = None
        try:
            process = await asyncio.create_subprocess_exec(
                claude_command_path, "agents", "--json", env=environment,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
            standard_output, _ = await asyncio.wait_for(process.communicate(), timeout=CLAUDE_AGENTS_TIMEOUT_SECONDS)
            for record in json.loads(standard_output or b"[]"):
                if isinstance(record, dict) and record.get("pid"):
                    claude_agents_by_process_id[int(record["pid"])] = dict(record, config_folder=config_folder)
        except (OSError, ValueError, asyncio.TimeoutError) as error:
            print(f"claude agents --json failed for config folder {config_folder or '(default)'}: {error!r}")
            if process is not None and process.returncode is None:
                process.kill()
                await process.wait()
    return claude_agents_by_process_id


def read_claude_code_session_status(config_folder, process_id):
    """Returns the status Claude Code keeps for a running session ("idle", "busy", "waiting", or
    "shell" while it runs a command; seen on 2026-10-21), read now from
    <config folder>/sessions/<process ID>.json (the file `claude agents --json` reads), or None
    when it can't be read. Cheap enough to read just before typing into a session, and for each
    snapshot for a tab iTerm2 gives no status, while the record from `claude agents --json` may be
    a minute old.

    Args:
        config_folder: The record's "config_folder", or None for the default ~/.claude.
        process_id: The Claude Code process ID.
    """
    path = os.path.join(config_folder or os.path.expanduser("~/.claude"), "sessions", f"{process_id}.json")
    try:
        with open(path) as session_file:
            return json.load(session_file).get("status")
    except (OSError, ValueError, AttributeError):
        return None


async def queue_message_for_codex_session(codex_command_path, conversation_id, message):
    """Runs `codex queue --thread <conversation ID> --message <text>`, which hands the message to
    the running Codex session; it runs it as its next turn, after any turn under way (checked live
    with Codex 0.155.1 on 2026-10-21). Returns an ActionResult."""
    if not codex_command_path:
        return ActionResult(False, "the codex command wasn't found")
    process = None
    try:
        # The message is attached to its option: on its own, a message starting with "-" (a note
        # that begins with a bullet) would be read as an unknown option.
        process = await asyncio.create_subprocess_exec(
            codex_command_path, "queue", "--thread", conversation_id, f"--message={message}",
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
        _, standard_error = await asyncio.wait_for(process.communicate(), timeout=CODEX_QUEUE_TIMEOUT_SECONDS)
    except (OSError, asyncio.TimeoutError) as error:
        if process is not None and process.returncode is None:
            process.kill()
            await process.wait()
        return ActionResult(False, f"codex queue failed: {error!r}")
    if process.returncode != 0:
        return ActionResult(False, f"codex queue failed: {standard_error.decode(errors='replace').strip()[-300:]}")
    return ActionResult(True)


def round_memory_and_cpu_use(memory_bytes, cpu_percent):
    """Returns the memory to the megabyte and the CPU use to a whole percent, 0 below
    CPU_PERCENT_SHOWN_AS_IDLE (None stays None), so an idle session's rows and the snapshot don't
    change at every reading."""
    if cpu_percent is not None:
        cpu_percent = round(cpu_percent) if cpu_percent >= CPU_PERCENT_SHOWN_AS_IDLE else 0
    return round(memory_bytes / 1024 ** 2) * 1024 ** 2, cpu_percent


def convert_exception_to_action_result(result_or_exception):
    """Returns an ActionResult from asyncio.gather(..., return_exceptions=True): a failed one for an exception."""
    if isinstance(result_or_exception, Exception):
        return ActionResult(False, f"it failed: {result_or_exception!r}")
    return result_or_exception


def build_conversation_file_record(conversation_key, path, looked_up_at, cleared_path):
    """Returns what the core keeps about a session's conversation file: the conversation it is for
    (DashboardCore.find_conversation_key), its path or None while not found, when it was looked
    up, the modification time of its last reading (None until read), and the file of a
    conversation /clear ended, which lookups skip (or None)."""
    return {"key": conversation_key, "path": path, "looked_up_at": looked_up_at, "modified_at": None,
            "cleared_path": cleared_path}


def explain_why_an_agent_session_cant_start(folder, launch_command):
    """Returns why an agent session can't start in folder ("~" expanded already) with
    launch_command, or "" when it can (checked before any terminal opens a tab for it)."""
    if not os.path.isdir(folder):
        return f"the folder {folder} doesn't exist"
    if not launch_command.strip():
        return "the agent tool has no launch command (see Settings)"
    return ""


def keep_the_hook_states_of_the_processes_still_running(started_at_and_hook_state_by_process_id):
    """Returns {agent process ID: hook state} from saved hook states ({process ID: (start time,
    hook state)}, hook_states_store.load_hook_states), for the processes that still run with the
    start time stored with their state."""
    hook_state_by_agent_process_id = {}
    for process_id, (started_at, hook_state) in started_at_and_hook_state_by_process_id.items():
        process = read_process_info(process_id)
        if process is not None and process.started_at == started_at:
            hook_state_by_agent_process_id[process_id] = hook_state
    return hook_state_by_agent_process_id


class DashboardCore:
    """The core: owns all shared state and runs on the background thread's event loop.

    Args:
        publish_snapshot: Called on the background thread with each new ComputerSnapshot.
        report_connection_lost: Called on the background thread with a message when an
            established iTerm2 connection closes, or the core stops; the app then exits so its
            supervisor restarts it.
        verbose: Print diagnostics.
        docked_window_frames_file_path: Where the frames windows had before a docked sidebar
            narrowed them are kept across restarts; None keeps them in memory only.
        pause_records_file_path: Where the records of paused agent sessions are kept across
            restarts (pause_records_store); None keeps them in memory only.
        pause_record_by_tab_and_agent_to_start_with: The records to start with when
            pause_records_file_path is None (dump-snapshot reads the app's file but never writes it).
        hook_events_socket_path: The socket the app's hooks send their events to
            (hook_event_forwarder.HOOK_EVENTS_SOCKET_PATH), or None to listen to none
            (dump-snapshot, which must not take the app's socket away).
        hook_states_file_path: Where what the agent processes' hooks reported is kept across
            restarts (hook_states_store); None keeps it in memory only.
        started_at_and_hook_state_by_agent_process_id_to_start_with: The saved hook states
            (hook_states_store.load_hook_states) to start with when hook_states_file_path is None
            (dump-snapshot reads the app's file but never writes it).
        report_terminal_problem: Called on the background thread with a message while a
            terminal runs but can't be reached (iTerm2 with its Python API off), and with None
            once every terminal can; the other terminals' agents are listed meanwhile.
    """

    def __init__(self, publish_snapshot, report_connection_lost, verbose=False, docked_window_frames_file_path=None,
                 pause_records_file_path=None, pause_record_by_tab_and_agent_to_start_with=None, hook_events_socket_path=None,
                 hook_states_file_path=None, started_at_and_hook_state_by_agent_process_id_to_start_with=None,
                 report_terminal_problem=None):
        self.publish_snapshot = publish_snapshot
        self.report_connection_lost = report_connection_lost
        self.report_terminal_problem = report_terminal_problem or (lambda message: None)
        self.verbose = verbose
        # The terminals, in the order their windows are listed (user request, 2026-11-01).
        self.terminal_backends = [
            terminal_backend_class(lambda: self.regather_needed.set(), lambda: self.snapshot_needed.set(),
                                   self.note_terminal_problem)
            for terminal_backend_class in (Iterm2TerminalBackend, TmuxTerminalBackend)]
        self.terminal_backend_by_name = {terminal_backend.terminal_name: terminal_backend
                                         for terminal_backend in self.terminal_backends}
        self.terminal_problem_by_terminal_name = {}  # note_terminal_problem's messages
        self.docked_window_frames_file_path = docked_window_frames_file_path
        self.pause_records_file_path = pause_records_file_path
        self.hook_events_socket_path = hook_events_socket_path
        self.hook_states_file_path = hook_states_file_path
        # What each agent process's hooks reported (hook_event_tracking.apply_hook_event), by its
        # process ID, kept while it runs or is a tab's agent, and while its row shows "exited";
        # those of the processes still running come back after a restart.
        self.hook_state_by_agent_process_id = keep_the_hook_states_of_the_processes_still_running(
            load_hook_states(hook_states_file_path) if hook_states_file_path
            else started_at_and_hook_state_by_agent_process_id_to_start_with or {})
        # {session ID: {"entry": the AgentSessionEntry its row last showed, "ended_at": when its
        # agent ended, "process_id": that agent's}} for the tabs whose agent ended without a normal
        # session end, shown as "exited" for EXITED_ROW_SECONDS (publish_snapshot_if_changed hands
        # build_computer_snapshot (entry, ended_at) pairs).
        self.exited_agent_sessions_by_session_id = {}
        self.last_agent_session_entry_by_session_id = {}  # from the last snapshot, for an "exited" row
        self.process_ids_ended_from_the_list = set()  # "Kill Session": their end is no crash
        self.hooks_status_per_agent_tool = ()  # ComputerSnapshot.hooks_status_per_agent_tool
        self.hooks_check_needed = asyncio.Event()
        self.specific_adapters, self.generic_adapter = build_built_in_agent_tool_adapters()
        self.process_ids_asked_about_in_claude_agents = set()
        self.claude_agents_retry_count_by_process_id = {}
        self.agent_sessions = {}
        self.claude_agents_refresh_is_running = False
        self.computer_id = socket.gethostname()
        self.computer_name = socket.gethostname().split(".")[0]
        self.claude_command_path = find_claude_command_path()
        # For `codex queue`; the app's settings then name it (set_core_settings).
        self.codex_command_path = find_program_on_path_or_in_the_usual_folders("codex")
        self.message_typing_lock_by_session_id = {}  # two held messages are never pasted into one tab at once
        self.gathered_windows = []  # every terminal's, in the order of terminal_backends
        self.gathered_windows_by_terminal_name = {}  # each terminal's last gather, kept when a regather of it fails
        # Till the first gather of a terminal whose windows have frames (iTerm2), saved frames can't
        # be matched to windows.
        self.windows_were_gathered = False
        self.monitors = []
        self.window_order = []
        self.session_tracking_by_session_id = {}
        # Codex agent processes that shared their folder with another Codex session while they ran
        # (remember_codex_sessions_that_share_a_folder).
        self.codex_process_ids_that_shared_their_folder = set()
        # For each tab whose row goes by its own agent (choose_status_value_of_the_tabs_own_agent):
        # the status iTerm2 held at the last check while an agent program ran inside the tab's agent.
        self.iterm2_status_seen_while_an_inner_agent_ran_by_session_id = {}
        # {session ID: (the status value set in iTerm2, when the dashboard first saw it, when it was
        # set as far as the hooks go)}, so a turn end its hooks report counts until iTerm2 sets another
        # value (find_when_the_hooks_ended_the_turn). A value already set when the dashboard first sees
        # the tab was set at a time it doesn't know, maybe before a turn end the hooks reported while
        # the app wasn't running, so that one is -inf: after each restart of the app, a row whose agent
        # had ended its turn with a background task running showed "stuck" (user report, 2026-10-29).
        self.status_set_in_iterm2_and_since_by_session_id = {}
        # {session ID: the agent's own report as a status value (read_status_value_of_the_tabs_own_agent)}
        # at the last reading, for the tabs of a terminal that reports no agent status (tmux).
        self.own_status_value_by_session_id = {}
        self.displayed_state_by_session_id = {}
        self.screen_readings_by_session_id = {}
        # {(session ID, agent process ID): agent_session_pausing.PauseRecord}, until the agent runs
        # again or is gone; an agent still paused from before a restart is listed again from its
        # record. By agent too: a tab can hold a paused agent and, at the shell, a newer one.
        self.pause_record_by_tab_and_agent = (load_pause_records(pause_records_file_path) if pause_records_file_path
                                              else dict(pause_record_by_tab_and_agent_to_start_with or {}))
        self.session_ids_being_paused_or_resumed = set()
        self.reserved_screen_area = None
        self.frames_before_making_room_by_window_id = (load_docked_window_frames(docked_window_frames_file_path)
                                                       if docked_window_frames_file_path else {})
        self.saved_docked_window_frames = dict(self.frames_before_making_room_by_window_id)
        self.records_loaded_from_file_await_adoption = bool(self.frames_before_making_room_by_window_id)
        self.reserved_area_was_received = False  # until the main thread says whether the sidebar is docked
        self.window_room_lock = asyncio.Lock()
        self.left_out_agent_tool_names = frozenset()  # the settings' Harnesses section
        self.resource_usage_is_read = False  # while the Memory or CPU column is shown, or the list is sorted by one
        self.resource_usage_reading_needed = asyncio.Event()  # set when resource_usage_is_read turns on
        self.claude_agents_by_process_id = {}
        self.last_seen_session_id = None
        self.dashboard_is_active = False
        # The last app other than the dashboard to be active (the main thread's), whose windows may
        # show a tmux client (find_session_id_in_front).
        self.last_other_active_application_process_id = None
        self.last_published_snapshot_without_time = None
        self.snapshot_needed = asyncio.Event()
        self.regather_needed = asyncio.Event()
        self.screen_check_needed = asyncio.Event()
        self.conversation_files_by_session_id = {}  # build_conversation_file_record's records
        self.peek_lock = asyncio.Lock()  # a quick release must not undo a peek that hasn't happened yet
        self.what_was_in_front_before_peek_by_terminal_name = None  # each terminal's remember_what_is_in_front
        self.action_registry = ActionRegistry()
        self.register_actions()

    # ----- the terminals -----

    def note_terminal_problem(self, terminal_name, message):
        """Receives a terminal backend's problem (a message) or its end (None), and reports the
        problems of every terminal together, when they changed."""
        problems_before = dict(self.terminal_problem_by_terminal_name)
        if message is None:
            self.terminal_problem_by_terminal_name.pop(terminal_name, None)
        else:
            print(f"{terminal_name}: {message}", file=sys.stderr)  # dump-snapshot's standard output holds only its JSON
            self.terminal_problem_by_terminal_name[terminal_name] = message
        if self.terminal_problem_by_terminal_name != problems_before:
            self.report_terminal_problem("; ".join(self.terminal_problem_by_terminal_name.values()) or None)

    def find_terminal_backend_and_gathered_window(self, window_id=None, tab_id=None, session_id=None):
        """Returns (the terminal backend, the gathered window) holding a window, a tab or a
        session, from the last gather, or (None, None)."""
        for gathered_window in self.gathered_windows:
            if gathered_window["window_id"] == window_id or any(
                    gathered_tab["tab_id"] == tab_id
                    or (gathered_tab["first_session"] is not None and gathered_tab["first_session"]["session_id"] == session_id)
                    for gathered_tab in gathered_window["tabs"]):
                return self.terminal_backend_by_name[gathered_window["terminal_name"]], gathered_window
        return None, None

    def find_terminal_backend_of(self, window_id=None, tab_id=None, session_id=None):
        """Returns the terminal backend whose last gather holds a window, a tab or a session, or None."""
        return self.find_terminal_backend_and_gathered_window(window_id, tab_id, session_id)[0]

    def find_terminal_session(self, session_id):
        """Returns the TerminalSession of a session, or None when it is gone."""
        terminal_backend = self.find_terminal_backend_of(session_id=session_id)
        return terminal_backend.find_session(session_id) if terminal_backend is not None else None

    def list_gathered_windows_with_frames(self):
        """Returns the gathered windows of the terminals whose windows have frames (iTerm2's)."""
        return [gathered_window for gathered_window in self.gathered_windows
                if self.terminal_backend_by_name[gathered_window["terminal_name"]].windows_have_frames]

    # ----- actions -----

    def register_actions(self):
        """Registers every action."""
        register = self.action_registry.register_action
        register("focus_agent_session", self.handle_focus_agent_session)
        register("focus_window", self.handle_focus_window)
        register("rename_tab", self.handle_rename_tab)
        register("rename_window", self.handle_rename_window)
        register("move_tab", self.handle_move_tab)
        register("move_tab_to_new_window", self.handle_move_tab_to_new_window)
        register("reorder_window_list", self.handle_reorder_window_list)
        register("start_agent_session", self.handle_start_agent_session)
        register("move_window_to_monitor", self.handle_move_window_to_monitor)
        register("close_window", self.handle_close_window)
        register("duplicate_agent_session", self.handle_duplicate_agent_session)
        register("peek_agent_session", self.handle_peek_agent_session)
        register("end_peek", self.handle_end_peek)
        register("send_slash_command", self.handle_send_slash_command)
        register("send_slash_command_to_agent_sessions", self.handle_send_slash_command_to_agent_sessions)
        register("end_agent_process", self.handle_end_agent_process)
        register("end_agent_processes", self.handle_end_agent_processes)
        register("read_child_process_details", self.handle_read_child_process_details)
        register("end_child_process", self.handle_end_child_process)
        register("pause_agent_sessions", self.handle_pause_agent_sessions)
        register("resume_agent_sessions", self.handle_resume_agent_sessions)
        register("ask_agent_sessions_to_talk_to_each_other", self.handle_ask_agent_sessions_to_talk_to_each_other)

    async def perform_action(self, action_request):
        """Runs an action request, then asks for a fresh gather and snapshot.

        The gather itself runs in publish_when_needed, so gathers never overlap and their
        errors are handled in one place.
        """
        action_result = await self.action_registry.perform_action(action_request)
        if self.verbose or not action_result.succeeded:
            print(f"action {action_request.action}: {action_result}")
        self.regather_needed.set()
        return action_result

    async def bring_forward_the_terminal_tab_running_the_client(self, action_result):
        """After a terminal that shows its windows through clients (tmux) brought a tab to the
        front in its client (details {"terminal_client_process_id"}): brings the tab of another
        terminal running that client (an iTerm2 tab) to the front too. Returns the ActionResult
        to report; with the details kept when no such tab is listed, so the app brings that
        client's terminal app to the front itself."""
        client_process_id = (action_result.details or {}).get("terminal_client_process_id")
        if not action_result.succeeded or client_process_id is None:
            return action_result
        for gathered_window in self.gathered_windows:
            terminal_backend = self.terminal_backend_by_name[gathered_window["terminal_name"]]
            if terminal_backend.shows_windows_through_clients:
                continue
            for gathered_tab in gathered_window["tabs"]:
                first_session = gathered_tab["first_session"]
                if first_session is not None and first_session["job_pid"] == client_process_id:
                    focus_result = await terminal_backend.focus_session(first_session["session_id"])
                    if not focus_result.succeeded:
                        return focus_result
                    other_details = {key: value for key, value in action_result.details.items()
                                     if key != "terminal_client_process_id"}
                    return action_result._replace(details=other_details or None)
        return action_result

    def note_the_tab_shown_as_seen(self, session_id):
        """Stamps a tab brought to the front from the list as seen: also one whose tmux client runs
        in a terminal app other than iTerm2, which find_seen_session_id can't tell."""
        if session_id in self.session_tracking_by_session_id:
            self.session_tracking_by_session_id[session_id]["last_time_tab_was_seen"] = time.time()
            self.snapshot_needed.set()

    async def handle_focus_agent_session(self, action_request):
        """Brings a tab to the front (a peek too); it then counts as seen (note_the_tab_shown_as_seen)."""
        session_id = action_request.target["session_id"]
        terminal_backend = self.find_terminal_backend_of(session_id=session_id)
        if terminal_backend is None:
            return ActionResult(False, "that tab no longer exists")
        action_result = await self.bring_forward_the_terminal_tab_running_the_client(await terminal_backend.focus_session(session_id))
        if action_result.succeeded:
            self.note_the_tab_shown_as_seen(session_id)
        return action_result

    async def handle_focus_window(self, action_request):
        """Brings a window to the front with the tab it shows, which then counts as seen (note_the_tab_shown_as_seen)."""
        window_id = action_request.target["window_id"]
        terminal_backend = self.find_terminal_backend_of(window_id=window_id)
        if terminal_backend is None:
            return ActionResult(False, "that window no longer exists")
        action_result = await self.bring_forward_the_terminal_tab_running_the_client(await terminal_backend.focus_window(window_id))
        selected_session_id = self.find_first_session_id_of_tab(terminal_backend.find_selected_tab_id(window_id))
        if action_result.succeeded and selected_session_id is not None:
            self.note_the_tab_shown_as_seen(selected_session_id)
        return action_result

    async def handle_rename_tab(self, action_request):
        terminal_backend = self.find_terminal_backend_of(tab_id=action_request.target["tab_id"])
        if terminal_backend is None:
            return ActionResult(False, "that tab no longer exists")
        return await terminal_backend.rename_tab(action_request.target["tab_id"], action_request.arguments["new_title"])

    async def handle_rename_window(self, action_request):
        terminal_backend = self.find_terminal_backend_of(window_id=action_request.target["window_id"])
        if terminal_backend is None:
            return ActionResult(False, "that window no longer exists")
        return await terminal_backend.rename_window(action_request.target["window_id"], action_request.arguments["new_title"])

    async def handle_move_tab(self, action_request):
        """Moves a tab within its window or into another window of the same terminal (an iTerm2
        tab can't go into a tmux session, nor the other way)."""
        terminal_backend = self.find_terminal_backend_of(tab_id=action_request.target["tab_id"])
        target_window_id = action_request.arguments["target_window_id"]
        target_terminal_backend = self.find_terminal_backend_of(window_id=target_window_id)
        if terminal_backend is None or target_terminal_backend is None:
            return ActionResult(False, "that tab or window no longer exists")
        if target_terminal_backend is not terminal_backend:
            return ActionResult(False, f"a {terminal_backend.terminal_name} tab can't move into a "
                                       f"{target_terminal_backend.terminal_name} window")
        return await terminal_backend.move_tab(action_request.target["tab_id"], target_window_id,
                                               action_request.arguments.get("drop_before_tab_id"))

    async def handle_move_tab_to_new_window(self, action_request):
        terminal_backend = self.find_terminal_backend_of(tab_id=action_request.target["tab_id"])
        if terminal_backend is None:
            return ActionResult(False, "that tab no longer exists")
        return await terminal_backend.move_tab_to_new_window(action_request.target["tab_id"])

    def choose_terminal_backend_for_a_new_window(self):
        """Returns the terminal a new window opens in: the first one connected, in the order of
        terminal_backends (iTerm2, then tmux), or None."""
        return next((terminal_backend for terminal_backend in self.terminal_backends if terminal_backend.is_connected()), None)

    def explain_why_no_terminal_can_open_a_window(self):
        """Returns why no terminal is connected for a new window: one runs but can't be reached
        (iTerm2 with its Python API off), or none runs."""
        terminal_names_that_run = [terminal_backend.terminal_name for terminal_backend in self.terminal_backends
                                   if terminal_backend.terminal_runs()]
        if terminal_names_that_run:
            return f"{' and '.join(terminal_names_that_run)} can't be reached yet (see the line at the top of the list)"
        return "no terminal runs: start iTerm2, or a tmux session"

    async def handle_start_agent_session(self, action_request):
        """Target {"window_id": ...} opens a tab there; an empty target opens a new window
        (choose_terminal_backend_for_a_new_window)."""
        window_id = action_request.target.get("window_id")
        terminal_backend = (self.find_terminal_backend_of(window_id=window_id) if window_id is not None
                            else self.choose_terminal_backend_for_a_new_window())
        if terminal_backend is None:
            return ActionResult(False, "that window no longer exists" if window_id is not None
                                else self.explain_why_no_terminal_can_open_a_window())
        folder = os.path.expanduser(action_request.arguments["folder"])
        reason_it_cant_start = explain_why_an_agent_session_cant_start(folder, action_request.arguments["launch_command"])
        if reason_it_cant_start:
            return ActionResult(False, reason_it_cant_start)
        return await self.bring_forward_the_terminal_tab_running_the_client(await terminal_backend.start_agent_session(
            window_id, folder, action_request.arguments["launch_command"]))

    async def handle_send_slash_command(self, action_request):
        """Types /compact or /clear into an agent session that waits at its prompt, idle, done,
        background or new as its row shows it (STATES_OF_AN_AGENT_WAITING_AT_ITS_PROMPT;
        agent_input_box_typing.type_slash_command checks its input box)."""
        session_id = action_request.target["session_id"]
        # Checked and typed holding the tab's lock, as every typing into a tab is: a held message may be being pasted.
        async with self.message_typing_lock_by_session_id.setdefault(session_id, asyncio.Lock()):
            agent_session = self.agent_sessions.get(session_id)
            if agent_session is None:
                return ActionResult(False, "that tab no longer runs an agent")
            if agent_session["is_in_background"]:
                # Its row keeps the state from before, but keys would go to the shell or editor in front.
                return ActionResult(False, "the agent is suspended or another program is in front of it")
            displayed_state = self.displayed_state_by_session_id.get(session_id)
            state = displayed_state.state if displayed_state is not None else "unknown"
            if state not in STATES_OF_AN_AGENT_WAITING_AT_ITS_PROMPT:
                return ActionResult(False, f"the agent is {state}; commands are typed only while it waits at its prompt")
            if self.claude_code_reports_the_session_busy(session_id, agent_session):
                return ActionResult(False, "Claude Code reports the session busy; commands are typed only while it waits at its prompt")
            return await self.type_slash_command_into_agent_session(
                session_id, agent_session, action_request.arguments["command"], waits_at_its_prompt=True) or ActionResult(
                False, "the tab has unsent text in its input box, the input box isn't on screen, or the agent refused it during a turn")

    async def type_slash_command_into_agent_session(self, session_id, agent_session, command, waits_at_its_prompt):
        """Types the command (agent_input_box_typing.type_slash_command: None when nothing was
        typed, the input box holding unsent text or being off screen). A /clear typed at the
        prompt forgets the cleared conversation at once; one the agent queues during its turn runs
        only when the turn ends, so its new conversation is found as after a /clear typed by hand
        (the `claude agents` refresh)."""
        terminal_session = self.find_terminal_session(session_id)
        if terminal_session is None:
            return ActionResult(False, "that tab no longer exists")
        clears_at_once = command == COMMAND_THAT_STARTS_A_NEW_CONVERSATION and waits_at_its_prompt
        # Known before the conversation ends, since a lookup after /clear can't tell old from new.
        path_of_conversation_to_clear = await self.find_current_conversation_file(session_id, agent_session) \
            if clears_at_once else None
        action_result = await agent_input_box_typing.type_slash_command(
            terminal_session, command, agent_session["adapter"], agent_is_in_the_middle_of_a_turn=not waits_at_its_prompt)
        if action_result is not None and action_result.succeeded and clears_at_once:
            self.forget_the_cleared_conversation(session_id, agent_session, path_of_conversation_to_clear)
        return action_result

    async def handle_send_slash_command_to_agent_sessions(self, action_request):
        """Types /compact or /clear (arguments {"command": ...}) into every session of target
        {"session_ids": [...]}, those in the middle of a turn too (the menu of several selected
        rows; user requests, 2026-10-21): type_slash_command_once_the_agent_can_take_it for each."""
        return await self.run_for_each_agent_session(action_request, self.type_slash_command_once_the_agent_can_take_it)

    async def type_slash_command_once_the_agent_can_take_it(self, action_request):
        """Types /compact or /clear into one agent session (target {"session_id": ...}) at once when
        it waits at its prompt (for a row without a state, when its screen shows no turn under
        way), or when its tool queues commands typed during a turn (Claude Code runs them when the
        turn ends). Otherwise (Codex refuses them during a task), and while the
        input box holds unsent text or is off screen (a permission prompt), checks again every
        SECONDS_BETWEEN_CHECKS_FOR_AN_AGENT_AT_ITS_PROMPT, as a held Talk to Each Other message
        does. Gives up after SECONDS_A_MESSAGE_WAITS_FOR_AN_AGENT_AT_ITS_PROMPT, or once the tab's
        agent process changes; at once for an agent that is suspended or has another program in
        front of it."""
        session_id = action_request.target["session_id"]
        command = action_request.arguments["command"]
        agent_session = self.agent_sessions.get(session_id)
        if agent_session is None or not agent_session["process_id"]:
            return ActionResult(False, "that tab no longer runs an agent")
        if agent_session["is_in_background"]:
            return ActionResult(False, "the agent is suspended or another program is in front of it")
        async def type_it_if_the_agent_can_take_it(agent_session):
            waits_at_its_prompt = (self.agent_waits_at_its_prompt(session_id, agent_session)
                                   or await self.agent_whose_state_is_unknown_shows_no_turn_on_screen(session_id, agent_session))
            if agent_session["is_in_background"] or not (
                    waits_at_its_prompt or agent_session["adapter"].queues_slash_commands_typed_during_a_turn):
                return None
            return await self.type_slash_command_into_agent_session(session_id, agent_session, command, waits_at_its_prompt)
        return await self.keep_trying_until_the_agent_takes_it(
            session_id, agent_session["process_id"], type_it_if_the_agent_can_take_it, command, "typed")

    async def keep_trying_until_the_agent_takes_it(self, session_id, agent_process_id, try_once, what_is_typed, how_it_is_typed):
        """Calls try_once every SECONDS_BETWEEN_CHECKS_FOR_AN_AGENT_AT_ITS_PROMPT until it returns
        an ActionResult, holding the tab's typing lock: a held message or command may have just
        been typed, starting a turn. Gives up after SECONDS_A_MESSAGE_WAITS_FOR_AN_AGENT_AT_ITS_PROMPT,
        once the tab's agent process is no longer agent_process_id, or once the tab closes. The
        slash commands and the Talk to Each Other messages share it (code review, 2026-10-29).

        Args:
            session_id: The agent session's tab.
            agent_process_id: The process ID of its agent when the action started.
            try_once: async function(agent session dict) returning the ActionResult of typing, or
                None while the agent can't take it yet.
            what_is_typed: For the failure messages, for example "/compact" or "the message".
            how_it_is_typed: For the failure messages: "typed" or "pasted".
        """
        deadline = time.time() + SECONDS_A_MESSAGE_WAITS_FOR_AN_AGENT_AT_ITS_PROMPT
        typing_lock = self.message_typing_lock_by_session_id.setdefault(session_id, asyncio.Lock())
        while True:
            async with typing_lock:
                agent_session = self.agent_sessions.get(session_id)
                if agent_session is None or agent_session["process_id"] != agent_process_id:
                    return ActionResult(False, f"its agent ended before {what_is_typed} could be {how_it_is_typed}")
                try:
                    action_result = await try_once(agent_session)
                except TerminalTypingRefused as error:
                    return ActionResult(False, str(error))
                except TerminalRequestFailed:
                    return ActionResult(False, "the tab closed meanwhile")
                if action_result is not None:
                    return action_result
            if time.time() >= deadline:
                return ActionResult(False, "it didn't wait at its prompt with an empty input box within "
                                           f"{SECONDS_A_MESSAGE_WAITS_FOR_AN_AGENT_AT_ITS_PROMPT // 60} minutes, "
                                           f"so {what_is_typed} wasn't {how_it_is_typed}")
            await asyncio.sleep(SECONDS_BETWEEN_CHECKS_FOR_AN_AGENT_AT_ITS_PROMPT)

    async def run_for_each_agent_session(self, action_request, handle_one):
        """Runs a one-session action (handle_one, taking target {"session_id": ...}) for every
        session of target {"session_ids": [...]} at once; returns one ActionResult whose message
        names each tab that failed and why."""
        session_ids = action_request.target["session_ids"]
        action_results = await asyncio.gather(*(handle_one(action_request._replace(target={"session_id": session_id}))
                                                for session_id in session_ids), return_exceptions=True)
        return self.combine_results_naming_the_tabs_that_failed(
            session_ids, [convert_exception_to_action_result(action_result) for action_result in action_results])

    def combine_results_naming_the_tabs_that_failed(self, session_ids, action_results):
        """Returns one ActionResult for an action on several agent sessions, whose message names
        each tab that failed and why."""
        failures = [f'"{self.find_tab_title_of_agent_session(session_id) or "a tab"}": {action_result.message}'
                    for session_id, action_result in zip(session_ids, action_results) if not action_result.succeeded]
        return ActionResult(not failures, "; ".join(failures))

    def find_tab_title_of_agent_session(self, session_id):
        """Returns the title the last snapshot shows for the agent session's tab, or ""."""
        if self.last_published_snapshot_without_time is None:
            return ""
        return next((agent_session_entry.tab_title
                     for _, _, agent_session_entry in iterate_agent_session_entries(self.last_published_snapshot_without_time)
                     if agent_session_entry.session_id == session_id), "")

    def agent_waits_at_its_prompt(self, session_id, agent_session):
        """Whether the agent session waits at its prompt: its row shows idle, done, background or
        new (STATES_OF_AN_AGENT_WAITING_AT_ITS_PROMPT), and Claude Code doesn't report it busy."""
        displayed_state = self.displayed_state_by_session_id.get(session_id)
        return (displayed_state is not None and displayed_state.state in STATES_OF_AN_AGENT_WAITING_AT_ITS_PROMPT
                and not self.claude_code_reports_the_session_busy(session_id, agent_session))

    async def agent_whose_state_is_unknown_shows_no_turn_on_screen(self, session_id, agent_session):
        """Whether an agent session whose row has no state (Codex 0.155.1 reports none to iTerm2)
        shows no turn under way on screen (its adapter's working_screen_pattern, such as Codex's
        "(5s • esc to interrupt)"), so it waits at its prompt. False when the row has a state, or
        the adapter no such pattern."""
        displayed_state = self.displayed_state_by_session_id.get(session_id)
        working_screen_pattern = agent_session["adapter"].working_screen_pattern
        terminal_session = self.find_terminal_session(session_id)
        if working_screen_pattern is None or terminal_session is None or (
                displayed_state is not None and displayed_state.state != "unknown"):
            return False
        return count_lines_matching(await terminal_session.read_screen_lines(), working_screen_pattern) == 0

    def claude_code_reports_the_session_busy(self, session_id, agent_session):
        """Whether Claude Code itself reports the agent session as anything but idle right now
        (read_claude_code_session_status). Checked just before typing into it: a row may take its
        idle state from a `claude agents --json` record up to a minute old, and Claude Code's input
        box is empty during a turn too. False when Claude Code reports nothing (another tool). Its
        "shell" while a background task runs after the turn ended, as its hooks report
        (find_when_the_hooks_ended_the_turn), counts as at the prompt: its input box takes text
        then (code review, 2026-10-27). With no background task running, that "shell" is a
        command of a turn that another Stop hook kept going (claude_code_reports_the_turn_going_on;
        code review, 2026-10-28)."""
        claude_agent_record = self.claude_agents_by_process_id.get(agent_session["process_id"])
        if claude_agent_record is None:
            return False
        status = read_claude_code_session_status(claude_agent_record.get("config_folder"), agent_session["process_id"])
        hook_state = self.hook_state_by_agent_process_id.get(agent_session["process_id"])
        if status == "shell" and (hook_state or {}).get("background_task_ids") != set() and find_when_the_hooks_ended_the_turn(
                hook_state, self.status_set_in_iterm2_and_since_by_session_id.get(session_id, (None, float("inf"), float("inf")))[2]) is not None:
            return False
        return status is not None and status != "idle"

    def claude_code_reports_the_turn_over(self, agent_session, seconds_working_has_held):
        """Whether Claude Code's own report of the session (read_claude_code_session_status) says
        idle, for a tab iTerm2 says is working, at a moment that can't be a turn's start: the
        hooks reported the turn's end and no prompt since, or working has held for
        SECONDS_BEFORE_CLAUDE_CODES_OWN_IDLE_COUNTS_OVER_WORKING since iTerm2's value changed and
        since the last prompt the hooks reported (Claude Code's own report may lag a new turn's
        start; code review, 2026-10-28). iTerm2's Claude Code integration set working again after
        turns that had ended with nothing running, which left their rows "stuck" (user report,
        2026-10-28; what set it isn't known). Claude Code says "shell" while a command runs and
        "busy" while a Stop hook keeps the turn going, so neither ends here. False when Claude
        Code reports nothing (another tool)."""
        process_id = agent_session["process_id"]
        claude_agent_record = self.claude_agents_by_process_id.get(process_id)
        if claude_agent_record is None:
            return False
        # No time is passed for iTerm2's value: it may have been set after the turn's end.
        hooks_report_the_turn_over = find_when_the_hooks_ended_the_turn(
            self.hook_state_by_agent_process_id.get(process_id), float("-inf")) is not None
        if not hooks_report_the_turn_over and seconds_working_has_held < SECONDS_BEFORE_CLAUDE_CODES_OWN_IDLE_COUNTS_OVER_WORKING:
            return False
        return read_claude_code_session_status(claude_agent_record.get("config_folder"), process_id) == "idle"

    def claude_code_reports_the_turn_going_on(self, agent_session):
        """Whether Claude Code's own report of the session (read_claude_code_session_status) says
        its turn goes on after the Stop its hooks reported: another Stop hook (Claude Code's goal
        feature) can block a Stop, and iTerm2's value then says working all along (code review,
        2026-10-28). That is "busy" or "waiting", or "shell" while none of its background tasks
        runs, so the command is the turn's own: while one runs after the turn ended, Claude Code
        says "shell" too. False when Claude Code reports nothing (another tool)."""
        process_id = agent_session["process_id"]
        claude_agent_record = self.claude_agents_by_process_id.get(process_id)
        if claude_agent_record is None:
            return False
        status = read_claude_code_session_status(claude_agent_record.get("config_folder"), process_id)
        background_task_ids = (self.hook_state_by_agent_process_id.get(process_id) or {}).get("background_task_ids")
        return status in ("busy", "waiting") or (status == "shell" and background_task_ids == set())

    def codex_conversation_may_be_another_sessions(self, session_id, agent_session):
        """Whether this Codex agent session shares its folder with another Codex agent session, or
        did while its process has run (remember_codex_sessions_that_share_a_folder). Codex's
        conversation files are matched to a process by folder and start time only
        (find_codex_conversation_files), so the earlier of two such sessions would take the later
        one's file for its own, also after the later one ended, since its file stays."""
        return isinstance(agent_session["adapter"], CodexAdapter) and (
            agent_session["process_id"] in self.codex_process_ids_that_shared_their_folder
            or any(other_session_id != session_id and isinstance(other_agent_session["adapter"], CodexAdapter)
                   and other_agent_session["working_folder"] == agent_session["working_folder"]
                   for other_session_id, other_agent_session in self.agent_sessions.items()))

    def remember_codex_sessions_that_share_a_folder(self):
        """Notes the process of each Codex agent session that shares its folder with another, for
        codex_conversation_may_be_another_sessions, and forgets the processes no longer listed."""
        codex_process_ids_by_folder = {}
        for agent_session in self.agent_sessions.values():
            if isinstance(agent_session["adapter"], CodexAdapter) and agent_session["process_id"]:
                codex_process_ids_by_folder.setdefault(agent_session["working_folder"], set()).add(agent_session["process_id"])
        self.codex_process_ids_that_shared_their_folder &= set().union(*codex_process_ids_by_folder.values())
        for process_ids in codex_process_ids_by_folder.values():
            if len(process_ids) >= 2:
                self.codex_process_ids_that_shared_their_folder |= process_ids

    def conversation_was_cleared_and_the_next_has_no_file_yet(self, session_id):
        """Whether /clear ended the agent session's conversation and the agent hasn't written its
        next one yet: a lookup by folder and start time would still find the ended one."""
        conversation_file = self.conversation_files_by_session_id.get(session_id) or {}
        return bool(conversation_file.get("cleared_path")) and conversation_file["path"] is None

    async def find_current_conversation_file(self, session_id, agent_session):
        """Returns the path of the agent session's conversation file: the one last found for its
        current conversation, else looked up now; or None."""
        conversation_file = self.conversation_files_by_session_id.get(session_id)
        if (conversation_file is not None and conversation_file["path"] is not None
                and conversation_file["key"] == self.find_conversation_key(agent_session)):
            return conversation_file["path"]
        return await self.look_up_conversation_file(session_id, agent_session)

    def forget_the_cleared_conversation(self, session_id, agent_session, cleared_path):
        """After /clear: the old conversation's size and preview no longer apply, and its finished
        turn no longer waits to be seen. Its file (cleared_path) is ignored until the agent writes
        a new one: Codex keeps its process and starts the new chat's file only with its first
        prompt, so looking the file up again finds the old one. Claude Code's new session ID is
        asked for at the gather that follows this action."""
        conversation_file = self.conversation_files_by_session_id.setdefault(session_id, build_conversation_file_record(
            self.find_conversation_key(agent_session), None, time.time(), None))
        conversation_file.update(path=None, modified_at=None, cleared_path=cleared_path or conversation_file["cleared_path"])
        self.forget_conversation_file_readings(session_id)
        if session_id in self.session_tracking_by_session_id:
            # The finished turn no longer waits to be seen; the tab itself wasn't seen, so its seen time stays.
            self.session_tracking_by_session_id[session_id]["finished_turn_cleared_at"] = time.time()
        self.process_ids_asked_about_in_claude_agents.discard(agent_session["process_id"])

    async def handle_end_agent_process(self, action_request):
        """Ends a tab's agent process ("Kill Session"; the tab and its shell stay): SIGTERM, then
        SIGCONT so an agent suspended with Ctrl+Z wakes up to handle it (as a shell's kill does
        for a stopped job), also for the stopped processes below a paused agent, and SIGKILL if it
        is still running SECONDS_AN_AGENT_GETS_TO_END_BEFORE_IT_IS_KILLED later."""
        agent_session = self.agent_sessions.get(action_request.target["session_id"])
        if agent_session is None or not agent_session["process_id"]:
            return ActionResult(False, "that tab no longer runs an agent")
        process_id = agent_session["process_id"]
        self.process_ids_ended_from_the_list.add(process_id)  # its row goes, rather than show "exited"
        # Listed first: once the agent ends, the processes below it are no longer its descendants.
        stopped_process_ids_below = [process.process_id for process in list_descendant_processes(process_id, maximum_depth=None)
                                     if process_is_stopped(process.process_id)]
        try:
            os.kill(process_id, signal.SIGTERM)
            os.kill(process_id, signal.SIGCONT)
        except ProcessLookupError:
            return ActionResult(True)  # it had ended already
        send_signal_to_processes(stopped_process_ids_below, signal.SIGCONT)  # a paused session's: none stays stopped
        deadline = time.time() + SECONDS_AN_AGENT_GETS_TO_END_BEFORE_IT_IS_KILLED
        while time.time() < deadline:
            await asyncio.sleep(0.2)
            if not process_is_alive(process_id):
                return ActionResult(True)
        try:
            os.kill(process_id, signal.SIGKILL)
        except ProcessLookupError:
            pass
        return ActionResult(True)

    async def handle_end_agent_processes(self, action_request):
        """end_agent_process for every session of target {"session_ids": [...]} (the menu of
        several selected rows; user request, 2026-10-21)."""
        return await self.run_for_each_agent_session(action_request, self.handle_end_agent_process)

    def child_process_is_listed(self, session_id, process_id):
        """Whether the session's tab row lists the process as one of its sub rows, so a sub row's
        Info and Kill can't reach any other process."""
        return any(child_process.process_id == process_id
                   for child_process in self.screen_readings_by_session_id.get(session_id, {}).get("child_processes", ()))

    async def handle_read_child_process_details(self, action_request):
        """Returns, in its details, what a sub row's Info shows about the process in arguments
        {"process_id"} of target {"session_id"} (user request, 2026-10-29): read_process_details's
        dict, with "cpu_percent", its and its descendants' CPU use over
        SECONDS_A_PROCESS_INFO_MEASURES_CPU_USE_OVER, rounded as the row's is."""
        process_id = action_request.arguments["process_id"]
        if not self.child_process_is_listed(action_request.target["session_id"], process_id):
            return ActionResult(False, "that process is no longer listed under its tab")
        first_resource_usage, first_read_at = read_process_tree_resource_usage(process_id), time.time()
        await asyncio.sleep(SECONDS_A_PROCESS_INFO_MEASURES_CPU_USE_OVER)
        details, read_at = read_process_details(process_id), time.time()
        if first_resource_usage is None or details is None:
            return ActionResult(False, "that process has ended")
        details["memory_bytes"], details["cpu_percent"] = round_memory_and_cpu_use(
            details["memory_bytes"], compute_cpu_percent((first_resource_usage[1], first_read_at), (details["cpu_seconds"], read_at)))
        return ActionResult(True, details=details)

    async def handle_end_child_process(self, action_request):
        """Ends the process in arguments {"process_id"}, a sub row of target {"session_id"}, and
        every process below it ("Kill" in a sub row's menu; user request, 2026-10-29): killing
        only a shell would leave the command in it running. SIGTERM, then SIGCONT for those
        stopped (a paused session's), and SIGKILL for any still running
        SECONDS_AN_AGENT_GETS_TO_END_BEFORE_IT_IS_KILLED later, as Kill Session does."""
        process_id = action_request.arguments["process_id"]
        if not self.child_process_is_listed(action_request.target["session_id"], process_id):
            return ActionResult(False, "that process is no longer listed under its tab")
        # Listed first: once it ends, the processes below it are no longer its descendants.
        process_ids = [process_id] + [process.process_id for process in list_descendant_processes(process_id, maximum_depth=None)]
        send_signal_to_processes(process_ids, signal.SIGTERM)
        send_signal_to_processes([listed_process_id for listed_process_id in process_ids if process_is_stopped(listed_process_id)],
                                 signal.SIGCONT)
        deadline = time.time() + SECONDS_AN_AGENT_GETS_TO_END_BEFORE_IT_IS_KILLED
        while time.time() < deadline:
            if not any(process_is_alive(listed_process_id) for listed_process_id in process_ids):
                return ActionResult(True)
            await asyncio.sleep(0.2)
        send_signal_to_processes(process_ids, signal.SIGKILL)
        return ActionResult(True)

    def list_detached_processes_of_the_agent_session(self, agent_session):
        """Returns the ProcessInfo of each detached process the agent session's conversation
        started, which its sub rows list (read_child_processes_periodically), so a pause stops
        them too (code review, 2026-10-29)."""
        conversation_id = (self.claude_agents_by_process_id.get(agent_session["process_id"]) or {}).get("sessionId")
        agent_started_at = agent_session.get("started_at")
        if not conversation_id or agent_started_at is None:
            return []
        return [process for process, _ in choose_detached_processes_of_the_conversation(
            list_detached_processes(agent_started_at), conversation_id, agent_started_at)]

    async def handle_pause_agent_sessions(self, action_request):
        """Pauses the agent sessions of target {"session_ids": [...]}: each agent as Ctrl+Z does,
        and every process below it (agent_session_pausing; user request, 2026-10-20)."""

        async def pause_one(session_id, agent_session, terminal_session):
            agent_process_id = agent_session["process_id"]
            # Ctrl+Z goes to the agent only while its group has the terminal (asked of the kernel)
            # and no other program, such as the editor Claude Code's Ctrl+G opens in the agent's
            # own group, is in front. iTerm2 still names the shell in front for a while after fg,
            # so a shell there doesn't count.
            if not process_is_stopped(agent_process_id) and (
                    not process_has_its_terminal(agent_process_id)
                    or (agent_session["is_in_background"]
                        and not command_line_runs_a_shell(self.find_command_line_of_the_session(session_id)))):
                return ActionResult(False, "another program is in front of the agent")
            if (session_id, agent_process_id) in self.pause_record_by_tab_and_agent:
                return ActionResult(True)  # paused already (a new record would lose the prompt seen then)
            action_result, pause_record = await pause_agent_session(
                terminal_session, agent_process_id, agent_session["agent_tool_name"],
                detached_processes=self.list_detached_processes_of_the_agent_session(agent_session))
            if pause_record is not None:
                self.pause_record_by_tab_and_agent[(session_id, agent_process_id)] = pause_record
                self.save_pause_records()
            return action_result
        return await self.pause_or_resume_each_agent_session(action_request.target["session_ids"], pause_one)

    async def handle_resume_agent_sessions(self, action_request):
        """Resumes the paused agent sessions of target {"session_ids": [...]}; one suspended with
        Ctrl+Z by hand gets an explanation instead, since no pause recorded its prompt."""

        async def resume_one(session_id, agent_session, terminal_session):
            tab_and_agent = (session_id, agent_session["process_id"])
            action_result = await resume_agent_session(terminal_session, agent_session["process_id"],
                                                       self.pause_record_by_tab_and_agent.get(tab_and_agent))
            if action_result.succeeded and self.pause_record_by_tab_and_agent.pop(tab_and_agent, None) is not None:
                self.save_pause_records()
            return action_result
        return await self.pause_or_resume_each_agent_session(action_request.target["session_ids"], resume_one)

    async def pause_or_resume_each_agent_session(self, session_ids, pause_or_resume_one):
        """Runs pause_or_resume_one(session_id, agent_session, terminal_session) for every session
        at once, each holding its tab's typing lock, as every typing into a tab does: its Ctrl+Z or
        fg can't come between a held message's paste and its Enter (code review, 2026-10-28).
        Returns one ActionResult whose message names each tab that failed and why."""

        async def run_for(session_id):
            agent_session = self.agent_sessions.get(session_id)
            terminal_session = self.find_terminal_session(session_id)
            if agent_session is None or not agent_session["process_id"] or terminal_session is None:
                return ActionResult(False, "that tab no longer runs an agent")
            if session_id in self.session_ids_being_paused_or_resumed:
                # A second click while the first is under way (both could see the same empty prompt and type fg twice).
                return ActionResult(True)
            self.session_ids_being_paused_or_resumed.add(session_id)
            try:
                async with self.message_typing_lock_by_session_id.setdefault(session_id, asyncio.Lock()):
                    return await pause_or_resume_one(session_id, agent_session, terminal_session)
            except TerminalTypingRefused as error:
                return ActionResult(False, str(error))
            except TerminalRequestFailed:
                return ActionResult(False, "the tab closed meanwhile")
            finally:
                self.session_ids_being_paused_or_resumed.discard(session_id)
        action_results = await asyncio.gather(*(run_for(session_id) for session_id in session_ids))
        return self.combine_results_naming_the_tabs_that_failed(session_ids, action_results)

    # ----- Talk to Each Other (user request, 2026-10-21) -----

    async def handle_ask_agent_sessions_to_talk_to_each_other(self, action_request):
        """Sends the agent sessions of target {"session_ids": [...]} that arguments
        {"recipient_session_ids": [...]} name (all of them when it is left out; one chosen in the
        Talk to Each Other window, user request, 2026-10-28) the note of arguments {"note": ...},
        followed by what each needs to send every other one of the sessions a message
        (messages_between_agent_sessions). A Codex session gets it through `codex queue`, which it
        runs after any turn under way. A Claude Code session has no supported way in from outside,
        so its message is held until the session waits at its prompt with an empty input box, then
        pasted there, giving up after SECONDS_A_MESSAGE_WAITS_FOR_AN_AGENT_AT_ITS_PROMPT. Returns
        once every message was delivered or given up on, naming each tab that didn't get one, and
        each session that couldn't take part."""
        session_ids = action_request.target["session_ids"]
        recipient_session_ids = action_request.arguments.get("recipient_session_ids") or session_ids
        agent_session_for_messages_by_session_id = {}
        action_result_by_session_id = {}
        for session_id in session_ids:
            agent_session_for_messages, reason = await self.describe_agent_session_for_messages(session_id)
            if agent_session_for_messages is None:
                action_result_by_session_id[session_id] = ActionResult(False, reason)
            else:
                agent_session_for_messages_by_session_id[session_id] = agent_session_for_messages
        if len(agent_session_for_messages_by_session_id) < 2:
            for session_id in agent_session_for_messages_by_session_id:
                action_result_by_session_id[session_id] = ActionResult(False, "no other selected session can take part")
        else:
            delivered_session_ids = [session_id for session_id in agent_session_for_messages_by_session_id
                                     if session_id in recipient_session_ids]
            agent_sessions_that_get_the_message = [agent_session_for_messages_by_session_id[session_id]
                                                   for session_id in delivered_session_ids]
            delivery_results = await asyncio.gather(*(
                self.deliver_message_to_agent_session(
                    session_id, agent_session_for_messages_by_session_id[session_id],
                    build_message_asking_agent_session_to_talk_to_the_others(
                        action_request.arguments["note"], agent_session_for_messages_by_session_id[session_id],
                        [other for other_session_id, other in agent_session_for_messages_by_session_id.items()
                         if other_session_id != session_id], agent_sessions_that_get_the_message))
                for session_id in delivered_session_ids), return_exceptions=True)
            for session_id, delivery_result in zip(delivered_session_ids, delivery_results):
                action_result_by_session_id[session_id] = convert_exception_to_action_result(delivery_result)
        # A session that only appears in the others' messages has a result only when it couldn't take part.
        reported_session_ids = [session_id for session_id in session_ids if session_id in action_result_by_session_id]
        return self.combine_results_naming_the_tabs_that_failed(
            reported_session_ids, [action_result_by_session_id[session_id] for session_id in reported_session_ids])

    async def describe_agent_session_for_messages(self, session_id):
        """Returns (AgentSessionForMessages, None) for an agent session that other sessions can
        send messages to, else (None, the reason it can't take part)."""
        agent_session = self.agent_sessions.get(session_id)
        if agent_session is None or not agent_session["process_id"]:
            return None, "that tab no longer runs an agent"
        process_id = agent_session["process_id"]
        how_other_agent_sessions_send_it_messages = agent_session["adapter"].how_other_agent_sessions_send_it_messages
        if how_other_agent_sessions_send_it_messages == MESSAGES_THROUGH_THE_SEND_MESSAGE_TOOL:
            address = (self.claude_agents_by_process_id.get(process_id) or {}).get("name")
            reason_without_address = "its Claude Code session name isn't known yet"
        elif how_other_agent_sessions_send_it_messages == MESSAGES_THROUGH_CODEX_QUEUE:
            if self.conversation_was_cleared_and_the_next_has_no_file_yet(session_id):
                return None, "its conversation was just cleared, and its next one starts at its next prompt"
            if self.codex_conversation_may_be_another_sessions(session_id, agent_session):
                return None, "another Codex session runs or ran in the same folder, so its conversation can't be told apart"
            address = await agent_session["adapter"].find_conversation_id(process_id, None)
            reason_without_address = ("its Codex conversation can't be identified: Codex starts it at the first prompt, "
                                      "and after /new more than one fits")
        else:
            return None, f"{agent_session['agent_tool_name']} sessions can't take messages from other sessions"
        if not address:
            return None, reason_without_address
        terminal_backend = self.find_terminal_backend_of(session_id=session_id)
        return AgentSessionForMessages(agent_session["agent_tool_name"], how_other_agent_sessions_send_it_messages, address,
                                       process_id, agent_session["working_folder"],
                                       self.find_tab_title_of_agent_session(session_id),
                                       terminal_backend.tab_kind_name if terminal_backend is not None else "tab"), None

    async def deliver_message_to_agent_session(self, session_id, agent_session_for_messages, message):
        """Queues the message for a Codex session, or pastes it into a Claude Code session once it waits at its prompt."""
        if agent_session_for_messages.how_other_agent_sessions_send_it_messages == MESSAGES_THROUGH_CODEX_QUEUE:
            return await queue_message_for_codex_session(self.codex_command_path, agent_session_for_messages.address, message)
        return await self.paste_message_once_the_agent_waits_at_its_prompt(session_id, agent_session_for_messages.process_id, message)

    async def paste_message_once_the_agent_waits_at_its_prompt(self, session_id, agent_process_id, message):
        """Checks every SECONDS_BETWEEN_CHECKS_FOR_AN_AGENT_AT_ITS_PROMPT until the agent session
        waits at its prompt (agent_waits_at_its_prompt) with an empty input box, then pastes the
        message there and presses Enter. Gives up after
        SECONDS_A_MESSAGE_WAITS_FOR_AN_AGENT_AT_ITS_PROMPT, or once the tab's agent process is no
        longer agent_process_id (keep_trying_until_the_agent_takes_it)."""
        async def paste_it_if_the_agent_waits_at_its_prompt(agent_session):
            if agent_session["is_in_background"] or not self.agent_waits_at_its_prompt(session_id, agent_session):
                return None
            terminal_session = self.find_terminal_session(session_id)
            if terminal_session is None:
                return ActionResult(False, "the tab closed meanwhile")
            return await agent_input_box_typing.paste_message_into_empty_input_box(terminal_session, message, agent_session["adapter"])
        return await self.keep_trying_until_the_agent_takes_it(
            session_id, agent_process_id, paste_it_if_the_agent_waits_at_its_prompt, "the message", "pasted")

    def set_reserved_screen_area(self, reserved_area):
        """Receives the docked sidebar's area, {"frame": (x, y, width, height), "edge": "right" or
        "left", "monitor_visible_frame": (...)}, or None when docking ends; kept until the windows
        with frames (iTerm2's) are gathered if it arrives earlier."""
        asyncio.ensure_future(self.change_reserved_screen_area(reserved_area))

    async def change_reserved_screen_area(self, new_reserved_area):
        """Gives the room back for the old area, then makes room for the new one, holding the
        lock so a regather can't resize windows in between. Before the first gather it only
        keeps the area, which make_room_for_reserved_area then applies: the saved frames of
        windows iTerm2 restored after a restart can be matched to them only once they are
        gathered, and giving room back earlier would drop those records (code review, 2026-10-28)."""
        async with self.window_room_lock:
            self.reserved_area_was_received = True
            if new_reserved_area == self.reserved_screen_area:
                if new_reserved_area is None and self.windows_were_gathered and self.frames_before_making_room_by_window_id:
                    await self.give_room_back_while_holding_the_lock()  # restores still pending from before
                return
            if self.windows_were_gathered:
                await self.give_room_back_while_holding_the_lock()  # both handle each window's errors themselves
            self.reserved_screen_area = new_reserved_area
            if self.windows_were_gathered:
                await self.make_room_while_holding_the_lock()

    async def make_room_for_reserved_area(self):
        """After each gather: while docked, narrows (or moves) iTerm2 windows that reach into the
        reserved area; while not, retries restores still pending (after a restart with docking
        off, or a window that was full screen when docking ended). Pending restores wait until
        the main thread has said whether the sidebar is docked, so a docked start doesn't widen
        windows only to narrow them again, and until the windows with frames (iTerm2's) were
        gathered: a gather before iTerm2 is connected (tmux's) would take its windows for closed
        and drop their records."""
        async with self.window_room_lock:
            if self.reserved_screen_area is not None:
                await self.make_room_while_holding_the_lock()
            elif self.windows_were_gathered and self.reserved_area_was_received and self.frames_before_making_room_by_window_id:
                await self.give_room_back_while_holding_the_lock()

    async def make_room_while_holding_the_lock(self):
        """Narrows or moves each window that reaches into the reserved area, remembering its frame
        so it can get the room back. Full-screen windows are left alone (they have their own desktop).

        Frames are read fresh, since with one monitor nothing polls them before docking starts.
        A window that was moved or resized by hand since it was last made room for gets that
        frame as the one to restore. A window that closes meanwhile is skipped.
        """
        if self.reserved_screen_area is None:
            return
        self.adopt_records_of_restored_windows()
        for gathered_window in self.list_gathered_windows_with_frames():
            terminal_backend = self.terminal_backend_by_name[gathered_window["terminal_name"]]
            window_id = gathered_window["window_id"]
            try:
                current_frame = await terminal_backend.read_window_frame(window_id)
                gathered_window["frame"] = current_frame
                new_frame = compute_frame_clear_of_area(
                    current_frame, self.reserved_screen_area["frame"], self.reserved_screen_area["edge"],
                    self.reserved_screen_area["monitor_visible_frame"], SMALLEST_WINDOW_WIDTH)
                if new_frame is None or await terminal_backend.window_is_full_screen(window_id):
                    continue
                original_frame, frame_that_was_set = self.frames_before_making_room_by_window_id.get(
                    window_id, (current_frame, None))
                if frame_that_was_set is not None and not frames_are_within_tolerance(
                        current_frame, frame_that_was_set, FRAME_TOLERANCE_POINTS):
                    original_frame = current_frame  # moved or resized by hand since
                await terminal_backend.set_window_frame(window_id, new_frame)
                self.frames_before_making_room_by_window_id[window_id] = (original_frame, new_frame)
                gathered_window["frame"] = new_frame
            except TerminalRequestFailed as error:
                print(f"could not make room for the docked sidebar in window {window_id}: {error}")
        self.save_docked_window_frames()

    async def give_room_back_while_holding_the_lock(self):
        """Restores the windows narrowed for a docked sidebar, unless the user moved or resized
        them since, and updates the cached frames so the next layout starts from the real ones.

        A window that is full screen, or whose restore fails, stays remembered, so it can still
        be restored when a later docking ends. Records of windows that are gone are dropped.
        """
        self.adopt_records_of_restored_windows()
        gathered_windows_by_window_id = {gathered_window["window_id"]: gathered_window
                                         for gathered_window in self.list_gathered_windows_with_frames()}
        for window_id, (original_frame, frame_that_was_set) in list(self.frames_before_making_room_by_window_id.items()):
            if window_id not in gathered_windows_by_window_id:
                del self.frames_before_making_room_by_window_id[window_id]
                continue
            terminal_backend = self.terminal_backend_by_name[gathered_windows_by_window_id[window_id]["terminal_name"]]
            try:
                if await terminal_backend.window_is_full_screen(window_id):
                    continue  # its own desktop shows a full-screen frame; it returns to the narrowed one later
                current_frame = await terminal_backend.read_window_frame(window_id)
                if frames_are_within_tolerance(current_frame, frame_that_was_set, FRAME_TOLERANCE_POINTS):
                    await terminal_backend.set_window_frame(window_id, original_frame)
                    current_frame = original_frame
            except TerminalRequestFailed as error:
                print(f"could not give window {window_id} its room back: {error}")
                continue
            del self.frames_before_making_room_by_window_id[window_id]
            gathered_windows_by_window_id[window_id]["frame"] = current_frame
        self.save_docked_window_frames()

    def adopt_records_of_restored_windows(self):
        """On the first pass after start-up, moves frames remembered from the file for windows
        that are gone to windows iTerm2 restored after a restart, which get new IDs but keep the
        narrowed frame. Later, the records of windows that closed are dropped instead, so a new
        window of the same size never takes over a closed window's record."""
        frame_by_window_id = {gathered_window["window_id"]: gathered_window["frame"]
                              for gathered_window in self.list_gathered_windows_with_frames()}
        if not frame_by_window_id:
            return
        if self.records_loaded_from_file_await_adoption:
            self.records_loaded_from_file_await_adoption = False
            self.frames_before_making_room_by_window_id = adopt_records_of_restored_windows(
                self.frames_before_making_room_by_window_id, frame_by_window_id, FRAME_TOLERANCE_POINTS)
        else:
            self.frames_before_making_room_by_window_id = {
                window_id: frames for window_id, frames in self.frames_before_making_room_by_window_id.items()
                if window_id in frame_by_window_id}

    def save_docked_window_frames(self):
        """Writes the remembered frames to their file when they changed since the last write."""
        if not self.docked_window_frames_file_path or self.frames_before_making_room_by_window_id == self.saved_docked_window_frames:
            return
        try:
            save_docked_window_frames(self.docked_window_frames_file_path, self.frames_before_making_room_by_window_id)
            self.saved_docked_window_frames = dict(self.frames_before_making_room_by_window_id)
        except OSError as error:
            print(f"could not save the docked windows' frames: {error!r}")

    async def handle_peek_agent_session(self, action_request):
        """Press and hold on a tab row: shows the tab, first remembering, in every terminal, what to put back."""
        async with self.peek_lock:
            self.what_was_in_front_before_peek_by_terminal_name = {
                terminal_backend.terminal_name: terminal_backend.remember_what_is_in_front()
                for terminal_backend in self.terminal_backends if terminal_backend.is_connected()}
            return await self.handle_focus_agent_session(action_request)

    async def handle_end_peek(self, action_request):
        """The button was released: arguments {"bring_back_iterm2_window": bool}, true when iTerm2
        was in front before the peek (otherwise the main thread brings back the app that was).
        Every terminal puts back what it showed before."""
        async with self.peek_lock:
            what_was_in_front_by_terminal_name, self.what_was_in_front_before_peek_by_terminal_name = (
                self.what_was_in_front_before_peek_by_terminal_name, None)
            if what_was_in_front_by_terminal_name is None:
                return ActionResult(False, "no peek to end")
            action_results = [await self.terminal_backend_by_name[terminal_name].put_back_what_was_in_front(
                                  what_was_in_front, action_request.arguments["bring_back_iterm2_window"])
                              for terminal_name, what_was_in_front in what_was_in_front_by_terminal_name.items()]
            return next((action_result for action_result in action_results if not action_result.succeeded), ActionResult(True))

    async def handle_duplicate_agent_session(self, action_request):
        """Target {"tab_id": ...}; arguments {"folder": ..., "launch_command": ...}. The copy, in a
        tab right after this one (like Chrome's Duplicate), continues a copy of the conversation
        when the agent tool can fork it, else it starts fresh."""
        tab_id = action_request.target["tab_id"]
        session_id = self.find_first_session_id_of_tab(tab_id)
        agent_session = self.agent_sessions.get(session_id)
        terminal_backend, gathered_window = self.find_terminal_backend_and_gathered_window(tab_id=tab_id)
        if agent_session is None or terminal_backend is None:
            return ActionResult(False, "that tab no longer runs an agent session")
        process_id = agent_session["process_id"]
        claude_agent_record = self.claude_agents_by_process_id.get(process_id)
        conversation_is_shared_with_another_codex_session = self.codex_conversation_may_be_another_sessions(
            session_id, agent_session)
        if self.conversation_was_cleared_and_the_next_has_no_file_yet(session_id) or conversation_is_shared_with_another_codex_session:
            conversation_id = None  # start fresh rather than fork a conversation that may not be this session's
        else:
            conversation_id = await agent_session["adapter"].find_conversation_id(process_id, claude_agent_record)
        launch_command_for_the_copy = agent_session["adapter"].build_launch_command_for_a_copy(
            action_request.arguments["launch_command"], conversation_id, claude_agent_record)
        # Without the conversation's ID, the copy starts a new conversation, silently: nothing showed
        # the notices a successful result carried (user's choice, 2026-10-30).
        folder = os.path.expanduser(action_request.arguments["folder"])
        reason_it_cant_start = explain_why_an_agent_session_cant_start(folder, launch_command_for_the_copy)
        if reason_it_cant_start:
            return ActionResult(False, reason_it_cant_start)
        return await terminal_backend.start_agent_session(gathered_window["window_id"], folder, launch_command_for_the_copy,
                                                          after_tab_id=tab_id)

    async def handle_close_window(self, action_request):
        terminal_backend = self.find_terminal_backend_of(window_id=action_request.target["window_id"])
        if terminal_backend is None:
            return ActionResult(False, "that window no longer exists")
        return await terminal_backend.close_window(action_request.target["window_id"])

    async def handle_move_window_to_monitor(self, action_request):
        terminal_backend = self.find_terminal_backend_of(window_id=action_request.target["window_id"])
        if terminal_backend is None:
            return ActionResult(False, "that window no longer exists")
        return await terminal_backend.move_window_to_monitor(action_request.target["window_id"], self.monitors,
                                                             action_request.arguments["monitor_id"])

    async def handle_reorder_window_list(self, action_request):
        self.window_order = move_window_in_window_list_order(
            self.window_order, action_request.target["window_id"], action_request.arguments.get("drop_before_window_id"))
        return ActionResult(True)

    # ----- messages from the main thread -----

    def set_monitors(self, monitors):
        """Receives the monitor rectangles from the main thread (menu-bar monitor first)."""
        self.monitors = list(monitors)
        self.snapshot_needed.set()

    def set_core_settings(self, core_settings):
        """Receives the settings the core uses (settings_store.build_core_settings): the agent
        tools the Harnesses section leaves out of the list, whether memory and CPU use are read,
        and the codex program for `codex queue`."""
        self.left_out_agent_tool_names = frozenset(core_settings["left_out_agent_tool_names"])
        if core_settings["resource_usage_is_read"] and not self.resource_usage_is_read:
            self.resource_usage_reading_needed.set()  # read now rather than at the end of the current wait
        self.resource_usage_is_read = core_settings["resource_usage_is_read"]
        self.codex_command_path = core_settings["codex_command_path"]
        self.snapshot_needed.set()

    def set_dashboard_is_active(self, dashboard_is_active):
        """Receives whether the dashboard itself is the active app (then nothing counts as seen)."""
        self.dashboard_is_active = dashboard_is_active
        self.record_seen_time_for_the_seen_session()
        self.snapshot_needed.set()

    def set_last_other_active_application_process_id(self, process_id):
        """Receives the process ID of the last app other than the dashboard to be active."""
        self.last_other_active_application_process_id = process_id
        self.record_seen_time_for_the_seen_session()
        self.snapshot_needed.set()

    # ----- gathering -----

    async def gather_every_terminals_windows(self):
        """Gathers the windows of every terminal that runs, in the order of terminal_backends.
        A terminal whose gather fails keeps its windows from before, and another gather follows
        RETRY_DELAY_SECONDS later; one that doesn't run has none."""
        gathered_windows = []
        for terminal_backend in self.terminal_backends:
            terminal_name = terminal_backend.terminal_name
            if not terminal_backend.is_connected():
                self.gathered_windows_by_terminal_name[terminal_name] = []
                continue
            try:
                self.gathered_windows_by_terminal_name[terminal_name] = await terminal_backend.gather_windows()
                if terminal_backend.windows_have_frames:
                    self.windows_were_gathered = True
            except TerminalRequestFailed as error:
                # For example iTerm2's app list changed under the refresh, or the tmux server ended.
                print(f"{terminal_name}'s windows couldn't be read, trying again: {error}", file=sys.stderr)
                asyncio.get_running_loop().call_later(RETRY_DELAY_SECONDS, self.regather_needed.set)
            gathered_windows += self.gathered_windows_by_terminal_name.get(terminal_name, [])
        return gathered_windows

    def update_session_tracking(self, now):
        """Records when each session's status value changed and when its turns start and end, and
        forgets sessions that are gone. A turn end the tab agent's hooks report counts while
        iTerm2's value still says working (find_when_the_hooks_ended_the_turn), unless Claude
        Code's own report says the turn goes on (claude_code_reports_the_turn_going_on), and so
        does Claude Code's own idle (claude_code_reports_the_turn_over), which ends no turn
        unless the hooks reported one. In a terminal that reports no agent status (tmux), the
        agent's own report stands in for the status (read_own_status_value_of_the_tabs_agent)."""
        live_session_ids = set()
        for gathered_window in self.gathered_windows:
            terminal_reports_agent_status = self.terminal_backend_by_name[gathered_window["terminal_name"]].reports_agent_status
            for gathered_tab in gathered_window["tabs"]:
                first_session = gathered_tab["first_session"]
                if first_session is None:
                    continue
                session_id = first_session["session_id"]
                live_session_ids.add(session_id)
                previous_tracking = self.session_tracking_by_session_id.get(session_id)
                agent_session = self.agent_sessions.get(session_id)
                if (previous_tracking is not None and not first_session["status_set_in_iterm2"] and agent_session is not None
                        and agent_session["process_id"] and (process_is_stopped(agent_session["process_id"])
                                                             or first_session.get("job_pid") != agent_session["process_id"])):
                    # Paused (or suspended with Ctrl+Z), or resumed while iTerm2 still sees the shell in
                    # front: the shell has no status, and the agent's turn goes on where it was once it is
                    # back in front, with the commands it started counted.
                    continue
                status_set_in_iterm2_and_since = self.status_set_in_iterm2_and_since_by_session_id.get(session_id)
                status_set_in_iterm2_changed = (status_set_in_iterm2_and_since is None
                                                or status_set_in_iterm2_and_since[0] != first_session["status_set_in_iterm2"])
                if status_set_in_iterm2_changed:
                    status_set_in_iterm2_and_since = (first_session["status_set_in_iterm2"], now,
                                                      now if status_set_in_iterm2_and_since is not None else float("-inf"))
                    self.status_set_in_iterm2_and_since_by_session_id[session_id] = status_set_in_iterm2_and_since
                first_session["status"] = (self.choose_status_value_of_the_tabs_own_agent(
                    session_id, first_session, previous_tracking, status_set_in_iterm2_changed) if terminal_reports_agent_status
                    else self.read_own_status_value_of_the_tabs_agent(session_id))
                ends_no_turn = False
                if agent_session is not None and map_iterm2_status_value_to_state(first_session["status"]) == "working":
                    hook_state = self.hook_state_by_agent_process_id.get(agent_session["process_id"])
                    if (find_when_the_hooks_ended_the_turn(hook_state, status_set_in_iterm2_and_since[2]) is not None
                            and not self.claude_code_reports_the_turn_going_on(agent_session)):
                        first_session["status"] = "idle"  # its turn ended, though iTerm2 still says working while a background task runs
                    elif self.claude_code_reports_the_turn_over(agent_session, now - max(
                            status_set_in_iterm2_and_since[1], (hook_state or {}).get("last_prompt_at") or float("-inf"))):
                        first_session["status"] = "idle"
                        # Without a turn end from the hooks, the working it replaces may be one iTerm2 set with
                        # no turn at all (as the app restarted, say), so it ends no turn (code review, 2026-10-28).
                        ends_no_turn = find_when_the_hooks_ended_the_turn(hook_state, float("-inf")) is None
                tracking = track_status_value(previous_tracking, first_session["status"], now,
                                              self.screen_readings_by_session_id.get(session_id), ends_no_turn)
                self.session_tracking_by_session_id[session_id] = tracking
                if (previous_tracking is not None and tracking is not previous_tracking
                        and map_iterm2_status_value_to_state(first_session["status"]) == "asking"):
                    self.screen_check_needed.set()
        for session_id in set(self.session_tracking_by_session_id) - live_session_ids:
            del self.session_tracking_by_session_id[session_id]
        for session_id in set(self.iterm2_status_seen_while_an_inner_agent_ran_by_session_id) - live_session_ids:
            del self.iterm2_status_seen_while_an_inner_agent_ran_by_session_id[session_id]
        for session_id in set(self.status_set_in_iterm2_and_since_by_session_id) - live_session_ids:
            del self.status_set_in_iterm2_and_since_by_session_id[session_id]
        for session_id in set(self.own_status_value_by_session_id) - live_session_ids:
            del self.own_status_value_by_session_id[session_id]

    def read_own_status_value_of_the_tabs_agent(self, session_id):
        """Returns the status value of a tab in a terminal that reports no agent status (tmux),
        from its agent's own report (read_status_value_of_the_tabs_own_agent), or None for a tab
        without an agent; remembered for watch_the_own_status_of_agents_in_terminals_without_status_reports."""
        agent_session = self.agent_sessions.get(session_id)
        status_value = (self.read_status_value_of_the_tabs_own_agent(session_id, agent_session)
                        if agent_session is not None and agent_session["process_id"] else None)
        self.own_status_value_by_session_id[session_id] = status_value
        return status_value

    def choose_status_value_of_the_tabs_own_agent(self, session_id, first_session, previous_tracking, status_set_in_iterm2_changed):
        """Returns the status value a tab's row goes by: the one set in iTerm2, except while an agent
        program runs inside the tab's agent (a `claude -p` its shell tool started, waited on or left
        running in the background), and after it ends until iTerm2's value fits the tab's agent
        again. That agent fires the same user-level hooks, so it sets the tab's status too, and its
        last value stays until the tab's agent sets another (Phase 0).
        Meanwhile the row goes by the tab's own agent (read_status_value_of_the_tabs_own_agent)
        and turns back to iTerm2's value once the two agree; for a tool without such a report, it
        keeps its value until iTerm2's changes after the inner agent ended. The agent's processes
        are listed only when the value set in iTerm2 changed since the last check
        (status_set_in_iterm2_changed: not the value the row went by, which a turn end its hooks
        report can make idle), and while the row goes by its own agent."""
        status_set_in_iterm2 = first_session["status_set_in_iterm2"]
        agent_session = self.agent_sessions.get(session_id)
        status_seen_while_an_inner_agent_ran = self.iterm2_status_seen_while_an_inner_agent_ran_by_session_id
        # Checked also when the dashboard first sees the tab (no previous_tracking): after a restart
        # while an inner agent ran, iTerm2's value stayed the same and the check never came
        # (code review, 2026-10-29).
        if agent_session is None or not agent_session["process_id"]:
            status_seen_while_an_inner_agent_ran.pop(session_id, None)
            return status_set_in_iterm2
        goes_by_its_own_agent = session_id in status_seen_while_an_inner_agent_ran
        if not goes_by_its_own_agent and not status_set_in_iterm2_changed:
            return status_set_in_iterm2
        an_inner_agent_runs = an_agent_program_runs_through_a_shell_below(
            list_descendant_processes(agent_session["process_id"]), read_name_the_process_was_started_as)
        if not goes_by_its_own_agent:
            if not an_inner_agent_runs:
                return status_set_in_iterm2
            self.screen_readings_by_session_id.setdefault(session_id, {}).pop("turn_shows_on_screen", None)  # an earlier one's
            self.screen_check_needed.set()
        own_status_value = self.read_status_value_of_the_tabs_own_agent(session_id, agent_session)
        if an_inner_agent_runs:
            status_seen_while_an_inner_agent_ran[session_id] = status_set_in_iterm2
        elif (status_values_put_the_agent_in_the_same_state(own_status_value, status_set_in_iterm2) if own_status_value
              else status_set_in_iterm2 != status_seen_while_an_inner_agent_ran[session_id]):
            del status_seen_while_an_inner_agent_ran[session_id]
            return status_set_in_iterm2
        return own_status_value or (previous_tracking["status_value"] if previous_tracking is not None else status_set_in_iterm2)

    def read_status_value_of_the_tabs_own_agent(self, session_id, agent_session):
        """Returns the state of the tab's own agent as an iTerm2 status value, from what no agent
        inside it writes, or None when its tool reports nothing of the kind: for Claude Code its own
        report of its session (read_claude_code_session_status); for a tool whose screen shows a
        turn under way (Codex's "(5s • esc to interrupt)"), the screen check made while the row
        goes by its own agent ("turn_shows_on_screen")."""
        process_id = agent_session["process_id"]
        if isinstance(agent_session["adapter"], ClaudeCodeAdapter):
            return CLAUDE_CODE_SESSION_STATUSES_TO_ITERM2_STATUS_VALUES.get(read_claude_code_session_status(
                (self.claude_agents_by_process_id.get(process_id) or {}).get("config_folder"), process_id))
        turn_shows_on_screen = self.screen_readings_by_session_id.get(session_id, {}).get("turn_shows_on_screen")
        if turn_shows_on_screen is None:
            return None
        return "working" if turn_shows_on_screen else "idle"

    def find_command_line_of_the_session(self, session_id):
        """Returns the gathered commandLine of a tab's first-pane session (its foreground job), or ""."""
        for gathered_window in self.gathered_windows:
            for gathered_tab in gathered_window["tabs"]:
                first_session = gathered_tab["first_session"]
                if first_session and first_session["session_id"] == session_id:
                    return first_session.get("command_line") or ""
        return ""

    def find_first_session_id_of_tab(self, tab_id):
        """Returns the first-pane session ID of a gathered tab, or None."""
        for gathered_window in self.gathered_windows:
            for gathered_tab in gathered_window["tabs"]:
                if gathered_tab["tab_id"] == tab_id and gathered_tab["first_session"]:
                    return gathered_tab["first_session"]["session_id"]
        return None

    def find_session_id_in_front(self, the_terminal_must_have_keyboard_focus, a_client_inside_another_terminal_app_counts):
        """Returns the session of the tab in front, or None.

        That is the selected tab of the front window of a terminal with windows of its own
        (iTerm2), once that terminal has keyboard focus when the_terminal_must_have_keyboard_focus,
        unless a client of a terminal that shows its windows through clients (tmux) is in front:
        when a_client_inside_another_terminal_app_counts, one running inside the last app other
        than the dashboard to be active, when that is another terminal app (such as Terminal);
        else the client running in that iTerm2 tab (`tmux attach` there). Such a client's tab is
        the one in front then, the most recently used client's when several qualify. A client
        inside another terminal app is found by the app alone, though it may run in one of its
        tabs that isn't shown, so it is good enough for the highlight but not for "seen".
        """
        tab_id_in_front = None
        terminal_application_process_ids = set()  # those of the terminals with windows of their own
        for terminal_backend in self.terminal_backends:
            if terminal_backend.shows_windows_through_clients or not terminal_backend.is_connected():
                continue
            terminal_application_process_ids |= set(terminal_backend.list_application_process_ids())
            if tab_id_in_front is None and (not the_terminal_must_have_keyboard_focus
                                            or terminal_backend.a_terminal_window_has_keyboard_focus()):
                tab_id_in_front = terminal_backend.find_front_tab_id()
        first_session_in_front = next((gathered_tab["first_session"] for gathered_window in self.gathered_windows
                                       for gathered_tab in gathered_window["tabs"] if gathered_tab["tab_id"] == tab_id_in_front), None)
        job_process_id_in_front = first_session_in_front["job_pid"] if first_session_in_front is not None else None
        terminal_clients = [terminal_client for terminal_backend in self.terminal_backends
                            if terminal_backend.shows_windows_through_clients and terminal_backend.is_connected()
                            for terminal_client in terminal_backend.list_clients()]
        # First a client inside the last other app, when that is another terminal app: while the
        # dashboard is active, iTerm2's front tab may be one the user didn't come from. iTerm2's
        # own clients are matched through its tab in front instead.
        other_application_process_id = self.last_other_active_application_process_id
        terminal_client_in_front = None
        if (a_client_inside_another_terminal_app_counts and other_application_process_id is not None
                and other_application_process_id not in terminal_application_process_ids):
            terminal_client_in_front = next((terminal_client for terminal_client in terminal_clients if other_application_process_id
                                             in list_the_process_and_its_ancestor_ids(terminal_client.process_id)), None)
        if terminal_client_in_front is None and job_process_id_in_front is not None:
            terminal_client_in_front = next((terminal_client for terminal_client in terminal_clients
                                             if terminal_client.process_id == job_process_id_in_front), None)
        if terminal_client_in_front is not None:
            return self.find_first_session_id_of_tab(terminal_client_in_front.front_tab_id)
        return first_session_in_front["session_id"] if first_session_in_front is not None else None

    def find_seen_session_id(self):
        """Returns the session being seen: the one in front (find_session_id_in_front) while its
        terminal, not the dashboard, is active, and for iTerm2, while a terminal window (not, for
        example, iTerm2's Settings) has keyboard focus. A tmux client inside another terminal app
        doesn't count (that app's tab in front may be another); showing its tab from the list does
        (handle_focus_agent_session)."""
        if self.dashboard_is_active:
            return None
        return self.find_session_id_in_front(the_terminal_must_have_keyboard_focus=True,
                                             a_client_inside_another_terminal_app_counts=False)

    def find_highlighted_session_id(self):
        """Returns the session shown as in front: the one in front while its terminal is active,
        and also while the dashboard itself is active (so the highlight doesn't vanish while you
        use the list); None while another app is active."""
        return self.find_session_id_in_front(the_terminal_must_have_keyboard_focus=not self.dashboard_is_active,
                                             a_client_inside_another_terminal_app_counts=True)

    def record_seen_time_for_the_seen_session(self):
        """Stamps "seen until now" on the session that was seen at the last check and on the one
        seen now. Called with each snapshot and when the dashboard's activity changes, so a tab
        that stops being seen keeps the time it was last seen, whenever the change is noticed."""
        now = time.time()
        seen_session_id = self.find_seen_session_id()
        for session_id in (self.last_seen_session_id, seen_session_id):
            if session_id in self.session_tracking_by_session_id:
                self.session_tracking_by_session_id[session_id]["last_time_tab_was_seen"] = now
        self.last_seen_session_id = seen_session_id

    async def regather_and_publish(self):
        """Re-reads the terminals and publishes a snapshot if anything changed."""
        self.gathered_windows = await self.gather_every_terminals_windows()
        await self.make_room_for_reserved_area()  # a new or moved window may reach under a docked sidebar
        self.publish_snapshot_if_changed()
        process_ids_not_asked_about = self.list_claude_code_process_ids() - self.process_ids_asked_about_in_claude_agents
        if self.claude_command_path and process_ids_not_asked_about and not self.claude_agents_refresh_is_running:
            self.process_ids_asked_about_in_claude_agents |= process_ids_not_asked_about
            # A separate task, so snapshots keep flowing while the command runs (it can take seconds).
            asyncio.ensure_future(self.refresh_claude_agents())

    def list_claude_code_process_ids(self):
        """Returns the agent process IDs of the tabs that run Claude Code (also those in the background)."""
        return {agent_session["process_id"] for agent_session in self.agent_sessions.values()
                if isinstance(agent_session["adapter"], ClaudeCodeAdapter) and agent_session["process_id"]}

    async def refresh_claude_agents(self):
        """Re-reads `claude agents --json` (names and folders), then asks for a snapshot.

        A Claude Code process registers its session a second or so after it starts, so a process
        missing from the output is asked about again by a gather CLAUDE_AGENTS_RETRY_DELAY_SECONDS
        later, up to CLAUDE_AGENTS_RETRIES_FOR_A_NEW_PROCESS times.
        """
        self.claude_agents_refresh_is_running = True
        try:
            claude_agents_read_now = await read_claude_agents_by_process_id(self.claude_command_path, list_claude_config_folders())
        finally:
            self.claude_agents_refresh_is_running = False
        # A run that failed or timed out for a config folder leaves its agents out; their last
        # records stay while their processes run, or their conversation would look new.
        claude_code_process_ids = self.list_claude_code_process_ids()
        self.claude_agents_by_process_id = {
            **{process_id: record for process_id, record in self.claude_agents_by_process_id.items()
               if process_id in claude_code_process_ids},
            **claude_agents_read_now}
        if claude_code_process_ids - self.process_ids_asked_about_in_claude_agents:
            # Asked about again while this run was under way (a /clear typed then, note_hook_event),
            # so this run may have read the record from before it: the next gather runs again at
            # once (code review, 2026-10-28).
            self.regather_needed.set()
        retry_is_due = False
        for process_id in claude_code_process_ids - set(self.claude_agents_by_process_id):
            self.claude_agents_retry_count_by_process_id[process_id] = self.claude_agents_retry_count_by_process_id.get(process_id, 0) + 1
            if self.claude_agents_retry_count_by_process_id[process_id] <= CLAUDE_AGENTS_RETRIES_FOR_A_NEW_PROCESS:
                self.process_ids_asked_about_in_claude_agents.discard(process_id)
                retry_is_due = True
        if retry_is_due:  # the next gather asks again
            asyncio.get_running_loop().call_later(CLAUDE_AGENTS_RETRY_DELAY_SECONDS, self.regather_needed.set)
        self.snapshot_needed.set()

    def continue_the_processes_of_agents_no_longer_paused(self):
        """Continues the processes a pause stopped below an agent that runs again without Resume
        (fg typed by hand) or is gone (its tab closed, also from iTerm2), so none stays stopped,
        and marks the agent sessions whose pause stopped the processes below them."""
        ended_tabs_and_agents = [tab_and_agent for tab_and_agent, pause_record in self.pause_record_by_tab_and_agent.items()
                                 if pause_has_ended(pause_record)]
        for tab_and_agent in ended_tabs_and_agents:
            continue_processes_stopped_at_pause(self.pause_record_by_tab_and_agent.pop(tab_and_agent).stopped_processes)
        if ended_tabs_and_agents:
            self.save_pause_records()
        for session_id, agent_session in self.agent_sessions.items():
            agent_session["is_paused_with_the_processes_below_it"] = (
                (session_id, agent_session["process_id"]) in self.pause_record_by_tab_and_agent)

    def save_pause_records(self):
        """Writes the pause records to their file. A write that fails (a full disk) is reported,
        not raised: it would end the core, which then restarts and fails the same way (code review, 2026-10-28)."""
        if not self.pause_records_file_path:
            return
        try:
            save_pause_records(self.pause_records_file_path, self.pause_record_by_tab_and_agent)
        except OSError as error:
            print(f"could not save the pause records: {error!r}")

    def save_hook_states(self):
        """Writes the hook states of the agent processes that still run to their file, each with its
        process's start time. A write that fails is reported, not raised, as for the pause records."""
        if not self.hook_states_file_path:
            return
        started_at_and_hook_state_by_process_id = {}
        for process_id, hook_state in self.hook_state_by_agent_process_id.items():
            process = read_process_info(process_id)
            if process is not None:
                started_at_and_hook_state_by_process_id[process_id] = (process.started_at, hook_state)
        try:
            save_hook_states(self.hook_states_file_path, started_at_and_hook_state_by_process_id)
        except OSError as error:
            print(f"could not save the hook states: {error!r}")

    def list_again_the_paused_agents_that_lost_their_row(self):
        """Adds resolve_agent_sessions entries for the agents a pause stopped that are still
        stopped but no longer listed, in gathered tabs with no other agent: after the dashboard
        restarted (their tabs show the shell in front, so they are found only through their
        records), or once a newer agent started at the paused tab's shell has ended
        (resolve_agent_sessions follows only a tab's latest agent). Their entries are then kept,
        in the background, while their process lives. A tab whose agents were paused one after
        another lists the last one paused."""
        gathered_session_ids = {gathered_tab["first_session"]["session_id"] for gathered_window in self.gathered_windows
                                for gathered_tab in gathered_window["tabs"] if gathered_tab["first_session"]}
        for (session_id, agent_process_id), pause_record in sorted(self.pause_record_by_tab_and_agent.items(),
                                                                   key=lambda item: item[1].agent_process[1], reverse=True):
            if (session_id in self.agent_sessions or session_id not in gathered_session_ids
                    or pause_has_ended(pause_record)):  # continue_the_processes_of_agents_no_longer_paused continues its processes
                continue
            # By the tool's name the pause recorded: a wrapped agent ("node .../codex") can't be
            # recognized from its process's name alone.
            adapter = next((adapter for adapter in self.specific_adapters
                            if adapter.agent_tool_name == pause_record.agent_tool_name), self.generic_adapter)
            self.agent_sessions[session_id] = {
                "adapter": adapter, "process_id": agent_process_id, "is_in_background": True, "is_stopped": True,
                "agent_tool_name": pause_record.agent_tool_name,
                "working_folder": read_process_current_folder(agent_process_id), "started_at": pause_record.agent_process[1]}

    def note_agents_that_ended_without_a_session_end(self, agent_sessions_before, now):
        """Keeps an "exited" row for each tab, still open, whose agent process is gone
        since the last snapshot without a normal session end, though its hooks reported (so one
        would have come): it crashed or was killed. An agent whose hooks never reported (started
        before they were installed, or since the app started without an event yet) can't be told
        apart from one that ended normally, so its row just goes, as does that of an agent ended
        from the list ("Kill Session"). The rows go after EXITED_ROW_SECONDS, when their tab
        closes or runs a new agent, or when a late session end arrives (note_hook_event).

        Args:
            agent_sessions_before: resolve_agent_sessions's result in the last snapshot.
            now: The current time.
        """
        gathered_session_ids = {gathered_tab["first_session"]["session_id"] for gathered_window in self.gathered_windows
                                for gathered_tab in gathered_window["tabs"] if gathered_tab["first_session"]}
        for session_id, agent_session in agent_sessions_before.items():
            process_id = agent_session["process_id"]
            hook_state = self.hook_state_by_agent_process_id.get(process_id)
            if (session_id in self.agent_sessions or session_id not in gathered_session_ids or not process_id
                    or process_is_alive(process_id) or hook_state is None or hook_state["session_ended_at"] is not None
                    or process_id in self.process_ids_ended_from_the_list
                    or session_id not in self.last_agent_session_entry_by_session_id):
                continue
            self.exited_agent_sessions_by_session_id[session_id] = {
                "entry": self.last_agent_session_entry_by_session_id[session_id], "ended_at": now, "process_id": process_id}
            asyncio.get_running_loop().call_later(EXITED_ROW_SECONDS, self.snapshot_needed.set)  # when the row goes
        for session_id, exited_agent_session in list(self.exited_agent_sessions_by_session_id.items()):
            if (session_id not in gathered_session_ids or session_id in self.agent_sessions
                    or now - exited_agent_session["ended_at"] >= EXITED_ROW_SECONDS):
                del self.exited_agent_sessions_by_session_id[session_id]

    def forget_agent_processes_that_are_gone(self):
        """Forgets the hook states and "Kill Session" marks of processes that no longer run, unless
        a tab still lists them as its agent (a gather that hasn't noticed yet) or shows their "exited" row."""
        kept_process_ids = ({agent_session["process_id"] for agent_session in self.agent_sessions.values()}
                            | {exited_agent_session["process_id"] for exited_agent_session in self.exited_agent_sessions_by_session_id.values()})
        process_ids_gone = [process_id for process_id in self.hook_state_by_agent_process_id
                            if process_id not in kept_process_ids and not process_is_alive(process_id)]
        for process_id in process_ids_gone:
            del self.hook_state_by_agent_process_id[process_id]
        if process_ids_gone:
            self.save_hook_states()
        self.process_ids_ended_from_the_list = {process_id for process_id in self.process_ids_ended_from_the_list
                                                if process_id in kept_process_ids or process_is_alive(process_id)}

    def note_background_task_counts(self):
        """Sets each agent session's background task count: Claude Code's own list of its
        background shells and agents while its hooks have reported it (hook_event_tracking), plus
        the detached jobs it started, which that list doesn't hold (user request, 2026-10-28),
        otherwise the background shells seen below the agent and its detached jobs
        (read_child_processes_periodically). Also whether the hooks report the turn over with
        background tasks running, as Claude Code's "shell" in `claude agents --json` then means
        (snapshot_builder; code review, 2026-10-29), and whether they report no prompt since
        the agent started, which makes its idle row "new" (user request, 2026-10-30)."""
        for session_id, agent_session in self.agent_sessions.items():
            reading = self.screen_readings_by_session_id.setdefault(session_id, {})
            hook_state = self.hook_state_by_agent_process_id.get(agent_session["process_id"])
            reported_background_task_ids = hook_state["background_task_ids"] if hook_state is not None else None
            reading["background_task_count"] = (
                len(reported_background_task_ids) + reading.get("detached_process_count", 0)
                if reported_background_task_ids is not None else reading.get("background_shell_count", 0))
            reading["hooks_report_background_tasks_after_the_turn"] = bool(
                reported_background_task_ids and find_when_the_hooks_ended_the_turn(hook_state, float("-inf")) is not None)
            reading["hooks_report_no_prompt_since_the_agent_started"] = hooks_report_no_prompt_since_the_agent_started(hook_state)

    def read_again_the_status_of_claude_agents_iterm2_reports_none_for(self):
        """Returns claude_agents_by_process_id with the status of each Claude Code agent whose tab
        iTerm2 gives no status read again from its session file (read_claude_code_session_status):
        its row goes by that status, and the `claude agents --json` record is up to a minute old
        (code review, 2026-10-29). The stored records stay as they are."""
        claude_agents_by_process_id = dict(self.claude_agents_by_process_id)
        for gathered_window in self.gathered_windows:
            for gathered_tab in gathered_window["tabs"]:
                first_session = gathered_tab["first_session"]
                if first_session is None or map_iterm2_status_value_to_state(first_session.get("status")) is not None:
                    continue
                process_id = (self.agent_sessions.get(first_session["session_id"]) or {}).get("process_id")
                claude_agent_record = claude_agents_by_process_id.get(process_id)
                if claude_agent_record is None:
                    continue
                status = read_claude_code_session_status(claude_agent_record.get("config_folder"), process_id)
                if status is not None:
                    claude_agents_by_process_id[process_id] = dict(claude_agent_record, status=status)
        return claude_agents_by_process_id

    def publish_snapshot_if_changed(self):
        """Builds the snapshot from the cached state and publishes it unless only its time changed."""
        now = time.time()
        # The agents are matched to their tabs first, since the status tracking looks up each
        # tab's agent: after a restart its first pass found none, missed a turn end the hooks had
        # saved, and the next pass took that turn as one ending then, showing "done" (code review, 2026-10-29).
        agent_sessions_before = self.agent_sessions
        self.agent_sessions = resolve_agent_sessions(
            self.gathered_windows, self.agent_sessions, self.specific_adapters, self.generic_adapter, process_is_alive,
            process_is_stopped)
        self.list_again_the_paused_agents_that_lost_their_row()
        self.update_session_tracking(now)
        self.record_seen_time_for_the_seen_session()
        self.window_order = update_window_list_order(
            self.window_order, [gathered_window["window_id"] for gathered_window in self.gathered_windows])
        for gathered_window in self.gathered_windows:  # iTerm2's App object follows tab switches itself, without a regather
            gathered_window["selected_tab_id"] = self.terminal_backend_by_name[gathered_window["terminal_name"]].find_selected_tab_id(
                gathered_window["window_id"])
        self.remember_codex_sessions_that_share_a_folder()
        self.continue_the_processes_of_agents_no_longer_paused()
        self.note_agents_that_ended_without_a_session_end(agent_sessions_before, now)
        self.forget_agent_processes_that_are_gone()
        self.note_background_task_counts()
        seen_session_id, highlighted_session_id = self.find_seen_session_id(), self.find_highlighted_session_id()
        claude_agents_by_process_id = self.read_again_the_status_of_claude_agents_iterm2_reports_none_for()

        def build_snapshot_of(agent_sessions, exited_agent_sessions_by_session_id):
            return build_computer_snapshot(
                self.gathered_windows, agent_sessions, self.monitors, self.window_order, self.session_tracking_by_session_id,
                self.displayed_state_by_session_id, self.screen_readings_by_session_id, STUCK_THRESHOLD_SECONDS,
                claude_agents_by_process_id, seen_session_id, highlighted_session_id, self.computer_id,
                self.computer_name, now, exited_agent_sessions_by_session_id, self.hooks_status_per_agent_tool)
        listed_agent_sessions = {session_id: agent_session for session_id, agent_session in self.agent_sessions.items()
                                 if agent_session["agent_tool_name"] not in self.left_out_agent_tool_names}
        entries_and_end_times_of_exited_agent_sessions_by_session_id = {
            session_id: (exited_agent_session["entry"], exited_agent_session["ended_at"])
            for session_id, exited_agent_session in self.exited_agent_sessions_by_session_id.items()}
        listed_exited_agent_sessions_by_session_id = {
            session_id: entry_and_end_time for session_id, entry_and_end_time
            in entries_and_end_times_of_exited_agent_sessions_by_session_id.items()
            if entry_and_end_time[0].agent_tool_name not in self.left_out_agent_tool_names}
        snapshot = build_snapshot_of(listed_agent_sessions, listed_exited_agent_sessions_by_session_id)
        # Sessions left out of the list (Harnesses) keep their shown state current too, so a
        # suspended agent's state and every state's start are right when they are listed again.
        snapshot_of_every_session = (
            snapshot if (len(listed_agent_sessions), len(listed_exited_agent_sessions_by_session_id))
            == (len(self.agent_sessions), len(entries_and_end_times_of_exited_agent_sessions_by_session_id))
            else build_snapshot_of(self.agent_sessions, entries_and_end_times_of_exited_agent_sessions_by_session_id))
        self.displayed_state_by_session_id = {
            agent_session_entry.session_id: DisplayedState(
                agent_session_entry.status, agent_session_entry.status_since,
                self.session_tracking_by_session_id.get(agent_session_entry.session_id, {}).get("status_change_time", now))
            for _, _, agent_session_entry in iterate_agent_session_entries(snapshot_of_every_session)}
        self.last_agent_session_entry_by_session_id = {
            agent_session_entry.session_id: agent_session_entry
            for _, _, agent_session_entry in iterate_agent_session_entries(snapshot_of_every_session)
            if agent_session_entry.session_id in self.agent_sessions}
        snapshot_without_time = snapshot._replace(taken_at=0)
        if snapshot_without_time != self.last_published_snapshot_without_time:
            self.last_published_snapshot_without_time = snapshot_without_time
            self.publish_snapshot(snapshot)

    # ----- background tasks -----

    async def listen_for_hook_events(self):
        """Receives the app's hook events (hook_event_forwarder), one line per connection, on the
        socket at hook_events_socket_path, taken over from an earlier run. While another copy of the
        app (one still giving docked windows their room back as it exits, say)
        receives them there, this one goes on without them, and takes the socket over once that
        copy has ended (code review, 2026-10-28). If the socket can't be made, it goes on without
        hook events (this task must not end: that ends the core)."""
        if another_process_listens_at(self.hook_events_socket_path):
            print(f"another copy of the app receives the hook events at {self.hook_events_socket_path}; "
                  f"this one goes on without them until that copy ends")
            while await asyncio.to_thread(another_process_listens_at, self.hook_events_socket_path):
                await asyncio.sleep(SECONDS_BETWEEN_CHECKS_FOR_ANOTHER_HOOK_EVENTS_LISTENER)
        try:
            if os.path.exists(self.hook_events_socket_path):
                os.unlink(self.hook_events_socket_path)  # left by a run that ended
            os.makedirs(os.path.dirname(self.hook_events_socket_path), exist_ok=True)
            server = await asyncio.start_unix_server(self.receive_hook_event, path=self.hook_events_socket_path,
                                                     limit=LONGEST_HOOK_EVENT_LINE)
        except OSError as error:
            print(f"hook events can't be received at {self.hook_events_socket_path}: {error!r}")
            await asyncio.Event().wait()
        async with server:
            await server.serve_forever()

    async def receive_hook_event(self, reader, writer):
        """Reads one hook event line from a forwarder's connection, then closes it."""
        try:
            line = await reader.readline()
            event = json.loads(line) if line else None
        except (ValueError, ConnectionError) as error:  # (a line over the limit raises ValueError)
            print(f"a hook event couldn't be read: {error!r}")
            event = None
        finally:
            writer.close()
        if isinstance(event, dict):
            self.note_hook_event(event)

    def note_hook_event(self, event):
        """Applies a hook event to the hook state of the agent process that sent it (an agent
        inside a tab's agent has its own), and takes away the "exited" row of an agent whose
        normal session end arrives after its process was gone."""
        ancestor_processes = [ancestor for ancestor in event.get("ancestor_processes") or []
                              if isinstance(ancestor, list) and len(ancestor) == 2 and isinstance(ancestor[0], int)]
        process_id = find_agent_process_id_of_hook_event(ancestor_processes)
        if process_id is None:
            return
        if self.verbose:
            print(f"hook event {event.get('hook_event_name')} from {event.get('agent_tool_name')} process {process_id}")
        hook_state = apply_hook_event(self.hook_state_by_agent_process_id.get(process_id), event)
        self.hook_state_by_agent_process_id[process_id] = hook_state
        self.save_hook_states()
        if hook_state["session_ended_at"] is not None:
            for session_id, exited_agent_session in list(self.exited_agent_sessions_by_session_id.items()):
                if exited_agent_session["process_id"] == process_id:
                    del self.exited_agent_sessions_by_session_id[session_id]
        if (event.get("hook_event_name") == "SessionStart"
                and event.get("source") in SESSION_START_SOURCES_OF_ANOTHER_CONVERSATION_IN_THE_SAME_PROCESS):
            # /clear or /resume typed in the terminal: the process goes on in another conversation,
            # whose ID the gather asked for now gets from `claude agents --json`, as after the list's
            # own /clear (forget_the_cleared_conversation), so Duplicate doesn't fork the old one
            # (code review, 2026-10-28).
            self.process_ids_asked_about_in_claude_agents.discard(process_id)
            self.regather_needed.set()
        self.snapshot_needed.set()

    async def check_hooks_periodically(self):
        """Keeps hooks_status_per_agent_tool current (hooks_installer.check_hooks), checked every
        HOOKS_CHECK_INTERVAL_SECONDS (the user can change the files, or approve Codex's hooks, at
        any time) and at once after the app installed hooks (hooks_check_needed)."""
        while True:
            try:
                hooks_status_per_agent_tool = await asyncio.to_thread(
                    lambda: tuple((agent_tool_name, check_hooks(agent_tool_name)) for agent_tool_name in HOOK_EVENTS_BY_AGENT_TOOL_NAME))
            except Exception as error:  # a check that fails leaves the last status; it must not end the core
                print(f"the hooks couldn't be checked: {error!r}")
                hooks_status_per_agent_tool = self.hooks_status_per_agent_tool
            if hooks_status_per_agent_tool != self.hooks_status_per_agent_tool:
                self.hooks_status_per_agent_tool = hooks_status_per_agent_tool
                self.snapshot_needed.set()
            try:
                await asyncio.wait_for(self.hooks_check_needed.wait(), HOOKS_CHECK_INTERVAL_SECONDS)
            except asyncio.TimeoutError:
                pass
            self.hooks_check_needed.clear()

    async def read_agent_screens_periodically(self):
        """Reads the screens the status rules need: the permission dialog of an
        asking session whose tool needs the check (every SCREEN_CHECK_INTERVAL_SECONDS), and a
        fingerprint of each working session (every FINGERPRINT_INTERVAL_SECONDS) for "stuck".
        One screen read serves both. A session that starts asking is read at once, so the count of
        "Interrupted" lines from before the user could answer is known. A session in a terminal
        that reports no agent status (tmux) whose tool shows a turn on screen (Codex) is read
        every time too, since its status comes from there (read_status_value_of_the_tabs_own_agent)."""
        while True:
            try:
                await asyncio.wait_for(self.screen_check_needed.wait(), SCREEN_CHECK_INTERVAL_SECONDS)
            except asyncio.TimeoutError:
                pass
            self.screen_check_needed.clear()
            status_by_session_id = {gathered_tab["first_session"]["session_id"]: gathered_tab["first_session"]["status"]
                                    for gathered_window in self.gathered_windows for gathered_tab in gathered_window["tabs"]
                                    if gathered_tab["first_session"]}
            session_ids_whose_terminal_reports_no_agent_status = {
                gathered_tab["first_session"]["session_id"] for gathered_window in self.gathered_windows
                if not self.terminal_backend_by_name[gathered_window["terminal_name"]].reports_agent_status
                for gathered_tab in gathered_window["tabs"] if gathered_tab["first_session"]}
            for session_id in set(self.screen_readings_by_session_id) - set(self.agent_sessions):
                del self.screen_readings_by_session_id[session_id]
            for session_id, agent_session in list(self.agent_sessions.items()):
                if agent_session["is_in_background"]:
                    continue
                state = map_iterm2_status_value_to_state(status_by_session_id.get(session_id))
                reading = self.screen_readings_by_session_id.setdefault(session_id, {})
                adapter = agent_session["adapter"]
                now = time.time()
                status_change_time = self.session_tracking_by_session_id.get(session_id, {}).get("status_change_time", now)
                needs_dialog_check = state == "asking" and bool(adapter.permission_dialog_screen_patterns)
                # An approved tool runs while iTerm2 still says waiting, so it is fingerprinted like "working".
                is_effectively_working = state == "working" or permission_dialog_was_answered(state, status_change_time, reading)
                await self.note_command_the_agent_is_waiting_on(session_id, agent_session, reading, state, is_effectively_working, now)
                needs_fingerprint = is_effectively_working and now - reading.get("fingerprint_checked_at", 0) >= FINGERPRINT_INTERVAL_SECONDS
                # A row that goes by its own agent: while an agent runs inside it (choose_status_value_of_the_tabs_own_agent),
                # and always in a terminal that reports no agent status.
                needs_turn_check = ((session_id in self.iterm2_status_seen_while_an_inner_agent_ran_by_session_id
                                     or session_id in session_ids_whose_terminal_reports_no_agent_status)
                                    and adapter.working_screen_pattern is not None)
                if not (needs_dialog_check or needs_fingerprint or needs_turn_check):
                    continue
                terminal_session = self.find_terminal_session(session_id)
                if terminal_session is None:
                    continue
                try:
                    screen_lines = await terminal_session.read_screen_lines()
                except TerminalRequestFailed:
                    continue  # the tab closed meanwhile
                if needs_dialog_check:
                    reading = update_permission_dialog_reading(
                        reading, screen_shows_permission_dialog(screen_lines, adapter.permission_dialog_screen_patterns),
                        count_lines_matching(screen_lines, adapter.interrupted_turn_screen_pattern), status_change_time, now)
                    self.screen_readings_by_session_id[session_id] = reading
                if needs_fingerprint:
                    fingerprint = compute_screen_fingerprint(screen_lines, adapter.progress_noise_patterns)
                    if fingerprint != reading.get("fingerprint"):
                        reading["fingerprint"] = fingerprint
                        reading["fingerprint_changed_at"] = now
                    reading["fingerprint_checked_at"] = now
                if needs_turn_check:
                    reading["turn_shows_on_screen"] = count_lines_matching(screen_lines, adapter.working_screen_pattern) > 0
                self.snapshot_needed.set()  # also lets "stuck" appear once its time has come

    async def read_context_window_usage_periodically(self):
        """Keeps each agent session's context size and preview fields ("context_tokens" and
        "conversation_preview" in its screen reading) current from the end of its conversation
        file (context_window_usage, session_preview_computation). The file is looked up
        (Claude Code: from `claude agents --json`; Codex: by its folder and start time) for each new process or Claude
        Code conversation and again every minute, and read again only when it changed."""
        while True:
            await asyncio.sleep(CONTEXT_USAGE_CHECK_INTERVAL_SECONDS)
            for session_id in set(self.conversation_files_by_session_id) - set(self.agent_sessions):
                del self.conversation_files_by_session_id[session_id]
            for session_id, agent_session in list(self.agent_sessions.items()):
                await self.update_conversation_file_readings(session_id, agent_session)

    async def update_conversation_file_readings(self, session_id, agent_session):
        """Looks up one agent session's conversation file when due and reads its end again when
        it changed. A /clear can change the session's record (conversation_files_by_session_id)
        while this waits on a lookup or a read; the record is then fetched again, and the reads'
        results are dropped."""
        # Looked up again for a new process or Claude Code conversation (/clear), and every so
        # often anyway: Codex's /new starts a new file inside the same process.
        conversation_key = self.find_conversation_key(agent_session)
        conversation_file = self.conversation_files_by_session_id.get(session_id)
        now = time.time()
        seconds_between_lookups = (CONVERSATION_FILE_RETRY_SECONDS if conversation_file is None or conversation_file["path"] is None
                                   else CONVERSATION_FILE_RECHECK_SECONDS)
        if (conversation_file is None or conversation_file["key"] != conversation_key
                or now - conversation_file["looked_up_at"] >= seconds_between_lookups):
            path = await self.look_up_conversation_file(session_id, agent_session)
            if session_id not in self.agent_sessions:
                return  # the tab closed during the lookup
            conversation_file = self.conversation_files_by_session_id.get(session_id)
            is_same_conversation = conversation_file is not None and conversation_file["key"] == conversation_key
            if is_same_conversation and path == conversation_file["cleared_path"]:
                path = None  # the conversation /clear ended; the agent hasn't written its next one yet
            if is_same_conversation and conversation_file["path"] == path:
                conversation_file["looked_up_at"] = now
            else:
                conversation_file = build_conversation_file_record(
                    conversation_key, path, now, conversation_file["cleared_path"] if is_same_conversation else None)
                self.conversation_files_by_session_id[session_id] = conversation_file
                self.forget_conversation_file_readings(session_id)
        path = conversation_file["path"]
        if path is None:
            return
        try:
            modified_at = os.path.getmtime(path)
        except OSError:
            conversation_file["path"] = None
            self.forget_conversation_file_readings(session_id)
            return
        if modified_at == conversation_file["modified_at"]:
            return
        context_tokens = await asyncio.to_thread(agent_session["adapter"].read_context_tokens, path)
        # None means no usage in the file's end (one huge line), so the last size stays.
        context_tokens_kept = (context_tokens if context_tokens is not None
                               else self.screen_readings_by_session_id.get(session_id, {}).get("context_tokens"))
        context_window_tokens = await asyncio.to_thread(
            agent_session["adapter"].read_context_window_tokens, path, agent_session["process_id"],
            self.claude_agents_by_process_id.get(agent_session["process_id"]), context_tokens_kept)
        preview_fields = await asyncio.to_thread(agent_session["adapter"].read_preview_fields, path)
        if (session_id not in self.agent_sessions or self.conversation_files_by_session_id.get(session_id) is not conversation_file
                or conversation_file["path"] != path):
            return  # the tab closed, or /clear or a new conversation took this file's place, during the reads
        conversation_file["modified_at"] = modified_at
        if context_tokens is not None:
            self.note_reading_value(session_id, "context_tokens", context_tokens)
        if context_window_tokens is not None:
            self.note_reading_value(session_id, "context_window_tokens", context_window_tokens)
        self.note_reading_value(session_id, "conversation_preview", preview_fields)
        open_tool_started_at = (preview_fields or {}).get("open_tool_started_at")
        if open_tool_started_at is not None:
            # Its row shows it running once it is that old, and the file may not change by then,
            # so nothing else would ask for that snapshot (code review, 2026-10-29). A tenth of a
            # second later, so the snapshot's time is past it.
            seconds_until_it_shows_as_running = SECONDS_BEFORE_AN_OPEN_TOOL_CALL_SHOWS_AS_RUNNING - (time.time() - open_tool_started_at)
            if seconds_until_it_shows_as_running > 0:
                asyncio.get_running_loop().call_later(seconds_until_it_shows_as_running + 0.1, self.snapshot_needed.set)

    def find_conversation_key(self, agent_session):
        """Returns (agent process ID, Claude Code session ID or None): another process, or another
        Claude Code conversation in the same process (after /clear), is another conversation."""
        process_id = agent_session["process_id"]
        return process_id, (self.claude_agents_by_process_id.get(process_id) or {}).get("sessionId")

    async def look_up_conversation_file(self, session_id, agent_session):
        """Returns the path of the agent session's conversation file as its adapter finds it, or
        None. None also for a Codex session that shares or shared its folder with another, whose file
        can't be told apart from the other's (codex_conversation_may_be_another_sessions): its row
        then shows no context size or preview rather than the other session's."""
        if self.codex_conversation_may_be_another_sessions(session_id, agent_session):
            return None
        process_id = agent_session["process_id"]
        # The config folders `claude agents` and the hooks installer use, listed now, so one made
        # after the app started counts too (code review, 2026-10-29).
        return await agent_session["adapter"].find_conversation_file(
            process_id, self.claude_agents_by_process_id.get(process_id) or {},
            [config_folder or os.path.expanduser("~/.claude") for config_folder in list_claude_config_folders()])

    def forget_conversation_file_readings(self, session_id):
        """Clears the values read from a session's conversation file, when it is no longer its
        conversation (a new one, a vanished file, /clear)."""
        for reading_name in CONVERSATION_FILE_READING_NAMES:
            self.note_reading_value(session_id, reading_name, None)

    def note_reading_value(self, session_id, reading_name, value):
        """Stores a value read for a session, from its conversation file ("context_tokens",
        "conversation_preview") or its processes ("memory_bytes", "cpu_percent"), in its screen
        reading, asking for a snapshot when it changed."""
        reading = self.screen_readings_by_session_id.setdefault(session_id, {})
        if value != reading.get(reading_name):
            reading[reading_name] = value
            self.snapshot_needed.set()

    async def read_resource_usage_periodically(self):
        """Keeps each agent session's memory and CPU use ("memory_bytes", "cpu_percent" in its
        screen reading) current while the Memory or CPU column is shown: its agent process and
        every process below it, read every RESOURCE_USAGE_CHECK_INTERVAL_SECONDS in a worker
        thread (a few system calls per process, no subprocess). Both are rounded (to the
        megabyte, to a whole percent, and below CPU_PERCENT_SHOWN_AS_IDLE to 0) so that an idle
        session's row and the snapshot don't change at every reading.

        A column shown, or the list sorted by memory or CPU, starts the readings at once
        (resource_usage_reading_needed), and a session's first reading, which can't give its CPU
        use yet, is followed by a second after SECONDS_BETWEEN_THE_FIRST_TWO_CPU_READINGS, so the
        values and the order show up within a second rather than after 3 to 6 (code review, 2026-10-29)."""
        cpu_time_readings_by_session_id = {}  # (process ID, CPU seconds, read at) from the last reading
        seconds_until_the_next_reading = RESOURCE_USAGE_CHECK_INTERVAL_SECONDS
        while True:
            try:
                await asyncio.wait_for(self.resource_usage_reading_needed.wait(), seconds_until_the_next_reading)
            except asyncio.TimeoutError:
                pass
            self.resource_usage_reading_needed.clear()
            seconds_until_the_next_reading = RESOURCE_USAGE_CHECK_INTERVAL_SECONDS
            if not self.resource_usage_is_read:
                # Columns shown again later start over rather than average over the hidden time.
                cpu_time_readings_by_session_id.clear()
                for session_id in list(self.screen_readings_by_session_id):
                    self.note_reading_value(session_id, "memory_bytes", None)
                    self.note_reading_value(session_id, "cpu_percent", None)
                continue
            process_ids_by_session_id = {session_id: agent_session["process_id"]
                                         for session_id, agent_session in self.agent_sessions.items() if agent_session["process_id"]}
            usages_by_session_id = await asyncio.to_thread(
                lambda: {session_id: read_process_tree_resource_usage(process_id)
                         for session_id, process_id in process_ids_by_session_id.items()})
            now = time.time()
            for session_id in set(cpu_time_readings_by_session_id) - set(usages_by_session_id):
                del cpu_time_readings_by_session_id[session_id]
            for session_id, usage in usages_by_session_id.items():
                if session_id not in self.agent_sessions:
                    continue  # the tab closed during the reading
                memory_bytes = cpu_percent = None
                if usage is not None:
                    process_id = process_ids_by_session_id[session_id]
                    previous_reading = cpu_time_readings_by_session_id.get(session_id)
                    # A new agent process in the tab starts its CPU time over.
                    if previous_reading is not None and previous_reading[0] == process_id:
                        cpu_percent = compute_cpu_percent(previous_reading[1:], (usage[1], now))
                    else:
                        seconds_until_the_next_reading = SECONDS_BETWEEN_THE_FIRST_TWO_CPU_READINGS
                    cpu_time_readings_by_session_id[session_id] = (process_id, usage[1], now)
                    memory_bytes, cpu_percent = round_memory_and_cpu_use(usage[0], cpu_percent)
                self.note_reading_value(session_id, "memory_bytes", memory_bytes)
                self.note_reading_value(session_id, "cpu_percent", cpu_percent)

    def list_agent_processes_and_their_turns(self):
        """Returns {session ID: (agent process ID, whether it is in a turn, when that turn began or
        None, its Claude Code conversation's ID or None, when the agent started or None)} for
        read_child_processes_periodically; the conversation's ID and the agent's start find its
        detached processes. Whether it is in a turn goes by the status value its row goes by, the
        tracked one: a paused agent's tab has none from iTerm2 while the shell is in front, and
        its turn goes on once it is back. With no value from iTerm2 at all (after iTerm2
        restarted), it goes by the state the row shows, from Claude Code's own report (code
        review, 2026-10-28)."""
        def agent_is_in_a_turn(session_id):
            status_value = self.session_tracking_by_session_id.get(session_id, {}).get("status_value")
            if status_value is None:
                displayed_state = self.displayed_state_by_session_id.get(session_id)
                return displayed_state is not None and (displayed_state.state in STATES_OF_A_TURN_UNDER_WAY
                                                        or displayed_state.state == "stuck")
            return map_iterm2_status_value_to_state(status_value) in STATES_OF_A_TURN_UNDER_WAY
        return {
            session_id: (agent_session["process_id"], agent_is_in_a_turn(session_id),
                         self.session_tracking_by_session_id.get(session_id, {}).get("turn_started_at"),
                         (self.claude_agents_by_process_id.get(agent_session["process_id"]) or {}).get("sessionId"),
                         agent_session.get("started_at"))
            for session_id, agent_session in self.agent_sessions.items() if agent_session["process_id"]}

    async def read_child_processes_periodically(self):
        """Keeps each agent session's child processes ("child_processes", the sub rows of its open
        tab row; user request, 2026-10-27) and its background shells ("background_shell_count")
        current, every CHILD_PROCESS_CHECK_INTERVAL_SECONDS, in a worker thread (a few
        system calls per process, no subprocess). While the Memory or CPU column shows, each
        child's use is read too, rounded as the agent's is (read_resource_usage_periodically)."""
        cpu_time_readings_by_child = {}  # {(process ID, started at): (CPU seconds, read at)} from the last reading
        while True:  # read at once, then every CHILD_PROCESS_CHECK_INTERVAL_SECONDS (dump-snapshot shows them too)
            reads_resource_usage = self.resource_usage_is_read
            agent_process_and_turn_by_session_id = self.list_agent_processes_and_their_turns()

            def read_every_agents_child_processes():
                # launchd's children are read once for all the agents, only when a conversation's ID is known.
                agent_starts = [agent_started_at for _, _, _, conversation_id, agent_started_at
                                in agent_process_and_turn_by_session_id.values() if conversation_id and agent_started_at is not None]
                detached_processes = list_detached_processes(min(agent_starts)) if agent_starts else []
                return {session_id: read_child_processes(
                            agent_process_id, turn_is_under_way, turn_started_at, reads_resource_usage, time.time(),
                            choose_detached_processes_of_the_conversation(detached_processes, conversation_id, agent_started_at)
                            if agent_started_at is not None else ())
                        for session_id, (agent_process_id, turn_is_under_way, turn_started_at, conversation_id, agent_started_at)
                        in agent_process_and_turn_by_session_id.items()}
            child_processes_by_session_id = await asyncio.to_thread(read_every_agents_child_processes)
            cpu_time_readings_by_child = self.note_child_processes(child_processes_by_session_id, cpu_time_readings_by_child,
                                                                   time.time())
            await asyncio.sleep(CHILD_PROCESS_CHECK_INTERVAL_SECONDS)

    def note_child_processes(self, child_processes_by_session_id, cpu_time_readings_by_child, now):
        """Stores one reading of each agent session's child processes (as ChildProcessEntry
        records) and background shells in its screen reading; returns the CPU time readings for the next one.

        Args:
            child_processes_by_session_id: {session ID: agent_process_tree.read_child_processes's list}.
            cpu_time_readings_by_child: {(process ID, started at): (CPU seconds, read at)} from the
                reading before, for each child's CPU use since then.
            now: When the processes were read.
        """
        new_cpu_time_readings_by_child = {}
        for session_id, child_processes in child_processes_by_session_id.items():
            if session_id not in self.agent_sessions:
                continue  # the tab closed during the reading
            child_process_entries = []
            for child_process in child_processes:
                child, usage = child_process["child"], child_process["resource_usage"]
                memory_bytes = cpu_percent = None
                if usage is not None:
                    child_key = (child.process_id, child.started_at)  # a reused process ID is another process
                    if child_key in cpu_time_readings_by_child:
                        cpu_percent = compute_cpu_percent(cpu_time_readings_by_child[child_key], (usage[1], now))
                    new_cpu_time_readings_by_child[child_key] = (usage[1], now)
                    memory_bytes, cpu_percent = round_memory_and_cpu_use(usage[0], cpu_percent)
                child_process_entries.append(ChildProcessEntry(
                    child.process_id, child_process["command_text"][:LONGEST_COMMAND_TEXT], child_process["command_started_at"],
                    child_process["is_background_shell"], memory_bytes, cpu_percent))
            self.note_reading_value(session_id, "child_processes", tuple(child_process_entries))
            self.note_reading_value(session_id, "background_shell_count",
                                    sum(child_process["is_background_shell"] for child_process in child_processes))
            self.note_reading_value(session_id, "detached_process_count",
                                    sum(child_process["is_detached"] for child_process in child_processes))
        return new_cpu_time_readings_by_child

    async def note_command_the_agent_is_waiting_on(self, session_id, agent_session, reading, state, is_effectively_working, now):
        """Records in the session's screen reading the command a working agent started in this
        turn and still runs ("running_command": (the name it was started as, started at)), and
        when one was last seen running, so "stuck" means thinking with nothing new on screen (user
        request, milestone 2). Between turns it records the processes running then
        ("helper_process_ids", for example MCP servers), which are not commands. A few system
        calls, no subprocess."""
        running_command = None
        if state in STATES_OF_AN_AGENT_WAITING_AT_ITS_PROMPT and agent_session["process_id"] and \
                now - reading.get("helper_process_ids_recorded_at", 0) >= SECONDS_BETWEEN_HELPER_PROCESS_RECORDINGS:
            reading["helper_process_ids"] = choose_helper_process_ids(list_descendant_processes(agent_session["process_id"]))
            reading["helper_process_ids_recorded_at"] = now
        if is_effectively_working and agent_session["process_id"]:
            turn_started_at = await self.find_turn_start(session_id, agent_session, now)
            command = choose_command_the_agent_is_waiting_on(
                list_descendant_processes(agent_session["process_id"]), turn_started_at,
                agent_session["adapter"].helper_process_names, now, reading.get("helper_process_ids", frozenset()),
                lambda process_id: read_process_arguments_and_environment(process_id)[0])
            if command is not None:
                command_process, command_started_at = command
                running_command = (read_name_the_process_was_started_as(command_process.process_id) or command_process.name,
                                   command_started_at)
        if running_command is not None:
            reading["command_last_seen_running_at"] = now
        if running_command != reading.get("running_command"):
            reading["running_command"] = running_command
            self.snapshot_needed.set()

    async def find_turn_start(self, session_id, agent_session, now):
        """Returns when the session's current turn began. It is known when the dashboard saw the
        status turn working; for a turn already under way when the dashboard started, it is read
        once from the conversation file (the latest prompt), so a command started in this turn
        counts and a dev server left from an earlier turn doesn't. Until that file is found, the
        agent's own start plus SECONDS_AN_AGENT_TAKES_TO_START_ITS_HELPERS stands in."""
        tracking = self.session_tracking_by_session_id.get(session_id) or {}  # set up by update_session_tracking
        if tracking.get("turn_started_at") is not None:
            return tracking["turn_started_at"]
        conversation_file_path = (self.conversation_files_by_session_id.get(session_id) or {}).get("path")
        if (session_id in self.session_tracking_by_session_id and conversation_file_path
                and not tracking.get("turn_start_was_read_from_the_conversation_file")):
            tracking["turn_start_was_read_from_the_conversation_file"] = True
            turn_started_at = await asyncio.to_thread(agent_session["adapter"].read_turn_started_at, conversation_file_path)
            if turn_started_at is not None:
                tracking["turn_started_at"] = turn_started_at
                return turn_started_at
        agent_process = read_process_info(agent_session["process_id"])
        return agent_process.started_at + SECONDS_AN_AGENT_TAKES_TO_START_ITS_HELPERS if agent_process is not None else now

    async def poll_window_frames_when_they_matter(self):
        """Notices windows moved by hand, which iTerm2 sends no notification for:
        with several monitors, to regroup them; while a sidebar is docked, to keep them out of its
        area (a regather makes room). With one monitor and no docked sidebar, frames don't matter."""
        while True:
            await asyncio.sleep(FRAME_POLL_INTERVAL_SECONDS)
            if len(self.monitors) < 2 and self.reserved_screen_area is None:
                continue
            for gathered_window in self.list_gathered_windows_with_frames():
                try:
                    new_frame = await self.terminal_backend_by_name[gathered_window["terminal_name"]].read_window_frame(
                        gathered_window["window_id"])
                except TerminalRequestFailed:
                    continue  # closed meanwhile; the layout change handles it
                if new_frame != gathered_window["frame"]:
                    gathered_window["frame"] = new_frame
                    if self.reserved_screen_area is not None:
                        self.regather_needed.set()
                    else:
                        self.snapshot_needed.set()

    async def refresh_claude_agents_periodically(self):
        """Keeps names and folders from `claude agents --json` current (started only when `claude` exists).

        The program is large, so this runs only while Claude Code tabs exist; new sessions
        trigger a run of their own.
        """
        while True:
            await asyncio.sleep(CLAUDE_AGENTS_REFRESH_INTERVAL_SECONDS)
            if not self.claude_agents_refresh_is_running and self.list_claude_code_process_ids():
                await self.refresh_claude_agents()

    async def publish_when_needed(self):
        """Coalesces requests: at most one regather or rebuild per SNAPSHOT_COALESCING_DELAY_SECONDS."""
        while True:
            regather_task = asyncio.ensure_future(self.regather_needed.wait())
            snapshot_task = asyncio.ensure_future(self.snapshot_needed.wait())
            await asyncio.wait([regather_task, snapshot_task], return_when=asyncio.FIRST_COMPLETED)
            regather_task.cancel()
            snapshot_task.cancel()
            await asyncio.sleep(SNAPSHOT_COALESCING_DELAY_SECONDS)
            regather_is_needed = self.regather_needed.is_set()
            self.regather_needed.clear()
            self.snapshot_needed.clear()
            if regather_is_needed:
                await self.regather_and_publish()
            else:
                self.publish_snapshot_if_changed()

    async def watch_the_own_status_of_agents_in_terminals_without_status_reports(self):
        """Asks for a snapshot when the own report of an agent in a terminal that reports no agent
        status (tmux) changes (read_status_value_of_the_tabs_own_agent): nothing else tells the
        core when Claude Code's session file changes. Read every SECONDS_BETWEEN_OWN_STATUS_READINGS."""
        while True:
            await asyncio.sleep(SECONDS_BETWEEN_OWN_STATUS_READINGS)
            for session_id, own_status_value in list(self.own_status_value_by_session_id.items()):
                agent_session = self.agent_sessions.get(session_id)
                if agent_session is not None and agent_session["process_id"] and (
                        self.read_status_value_of_the_tabs_own_agent(session_id, agent_session) != own_status_value):
                    self.snapshot_needed.set()
                    break

    async def run(self):
        """Runs every terminal backend (each connects when its terminal runs) and the core's own
        tasks until one ends: an established iTerm2 connection that is lost, or a task that
        fails. Every way this ends calls report_connection_lost, so the app exits and its
        supervisor restarts it (the iterm2 package can't reconnect within one process).
        """
        # Before connecting: once iTerm2 quits, its paused agents are gone, and what their pauses
        # stopped below them (in sessions of their own, which closing the tabs doesn't reach) is
        # continued even while no connection can be made.
        self.continue_the_processes_of_agents_no_longer_paused()
        coroutines = [terminal_backend.run() for terminal_backend in self.terminal_backends] + [
            self.poll_window_frames_when_they_matter(), self.read_agent_screens_periodically(),
            self.read_context_window_usage_periodically(), self.read_resource_usage_periodically(),
            self.read_child_processes_periodically(), self.check_hooks_periodically(),
            self.watch_the_own_status_of_agents_in_terminals_without_status_reports(), self.publish_when_needed()]
        if self.claude_command_path:
            coroutines.append(self.refresh_claude_agents_periodically())
        if self.hook_events_socket_path:
            coroutines.append(self.listen_for_hook_events())
        self.regather_needed.set()  # the first snapshot, with whatever terminals run by then
        background_tasks = [asyncio.ensure_future(coroutine) for coroutine in coroutines]
        done_tasks, _ = await asyncio.wait(background_tasks, return_when=asyncio.FIRST_COMPLETED)
        failure_message = "the core stopped"
        for done_task in done_tasks:
            exception = None if done_task.cancelled() else done_task.exception()
            if isinstance(exception, TerminalConnectionLost):
                failure_message = str(exception)
            elif exception is not None:
                failure_message = f"the core stopped: {exception!r}"
        self.report_connection_lost(failure_message)


class DashboardCoreThread(threading.Thread):
    """Runs DashboardCore on its own asyncio event loop, and offers thread-safe entry points."""

    def __init__(self, publish_snapshot, report_connection_lost, verbose=False, docked_window_frames_file_path=None,
                 pause_records_file_path=None, pause_record_by_tab_and_agent_to_start_with=None, hook_events_socket_path=None,
                 hook_states_file_path=None, started_at_and_hook_state_by_agent_process_id_to_start_with=None,
                 report_terminal_problem=None):
        super().__init__(name="dashboard-core", daemon=True)
        self.core_ready = threading.Event()
        self.core = None
        self.event_loop = None
        self.publish_snapshot = publish_snapshot
        self.report_connection_lost = report_connection_lost
        self.report_terminal_problem = report_terminal_problem
        self.verbose = verbose
        self.docked_window_frames_file_path = docked_window_frames_file_path
        self.pause_records_file_path = pause_records_file_path
        self.pause_record_by_tab_and_agent_to_start_with = pause_record_by_tab_and_agent_to_start_with
        self.hook_events_socket_path = hook_events_socket_path
        self.hook_states_file_path = hook_states_file_path
        self.started_at_and_hook_state_by_agent_process_id_to_start_with = started_at_and_hook_state_by_agent_process_id_to_start_with

    def run(self):
        self.event_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.event_loop)
        self.core = DashboardCore(self.publish_snapshot, self.report_connection_lost, self.verbose,
                                  self.docked_window_frames_file_path, self.pause_records_file_path,
                                  self.pause_record_by_tab_and_agent_to_start_with, self.hook_events_socket_path,
                                  self.hook_states_file_path, self.started_at_and_hook_state_by_agent_process_id_to_start_with,
                                  self.report_terminal_problem)
        self.core_ready.set()
        self.event_loop.run_until_complete(self.core.run())

    def give_room_back_and_wait(self, timeout_seconds):
        """Thread-safe: ends docking and waits, at most timeout_seconds, until the iTerm2 windows
        (the only ones with frames) have their room back; called just before the app exits."""
        if not self.core_ready.is_set():
            return
        future = asyncio.run_coroutine_threadsafe(self.core.change_reserved_screen_area(None), self.event_loop)
        try:
            future.result(timeout_seconds)
        except Exception as error:  # a timeout or a lost connection: the saved frames still allow a later restore
            print(f"could not give the iTerm2 windows their room back before exiting: {error!r}")

    def submit_action(self, action_request):
        """Thread-safe: schedules an action on the core's loop (the core prints failures); returns
        a concurrent.futures.Future that completes with its ActionResult."""
        self.core_ready.wait()
        return asyncio.run_coroutine_threadsafe(self.core.perform_action(action_request), self.event_loop)

    def send_monitors(self, monitors):
        """Thread-safe: passes the monitor rectangles to the core."""
        self.core_ready.wait()
        self.event_loop.call_soon_threadsafe(self.core.set_monitors, monitors)

    def send_reserved_screen_area(self, area):
        """Thread-safe: passes the docked sidebar's area (or None) to the core."""
        self.core_ready.wait()
        self.event_loop.call_soon_threadsafe(self.core.set_reserved_screen_area, area)

    def send_core_settings(self, core_settings):
        """Thread-safe: passes the settings the core uses (settings_store.build_core_settings) to the core."""
        self.core_ready.wait()
        self.event_loop.call_soon_threadsafe(self.core.set_core_settings, core_settings)

    def send_dashboard_is_active(self, dashboard_is_active):
        """Thread-safe: tells the core whether the dashboard is the active app."""
        self.core_ready.wait()
        self.event_loop.call_soon_threadsafe(self.core.set_dashboard_is_active, dashboard_is_active)

    def send_last_other_active_application_process_id(self, process_id):
        """Thread-safe: tells the core the process ID of the last app other than the dashboard to be active."""
        self.core_ready.wait()
        self.event_loop.call_soon_threadsafe(self.core.set_last_other_active_application_process_id, process_id)

    def send_hooks_changed(self):
        """Thread-safe: has the core check the agent tools' hooks again now (after the app installed them)."""
        self.core_ready.wait()
        self.event_loop.call_soon_threadsafe(self.core.hooks_check_needed.set)
