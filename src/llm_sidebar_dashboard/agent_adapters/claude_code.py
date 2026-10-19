"""Adapter for Anthropic's Claude Code."""

import re

from llm_sidebar_dashboard.agent_adapters.base import AgentToolAdapter, split_command_line_into_program_names

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

    def matches_command_line(self, command_line):
        """Returns whether the command line's program is `claude` (by base name, so full paths match)."""
        program_names = split_command_line_into_program_names(command_line)
        return bool(program_names) and program_names[0] == "claude"
