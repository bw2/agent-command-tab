"""Adapter for Anthropic's Claude Code."""

import asyncio
import re
import shlex

from llm_sidebar_dashboard.agent_adapters.base import AgentToolAdapter, split_command_line_into_program_names
from llm_sidebar_dashboard.context_window_usage import (find_claude_code_transcript,
                                                        read_context_tokens_from_claude_code_transcript,
                                                        read_turn_started_at_from_claude_code_transcript)
from llm_sidebar_dashboard.session_preview_computation import read_preview_fields_from_claude_code_transcript

ELAPSED_TIME = r"(?:\d+h )?(?:\d+m )?\d+s"


class ClaudeCodeAdapter(AgentToolAdapter):
    """Claude Code runs as `claude ...` (iTerm2's jobName is its version number, so the command line is used).

    Screen patterns are from Claude Code 2.1.302 (PHASE0_RESULTS.md).
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
    slash_commands = frozenset({"/compact", "/clear"})
    # The input box: "❯" and a no-break space, then the input, between two rules of "─" (Phase 0).
    input_line_marker = "❯"
    input_area_end_pattern = re.compile(r"^─{10,}\s*$")

    def matches_command_line(self, command_line):
        """Returns whether the command line's program is `claude` (by base name, so full paths match)."""
        program_names = split_command_line_into_program_names(command_line)
        return bool(program_names) and program_names[0] == "claude"

    async def find_conversation_id(self, agent_process_id, claude_agent_record):
        """Returns `sessionId` from the process's `claude agents --json` record."""
        return (claude_agent_record or {}).get("sessionId")

    async def find_conversation_file(self, agent_process_id, claude_agent_record, claude_config_folders):
        """Returns the transcript named after the record's `sessionId` in one of the config folders."""
        conversation_id = await self.find_conversation_id(agent_process_id, claude_agent_record)
        if not conversation_id:
            return None
        return await asyncio.to_thread(find_claude_code_transcript, claude_config_folders, conversation_id)

    def read_context_tokens(self, conversation_file_path):
        return read_context_tokens_from_claude_code_transcript(conversation_file_path)

    def read_turn_started_at(self, conversation_file_path):
        return read_turn_started_at_from_claude_code_transcript(conversation_file_path)

    def read_preview_fields(self, conversation_file_path):
        return read_preview_fields_from_claude_code_transcript(conversation_file_path)

    def build_launch_command_for_a_copy(self, launch_command, conversation_id, claude_agent_record=None):
        """Continues a copy of the conversation under a new session ID (`--resume <id> --fork-session`),
        with the original's config folder when it isn't the default one (Claude Code finds the
        conversation only there)."""
        if not conversation_id:
            return launch_command
        config_folder = (claude_agent_record or {}).get("config_folder")
        environment_prefix = f"CLAUDE_CONFIG_DIR={shlex.quote(config_folder)} " if config_folder else ""
        return f"{environment_prefix}{launch_command} --resume {shlex.quote(conversation_id)} --fork-session"
