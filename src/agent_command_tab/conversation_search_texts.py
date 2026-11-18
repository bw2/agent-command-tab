"""The text of each agent session's conversation that the list's search box looks through: the
user's prompts, the agent's replies and the shell commands it ran, read from the conversation
file (not tool output, thinking, or the files the agent read or changed). Pure functions, except
read_new_search_texts, which reads a file.

The core reads each file once and then only the lines added since (read_new_search_texts), and
keeps the text of each session in memory (a ConversationSearchText): a Claude Code transcript of
68 MB held 0.66 million characters of prompts and replies, and searching all of that takes far
less than a millisecond.
"""

import collections
import json
import os
import re

from agent_command_tab.claude_code_transcript_records import (COMMAND_NAME_PATTERN, classify_user_record, join_text_blocks,
                                                              list_message_blocks)
from agent_command_tab.open_tool_call_reading import read_codex_message_text

# Claude Code's tool that runs shell commands.
CLAUDE_CODE_SHELL_TOOL_NAME = "Bash"
# What the user typed after a slash command or skill's name, in the record Claude Code writes for it
# (claude_code_transcript_records.COMMAND_NAME_PATTERN has the name).
CLAUDE_CODE_COMMAND_ARGUMENTS_PATTERN = re.compile(r"<command-args>(.*?)</command-args>", re.S)
# Earlier Codex versions' shell tool calls, whose arguments hold the command as "cmd" (a string)
# or "command" (a list of words, or a string).
CODEX_SHELL_FUNCTION_NAMES = frozenset({"shell", "exec_command", "local_shell", "container.exec"})
# agy wraps the user's prompt in this tag, followed by metadata it adds itself (the time,
# settings changes, a skill's text).
AGY_USER_REQUEST_PATTERN = re.compile(r"<USER_REQUEST>\s*(.*?)\s*</USER_REQUEST>", re.S)
AGY_SHELL_TOOL_NAME = "run_command"

ConversationSearchText = collections.namedtuple(
    "ConversationSearchText", ["path", "read_up_to_byte", "text", "casefolded_text"])
ConversationSearchText.__doc__ = ("The searchable text of one conversation file: its path, how far it has been read "
                                  "(a byte offset after a complete line), the text so far (one piece per line), and "
                                  "the same text casefolded, for a search that ignores case.")


def build_empty_conversation_search_text(path):
    """Returns a ConversationSearchText for a file not read yet."""
    return ConversationSearchText(path, 0, "", "")


def add_texts_to_conversation_search_text(conversation_search_text, new_texts, read_up_to_byte):
    """Returns the ConversationSearchText with new_texts added after its text, read up to read_up_to_byte."""
    added_text = "".join("\n" + text for text in new_texts)
    return conversation_search_text._replace(read_up_to_byte=read_up_to_byte,
                                             text=conversation_search_text.text + added_text,
                                             casefolded_text=conversation_search_text.casefolded_text + added_text.casefold())


def read_new_search_texts(file_path, start_byte, extract_search_texts_from_record):
    """Reads a conversation file's complete lines from start_byte on and returns their search texts.

    Args:
        file_path: The conversation file (JSON lines).
        start_byte: Where the last read ended; a file now shorter than that was rewritten, and is read from its start.
        extract_search_texts_from_record: The agent tool's function from one decoded record to its texts.

    Returns:
        (texts, the byte after the last complete line read, whether the file was read from its
        start because it got shorter), or None when the file can't be read.
    """
    texts = []
    try:
        with open(file_path, "rb") as file:
            file.seek(0, os.SEEK_END)
            started_over = file.tell() < start_byte
            if started_over:
                start_byte = 0
            file.seek(start_byte)
            read_up_to_byte = start_byte
            # A line at a time, since a file read for the first time may hold tens of megabytes,
            # nearly all of it tool output that has no search text.
            for line in file:
                if not line.endswith(b"\n"):
                    break  # a line still being written waits for the next read
                read_up_to_byte += len(line)
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                if isinstance(record, dict):
                    texts.extend(text for text in extract_search_texts_from_record(record) if text)
    except OSError:
        return None
    return texts, read_up_to_byte, started_over


def describe_claude_code_command_the_user_typed(text):
    """Returns a slash command or skill the user typed, as they typed it ("/code-review-local
    run_all_steps.py"), from the tags Claude Code writes it in."""
    return " ".join(match.group(1).strip() for match in (COMMAND_NAME_PATTERN.search(text), CLAUDE_CODE_COMMAND_ARGUMENTS_PATTERN.search(text))
                    if match and match.group(1).strip())


def extract_search_texts_from_claude_code_record(record):
    """Returns the texts of one Claude Code transcript record: a prompt, slash command or skill the
    user typed, the main agent's reply text and the commands its Bash tool ran (not a subagent's)."""
    blocks = list_message_blocks(record)
    if record.get("type") == "user":
        kind = classify_user_record(record, blocks)
        if kind == "prompt":
            return [join_text_blocks(blocks)]
        return [describe_claude_code_command_the_user_typed(join_text_blocks(blocks))] if kind == "command" else []
    if record.get("type") != "assistant" or record.get("isSidechain"):
        return []
    texts = []
    for block in blocks:
        if block.get("type") == "text":
            texts.append(str(block.get("text") or ""))
        elif block.get("type") == "tool_use" and block.get("name") == CLAUDE_CODE_SHELL_TOOL_NAME:
            command = (block.get("input") or {}).get("command")
            if isinstance(command, str):
                texts.append(command)
    return texts


def describe_codex_command(command):
    """Returns a Codex command as one line: a string as it is, a list of words as the last word
    when it runs through a shell (["/bin/bash", "-lc", "pwd"] -> "pwd"), else the words joined."""
    if isinstance(command, str):
        return command
    if not isinstance(command, list) or not all(isinstance(word, str) for word in command):
        return ""
    return command[-1] if len(command) >= 3 and command[-2] in ("-c", "-lc") else " ".join(command)


def extract_search_texts_from_codex_record(record):
    """Returns the texts of one Codex conversation file record: the user's prompt and the agent's
    reply (open_tool_call_reading.read_codex_message_text), and a command it ran (a
    CommandExecution item, or an earlier version's shell tool call)."""
    payload = record.get("payload")
    if not isinstance(payload, dict):
        return []
    payload_type = payload.get("type")
    message_text = read_codex_message_text(record)
    if message_text:
        return [message_text]
    if record.get("type") == "event_msg":
        item = payload.get("item") if payload_type == "item_completed" else None
        if isinstance(item, dict) and item.get("type") == "CommandExecution":
            # The whole command as the agent ran it ("pwd && ls"), else the parts Codex parsed it into.
            whole_command = describe_codex_command(item.get("command"))
            if whole_command:
                return [whole_command]
            parsed_commands = [part.get("cmd") for part in item.get("parsed_cmd") or [] if isinstance(part, dict)]
            return [command for command in parsed_commands if isinstance(command, str)]
        return []
    if record.get("type") == "response_item" and payload_type == "function_call" and payload.get("name") in CODEX_SHELL_FUNCTION_NAMES:
        try:
            arguments = json.loads(payload.get("arguments") or "{}")
        except ValueError:
            return []
        if isinstance(arguments, dict):
            return [describe_codex_command(arguments.get("cmd") or arguments.get("command"))]
    return []


def decode_agy_argument(value):
    """Returns an agy tool call's text argument as text ("" for an argument of another kind). Its
    transcript.jsonl has each argument as JSON ("\\"ls -la\\""), which is decoded; its whole
    transcript (agent_adapters.agy.AGY_WHOLE_TRANSCRIPT_FILE_NAME) has the text itself, which stays
    as it is, also when it reads as JSON of another kind (the command "true")."""
    if not isinstance(value, str):
        return ""
    try:
        decoded = json.loads(value)
    except ValueError:
        return value
    return decoded if isinstance(decoded, str) else value


def extract_search_texts_from_agy_record(record):
    """Returns the texts of one agy transcript record: the user's request (without the metadata agy
    adds after it), the model's reply and the commands its run_command tool ran."""
    content = record.get("content")
    if record.get("type") == "USER_INPUT" and isinstance(content, str):
        match = AGY_USER_REQUEST_PATTERN.search(content)
        return [match.group(1) if match else content]
    if record.get("type") != "PLANNER_RESPONSE":
        return []
    texts = [content] if isinstance(content, str) else []
    for tool_call in record.get("tool_calls") or []:
        if isinstance(tool_call, dict) and tool_call.get("name") == AGY_SHELL_TOOL_NAME:
            texts.append(decode_agy_argument((tool_call.get("args") or {}).get("CommandLine")))
    return texts


def text_contains(text, casefolded_text, search_text, is_case_sensitive):
    """Whether text holds search_text, ignoring case unless is_case_sensitive (casefolded_text is
    text.casefold(), made once)."""
    return search_text in text if is_case_sensitive else search_text.casefold() in casefolded_text


def find_session_ids_whose_conversations_contain(session_ids_and_conversation_search_texts, search_text, is_case_sensitive):
    """Returns the IDs of the sessions whose conversation text holds search_text (none for an empty one).

    Args:
        session_ids_and_conversation_search_texts: [(session ID, ConversationSearchText)].
        search_text: The text typed in the search box, matched as it is (one phrase).
        is_case_sensitive: Whether case counts (the "aA" checkbox).
    """
    if not search_text:
        return []
    return [session_id for session_id, conversation_search_text in session_ids_and_conversation_search_texts
            if text_contains(conversation_search_text.text, conversation_search_text.casefolded_text, search_text, is_case_sensitive)]
