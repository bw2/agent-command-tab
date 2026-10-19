import json
import os
import tempfile
import unittest

from agent_command_tab.docked_window_frames_store import (adopt_records_of_restored_windows,
                                                          load_docked_window_frames, save_docked_window_frames)


class DockedWindowFramesFileTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.file_path = os.path.join(self.folder.name, "nested", "docked_window_frames.json")

    def tearDown(self):
        self.folder.cleanup()

    def test_saved_records_load_back(self):
        records = {"w1": ((100, 50, 1800, 1000), (100, 50, 1500, 1000))}
        save_docked_window_frames(self.file_path, records)
        self.assertEqual(load_docked_window_frames(self.file_path), records)

    def test_missing_unreadable_or_malformed_file_gives_no_records(self):
        self.assertEqual(load_docked_window_frames(self.file_path), {})
        os.makedirs(os.path.dirname(self.file_path))
        with open(self.file_path, "w") as file:
            file.write("{not json")
        self.assertEqual(load_docked_window_frames(self.file_path), {})
        with open(self.file_path, "w") as file:
            json.dump({"w1": [[1, 2, 3, 4], [1, 2, 3, 4]], "w2": [[1, 2, 3], [1, 2, 3, 4]], "w3": "x"}, file)
        self.assertEqual(list(load_docked_window_frames(self.file_path)), ["w1"])


class AdoptRecordsOfRestoredWindowsTests(unittest.TestCase):
    def test_a_restored_window_with_the_narrowed_frame_takes_over_the_record(self):
        records = {"old-id": ((100, 50, 1800, 1000), (100, 50, 1500, 1000))}
        current_frames = {"new-id": (100, 52, 1498, 1000), "other": (0, 0, 500, 500)}
        self.assertEqual(adopt_records_of_restored_windows(records, current_frames, 20),
                         {"new-id": ((100, 50, 1800, 1000), (100, 50, 1500, 1000))})

    def test_records_of_live_windows_and_unmatched_records_stay(self):
        records = {"w1": ((0, 0, 900, 900), (0, 0, 800, 900)), "gone": ((1, 1, 9, 9), (1, 1, 8, 9))}
        self.assertEqual(adopt_records_of_restored_windows(records, {"w1": (0, 0, 800, 900)}, 20), records)


if __name__ == "__main__":
    unittest.main()
