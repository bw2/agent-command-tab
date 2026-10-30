import json
import os
import tempfile
import unittest
import unittest.mock

from agent_command_tab import iterm2_connection_thread
from agent_command_tab.agent_process_tree import ProcessInfo
from agent_command_tab.hook_event_tracking import apply_hook_event, find_when_the_hooks_ended_the_turn
from agent_command_tab.hook_states_store import load_hook_states, save_hook_states
from agent_command_tab.iterm2_connection_thread import DashboardCore


def make_event(hook_event_name, agent_process_id, **fields):
    return dict({"agent_tool_name": "Claude Code", "hook_event_name": hook_event_name, "sent_at": 50.0,
                 "ancestor_processes": [[agent_process_id + 1000, "sh"], [agent_process_id, "2.1.311"]],
                 "background_tasks": None, "background_task_launched_id": None}, **fields)


class HookStatesFileTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.file_path = os.path.join(self.folder.name, "nested", "agent_hook_states.json")

    def tearDown(self):
        self.folder.cleanup()

    def test_saved_states_load_back(self):
        turn_ended = apply_hook_event(None, make_event("Stop", 101, background_tasks=[{"id": "b2", "status": "running"},
                                                                                      {"id": "b1", "status": "running"}]))
        unknown_tasks = apply_hook_event(None, dict(make_event("UserPromptSubmit", 102), agent_tool_name="Codex"))
        states = {101: (1000.25, turn_ended), 102: (2000.5, unknown_tasks)}
        save_hook_states(self.file_path, states)
        self.assertEqual(load_hook_states(self.file_path), states)
        self.assertEqual(turn_ended["background_task_ids"], {"b1", "b2"})
        self.assertIsNone(unknown_tasks["background_task_ids"])

    def test_a_started_state_and_one_saved_before_starts_were_recorded_both_load(self):  # user request, 2026-10-30
        started = apply_hook_event(None, make_event("SessionStart", 101, source="startup"))
        self.assertEqual(started["startup_reported_at"], 50.0)
        saved_before = apply_hook_event(None, make_event("Stop", 102))
        self.assertNotIn("startup_reported_at", saved_before)
        states = {101: (1000.25, started), 102: (2000.5, saved_before)}
        save_hook_states(self.file_path, states)
        self.assertEqual(load_hook_states(self.file_path), states)

    def test_missing_unreadable_or_malformed_file_gives_no_states(self):
        self.assertEqual(load_hook_states(self.file_path), {})
        os.makedirs(os.path.dirname(self.file_path))
        with open(self.file_path, "w") as file:
            file.write("{not json")
        self.assertEqual(load_hook_states(self.file_path), {})
        good_state = dict(apply_hook_event(None, make_event("Stop", 101, background_tasks=[])), background_task_ids=[])
        with open(self.file_path, "w") as file:
            json.dump([{"process_id": 101, "started_at": 1.0, "hook_state": good_state},
                       {"process_id": "102", "started_at": 1.0, "hook_state": good_state},
                       {"process_id": 103, "started_at": None, "hook_state": good_state},
                       {"process_id": 104, "started_at": 1.0, "hook_state": {"last_turn_end_at": 1.0}},  # keys missing
                       {"process_id": 105, "started_at": 1.0, "hook_state": dict(good_state, last_turn_end_at=[1])},
                       "x"], file)
        self.assertEqual(list(load_hook_states(self.file_path)), [101])


class HookStatesAcrossRestartsTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.file_path = os.path.join(self.folder.name, "agent_hook_states.json")
        self.started_at_by_process_id = {101: 1000.25, 102: 2000.5}
        process_info_patch = unittest.mock.patch.object(
            iterm2_connection_thread, "read_process_info",
            side_effect=lambda process_id: (ProcessInfo(process_id, 1, "2.1.311", self.started_at_by_process_id[process_id])
                                            if process_id in self.started_at_by_process_id else None))
        process_info_patch.start()
        self.addCleanup(process_info_patch.stop)

    def tearDown(self):
        self.folder.cleanup()

    def make_core(self):
        return DashboardCore(lambda snapshot: None, lambda message: None, hook_states_file_path=self.file_path)

    def test_a_turn_end_the_hooks_reported_still_counts_after_a_restart(self):
        # The turn ended while a background task ran on (user report, 2026-10-28): iTerm2 still says
        # working, Claude Code "shell", so only the hooks tell the turn is over, also once the app restarted.
        core = self.make_core()
        core.note_hook_event(make_event("UserPromptSubmit", 101, sent_at=5.0))
        core.note_hook_event(make_event("Stop", 101, sent_at=6.0, background_tasks=[{"id": "b1", "status": "running"}]))
        core.note_hook_event(make_event("Stop", 102, sent_at=7.0, background_tasks=[]))
        restarted_core = self.make_core()
        self.assertEqual(restarted_core.hook_state_by_agent_process_id, core.hook_state_by_agent_process_id)
        hook_state = restarted_core.hook_state_by_agent_process_id[101]
        self.assertEqual(find_when_the_hooks_ended_the_turn(hook_state, 5.5), 6.0)
        self.assertEqual(hook_state["background_task_ids"], {"b1"})

    def test_dump_snapshot_starts_from_the_saved_states_without_writing_the_file(self):  # code review, 2026-10-29
        core = self.make_core()
        core.note_hook_event(make_event("Stop", 101, sent_at=6.0, background_tasks=[{"id": "b1", "status": "running"}]))
        with open(self.file_path) as saved_file:
            saved_file_contents = saved_file.read()
        core_of_dump_snapshot = DashboardCore(lambda snapshot: None, lambda message: None,
                                              started_at_and_hook_state_by_agent_process_id_to_start_with=load_hook_states(self.file_path))
        self.assertEqual(core_of_dump_snapshot.hook_state_by_agent_process_id, core.hook_state_by_agent_process_id)
        core_of_dump_snapshot.note_hook_event(make_event("UserPromptSubmit", 101, sent_at=8.0))
        with open(self.file_path) as saved_file:
            self.assertEqual(saved_file.read(), saved_file_contents)

    def test_a_process_gone_or_started_again_with_the_same_id_gets_no_state(self):
        core = self.make_core()
        core.note_hook_event(make_event("Stop", 101, sent_at=6.0, background_tasks=[]))
        core.note_hook_event(make_event("Stop", 102, sent_at=7.0, background_tasks=[]))
        self.started_at_by_process_id = {101: 3000.0}  # 101 is another process now, and 102 is gone
        self.assertEqual(self.make_core().hook_state_by_agent_process_id, {})

    def test_the_states_of_processes_that_ended_leave_the_file(self):
        core = self.make_core()
        core.note_hook_event(make_event("Stop", 101, sent_at=6.0, background_tasks=[]))
        core.note_hook_event(make_event("Stop", 102, sent_at=7.0, background_tasks=[]))
        del self.started_at_by_process_id[102]
        with unittest.mock.patch.object(iterm2_connection_thread, "process_is_alive", side_effect=lambda process_id: process_id == 101):
            core.forget_agent_processes_that_are_gone()
        self.assertEqual(list(load_hook_states(self.file_path)), [101])

    def test_a_hook_states_file_that_cant_be_written_is_reported_rather_than_ending_the_core(self):
        not_a_folder = os.path.join(self.folder.name, "a file")
        with open(not_a_folder, "w"):
            pass
        core = DashboardCore(lambda snapshot: None, lambda message: None,
                             hook_states_file_path=os.path.join(not_a_folder, "agent_hook_states.json"))
        with unittest.mock.patch("builtins.print") as print_mock:
            core.note_hook_event(make_event("Stop", 101, sent_at=6.0, background_tasks=[]))
        self.assertIn("could not save the hook states", print_mock.call_args.args[0])
        self.assertIn(101, core.hook_state_by_agent_process_id)


if __name__ == "__main__":
    unittest.main()
