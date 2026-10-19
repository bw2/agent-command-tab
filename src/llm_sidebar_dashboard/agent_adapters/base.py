"""What every agent tool adapter provides (PLAN.md section 11)."""

import os
import shlex

SHELL_PROGRAM_NAMES = {"sh", "bash", "zsh", "fish", "dash", "ksh", "tcsh", "csh", "nu", "xonsh", "elvish"}


def split_command_line_into_program_names(command_line):
    """Returns the base names of the words of a command line, for matching against tool names.

    A login shell's leading "-" (for example "-bash") is removed. Unbalanced quotes fall back to
    splitting on spaces.

    Args:
        command_line: iTerm2's `commandLine` variable for a session, or None.

    Returns:
        A list of base names, possibly empty.
    """
    if not command_line:
        return []
    try:
        words = shlex.split(command_line)
    except ValueError:
        words = command_line.split()
    return [os.path.basename(word).lstrip("-") for word in words]


def command_line_runs_a_shell(command_line):
    """Returns whether the foreground job described by command_line is a plain shell."""
    program_names = split_command_line_into_program_names(command_line)
    return bool(program_names) and program_names[0] in SHELL_PROGRAM_NAMES


class AgentToolAdapter:
    """Describes one agent tool. Subclasses set the class attributes and may override methods.

    Attributes:
        agent_tool_name: The name shown in the list, for example "Claude Code".
        permission_dialog_screen_patterns: Compiled patterns that all appear while the tool's
            permission dialog is showing; empty when the tool's own status already covers it.
        progress_noise_patterns: Compiled patterns for screen text that changes every second
            without real progress (screen_readings.compute_screen_fingerprint).
        interrupted_turn_screen_pattern: Compiled pattern for the line the tool prints when a
            permission prompt is denied or the turn is interrupted, or None.
    """

    agent_tool_name = ""
    permission_dialog_screen_patterns = ()
    progress_noise_patterns = ()
    interrupted_turn_screen_pattern = None

    def matches_command_line(self, command_line):
        """Returns whether a tab whose foreground job has this command line runs this tool.

        Args:
            command_line: iTerm2's `commandLine` variable, for example "claude --model haiku".
        """
        raise NotImplementedError

    def name_agent_tool_for_command_line(self, command_line):
        """Returns the tool name shown for a tab with this command line (the generic adapter overrides it)."""
        return self.agent_tool_name
