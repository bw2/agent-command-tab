"""When the last message of each agent session's conversation was written, read from the end of
its own conversation file, the same file the context size comes from (context_window_usage). The
Last Msg column shows how long ago that was. It comes from the file rather than from what the app
saw happen, so looking at a tab doesn't start the count again, and it is right after the app restarts.

A message is what the user or the agent put into the conversation, as each tool's file records it:

- Claude Code writes the conversation itself as "user" and "assistant" records: the user's prompts
  and typed commands, the agent's replies and tool calls, the tools' results, and its own notes in
  the conversation (a turn the user interrupted, the summary after /compact). Every such record of
  the main conversation counts, so the time is that of the last thing either side did. What it
  keeps beside the conversation (attachments, hook summaries, costs, settings, snapshots of files)
  has other record types, and a subagent's records belong to another conversation.
- Codex writes each prompt and each reply as a record of its own
  (open_tool_call_reading.read_codex_message_text), and those count. Its "response_item" messages
  don't: they also hold what Codex itself gives the model (its instructions, the AGENTS.md text,
  the environment's description).
- agy writes one record per step: "USER_INPUT" for a prompt and "PLANNER_RESPONSE" for what the
  model answered, a reply or tool calls. Those two count; the tools' results and agy's own steps
  (system messages, API errors, checkpoints) don't. A step's "created_at" is when it began, so a
  reply's time is when the model started on it (seen in agy 1.5.10).

Each reader returns the time of the newest such record in the file's end
(context_window_usage.read_last_records) as seconds since 1970, or None when the file's end holds
no message, or when that record has no time that can be read.
"""

from agent_command_tab.context_window_usage import parse_iso_time, read_last_records
from agent_command_tab.open_tool_call_reading import read_codex_message_text, read_record_time

CLAUDE_CODE_MESSAGE_RECORD_TYPES = ("user", "assistant")
AGY_MESSAGE_RECORD_TYPES = ("USER_INPUT", "PLANNER_RESPONSE")


def read_last_message_at_from_claude_code_transcript(file_path):
    """Returns when the newest "user" or "assistant" record of the main conversation in a Claude
    Code transcript's end was written, or None."""
    for record in read_last_records(file_path):
        if record.get("type") in CLAUDE_CODE_MESSAGE_RECORD_TYPES and not record.get("isSidechain"):
            return read_record_time(record)
    return None


def read_last_message_at_from_codex_conversation_file(file_path):
    """Returns when the newest prompt or reply in a Codex conversation file's end was written, or None."""
    for record in read_last_records(file_path):
        if read_codex_message_text(record).strip():
            return read_record_time(record)
    return None


def read_last_message_at_from_agy_transcript(file_path):
    """Returns when the newest prompt or model answer in an agy transcript's end began, or None."""
    for record in read_last_records(file_path):
        if record.get("type") in AGY_MESSAGE_RECORD_TYPES:
            return parse_iso_time(record.get("created_at")) or None
    return None
