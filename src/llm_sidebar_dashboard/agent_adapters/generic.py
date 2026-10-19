"""Adapter for agent tools that have no adapter of their own (PLAN.md section 11, R22)."""

from llm_sidebar_dashboard.agent_adapters.base import (AgentToolAdapter, command_line_runs_a_shell,
                                                       split_command_line_into_program_names)


class GenericAdapter(AgentToolAdapter):
    """A tab counts as an agent session of an unknown tool when its foreground job is not a shell
    and its iTerm2 `status` variable holds a recognized value. The tool is named after the
    command line's first word. A command line alone never qualifies, because nothing would tell
    such a tool apart from any other program."""

    agent_tool_name = "Agent"

    def matches_command_line(self, command_line):
        """Returns whether the foreground job is something other than a shell (the status is checked separately)."""
        return bool(command_line) and not command_line_runs_a_shell(command_line)

    def name_agent_tool_for_command_line(self, command_line):
        """Returns the name shown for an unknown tool: the base name of the command line's first word."""
        program_names = split_command_line_into_program_names(command_line)
        return program_names[0] if program_names else self.agent_tool_name
