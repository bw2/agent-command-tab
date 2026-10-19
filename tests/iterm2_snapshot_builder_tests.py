import json
import unittest

from llm_sidebar_dashboard.agent_adapters import build_built_in_agent_tool_adapters
from llm_sidebar_dashboard.iterm2_snapshot_builder import (build_computer_snapshot, build_folder_display_name,
                                                           compute_displayed_state_since, resolve_agent_sessions)
from llm_sidebar_dashboard.snapshot_data_classes import convert_snapshot_to_json_ready_dict, iterate_agent_session_entries

MONITOR = {"monitor_id": "display-1", "name": "Built-in", "frame": (0, 0, 2000, 1300), "visible_frame": (0, 0, 2000, 1260)}


def make_tab(tab_id, command_line, status=None, path="/Users/someone/code/project", title="claude",
             title_override_format=None, job_pid=None):
    return {"tab_id": tab_id, "title": title, "title_override_format": title_override_format,
            "first_session": {"session_id": f"session-{tab_id}", "command_line": command_line, "job_pid": job_pid,
                              "status": status, "path": path}}


def make_window(window_id, tabs, title="claude", title_override_format=None, frame=(10, 10, 800, 600)):
    return {"window_id": window_id, "frame": frame, "title": title, "title_override_format": title_override_format,
            "tabs": tabs}


def resolve(gathered_windows, previous_agent_sessions=None, alive_process_ids=()):
    specific_adapters, generic_adapter = build_built_in_agent_tool_adapters()
    return resolve_agent_sessions(gathered_windows, previous_agent_sessions or {}, specific_adapters, generic_adapter,
                                  lambda process_id: process_id in alive_process_ids)


def build_snapshot(gathered_windows, window_order=(), claude_agents_by_process_id=None, tracking=None,
                   seen_session_id=None, monitors=(MONITOR,), agent_sessions=None, displayed_states=None,
                   screen_readings=None):
    return build_computer_snapshot(gathered_windows, agent_sessions if agent_sessions is not None else resolve(gathered_windows),
                                   list(monitors), list(window_order), tracking or {}, displayed_states or {},
                                   screen_readings or {}, 600, claude_agents_by_process_id or {}, seen_session_id,
                                   seen_session_id, "computer-1", "Test Mac", 1000.0)


class BuildComputerSnapshotTests(unittest.TestCase):
    def test_windows_without_agents_and_shell_tabs_are_left_out(self):
        snapshot = build_snapshot([
            make_window("w-shells", [make_tab("1", "-bash")]),
            make_window("w-mixed", [make_tab("2", "-zsh"), make_tab("3", "claude --model haiku", status="working")]),
        ])
        windows = snapshot.monitors[0].windows
        self.assertEqual([window.window_id for window in windows], ["w-mixed"])
        self.assertEqual(windows[0].total_tab_count, 2)
        self.assertEqual([entry.tab_id for entry in windows[0].agent_sessions], ["3"])
        self.assertEqual(windows[0].agent_sessions[0].status, "working")

    def test_default_names_come_from_agent_name_then_folder(self):
        snapshot = build_snapshot(
            [make_window("w", [make_tab("1", "claude", job_pid=42), make_tab("2", "codex", path="/tmp/other-project")])],
            claude_agents_by_process_id={42: {"name": "profile-photo-3a", "cwd": "/Users/someone/code/photos"}})
        window = snapshot.monitors[0].windows[0]
        self.assertEqual(window.title, "photos")
        self.assertFalse(window.window_title_set_by_user)
        self.assertEqual([entry.tab_title for entry in window.agent_sessions], ["profile-photo-3a", "other-project"])
        self.assertEqual(window.agent_sessions[0].working_folder, "/Users/someone/code/photos")

    def test_agent_process_folder_wins_over_iterm2_path(self):
        tab = make_tab("1", "codex", path="/Users/someone/stale-folder")
        tab["first_session"]["job_folder"] = "/Users/someone/code/real-project"
        snapshot = build_snapshot([make_window("w", [tab])])
        entry = snapshot.monitors[0].windows[0].agent_sessions[0]
        self.assertEqual((entry.working_folder, entry.tab_title), ("/Users/someone/code/real-project", "real-project"))

    def test_agent_process_start_time_becomes_the_created_time(self):
        tab = make_tab("1", "claude", job_pid=42)
        tab["first_session"]["job_started_at"] = 1760850000.0
        snapshot = build_snapshot([make_window("w", [tab, make_tab("2", "codex")])])
        self.assertEqual([entry.started_at for entry in snapshot.monitors[0].windows[0].agent_sessions], [1760850000.0, None])

    def test_titles_set_by_the_user_win(self):
        snapshot = build_snapshot([make_window(
            "w", [make_tab("1", "claude", title="Review bot", title_override_format="Review bot")],
            title="My project", title_override_format="My project")])
        window = snapshot.monitors[0].windows[0]
        self.assertEqual(window.title, "My project")
        self.assertTrue(window.window_title_set_by_user)
        self.assertEqual(window.agent_sessions[0].tab_title, "Review bot")
        self.assertTrue(window.agent_sessions[0].tab_title_set_by_user)

    def test_window_order_is_the_list_order_and_unknown_windows_follow(self):
        snapshot = build_snapshot([make_window(window_id, [make_tab(window_id + "-tab", "claude")]) for window_id in "abc"],
                                  window_order=["c", "a"])
        self.assertEqual([window.window_id for window in snapshot.monitors[0].windows], ["c", "a", "b"])

    def test_done_becomes_idle_once_seen(self):
        tracking = {"session-1": {"status_change_time": 900.0, "last_time_tab_was_seen": 950.0}}
        snapshot = build_snapshot([make_window("w", [make_tab("1", "claude", status="done")])], tracking=tracking)
        self.assertEqual(snapshot.monitors[0].windows[0].agent_sessions[0].status, "idle")

    def test_screen_readings_reach_the_status(self):
        tracking = {"session-1": {"status_change_time": 100.0, "last_time_tab_was_seen": None}}
        snapshot = build_snapshot([make_window("w", [make_tab("1", "claude", status="working")])], tracking=tracking,
                                  screen_readings={"session-1": {"fingerprint_changed_at": 200.0}})
        entry = snapshot.monitors[0].windows[0].agent_sessions[0]
        self.assertEqual((entry.status, entry.status_reason), ("stuck", "thinking with no screen change for 13 min"))

    def test_snapshot_converts_to_json(self):
        snapshot = build_snapshot([make_window("w", [make_tab("1", "claude", status="waiting")])])
        json_ready = convert_snapshot_to_json_ready_dict(snapshot)
        self.assertEqual(json_ready["monitors"][0]["windows"][0]["agent_sessions"][0]["status"], "asking")
        self.assertEqual(json_ready["monitors"][0]["frame"], [0, 0, 2000, 1300])
        json.dumps(json_ready)
        self.assertEqual(len(list(iterate_agent_session_entries(snapshot))), 1)

    def test_monitor_groups_go_left_to_right(self):
        monitor_left_of_main = {"monitor_id": "left", "name": "Left", "frame": (-1920, 0, 1920, 1080),
                                "visible_frame": (-1920, 0, 1920, 1050)}
        snapshot = build_snapshot([make_window("w", [make_tab("1", "claude")], frame=(-1500, 100, 800, 600))],
                                  monitors=(MONITOR, monitor_left_of_main))
        self.assertEqual([monitor.monitor_id for monitor in snapshot.monitors], ["left", "display-1"])
        self.assertEqual(len(snapshot.monitors[0].windows), 1)

    def test_no_monitor_message_yet_uses_a_placeholder_monitor(self):
        snapshot = build_snapshot([make_window("w", [make_tab("1", "claude")])], monitors=())
        self.assertEqual(snapshot.monitors[0].monitor_id, "unknown")
        self.assertEqual(len(snapshot.monitors[0].windows), 1)

    def test_suspended_agent_keeps_its_row_and_state_while_its_process_lives(self):
        running = [make_window("w", [make_tab("1", "claude --model haiku", status="waiting", job_pid=42)])]
        agent_sessions = resolve(running)
        displayed_states = {"session-1": ("asking", 900.0)}
        # Ctrl+Z: the shell is the foreground job again, and iTerm2 cleared the status.
        suspended = [make_window("w", [make_tab("1", "-zsh", status=None, job_pid=77)])]
        agent_sessions = resolve(suspended, agent_sessions, alive_process_ids={42})
        self.assertTrue(agent_sessions["session-1"]["is_in_background"])
        snapshot = build_snapshot(suspended, agent_sessions=agent_sessions, displayed_states=displayed_states,
                                  claude_agents_by_process_id={42: {"name": "reviews-1a", "cwd": "/x/reviews"}})
        entry = snapshot.monitors[0].windows[0].agent_sessions[0]
        self.assertEqual((entry.status, entry.status_since, entry.tab_title), ("asking", 900.0, "reviews-1a"))
        self.assertEqual(entry.agent_tool_name, "Claude Code")
        self.assertEqual(resolve(suspended, agent_sessions, alive_process_ids=()), {})  # the agent process ended


class DisplayedStateSinceTests(unittest.TestCase):
    def test_first_sight_uses_the_status_change_time(self):
        self.assertEqual(compute_displayed_state_since("working", 900.0, None, 1000.0), 900.0)

    def test_unchanged_state_keeps_its_time(self):
        self.assertEqual(compute_displayed_state_since("idle", 990.0, ("idle", 950.0), 1000.0), 950.0)

    def test_new_status_report_uses_its_time(self):
        self.assertEqual(compute_displayed_state_since("working", 980.0, ("idle", 950.0), 1000.0), 980.0)

    def test_done_becoming_idle_when_seen_starts_now(self):
        self.assertEqual(compute_displayed_state_since("idle", 900.0, ("done", 900.0), 950.0), 950.0)


class FolderDisplayNameTests(unittest.TestCase):
    def test_folder_names(self):
        self.assertEqual(build_folder_display_name("/Users/someone/code/project/"), "project")
        self.assertEqual(build_folder_display_name(""), "")
        self.assertEqual(build_folder_display_name("/"), "/")


if __name__ == "__main__":
    unittest.main()
