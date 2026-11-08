"""Adapter for OpenAI's Codex CLI."""

import asyncio
import os
import re
import shlex
import time

from agent_command_tab.agent_adapters.base import (COMPACT_AND_CLEAR_SLASH_COMMANDS, MESSAGES_THROUGH_CODEX_QUEUE,
                                                   AgentToolAdapter, remove_environment_settings_from_launch_command,
                                                   split_command_line_into_program_names)
from agent_command_tab.agent_process_tree import (read_process_arguments_and_environment, read_process_current_folder,
                                                  read_process_info)
from agent_command_tab.context_window_usage import (codex_session_header_says_it_runs_in_the_shared_server,
                                                    find_codex_conversation_files, read_codex_session_header,
                                                    read_context_tokens_from_codex_conversation_file,
                                                    read_context_window_tokens_from_codex_conversation_file,
                                                    read_turn_is_under_way_from_codex_conversation_file,
                                                    read_turn_started_at_from_codex_conversation_file)
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


# <sessions folder>/2026/10/18/rollout-2026-10-18T15-04-05-<conversation ID>.jsonl
CONVERSATION_FILE_PATTERN = re.compile(
    r"rollout-[^/]*?([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.jsonl$")
# The timer Codex shows above its input box only while a turn runs: "Working (5s • esc to
# interrupt)", "Waiting for background terminal (16s • esc to interrupt)" (0.155.1 and 0.163).
TURN_TIMER_PATTERN = re.compile(r"\((?:\d+h )?(?:\d+m )?\d+s • esc to interrupt\)")


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

    def find_conversation_files(self, agent_process_id):
        """Returns the files that may be the Codex process's conversation, matched by its folder and
        start time in the sessions folder of its Codex home folder (find_codex_home_folder_of_process),
        newest first (context_window_usage.find_codex_conversation_files; Codex 0.163 doesn't keep
        the file open, so lsof can't find it). A process started with `codex resume` (or `node
        .../codex resume`) may be writing an earlier conversation's file."""
        agent_process = read_process_info(agent_process_id) if agent_process_id else None
        if agent_process is None:
            return []
        return find_codex_conversation_files(
            os.path.join(self.find_codex_home_folder_of_process(agent_process_id), "sessions"), agent_process.started_at,
            read_process_current_folder(agent_process_id), time.time(),
            agent_resumed_a_conversation="resume" in read_process_arguments_and_environment(agent_process_id)[0][1:])

    def find_codex_home_folder_of_process(self, agent_process_id):
        """Returns the folder the Codex process keeps its files in: its own environment's
        CODEX_HOME (a launch command may set another than the app's), ~/.codex when it has none,
        or the app's own (find_codex_home_folder) when its environment can't be read."""
        arguments, environment = read_process_arguments_and_environment(agent_process_id)
        return find_codex_home_folder(environment if arguments else None)

    def read_context_tokens(self, conversation_file_path):
        return read_context_tokens_from_codex_conversation_file(conversation_file_path)

    def read_context_window_tokens(self, conversation_file_path, agent_process_id, claude_agent_record, context_tokens):
        return read_context_window_tokens_from_codex_conversation_file(conversation_file_path)

    def read_turn_started_at(self, conversation_file_path):
        return read_turn_started_at_from_codex_conversation_file(conversation_file_path)

    def read_turn_is_under_way(self, conversation_file_path):
        return read_turn_is_under_way_from_codex_conversation_file(conversation_file_path)

    def read_turns_run_outside_the_tab(self, conversation_file_path):
        return codex_session_header_says_it_runs_in_the_shared_server(read_codex_session_header(conversation_file_path))

    def read_open_tool_call(self, conversation_file_path):
        return read_open_tool_call_from_codex_conversation_file(conversation_file_path)

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
