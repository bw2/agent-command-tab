"""How much of its context window each agent session uses, read from the end of its own
conversation file (user request, milestone 2: a tokens column, for example "425k").

- Claude Code writes a transcript, <config folder>/projects/<folder>/<session ID>.jsonl; the
  latest reply's usage (input, cache-write and cache-read tokens) is the context in use, the
  same number its status line shows as total_input_tokens.
- Codex writes ~/.codex/sessions/YYYY/MM/DD/rollout-<time>-<ID>.jsonl with "token_count"
  events whose last_token_usage.total_tokens is the context in use (find_codex_conversation_files).
- The context window's size, for the share in use (user request, 2026-10-26: "134k (13%)" in the
  Tokens column): Codex reports it with its usage; for Claude Code it follows from the model of the
  latest reply (read_model_from_claude_code_transcript) and from the model the session was started
  with, from its arguments, environment or settings (read_claude_code_model_setting).

Only the last BYTES_TO_READ_FROM_THE_END bytes are read, since transcripts reach tens of megabytes.
"""

import datetime
import glob
import json
import os
import re

from agent_command_tab.claude_code_transcript_records import (RECORD_KINDS_THAT_START_A_TURN, classify_user_record,
                                                              list_message_blocks)

BYTES_TO_READ_FROM_THE_END = 512 * 1024
# A file whose newest record is longer than this many bytes gives no lines (read_last_lines).
LARGEST_BYTE_COUNT_TO_READ_FROM_THE_END = 16 * 1024 * 1024
CLAUDE_CODE_CONTEXT_WINDOW_TOKENS = 200_000
CLAUDE_CODE_1M_CONTEXT_WINDOW_TOKENS = 1_000_000
# The newest version in each Claude model family whose context window is 200k unless chosen with
# the "[1m]" suffix, from Claude Code 2.1.314's built-in model list. Newer models, and models not
# listed, have a 1M window (user request, 2026-10-30: claude-opus-5-5 showed "175k (87%)" while
# its status line said 1000k).
NEWEST_CLAUDE_MODEL_VERSIONS_WITH_A_200K_CONTEXT_WINDOW = {"opus": (4, 6), "sonnet": (4, 6), "haiku": (4, 5)}
# "claude-opus-4-6", "claude-haiku-4-5-20251001", "claude-opus-4-20250514" (4.0): a minor version
# has one or two digits, so a date isn't taken for one.
CLAUDE_MODEL_FAMILY_AND_VERSION_PATTERN = re.compile(r"claude-(opus|sonnet|haiku)-(\d+)(?:-(\d{1,2}))?(?!\d)")


def read_last_lines(file_path, byte_count=BYTES_TO_READ_FROM_THE_END):
    """Returns the file's last complete lines within its last byte_count bytes, newest first. When
    those bytes hold no complete line, as when the newest record is a large tool result, it reads
    four times as many, up to LARGEST_BYTE_COUNT_TO_READ_FROM_THE_END (code review, 2026-10-29)."""
    while True:
        try:
            with open(file_path, "rb") as file:
                file.seek(0, os.SEEK_END)
                size = file.tell()
                file.seek(max(0, size - byte_count))
                text = file.read().decode(errors="replace")
        except OSError:
            return []
        # Only "\n" ends a line: JSON leaves U+2028 and U+0085 inside strings, which splitlines() would split at.
        lines = [line for line in text.split("\n") if line]
        if size > byte_count:
            lines = lines[1:]  # the first line was cut
        if lines or size <= byte_count or byte_count >= LARGEST_BYTE_COUNT_TO_READ_FROM_THE_END:
            return list(reversed(lines))
        byte_count = min(byte_count * 4, LARGEST_BYTE_COUNT_TO_READ_FROM_THE_END)


def read_last_records(file_path):
    """Yields the JSON objects of a conversation file's last complete lines (read_last_lines),
    newest first, skipping lines that are not JSON objects."""
    for line in read_last_lines(file_path):
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict):
            yield record


def read_context_tokens_from_claude_code_transcript(file_path):
    """Returns the tokens in context after the latest reply in a Claude Code transcript, or after
    the latest compaction (/compact, or an automatic one) when no reply came since, or None."""
    for record in read_last_records(file_path):
        if record.get("type") == "system" and record.get("subtype") == "compact_boundary" and not record.get("isSidechain"):
            post_compaction_tokens = (record.get("compactMetadata") or {}).get("postTokens")
            if isinstance(post_compaction_tokens, int):
                return post_compaction_tokens
        message = (record.get("message") or {}) if record.get("type") == "assistant" else {}
        usage = message.get("usage")
        if not isinstance(usage, dict) or message.get("model") == "<synthetic>":
            continue  # Claude Code writes errors (API errors, spend limits) as replies with all-zero usage
        context_tokens = (usage.get("input_tokens", 0) + usage.get("cache_creation_input_tokens", 0)
                          + usage.get("cache_read_input_tokens", 0))
        if context_tokens:
            return context_tokens
    return None


def read_context_tokens_from_codex_conversation_file(file_path):
    """Returns the tokens in context after the latest turn in a Codex conversation file, or None."""
    for record in read_last_records(file_path):
        payload = record.get("payload")
        if isinstance(payload, dict) and payload.get("type") == "token_count":
            last_token_usage = (payload.get("info") or {}).get("last_token_usage") or {}
            if "total_tokens" in last_token_usage:
                return last_token_usage["total_tokens"]
    return None


def read_context_window_tokens_from_codex_conversation_file(file_path):
    """Returns the size of the context window Codex reports with its latest turn's usage
    (model_context_window, for example 258400), or None."""
    for record in read_last_records(file_path):
        payload = record.get("payload")
        if isinstance(payload, dict) and payload.get("type") == "token_count":
            model_context_window = (payload.get("info") or {}).get("model_context_window")
            if isinstance(model_context_window, int) and model_context_window > 0:
                return model_context_window
    return None


def read_claude_code_model_setting(process_arguments, process_environment, config_folder):
    """Returns the model a Claude Code session was started with, in the order Claude Code goes by:
    its `--model` argument, else its ANTHROPIC_MODEL environment variable, else the "model" in its
    config folder's settings.json (for example "opus[1m]"), else None. Its transcript names the
    model without saying whether its context window is the 1M one."""
    for index, argument in enumerate(process_arguments):
        if argument == "--model" and index + 1 < len(process_arguments):
            return process_arguments[index + 1]
        if argument.startswith("--model="):
            return argument.partition("=")[2]
    if process_environment.get("ANTHROPIC_MODEL"):
        return process_environment["ANTHROPIC_MODEL"]
    try:
        with open(os.path.join(config_folder or os.path.expanduser("~/.claude"), "settings.json")) as settings_file:
            model = json.load(settings_file).get("model")
    except (OSError, ValueError, AttributeError):
        return None
    return model if isinstance(model, str) else None


def read_model_from_claude_code_transcript(file_path):
    """Returns the model of the latest reply in a Claude Code transcript (for example
    "claude-opus-5-5", without the "[1m]" suffix it may have been chosen with), or None."""
    for record in read_last_records(file_path):
        model = (record.get("message") or {}).get("model") if record.get("type") == "assistant" else None
        if isinstance(model, str) and model != "<synthetic>":
            return model
    return None


def claude_model_has_a_200k_context_window(model):
    """Returns whether a Claude model's context window is 200k unless chosen with the "[1m]"
    suffix: a Claude 3 model, or one up to NEWEST_CLAUDE_MODEL_VERSIONS_WITH_A_200K_CONTEXT_WINDOW
    in its family, in its Bedrock and Vertex spellings too ("us.anthropic.claude-opus-4-6-v1")."""
    if "claude-3-" in model:
        return True
    match = CLAUDE_MODEL_FAMILY_AND_VERSION_PATTERN.search(model)
    return bool(match) and (int(match[2]), int(match[3] or 0)) <= NEWEST_CLAUDE_MODEL_VERSIONS_WITH_A_200K_CONTEXT_WINDOW[match[1]]


def choose_claude_code_context_window_tokens(model_setting, model, context_tokens):
    """Returns the size of a Claude Code session's context window: 1M for a model chosen with the
    "[1m]" suffix, once the context holds more than 200k tokens (a 1M model chosen some other
    way), or when its latest reply's model isn't one with a 200k window
    (claude_model_has_a_200k_context_window), else 200k.

    Args:
        model_setting: The model the session was started with (read_claude_code_model_setting), or None.
        model: The model of its latest reply (read_model_from_claude_code_transcript), or None.
        context_tokens: The tokens in its context, or None.
    """
    if ((model_setting or "").lower().endswith("[1m]") or (context_tokens or 0) > CLAUDE_CODE_CONTEXT_WINDOW_TOKENS
            or not claude_model_has_a_200k_context_window(model or "")):
        return CLAUDE_CODE_1M_CONTEXT_WINDOW_TOKENS
    return CLAUDE_CODE_CONTEXT_WINDOW_TOKENS


def read_turn_started_at_from_claude_code_transcript(file_path):
    """Returns when the current turn began: the newest record that starts a turn (a prompt, a
    typed command or a background task's notification; claude_code_transcript_records), or
    None if none is in the file's end."""
    for record in read_last_records(file_path):
        if (classify_user_record(record, list_message_blocks(record)) in RECORD_KINDS_THAT_START_A_TURN
                and parse_iso_time(record.get("timestamp"))):
            return parse_iso_time(record.get("timestamp"))
    return None


def read_turn_started_at_from_codex_conversation_file(file_path):
    """Returns when the current turn began (the newest "task_started" event), or None."""
    for record in read_last_records(file_path):
        payload = record.get("payload")
        if isinstance(payload, dict) and payload.get("type") == "task_started" and parse_iso_time(record.get("timestamp")):
            return parse_iso_time(record.get("timestamp"))
    return None


def find_claude_code_transcript(claude_config_folders, claude_session_id):
    """Returns the path of a Claude Code session's transcript, or None if it isn't found."""
    for config_folder in claude_config_folders:
        matches = glob.glob(os.path.join(glob.escape(config_folder), "projects", "*", glob.escape(claude_session_id) + ".jsonl"))
        if matches:
            return matches[0]
    return None


def find_codex_conversation_files(codex_sessions_folder, agent_started_at, agent_working_folder, now,
                                  agent_resumed_a_conversation=False):
    """Returns the files that may be a Codex session's conversation, most recently written first;
    empty before the first prompt (Codex writes the file then, and doesn't keep it open, so lsof
    can't find it).

    They are the rollout-*.jsonl files in the day folders (YYYY/MM/DD, local time) from the
    agent's start to today whose first line, the session_meta header, is an interactive
    session's (source "cli", not the auto-review helper's or `codex exec`'s), names the
    agent's folder, and starts no earlier than the agent process. The first is the current
    conversation (Codex's /new starts another). Two Codex sessions in one folder can't be
    told apart (a known limit), so callers that must be sure check that there is only one.

    `codex resume` goes on writing the earlier conversation's file, which stays in its own day
    folder with its own header: for such a process, every day folder is searched, and a file
    written since the process started fits whatever start its header names.

    Args:
        codex_sessions_folder: Codex's sessions folder ($CODEX_HOME/sessions).
        agent_started_at: When the agent process started.
        agent_working_folder: The agent process's current folder.
        now: The current time.
        agent_resumed_a_conversation: Whether the agent process was started with `codex resume`.
    """
    if agent_resumed_a_conversation:
        paths = glob.glob(os.path.join(glob.escape(codex_sessions_folder), "*", "*", "*", "rollout-*.jsonl"))
    else:
        paths = []
        day = datetime.date.fromtimestamp(agent_started_at)
        while day <= datetime.date.fromtimestamp(now):
            day_folder = os.path.join(codex_sessions_folder, f"{day.year:04d}", f"{day.month:02d}", f"{day.day:02d}")
            paths += glob.glob(os.path.join(glob.escape(day_folder), "rollout-*.jsonl"))
            day += datetime.timedelta(days=1)
    candidates = []
    for path in paths:
        try:
            modified_at = os.path.getmtime(path)
        except OSError:
            continue
        if modified_at >= agent_started_at:
            candidates.append((modified_at, path))
    conversation_files = []
    for _, path in sorted(candidates, reverse=True):
        header = read_codex_session_header(path)
        if (header.get("source") == "cli" and header.get("cwd") == agent_working_folder
                and (agent_resumed_a_conversation or parse_iso_time(header.get("timestamp")) >= agent_started_at - 5)):
            conversation_files.append(path)
    return conversation_files


def read_codex_session_header(file_path):
    """Returns the session_meta payload from a Codex conversation file's first line, or {}."""
    try:
        with open(file_path) as file:
            record = json.loads(file.readline())
    except (OSError, ValueError):
        return {}
    return record.get("payload") or {} if isinstance(record, dict) and record.get("type") == "session_meta" else {}


def parse_iso_time(text):
    """Returns seconds since the epoch for "2026-10-19T02:14:42.315Z", or 0 if it can't be read."""
    try:
        return datetime.datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except (AttributeError, ValueError):
        return 0


def format_token_count(token_count):
    """Returns a short token count: "425k", or "1.2M" from 999,500 up (which "k" would round to "1000k")."""
    if token_count >= 999_500:
        return f"{token_count / 1_000_000:.1f}M"
    return f"{round(token_count / 1000)}k"
