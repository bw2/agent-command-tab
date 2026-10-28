"""Which records of a Claude Code transcript start a turn, the one rule for both readers of a
transcript's end: when the current turn began (context_window_usage) and the preview's last
prompt (session_preview_computation). Pure functions."""

import re

# The tags Claude Code wraps its own texts in user records with (seen in Claude Code 2.1
# transcripts): a local command's output and caveat, a "!" shell command and its output, and
# reminders. Its notes after an interrupt and its caveats start with plain words instead. Any
# other text is the user's, also one that starts with a tag (pasted content, HTML).
GENERATED_USER_TEXT_PATTERN = re.compile(
    r"^(?:<(?:local-command-stdout|local-command-stderr|local-command-caveat|bash-input|bash-stdout|bash-stderr"
    r"|system-reminder)>|\[Request interrupted|Caveat:)")
# A slash command or skill the user typed is written as "<command-message>…<command-name>/x</command-name>
# <command-args>…</command-args>".
COMMAND_NAME_PATTERN = re.compile(r"<command-name>(.*?)</command-name>", re.S)
COMMAND_ARGUMENTS_PATTERN = re.compile(r"<command-args>(.*?)</command-args>", re.S)
TURN_STARTING_NOTIFICATION_START = "<task-notification>"  # a background task's notification starts a turn too
RECORD_KINDS_THAT_START_A_TURN = frozenset({"prompt", "command", "notification"})


def list_message_blocks(record):
    """Returns the content blocks of a record's message, as dicts (a plain-text content is one text block)."""
    message = record.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    blocks = content if isinstance(content, list) else [{"type": "text", "text": content}] if isinstance(content, str) else []
    return [block for block in blocks if isinstance(block, dict)]


def join_text_blocks(blocks):
    """Returns the text of a record's text blocks, one per line."""
    return "\n".join(str(block.get("text") or "") for block in blocks if block.get("type") == "text")


def classify_user_record(record, blocks):
    """Returns what a transcript record is, when looking for where the current turn began.

    Args:
        record: One decoded transcript record.
        blocks: Its message blocks (list_message_blocks).

    Returns:
        "tool_result" for a tool's result; "prompt" for text the user typed; "command" for a
        slash command or skill the user typed (describe_typed_command); "notification" for a
        background task's notification or a message from another Claude Code session (its
        SendMessage tool's, a meta record with turnOrigin "peer"; code review, 2026-10-28); or
        None for anything else: not a user record, a subagent's record, another meta record
        (such as a skill's text, written in the middle of a turn), the summary after /compact,
        Claude Code's own texts, or an empty record.
    """
    if record.get("type") != "user" or record.get("isSidechain") or record.get("isCompactSummary"):
        return None
    if record.get("turnOrigin") == "peer":
        return "notification"
    if record.get("isMeta"):
        return None
    if any(block.get("type") == "tool_result" for block in blocks):
        return "tool_result"
    text = join_text_blocks(blocks).lstrip()
    if COMMAND_NAME_PATTERN.search(text):
        return "command"
    if text.startswith(TURN_STARTING_NOTIFICATION_START):
        return "notification"
    if text and not GENERATED_USER_TEXT_PATTERN.match(text):
        return "prompt"
    return None


def describe_typed_command(text):
    """Returns "/name arguments" for a typed slash command's record text."""
    command_arguments_match = COMMAND_ARGUMENTS_PATTERN.search(text)
    return f"{COMMAND_NAME_PATTERN.search(text).group(1)} {command_arguments_match.group(1) if command_arguments_match else ''}"
