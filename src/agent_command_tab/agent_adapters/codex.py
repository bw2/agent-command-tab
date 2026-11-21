"""Adapter for OpenAI's Codex CLI."""

import asyncio
import os
import re
import shlex
import time

from agent_command_tab.agent_adapters.base import (COMPACT_AND_CLEAR_SLASH_COMMANDS, MESSAGES_THROUGH_CODEX_QUEUE,
                                                   AgentToolAdapter, remove_environment_settings_from_launch_command,
                                                   split_command_line_into_program_names)
from agent_command_tab.agent_process_tree import (a_codex_shared_background_server_runs, read_process_arguments_and_environment,
                                                  read_process_current_folder, read_process_info)
from agent_command_tab.context_window_usage import (codex_session_header_says_it_runs_in_the_shared_server,
                                                    find_codex_conversation_file_by_id, find_codex_conversation_files,
                                                    read_codex_session_header,
                                                    read_context_tokens_and_context_window_tokens_from_codex_conversation_file,
                                                    read_turn_is_under_way_from_codex_conversation_file,
                                                    read_turn_started_at_from_codex_conversation_file)
from agent_command_tab.conversation_search_texts import extract_search_texts_from_codex_record
from agent_command_tab.last_message_time_reading import read_last_message_at_from_codex_conversation_file
from agent_command_tab.open_tool_call_reading import read_open_tool_call_from_codex_conversation_file


DEFAULT_CODEX_HOME_FOLDER = os.path.expanduser("~/.codex")  # the one a Codex without CODEX_HOME uses


def build_codex_home_setting(codex_home_folder):
    """Returns "CODEX_HOME=<folder> " to set in front of a command, or "" for None."""
    return f"CODEX_HOME={shlex.quote(codex_home_folder)} " if codex_home_folder else ""


def set_codex_home_in_front_of_launch_command(launch_command, codex_home_folder):
    """Returns the launch command run with CODEX_HOME set to codex_home_folder, in place of any it
    sets itself (which would come later, and win); unchanged for None (not known)."""
    if not codex_home_folder:
        return launch_command
    return build_codex_home_setting(codex_home_folder) + remove_environment_settings_from_launch_command(launch_command, "CODEX_HOME")


def find_codex_home_folder(environment=None):
    """Returns the folder Codex keeps its files in: environment's (by default the app's own)
    CODEX_HOME, or DEFAULT_CODEX_HOME_FOLDER."""
    return os.path.expanduser((os.environ if environment is None else environment).get("CODEX_HOME") or DEFAULT_CODEX_HOME_FOLDER)


def find_codex_home_folder_of_process_started_with(arguments, environment):
    """Returns the folder a Codex process keeps its files in, from the arguments and environment
    it was started with (read_process_arguments_and_environment): its own environment's CODEX_HOME
    (a launch command may set another than the app's), ~/.codex when it has none, or the app's
    own (find_codex_home_folder) when they can't be read (no arguments)."""
    return find_codex_home_folder(environment if arguments else None)


# A Codex conversation's ID, which ends its file's name and which `codex resume` takes.
CONVERSATION_ID_PATTERN = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
# <sessions folder>/2026/10/18/rollout-2026-10-18T15-04-05-<conversation ID>.jsonl
CONVERSATION_FILE_PATTERN = re.compile(rf"rollout-[^/]*?({CONVERSATION_ID_PATTERN.pattern})\.jsonl$")
# The timer Codex shows above its input box only while a turn runs: "Working (5s • esc to
# interrupt)", "Waiting for background terminal (16s • esc to interrupt)" (0.155.1 and 0.163).
TURN_TIMER_PATTERN = re.compile(r"\((?:\d+h )?(?:\d+m )?\d+s • esc to interrupt\)")
# The subcommand a Codex process goes on with an earlier conversation by: `codex resume <conversation
# ID>`, or `codex resume` with `--last`, a session's name, or nothing (its picker).
RESUME_SUBCOMMAND = "resume"
# The option that has a Codex 0.165 or later run its turns and commands itself, without its
# shared background server.
OPTION_TO_RUN_WITHOUT_THE_SHARED_SERVER = "--no-daemon"


def find_conversation_id_named_after_resume(arguments):
    """Returns the ID of the conversation a Codex process's arguments name after `resume` (as
    Restore sessions starts it), or None: for a process started without resume, or to resume
    its last conversation, one chosen in its picker, or one named by a session's name.

    Args:
        arguments: The arguments the Codex process was started with, [] when they can't be read.
    """
    if RESUME_SUBCOMMAND not in arguments[1:]:
        return None
    return next((argument for argument in arguments[arguments.index(RESUME_SUBCOMMAND, 1) + 1:]
                 if CONVERSATION_ID_PATTERN.fullmatch(argument)), None)


def codex_process_runs_its_turns_in_the_shared_server(header, arguments, a_shared_server_runs):
    """Returns whether Codex's shared background server, not the Codex process in the tab, runs
    the turns and commands of the process's conversation.

    For a conversation the process started, the header of the conversation's file tells
    (codex_session_header_says_it_runs_in_the_shared_server). A conversation it resumed keeps the
    header of the Codex that started it, which may have run its turns the other way (an earlier
    version, or one started with `--no-daemon`), so that header tells nothing about this
    process, and its own arguments tell instead: with `--no-daemon` it runs its turns itself.
    Without it, nothing tells an earlier Codex, which runs them itself, from a later one, which
    runs them in the server, so the turns count as the server's whenever a shared server runs at
    all (a pause that stopped only the server's client would report success and leave the work
    running), and as the process's own when none runs, since no server can be running them then.

    Args:
        header: The session_meta header of the conversation's file (read_codex_session_header).
        arguments: The arguments the Codex process was started with, [] when they can't be read.
        a_shared_server_runs: Returns whether a shared background server runs
            (agent_process_tree.a_codex_shared_background_server_runs); asked only about a
            process started with `codex resume` and without `--no-daemon`.
    """
    if RESUME_SUBCOMMAND not in arguments[1:]:
        return codex_session_header_says_it_runs_in_the_shared_server(header)
    return OPTION_TO_RUN_WITHOUT_THE_SHARED_SERVER not in arguments[1:] and a_shared_server_runs()


class CodexAdapter(AgentToolAdapter):
    """Codex runs as `codex ...` (0.163) or through its Node.js wrapper, `node .../codex ...` (0.154).

    Codex 0.163 reports "waiting" itself exactly while its approval menu shows, so it needs no
    permission dialog check.
    """

    agent_tool_name = "Codex"
    working_screen_pattern = TURN_TIMER_PATTERN
    helper_process_names = frozenset({"codex-code-mode-host"})  # stays running for the whole session
    slash_commands = COMPACT_AND_CLEAR_SLASH_COMMANDS
    # The input box: "›", then the input, above the line with the model and the folder
    # ("  GPT-6-Astra low fast · /private/tmp/…").
    input_line_marker = "›"
    input_area_end_pattern = re.compile(r"^  \S[^·]* · [~/…]")
    seconds_before_enter = 0.3  # Codex takes keys within 8 ms of each other as a paste, and Enter for 120 ms after it as a newline
    # Codex 0.155.1 hides its turn timer while answer text streams, so a turn can look over on
    # screen; a /compact typed then is refused with this line and dropped from the box.
    slash_command_refused_during_a_turn_pattern = re.compile(r"'/\w+' is disabled while a task is in progress")
    # `codex queue` reached a busy Codex 0.155.1 session in 0.2 s, which ran the message right after its turn.
    how_other_agent_sessions_send_it_messages = MESSAGES_THROUGH_CODEX_QUEUE
    can_resume_conversations = True

    def __init__(self):
        # {(sessions folder, conversation ID): its file} for the conversations processes were started
        # to resume, each found once (find_file_of_the_conversation_named_after_resume).
        self.resumed_conversation_file_by_sessions_folder_and_conversation_id = {}

    def matches_command_line(self, command_line):
        """Returns whether the command line runs `codex`, directly or as `node <path>/codex`."""
        program_names = split_command_line_into_program_names(command_line)
        if not program_names:
            return False
        if program_names[0] == "codex":
            return True
        return program_names[0] == "node" and len(program_names) > 1 and program_names[1] in ("codex", "codex.js")

    async def find_conversation_id(self, agent_process_id, claude_agent_record):
        """Returns the conversation ID from the conversation file's name, only when exactly one
        file fits, so a Duplicate doesn't fork an older conversation of this process (after /new).
        One file fitting can still be another Codex session's in the same folder (a later one,
        while this one has no file yet); callers check that with
        DashboardCore.codex_conversation_may_be_another_sessions."""
        conversation_files = await asyncio.to_thread(self.find_conversation_files, agent_process_id)
        return CONVERSATION_FILE_PATTERN.search(conversation_files[0]).group(1) if len(conversation_files) == 1 else None

    async def find_conversation_ids_it_may_be_in(self, agent_process_id):
        """Returns the conversation ID of every file that fits the process: after /new, or with a
        later Codex session in its folder, more than one does."""
        conversation_files = await asyncio.to_thread(self.find_conversation_files, agent_process_id)
        return [CONVERSATION_FILE_PATTERN.search(conversation_file).group(1) for conversation_file in conversation_files]

    async def find_conversation_file(self, agent_process_id, claude_agent_record, claude_config_folders):
        """Returns the most recently written file that fits (see find_conversation_files)."""
        conversation_files = await asyncio.to_thread(self.find_conversation_files, agent_process_id)
        return conversation_files[0] if conversation_files else None

    def find_conversation_file_by_id(self, conversation_id, claude_config_folders, claude_config_folder=None, codex_home_folder=None):
        """Returns the conversation's file in the sessions folder of the CODEX_HOME the session ran
        with, or of the app's own (find_codex_home_folder) when that isn't known."""
        return find_codex_conversation_file_by_id(os.path.join(codex_home_folder or find_codex_home_folder(), "sessions"), conversation_id)

    def find_conversation_files(self, agent_process_id):
        """Returns the files that may be the Codex process's conversation, matched by its folder and
        start time in the sessions folder of its Codex home folder (find_codex_home_folder_of_process),
        newest first (context_window_usage.find_codex_conversation_files; Codex 0.163 doesn't keep
        the file open, so lsof can't find it). A process started with `codex resume` (or `node
        .../codex resume`) may be writing an earlier conversation's file, which is looked for by
        its ID when the process's arguments name it
        (find_file_of_the_conversation_named_after_resume). The process's arguments and
        environment are read once for all of that."""
        agent_process = read_process_info(agent_process_id) if agent_process_id else None
        if agent_process is None:
            return []
        arguments, environment = read_process_arguments_and_environment(agent_process_id)
        codex_sessions_folder = os.path.join(find_codex_home_folder_of_process_started_with(arguments, environment), "sessions")
        return find_codex_conversation_files(
            codex_sessions_folder, agent_process.started_at, read_process_current_folder(agent_process_id), time.time(),
            agent_resumed_a_conversation=RESUME_SUBCOMMAND in arguments[1:],
            resumed_conversation_file=self.find_file_of_the_conversation_named_after_resume(codex_sessions_folder, arguments))

    def find_file_of_the_conversation_named_after_resume(self, codex_sessions_folder, arguments):
        """Returns the file of the conversation the process's arguments name after `resume`
        (find_conversation_id_named_after_resume), or None when they name none or the sessions
        folder has no file of it. Looked for once for each conversation: its file stays where
        it is, and looking for it lists every day folder Codex has kept."""
        conversation_id = find_conversation_id_named_after_resume(arguments)
        if conversation_id is None:
            return None
        sessions_folder_and_conversation_id = (codex_sessions_folder, conversation_id)
        conversation_file = (
            self.resumed_conversation_file_by_sessions_folder_and_conversation_id.get(sessions_folder_and_conversation_id)
            or find_codex_conversation_file_by_id(codex_sessions_folder, conversation_id))
        if conversation_file is not None:
            self.resumed_conversation_file_by_sessions_folder_and_conversation_id[sessions_folder_and_conversation_id] = conversation_file
        return conversation_file

    def find_codex_home_folder_of_process(self, agent_process_id):
        """Returns the folder the Codex process keeps its files in
        (find_codex_home_folder_of_process_started_with)."""
        return find_codex_home_folder_of_process_started_with(*read_process_arguments_and_environment(agent_process_id))

    def read_context_tokens_and_context_window_tokens(self, conversation_file_path, agent_process_id, claude_agent_record,
                                                      context_tokens_read_before):
        return read_context_tokens_and_context_window_tokens_from_codex_conversation_file(conversation_file_path)

    def read_turn_started_at(self, conversation_file_path):
        return read_turn_started_at_from_codex_conversation_file(conversation_file_path)

    def read_turn_is_under_way(self, conversation_file_path):
        return read_turn_is_under_way_from_codex_conversation_file(conversation_file_path)

    def read_turns_run_outside_the_tab(self, conversation_file_path, agent_process_id):
        """Whether Codex's shared background server runs the conversation's turns: by the file's
        header, or, for a process that resumed the conversation, by how it was started and whether
        such a server runs (codex_process_runs_its_turns_in_the_shared_server)."""
        return codex_process_runs_its_turns_in_the_shared_server(
            read_codex_session_header(conversation_file_path), read_process_arguments_and_environment(agent_process_id)[0],
            a_codex_shared_background_server_runs)

    def read_whether_a_conversation_it_may_be_in_runs_outside_the_tab(self, agent_process_id):
        """Returns whether the turns of any file that may be the process's conversation
        (find_conversation_files) run outside the tab (read_turns_run_outside_the_tab). One such
        file is enough: when the process's own file can't be told apart from another session's,
        a pause that stopped only the tab's client of the server would leave the session's work
        running. Called in a worker thread."""
        return any(self.read_turns_run_outside_the_tab(path, agent_process_id) for path in self.find_conversation_files(agent_process_id))

    def read_open_tool_call(self, conversation_file_path):
        return read_open_tool_call_from_codex_conversation_file(conversation_file_path)

    def read_last_message_at(self, conversation_file_path):
        return read_last_message_at_from_codex_conversation_file(conversation_file_path)

    def extract_search_texts_from_conversation_record(self, record):
        return extract_search_texts_from_codex_record(record)

    def build_launch_command_for_a_copy(self, launch_command, conversation_id, claude_agent_record=None, codex_home_folder=None):
        """Continues a copy of the conversation (`codex fork <id>`), with the original's CODEX_HOME
        (build_launch_command_to_resume)."""
        if not conversation_id:
            return launch_command
        return f"{set_codex_home_in_front_of_launch_command(launch_command, codex_home_folder)} fork {shlex.quote(conversation_id)}"

    def build_launch_command_to_resume(self, launch_command, conversation_id, claude_config_folder=None, codex_home_folder=None):
        """`<launch command> resume <id>`, with CODEX_HOME set to the conversation's home folder
        when it is known (Codex finds the conversation only there), in place of the launch command's own."""
        return f"{set_codex_home_in_front_of_launch_command(launch_command, codex_home_folder)} resume {shlex.quote(conversation_id)}"
