"""Adapter for Anthropic's Claude Code."""

import asyncio
import glob
import os
import re
import shlex

from agent_command_tab.agent_adapters.base import (COMPACT_AND_CLEAR_SLASH_COMMANDS, MESSAGES_THROUGH_THE_SEND_MESSAGE_TOOL,
                                                   AgentToolAdapter, remove_environment_settings_from_launch_command,
                                                   split_command_line_into_program_names)
from agent_command_tab.agent_process_tree import read_process_arguments_and_environment
from agent_command_tab.context_window_usage import (choose_claude_code_context_window_tokens, find_claude_code_transcript,
                                                    read_claude_code_model_setting,
                                                    read_context_tokens_and_model_from_claude_code_transcript,
                                                    read_turn_started_at_from_claude_code_transcript)
from agent_command_tab.conversation_search_texts import extract_search_texts_from_claude_code_record
from agent_command_tab.last_message_time_reading import read_last_message_at_from_claude_code_transcript
from agent_command_tab.open_tool_call_reading import read_open_tool_call_from_claude_code_transcript
from agent_command_tab.settings_store import (SETTINGS_FILE_PATH, find_launch_command, merge_settings_with_defaults,
                                              read_environment_settings_of_launch_command, read_json_file)

DEFAULT_CLAUDE_CONFIG_FOLDER = os.path.expanduser("~/.claude")  # the one a Claude Code without CLAUDE_CONFIG_DIR uses


def list_claude_config_folders(settings_file_path=SETTINGS_FILE_PATH):
    """Returns the Claude Code config folders in use: the default folder (None: a command run
    without CLAUDE_CONFIG_DIR uses it), the dashboard's own CLAUDE_CONFIG_DIR, the one the Claude
    Code launch command in the settings sets (CLAUDE_CONFIG_DIR=... in front of its program,
    which may name a folder anywhere), once it exists, and every other ~/.claude* folder with a
    sessions folder, since a Claude Code started with its own CLAUDE_CONFIG_DIR (for example
    ~/.claude_work) keeps its sessions and settings there. Each folder is listed once, whichever
    paths lead to it. Asked by `claude agents --json`, which lists a session only from its own
    folder, and by the hooks installer, since such a session reads only its own folder's settings.json.
    """
    # The settings are read without load_settings's message about an unreadable file, which every
    # lookup of a conversation file would print again.
    launch_command = find_launch_command(merge_settings_with_defaults(read_json_file(settings_file_path, dict)),
                                         ClaudeCodeAdapter.agent_tool_name)
    # As the shell the launch command is typed into reads it: "~/x" and "$HOME/x" alike ("" when it sets none).
    folder_the_launch_command_sets = os.path.expandvars(os.path.expanduser(
        read_environment_settings_of_launch_command(launch_command).get("CLAUDE_CONFIG_DIR", "")))
    other_folders = (([os.environ["CLAUDE_CONFIG_DIR"]] if os.environ.get("CLAUDE_CONFIG_DIR") else [])
                     + ([folder_the_launch_command_sets] if os.path.isdir(folder_the_launch_command_sets) else [])
                     + sorted(folder for folder in glob.glob(os.path.expanduser("~/.claude*"))
                              if os.path.isdir(os.path.join(folder, "sessions"))))
    other_folder_by_real_path = {}
    for folder in other_folders:
        other_folder_by_real_path.setdefault(os.path.realpath(folder), folder)
    other_folder_by_real_path.pop(os.path.realpath(DEFAULT_CLAUDE_CONFIG_FOLDER), None)
    return [None] + list(other_folder_by_real_path.values())


def list_claude_config_folder_paths():
    """Returns list_claude_config_folders's folders, the default one by its path."""
    return [folder or DEFAULT_CLAUDE_CONFIG_FOLDER for folder in list_claude_config_folders()]


class ClaudeCodeAdapter(AgentToolAdapter):
    """Claude Code runs as `claude ...` (iTerm2's jobName is its version number, so the command line is used).

    Screen patterns are from Claude Code 2.1.302. The permission dialog's and the interrupted
    turn's are read only for a session whose own report of its status can't be read: that report
    tells whether the dialog was answered (DashboardCore.read_agent_screens_periodically).
    """

    agent_tool_name = "Claude Code"
    # Every permission dialog asks "Do you want to ...?" ("proceed?", "make this edit to <file>?",
    # "allow Claude to fetch this content?" in 2.1.303) above an "Esc to cancel" hint.
    permission_dialog_screen_patterns = (re.compile(r"Do you want to .+\?"), re.compile(r"Esc to cancel"))
    interrupted_turn_screen_pattern = re.compile(r"Interrupted · What should Claude do instead\?")
    helper_process_names = frozenset({"caffeinate"})  # keeps the Mac awake while a turn runs
    slash_commands = COMPACT_AND_CLEAR_SLASH_COMMANDS
    # The input box: "❯" and a no-break space, then the input, between two rules of "─".
    input_line_marker = "❯"
    input_area_end_pattern = re.compile(r"^─{10,}\s*$")
    # Checked live with Claude Code 2.1.307: /compact and /clear typed during a turn
    # showed under "Press up to edit queued messages" and ran when the turn ended.
    queues_slash_commands_typed_during_a_turn = True
    how_other_agent_sessions_send_it_messages = MESSAGES_THROUGH_THE_SEND_MESSAGE_TOOL
    can_resume_conversations = True

    def matches_command_line(self, command_line):
        """Returns whether the command line's program is `claude` (by base name, so full paths match)."""
        program_names = split_command_line_into_program_names(command_line)
        return bool(program_names) and program_names[0] == "claude"

    async def find_conversation_id(self, agent_process_id, claude_agent_record):
        """Returns `sessionId` from the process's `claude agents --json` record."""
        return (claude_agent_record or {}).get("sessionId")

    async def find_conversation_file(self, agent_process_id, claude_agent_record, claude_config_folders):
        """Returns the transcript named after the record's `sessionId`: in the record's own config
        folder first, else in one of the config folders."""
        conversation_id = await self.find_conversation_id(agent_process_id, claude_agent_record)
        if not conversation_id:
            return None
        own_config_folder = (claude_agent_record or {}).get("config_folder") or DEFAULT_CLAUDE_CONFIG_FOLDER
        return await asyncio.to_thread(find_claude_code_transcript, [own_config_folder, *claude_config_folders], conversation_id)

    def find_conversation_file_by_id(self, conversation_id, claude_config_folders, claude_config_folder=None, codex_home_folder=None):
        """Returns the transcript named after the ID: in the config folder the session ran with
        first, else in one of the config folders."""
        return find_claude_code_transcript([claude_config_folder or DEFAULT_CLAUDE_CONFIG_FOLDER, *claude_config_folders], conversation_id)

    def read_context_tokens_and_context_window_tokens(self, conversation_file_path, agent_process_id, claude_agent_record,
                                                      context_tokens_read_before):
        context_tokens, model = read_context_tokens_and_model_from_claude_code_transcript(conversation_file_path)
        return context_tokens, choose_claude_code_context_window_tokens(read_claude_code_model_setting(
            *read_process_arguments_and_environment(agent_process_id),
            (claude_agent_record or {}).get("config_folder") or DEFAULT_CLAUDE_CONFIG_FOLDER),
            model, context_tokens if context_tokens is not None else context_tokens_read_before)

    def read_turn_started_at(self, conversation_file_path):
        return read_turn_started_at_from_claude_code_transcript(conversation_file_path)

    def read_open_tool_call(self, conversation_file_path):
        return read_open_tool_call_from_claude_code_transcript(conversation_file_path)

    def read_last_message_at(self, conversation_file_path):
        return read_last_message_at_from_claude_code_transcript(conversation_file_path)

    def extract_search_texts_from_conversation_record(self, record):
        return extract_search_texts_from_claude_code_record(record)

    def build_launch_command_for_a_copy(self, launch_command, conversation_id, claude_agent_record=None, codex_home_folder=None):
        """Continues a copy of the conversation under a new session ID (`--resume <id> --fork-session`),
        in the original's config folder (build_launch_command_to_resume)."""
        if not conversation_id:
            return launch_command
        return self.build_launch_command_to_resume(launch_command, conversation_id,
                                                   (claude_agent_record or {}).get("config_folder")) + " --fork-session"

    def build_launch_command_to_resume(self, launch_command, conversation_id, claude_config_folder=None, codex_home_folder=None):
        """`<launch command> --resume <id>`, in the conversation's config folder (Claude Code finds
        the conversation only there): a CLAUDE_CONFIG_DIR set in front of the launch command gives
        way to that folder, or to none for a conversation in the default folder."""
        environment_prefix = f"CLAUDE_CONFIG_DIR={shlex.quote(claude_config_folder)} " if claude_config_folder else ""
        return (f"{environment_prefix}{remove_environment_settings_from_launch_command(launch_command, 'CLAUDE_CONFIG_DIR')}"
                f" --resume {shlex.quote(conversation_id)}")
