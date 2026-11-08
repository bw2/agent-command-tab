"""The saved agent sessions: conversations to resume later, saved from a row's "Save session" or
"Save sessions" and listed by the Restore sessions window (user request, 2026-11-06). They are
kept in a file next to the settings. Pure functions apart from the file reading and writing.

A saved window is the terminal window its sessions were saved from:
    {"terminal_name": "iTerm2" or "tmux", "terminal_window_id": the window's ID when saved,
     "terminal_window_opened_at": when that window was opened, which tells it from a later one
         given the same ID by a new tmux server ($0 again), or None in iTerm2, whose IDs are UUIDs,
     "name": str, "name_was_set_by_user": bool (the user's own title, given back on restore),
     "frame": [x, y, width, height], or None for a window without a frame of its own (tmux),
     "saved_sessions": [saved session, ...] in the order they were saved, which a restore keeps:
         tab order, except that a session saved again keeps its place and one saved later goes last}
A saved session:
    {"conversation_id": str, "agent_tool_name": "Claude Code" or "Codex", "name": str,
     "name_was_set_by_user": bool, "folder": where the agent ran, which a resume needs,
     "claude_config_folder": the Claude Code config folder it ran with, or None for the default,
     "terminal_session_id": its tab's first pane when saved, so a restore can type into that tab
         when the terminal brought it back (iTerm2 keeps its IDs across a reboot, seen 2026-11-06),
     "saved_at": when it was saved, in seconds since 1970}
A conversation is saved once: saving it again replaces its entry. A saved window is known by its
terminal, window ID and the time that window was opened (find_saved_window_key), a saved session
by its conversation ID.
"""

import json
import os

from agent_command_tab.hook_states_store import is_number
from agent_command_tab.settings_store import SETTINGS_FOLDER, write_json_file_atomically

SAVED_AGENT_SESSIONS_FILE_PATH = os.path.join(SETTINGS_FOLDER, "saved_agent_sessions.json")


def find_saved_window_key(saved_window):
    """Returns (terminal name, window ID when saved, when that window was opened), which names a saved window."""
    return saved_window["terminal_name"], saved_window["terminal_window_id"], saved_window["terminal_window_opened_at"]


def build_saved_session_from_stored_value(value):
    """Returns the saved session a stored value describes, or None when it doesn't have that shape."""
    if not isinstance(value, dict):
        return None
    if not all(isinstance(value.get(field_name), str) and value[field_name]
               for field_name in ("conversation_id", "agent_tool_name", "folder")):
        return None
    if not (isinstance(value.get("name"), str) and isinstance(value.get("name_was_set_by_user"), bool)
            and isinstance(value.get("terminal_session_id"), str) and is_number(value.get("saved_at"))
            and (value.get("claude_config_folder") is None or isinstance(value["claude_config_folder"], str))):
        return None
    return {field_name: value.get(field_name) for field_name in (
        "conversation_id", "agent_tool_name", "name", "name_was_set_by_user", "folder", "claude_config_folder",
        "terminal_session_id", "saved_at")}


def build_saved_window_from_stored_value(value):
    """Returns the saved window a stored value describes, with its saved sessions that have the
    right shape, or None when it has none or doesn't have that shape."""
    if not isinstance(value, dict) or not isinstance(value.get("saved_sessions"), list):
        return None
    frame = value.get("frame")
    opened_at = value.get("terminal_window_opened_at")
    if not (isinstance(value.get("terminal_name"), str) and isinstance(value.get("terminal_window_id"), str)
            and (opened_at is None or is_number(opened_at))
            and isinstance(value.get("name"), str) and isinstance(value.get("name_was_set_by_user"), bool)
            and (frame is None or (isinstance(frame, list) and len(frame) == 4 and all(map(is_number, frame))))):
        return None
    saved_sessions = [saved_session for saved_session in map(build_saved_session_from_stored_value, value["saved_sessions"])
                      if saved_session is not None]
    if not saved_sessions:
        return None
    return {"terminal_name": value["terminal_name"], "terminal_window_id": value["terminal_window_id"],
            "terminal_window_opened_at": opened_at, "name": value["name"],
            "name_was_set_by_user": value["name_was_set_by_user"], "frame": frame, "saved_sessions": saved_sessions}


def load_saved_windows(file_path):
    """Returns the saved windows from the file; [] when it is missing or unreadable, and without
    entries that don't have the right shape."""
    try:
        with open(file_path) as file:
            stored_windows = json.load(file)
    except (OSError, ValueError):
        return []
    if not isinstance(stored_windows, list):
        return []
    return [saved_window for saved_window in map(build_saved_window_from_stored_value, stored_windows)
            if saved_window is not None]


def save_saved_windows(file_path, saved_windows):
    write_json_file_atomically(saved_windows, file_path)


def build_saved_window(window_entry, saved_sessions, opened_at):
    """Returns the saved window for a list's WindowEntry, opened at opened_at (None in a terminal
    whose IDs are never reused), and the saved sessions of its tabs."""
    return {"terminal_name": window_entry.terminal_name, "terminal_window_id": window_entry.window_id,
            "terminal_window_opened_at": opened_at,
            "name": window_entry.title, "name_was_set_by_user": window_entry.window_title_set_by_user,
            "frame": list(window_entry.frame) if window_entry.can_move_to_another_monitor else None,
            "saved_sessions": saved_sessions}


def build_saved_session(agent_session_entry, folder, conversation_id, claude_config_folder, saved_at):
    """Returns the saved session for a list's AgentSessionEntry, whose agent ran in folder."""
    return {"conversation_id": conversation_id, "agent_tool_name": agent_session_entry.agent_tool_name,
            "name": agent_session_entry.tab_title, "name_was_set_by_user": agent_session_entry.tab_title_set_by_user,
            "folder": folder, "claude_config_folder": claude_config_folder,
            "terminal_session_id": agent_session_entry.session_id, "saved_at": saved_at}


def keep_a_name_the_user_gave(old_saved_session, new_saved_session):
    """Returns the new saved session of a conversation saved before as old_saved_session (or
    None), with the old one's name when the user gave it that name and not the new one."""
    if old_saved_session is not None and old_saved_session["name_was_set_by_user"] and not new_saved_session["name_was_set_by_user"]:
        return dict(new_saved_session, name=old_saved_session["name"], name_was_set_by_user=True)
    return new_saved_session


def merge_saved_windows(saved_windows, new_saved_windows):
    """Returns the saved windows with new ones added: a new saved session replaces the entry of
    its conversation, in place when that entry is in the same saved window, else at the end of
    the saved window it now belongs to, keeping a name the user gave the entry. A saved window
    saved from again takes the new frame, and the new name when the user gave it that name; one
    left with no sessions is dropped."""
    merged_windows = [dict(saved_window, saved_sessions=list(saved_window["saved_sessions"])) for saved_window in saved_windows]
    for new_saved_window in new_saved_windows:
        old_saved_session_by_conversation_id = {saved_session["conversation_id"]: saved_session
                                                for saved_window in merged_windows for saved_session in saved_window["saved_sessions"]}
        new_saved_window = dict(new_saved_window, saved_sessions=[
            keep_a_name_the_user_gave(old_saved_session_by_conversation_id.get(saved_session["conversation_id"]), saved_session)
            for saved_session in new_saved_window["saved_sessions"]])
        new_conversation_ids = {saved_session["conversation_id"] for saved_session in new_saved_window["saved_sessions"]}
        target_window = next((saved_window for saved_window in merged_windows
                              if find_saved_window_key(saved_window) == find_saved_window_key(new_saved_window)), None)
        for saved_window in merged_windows:
            if saved_window is not target_window:
                saved_window["saved_sessions"] = [saved_session for saved_session in saved_window["saved_sessions"]
                                                  if saved_session["conversation_id"] not in new_conversation_ids]
        if target_window is None:
            merged_windows.append(dict(new_saved_window, saved_sessions=list(new_saved_window["saved_sessions"])))
            continue
        target_window["frame"] = new_saved_window["frame"]
        if new_saved_window["name_was_set_by_user"]:
            target_window.update(name=new_saved_window["name"], name_was_set_by_user=True)
        position_by_conversation_id = {saved_session["conversation_id"]: position
                                       for position, saved_session in enumerate(target_window["saved_sessions"])}
        for new_saved_session in new_saved_window["saved_sessions"]:
            position = position_by_conversation_id.get(new_saved_session["conversation_id"])
            if position is None:
                target_window["saved_sessions"].append(new_saved_session)
            else:
                target_window["saved_sessions"][position] = new_saved_session
    return [saved_window for saved_window in merged_windows if saved_window["saved_sessions"]]


def rename_saved_window(saved_windows, window_key, new_name):
    """Returns the saved windows with one renamed; the name then counts as the user's own."""
    return [dict(saved_window, name=new_name, name_was_set_by_user=True) if find_saved_window_key(saved_window) == window_key
            else saved_window for saved_window in saved_windows]


def rename_saved_session(saved_windows, conversation_id, new_name):
    """Returns the saved windows with one saved session renamed; the name then counts as the user's own."""
    return [dict(saved_window, saved_sessions=[
        dict(saved_session, name=new_name, name_was_set_by_user=True) if saved_session["conversation_id"] == conversation_id
        else saved_session for saved_session in saved_window["saved_sessions"]]) for saved_window in saved_windows]


def delete_saved_items(saved_windows, window_keys, conversation_ids):
    """Returns the saved windows without the saved windows named by window_keys and the saved
    sessions named by conversation_ids; a saved window left with no sessions is dropped too."""
    remaining_windows = []
    for saved_window in saved_windows:
        if find_saved_window_key(saved_window) in window_keys:
            continue
        saved_sessions = [saved_session for saved_session in saved_window["saved_sessions"]
                          if saved_session["conversation_id"] not in conversation_ids]
        if saved_sessions:
            remaining_windows.append(dict(saved_window, saved_sessions=saved_sessions))
    return remaining_windows


def choose_saved_windows_to_restore(saved_windows, window_keys, conversation_ids):
    """Returns the saved windows with only the saved sessions a selection restores: every one of a
    selected saved window, and each selected saved session, in the order they are listed."""
    chosen_windows = []
    for saved_window in saved_windows:
        window_is_selected = find_saved_window_key(saved_window) in window_keys
        saved_sessions = [saved_session for saved_session in saved_window["saved_sessions"]
                          if window_is_selected or saved_session["conversation_id"] in conversation_ids]
        if saved_sessions:
            chosen_windows.append(dict(saved_window, saved_sessions=saved_sessions))
    return chosen_windows
