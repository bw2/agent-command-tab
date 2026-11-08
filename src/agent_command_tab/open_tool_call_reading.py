"""The tool call an agent session is waiting on, if any, read from the end of its own
conversation file, the same file the context size comes from (context_window_usage). It names
the tool a working session waits on: a tool call open for a while shows as "running"
(snapshot_builder.choose_tool_the_agent_is_waiting_on).
"""

from agent_command_tab.claude_code_transcript_records import (RECORD_KINDS_THAT_START_A_TURN, classify_user_record,
                                                              list_message_blocks)
from agent_command_tab.context_window_usage import parse_iso_time, read_last_records


def build_open_tool_call_fields(open_tool_name, open_tool_started_at):
    """Returns the dict the readers below return."""
    return {"open_tool_name": open_tool_name, "open_tool_started_at": open_tool_started_at}


def read_record_time(record):
    """Returns a conversation file record's "timestamp", which Claude Code and Codex both write in
    ISO 8601 ("2026-10-28T11:12:04.784Z"), as seconds since 1970, or None without a readable one.
    parse_iso_time reads the trailing "Z", which datetime.fromisoformat does only from Python 3.11."""
    return parse_iso_time(record.get("timestamp")) or None


def read_open_tool_call_from_claude_code_transcript(file_path):
    """Returns the open tool call at the end of a Claude Code transcript.

    Records are read newest first, back to the start of the current turn. Each assistant record
    holds one block (text, thinking or tool_use); a tool_use is open while no later user record
    holds its tool_result and the agent has written no text since. Which user records start a
    turn is claude_code_transcript_records.classify_user_record's rule: a prompt, a slash command
    or skill the user typed, or a notification (a background task's, or another session's message).

    Returns:
        {"open_tool_name": str, "open_tool_started_at": float or None}: the open call's tool and
        when its record was written (read_record_time), or "" and None when no call is open.
    """
    answered_tool_use_ids = set()
    for record in read_last_records(file_path):
        if record.get("isSidechain"):
            continue
        blocks = list_message_blocks(record)
        user_record_kind = classify_user_record(record, blocks)
        if user_record_kind == "tool_result":
            answered_tool_use_ids |= {block.get("tool_use_id") for block in blocks if block.get("type") == "tool_result"}
        elif user_record_kind in RECORD_KINDS_THAT_START_A_TURN:
            break
        elif record.get("type") == "assistant":
            for block in reversed(blocks):
                if block.get("type") == "text" and str(block.get("text") or "").strip():
                    return build_open_tool_call_fields("", None)  # it wrote text after its last call
                if block.get("type") == "tool_use" and block.get("id") not in answered_tool_use_ids:
                    return build_open_tool_call_fields(block.get("name") or "tool", read_record_time(record))
    return build_open_tool_call_fields("", None)


def join_codex_item_texts(item):
    """Returns the text of a Codex 0.163 UserMessage or AgentMessage item (its "text" or "Text" parts)."""
    content = item.get("content")
    return "\n".join(str(part.get("text") or "") for part in content if isinstance(part, dict)
                     and str(part.get("type")).lower() == "text") if isinstance(content, list) else ""


def read_open_tool_call_from_codex_conversation_file(file_path):
    """Returns the open tool call at the end of a Codex conversation file (rollout-*.jsonl).

    Records are read newest first, back to the last prompt: an "item_completed" event with a
    UserMessage item (Codex 0.163) or a "user_message" event (earlier versions). A function_call
    or custom_tool_call of any kind (also one without a command, such as a wait on a command that
    runs) is open while no later output record has its call_id and the agent has said nothing
    since (an AgentMessage item or an "agent_message" event).

    Returns:
        The dict read_open_tool_call_from_claude_code_transcript returns.
    """
    answered_call_ids = set()
    for record in read_last_records(file_path):
        payload = record.get("payload")
        if not isinstance(payload, dict):
            continue
        payload_type = payload.get("type")
        if record.get("type") == "event_msg":
            item = payload.get("item") if payload_type == "item_completed" and isinstance(payload.get("item"), dict) else {}
            prompt_text = (str(payload.get("message") or "") if payload_type == "user_message"
                           else join_codex_item_texts(item) if item.get("type") == "UserMessage" else "")
            agent_text = (str(payload.get("message") or "") if payload_type == "agent_message"
                          else join_codex_item_texts(item) if item.get("type") == "AgentMessage" else "")
            if prompt_text.strip() or agent_text.strip():
                break
        elif record.get("type") == "response_item":
            if payload_type in ("function_call_output", "custom_tool_call_output"):
                answered_call_ids.add(payload.get("call_id"))
            elif payload_type in ("function_call", "custom_tool_call") and payload.get("call_id") not in answered_call_ids:
                return build_open_tool_call_fields(payload.get("name") or "tool", read_record_time(record))
    return build_open_tool_call_fields("", None)
