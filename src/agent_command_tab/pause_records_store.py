"""The records of paused agent sessions (agent_session_pausing.PauseRecord), kept in a file next to the settings.

So the processes a pause stopped are still continued, and Resume still works, after the
dashboard restarts while sessions are paused (a reconnect when iTerm2's connection drops, a
quit, an upgrade). Pure functions apart from the file reading and writing.
"""

import json
import os

from agent_command_tab.agent_session_pausing import PauseRecord
from agent_command_tab.settings_store import SETTINGS_FOLDER, write_json_file_atomically

PAUSE_RECORDS_FILE_PATH = os.path.join(SETTINGS_FOLDER, "paused_agent_sessions.json")


def is_process_record(value):
    """Whether value is a stored (process ID, start time) pair."""
    return (isinstance(value, list) and len(value) == 2 and isinstance(value[0], int) and not isinstance(value[0], bool)
            and isinstance(value[1], (int, float)) and not isinstance(value[1], bool))


def build_pause_record_from_stored_value(value):
    """Returns the PauseRecord a stored value describes, or None when it doesn't have that shape.
    A record saved before 2026-11-06, of a pause that pressed Ctrl+Z, has no shell_processes (it
    has the shell's prompt instead, no longer used) and gets none: its shell has the terminal."""
    if (not isinstance(value, dict) or not is_process_record(value.get("agent_process"))
            or not isinstance(value.get("agent_tool_name"), str)):
        return None
    shell_processes = value.get("shell_processes", [])
    stopped_processes = value.get("stopped_processes")
    if not all(isinstance(processes, list) and all(map(is_process_record, processes))
               for processes in (shell_processes, stopped_processes)):
        return None
    return PauseRecord(tuple(value["agent_process"]), value["agent_tool_name"],
                       [tuple(process) for process in shell_processes], [tuple(process) for process in stopped_processes])


def load_pause_records(file_path):
    """Returns {(session ID, agent process ID): PauseRecord} from the file; {} when it is missing
    or unreadable, and without records that don't have that shape."""
    try:
        with open(file_path) as file:
            stored_records = json.load(file)
    except (OSError, ValueError):
        return {}
    if not isinstance(stored_records, list):
        return {}
    pause_record_by_tab_and_agent = {}
    for value in stored_records:
        pause_record = build_pause_record_from_stored_value(value)
        if pause_record is not None and isinstance(value.get("session_id"), str):
            pause_record_by_tab_and_agent[(value["session_id"], pause_record.agent_process[0])] = pause_record
    return pause_record_by_tab_and_agent


def save_pause_records(file_path, pause_record_by_tab_and_agent):
    """Writes the records as a list, each with its tab's session ID."""
    write_json_file_atomically([dict(pause_record._asdict(), session_id=session_id)
                                for (session_id, _), pause_record in pause_record_by_tab_and_agent.items()], file_path)
