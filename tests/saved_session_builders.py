"""Builds saved sessions and saved windows in saved_agent_sessions_store's format, for the tests
of the store and of the core, which give them their own defaults."""


def build_saved_session_record(conversation_id, folder, terminal_session_id, name, name_was_set_by_user=False,
                               agent_tool_name="Claude Code", claude_config_folder=None, saved_at=1.0, codex_home_folder=None):
    return {"conversation_id": conversation_id, "agent_tool_name": agent_tool_name, "name": name,
            "name_was_set_by_user": name_was_set_by_user, "folder": folder, "claude_config_folder": claude_config_folder,
            "codex_home_folder": codex_home_folder, "terminal_session_id": terminal_session_id, "saved_at": saved_at}


def build_saved_window_record(saved_sessions, terminal_window_id, name, frame, name_was_set_by_user=False):
    """Returns a saved iTerm2 window; frame None for one without a frame of its own."""
    return {"terminal_name": "iTerm2", "terminal_window_id": terminal_window_id, "terminal_window_opened_at": None, "name": name,
            "name_was_set_by_user": name_was_set_by_user, "frame": list(frame) if frame else None,
            "saved_sessions": saved_sessions}
