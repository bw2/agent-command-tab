"""The saved agent sessions' file and the pure functions that change the list (user request, 2026-11-06)."""

import json
import os
import tempfile
import unittest

from agent_command_tab.saved_agent_sessions_store import (build_saved_session, build_saved_window,
                                                          choose_saved_windows_to_restore, delete_saved_items,
                                                          load_saved_windows, merge_saved_windows, rename_saved_session,
                                                          rename_saved_window, save_saved_windows)
from agent_command_tab.snapshot_data_classes import AgentSessionEntry, WindowEntry


def make_saved_session(conversation_id, name="", saved_at=1.0):
    return {"conversation_id": conversation_id, "agent_tool_name": "Claude Code", "name": name or conversation_id,
            "name_was_set_by_user": False, "folder": "/p", "claude_config_folder": None,
            "terminal_session_id": f"session-{conversation_id}", "saved_at": saved_at}


def make_saved_window(window_id, conversation_ids, name=None, name_was_set_by_user=False, frame=(0, 0, 800, 600)):
    return {"terminal_name": "iTerm2", "terminal_window_id": window_id, "terminal_window_opened_at": None, "name": name or window_id,
            "name_was_set_by_user": name_was_set_by_user, "frame": list(frame),
            "saved_sessions": [make_saved_session(conversation_id) for conversation_id in conversation_ids]}


def list_conversation_ids_by_window(saved_windows):
    return [(saved_window["terminal_window_id"], [saved_session["conversation_id"] for saved_session in saved_window["saved_sessions"]])
            for saved_window in saved_windows]


class SavedWindowsFileTests(unittest.TestCase):
    def test_saved_windows_come_back_from_the_file(self):
        with tempfile.TemporaryDirectory() as folder:
            file_path = os.path.join(folder, "saved_agent_sessions.json")
            self.assertEqual(load_saved_windows(file_path), [])  # no file yet
            saved_windows = [make_saved_window("w1", ["a", "b"]), make_saved_window("w2", ["c"])]
            save_saved_windows(file_path, saved_windows)
            self.assertEqual(load_saved_windows(file_path), saved_windows)

    def test_entries_without_the_right_shape_are_left_out(self):
        good_window = make_saved_window("w1", ["a"])
        tmux_window = dict(make_saved_window("$1", ["t"]), terminal_name="tmux", frame=None)
        window_with_one_bad_session = make_saved_window("w2", ["b", "c"])
        window_with_one_bad_session["saved_sessions"][1]["saved_at"] = "yesterday"
        window_with_only_bad_sessions = make_saved_window("w3", ["d"])
        window_with_only_bad_sessions["saved_sessions"][0]["conversation_id"] = ""
        with tempfile.TemporaryDirectory() as folder:
            file_path = os.path.join(folder, "saved_agent_sessions.json")
            with open(file_path, "w") as file:
                json.dump([good_window, tmux_window, window_with_one_bad_session, window_with_only_bad_sessions,
                           dict(good_window, frame=[1, 2]), "not a window", {"saved_sessions": []}], file)
            self.assertEqual(list_conversation_ids_by_window(load_saved_windows(file_path)),
                             [("w1", ["a"]), ("$1", ["t"]), ("w2", ["b"])])
            with open(file_path, "w") as file:
                json.dump([dict(good_window, terminal_window_opened_at="today")], file)
            self.assertEqual(load_saved_windows(file_path), [])
            with open(file_path, "w") as file:
                file.write("{not json")
            self.assertEqual(load_saved_windows(file_path), [])


class SavedWindowRecordTests(unittest.TestCase):
    def test_records_are_built_from_the_lists_rows(self):
        window_entry = WindowEntry("w1", "my-project", True, (10, 20, 800, 600), 3, ())
        agent_session_entry = AgentSessionEntry(tab_id="t1", session_id="s1", tab_title="planning", tab_title_set_by_user=False,
                                                agent_session_name="", agent_tool_name="Codex", working_folder="/x", status="idle")
        saved_session = build_saved_session(agent_session_entry, "/p", "conv-1", None, 5.0)
        self.assertEqual(build_saved_window(window_entry, [saved_session], None), {
            "terminal_name": "iTerm2", "terminal_window_id": "w1", "terminal_window_opened_at": None, "name": "my-project",
            "name_was_set_by_user": True,
            "frame": [10, 20, 800, 600], "saved_sessions": [{
                "conversation_id": "conv-1", "agent_tool_name": "Codex", "name": "planning", "name_was_set_by_user": False,
                "folder": "/p", "claude_config_folder": None, "terminal_session_id": "s1", "saved_at": 5.0}]})
        tmux_window_entry = WindowEntry("$1", "tmux: work", False, (0, 0, 0, 0), 1, (), terminal_name="tmux",
                                        can_move_to_another_monitor=False)
        self.assertIsNone(build_saved_window(tmux_window_entry, [saved_session], 1793977852)["frame"])  # it has no frame of its own


class MergeSavedWindowsTests(unittest.TestCase):
    def test_a_conversation_saved_again_replaces_its_entry(self):
        saved_windows = [make_saved_window("w1", ["a", "b"]), make_saved_window("w2", ["c"])]
        newer_b = dict(make_saved_window("w1", ["b"]), saved_sessions=[make_saved_session("b", "renamed b", saved_at=9.0)])
        merged_windows = merge_saved_windows(saved_windows, [newer_b])
        self.assertEqual(list_conversation_ids_by_window(merged_windows), [("w1", ["a", "b"]), ("w2", ["c"])])  # in place
        self.assertEqual(merged_windows[0]["saved_sessions"][1]["name"], "renamed b")
        self.assertEqual(saved_windows[0]["saved_sessions"][1]["name"], "b")  # the list given is left as it was

    def test_a_conversation_saved_from_another_window_moves_there_and_an_emptied_window_goes(self):
        saved_windows = [make_saved_window("w1", ["a"]), make_saved_window("w2", ["c"])]
        merged_windows = merge_saved_windows(saved_windows, [make_saved_window("w2", ["a", "d"])])
        self.assertEqual(list_conversation_ids_by_window(merged_windows), [("w2", ["c", "a", "d"])])
        merged_windows = merge_saved_windows(saved_windows, [make_saved_window("w3", ["e"])])
        self.assertEqual(list_conversation_ids_by_window(merged_windows), [("w1", ["a"]), ("w2", ["c"]), ("w3", ["e"])])

    def test_a_window_saved_from_again_takes_the_new_frame_and_only_a_name_the_user_gave_it(self):
        saved_windows = [make_saved_window("w1", ["a"], name="agent-command-tab")]
        merged_window, = merge_saved_windows(saved_windows, [make_saved_window("w1", ["b"], name="other-folder", frame=(5, 5, 900, 700))])
        self.assertEqual((merged_window["name"], merged_window["frame"]), ("agent-command-tab", [5, 5, 900, 700]))
        merged_window, = merge_saved_windows(saved_windows, [make_saved_window("w1", ["b"], name="Reviews", name_was_set_by_user=True)])
        self.assertEqual((merged_window["name"], merged_window["name_was_set_by_user"]), ("Reviews", True))

    def test_a_conversation_saved_again_keeps_the_name_the_user_gave_it_in_the_saved_sessions_window(self):
        saved_windows = rename_saved_session([make_saved_window("w1", ["a"]), make_saved_window("w2", ["b"])], "a", "Plans")
        for new_saved_window in (make_saved_window("w1", ["a"]), make_saved_window("w2", ["a"])):  # in place, or moved
            merged_windows = merge_saved_windows(saved_windows, [new_saved_window])
            self.assertEqual([(saved_session["name"], saved_session["name_was_set_by_user"]) for saved_window in merged_windows
                              for saved_session in saved_window["saved_sessions"] if saved_session["conversation_id"] == "a"],
                             [("Plans", True)])
        merged_windows = merge_saved_windows(saved_windows, [dict(make_saved_window("w1", []), saved_sessions=[
            dict(make_saved_session("a", "Reviews"), name_was_set_by_user=True)])])  # its tab renamed since
        self.assertEqual(merged_windows[0]["saved_sessions"][0]["name"], "Reviews")  # the user's newer name

    def test_a_tmux_session_given_the_id_of_one_saved_before_a_restart_gets_a_saved_window_of_its_own(self):
        # A new tmux server numbers its sessions from $0 again; the time a session was created tells them apart.
        def make_tmux_saved_window(conversation_ids, opened_at, name):
            return dict(make_saved_window("$0", conversation_ids, name=name, name_was_set_by_user=True), terminal_name="tmux",
                        terminal_window_opened_at=opened_at, frame=None)
        saved_windows = [make_tmux_saved_window(["a"], 1000, "work")]
        merged_windows = merge_saved_windows(saved_windows, [make_tmux_saved_window(["b"], 1000, "work")])
        self.assertEqual(list_conversation_ids_by_window(merged_windows), [("$0", ["a", "b"])])  # the same session
        merged_windows = merge_saved_windows(saved_windows, [make_tmux_saved_window(["c"], 2000, "notes")])
        self.assertEqual([(saved_window["name"], [saved_session["conversation_id"] for saved_session in saved_window["saved_sessions"]])
                          for saved_window in merged_windows], [("work", ["a"]), ("notes", ["c"])])


class RenameDeleteAndChooseTests(unittest.TestCase):
    def setUp(self):
        self.saved_windows = [make_saved_window("w1", ["a", "b"]), make_saved_window("w2", ["c"])]

    def test_a_rename_counts_as_the_users_own_name(self):
        renamed_windows = rename_saved_window(self.saved_windows, ("iTerm2", "w2", None), "Reviews")
        self.assertEqual((renamed_windows[1]["name"], renamed_windows[1]["name_was_set_by_user"]), ("Reviews", True))
        self.assertEqual(renamed_windows[0], self.saved_windows[0])
        renamed_windows = rename_saved_session(self.saved_windows, "b", "planning")
        self.assertEqual([(saved_session["name"], saved_session["name_was_set_by_user"])
                          for saved_session in renamed_windows[0]["saved_sessions"]], [("a", False), ("planning", True)])

    def test_deleting_windows_and_sessions(self):
        self.assertEqual(list_conversation_ids_by_window(delete_saved_items(self.saved_windows, {("iTerm2", "w1", None)}, set())),
                         [("w2", ["c"])])
        self.assertEqual(list_conversation_ids_by_window(delete_saved_items(self.saved_windows, set(), {"b", "c"})),
                         [("w1", ["a"])])  # w2 has no sessions left
        self.assertEqual(delete_saved_items(self.saved_windows, {("tmux", "w1", None)}, set()), self.saved_windows)  # another terminal's

    def test_a_selected_window_restores_all_of_its_sessions_and_a_selected_session_only_itself(self):
        self.assertEqual(list_conversation_ids_by_window(choose_saved_windows_to_restore(self.saved_windows, {("iTerm2", "w2", None)},
                                                                                         {"b"})),
                         [("w1", ["b"]), ("w2", ["c"])])
        self.assertEqual(choose_saved_windows_to_restore(self.saved_windows, set(), set()), [])


if __name__ == "__main__":
    unittest.main()
