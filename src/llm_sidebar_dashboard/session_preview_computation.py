"""The one-line preview under each tab row (R23; user request, 2026-10-19), read from the end of
the agent session's own conversation file, the same file the context size comes from
(context_window_usage).

In order of preference the preview shows what the agent asks permission for (the tool and its
main input, or the question it asks), the tool it is running, the start of what it said since
the last prompt, and the last prompt itself ("You: ...").
"""

import json
import re

from llm_sidebar_dashboard.context_window_usage import read_last_lines

LONGEST_PREVIEW_TEXT = 300  # the row cuts it to its width anyway
TOOL_INPUT_KEYS_IN_PREFERENCE_ORDER = ("command", "file_path", "notebook_path", "path", "pattern", "url", "query",
                                       "description", "prompt", "skill")
# A line holding only a time, such as "[10/19/2026 09:19:52 AM EDT]", which some users have every reply start with.
BRACKETED_TIME_LINE = re.compile(r"^\[[^\]]*\d{1,2}:\d{2}[^\]]*\]$")
MARKDOWN_LINE_START = re.compile(r"^(?:#+|>|[-*+]|\d+[.)])\s+")
# User records that Claude Code writes itself: slash command output, caveats, interrupt notes.
GENERATED_USER_TEXT_STARTS = ("<", "[Request interrupted", "Caveat:")
# A slash command or skill the user typed is written as "<command-message>…<command-name>/x</command-name>
# <command-args>…</command-args>": a prompt. A background task's "<task-notification>" also starts a turn.
COMMAND_NAME_PATTERN = re.compile(r"<command-name>(.*?)</command-name>", re.S)
COMMAND_ARGUMENTS_PATTERN = re.compile(r"<command-args>(.*?)</command-args>", re.S)
TURN_STARTING_NOTIFICATION_START = "<task-notification>"
# The command in a Codex "exec" tool call, whose input is JavaScript such as tools.exec_command({cmd:"ls -la"}).
CODEX_EXEC_COMMAND = re.compile(r'\bcmd\s*:\s*"((?:[^"\\]|\\.)*)"')


def make_one_line(text):
    """Returns text as one line for a preview: blank lines and lines holding only a bracketed
    time are dropped, Markdown heading, quote and list markers and bold markers are removed,
    runs of whitespace become one space, and the result is cut to LONGEST_PREVIEW_TEXT."""
    kept_lines = []
    for line in str(text).replace("**", "").split("\n"):
        line = MARKDOWN_LINE_START.sub("", line.strip())
        if line and not BRACKETED_TIME_LINE.match(line):
            kept_lines.append(line)
    return " ".join(" ".join(kept_lines).split())[:LONGEST_PREVIEW_TEXT]


def describe_claude_code_tool_call(tool_name, tool_input):
    """Returns "Bash: python3 -m unittest": the tool's name and its main input (the command, file,
    pattern and so on), or the question an AskUserQuestion call asks."""
    if not isinstance(tool_input, dict):
        return tool_name
    questions = tool_input.get("questions")
    if tool_name == "AskUserQuestion" and isinstance(questions, list) and questions and isinstance(questions[0], dict):
        return make_one_line(questions[0].get("question") or "") or tool_name
    for key in TOOL_INPUT_KEYS_IN_PREFERENCE_ORDER:
        if isinstance(tool_input.get(key), str) and tool_input[key].strip():
            return f"{tool_name}: {make_one_line(tool_input[key])}"
    return tool_name


def describe_codex_tool_call(payload):
    """Returns "exec: ls -la" for a Codex function_call or custom_tool_call record's payload: the
    tool's name and its command, or "" for a call without one (Codex polls a running command
    with write_stdin and wait calls)."""
    tool_name = payload.get("name") or "tool"
    command = None
    if payload.get("type") == "function_call":
        try:
            arguments = json.loads(payload.get("arguments") or "{}")
        except ValueError:
            arguments = {}
        command = arguments.get("cmd") or arguments.get("command") if isinstance(arguments, dict) else None
        if isinstance(command, list):
            command = " ".join(str(part) for part in command)
    else:
        command_match = CODEX_EXEC_COMMAND.search(str(payload.get("input") or ""))
        if command_match:
            try:
                command = json.loads(f'"{command_match.group(1)}"')
            except ValueError:
                command = command_match.group(1)
    return f"{tool_name}: {make_one_line(command)}" if isinstance(command, str) and command.strip() else ""


def join_codex_item_texts(item):
    """Returns the text of a Codex 0.163 UserMessage or AgentMessage item (its "text" or "Text" parts)."""
    content = item.get("content")
    return "\n".join(str(part.get("text") or "") for part in content if isinstance(part, dict)
                     and str(part.get("type")).lower() == "text") if isinstance(content, list) else ""


def build_preview_fields(last_prompt, message_since_prompt, open_tool_call):
    """Returns the preview fields dict that the readers below return."""
    return {"last_prompt": last_prompt, "message_since_prompt": message_since_prompt, "open_tool_call": open_tool_call}


def read_preview_fields_from_claude_code_transcript(file_path):
    """Returns the preview fields from the end of a Claude Code transcript.

    Records are read newest first, back to the last prompt. Each assistant record holds one
    block (text, thinking or tool_use); a tool_use is open while no later user record holds its
    tool_result and the agent has written no text since. A slash command or skill the user typed
    is a prompt ("/name arguments"); a background task's notification starts a turn too, whose
    prompt is then the last one typed. Subagent records, Claude Code's own other user records
    (GENERATED_USER_TEXT_STARTS, meta records, the summary after /compact) and tool results are
    not prompts. When the prompt is older than the part read, or a notification started the
    turn, the "last-prompt" record that Claude Code keeps near the end supplies it.

    Returns:
        {"last_prompt": str, "message_since_prompt": str, "open_tool_call": str}, each "" when
        not found; texts are one line (make_one_line).
    """
    answered_tool_use_ids = set()
    message_since_prompt = ""
    open_tool_call = ""
    saved_last_prompt = ""
    turn_started_with_a_notification = False  # then only the prompt is still looked for
    for line in read_last_lines(file_path):
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if not isinstance(record, dict) or record.get("isSidechain"):
            continue
        if record.get("type") == "last-prompt":
            saved_last_prompt = saved_last_prompt or make_one_line(record.get("lastPrompt") or "")
            if turn_started_with_a_notification:
                break
            continue
        content = (record.get("message") or {}).get("content") if isinstance(record.get("message"), dict) else None
        blocks = content if isinstance(content, list) else [{"type": "text", "text": content}] if isinstance(content, str) else []
        blocks = [block for block in blocks if isinstance(block, dict)]
        if record.get("type") == "user" and not record.get("isMeta") and not record.get("isCompactSummary"):
            tool_result_ids = {block.get("tool_use_id") for block in blocks if block.get("type") == "tool_result"}
            if tool_result_ids:
                answered_tool_use_ids |= tool_result_ids
                continue
            prompt_text = "\n".join(str(block.get("text") or "") for block in blocks if block.get("type") == "text")
            command_name_match = COMMAND_NAME_PATTERN.search(prompt_text)
            if command_name_match:
                command_arguments_match = COMMAND_ARGUMENTS_PATTERN.search(prompt_text)
                command = f"{command_name_match.group(1)} {command_arguments_match.group(1) if command_arguments_match else ''}"
                return build_preview_fields(make_one_line(command), message_since_prompt, open_tool_call)
            if prompt_text.lstrip().startswith(TURN_STARTING_NOTIFICATION_START):
                turn_started_with_a_notification = True
                if saved_last_prompt:
                    break
                continue
            if prompt_text.strip() and not prompt_text.lstrip().startswith(GENERATED_USER_TEXT_STARTS):
                return build_preview_fields(make_one_line(prompt_text), message_since_prompt, open_tool_call)
        elif record.get("type") == "assistant" and not turn_started_with_a_notification:
            for block in reversed(blocks):
                if block.get("type") == "text" and not message_since_prompt and str(block.get("text") or "").strip():
                    message_since_prompt = make_one_line(block["text"])
                elif (block.get("type") == "tool_use" and not open_tool_call and not message_since_prompt
                      and block.get("id") not in answered_tool_use_ids):
                    open_tool_call = describe_claude_code_tool_call(block.get("name") or "tool", block.get("input"))
    return build_preview_fields(saved_last_prompt, message_since_prompt, open_tool_call)


def read_preview_fields_from_codex_conversation_file(file_path):
    """Returns the preview fields from the end of a Codex conversation file (rollout-*.jsonl).

    Records are read newest first, back to the last prompt: an "item_completed" event with a
    UserMessage item (Codex 0.163) or a "user_message" event (earlier versions). What the agent
    said comes from AgentMessage items or "agent_message" events. A function_call or
    custom_tool_call with a command is open while no later output record has its call_id and
    the agent has said nothing since.

    Returns:
        {"last_prompt": str, "message_since_prompt": str, "open_tool_call": str}, each "" when
        not found; texts are one line (make_one_line).
    """
    answered_call_ids = set()
    message_since_prompt = ""
    open_tool_call = ""
    for line in read_last_lines(file_path):
        try:
            record = json.loads(line)
        except ValueError:
            continue
        payload = record.get("payload") if isinstance(record, dict) else None
        if not isinstance(payload, dict):
            continue
        payload_type = payload.get("type")
        if record.get("type") == "event_msg":
            item = payload.get("item") if payload_type == "item_completed" and isinstance(payload.get("item"), dict) else {}
            prompt_text = (str(payload.get("message") or "") if payload_type == "user_message"
                           else join_codex_item_texts(item) if item.get("type") == "UserMessage" else "")
            if prompt_text.strip():
                return build_preview_fields(make_one_line(prompt_text), message_since_prompt, open_tool_call)
            agent_text = (str(payload.get("message") or "") if payload_type == "agent_message"
                          else join_codex_item_texts(item) if item.get("type") == "AgentMessage" else "")
            if agent_text.strip() and not message_since_prompt:
                message_since_prompt = make_one_line(agent_text)
        elif record.get("type") == "response_item":
            if payload_type in ("function_call_output", "custom_tool_call_output"):
                answered_call_ids.add(payload.get("call_id"))
            elif (payload_type in ("function_call", "custom_tool_call") and not open_tool_call and not message_since_prompt
                  and payload.get("call_id") not in answered_call_ids):
                open_tool_call = describe_codex_tool_call(payload)  # "" for a call without a command: look further
    return build_preview_fields("", message_since_prompt, open_tool_call)


def choose_preview_text(state, preview_fields):
    """Returns the preview line for an agent session in this state (R23): the open tool call
    while it works or asks (the tool asking permission, or the question), else what it said
    since the last prompt, else the last prompt ("You: ..."), else "".

    Args:
        state: The session's state (session_status_computation.STATES_IN_URGENCY_ORDER).
        preview_fields: From the adapter's read_preview_fields, or None before the file was read.
    """
    preview_fields = preview_fields or {}
    if state in ("asking", "working", "stuck") and preview_fields.get("open_tool_call"):
        return preview_fields["open_tool_call"]
    if preview_fields.get("message_since_prompt"):
        return preview_fields["message_since_prompt"]
    if preview_fields.get("last_prompt"):
        return "You: " + preview_fields["last_prompt"]
    return ""
