"""The core's background thread: the iTerm2 connection, its subscriptions, and snapshot publishing.

PLAN.md section 4. Everything here runs on one asyncio event loop in a background thread; the
AppKit main thread only receives snapshots (through publish_snapshot) and sends messages and
action requests (through the thread-safe methods of Iterm2ConnectionThread).
"""

import asyncio
import glob
import json
import os
import shutil
import signal
import socket
import threading
import time

import iterm2
import iterm2.auth
import iterm2.rpc
from iterm2 import api_pb2
from iterm2.notifications import async_subscribe_to_variable_change_notification

from agent_command_tab import iterm2_actions
from agent_command_tab.agent_process_tree import (an_agent_program_runs_through_a_shell_below,
                                                  choose_command_the_agent_is_waiting_on,
                                                  choose_detached_processes_of_the_conversation, choose_helper_process_ids,
                                                  compute_cpu_percent, list_descendant_processes, list_detached_processes,
                                                  process_has_its_terminal, process_is_alive,
                                                  process_is_stopped, read_child_processes,
                                                  read_name_the_process_was_started_as, read_process_current_folder,
                                                  read_process_info, read_process_tree_resource_usage)
from agent_command_tab.hook_event_tracking import (apply_hook_event, find_agent_process_id_of_hook_event,
                                                   find_when_the_hooks_ended_the_turn)
from agent_command_tab.hooks_installer import HOOK_EVENTS_BY_AGENT_TOOL_NAME, check_hooks
from agent_command_tab.agent_session_pausing import (continue_processes_stopped_at_pause, pause_agent_session,
                                                     pause_has_ended, resume_agent_session, send_signal_to_processes)
from agent_command_tab.pause_records_store import load_pause_records, save_pause_records
from agent_command_tab.agent_adapters import build_built_in_agent_tool_adapters
from agent_command_tab.agent_adapters.base import (COMMAND_THAT_STARTS_A_NEW_CONVERSATION, MESSAGES_THROUGH_CODEX_QUEUE,
                                                   MESSAGES_THROUGH_THE_SEND_MESSAGE_TOOL, command_line_runs_a_shell)
from agent_command_tab.agent_adapters.claude_code import ClaudeCodeAdapter, list_claude_config_folders
from agent_command_tab.agent_adapters.codex import CodexAdapter
from agent_command_tab.dashboard_core_api import ActionRegistry, ActionResult
from agent_command_tab.messages_between_agent_sessions import (SECONDS_A_MESSAGE_WAITS_FOR_AN_AGENT_AT_ITS_PROMPT,
                                                               AgentSessionForMessages,
                                                               build_message_asking_agent_session_to_talk_to_the_others)
from agent_command_tab.iterm2_snapshot_builder import DisplayedState, build_computer_snapshot, resolve_agent_sessions
from agent_command_tab.docked_window_frames_store import (adopt_records_of_restored_windows,
                                                          load_docked_window_frames, save_docked_window_frames)
from agent_command_tab.monitor_assignment import compute_frame_clear_of_area, frames_are_within_tolerance
from agent_command_tab.screen_readings import (compute_screen_fingerprint, count_lines_matching,
                                               screen_shows_permission_dialog)
from agent_command_tab.session_status_computation import (STATES_OF_A_TURN_UNDER_WAY, STATES_OF_AN_AGENT_WAITING_AT_ITS_PROMPT,
                                                          map_iterm2_status_value_to_state,
                                                          permission_dialog_was_answered,
                                                          status_values_put_the_agent_in_the_same_state,
                                                          track_status_value,
                                                          update_permission_dialog_reading)
from agent_command_tab.snapshot_data_classes import ChildProcessEntry, iterate_agent_session_entries
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
CPU_PERCENT_SHOWN_AS_IDLE = 3  # an idle agent's CPU use wavers between 0 and 2 percent
SECONDS_AN_AGENT_GETS_TO_END_BEFORE_IT_IS_KILLED = 5  # "Kill Session": SIGTERM, then SIGKILL
CONVERSATION_FILE_RETRY_SECONDS = 15  # a conversation file not found yet (Codex writes it at the first prompt)
CONVERSATION_FILE_RECHECK_SECONDS = 60  # a conversation file found: looked up again in case the conversation changed
CONVERSATION_FILE_READING_NAMES = ("context_tokens", "context_window_tokens", "conversation_preview")  # screen reading values read from it
# How long a working agent may think with nothing new on screen before its row shows "stuck"; no
# longer a setting (user request, 2026-10-26).
STUCK_THRESHOLD_SECONDS = 600
SMALLEST_WINDOW_WIDTH = 400  # a docked sidebar never makes an iTerm2 window narrower than this
FRAME_TOLERANCE_POINTS = 20  # iTerm2 rounds window sizes to whole character cells
RETRY_DELAY_SECONDS = 1
CLAUDE_AGENTS_REFRESH_INTERVAL_SECONDS = 60
CLAUDE_AGENTS_TIMEOUT_SECONDS = 5
CLAUDE_AGENTS_RETRIES_FOR_A_NEW_PROCESS = 3
CLAUDE_AGENTS_RETRY_DELAY_SECONDS = 3
SNAPSHOT_COALESCING_DELAY_SECONDS = 0.1
SESSION_VARIABLES_TO_WATCH = ("commandLine", "status")
# Claude Code's own report of a session (read_claude_code_session_status) as the iTerm2 status
# value it stands for; "shell" is a command running.
CLAUDE_CODE_SESSION_STATUSES_TO_ITERM2_STATUS_VALUES = {"idle": "idle", "busy": "working", "shell": "working",
                                                        "waiting": "waiting"}
SECONDS_TO_WAIT_FOR_THE_STATUS_IN_ITERM2S_TAB_BAR = 2
CODEX_QUEUE_TIMEOUT_SECONDS = 30
# How often "Talk to Each Other" checks whether a held message can be pasted (user request, 2026-10-21).
SECONDS_BETWEEN_CHECKS_FOR_AN_AGENT_AT_ITS_PROMPT = 3
# How long the row of an agent that ended without a normal session end stays, gray and "exited"
# (R14.1; a fixed time rather than a setting, as for "stuck").
EXITED_ROW_SECONDS = 300
CHILD_PROCESS_CHECK_INTERVAL_SECONDS = 3  # an open tab row's sub rows and the background shells (R26.2)
LONGEST_COMMAND_TEXT = 500  # characters of a sub row's command kept in the snapshot
HOOKS_CHECK_INTERVAL_SECONDS = 10  # whether the agent tools' settings hold the app's hooks
LONGEST_HOOK_EVENT_LINE = 1024 ** 2
SECONDS_TO_REACH_ANOTHER_HOOK_EVENTS_LISTENER = 0.5


def find_claude_command_path():
    """Returns the path of the `claude` command (the stable link on PATH, not a versioned file), or None."""
    return shutil.which("claude") or next(
        (path for path in [os.path.expanduser("~/.local/bin/claude")] if os.path.exists(path)), None)


def list_folders_that_may_hold_claude_code_transcripts():
    """Returns CLAUDE_CONFIG_DIR, ~/.claude, and other ~/.claude* folders with a projects folder
    (a Claude Code started with its own CLAUDE_CONFIG_DIR, for example ~/.claude_work)."""
    home_folder = os.path.expanduser("~")
    candidate_folders = ([os.environ["CLAUDE_CONFIG_DIR"]] if os.environ.get("CLAUDE_CONFIG_DIR") else []) + \
        [os.path.join(home_folder, ".claude")] + sorted(glob.glob(os.path.join(home_folder, ".claude*")))
    return [folder for folder in dict.fromkeys(candidate_folders) if os.path.isdir(os.path.join(folder, "projects"))]


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
    when it can't be read. Cheap enough to read just before typing into a session,
    while the record from `claude agents --json` may be a minute old.

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


def another_process_listens_at(socket_path):
    """Returns whether a process accepts connections on this Unix socket (not just a file left by
    a process that ended)."""
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(SECONDS_TO_REACH_ANOTHER_HOOK_EVENTS_LISTENER)
            connection.connect(socket_path)
        return True
    except OSError:  # no file, or no one listening (ConnectionRefusedError)
        return False


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


def parse_job_process_id(job_pid_value):
    """Returns iTerm2's `jobPid` variable as an int, or None."""
    try:
        return int(job_pid_value)
    except (TypeError, ValueError):
        return None


async def read_iterm2_variables(connection, names, session_id=None, tab_id=None, window_id=None):
    """Returns the values of several variables of one session, tab or window, read in a single
    request (the iterm2 package's async_get_variable sends one request per variable).

    Raises:
        iterm2.RPCException: The session, tab or window is gone.
    """
    response = await iterm2.rpc.async_variable(connection, session_id, [], names, tab_id=tab_id, window_id=window_id)
    status = response.variable_response.status
    if status != api_pb2.VariableResponse.Status.Value("OK"):
        raise iterm2.RPCException(api_pb2.VariableResponse.Status.Name(status))
    return [json.loads(value) for value in response.variable_response.values]


async def read_status_shown_in_iterm2s_tab_bar(session):
    """Returns the status iTerm2 shows for a session in its tab bar ("idle", "working", ...), from
    its built-in function iterm2.get_session_status (not in iTerm2's documentation), or None.

    After iTerm2 restarts, its tab bar still shows each tab's status, and this function returns
    it, but the session's `status` variable stays empty until the agent's next hook event (seen
    with iTerm2 3.7.6; user request, 2026-10-21).
    """
    try:
        result = await session.async_invoke_function(f'iterm2.get_session_status(session_id: "{session.session_id}")',
                                                     timeout=SECONDS_TO_WAIT_FOR_THE_STATUS_IN_ITERM2S_TAB_BAR)
    except iterm2.RPCException:
        return None
    return result.get("status") if isinstance(result, dict) else None


async def read_window_frame(window):
    """Returns an iTerm2 window's frame as (x, y, width, height)."""
    frame = await window.async_get_frame()
    return (frame.origin.x, frame.origin.y, frame.size.width, frame.size.height)


class DashboardCore:
    """The core: owns all shared state and runs on the background thread's event loop.

    Args:
        publish_snapshot: Called on the background thread with each new ComputerSnapshot.
        report_connection_lost: Called on the background thread with a message when the
            iTerm2 connection fails or closes; the app then exits so its supervisor restarts it.
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
    """

    def __init__(self, publish_snapshot, report_connection_lost, verbose=False, docked_window_frames_file_path=None,
                 pause_records_file_path=None, pause_record_by_tab_and_agent_to_start_with=None, hook_events_socket_path=None):
        self.publish_snapshot = publish_snapshot
        self.report_connection_lost = report_connection_lost
        self.verbose = verbose
        self.docked_window_frames_file_path = docked_window_frames_file_path
        self.pause_records_file_path = pause_records_file_path
        self.hook_events_socket_path = hook_events_socket_path
        # What each agent process's hooks reported (hook_event_tracking.apply_hook_event), by its
        # process ID, kept while it runs or is a tab's agent, and while its row shows "exited".
        self.hook_state_by_agent_process_id = {}
        # {session ID: (the AgentSessionEntry its row last showed, when its agent ended)} for the
        # tabs whose agent ended without a normal session end, shown as "exited" for EXITED_ROW_SECONDS (R14.1).
        self.exited_agent_sessions_by_session_id = {}
        self.last_agent_session_entry_by_session_id = {}  # from the last snapshot, for an "exited" row
        self.process_ids_ended_from_the_list = set()  # "Kill Session": their end is no crash
        self.hooks_status_per_agent_tool = ()  # ComputerSnapshot.hooks_status_per_agent_tool
        self.hooks_check_needed = asyncio.Event()
        self.connection = None
        self.app = None
        self.specific_adapters, self.generic_adapter = build_built_in_agent_tool_adapters()
        self.process_ids_asked_about_in_claude_agents = set()
        self.claude_agents_retry_count_by_process_id = {}
        self.agent_sessions = {}
        self.claude_agents_refresh_is_running = False
        self.computer_id = socket.gethostname()
        self.computer_name = socket.gethostname().split(".")[0]
        self.claude_command_path = find_claude_command_path()
        self.codex_command_path = shutil.which("codex")  # for `codex queue`; the app's settings then name it (set_core_settings)
        self.message_typing_lock_by_session_id = {}  # two held messages are never pasted into one tab at once
        self.gathered_windows = []
        self.gather_is_in_progress = False
        self.monitors = []
        self.window_order = []
        self.session_tracking_by_session_id = {}
        # Codex agent processes that shared their folder with another Codex session while they ran
        # (remember_codex_sessions_that_share_a_folder).
        self.codex_process_ids_that_shared_their_folder = set()
        # For each tab whose row goes by its own agent (choose_status_value_of_the_tabs_own_agent):
        # the status iTerm2 held at the last check while an agent program ran inside the tab's agent.
        self.iterm2_status_seen_while_an_inner_agent_ran_by_session_id = {}
        # {session ID: (the status value set in iTerm2, when the dashboard first saw it)}, so a turn
        # end its hooks report counts until iTerm2 sets another value (find_when_the_hooks_ended_the_turn).
        self.status_set_in_iterm2_and_since_by_session_id = {}
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
        self.claude_agents_by_process_id = {}
        self.key_terminal_window_id = None  # None while no terminal window (for example iTerm2's Settings) has keyboard focus
        self.last_seen_session_id = None
        self.dashboard_is_active = False
        self.last_published_snapshot_without_time = None
        self.snapshot_needed = asyncio.Event()
        self.regather_needed = asyncio.Event()
        self.screen_check_needed = asyncio.Event()
        self.conversation_files_by_session_id = {}  # build_conversation_file_record's records
        self.folders_that_may_hold_claude_code_transcripts = list_folders_that_may_hold_claude_code_transcripts()
        self.peek_lock = asyncio.Lock()  # a quick release must not undo a peek that hasn't happened yet
        self.iterm2_state_before_peek = None
        self.action_registry = ActionRegistry()
        self.register_actions()

    # ----- actions -----

    def register_actions(self):
        """Registers every action with its flags (PLAN.md section 4)."""
        register = self.action_registry.register_action
        register("focus_agent_session", self.handle_focus_agent_session, True, False)
        register("focus_window", self.handle_focus_window, True, False)
        register("rename_tab", self.handle_rename_tab, True, False)
        register("rename_window", self.handle_rename_window, True, False)
        register("move_tab", self.handle_move_tab, False, False)
        register("move_tab_to_new_window", self.handle_move_tab_to_new_window, False, False)
        register("reorder_window_list", self.handle_reorder_window_list, False, False)
        register("start_agent_session", self.handle_start_agent_session, True, False)
        register("move_window_to_monitor", self.handle_move_window_to_monitor, False, False)
        register("close_window", self.handle_close_window, False, False)
        register("duplicate_agent_session", self.handle_duplicate_agent_session, True, False)
        register("peek_agent_session", self.handle_peek_agent_session, False, False)
        register("end_peek", self.handle_end_peek, False, False)
        register("send_slash_command", self.handle_send_slash_command, True, True)
        register("send_slash_command_to_agent_sessions", self.handle_send_slash_command_to_agent_sessions, True, True)
        register("end_agent_process", self.handle_end_agent_process, False, False)
        register("end_agent_processes", self.handle_end_agent_processes, False, False)
        register("pause_agent_sessions", self.handle_pause_agent_sessions, True, True)
        register("resume_agent_sessions", self.handle_resume_agent_sessions, True, True)
        register("ask_agent_sessions_to_talk_to_each_other", self.handle_ask_agent_sessions_to_talk_to_each_other, True, True)

    async def perform_action(self, action_request):
        """Runs an action request, then asks for a fresh gather and snapshot.

        The gather itself runs in publish_when_needed, so gathers never overlap and their
        errors are handled in one place.
        """
        if self.app is None:
            return ActionResult(False, "not connected to iTerm2")
        action_result = await self.action_registry.perform_action(action_request)
        if self.verbose or not action_result.succeeded:
            print(f"action {action_request.action}: {action_result}")
        self.regather_needed.set()
        return action_result

    async def handle_focus_agent_session(self, action_request):
        return await iterm2_actions.focus_agent_session(self.app, action_request.target["session_id"])

    async def handle_focus_window(self, action_request):
        return await iterm2_actions.focus_window(self.app, action_request.target["window_id"])

    async def handle_rename_tab(self, action_request):
        return await iterm2_actions.rename_tab(self.app, action_request.target["tab_id"], action_request.arguments["new_title"])

    async def handle_rename_window(self, action_request):
        return await iterm2_actions.rename_window(self.app, action_request.target["window_id"],
                                                  action_request.arguments["new_title"])

    async def handle_move_tab(self, action_request):
        return await iterm2_actions.move_tab_to_window_position(
            self.app, action_request.target["tab_id"], action_request.arguments["target_window_id"],
            action_request.arguments.get("drop_before_tab_id"))

    async def handle_move_tab_to_new_window(self, action_request):
        return await iterm2_actions.move_tab_to_new_window(self.app, action_request.target["tab_id"])

    async def handle_start_agent_session(self, action_request):
        """Target {"window_id": ...} opens a tab there; an empty target opens a new window."""
        return await iterm2_actions.start_agent_session(
            self.connection, self.app, action_request.target.get("window_id"), action_request.arguments["folder"],
            action_request.arguments["launch_command"])

    async def handle_send_slash_command(self, action_request):
        """Types /compact or /clear into an agent session that waits at its prompt, idle or done
        as its row shows it (R15.1; iterm2_actions.type_slash_command checks its input box)."""
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
        """Types the command (iterm2_actions.type_slash_command: None when nothing was typed, the
        input box holding unsent text or being off screen). A /clear typed at the prompt forgets
        the cleared conversation at once; one the agent queues during its turn runs only when the
        turn ends, so its new conversation is found as after a /clear typed by hand (the
        `claude agents` refresh)."""
        clears_at_once = command == COMMAND_THAT_STARTS_A_NEW_CONVERSATION and waits_at_its_prompt
        # Known before the conversation ends, since a lookup after /clear can't tell old from new.
        path_of_conversation_to_clear = await self.find_current_conversation_file(session_id, agent_session) \
            if clears_at_once else None
        action_result = await iterm2_actions.type_slash_command(
            self.app, session_id, command, agent_session["adapter"], agent_is_in_the_middle_of_a_turn=not waits_at_its_prompt)
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
        agent_process_id = agent_session["process_id"]
        deadline = time.time() + SECONDS_A_MESSAGE_WAITS_FOR_AN_AGENT_AT_ITS_PROMPT
        typing_lock = self.message_typing_lock_by_session_id.setdefault(session_id, asyncio.Lock())
        while True:
            # Checked holding the lock: a held message or command may have just been typed, starting a turn.
            async with typing_lock:
                agent_session = self.agent_sessions.get(session_id)
                if agent_session is None or agent_session["process_id"] != agent_process_id:
                    return ActionResult(False, f"its agent ended before {command} could be typed")
                try:
                    waits_at_its_prompt = (self.agent_waits_at_its_prompt(session_id, agent_session)
                                           or await self.agent_whose_state_is_unknown_shows_no_turn_on_screen(session_id, agent_session))
                    if not agent_session["is_in_background"] and (
                            waits_at_its_prompt or agent_session["adapter"].queues_slash_commands_typed_during_a_turn):
                        action_result = await self.type_slash_command_into_agent_session(
                            session_id, agent_session, command, waits_at_its_prompt)
                        if action_result is not None:
                            return action_result
                except iterm2.RPCException:
                    return ActionResult(False, "the tab closed meanwhile")
            if time.time() >= deadline:
                return ActionResult(False, "it didn't wait at its prompt with an empty input box within "
                                           f"{SECONDS_A_MESSAGE_WAITS_FOR_AN_AGENT_AT_ITS_PROMPT // 60} minutes, "
                                           f"so {command} wasn't typed")
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
        """Whether the agent session waits at its prompt: its row shows idle or done, and Claude
        Code doesn't report it busy."""
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
        session = self.app.get_session_by_id(session_id)
        if working_screen_pattern is None or session is None or (displayed_state is not None and displayed_state.state != "unknown"):
            return False
        return count_lines_matching([text for text, _ in await iterm2_actions.read_screen_lines_with_faint_flags(session)],
                                    working_screen_pattern) == 0

    def claude_code_reports_the_session_busy(self, session_id, agent_session):
        """Whether Claude Code itself reports the agent session as anything but idle right now
        (read_claude_code_session_status). Checked just before typing into it: a row may take its
        idle state from a `claude agents --json` record up to a minute old, and Claude Code's input
        box is empty during a turn too. False when Claude Code reports nothing (another tool). Its
        "shell" while a background task runs after the turn ended, as its hooks report
        (find_when_the_hooks_ended_the_turn), counts as at the prompt: its input box takes text
        then (code review, 2026-10-27)."""
        claude_agent_record = self.claude_agents_by_process_id.get(agent_session["process_id"])
        if claude_agent_record is None:
            return False
        status = read_claude_code_session_status(claude_agent_record.get("config_folder"), agent_session["process_id"])
        if status == "shell" and find_when_the_hooks_ended_the_turn(
                self.hook_state_by_agent_process_id.get(agent_session["process_id"]),
                self.status_set_in_iterm2_and_since_by_session_id.get(session_id, (None, float("inf")))[1]) is not None:
            return False
        return status is not None and status != "idle"

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

    async def handle_pause_agent_sessions(self, action_request):
        """Pauses the agent sessions of target {"session_ids": [...]}: each agent as Ctrl+Z does,
        and every process below it (agent_session_pausing; user request, 2026-10-20)."""

        async def pause_one(session_id, agent_session, session):
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
            action_result, pause_record = await pause_agent_session(session, agent_process_id, agent_session["agent_tool_name"])
            if pause_record is not None:
                self.pause_record_by_tab_and_agent[(session_id, agent_process_id)] = pause_record
                self.save_pause_records()
            return action_result
        return await self.pause_or_resume_each_agent_session(action_request.target["session_ids"], pause_one)

    async def handle_resume_agent_sessions(self, action_request):
        """Resumes the paused agent sessions of target {"session_ids": [...]}; one suspended with
        Ctrl+Z by hand gets an explanation instead, since no pause recorded its prompt."""

        async def resume_one(session_id, agent_session, session):
            tab_and_agent = (session_id, agent_session["process_id"])
            action_result = await resume_agent_session(session, agent_session["process_id"],
                                                       self.pause_record_by_tab_and_agent.get(tab_and_agent))
            if action_result.succeeded and self.pause_record_by_tab_and_agent.pop(tab_and_agent, None) is not None:
                self.save_pause_records()
            return action_result
        return await self.pause_or_resume_each_agent_session(action_request.target["session_ids"], resume_one)

    async def pause_or_resume_each_agent_session(self, session_ids, pause_or_resume_one):
        """Runs pause_or_resume_one(session_id, agent_session, session) for every session at once;
        returns one ActionResult whose message names each tab that failed and why."""

        async def run_for(session_id):
            agent_session = self.agent_sessions.get(session_id)
            session = self.app.get_session_by_id(session_id)
            if agent_session is None or not agent_session["process_id"] or session is None:
                return ActionResult(False, "that tab no longer runs an agent")
            if session_id in self.session_ids_being_paused_or_resumed:
                # A second click while the first is under way (both could see the same empty prompt and type fg twice).
                return ActionResult(True)
            self.session_ids_being_paused_or_resumed.add(session_id)
            try:
                return await pause_or_resume_one(session_id, agent_session, session)
            except iterm2.RPCException:
                return ActionResult(False, "the tab closed meanwhile")
            finally:
                self.session_ids_being_paused_or_resumed.discard(session_id)
        action_results = await asyncio.gather(*(run_for(session_id) for session_id in session_ids))
        return self.combine_results_naming_the_tabs_that_failed(session_ids, action_results)

    # ----- Talk to Each Other (user request, 2026-10-21) -----

    async def handle_ask_agent_sessions_to_talk_to_each_other(self, action_request):
        """Sends each agent session of target {"session_ids": [...]} the note of arguments
        {"note": ...}, followed by what it needs to send each of the others a message
        (messages_between_agent_sessions). A Codex session gets it through `codex queue`, which it
        runs after any turn under way. A Claude Code session has no supported way in from outside,
        so its message is held until the session waits at its prompt with an empty input box, then
        pasted there, giving up after SECONDS_A_MESSAGE_WAITS_FOR_AN_AGENT_AT_ITS_PROMPT. Returns
        once every message was delivered or given up on, naming each tab that didn't get one."""
        session_ids = action_request.target["session_ids"]
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
            participant_session_ids = list(agent_session_for_messages_by_session_id)
            delivery_results = await asyncio.gather(*(
                self.deliver_message_to_agent_session(
                    session_id, agent_session_for_messages_by_session_id[session_id],
                    build_message_asking_agent_session_to_talk_to_the_others(
                        action_request.arguments["note"], agent_session_for_messages_by_session_id[session_id],
                        [other for other_session_id, other in agent_session_for_messages_by_session_id.items()
                         if other_session_id != session_id]))
                for session_id in participant_session_ids), return_exceptions=True)
            for session_id, delivery_result in zip(participant_session_ids, delivery_results):
                action_result_by_session_id[session_id] = convert_exception_to_action_result(delivery_result)
        return self.combine_results_naming_the_tabs_that_failed(
            session_ids, [action_result_by_session_id[session_id] for session_id in session_ids])

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
        return AgentSessionForMessages(agent_session["agent_tool_name"], how_other_agent_sessions_send_it_messages, address,
                                       process_id, agent_session["working_folder"],
                                       self.find_tab_title_of_agent_session(session_id)), None

    async def deliver_message_to_agent_session(self, session_id, agent_session_for_messages, message):
        """Queues the message for a Codex session, or pastes it into a Claude Code session once it waits at its prompt."""
        if agent_session_for_messages.how_other_agent_sessions_send_it_messages == MESSAGES_THROUGH_CODEX_QUEUE:
            return await queue_message_for_codex_session(self.codex_command_path, agent_session_for_messages.address, message)
        return await self.paste_message_once_the_agent_waits_at_its_prompt(session_id, agent_session_for_messages.process_id, message)

    async def paste_message_once_the_agent_waits_at_its_prompt(self, session_id, agent_process_id, message):
        """Checks every SECONDS_BETWEEN_CHECKS_FOR_AN_AGENT_AT_ITS_PROMPT until the agent session
        waits at its prompt (its row shows idle or done) with an empty input box, then pastes the
        message there and presses Enter. Gives up after
        SECONDS_A_MESSAGE_WAITS_FOR_AN_AGENT_AT_ITS_PROMPT, or once the tab's agent process is no
        longer agent_process_id."""
        deadline = time.time() + SECONDS_A_MESSAGE_WAITS_FOR_AN_AGENT_AT_ITS_PROMPT
        typing_lock = self.message_typing_lock_by_session_id.setdefault(session_id, asyncio.Lock())
        while True:
            # Checked holding the lock: another message held for this session may have just
            # been pasted, starting a turn.
            async with typing_lock:
                agent_session = self.agent_sessions.get(session_id)
                if agent_session is None or agent_session["process_id"] != agent_process_id:
                    return ActionResult(False, "its agent ended before the message could be pasted")
                if not agent_session["is_in_background"] and self.agent_waits_at_its_prompt(session_id, agent_session):
                    try:
                        action_result = await iterm2_actions.paste_message_into_empty_input_box(
                            self.app, session_id, message, agent_session["adapter"])
                    except iterm2.RPCException:
                        return ActionResult(False, "the tab closed meanwhile")
                    if action_result is not None:
                        return action_result
            if time.time() >= deadline:
                return ActionResult(False, "it didn't wait at its prompt with an empty input box within "
                                           f"{SECONDS_A_MESSAGE_WAITS_FOR_AN_AGENT_AT_ITS_PROMPT // 60} minutes, "
                                           "so the message wasn't pasted")
            await asyncio.sleep(SECONDS_BETWEEN_CHECKS_FOR_AN_AGENT_AT_ITS_PROMPT)

    def set_reserved_screen_area(self, reserved_area):
        """Receives the docked sidebar's area, {"frame": (x, y, width, height), "edge": "right" or
        "left", "monitor_visible_frame": (...)}, or None when docking ends; kept until iTerm2 is
        connected if it arrives earlier."""
        asyncio.ensure_future(self.change_reserved_screen_area(reserved_area))

    async def change_reserved_screen_area(self, new_reserved_area):
        """Gives the room back for the old area, then makes room for the new one, holding the
        lock so a regather can't resize windows in between."""
        async with self.window_room_lock:
            self.reserved_area_was_received = True
            if new_reserved_area == self.reserved_screen_area:
                if new_reserved_area is None and self.app is not None and self.frames_before_making_room_by_window_id:
                    await self.give_room_back_while_holding_the_lock()  # restores still pending from before
                return
            if self.app is not None:
                await self.give_room_back_while_holding_the_lock()  # both handle each window's errors themselves
            self.reserved_screen_area = new_reserved_area
            if self.app is not None:
                await self.make_room_while_holding_the_lock()

    async def make_room_for_reserved_area(self):
        """After each gather: while docked, narrows (or moves) iTerm2 windows that reach into the
        reserved area; while not, retries restores still pending (after a restart with docking
        off, or a window that was full screen when docking ended). Pending restores wait until
        the main thread has said whether the sidebar is docked, so a docked start doesn't widen
        windows only to narrow them again."""
        async with self.window_room_lock:
            if self.reserved_screen_area is not None:
                await self.make_room_while_holding_the_lock()
            elif self.reserved_area_was_received and self.frames_before_making_room_by_window_id:
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
        for gathered_window in self.gathered_windows:
            window = self.app.get_window_by_id(gathered_window["window_id"])
            if window is None:
                continue
            try:
                current_frame = await read_window_frame(window)
                gathered_window["frame"] = current_frame
                new_frame = compute_frame_clear_of_area(
                    current_frame, self.reserved_screen_area["frame"], self.reserved_screen_area["edge"],
                    self.reserved_screen_area["monitor_visible_frame"], SMALLEST_WINDOW_WIDTH)
                if new_frame is None or await window.async_get_fullscreen():
                    continue
                original_frame, frame_that_was_set = self.frames_before_making_room_by_window_id.get(
                    window.window_id, (current_frame, None))
                if frame_that_was_set is not None and not frames_are_within_tolerance(
                        current_frame, frame_that_was_set, FRAME_TOLERANCE_POINTS):
                    original_frame = current_frame  # moved or resized by hand since
                await window.async_set_frame(iterm2.Frame(iterm2.Point(new_frame[0], new_frame[1]),
                                                          iterm2.Size(new_frame[2], new_frame[3])))
                self.frames_before_making_room_by_window_id[window.window_id] = (original_frame, new_frame)
                gathered_window["frame"] = new_frame
            except (iterm2.RPCException, iterm2.GetPropertyException, iterm2.SetPropertyException) as error:
                print(f"could not make room for the docked sidebar in window {gathered_window['window_id']}: {error!r}")
        self.save_docked_window_frames()

    async def give_room_back_while_holding_the_lock(self):
        """Restores the windows narrowed for a docked sidebar, unless the user moved or resized
        them since, and updates the cached frames so the next layout starts from the real ones.

        A window that is full screen, or whose restore fails, stays remembered, so it can still
        be restored when a later docking ends. Records of windows that are gone are dropped.
        """
        self.adopt_records_of_restored_windows()
        gathered_windows_by_window_id = {gathered_window["window_id"]: gathered_window for gathered_window in self.gathered_windows}
        for window_id, (original_frame, frame_that_was_set) in list(self.frames_before_making_room_by_window_id.items()):
            window = self.app.get_window_by_id(window_id)
            if window is None:
                del self.frames_before_making_room_by_window_id[window_id]
                continue
            try:
                if await window.async_get_fullscreen():
                    continue  # its own desktop shows a full-screen frame; it returns to the narrowed one later
                current_frame = await read_window_frame(window)
                if frames_are_within_tolerance(current_frame, frame_that_was_set, FRAME_TOLERANCE_POINTS):
                    await window.async_set_frame(iterm2.Frame(iterm2.Point(original_frame[0], original_frame[1]),
                                                              iterm2.Size(original_frame[2], original_frame[3])))
                    current_frame = original_frame
            except (iterm2.RPCException, iterm2.GetPropertyException, iterm2.SetPropertyException) as error:
                print(f"could not give window {window_id} its room back: {error!r}")
                continue
            del self.frames_before_making_room_by_window_id[window_id]
            if window_id in gathered_windows_by_window_id:
                gathered_windows_by_window_id[window_id]["frame"] = current_frame
        self.save_docked_window_frames()

    def adopt_records_of_restored_windows(self):
        """On the first pass after start-up, moves frames remembered from the file for windows
        that are gone to windows iTerm2 restored after a restart, which get new IDs but keep the
        narrowed frame. Later, the records of windows that closed are dropped instead, so a new
        window of the same size never takes over a closed window's record."""
        if not self.gathered_windows:
            return
        if self.records_loaded_from_file_await_adoption:
            self.records_loaded_from_file_await_adoption = False
            self.frames_before_making_room_by_window_id = adopt_records_of_restored_windows(
                self.frames_before_making_room_by_window_id,
                {gathered_window["window_id"]: gathered_window["frame"] for gathered_window in self.gathered_windows},
                FRAME_TOLERANCE_POINTS)
        else:
            self.frames_before_making_room_by_window_id = {
                window_id: frames for window_id, frames in self.frames_before_making_room_by_window_id.items()
                if self.app.get_window_by_id(window_id) is not None}

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
        """Press and hold on a tab row: shows the tab, first remembering what to put back."""
        async with self.peek_lock:
            current_window = self.app.current_terminal_window
            self.iterm2_state_before_peek = {
                # The App's current window, not the key window: while the dropdown is open the
                # dashboard is active and no iTerm2 window is key.
                "key_window_id": current_window.window_id if current_window is not None else None,
                "selected_tab_id_by_window_id": {window.window_id: window.current_tab.tab_id
                                                 for window in self.app.terminal_windows if window.current_tab is not None},
            }
            return await iterm2_actions.focus_agent_session(self.app, action_request.target["session_id"])

    async def handle_end_peek(self, action_request):
        """The button was released: arguments {"bring_back_iterm2_window": bool}, true when iTerm2
        was in front before the peek (otherwise the main thread brings back the app that was)."""
        async with self.peek_lock:
            state, self.iterm2_state_before_peek = self.iterm2_state_before_peek, None
            if state is None:
                return ActionResult(False, "no peek to end")
            return await iterm2_actions.put_back_tabs_and_window_after_peek(
                self.app, state["selected_tab_id_by_window_id"],
                state["key_window_id"] if action_request.arguments["bring_back_iterm2_window"] else None)

    async def handle_duplicate_agent_session(self, action_request):
        """Target {"tab_id": ...}; arguments {"folder": ..., "launch_command": ...}. The copy continues
        a copy of the conversation when the agent tool can fork it, else it starts fresh."""
        session_id = self.find_first_session_id_of_tab(action_request.target["tab_id"])
        agent_session = self.agent_sessions.get(session_id)
        if agent_session is None:
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
        action_result = await iterm2_actions.duplicate_agent_session(
            self.connection, self.app, action_request.target["tab_id"], action_request.arguments["folder"],
            launch_command_for_the_copy)
        if action_result.succeeded and conversation_is_shared_with_another_codex_session:
            return ActionResult(True, "another Codex session runs or ran in the same folder, so its conversation can't be told apart, "
                                      "and the copy starts a new conversation")
        if action_result.succeeded and not conversation_id:
            return ActionResult(True, "the conversation's ID wasn't found, so the copy starts a new conversation")
        return action_result

    async def handle_close_window(self, action_request):
        return await iterm2_actions.close_window(self.app, action_request.target["window_id"])

    async def handle_move_window_to_monitor(self, action_request):
        return await iterm2_actions.move_window_to_monitor(
            self.app, action_request.target["window_id"], self.monitors, action_request.arguments["monitor_id"])

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
        self.resource_usage_is_read = core_settings["resource_usage_is_read"]
        self.codex_command_path = core_settings["codex_command_path"]
        self.snapshot_needed.set()

    def set_dashboard_is_active(self, dashboard_is_active):
        """Receives whether the dashboard itself is the active app (then nothing counts as seen)."""
        self.dashboard_is_active = dashboard_is_active
        self.record_seen_time_for_the_seen_session()
        self.snapshot_needed.set()

    # ----- gathering -----

    async def gather_iterm2_tab(self, tab):
        """Reads one tab and its first-pane session into a plain dict, one request for each."""
        first_session = tab.sessions[0] if tab.sessions else None
        tab_variables_reading = read_iterm2_variables(self.connection, ["title", "titleOverrideFormat"], tab_id=tab.tab_id)
        gathered_session = None
        if first_session is None:
            title, title_override_format = await tab_variables_reading
        else:
            (title, title_override_format), (job_pid, command_line, status, path) = await asyncio.gather(
                tab_variables_reading, read_iterm2_variables(self.connection, ["jobPid", "commandLine", "status", "path"],
                                                             session_id=first_session.session_id))
            if not status and command_line and not command_line_runs_a_shell(command_line):
                # As after iTerm2 restarts: the status its tab bar shows (the variable then takes over).
                status = await read_status_shown_in_iterm2s_tab_bar(first_session)
            job_process_id = parse_job_process_id(job_pid)
            job_process = read_process_info(job_process_id) if job_process_id else None
            gathered_session = {
                "session_id": first_session.session_id,
                "command_line": command_line,
                "job_pid": job_process_id,
                "job_folder": read_process_current_folder(job_process_id),
                "job_started_at": job_process.started_at if job_process is not None else None,
                "status_set_in_iterm2": status,
                "status": status,  # the one the row goes by (update_session_tracking)
                "path": path or "",
            }
        return {
            "tab_id": tab.tab_id,
            "title": title or "",
            "title_override_format": title_override_format,
            "first_session": gathered_session,
        }

    async def gather_iterm2_window(self, window):
        """Reads one window and its tabs into a plain dict; a tab that closes meanwhile is left
        out (and another gather is requested). Raises if the window itself is gone."""

        async def gather_tab_unless_it_closed(tab):
            try:
                return await self.gather_iterm2_tab(tab)
            except iterm2.RPCException:
                self.regather_needed.set()
                return None

        frame, (title, title_override_format), *gathered_tabs = await asyncio.gather(
            read_window_frame(window),
            read_iterm2_variables(self.connection, ["title", "titleOverrideFormat"], window_id=window.window_id),
            *(gather_tab_unless_it_closed(tab) for tab in window.tabs))
        return {
            "window_id": window.window_id,
            "frame": frame,
            "title": title or "",
            "title_override_format": title_override_format,
            "tabs": [gathered_tab for gathered_tab in gathered_tabs if gathered_tab is not None],
        }

    async def gather_iterm2_windows(self):
        """Reads every window, tab and first-pane session that the snapshot needs into plain dicts.

        All windows and tabs are read at the same time, so a gather takes about as long as one
        round trip to iTerm2 rather than one per tab (20 windows with 100 tabs stay quick). A
        window or tab that closes while it is being read makes iTerm2 answer with an error; it
        is skipped, and another gather is requested so the list catches up.
        """

        async def gather_window_unless_it_closed(window):
            try:
                return await self.gather_iterm2_window(window)
            except (iterm2.RPCException, iterm2.GetPropertyException):
                self.regather_needed.set()
                return None

        gathered_windows = await asyncio.gather(*(gather_window_unless_it_closed(window) for window in self.app.terminal_windows))
        return [gathered_window for gathered_window in gathered_windows if gathered_window is not None]

    def update_session_tracking(self, now):
        """Records when each session's status value changed and when its turns start and end, and
        forgets sessions that are gone. A turn end the tab agent's hooks report counts while
        iTerm2's value still says working (find_when_the_hooks_ended_the_turn)."""
        live_session_ids = set()
        for gathered_window in self.gathered_windows:
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
                    status_set_in_iterm2_and_since = (first_session["status_set_in_iterm2"], now)
                    self.status_set_in_iterm2_and_since_by_session_id[session_id] = status_set_in_iterm2_and_since
                first_session["status"] = self.choose_status_value_of_the_tabs_own_agent(
                    session_id, first_session, previous_tracking, status_set_in_iterm2_changed)
                if (agent_session is not None and map_iterm2_status_value_to_state(first_session["status"]) == "working"
                        and find_when_the_hooks_ended_the_turn(self.hook_state_by_agent_process_id.get(agent_session["process_id"]),
                                                               status_set_in_iterm2_and_since[1]) is not None):
                    first_session["status"] = "idle"  # its turn ended, though iTerm2 still says working while a background task runs
                tracking = track_status_value(previous_tracking, first_session["status"], now)
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

    def choose_status_value_of_the_tabs_own_agent(self, session_id, first_session, previous_tracking, status_set_in_iterm2_changed):
        """Returns the status value a tab's row goes by: the one set in iTerm2, except while an agent
        program runs inside the tab's agent (a `claude -p` its shell tool started, waited on or left
        running in the background), and after it ends until iTerm2's value fits the tab's agent
        again. That agent fires the same user-level hooks, so it sets the tab's status too, and its
        last value stays until the tab's agent sets another (Phase 0; PLAN.md section 6).
        Meanwhile the row goes by the tab's own agent (read_status_value_of_the_tabs_own_agent)
        and turns back to iTerm2's value once the two agree; for a tool without such a report, it
        keeps its value until iTerm2's changes after the inner agent ended. The agent's processes
        are listed only when the value set in iTerm2 changed since the last check
        (status_set_in_iterm2_changed: not the value the row went by, which a turn end its hooks
        report can make idle), and while the row goes by its own agent."""
        status_set_in_iterm2 = first_session["status_set_in_iterm2"]
        agent_session = self.agent_sessions.get(session_id)
        status_seen_while_an_inner_agent_ran = self.iterm2_status_seen_while_an_inner_agent_ran_by_session_id
        if previous_tracking is None or agent_session is None or not agent_session["process_id"]:
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
        return own_status_value or previous_tracking["status_value"]

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

    def find_front_tab_id(self):
        """Returns the selected tab of iTerm2's current terminal window, as the iterm2 App object
        tracks it from focus notifications (it ignores windows resigning focus), or None."""
        current_window = self.app.current_terminal_window
        if current_window is None or current_window.current_tab is None:
            return None
        return current_window.current_tab.tab_id

    def find_seen_session_id(self):
        """Returns the session being seen: the front tab's, while iTerm2 (not the dashboard) is active
        and a terminal window (not, for example, iTerm2's Settings) has keyboard focus."""
        if self.app is None or not self.app.app_active or self.key_terminal_window_id is None or self.dashboard_is_active:
            return None  # (the main thread can report the dashboard's activity before iTerm2 is connected)
        return self.find_first_session_id_of_tab(self.find_front_tab_id())

    def find_highlighted_session_id(self):
        """Returns the session shown as in front (R41): the front tab's while it is seen, and also
        while the dashboard itself is active (so the highlight doesn't vanish while you use the
        list); None while another app is active."""
        if self.dashboard_is_active or self.find_seen_session_id() is not None:
            return self.find_first_session_id_of_tab(self.find_front_tab_id())
        return None

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
        """Re-reads iTerm2 and publishes a snapshot if anything changed."""
        await self.app.async_refresh()
        self.gather_is_in_progress = True
        try:
            self.gathered_windows = await self.gather_iterm2_windows()
        finally:
            self.gather_is_in_progress = False
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
        if self.pause_records_file_path:
            save_pause_records(self.pause_records_file_path, self.pause_record_by_tab_and_agent)

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
        """Keeps an "exited" row (R14.1) for each tab, still open, whose agent process is gone
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
        for process_id in list(self.hook_state_by_agent_process_id):
            if process_id not in kept_process_ids and not process_is_alive(process_id):
                del self.hook_state_by_agent_process_id[process_id]
        self.process_ids_ended_from_the_list = {process_id for process_id in self.process_ids_ended_from_the_list
                                                if process_id in kept_process_ids or process_is_alive(process_id)}

    def note_background_task_counts(self):
        """Sets each agent session's background task count (R26.2): Claude Code's own list of its
        background shells and agents while its hooks have reported it (hook_event_tracking), plus
        the detached jobs it started, which that list doesn't hold (user request, 2026-10-28),
        otherwise the background shells seen below the agent and its detached jobs
        (read_child_processes_periodically)."""
        for session_id, agent_session in self.agent_sessions.items():
            reading = self.screen_readings_by_session_id.setdefault(session_id, {})
            hook_state = self.hook_state_by_agent_process_id.get(agent_session["process_id"])
            reported_background_task_ids = hook_state["background_task_ids"] if hook_state is not None else None
            reading["background_task_count"] = (
                len(reported_background_task_ids) + reading.get("detached_process_count", 0)
                if reported_background_task_ids is not None else reading.get("background_shell_count", 0))

    def publish_snapshot_if_changed(self):
        """Builds the snapshot from the cached state and publishes it unless only its time changed."""
        now = time.time()
        self.update_session_tracking(now)
        self.record_seen_time_for_the_seen_session()
        self.window_order = update_window_list_order(
            self.window_order, [gathered_window["window_id"] for gathered_window in self.gathered_windows])
        for gathered_window in self.gathered_windows:  # the App object follows tab switches itself, without a regather
            window = self.app.get_window_by_id(gathered_window["window_id"])
            gathered_window["selected_tab_id"] = window.current_tab.tab_id if window and window.current_tab else None
        agent_sessions_before = self.agent_sessions
        self.agent_sessions = resolve_agent_sessions(
            self.gathered_windows, self.agent_sessions, self.specific_adapters, self.generic_adapter, process_is_alive,
            process_is_stopped)
        self.list_again_the_paused_agents_that_lost_their_row()
        self.remember_codex_sessions_that_share_a_folder()
        self.continue_the_processes_of_agents_no_longer_paused()
        self.note_agents_that_ended_without_a_session_end(agent_sessions_before, now)
        self.forget_agent_processes_that_are_gone()
        self.note_background_task_counts()
        seen_session_id, highlighted_session_id = self.find_seen_session_id(), self.find_highlighted_session_id()

        def build_snapshot_of(agent_sessions, exited_agent_sessions_by_session_id):
            return build_computer_snapshot(
                self.gathered_windows, agent_sessions, self.monitors, self.window_order, self.session_tracking_by_session_id,
                self.displayed_state_by_session_id, self.screen_readings_by_session_id, STUCK_THRESHOLD_SECONDS,
                self.claude_agents_by_process_id, seen_session_id, highlighted_session_id, self.computer_id,
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

    # ----- subscriptions -----

    async def handle_session_variable_change(self, connection, notification):
        """Updates the cached commandLine or status of one session, then asks for a snapshot.

        A change that arrives during a gather would be written into the list that the gather
        is about to replace, so it asks for another gather instead.
        """
        if self.gather_is_in_progress:
            self.regather_needed.set()
            return
        new_value = json.loads(notification.json_new_value) if notification.json_new_value else None
        field_name = {"commandLine": "command_line", "status": "status_set_in_iterm2"}[notification.name]
        for gathered_window in self.gathered_windows:
            for gathered_tab in gathered_window["tabs"]:
                first_session = gathered_tab["first_session"]
                if first_session and first_session["session_id"] == notification.identifier:
                    first_session[field_name] = new_value
                    if field_name == "command_line":
                        self.regather_needed.set()  # the job process ID and folder change with the job
        self.snapshot_needed.set()

    async def listen_for_hook_events(self):
        """Receives the app's hook events (hook_event_forwarder), one line per connection, on the
        socket at hook_events_socket_path, taken over from an earlier run. While another copy of the
        app (a live check's, say) receives them there, or if the socket can't be made, this one
        goes on without hook events (this task must not end: that ends the core)."""
        if another_process_listens_at(self.hook_events_socket_path):
            print(f"another copy of the app receives the hook events at {self.hook_events_socket_path}; this one goes on without them")
            await asyncio.Event().wait()
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
        if hook_state["session_ended_at"] is not None:
            for session_id, exited_agent_session in list(self.exited_agent_sessions_by_session_id.items()):
                if exited_agent_session["process_id"] == process_id:
                    del self.exited_agent_sessions_by_session_id[session_id]
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

    async def watch_layout_changes(self):
        async with iterm2.LayoutChangeMonitor(self.connection) as monitor:
            while True:
                await monitor.async_get()
                self.regather_needed.set()

    async def watch_new_sessions(self):
        async with iterm2.NewSessionMonitor(self.connection) as monitor:
            while True:
                await monitor.async_get()
                self.regather_needed.set()

    async def watch_session_terminations(self):
        async with iterm2.SessionTerminationMonitor(self.connection) as monitor:
            while True:
                await monitor.async_get()
                self.regather_needed.set()

    async def watch_focus_changes(self):
        """Tracks which terminal window has keyboard focus, and asks for a snapshot on any focus change.

        The front tab and whether iTerm2 is active come from the iterm2 App object, which handles
        the same notifications itself; the snapshot is built after the coalescing delay, by which
        time it has.
        """
        async with iterm2.FocusMonitor(self.connection) as monitor:
            while True:
                focus_update = await monitor.async_get_next_update()
                if focus_update.window_changed is not None:
                    self.update_key_terminal_window(focus_update.window_changed)
                self.snapshot_needed.set()

    def update_key_terminal_window(self, window_changed):
        """Tracks which terminal window has keyboard focus, whatever order iTerm2 reports the
        old window resigning and the new one becoming key in."""
        reasons = iterm2.FocusUpdateWindowChanged.Reason
        if window_changed.event == reasons.TERMINAL_WINDOW_BECAME_KEY:
            self.key_terminal_window_id = window_changed.window_id
        elif window_changed.event == reasons.TERMINAL_WINDOW_IS_CURRENT:
            self.key_terminal_window_id = None  # a non-terminal window such as Settings has keyboard focus
        elif window_changed.event == reasons.TERMINAL_WINDOW_RESIGNED_KEY and self.key_terminal_window_id == window_changed.window_id:
            self.key_terminal_window_id = None

    async def read_agent_screens_periodically(self):
        """Reads the screens the status rules need (PLAN.md section 6): the permission dialog of an
        asking session whose tool needs the check (every SCREEN_CHECK_INTERVAL_SECONDS), and a
        fingerprint of each working session (every FINGERPRINT_INTERVAL_SECONDS) for "stuck".
        One screen read serves both. A session that starts asking is read at once, so the count of
        "Interrupted" lines from before the user could answer is known."""
        while True:
            try:
                await asyncio.wait_for(self.screen_check_needed.wait(), SCREEN_CHECK_INTERVAL_SECONDS)
            except asyncio.TimeoutError:
                pass
            self.screen_check_needed.clear()
            status_by_session_id = {gathered_tab["first_session"]["session_id"]: gathered_tab["first_session"]["status"]
                                    for gathered_window in self.gathered_windows for gathered_tab in gathered_window["tabs"]
                                    if gathered_tab["first_session"]}
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
                # A row that goes by its own agent while an agent runs inside it (choose_status_value_of_the_tabs_own_agent).
                needs_turn_check = (session_id in self.iterm2_status_seen_while_an_inner_agent_ran_by_session_id
                                    and adapter.working_screen_pattern is not None)
                if not (needs_dialog_check or needs_fingerprint or needs_turn_check):
                    continue
                session = self.app.get_session_by_id(session_id)
                if session is None:
                    continue
                try:
                    contents = await session.async_get_screen_contents()
                except iterm2.RPCException:
                    continue  # the tab closed meanwhile
                screen_lines = [contents.line(index).string for index in range(contents.number_of_lines)]
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
        return await agent_session["adapter"].find_conversation_file(
            process_id, self.claude_agents_by_process_id.get(process_id) or {}, self.folders_that_may_hold_claude_code_transcripts)

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
        session's row and the snapshot don't change at every reading."""
        cpu_time_readings_by_session_id = {}  # (process ID, CPU seconds, read at) from the last reading
        while True:
            await asyncio.sleep(RESOURCE_USAGE_CHECK_INTERVAL_SECONDS)
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
                    cpu_time_readings_by_session_id[session_id] = (process_id, usage[1], now)
                    memory_bytes, cpu_percent = round_memory_and_cpu_use(usage[0], cpu_percent)
                self.note_reading_value(session_id, "memory_bytes", memory_bytes)
                self.note_reading_value(session_id, "cpu_percent", cpu_percent)

    async def read_child_processes_periodically(self):
        """Keeps each agent session's child processes ("child_processes", the sub rows of its open
        tab row; user request, 2026-10-27) and its background shells ("background_shell_count",
        R26.2) current, every CHILD_PROCESS_CHECK_INTERVAL_SECONDS, in a worker thread (a few
        system calls per process, no subprocess). While the Memory or CPU column shows, each
        child's use is read too, rounded as the agent's is (read_resource_usage_periodically)."""
        cpu_time_readings_by_child = {}  # {(process ID, started at): (CPU seconds, read at)} from the last reading
        while True:  # read at once, then every CHILD_PROCESS_CHECK_INTERVAL_SECONDS (dump-snapshot shows them too)
            reads_resource_usage = self.resource_usage_is_read
            # Whether each agent is in a turn, by the status value its row goes by, and since when.
            status_value_by_session_id = {gathered_tab["first_session"]["session_id"]: gathered_tab["first_session"]["status"]
                                          for gathered_window in self.gathered_windows for gathered_tab in gathered_window["tabs"]
                                          if gathered_tab["first_session"]}
            # Also the Claude Code conversation's ID and the agent's start, for its detached processes.
            agent_process_and_turn_by_session_id = {
                session_id: (agent_session["process_id"],
                             map_iterm2_status_value_to_state(status_value_by_session_id.get(session_id)) in STATES_OF_A_TURN_UNDER_WAY,
                             self.session_tracking_by_session_id.get(session_id, {}).get("turn_started_at"),
                             (self.claude_agents_by_process_id.get(agent_session["process_id"]) or {}).get("sessionId"),
                             agent_session.get("started_at"))
                for session_id, agent_session in self.agent_sessions.items() if agent_session["process_id"]}

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
                agent_session["adapter"].helper_process_names, now, reading.get("helper_process_ids", frozenset()))
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

    async def handle_title_change(self, connection, notification):
        """A tab or window title changed (here, in iTerm2's own dialogs, or by a program): re-read."""
        self.regather_needed.set()

    async def poll_window_frames_when_they_matter(self):
        """Notices windows moved by hand, which iTerm2 sends no notification for (PLAN.md section 4):
        with several monitors, to regroup them; while a sidebar is docked, to keep them out of its
        area (a regather makes room). With one monitor and no docked sidebar, frames don't matter."""
        while True:
            await asyncio.sleep(FRAME_POLL_INTERVAL_SECONDS)
            if len(self.monitors) < 2 and self.reserved_screen_area is None:
                continue
            for gathered_window in list(self.gathered_windows):
                window = self.app.get_window_by_id(gathered_window["window_id"])
                if window is None:
                    continue
                try:
                    new_frame = await read_window_frame(window)
                except iterm2.GetPropertyException:
                    continue  # closed meanwhile; the layout change handles it
                if new_frame != gathered_window["frame"]:
                    gathered_window["frame"] = new_frame
                    if self.reserved_screen_area is not None:
                        self.regather_needed.set()
                    else:
                        self.snapshot_needed.set()

    async def refresh_claude_agents_periodically(self):
        """Keeps names and folders from `claude agents --json` current (started only when `claude` exists).

        PLAN.md section 7: the program is large, so this runs only while Claude Code tabs exist
        (in phase 2: only those without hooks); new sessions trigger a run of their own.
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
            try:
                if regather_is_needed:
                    await self.regather_and_publish()
                else:
                    self.publish_snapshot_if_changed()
            except (iterm2.RPCException, iterm2.GetPropertyException) as error:
                # For example the app list changed under the refresh; a lost connection is caught elsewhere.
                print(f"regather failed, trying again: {error!r}")
                asyncio.get_running_loop().call_later(RETRY_DELAY_SECONDS, self.regather_needed.set)

    async def watch_for_connection_loss(self):
        """Returns when the websocket closes or the package's dispatch task ends."""
        dispatch_future = getattr(self.connection, "_Connection__dispatch_forever_future", None)
        waiters = [asyncio.ensure_future(self.connection.websocket.wait_closed())]
        if dispatch_future is not None:
            waiters.append(dispatch_future)
        await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)

    async def start_up_after_connecting(self):
        """Reads the App object, subscribes to session variable changes, and publishes the first snapshot."""
        self.app = await iterm2.async_get_app(self.connection)
        # FocusMonitor reports only later changes, so read the current focus once. Its window
        # entry says whether that terminal window has keyboard focus or only is current (for
        # example while iTerm2's Settings window has focus).
        focus_info = await iterm2.rpc.async_get_focus_info(self.connection)
        for notification in focus_info.focus_response.notifications:
            if notification.HasField("window"):
                self.update_key_terminal_window(iterm2.FocusUpdateWindowChanged(
                    notification.window.window_id, iterm2.FocusUpdateWindowChanged.Reason(notification.window.window_status)))
        session_scope, window_scope = api_pb2.VariableScope.Value("SESSION"), api_pb2.VariableScope.Value("WINDOW")
        for variable_name in SESSION_VARIABLES_TO_WATCH:
            await async_subscribe_to_variable_change_notification(
                self.connection, self.handle_session_variable_change, session_scope, variable_name, "all")
        # Titles set by the user. iTerm2 rejects "all" for the tab scope, but a session's
        # tab.titleOverrideFormat reports its tab's title (checked against iTerm2 3.7.6).
        await async_subscribe_to_variable_change_notification(
            self.connection, self.handle_title_change, session_scope, "tab.titleOverrideFormat", "all")
        await async_subscribe_to_variable_change_notification(
            self.connection, self.handle_title_change, window_scope, "titleOverrideFormat", "all")
        await self.regather_and_publish()

    async def run(self):
        """Connects, gathers, subscribes, and runs until the connection is lost.

        Every way this ends calls report_connection_lost, so the app exits and its supervisor
        restarts it.
        """
        # Before connecting: once iTerm2 quits, its paused agents are gone, and what their pauses
        # stopped below them (in sessions of their own, which closing the tabs doesn't reach) is
        # continued even while no connection can be made.
        self.continue_the_processes_of_agents_no_longer_paused()
        iterm2.auth.gAppKitAvailable = False  # fetch the token with osascript, never NSAppleScript (PLAN.md section 4)
        try:
            self.connection = await iterm2.Connection.async_create()
        except Exception as error:
            self.report_connection_lost(f"could not connect to iTerm2: {error}")
            return
        # The watchdog starts at once: a request whose answer never comes (iTerm2 quit meanwhile)
        # would otherwise hang the start-up forever.
        connection_loss_task = asyncio.ensure_future(self.watch_for_connection_loss())
        start_up_task = asyncio.ensure_future(self.start_up_after_connecting())
        await asyncio.wait([start_up_task, connection_loss_task], return_when=asyncio.FIRST_COMPLETED)
        if not start_up_task.done():
            start_up_task.cancel()
            self.report_connection_lost("the iTerm2 connection closed during start-up")
            return
        if start_up_task.exception() is not None:
            self.report_connection_lost(f"the core could not start: {start_up_task.exception()!r}")
            return
        coroutines = [self.watch_layout_changes(), self.watch_new_sessions(), self.watch_session_terminations(),
                      self.watch_focus_changes(), self.poll_window_frames_when_they_matter(),
                      self.read_agent_screens_periodically(), self.read_context_window_usage_periodically(),
                      self.read_resource_usage_periodically(), self.read_child_processes_periodically(),
                      self.check_hooks_periodically(), self.publish_when_needed()]
        if self.claude_command_path:
            coroutines.append(self.refresh_claude_agents_periodically())
        if self.hook_events_socket_path:
            coroutines.append(self.listen_for_hook_events())
        background_tasks = [asyncio.ensure_future(coroutine) for coroutine in coroutines]
        done_tasks, _ = await asyncio.wait(background_tasks + [connection_loss_task], return_when=asyncio.FIRST_COMPLETED)
        failure_message = "the iTerm2 connection closed"
        for done_task in done_tasks:
            if done_task is not connection_loss_task and not done_task.cancelled() and done_task.exception():
                failure_message = f"the core stopped: {done_task.exception()!r}"
        self.report_connection_lost(failure_message)


class Iterm2ConnectionThread(threading.Thread):
    """Runs DashboardCore on its own asyncio event loop, and offers thread-safe entry points."""

    def __init__(self, publish_snapshot, report_connection_lost, verbose=False, docked_window_frames_file_path=None,
                 pause_records_file_path=None, pause_record_by_tab_and_agent_to_start_with=None, hook_events_socket_path=None):
        super().__init__(name="iterm2-connection", daemon=True)
        self.core_ready = threading.Event()
        self.core = None
        self.event_loop = None
        self.publish_snapshot = publish_snapshot
        self.report_connection_lost = report_connection_lost
        self.verbose = verbose
        self.docked_window_frames_file_path = docked_window_frames_file_path
        self.pause_records_file_path = pause_records_file_path
        self.pause_record_by_tab_and_agent_to_start_with = pause_record_by_tab_and_agent_to_start_with
        self.hook_events_socket_path = hook_events_socket_path

    def run(self):
        self.event_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.event_loop)
        self.core = DashboardCore(self.publish_snapshot, self.report_connection_lost, self.verbose,
                                  self.docked_window_frames_file_path, self.pause_records_file_path,
                                  self.pause_record_by_tab_and_agent_to_start_with, self.hook_events_socket_path)
        self.core_ready.set()
        self.event_loop.run_until_complete(self.core.run())

    def give_room_back_and_wait(self, timeout_seconds):
        """Thread-safe: ends docking and waits, at most timeout_seconds, until the iTerm2 windows
        have their room back; called just before the app exits."""
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

    def send_hooks_changed(self):
        """Thread-safe: has the core check the agent tools' hooks again now (after the app installed them)."""
        self.core_ready.wait()
        self.event_loop.call_soon_threadsafe(self.core.hooks_check_needed.set)
