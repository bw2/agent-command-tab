"""Adapter for agy, Google's Antigravity CLI.

Checked with agy 1.5.9 and 1.5.10:
- agy reports its state to the terminal itself (OSC 7501), so iTerm2's status variable says
  working, waiting (while it asks for permission), done or idle, from its first message on.
- It keeps <its folder>/presence/<conversation ID>.lock open (its folder is
  ~/.gemini/antigravity-cli) for the conversation it is in, and for that one only: after /new,
  the earlier conversation's other files stay open, but its lock is closed. A fresh agy is in no
  conversation until its first message or a /new.
- A conversation's transcript, <its folder>/brain/<conversation ID>/.system_generated/logs/transcript.jsonl,
  appears with its first message, and so does transcript_full.jsonl beside it, which holds the
  same steps (AGY_WHOLE_TRANSCRIPT_FILE_NAME).
- `agy --conversation <ID>` resumes a conversation; agy has no way to continue a copy of one.
- A fresh agy shows a hint in its input box ("Auto mode: edits & safe commands auto-approved")
  until something is typed there. It is drawn in gray rather than faint, so the box reads as
  holding text (screen_readings) and no /compact is typed into it; there would be nothing to
  compact yet anyway.
"""

import asyncio
import os
import re
import shlex

from agent_command_tab.agent_adapters.base import AgentToolAdapter, split_command_line_into_program_names
from agent_command_tab.agent_process_tree import list_the_files_a_process_has_open
from agent_command_tab.context_window_usage import read_context_tokens_from_agy_transcript
from agent_command_tab.conversation_search_texts import extract_search_texts_from_agy_record
from agent_command_tab.last_message_time_reading import read_last_message_at_from_agy_transcript

# <agy's folder>/presence/<conversation ID>.lock
PRESENCE_LOCK_FILE_PATTERN = re.compile(
    r"^(.+)/presence/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.lock$")
# The line below agy's input box starts with "esc to cancel" while a turn runs, also while it asks
# for permission, and with "? for shortcuts" between turns; the model's name follows at its right end.
TURN_UNDER_WAY_SCREEN_PATTERN = re.compile(r"^esc to cancel\b")
# The file agy writes beside a conversation's transcript.jsonl, with the same steps. It holds each
# prompt, reply and tool call whole; transcript.jsonl has the middle cut out of each long one
# ("<truncated 5120 bytes>"), which the list's search would then miss.
AGY_WHOLE_TRANSCRIPT_FILE_NAME = "transcript_full.jsonl"
# agy's folder, where the conversation of a saved session is looked for: a running agy's own is
# read from the path of the lock it has open (PRESENCE_LOCK_FILE_PATTERN).
DEFAULT_AGY_FOLDER = os.path.expanduser("~/.gemini/antigravity-cli")


def build_agy_transcript_path(agy_folder, conversation_id):
    """Returns where agy writes a conversation's transcript.jsonl."""
    return os.path.join(agy_folder, "brain", conversation_id, ".system_generated", "logs", "transcript.jsonl")


def find_agy_transcript(agy_folder, conversation_id):
    """Returns the path of a conversation's whole transcript (AGY_WHOLE_TRANSCRIPT_FILE_NAME), or
    of its transcript.jsonl when agy wrote no whole one, or None while it has neither (before the
    conversation's first message, or once it was deleted)."""
    transcript_path = build_agy_transcript_path(agy_folder, conversation_id)
    whole_transcript_path = os.path.join(os.path.dirname(transcript_path), AGY_WHOLE_TRANSCRIPT_FILE_NAME)
    return next(filter(os.path.isfile, (whole_transcript_path, transcript_path)), None)


def find_agy_conversation_and_transcript(open_file_paths):
    """Returns (conversation ID, transcript path or None) for the conversation an agy process is
    in, from the paths of the files it has open: the transcript's path is None until the
    conversation's first message, then that of its whole transcript
    (AGY_WHOLE_TRANSCRIPT_FILE_NAME), or of transcript.jsonl when agy wrote no whole one.
    (None, None) when it is in no conversation yet; None when the
    files can't be read (open_file_paths is None) or name more than one conversation.

    Args:
        open_file_paths: The paths of the files the process has open, or None.
    """
    if open_file_paths is None:
        return None
    agy_folders_and_conversation_ids = {match.groups() for match in map(PRESENCE_LOCK_FILE_PATTERN.match, open_file_paths)
                                        if match}
    if not agy_folders_and_conversation_ids:
        return None, None
    if len(agy_folders_and_conversation_ids) > 1:
        return None
    agy_folder, conversation_id = next(iter(agy_folders_and_conversation_ids))
    return conversation_id, find_agy_transcript(agy_folder, conversation_id)


def find_agy_conversation_and_transcript_of_process(agent_process_id):
    """Returns find_agy_conversation_and_transcript's result for the files the agy process has
    open. It lists the process's open files and looks for the transcript on disk, so a caller on
    the core's event loop runs it in a worker thread."""
    open_files_by_file_descriptor = list_the_files_a_process_has_open(agent_process_id)
    return find_agy_conversation_and_transcript(
        None if open_files_by_file_descriptor is None else open_files_by_file_descriptor.values())


class AgyAdapter(AgentToolAdapter):
    """agy runs as `agy ...`, a single program that runs its turns, and the commands it starts,
    itself. Its permission prompts need no screen check: it reports "waiting" for exactly as
    long as one shows."""

    agent_tool_name = "agy"
    working_screen_pattern = TURN_UNDER_WAY_SCREEN_PATTERN
    slash_commands = ("/compact",)  # it starts a new conversation with /new, not /clear
    # The input box: ">", then the input, between two rules of "─", above "? for shortcuts".
    input_line_marker = ">"
    input_area_end_pattern = re.compile(r"^─{10,}\s*$")
    can_resume_conversations = True

    def matches_command_line(self, command_line):
        """Returns whether the command line's program is `agy` (by base name, so full paths match)."""
        program_names = split_command_line_into_program_names(command_line)
        return bool(program_names) and program_names[0] == "agy"

    async def find_conversation_and_transcript(self, agent_process_id):
        """Returns find_agy_conversation_and_transcript's result for the process, read in a
        worker thread (find_agy_conversation_and_transcript_of_process), or None without a process ID."""
        if not agent_process_id:
            return None
        return await asyncio.to_thread(find_agy_conversation_and_transcript_of_process, agent_process_id)

    async def find_conversation_id(self, agent_process_id, claude_agent_record):
        """Returns the ID of the conversation whose presence lock the process has open."""
        return (await self.find_conversation_and_transcript(agent_process_id) or (None, None))[0]

    async def find_conversation_file(self, agent_process_id, claude_agent_record, claude_config_folders):
        """Returns the transcript of the conversation the process is in, once it has one."""
        return (await self.find_conversation_and_transcript(agent_process_id) or (None, None))[1]

    def find_conversation_file_by_id(self, conversation_id, claude_config_folders, claude_config_folder=None, codex_home_folder=None):
        """Returns the conversation's transcript in agy's folder (find_agy_transcript)."""
        return find_agy_transcript(DEFAULT_AGY_FOLDER, conversation_id)

    async def find_whether_no_message_was_sent_in_its_conversation(self, agent_process_id):
        """True while the process is in no conversation, or in one without a transcript yet."""
        conversation_and_transcript = await self.find_conversation_and_transcript(agent_process_id)
        return None if conversation_and_transcript is None else conversation_and_transcript[1] is None

    def read_context_tokens_and_context_window_tokens(self, conversation_file_path, agent_process_id, claude_agent_record,
                                                      context_tokens_read_before):
        """agy doesn't say how large its models' context windows are."""
        return read_context_tokens_from_agy_transcript(conversation_file_path), None

    def read_last_message_at(self, conversation_file_path):
        return read_last_message_at_from_agy_transcript(conversation_file_path)

    def extract_search_texts_from_conversation_record(self, record):
        return extract_search_texts_from_agy_record(record)

    def build_launch_command_to_resume(self, launch_command, conversation_id, claude_config_folder=None, codex_home_folder=None):
        """`<launch command> --conversation <id>`."""
        return f"{launch_command} --conversation {shlex.quote(conversation_id)}"
