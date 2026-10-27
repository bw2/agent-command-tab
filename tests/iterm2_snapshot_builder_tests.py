import json
import unittest

from agent_command_tab.agent_adapters import build_built_in_agent_tool_adapters
from agent_command_tab.iterm2_snapshot_builder import (DisplayedState, build_computer_snapshot,
                                                       build_folder_display_name, compute_context_fill_percent,
                                                       compute_displayed_state_since, resolve_agent_sessions)
from agent_command_tab.snapshot_data_classes import (ChildProcessEntry, convert_snapshot_to_json_ready_dict,
                                                     iterate_agent_session_entries)

MONITOR = {"monitor_id": "display-1", "name": "Built-in", "frame": (0, 0, 2000, 1300), "visible_frame": (0, 0, 2000, 1260)}


def make_tab(tab_id, command_line, status=None, path="/Users/someone/code/project", title="claude",
             title_override_format=None, job_pid=None):
    return {"tab_id": tab_id, "title": title, "title_override_format": title_override_format,
            "first_session": {"session_id": f"session-{tab_id}", "command_line": command_line, "job_pid": job_pid,
                              "status": status, "path": path}}


def make_window(window_id, tabs, title="claude", title_override_format=None, frame=(10, 10, 800, 600)):
    return {"window_id": window_id, "frame": frame, "title": title, "title_override_format": title_override_format,
            "tabs": tabs}


def resolve(gathered_windows, previous_agent_sessions=None, alive_process_ids=(), stopped_process_ids=()):
    specific_adapters, generic_adapter = build_built_in_agent_tool_adapters()
    return resolve_agent_sessions(gathered_windows, previous_agent_sessions or {}, specific_adapters, generic_adapter,
                                  lambda process_id: process_id in alive_process_ids,
                                  lambda process_id: process_id in stopped_process_ids)


def build_snapshot(gathered_windows, window_order=(), claude_agents_by_process_id=None, tracking=None,
                   seen_session_id=None, monitors=(MONITOR,), agent_sessions=None, displayed_states=None,
                   screen_readings=None, now=1000.0, exited_agent_sessions_by_session_id=None, hooks_status_per_agent_tool=()):
    return build_computer_snapshot(gathered_windows, agent_sessions if agent_sessions is not None else resolve(gathered_windows),
                                   list(monitors), list(window_order), tracking or {}, displayed_states or {},
                                   screen_readings or {}, 600, claude_agents_by_process_id or {}, seen_session_id,
                                   seen_session_id, "computer-1", "Test Mac", now, exited_agent_sessions_by_session_id,
                                   hooks_status_per_agent_tool)


class ContextFillPercentTests(unittest.TestCase):
    def test_the_share_of_the_window_in_use_once_both_numbers_are_known(self):
        self.assertEqual(compute_context_fill_percent(84_000, 200_000), 42)
        self.assertEqual(compute_context_fill_percent(84_000, 1_000_000), 8)
        self.assertIsNone(compute_context_fill_percent(84_000, None))
        self.assertIsNone(compute_context_fill_percent(None, 200_000))


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
        tracking = {"session-1": {"status_change_time": 900.0, "last_turn_ended_at": 900.0, "last_time_tab_was_seen": 950.0}}
        snapshot = build_snapshot([make_window("w", [make_tab("1", "claude", status="done")])], tracking=tracking)
        self.assertEqual(snapshot.monitors[0].windows[0].agent_sessions[0].status, "idle")

    def test_a_cleared_finished_turn_is_idle_without_counting_as_seen(self):
        tracking = {"session-1": {"status_change_time": 900.0, "last_turn_ended_at": 900.0, "last_time_tab_was_seen": None,
                                  "finished_turn_cleared_at": 950.0}}
        entry = build_snapshot([make_window("w", [make_tab("1", "claude", status="done")])],
                               tracking=tracking).monitors[0].windows[0].agent_sessions[0]
        self.assertEqual((entry.status, entry.last_time_tab_was_seen), ("idle", None))

    def test_an_ended_turn_stays_done_until_seen_after_the_status_says_idle(self):
        tab = make_tab("1", "codex", status="idle")
        tracking = {"session-1": {"status_change_time": 900.0, "last_turn_ended_at": 900.0, "last_time_tab_was_seen": 850.0}}
        self.assertEqual(build_snapshot([make_window("w", [tab])], tracking=tracking).monitors[0].windows[0]
                         .agent_sessions[0].status, "done")
        tracking["session-1"]["last_time_tab_was_seen"] = 950.0
        self.assertEqual(build_snapshot([make_window("w", [tab])], tracking=tracking).monitors[0].windows[0]
                         .agent_sessions[0].status, "idle")

    def test_claude_codes_own_report_fills_in_while_iterm2_reports_no_status(self):
        # After iTerm2 restarts, its API returns no status for a tab until the session's next hook event.
        def build_entry(iterm2_status, claude_agents_status, displayed_states=None):
            return build_snapshot([make_window("w", [make_tab("1", "claude", status=iterm2_status, job_pid=42)])],
                                  claude_agents_by_process_id={42: {"name": "n", "status": claude_agents_status}},
                                  displayed_states=displayed_states).monitors[0].windows[0].agent_sessions[0]
        for claude_agents_status, state in (("idle", "idle"), ("busy", "working"), ("waiting", "asking"),
                                            (None, "unknown"), ("some new word", "unknown")):
            self.assertEqual(build_entry(None, claude_agents_status).status, state, claude_agents_status)
        self.assertEqual(build_entry(None, "idle").status_reason, "from claude agents --json: iTerm2 reports no status for this tab")
        self.assertEqual(build_entry("working", "idle").status, "working")  # iTerm2's status wins once there is one
        self.assertEqual(build_entry(None, "idle", {"session-1": DisplayedState("idle", 500.0, 400.0)}).status_since, 500.0)
        self.assertEqual(build_entry(None, "idle", {"session-1": DisplayedState("unknown", 500.0, 400.0)}).status_since, 1000.0)

    def test_screen_readings_reach_the_status(self):
        tracking = {"session-1": {"status_change_time": 100.0, "last_time_tab_was_seen": None}}
        snapshot = build_snapshot([make_window("w", [make_tab("1", "claude", status="working")])], tracking=tracking,
                                  screen_readings={"session-1": {"fingerprint_changed_at": 200.0}})
        entry = snapshot.monitors[0].windows[0].agent_sessions[0]
        self.assertEqual((entry.status, entry.status_reason), ("stuck", "thinking with no screen change for 13 min"))

    def test_the_preview_follows_the_state(self):
        preview = {"last_prompt": "Fix it", "message_since_prompt": "Looking now.", "open_tool_call": "Bash: pytest"}
        for status, expected_preview_line in (("working", "Bash: pytest"), ("idle", "Looking now.")):
            snapshot = build_snapshot([make_window("w", [make_tab("1", "claude", status=status)])],
                                      screen_readings={"session-1": {"conversation_preview": preview}})
            self.assertEqual(snapshot.monitors[0].windows[0].agent_sessions[0].preview_line, expected_preview_line)

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

    def test_an_agent_behind_another_program_keeps_its_row_and_state_while_its_process_lives(self):
        running = [make_window("w", [make_tab("1", "claude --model haiku", status="waiting", job_pid=42)])]
        agent_sessions = resolve(running)
        displayed_states = {"session-1": DisplayedState("asking", 900.0, 900.0)}
        # Ctrl+G: an editor is the foreground job, and iTerm2 cleared the status.
        suspended = [make_window("w", [make_tab("1", "vim", status=None, job_pid=77)])]
        agent_sessions = resolve(suspended, agent_sessions, alive_process_ids={42})
        self.assertTrue(agent_sessions["session-1"]["is_in_background"])
        snapshot = build_snapshot(suspended, agent_sessions=agent_sessions, displayed_states=displayed_states,
                                  claude_agents_by_process_id={42: {"name": "reviews-1a", "cwd": "/x/reviews"}})
        entry = snapshot.monitors[0].windows[0].agent_sessions[0]
        self.assertEqual((entry.status, entry.status_since, entry.tab_title), ("asking", 900.0, "reviews-1a"))
        self.assertEqual(entry.agent_tool_name, "Claude Code")
        self.assertEqual(resolve(suspended, agent_sessions, alive_process_ids=()), {})  # the agent process ended

    def test_a_stopped_agent_shows_paused_and_paused_ends_when_it_is_resumed(self):
        running = [make_window("w", [make_tab("1", "claude", status="working", job_pid=42)])]
        agent_sessions = resolve(running)
        # Paused (or Ctrl+Z): the shell is the foreground job again and the agent's process is stopped.
        at_the_shell = [make_window("w", [make_tab("1", "-zsh", status=None, job_pid=77)])]
        agent_sessions = resolve(at_the_shell, agent_sessions, alive_process_ids={42}, stopped_process_ids={42})
        self.assertTrue(agent_sessions["session-1"]["is_stopped"])
        displayed_states = {"session-1": DisplayedState("working", 900.0, 900.0)}
        entry = build_snapshot(at_the_shell, agent_sessions=agent_sessions, displayed_states=displayed_states,
                               now=1000.0).monitors[0].windows[0].agent_sessions[0]
        self.assertEqual((entry.status, entry.status_since), ("paused", 1000.0))
        # Resumed, but iTerm2 hasn't seen the agent back in front yet: "paused" isn't kept.
        agent_sessions = resolve(at_the_shell, agent_sessions, alive_process_ids={42})
        displayed_states = {"session-1": DisplayedState("paused", 1000.0, 900.0)}
        entry = build_snapshot(at_the_shell, agent_sessions=agent_sessions, displayed_states=displayed_states,
                               now=1010.0).monitors[0].windows[0].agent_sessions[0]
        self.assertEqual(entry.status, "unknown")

    def test_background_tasks_and_child_processes_come_from_the_readings(self):
        child_process = ChildProcessEntry(77, "python3 -m unittest", 990.0, True, None, None)
        snapshot = build_snapshot([make_window("w", [make_tab("1", "claude", status="working")])], screen_readings={
            "session-1": {"background_task_count": 2, "child_processes": (child_process,)}})
        entry = snapshot.monitors[0].windows[0].agent_sessions[0]
        self.assertEqual((entry.background_task_count, entry.child_processes), (2, (child_process,)))

    def test_an_agent_that_ended_without_a_session_end_keeps_an_exited_row_in_its_tab(self):
        running = [make_window("w", [make_tab("1", "claude", status="working", job_pid=42),
                                     make_tab("2", "claude", status="idle", job_pid=43)])]
        last_entry = build_snapshot(running, screen_readings={"session-1": {
            "memory_bytes": 1024 ** 2, "conversation_preview": {"open_tool_call": "Bash: pytest"}}}).monitors[0].windows[0].agent_sessions[0]
        # The first tab's agent is gone: its shell is in front again.
        after_the_crash = [make_window("w", [make_tab("1", "-zsh", job_pid=80), make_tab("2", "claude", status="idle", job_pid=43)])]
        snapshot = build_snapshot(after_the_crash, exited_agent_sessions_by_session_id={"session-1": (last_entry, 1500.0)},
                                  now=1600.0)
        exited_entry, other_entry = snapshot.monitors[0].windows[0].agent_sessions
        self.assertEqual((exited_entry.tab_id, exited_entry.status, exited_entry.status_since, exited_entry.ended_abnormally_at),
                         ("1", "exited", 1500.0, 1500.0))
        self.assertEqual((exited_entry.preview_line, exited_entry.memory_bytes, exited_entry.slash_commands),
                         ("Bash: pytest", None, ()))  # what it was doing; nothing of a running agent
        self.assertEqual(other_entry.tab_id, "2")
        # A window whose only agent crashed stays for its row.
        alone = build_snapshot([make_window("w", [make_tab("1", "-zsh", job_pid=80)])],
                               exited_agent_sessions_by_session_id={"session-1": (last_entry, 1500.0)})
        self.assertEqual([entry.status for entry in alone.monitors[0].windows[0].agent_sessions], ["exited"])
        self.assertEqual(alone.monitors[0].windows[0].agent_sessions[0].tab_title, last_entry.tab_title)
        # Renamed after the crash: the row takes the tab's new title.
        renamed = build_snapshot([make_window("w", [make_tab("1", "-zsh", job_pid=80, title="crashed run",
                                                             title_override_format="crashed run")])],
                                 exited_agent_sessions_by_session_id={"session-1": (last_entry, 1500.0)})
        exited_entry = renamed.monitors[0].windows[0].agent_sessions[0]
        self.assertEqual((exited_entry.tab_title, exited_entry.tab_title_set_by_user), ("crashed run", True))

    def test_the_hooks_status_is_in_the_snapshot(self):
        snapshot = build_snapshot([make_window("w", [make_tab("1", "claude")])],
                                  hooks_status_per_agent_tool=(("Claude Code", "installed"), ("Codex", "missing")))
        self.assertEqual(convert_snapshot_to_json_ready_dict(snapshot)["hooks_status_per_agent_tool"],
                         [["Claude Code", "installed"], ["Codex", "missing"]])


class DisplayedStateSinceTests(unittest.TestCase):
    def test_first_sight_uses_the_status_change_time(self):
        self.assertEqual(compute_displayed_state_since("working", 900.0, None, 1000.0), 900.0)

    def test_unchanged_state_keeps_its_time(self):
        self.assertEqual(compute_displayed_state_since("idle", 990.0, DisplayedState("idle", 950.0, 950.0), 1000.0), 950.0)

    def test_new_status_report_uses_its_time(self):
        self.assertEqual(compute_displayed_state_since("working", 980.0, DisplayedState("idle", 950.0, 950.0), 1000.0), 980.0)

    def test_done_becoming_idle_when_seen_starts_now(self):
        self.assertEqual(compute_displayed_state_since("idle", 900.0, DisplayedState("done", 900.0, 900.0), 950.0), 950.0)

    def test_done_seen_after_the_status_already_said_idle_starts_now(self):
        # Done at 100; the status said idle at 150 while the row stayed done; the tab is seen at 200.
        self.assertEqual(compute_displayed_state_since("idle", 150.0, DisplayedState("done", 100.0, 150.0), 200.0), 200.0)


class FolderDisplayNameTests(unittest.TestCase):
    def test_folder_names(self):
        self.assertEqual(build_folder_display_name("/Users/someone/code/project/"), "project")
        self.assertEqual(build_folder_display_name(""), "")
        self.assertEqual(build_folder_display_name("/"), "/")


if __name__ == "__main__":
    unittest.main()
