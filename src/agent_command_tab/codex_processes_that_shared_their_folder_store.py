"""The Codex agent processes that shared their folder with another Codex session while they ran
(DashboardCore.remember_codex_sessions_that_share_a_folder), kept in a file next to the settings.

So such a process's conversation still can't be told from the other session's after the dashboard
restarts once the other session's tab has closed: that session's conversation file stays, and
fits this process by folder and start time, so a Duplicate would fork it. Each process is stored
with its start time, so a later process that gets the same ID isn't taken for it. Pure functions
apart from the file reading and writing.
"""

import os

from agent_command_tab.settings_store import SETTINGS_FOLDER, is_number, read_json_file, write_json_file_atomically

CODEX_PROCESSES_THAT_SHARED_THEIR_FOLDER_FILE_PATH = os.path.join(SETTINGS_FOLDER, "codex_processes_that_shared_their_folder.json")


def load_codex_processes_that_shared_their_folder(file_path):
    """Returns {process ID: its start time} from the file; {} when it is missing or unreadable,
    and without records that don't have that shape."""
    stored_records = read_json_file(file_path, list)
    if stored_records is None:
        return {}
    return {value["process_id"]: value["started_at"] for value in stored_records
            if isinstance(value, dict) and isinstance(value.get("process_id"), int) and not isinstance(value["process_id"], bool)
            and is_number(value.get("started_at"))}


def save_codex_processes_that_shared_their_folder(file_path, started_at_by_process_id):
    """Writes the processes as a list, each with its process ID and start time."""
    write_json_file_atomically([{"process_id": process_id, "started_at": started_at}
                                for process_id, started_at in sorted(started_at_by_process_id.items())], file_path)
