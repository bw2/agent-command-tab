"""The frames iTerm2 windows had before a docked sidebar narrowed them (PLAN.md section 8, "Docked").

They are kept in a file next to the settings, so windows can still get their room back after
the dashboard restarts (a reconnect when iTerm2 quits, a crash, Ctrl+C). Pure functions apart
from the file reading and writing.
"""

import json
import os

from agent_command_tab.monitor_assignment import frames_are_within_tolerance
from agent_command_tab.settings_store import SETTINGS_FOLDER, write_json_file_atomically

DOCKED_WINDOW_FRAMES_FILE_PATH = os.path.join(SETTINGS_FOLDER, "docked_window_frames.json")


def frame_is_valid(frame):
    return (isinstance(frame, (list, tuple)) and len(frame) == 4
            and all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in frame))


def load_docked_window_frames(file_path):
    """Returns {window ID: (frame before docking, frame that was set)} from the file; {} when it
    is missing or unreadable, and without records that don't have that shape."""
    try:
        with open(file_path) as file:
            stored_records = json.load(file)
    except (OSError, ValueError):
        return {}
    if not isinstance(stored_records, dict):
        return {}
    return {window_id: (tuple(frames[0]), tuple(frames[1])) for window_id, frames in stored_records.items()
            if isinstance(frames, list) and len(frames) == 2 and all(map(frame_is_valid, frames))}


def save_docked_window_frames(file_path, frames_by_window_id):
    write_json_file_atomically({window_id: [list(frames[0]), list(frames[1])]
                                for window_id, frames in frames_by_window_id.items()}, file_path)


def adopt_records_of_restored_windows(frames_by_window_id, current_frames_by_window_id, tolerance_points):
    """Returns the records with each record whose window is gone moved to a live window that has
    no record and still has the frame that was set: iTerm2 gives the windows it restores after
    a restart new IDs.

    Args:
        frames_by_window_id: {window ID: (frame before docking, frame that was set)}.
        current_frames_by_window_id: {window ID: current frame} of the live windows.
        tolerance_points: How far a frame may differ and still count as the same.
    """
    records = dict(frames_by_window_id)
    windows_without_records = [window_id for window_id in current_frames_by_window_id if window_id not in records]
    for window_id in [window_id for window_id in records if window_id not in current_frames_by_window_id]:
        frame_that_was_set = records[window_id][1]
        matching_window_id = next((candidate for candidate in windows_without_records if frames_are_within_tolerance(
            current_frames_by_window_id[candidate], frame_that_was_set, tolerance_points)), None)
        if matching_window_id is not None:
            records[matching_window_id] = records.pop(window_id)
            windows_without_records.remove(matching_window_id)
    return records
