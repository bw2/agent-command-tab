"""Adapter for OpenAI's Codex CLI."""

import re

from llm_sidebar_dashboard.agent_adapters.base import AgentToolAdapter, split_command_line_into_program_names


class CodexAdapter(AgentToolAdapter):
    """Codex runs as `codex ...` (0.163) or through its Node.js wrapper, `node .../codex ...` (0.154).

    Codex 0.163 reports "waiting" itself exactly while its approval menu shows, so it needs no
    permission dialog check (PHASE0_RESULTS.md).
    """

    agent_tool_name = "Codex"
    progress_noise_patterns = (re.compile(r"\((?:\d+h )?(?:\d+m )?\d+s • esc to interrupt\)"),)

    def matches_command_line(self, command_line):
        """Returns whether the command line runs `codex`, directly or as `node <path>/codex`."""
        program_names = split_command_line_into_program_names(command_line)
        if not program_names:
            return False
        if program_names[0] == "codex":
            return True
        return program_names[0] == "node" and len(program_names) > 1 and program_names[1] in ("codex", "codex.js")
