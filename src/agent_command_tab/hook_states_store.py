"""What each agent process's hooks reported (hook_event_tracking.apply_hook_event), kept in a file next to the settings.

So a turn end the hooks reported still counts after the dashboard restarts while iTerm2 says
working (find_when_the_hooks_ended_the_turn): a background task that runs after the turn keeps
iTerm2 at working and Claude Code's own report at "shell", so nothing else tells the turn is
over, and the row turned "stuck" (user report, 2026-10-28). Each state is stored with its
process's start time, so a later process that gets the same ID doesn't take it over. Pure
functions apart from the file reading and writing.
"""

import json
import os

from agent_command_tab.hook_event_tracking import apply_hook_event
from agent_command_tab.settings_store import SETTINGS_FOLDER, write_json_file_atomically

HOOK_STATES_FILE_PATH = os.path.join(SETTINGS_FOLDER, "agent_hook_states.json")


def is_stored_hook_state(value):
    """Whether value has a hook state's keys, each holding None, a number, a string or (the background task IDs) a list of strings."""
    if not isinstance(value, dict) or not set(apply_hook_event(None, {})) <= set(value):  # the keys a first event gives
        return False
    return all(item is None or (isinstance(item, (int, float, str)) and not isinstance(item, bool))
               or (key == "background_task_ids" and isinstance(item, list) and all(isinstance(task_id, str) for task_id in item))
               for key, item in value.items())


def load_hook_states(file_path):
    """Returns {agent process ID: (its start time, its hook state)} from the file; {} when it is
    missing or unreadable, and without records that don't have that shape."""
    try:
        with open(file_path) as file:
            stored_records = json.load(file)
    except (OSError, ValueError):
        return {}
    if not isinstance(stored_records, list):
        return {}
    started_at_and_hook_state_by_process_id = {}
    for value in stored_records:
        if not isinstance(value, dict) or not is_stored_hook_state(value.get("hook_state")):
            continue
        process_id, started_at = value.get("process_id"), value.get("started_at")
        if (not isinstance(process_id, int) or isinstance(process_id, bool) or not isinstance(started_at, (int, float))
                or isinstance(started_at, bool)):
            continue
        hook_state = dict(value["hook_state"])
        if hook_state["background_task_ids"] is not None:
            hook_state["background_task_ids"] = set(hook_state["background_task_ids"])
        started_at_and_hook_state_by_process_id[process_id] = (started_at, hook_state)
    return started_at_and_hook_state_by_process_id


def save_hook_states(file_path, started_at_and_hook_state_by_process_id):
    """Writes the states as a list, each with its process ID and start time."""
    write_json_file_atomically([
        {"process_id": process_id, "started_at": started_at,
         "hook_state": dict(hook_state, background_task_ids=sorted(hook_state["background_task_ids"])
                            if hook_state["background_task_ids"] is not None else None)}
        for process_id, (started_at, hook_state) in sorted(started_at_and_hook_state_by_process_id.items())], file_path)
