"""What the next agent in a tab starts with (DashboardCore.update_session_tracking,
forget_what_was_read_about_agents_no_longer_in_their_tabs): the session tracking and the readings
are kept by tab, and the last agent's must not become the next one's."""

import asyncio
import unittest
import unittest.mock

from agent_command_tab import dashboard_core
from agent_command_tab.conversation_search_texts import build_empty_conversation_search_text
from agent_command_tab.dashboard_core import DashboardCore, build_conversation_file_record
from agent_command_tab.snapshot_data_classes import ChildProcessEntry
from condition_waiting import wait_until_in_a_coroutine
from gathered_window_builders import make_agent_session, make_gathered_tab, make_gathered_window
from hook_event_builders import make_event


class OneTabWhoseAgentChangesTestCase(unittest.TestCase):
    """A core with one iTerm2 tab, s1, whose agent is process 101, and stand-ins for what is read
    of the processes: every process is alive, and those of stopped_process_ids are stopped."""

    def setUp(self):
        self.snapshots = []
        self.core = DashboardCore(self.snapshots.append, lambda message: None)
        self.core.gathered_windows = [make_gathered_window("w1", [
            make_gathered_tab("t1", "s1", "claude", 101, status_set_in_iterm2="idle", status="idle")])]
        self.first_session = self.core.gathered_windows[0]["tabs"][0]["first_session"]
        self.stopped_process_ids = set()
        for name, replacement in (("process_is_alive", lambda process_id: True),
                                  ("process_is_stopped", lambda process_id: process_id in self.stopped_process_ids),
                                  ("list_descendant_processes", lambda process_id, maximum_depth=None: [])):
            patch = unittest.mock.patch.object(dashboard_core, name, replacement)
            patch.start()
            self.addCleanup(patch.stop)


class TrackingOfTheNextAgentInATabTests(OneTabWhoseAgentChangesTestCase):
    def setUp(self):
        super().setUp()
        self.track(1.0, "idle", 101)
        self.track(2.0, "working", 101)  # its turn starts

    def track(self, now, status_set_in_iterm2, agent_process_id):
        """Tracks the tab with this value set in iTerm2 and this agent process in front (None: the
        agent is gone and the tab's shell is in front); returns the tab's tracking."""
        self.first_session.update(status_set_in_iterm2=status_set_in_iterm2, status=status_set_in_iterm2,
                                  job_pid=agent_process_id or 90)
        self.core.agent_sessions = {"s1": make_agent_session(agent_process_id)} if agent_process_id else {}
        self.core.update_session_tracking(now)
        return self.core.session_tracking_by_session_id["s1"]

    def test_the_first_idle_of_an_agent_started_after_one_killed_in_its_turn_ends_no_turn(self):
        self.track(3.0, "working", None)  # killed in its turn: iTerm2 keeps its working
        tracking = self.track(4.0, "idle", 202)  # the next agent, first seen once it has set its own idle
        self.assertEqual(tracking, {"status_value": "idle", "status_change_time": 4.0, "last_time_tab_was_seen": None,
                                    "agent_process_id": 202})  # no "last_turn_ended_at": its row isn't "done"
        # Its own turns start and end as any other's.
        self.track(5.0, "working", 202)
        tracking = self.track(6.0, "idle", 202)
        self.assertEqual((tracking["turn_started_at"], tracking["last_turn_ended_at"]), (5.0, 6.0))

    def test_the_first_change_from_the_value_the_killed_agent_left_ends_no_turn_either(self):
        self.track(3.0, "working", None)
        # The next agent is seen before it has set a value of its own, as when it has just started.
        self.assertEqual(self.track(4.0, "working", 202)["agent_process_id"], 202)
        tracking = self.track(5.0, "idle", 202)
        self.assertNotIn("last_turn_ended_at", tracking)
        self.assertNotIn("status_value_may_be_the_last_agents", tracking)
        self.track(6.0, "working", 202)
        self.assertEqual(self.track(7.0, "done", 202)["last_turn_ended_at"], 7.0)

    def test_a_turn_the_next_agents_hooks_saw_start_ends_though_iterm2_said_working_before_it(self):
        # As `claude "a prompt"` in the tab of an agent killed in its turn: its working is no new value in iTerm2.
        self.track(3.0, "working", None)
        self.track(4.0, "working", 202)
        self.core.note_hook_event(make_event("UserPromptSubmit", 202, sent_at=4.5))
        self.assertEqual(self.track(6.0, "done", 202)["last_turn_ended_at"], 6.0)

    def test_the_done_the_last_agent_left_is_no_finished_turn_of_the_next_agent(self):
        self.assertEqual(self.track(3.0, "done", 101)["last_turn_ended_at"], 3.0)
        self.track(4.0, "done", None)  # it ended, and iTerm2 keeps its done
        self.assertNotIn("last_turn_ended_at", self.track(5.0, "done", 202))

    def test_the_next_agent_keeps_only_when_the_tab_was_last_seen(self):
        tracking = self.track(2.5, "idle", 101)  # its turn ends, unseen
        self.assertEqual((tracking["turn_started_at"], tracking["last_turn_ended_at"]), (2.0, 2.5))
        tracking.update(last_time_tab_was_seen=1.5, finished_turn_cleared_at=2.6,  # as a /clear of the finished turn records
                        turn_start_was_read_from_the_conversation_file=True)
        self.track(3.0, None, None)
        self.assertEqual(self.track(4.0, None, 202), {"status_value": None, "status_change_time": 4.0,
                                                      "last_time_tab_was_seen": 1.5, "agent_process_id": 202})

    def test_a_paused_agent_listed_again_after_a_newer_agent_in_its_tab_ended_gets_a_tracking_of_its_own(self):
        # 202, started at the shell of the paused 101, was killed in its turn. The shell in front
        # has no status, so the paused agent's pass ends before anything is tracked.
        self.track(3.0, None, None)
        self.track(4.0, "working", 202)
        self.first_session.update(status_set_in_iterm2=None, status=None, job_pid=90)
        self.core.agent_sessions = {"s1": dict(make_agent_session(101), is_in_background=True, is_stopped=True)}
        self.stopped_process_ids = {101}
        self.core.update_session_tracking(5.0)
        self.assertEqual(self.core.session_tracking_by_session_id["s1"],
                         {"status_value": None, "status_change_time": 5.0, "last_time_tab_was_seen": None, "agent_process_id": 101})
        # So its child processes aren't read as those of the turn the newer agent was in.
        self.assertEqual(self.core.list_agent_processes_and_their_turns()["s1"][:3], (101, False, None))


class ReadingsOfTheNextAgentInATabTests(OneTabWhoseAgentChangesTestCase):
    def setUp(self):
        super().setUp()
        self.publish()
        # What the periodic readings have noted about process 101 since.
        self.core.session_readings_by_session_id["s1"].update(
            context_tokens=5000, memory_bytes=300 * 1024 ** 2, running_command=("pytest", 1.0),
            child_processes=(ChildProcessEntry(201, "sleep 600", 1.0, True, None, None),))
        self.core.conversation_files_by_session_id["s1"] = build_conversation_file_record((101, None), "/c/101.jsonl", 1.0, None)
        self.core.conversation_search_texts_by_session_id["s1"] = build_empty_conversation_search_text("/c/101.jsonl")

    def publish(self):
        """Publishes a snapshot; returns the row of s1 in it, or None when its tab runs no agent."""
        self.core.publish_snapshot_if_changed()
        windows = self.snapshots[-1].monitors[0].windows
        return windows[0].agent_sessions[0] if windows else None

    def run_another_agent_in_the_tab(self):
        """Process 202 takes the place of 101 as the tab's foreground job; its hooks report its start."""
        self.first_session.update(job_pid=202, job_started_at=50.0)
        self.core.note_hook_event(make_event("SessionStart", 202, source="startup"))

    def assert_nothing_read_about_the_last_agent_is_left(self, row):
        self.assertEqual((row.status, row.context_tokens, row.memory_bytes, row.child_processes), ("new", None, None, ()))
        self.assertNotIn("running_command", self.core.session_readings_by_session_id["s1"])
        self.assertEqual((self.core.conversation_files_by_session_id, self.core.conversation_search_texts_by_session_id), ({}, {}))

    def test_another_snapshot_of_the_same_agent_keeps_its_readings(self):
        self.assertEqual((self.publish().context_tokens, len(self.core.conversation_files_by_session_id),
                          len(self.core.conversation_search_texts_by_session_id)), (5000, 1, 1))

    def test_an_agent_started_in_the_tab_between_two_snapshots_shows_nothing_read_about_the_last_one(self):
        # As `claude && claude`, or an agent started at the shell of one suspended with Ctrl+Z.
        search_text_revision_before = self.core.conversation_search_text_revision
        self.run_another_agent_in_the_tab()
        self.assert_nothing_read_about_the_last_agent_is_left(self.publish())  # with its context size, it showed "idle"
        self.assertEqual(self.snapshots[-1].conversation_search_text_revision, search_text_revision_before + 1)  # the list searches again

    def test_an_agent_started_right_after_the_last_one_ended_shows_nothing_read_about_it(self):
        self.first_session.update(command_line="zsh", job_pid=90)
        with unittest.mock.patch.object(dashboard_core, "process_is_alive", lambda process_id: process_id != 101):
            self.assertIsNone(self.publish())  # its shell is in front
        self.assertNotIn("s1", self.core.session_readings_by_session_id)
        self.first_session.update(command_line="claude")
        self.run_another_agent_in_the_tab()
        self.assert_nothing_read_about_the_last_agent_is_left(self.publish())

    def test_the_done_the_last_agent_left_in_iterm2_doesnt_show_on_the_next_agents_row(self):
        self.first_session.update(status_set_in_iterm2="working")
        self.publish()
        self.first_session.update(status_set_in_iterm2="done")
        self.assertEqual(self.publish().status, "done")  # its own turn ended, and the tab wasn't seen since
        # iTerm2 keeps that done until the next agent sets a status of its own: no turn of the next agent's
        # ended, so its row doesn't wait to be seen, and "new" can take the place of its idle.
        self.run_another_agent_in_the_tab()
        self.assertEqual([self.publish().status, self.publish().status], ["new", "new"])

    def test_the_next_agents_state_starts_when_it_is_first_seen_though_the_last_agents_row_showed_the_same_state(self):
        self.run_another_agent_in_the_tab()
        with unittest.mock.patch.object(dashboard_core.time, "time", return_value=1000.0):
            row_of_the_last_agent = self.publish()
        self.first_session.update(job_pid=303, job_started_at=1050.0)  # as `claude && claude`: no snapshot in between
        self.core.note_hook_event(make_event("SessionStart", 303, source="startup"))
        with unittest.mock.patch.object(dashboard_core.time, "time", return_value=1100.0):
            row_of_the_next_agent = self.publish()
        self.assertEqual((row_of_the_last_agent.status, row_of_the_last_agent.status_since), ("new", 1000.0))
        # Not 1000.0, from before it started, which the Status sort would order it by.
        self.assertEqual((row_of_the_next_agent.status, row_of_the_next_agent.status_since), ("new", 1100.0))
        with unittest.mock.patch.object(dashboard_core.time, "time", return_value=1200.0):
            self.assertEqual(self.publish().status_since, 1100.0)  # its own state's start stays, as any agent's

    def test_a_snapshot_asks_for_a_reading_of_the_conversation_files_when_one_wasnt_looked_up_yet(self):
        reading_needed = self.core.conversation_file_reading_needed
        self.assertTrue(reading_needed.is_set())  # setUp's snapshot first listed process 101
        reading_needed.clear()
        self.publish()
        self.assertFalse(reading_needed.is_set())  # its file was looked up since (setUp's record)
        self.core.claude_agents_by_process_id = {101: {"sessionId": "its-conversation"}}  # as `claude agents --json` names it
        self.publish()
        self.assertTrue(reading_needed.is_set())
        reading_needed.clear()
        self.core.conversation_files_by_session_id["s1"] = build_conversation_file_record(
            (101, "its-conversation"), "/c/its-conversation.jsonl", 1.0, None)
        self.run_another_agent_in_the_tab()
        self.publish()
        self.assertTrue(reading_needed.is_set())

    def test_the_conversation_files_are_read_at_once_when_a_snapshot_asked_for_it(self):
        sessions_read = []

        async def record_the_session_read(session_id, agent_session):
            sessions_read.append(session_id)

        async def run_the_periodic_reading_until_it_read():
            periodic_reading = asyncio.ensure_future(self.core.read_context_window_usage_periodically())
            try:
                return await wait_until_in_a_coroutine(lambda: sessions_read, seconds=1)
            finally:
                periodic_reading.cancel()
        # So the list's rows, and dump-snapshot's one list, don't wait for the next periodic reading.
        with unittest.mock.patch.object(self.core, "update_conversation_file_readings", record_the_session_read), \
                unittest.mock.patch.object(dashboard_core, "CONTEXT_USAGE_CHECK_INTERVAL_SECONDS", 60):
            self.assertTrue(asyncio.run(run_the_periodic_reading_until_it_read()), "no reading came before the periodic one")
        self.assertEqual(sessions_read, ["s1"])
        self.assertFalse(self.core.conversation_file_reading_needed.is_set())  # until a snapshot asks again

    def test_a_snapshot_asks_for_a_reading_of_the_processes_of_an_agent_process_the_last_one_didnt_list(self):
        readings_needed = (self.core.child_process_reading_needed, self.core.resource_usage_reading_needed)
        self.assertEqual([reading_needed.is_set() for reading_needed in readings_needed], [True, True])  # setUp's first listed 101
        for reading_needed in readings_needed:
            reading_needed.clear()
        self.publish()
        self.assertEqual([reading_needed.is_set() for reading_needed in readings_needed], [False, False])  # 101 was listed before
        self.run_another_agent_in_the_tab()
        self.publish()
        self.assertEqual([reading_needed.is_set() for reading_needed in readings_needed], [True, True])

    def test_the_child_processes_and_resource_use_of_an_agent_process_are_read_at_once_when_a_snapshot_first_lists_it(self):
        process_ids_whose_children_were_read, process_ids_whose_resource_use_was_read = [], []

        def read_child_processes(agent_process_id, *other_arguments):
            process_ids_whose_children_were_read.append(agent_process_id)
            return []

        def read_process_tree_resource_usage(process_id):
            process_ids_whose_resource_use_was_read.append(process_id)
            return (512 * 1024 ** 2, 0.0)  # memory in bytes, CPU seconds so far

        async def run_the_periodic_readings_while_another_agent_takes_the_tab():
            periodic_readings = [asyncio.ensure_future(periodic_reading) for periodic_reading in (
                self.core.read_child_processes_periodically(), self.core.read_resource_usage_periodically())]
            try:
                await asyncio.sleep(0.05)  # both wait out their (long) intervals by now
                self.run_another_agent_in_the_tab()
                self.publish()
                return await wait_until_in_a_coroutine(
                    lambda: 202 in process_ids_whose_children_were_read and 202 in process_ids_whose_resource_use_was_read, seconds=1)
            finally:
                for periodic_reading in periodic_readings:
                    periodic_reading.cancel()
        self.core.resource_usage_is_read = True  # the Memory or CPU column is shown
        for reading_needed in (self.core.child_process_reading_needed, self.core.resource_usage_reading_needed):
            reading_needed.clear()  # as the readings that followed setUp's snapshot did
        # So a row doesn't show an agent with a background command as idle, and without its memory, until the next reading.
        with unittest.mock.patch.object(dashboard_core, "read_child_processes", read_child_processes), \
                unittest.mock.patch.object(dashboard_core, "read_process_tree_resource_usage", read_process_tree_resource_usage), \
                unittest.mock.patch.object(dashboard_core, "CHILD_PROCESS_CHECK_INTERVAL_SECONDS", 60), \
                unittest.mock.patch.object(dashboard_core, "RESOURCE_USAGE_CHECK_INTERVAL_SECONDS", 60):
            self.assertTrue(asyncio.run(run_the_periodic_readings_while_another_agent_takes_the_tab()),
                            "no reading of process 202 came before the periodic ones")

    def test_a_reading_made_while_another_agent_took_the_tab_is_dropped(self):
        agent_session_the_reading_began_with = self.core.agent_sessions["s1"]
        self.assertTrue(self.core.tab_still_runs_the_agent_process("s1", agent_session_the_reading_began_with))
        self.publish()  # each snapshot builds the record anew, for the same process
        self.assertIsNot(self.core.agent_sessions["s1"], agent_session_the_reading_began_with)
        self.assertTrue(self.core.tab_still_runs_the_agent_process("s1", agent_session_the_reading_began_with))
        self.run_another_agent_in_the_tab()
        self.publish()
        self.assertFalse(self.core.tab_still_runs_the_agent_process("s1", agent_session_the_reading_began_with))
        self.assertFalse(self.core.tab_still_runs_the_agent_process("s-closed", agent_session_the_reading_began_with))


if __name__ == "__main__":
    unittest.main()
