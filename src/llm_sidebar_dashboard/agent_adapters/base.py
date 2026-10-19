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
        helper_process_names: Names of processes the tool starts for itself during a turn, which
            are not commands the agent waits on (agent_process_tree).
    """

    agent_tool_name = ""
    permission_dialog_screen_patterns = ()
    progress_noise_patterns = ()
    interrupted_turn_screen_pattern = None
    helper_process_names = frozenset()

    async def find_conversation_id(self, agent_process_id, claude_agent_record):
        """Returns the ID of the conversation the agent process is in, or None if unknown.

        Args:
            agent_process_id: The agent's process ID (iTerm2's jobPid).
            claude_agent_record: The process's record from `claude agents --json`, or None.
        """
        return None

    async def find_conversation_file(self, agent_process_id, claude_agent_record, claude_config_folders):
        """Returns the path of the file the tool writes the conversation to, or None if unknown.
        Runs on the core's event loop, so a lookup that reads folders or files does that in a
        worker thread (asyncio.to_thread).

        Args:
            agent_process_id: The agent's process ID (iTerm2's jobPid).
            claude_agent_record: The process's record from `claude agents --json`, or None.
            claude_config_folders: Folders that may hold Claude Code transcripts.
        """
        return None

    def read_context_tokens(self, conversation_file_path):
        """Returns how many tokens the conversation's context holds, from its file, or None.
        The core calls this, and read_turn_started_at, in a worker thread."""
        return None

    def read_turn_started_at(self, conversation_file_path):
        """Returns when the current turn began, from the conversation file, or None."""
        return None

    def build_launch_command_for_a_copy(self, launch_command, conversation_id, claude_agent_record=None):
        """Returns the command that starts a copy of a session (Duplicate): one that continues a
        copy of the conversation where the tool can fork it, else a fresh session.

        Args:
            launch_command: The tool's launch command from the settings.
            conversation_id: From find_conversation_id, or None.
            claude_agent_record: The original's record from `claude agents --json`, or None.
        """
        return launch_command

    def matches_command_line(self, command_line):
        """Returns whether a tab whose foreground job has this command line runs this tool.

        Args:
            command_line: iTerm2's `commandLine` variable, for example "claude --model haiku".
        """
        raise NotImplementedError

    def name_agent_tool_for_command_line(self, command_line):
        """Returns the tool name shown for a tab with this command line (the generic adapter overrides it)."""
        return self.agent_tool_name
