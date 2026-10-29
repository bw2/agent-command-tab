"""Adapter for Anthropic's Claude Code."""

import asyncio
import glob
import os
import re
import shlex

from agent_command_tab.agent_adapters.base import (COMPACT_AND_CLEAR_SLASH_COMMANDS, MESSAGES_THROUGH_THE_SEND_MESSAGE_TOOL,
                                                   AgentToolAdapter, split_command_line_into_program_names)
from agent_command_tab.agent_process_tree import read_process_arguments_and_environment
from agent_command_tab.context_window_usage import (choose_claude_code_context_window_tokens, find_claude_code_transcript,
                                                    read_claude_code_model_setting,
                                                    read_context_tokens_from_claude_code_transcript,
                                                    read_turn_started_at_from_claude_code_transcript)
from agent_command_tab.session_preview_computation import read_preview_fields_from_claude_code_transcript

ELAPSED_TIME = r"(?:\d+h )?(?:\d+m )?\d+s"
# One "NAME=value " word in front of a launch command's program, the value maybe quoted
# ("ANTHROPIC_MODEL=x CLAUDE_CONFIG_DIR=~/.claude_work claude").
LEADING_ENVIRONMENT_SETTING_PATTERN = re.compile(r"""([A-Za-z_][A-Za-z0-9_]*)=(?:'[^']*'|"(?:[^"\\]|\\.)*"|\\.|[^\s'"\\])*\s+""")


def list_claude_config_folders():
    """Returns the Claude Code config folders in use: the default folder (None: a command run
    without CLAUDE_CONFIG_DIR uses it), the dashboard's own CLAUDE_CONFIG_DIR, and every other
    ~/.claude* folder with a sessions folder, since a Claude Code started with its own
    CLAUDE_CONFIG_DIR (for example ~/.claude_work) keeps its sessions and settings there. Asked
    by `claude agents --json`, which lists a session only from its own folder, and by the hooks
    installer, since such a session reads only its own folder's settings.json.
    """
    default_folder = os.path.expanduser("~/.claude")
    other_folders = ([os.environ["CLAUDE_CONFIG_DIR"]] if os.environ.get("CLAUDE_CONFIG_DIR") else []) + sorted(
        folder for folder in glob.glob(os.path.expanduser("~/.claude*")) if os.path.isdir(os.path.join(folder, "sessions")))
    return [None] + [folder for folder in dict.fromkeys(other_folders) if os.path.realpath(folder) != os.path.realpath(default_folder)]


def remove_config_folder_settings_from_launch_command(launch_command):
    """Returns the launch command without the CLAUDE_CONFIG_DIR=... words in front of its
    program, keeping its other environment settings."""
    kept_settings = []
    rest = launch_command.lstrip()
    while (match := LEADING_ENVIRONMENT_SETTING_PATTERN.match(rest)):
        if match.group(1) != "CLAUDE_CONFIG_DIR":
            kept_settings.append(match.group(0))
        rest = rest[match.end():]
    return "".join(kept_settings) + rest


class ClaudeCodeAdapter(AgentToolAdapter):
    """Claude Code runs as `claude ...` (iTerm2's jobName is its version number, so the command line is used).

    Screen patterns are from Claude Code 2.1.302.
    """

    agent_tool_name = "Claude Code"
    # Every permission dialog asks "Do you want to ...?" ("proceed?", "make this edit to <file>?",
    # "allow Claude to fetch this content?" in 2.1.303) above an "Esc to cancel" hint.
    permission_dialog_screen_patterns = (re.compile(r"Do you want to .+\?"), re.compile(r"Esc to cancel"))
    interrupted_turn_screen_pattern = re.compile(r"Interrupted · What should Claude do instead\?")
    progress_noise_patterns = (
        re.compile(r"^\s*[✳✶✢✻✽✺·*⏺]\s"),  # the spinner glyph, and the tool line's blinking bullet
        re.compile(r"\(" + ELAPSED_TIME + r"(?: · [↓↑] [\d.,]+k? tokens)?\)"),  # "(5s · ↓ 97 tokens)", "(4s)"
        re.compile(r" · " + ELAPSED_TIME + r"\b"),  # the tool line's " · 4s"
        re.compile(r"\(ctrl\+b to run in background\)"),
    )
    helper_process_names = frozenset({"caffeinate"})  # keeps the Mac awake while a turn runs
    slash_commands = COMPACT_AND_CLEAR_SLASH_COMMANDS
    # The input box: "❯" and a no-break space, then the input, between two rules of "─" (Phase 0).
    input_line_marker = "❯"
    input_area_end_pattern = re.compile(r"^─{10,}\s*$")
    # Checked live with Claude Code 2.1.307 on 2026-10-21: /compact and /clear typed during a turn
    # showed under "Press up to edit queued messages" and ran when the turn ended.
    queues_slash_commands_typed_during_a_turn = True
    how_other_agent_sessions_send_it_messages = MESSAGES_THROUGH_THE_SEND_MESSAGE_TOOL

    def matches_command_line(self, command_line):
        """Returns whether the command line's program is `claude` (by base name, so full paths match)."""
        program_names = split_command_line_into_program_names(command_line)
        return bool(program_names) and program_names[0] == "claude"

    async def find_conversation_id(self, agent_process_id, claude_agent_record):
        """Returns `sessionId` from the process's `claude agents --json` record."""
        return (claude_agent_record or {}).get("sessionId")

    async def find_conversation_file(self, agent_process_id, claude_agent_record, claude_config_folders):
        """Returns the transcript named after the record's `sessionId`: in the record's own config
        folder first (one created after the app started is in no list made then), else in one of
        the config folders."""
        conversation_id = await self.find_conversation_id(agent_process_id, claude_agent_record)
        if not conversation_id:
            return None
        own_config_folder = (claude_agent_record or {}).get("config_folder") or os.path.expanduser("~/.claude")
        return await asyncio.to_thread(find_claude_code_transcript, [own_config_folder, *claude_config_folders], conversation_id)

    def read_context_tokens(self, conversation_file_path):
        return read_context_tokens_from_claude_code_transcript(conversation_file_path)

    def read_context_window_tokens(self, conversation_file_path, agent_process_id, claude_agent_record, context_tokens):
        return choose_claude_code_context_window_tokens(read_claude_code_model_setting(
            *read_process_arguments_and_environment(agent_process_id), (claude_agent_record or {}).get("config_folder")),
            context_tokens)

    def read_turn_started_at(self, conversation_file_path):
        return read_turn_started_at_from_claude_code_transcript(conversation_file_path)

    def read_preview_fields(self, conversation_file_path):
        return read_preview_fields_from_claude_code_transcript(conversation_file_path)

    def build_launch_command_for_a_copy(self, launch_command, conversation_id, claude_agent_record=None):
        """Continues a copy of the conversation under a new session ID (`--resume <id> --fork-session`),
        in the original's config folder (Claude Code finds the conversation only there): a
        CLAUDE_CONFIG_DIR set in front of the launch command gives way to the original's folder,
        or to none for an original in the default folder."""
        if not conversation_id:
            return launch_command
        config_folder = (claude_agent_record or {}).get("config_folder")
        environment_prefix = f"CLAUDE_CONFIG_DIR={shlex.quote(config_folder)} " if config_folder else ""
        return (f"{environment_prefix}{remove_config_folder_settings_from_launch_command(launch_command)}"
                f" --resume {shlex.quote(conversation_id)} --fork-session")
