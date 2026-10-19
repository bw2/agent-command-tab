"""Adapter for OpenAI's Codex CLI."""

import asyncio
import os
import re
import shlex
import time

from llm_sidebar_dashboard.agent_adapters.base import AgentToolAdapter, split_command_line_into_program_names
from llm_sidebar_dashboard.agent_process_tree import read_process_current_folder, read_process_info
from llm_sidebar_dashboard.context_window_usage import (find_codex_conversation_files,
                                                        read_context_tokens_from_codex_conversation_file,
                                                        read_turn_started_at_from_codex_conversation_file)
from llm_sidebar_dashboard.session_preview_computation import read_preview_fields_from_codex_conversation_file

# <sessions folder>/2026/10/18/rollout-2026-10-18T15-04-05-<conversation ID>.jsonl
CONVERSATION_FILE_PATTERN = re.compile(
    r"rollout-[^/]*?([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.jsonl$")


class CodexAdapter(AgentToolAdapter):
    """Codex runs as `codex ...` (0.163) or through its Node.js wrapper, `node .../codex ...` (0.154).

    Codex 0.163 reports "waiting" itself exactly while its approval menu shows, so it needs no
    permission dialog check (PHASE0_RESULTS.md).
    """

    agent_tool_name = "Codex"
    progress_noise_patterns = (re.compile(r"\((?:\d+h )?(?:\d+m )?\d+s • esc to interrupt\)"),)
    helper_process_names = frozenset({"codex-code-mode-host"})  # stays running for the whole session
    slash_commands = frozenset({"/compact", "/clear"})
    # The input box: "›", then the input, above the line with the model and the folder
    # ("  GPT-6-Astra low fast · /private/tmp/…", Phase 0).
    input_line_marker = "›"
    input_area_end_pattern = re.compile(r"^  \S[^·]* · [~/…]")
    seconds_before_enter = 0.3  # Codex takes keys within 8 ms of each other as a paste, and Enter for 120 ms after it as a newline

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
        file fits: a Duplicate must not fork another session's conversation."""
        conversation_files = await asyncio.to_thread(self.find_conversation_files, agent_process_id)
        return CONVERSATION_FILE_PATTERN.search(conversation_files[0]).group(1) if len(conversation_files) == 1 else None

    async def find_conversation_file(self, agent_process_id, claude_agent_record, claude_config_folders):
        """Returns the most recently written file that fits (see find_conversation_files)."""
        conversation_files = await asyncio.to_thread(self.find_conversation_files, agent_process_id)
        return conversation_files[0] if conversation_files else None

    def find_conversation_files(self, agent_process_id):
        """Returns the files that may be the Codex process's conversation, matched by its folder and
        start time in Codex's sessions folder, newest first (context_window_usage.find_codex_conversation_files;
        Codex 0.163 doesn't keep the file open, so lsof can't find it)."""
        agent_process = read_process_info(agent_process_id) if agent_process_id else None
        if agent_process is None:
            return []
        codex_home = os.environ.get("CODEX_HOME") or os.path.expanduser("~/.codex")
        return find_codex_conversation_files(os.path.join(codex_home, "sessions"), agent_process.started_at,
                                             read_process_current_folder(agent_process_id), time.time())

    def read_context_tokens(self, conversation_file_path):
        return read_context_tokens_from_codex_conversation_file(conversation_file_path)

    def read_turn_started_at(self, conversation_file_path):
        return read_turn_started_at_from_codex_conversation_file(conversation_file_path)

    def read_preview_fields(self, conversation_file_path):
        return read_preview_fields_from_codex_conversation_file(conversation_file_path)

    def build_launch_command_for_a_copy(self, launch_command, conversation_id, claude_agent_record=None):
        """Continues a copy of the conversation (`codex fork <id>`)."""
        if not conversation_id:
            return launch_command
        return f"{launch_command} fork {shlex.quote(conversation_id)}"
