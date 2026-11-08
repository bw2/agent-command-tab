"""The core's side of the app's hooks (DashboardCore): hook events received on its socket, the
"exited" row of an agent that ended without a normal session end, the background task
counts and the child processes of each agent."""

import asyncio
import io
import json
import os
import sys
import tempfile
import unittest
import unittest.mock

from agent_command_tab import hook_event_forwarder, dashboard_core
from agent_command_tab.agent_process_tree import ProcessInfo
from agent_command_tab.dashboard_core import (EXITED_ROW_SECONDS, SECONDS_A_NEW_CLAUDE_CODE_SESSION_HAS_TO_SEND_A_HOOK_EVENT,
                                                        SECONDS_BEFORE_CLAUDE_CODES_OWN_IDLE_COUNTS_OVER_WORKING,
                                                        DashboardCore, another_process_listens_at)
from agent_command_tab.hooks_installer import HOOKS_ARE_INSTALLED, HOOKS_ARE_MISSING, HOOKS_SEND_NO_EVENTS
from agent_command_tab.snapshot_builder import DisplayedState
from agent_command_tab.snapshot_data_classes import ChildProcessEntry
from condition_waiting import wait_until_in_a_coroutine
from gathered_window_builders import make_agent_session, make_agent_session_entry, make_gathered_tab, make_gathered_window
from hook_event_builders import make_event


def make_child_reading(child, **changed_fields):
    """Returns the reading of a background shell below an agent (DashboardCore.note_child_processes's
    format), with these fields changed."""
    return dict({"child": child, "command_text": "sleep 30", "command_started_at": 5.0, "is_background_shell": True,
                 "is_detached": False, "claude_code_task_id": None, "is_a_shell_writing_to_a_file_of_no_claude_code_task": False,
                 "is_a_claude_code_monitor_shell": False, "resource_usage": None}, **changed_fields)


class ExitedRowTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        # Two tabs run Claude Code: s1's agent is process 101, s2's 102.
        self.core = DashboardCore(lambda snapshot: None, lambda message: None)
        self.core.gathered_windows = [{"window_id": "w1", "terminal_name": "iTerm2", "tabs": [
            {"tab_id": f"t-{session_id}", "first_session": {"session_id": session_id}} for session_id in ("s1", "s2")]}]
        self.agent_sessions_before = {"s1": make_agent_session(101), "s2": make_agent_session(102)}
        self.core.last_agent_session_entry_by_session_id = {
            session_id: make_agent_session_entry(session_id, tab_title, status="working")
            for session_id, tab_title in (("s1", "planning"), ("s2", "api"))}
        self.alive_process_ids = {102}  # 101 is gone
        patch = unittest.mock.patch.object(dashboard_core, "process_is_alive",
                                           lambda process_id: process_id in self.alive_process_ids)
        patch.start()
        self.addCleanup(patch.stop)

    def notice_the_end_of_s1(self):
        """The snapshot after s1's agent ended: its tab shows its shell, so it runs no agent."""
        self.core.agent_sessions = {"s2": self.agent_sessions_before["s2"]}
        self.core.note_agents_that_ended_without_a_session_end(self.agent_sessions_before, 100.0)
        self.core.forget_agent_processes_that_are_gone()

    async def test_an_agent_whose_hooks_reported_and_that_is_gone_without_a_session_end_crashed(self):
        self.core.note_hook_event(make_event("SessionStart", 101, source="startup"))
        self.notice_the_end_of_s1()
        exited_agent_session = self.core.exited_agent_sessions_by_session_id["s1"]
        self.assertEqual((exited_agent_session["entry"].tab_title, exited_agent_session["ended_at"],
                          exited_agent_session["process_id"]), ("planning", 100.0, 101))
        self.assertIn(101, self.core.hook_state_by_agent_process_id)  # kept while its row shows
        # The row goes once its time is up.
        self.core.note_agents_that_ended_without_a_session_end({}, 100.0 + EXITED_ROW_SECONDS)
        self.assertEqual(self.core.exited_agent_sessions_by_session_id, {})
        self.core.forget_agent_processes_that_are_gone()
        self.assertNotIn(101, self.core.hook_state_by_agent_process_id)

    async def test_a_normal_session_end_an_agent_without_hooks_and_kill_session_leave_no_row(self):
        self.core.note_hook_event(make_event("SessionEnd", 101, reason="prompt_input_exit"))
        self.notice_the_end_of_s1()
        self.assertEqual(self.core.exited_agent_sessions_by_session_id, {})
        # Its hook state was forgotten once its process was found gone: now an agent without hook
        # events, which can't be told apart from one that ended normally.
        self.notice_the_end_of_s1()
        self.assertEqual(self.core.exited_agent_sessions_by_session_id, {})
        self.core.note_hook_event(make_event("SessionStart", 101, source="startup"))
        self.core.process_ids_ended_from_the_list.add(101)
        self.notice_the_end_of_s1()
        self.assertEqual(self.core.exited_agent_sessions_by_session_id, {})
        self.assertEqual(self.core.process_ids_ended_from_the_list, set())  # forgotten once gone

    async def test_a_late_session_end_a_new_agent_or_a_closed_tab_take_the_row_away(self):
        self.core.note_hook_event(make_event("Stop", 101, background_tasks=[]))
        self.notice_the_end_of_s1()
        self.assertIn("s1", self.core.exited_agent_sessions_by_session_id)
        self.core.note_hook_event(make_event("SessionEnd", 101, reason="other"))  # it arrived after the process was gone
        self.assertEqual(self.core.exited_agent_sessions_by_session_id, {})
        self.core.hook_state_by_agent_process_id = {101: {"session_ended_at": None, "background_task_ids": None}}
        self.notice_the_end_of_s1()
        self.assertIn("s1", self.core.exited_agent_sessions_by_session_id)
        self.core.agent_sessions["s1"] = make_agent_session(103)  # a new agent in the tab
        self.core.note_agents_that_ended_without_a_session_end({}, 110.0)
        self.assertEqual(self.core.exited_agent_sessions_by_session_id, {})
        self.notice_the_end_of_s1()
        self.assertIn("s1", self.core.exited_agent_sessions_by_session_id)
        self.core.gathered_windows[0]["tabs"].pop(0)  # its tab closed
        self.core.note_agents_that_ended_without_a_session_end({}, 110.0)
        self.assertEqual(self.core.exited_agent_sessions_by_session_id, {})


class HookEventTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.core = DashboardCore(lambda snapshot: None, lambda message: None)
        self.core.agent_sessions = {"s1": make_agent_session(101), "s2": make_agent_session(102)}

    def test_background_tasks_count_from_claude_codes_list_or_else_from_the_shells_seen(self):
        self.core.note_hook_event(make_event("SessionStart", 101, source="startup"))
        self.core.note_hook_event(make_event("PostToolUse", 101, background_task_launched_id="a1"))
        self.core.note_hook_event(make_event("PostToolUse", 101, background_task_launched_id="b1"))
        self.core.session_readings_by_session_id = {"s1": {"background_shell_count": 5}, "s2": {"background_shell_count": 1}}
        self.core.note_background_task_counts()
        self.assertEqual(self.core.session_readings_by_session_id["s1"]["background_task_count"], 2)
        self.assertEqual(self.core.session_readings_by_session_id["s2"]["background_task_count"], 1)  # no hook list

    def test_an_agent_inside_a_tabs_agent_has_its_own_hook_state(self):
        # A `claude -p` (process 650) run by s1's agent's shell tool ends normally; s1's agent goes on.
        self.core.note_hook_event(make_event("SessionEnd", 650, reason="other", ancestor_processes=[
            [700, "sh"], [650, "2.1.311"], [640, "bash"], [101, "2.1.311"]]))
        self.assertEqual(set(self.core.hook_state_by_agent_process_id), {650})

    def test_a_clear_typed_in_the_terminal_asks_for_the_next_conversations_id_at_once(self):
        self.core.process_ids_asked_about_in_claude_agents = {101, 102}
        self.core.note_hook_event(make_event("SessionStart", 101, source="compact"))  # the same conversation goes on
        self.assertFalse(self.core.regather_needed.is_set())
        self.core.note_hook_event(make_event("SessionEnd", 101, reason="clear"))
        self.core.note_hook_event(make_event("SessionStart", 101, source="clear"))
        self.assertEqual(self.core.process_ids_asked_about_in_claude_agents, {102})  # the next gather runs `claude agents --json`
        self.assertTrue(self.core.regather_needed.is_set())

    async def wait_for_the_socket(self, socket_path, listening_task):
        """Waits up to 5 seconds for the listener to make its socket, failing rather than hanging
        when it ends or makes none (listen_for_hook_events waits forever after a failed bind)."""
        self.assertTrue(await wait_until_in_a_coroutine(lambda: listening_task.done() or os.path.exists(socket_path)),
                        "the listener made no socket")
        self.assertFalse(listening_task.done(), listening_task)

    async def test_events_arrive_through_the_socket_from_the_forwarder(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.core.hook_events_socket_path = os.path.join(folder.name, "hook_events.sock")
        listening_task = asyncio.ensure_future(self.core.listen_for_hook_events())
        try:
            await self.wait_for_the_socket(self.core.hook_events_socket_path, listening_task)
            payload = {"hook_event_name": "SessionEnd", "reason": "prompt_input_exit", "session_id": "x"}
            with unittest.mock.patch.object(hook_event_forwarder, "HOOK_EVENTS_SOCKET_PATH", self.core.hook_events_socket_path), \
                    unittest.mock.patch.object(sys, "stdin", io.StringIO(json.dumps(payload))), \
                    unittest.mock.patch.object(sys, "argv", ["hook_event_forwarder.py", "Claude Code"]):
                await asyncio.to_thread(hook_event_forwarder.main)
            self.assertTrue(await wait_until_in_a_coroutine(lambda: self.core.hook_state_by_agent_process_id), "no event arrived")
        finally:
            listening_task.cancel()
        # The nearest process above the test that isn't a shell (its parent, or one further up when
        # the tests run from a shell) stands for the agent.
        (hook_state,) = self.core.hook_state_by_agent_process_id.values()
        self.assertIsNotNone(hook_state["session_ended_at"])
        self.assertTrue(self.core.snapshot_needed.is_set())

    async def test_a_second_copy_leaves_the_socket_to_the_copy_that_listens(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        socket_path = os.path.join(folder.name, "hook_events.sock")
        self.core.hook_events_socket_path = socket_path
        second_core = DashboardCore(lambda snapshot: None, lambda message: None, hook_events_socket_path=socket_path)
        first_listening_task = asyncio.ensure_future(self.core.listen_for_hook_events())
        second_listening_task = None
        try:
            await self.wait_for_the_socket(socket_path, first_listening_task)
            inode_of_the_first = os.stat(socket_path).st_ino
            with unittest.mock.patch.object(dashboard_core, "SECONDS_BETWEEN_CHECKS_FOR_ANOTHER_HOOK_EVENTS_LISTENER", 0.05):
                second_listening_task = asyncio.ensure_future(second_core.listen_for_hook_events())
                await asyncio.sleep(0.3)
                self.assertEqual(os.stat(socket_path).st_ino, inode_of_the_first)  # not taken over
                # Once the first copy ends, the second takes the socket over.
                first_listening_task.cancel()
                self.assertTrue(await wait_until_in_a_coroutine(lambda: (
                    os.path.exists(socket_path) and os.stat(socket_path).st_ino != inode_of_the_first
                    and another_process_listens_at(socket_path))), "the second copy didn't take the socket over")
        finally:
            first_listening_task.cancel()
            if second_listening_task is not None:
                second_listening_task.cancel()


class HooksThatSendNoEventsTests(unittest.TestCase):
    def test_installed_hooks_are_said_to_send_no_events_once_a_new_session_has_sent_none_for_two_minutes(self):
        core = DashboardCore(lambda snapshot: None, lambda message: None)
        core.hook_events_are_received = True
        core.agent_sessions = {"s1": make_agent_session(101)}  # started at 10, before the hooks were found
        installed = (("Claude Code", HOOKS_ARE_INSTALLED), ("Codex", HOOKS_ARE_MISSING))
        marked = (("Claude Code", HOOKS_SEND_NO_EVENTS), ("Codex", HOOKS_ARE_MISSING))
        self.assertEqual(core.mark_hooks_that_send_no_events(installed, 1000.0), installed)  # found installed at 1000
        core.agent_sessions["s2"] = dict(make_agent_session(102), started_at=1010.0)  # started after: it reads them
        for now, hook_events_are_received, expected in (
                (1000.0 + 10 + SECONDS_A_NEW_CLAUDE_CODE_SESSION_HAS_TO_SEND_A_HOOK_EVENT - 1, True, installed),  # not yet
                (1000.0 + 10 + SECONDS_A_NEW_CLAUDE_CODE_SESSION_HAS_TO_SEND_A_HOOK_EVENT, True, marked),
                (1000.0 + 10 + SECONDS_A_NEW_CLAUDE_CODE_SESSION_HAS_TO_SEND_A_HOOK_EVENT, False, installed)):  # another copy gets them
            core.hook_events_are_received = hook_events_are_received
            self.assertEqual(core.mark_hooks_that_send_no_events(installed, now), expected, (now, hook_events_are_received))
        core.hook_events_are_received = True
        core.note_hook_event(make_event("SessionStart", 102, source="startup"))
        self.assertEqual(core.mark_hooks_that_send_no_events(installed, 2000.0), installed)

    def test_hooks_installed_again_count_from_when_they_were_found_again(self):
        core = DashboardCore(lambda snapshot: None, lambda message: None)
        core.hook_events_are_received = True
        core.agent_sessions = {"s1": dict(make_agent_session(101), started_at=1010.0)}
        core.mark_hooks_that_send_no_events((("Claude Code", HOOKS_ARE_INSTALLED),), 1000.0)
        core.mark_hooks_that_send_no_events((("Claude Code", HOOKS_ARE_MISSING),), 1005.0)  # taken out, then put back
        self.assertEqual(core.mark_hooks_that_send_no_events((("Claude Code", HOOKS_ARE_INSTALLED),), 1020.0),
                         (("Claude Code", HOOKS_ARE_INSTALLED),))
        # s1 started before they were found again, so it may have started without them.
        self.assertEqual(core.mark_hooks_that_send_no_events((("Claude Code", HOOKS_ARE_INSTALLED),), 5000.0),
                         (("Claude Code", HOOKS_ARE_INSTALLED),))


class AnotherAgentInTheSameTabTests(unittest.TestCase):
    def test_a_new_agent_in_the_tab_starts_without_the_last_agents_finished_turn(self):
        core = DashboardCore(lambda snapshot: None, lambda message: None)
        core.gathered_windows = [make_gathered_window("w1", [
            make_gathered_tab("t1", "s1", "claude", 101, status_set_in_iterm2="working", status="working")])]
        first_session = core.gathered_windows[0]["tabs"][0]["first_session"]

        def track(now, status, agent_process_id):
            first_session.update(status_set_in_iterm2=status, status=status, job_pid=agent_process_id or 90)
            core.agent_sessions = {"s1": make_agent_session(agent_process_id)} if agent_process_id else {}
            core.update_session_tracking(now)
            return core.session_tracking_by_session_id["s1"]
        track(0.5, "idle", 101)
        track(1.0, "working", 101)  # a turn starts
        core.session_tracking_by_session_id["s1"]["last_time_tab_was_seen"] = 0.5
        tracking = track(2.0, "idle", 101)  # done, unseen
        self.assertEqual((tracking["turn_started_at"], tracking["last_turn_ended_at"]), (1.0, 2.0))
        tracking["finished_turn_cleared_at"] = 2.5  # as a /clear of the finished turn records
        track(3.0, None, None)  # the agent ended; its shell is in front
        tracking = track(4.0, "idle", 202)  # a new agent started in the tab (a restore, say)
        for key in ("turn_started_at", "last_turn_ended_at", "finished_turn_cleared_at"):
            self.assertNotIn(key, tracking)
        self.assertEqual((tracking["agent_process_id"], tracking["last_time_tab_was_seen"]), (202, 0.5))


class TurnEndWhileABackgroundTaskRunsTests(unittest.TestCase):
    """iTerm2's Claude Code integration keeps the status at working while a background task runs
    after the turn ended; the tab agent's Stop event ends the turn."""

    def setUp(self):
        self.core = DashboardCore(lambda snapshot: None, lambda message: None)
        self.core.agent_sessions = {"s1": make_agent_session(101)}
        self.core.gathered_windows = [make_gathered_window("w1", [
            make_gathered_tab("t1", "s1", "claude", 101, status_set_in_iterm2="working", status="working")])]
        self.first_session = self.core.gathered_windows[0]["tabs"][0]["first_session"]
        self.list_descendant_processes = unittest.mock.Mock(return_value=[])
        self.claude_code_status = None  # Claude Code's own report of its session (report_claude_codes_own_status)
        for name, replacement in (("list_descendant_processes", self.list_descendant_processes),
                                  ("read_claude_code_session_status", lambda config_folder, process_id: self.claude_code_status)):
            patch = unittest.mock.patch.object(dashboard_core, name, replacement)
            patch.start()
            self.addCleanup(patch.stop)

    def report_claude_codes_own_status(self, status):
        """Has Claude Code's own report of the session (its session file) say status from now on."""
        self.core.claude_agents_by_process_id = {101: {"config_folder": None}}
        self.claude_code_status = status

    def track(self, now, status_set_in_iterm2=None):
        self.first_session["status_set_in_iterm2"] = status_set_in_iterm2 or self.first_session["status_set_in_iterm2"]
        self.core.update_session_tracking(now)
        return self.first_session["status"]

    def test_the_stop_event_ends_the_turn_until_the_next_prompt(self):
        self.assertEqual(self.track(1.0), "working")  # the turn starts
        self.core.note_hook_event(make_event("Stop", 101, sent_at=5.0, background_tasks=[{"id": "b1", "status": "running"}]))
        self.assertEqual(self.track(6.0), "idle")
        self.assertEqual(self.core.session_tracking_by_session_id["s1"]["last_turn_ended_at"], 6.0)  # so it shows done until seen
        self.list_descendant_processes.reset_mock()
        self.assertEqual(self.track(7.0), "idle")  # and stays,
        self.list_descendant_processes.assert_not_called()  # without listing its processes again: iTerm2's value didn't change
        self.core.note_hook_event(make_event("UserPromptSubmit", 101, sent_at=8.0))
        self.assertEqual(self.track(9.0), "working")  # a new turn, though iTerm2's value never changed

    def test_claude_codes_shell_report_after_the_turn_ended_isnt_busy(self):
        # Claude Code says "shell" while its background task runs; its input box takes text then.
        self.report_claude_codes_own_status("shell")
        agent_session = self.core.agent_sessions["s1"]
        self.track(1.0)
        self.assertTrue(self.core.claude_code_reports_the_session_busy("s1", agent_session))  # a command in the turn
        self.core.note_hook_event(make_event("Stop", 101, sent_at=5.0))
        self.track(6.0)
        self.assertFalse(self.core.claude_code_reports_the_session_busy("s1", agent_session))
        self.core.note_hook_event(make_event("UserPromptSubmit", 101, sent_at=8.0))
        self.assertTrue(self.core.claude_code_reports_the_session_busy("s1", agent_session))

    def test_a_stop_from_before_the_app_first_saw_the_tab_still_ends_the_turn(self):
        # The app restarted after the turn ended, while a background task ran: the Stop it saved counts, though
        # the dashboard first sees iTerm2's working after it, and Claude Code says "shell" rather than idle.
        self.report_claude_codes_own_status("shell")
        self.core.note_hook_event(make_event("Stop", 101, sent_at=5.0, background_tasks=[{"id": "b1", "status": "running"}]))
        self.assertEqual(self.track(10.0), "idle")
        self.assertFalse(self.core.claude_code_reports_the_session_busy("s1", self.core.agent_sessions["s1"]))
        self.assertEqual(self.track(11.0, "waiting"), "waiting")  # a value set since counts
        self.assertEqual(self.track(12.0, "working"), "working")

    def test_a_snapshot_matches_the_agents_to_their_tabs_before_the_status_tracking(self):
        # Tracking first found no agent on the first pass after a restart, missed the saved Stop
        # above, and the next pass took the turn as one ending then: "done" for a turn long over.
        core = DashboardCore(lambda snapshot: None, lambda message: None)
        agent_sessions_seen_by_the_tracking = []

        class StopAfterTheTracking(Exception):
            pass
        core.update_session_tracking = lambda now: agent_sessions_seen_by_the_tracking.append(
            {session_id: agent_session["process_id"] for session_id, agent_session in core.agent_sessions.items()})
        core.record_seen_time_for_the_seen_session = unittest.mock.Mock(side_effect=StopAfterTheTracking)
        with unittest.mock.patch.object(dashboard_core, "resolve_agent_sessions",
                                        return_value={"s1": make_agent_session(101)}), \
                self.assertRaises(StopAfterTheTracking):
            core.publish_snapshot_if_changed()
        self.assertEqual(agent_sessions_seen_by_the_tracking, [{"s1": 101}])

    def test_a_value_iterm2_sets_after_the_stop_counts(self):
        self.track(1.0)
        self.core.note_hook_event(make_event("Stop", 101, sent_at=5.0))
        self.assertEqual(self.track(6.0, "waiting"), "waiting")
        self.assertEqual(self.track(7.0, "working"), "working")  # set after the Stop (another Stop hook kept the turn going)

    def test_claude_codes_own_idle_counts_over_a_working_iterm2_set_after_the_turn(self):
        self.report_claude_codes_own_status("busy")
        self.assertEqual(self.track(1.0), "working")
        self.core.note_hook_event(make_event("Stop", 101, sent_at=5.0))
        self.report_claude_codes_own_status("idle")
        self.assertEqual(self.track(6.0, "done"), "done")
        self.assertEqual(self.track(7.0, "working"), "idle")  # set after the turn ended, with no prompt since
        self.assertEqual(self.core.session_tracking_by_session_id["s1"]["last_turn_ended_at"], 6.0)  # not a new turn
        self.core.note_hook_event(make_event("UserPromptSubmit", 101, sent_at=8.0))
        self.assertEqual(self.track(9.0), "working")  # a new turn, though Claude Code's own report still says idle
        self.report_claude_codes_own_status("shell")
        self.assertEqual(self.track(9.0 + SECONDS_BEFORE_CLAUDE_CODES_OWN_IDLE_COUNTS_OVER_WORKING), "working")  # a command runs

    def test_claude_codes_own_idle_counts_over_a_working_that_held_without_hook_events(self):
        # As after the app restarted: no hook events since the turn ended.
        self.report_claude_codes_own_status("idle")
        self.assertEqual(self.track(1.0), "working")  # may be a turn's start, before Claude Code's own report says busy
        self.assertEqual(self.track(1.0 + SECONDS_BEFORE_CLAUDE_CODES_OWN_IDLE_COUNTS_OVER_WORKING), "idle")
        # No turn the dashboard saw ended, so the row is idle, not done.
        self.assertNotIn("last_turn_ended_at", self.core.session_tracking_by_session_id["s1"])

    def test_a_snapshot_is_asked_for_once_claude_codes_own_idle_counts(self):
        # No event comes then, so the row would stay working until something else asked for one.
        self.report_claude_codes_own_status("idle")
        self.assertEqual(self.track(1.0), "working")
        counts_at = 1.0 + SECONDS_BEFORE_CLAUDE_CODES_OWN_IDLE_COUNTS_OVER_WORKING
        self.assertEqual(self.core.own_idle_counts_at_by_session_id, {"s1": counts_at})
        self.core.snapshot_needed.clear()
        self.core.ask_for_a_snapshot_once_claude_codes_own_idle_counts(counts_at - 1)
        self.assertFalse(self.core.snapshot_needed.is_set())
        self.core.ask_for_a_snapshot_once_claude_codes_own_idle_counts(counts_at)
        self.assertTrue(self.core.snapshot_needed.is_set())
        self.assertEqual(self.core.own_idle_counts_at_by_session_id, {})  # asked for once
        self.assertEqual(self.track(counts_at), "idle")  # which that snapshot's tracking finds

    def test_a_new_prompt_gets_the_wait_before_claude_codes_own_idle_counts(self):
        self.report_claude_codes_own_status("idle")  # its own report lags the new turn
        self.track(1.0)  # a working that iTerm2 set with no turn
        self.assertEqual(self.track(1.0 + SECONDS_BEFORE_CLAUDE_CODES_OWN_IDLE_COUNTS_OVER_WORKING), "idle")
        self.core.note_hook_event(make_event("UserPromptSubmit", 101, sent_at=30.0))
        self.assertEqual(self.track(31.0), "working")  # iTerm2's value held 30 s, but the prompt is 1 s old
        self.assertEqual(self.core.session_tracking_by_session_id["s1"]["turn_started_at"], 31.0)

    def test_a_stop_another_stop_hook_blocked_keeps_the_turn_going(self):
        # Claude Code's goal feature blocks the Stop, and the turn goes on with iTerm2 at working all along.
        self.report_claude_codes_own_status("busy")
        self.assertEqual(self.track(1.0), "working")
        self.core.note_hook_event(make_event("Stop", 101, sent_at=5.0, background_tasks=[]))
        self.assertEqual(self.track(6.0), "working")  # it thinks on
        self.report_claude_codes_own_status("shell")
        self.assertEqual(self.track(7.0), "working")  # a command of its own, since no background task runs
        self.assertNotIn("last_turn_ended_at", self.core.session_tracking_by_session_id["s1"])
        self.assertTrue(self.core.claude_code_reports_the_session_busy("s1", self.core.agent_sessions["s1"]))  # nothing typed in
        # The Stop that ends it, leaving a background task running, which Claude Code reports as "shell" too.
        self.core.note_hook_event(make_event("Stop", 101, sent_at=8.0, background_tasks=[{"id": "b1", "status": "running"}]))
        self.assertEqual(self.track(9.0), "idle")
        self.assertEqual(self.core.session_tracking_by_session_id["s1"]["last_turn_ended_at"], 9.0)
        # Blocked again, it starts a command while b1 runs: "shell" says nothing, but the main agent's
        # PreToolUse (no agent_id) after the Stop shows the turn going on.
        self.core.note_hook_event(make_event("Stop", 101, sent_at=10.0, background_tasks=[{"id": "b1", "status": "running"}]))
        self.assertEqual(self.track(11.0), "idle")
        self.core.note_hook_event(make_event("PreToolUse", 101, sent_at=12.0))
        self.assertEqual(self.track(13.0), "working")


class ChildProcessReadingTests(unittest.TestCase):
    def test_a_paused_agent_is_still_in_the_turn_its_tracking_last_saw(self):
        core = DashboardCore(lambda snapshot: None, lambda message: None)
        core.agent_sessions = {"paused": make_agent_session(101), "idle": make_agent_session(102)}
        # The turn goes by the status its tracking last saw, which a pause keeps.
        core.session_tracking_by_session_id = {"paused": {"status_value": "working", "turn_started_at": 900.0},
                                               "idle": {"status_value": "idle"}}
        self.assertEqual(core.list_agent_processes_and_their_turns(),
                         {"paused": (101, True, 900.0, None, 10.0), "idle": (102, False, None, None, 10.0)})

    def test_with_no_status_from_iterm2_an_agent_is_in_a_turn_by_the_state_its_row_shows(self):
        core = DashboardCore(lambda snapshot: None, lambda message: None)
        core.agent_sessions = {"busy": make_agent_session(101), "idle": make_agent_session(102)}
        # After iTerm2 restarted: no value from it yet, so the rows show Claude Code's own report.
        core.session_tracking_by_session_id = {"busy": {"status_value": None}, "idle": {"status_value": None}}
        core.displayed_state_by_session_id = {"busy": DisplayedState("working", 50.0, 50.0), "idle": DisplayedState("idle", 50.0, 50.0)}
        self.assertEqual({session_id: in_a_turn for session_id, (_, in_a_turn, _, _, _) in core.list_agent_processes_and_their_turns().items()},
                         {"busy": True, "idle": False})

    def test_entries_with_their_cpu_use_since_the_reading_before(self):
        core = DashboardCore(lambda snapshot: None, lambda message: None)
        core.agent_sessions = {"s1": make_agent_session(101)}
        child = ProcessInfo(201, 101, "bash", 5.0)

        def read_once(cpu_seconds, now, cpu_time_readings_by_child):
            return core.note_child_processes({"s1": [make_child_reading(
                child, command_text="python3 -m unittest", command_started_at=50.0, claude_code_task_id="b1",
                resource_usage=(300 * 1024 ** 2 + 12345, cpu_seconds))], "s-closed": []}, cpu_time_readings_by_child, now)
        cpu_time_readings_by_child = read_once(10.0, 100.0, {})
        self.assertEqual(core.session_readings_by_session_id["s1"]["child_processes"],
                         (ChildProcessEntry(201, "python3 -m unittest", 50.0, True, 300 * 1024 ** 2, None),))
        read_once(11.5, 103.0, cpu_time_readings_by_child)  # 1.5 CPU seconds in 3 seconds
        self.assertEqual(core.session_readings_by_session_id["s1"]["child_processes"][0].cpu_percent, 50)
        self.assertEqual(core.session_readings_by_session_id["s1"]["background_shell_count"], 1)
        self.assertEqual(core.session_readings_by_session_id["s1"]["running_claude_code_task_ids"], ("b1",))
        self.assertEqual(core.session_readings_by_session_id["s1"]["running_claude_code_task_ids_read_at"], 103.0)
        self.assertNotIn("s-closed", core.session_readings_by_session_id)

    def test_detached_jobs_add_to_claude_codes_own_count_of_background_tasks(self):
        core = DashboardCore(lambda snapshot: None, lambda message: None)
        core.agent_sessions = {"s1": make_agent_session(101)}
        background_shell, detached_job = ProcessInfo(201, 101, "bash", 5.0), ProcessInfo(202, 1, "bash", 6.0)
        core.note_child_processes({"s1": [
            make_child_reading(child, is_detached=is_detached, claude_code_task_id=None if is_detached else "shell-1")
            for child, is_detached in ((background_shell, False), (detached_job, True))]}, {}, 100.0)
        core.note_background_task_counts()  # no hooks: the background shells seen, detached jobs included
        self.assertEqual(core.session_readings_by_session_id["s1"]["background_task_count"], 2)
        # Claude Code's hooks list only the shell it started, not the job that shell left behind.
        core.hook_state_by_agent_process_id = {101: {"background_task_ids": {"shell-1"}}}
        core.note_background_task_counts()
        self.assertEqual(core.session_readings_by_session_id["s1"]["background_task_count"], 2)

    def test_a_listed_shell_counts_only_while_a_shell_below_the_agent_writes_to_its_task_file(self):
        core = DashboardCore(lambda snapshot: None, lambda message: None)
        core.agent_sessions = {"s1": make_agent_session(101)}
        core.note_hook_event(make_event("SessionStart", 101, source="startup"))
        core.note_hook_event(make_event("Stop", 101, sent_at=10.0, background_tasks=[
            {"id": "b1", "type": "shell", "status": "running"}, {"id": "a1", "type": "subagent", "status": "running"}]))

        def count_with_the_shells_running(read_at, *task_ids):
            """Each shell runs the task with that ID, or for "monitor" writes to a socket (as a Claude
            Code Monitor's does), or for "renamed" writes to a file of no task."""
            core.snapshot_needed.clear()
            core.note_child_processes({"s1": [
                make_child_reading(ProcessInfo(201 + index, 101, "bash", 5.0), command_text="sleep 600",
                                   claude_code_task_id=None if task_id in ("monitor", "renamed") else task_id,
                                   is_a_shell_writing_to_a_file_of_no_claude_code_task=task_id == "renamed",
                                   is_a_claude_code_monitor_shell=task_id == "monitor")
                for index, task_id in enumerate(task_ids)]}, {}, read_at)
            core.note_background_task_counts()
            reading = core.session_readings_by_session_id["s1"]
            return reading["background_task_count"], reading["hooks_report_background_tasks_after_the_turn"]
        # Readings taken before the list's shells could all show (a shell younger than
        # SECONDS_A_COMMAND_RUNS_BEFORE_IT_COUNTS isn't read) leave them counted.
        self.assertEqual(count_with_the_shells_running(11.0), (2, True))
        self.assertEqual(count_with_the_shells_running(11.5), (2, True))
        self.assertFalse(core.snapshot_needed.is_set())  # nothing read changed
        # The first reading that tells shows the shell ended: it asks for a snapshot though nothing it read changed.
        self.assertEqual(count_with_the_shells_running(12.0), (1, True))
        self.assertTrue(core.snapshot_needed.is_set())
        self.assertEqual(count_with_the_shells_running(100.0), (1, True))
        self.assertFalse(core.snapshot_needed.is_set())
        self.assertEqual(count_with_the_shells_running(101.0, "b7"), (1, True))  # another task's shell
        # Claude Code lists a Monitor's task as a shell, so b1 might be the Monitor whose shell runs.
        self.assertEqual(count_with_the_shells_running(101.5, "b7", "monitor"), (2, True))
        self.assertEqual(count_with_the_shells_running(102.0, "b1"), (2, True))
        # A shell that writes to a file of no task might be b1's (Claude Code named its files
        # otherwise, say), so b1 counts, and the log says why, once.
        with unittest.mock.patch("builtins.print") as printed:
            self.assertEqual(count_with_the_shells_running(103.0, "renamed"), (2, True))
            self.assertEqual(count_with_the_shells_running(104.0, "renamed"), (2, True))
        self.assertEqual(printed.call_count, 1)
        self.assertIs(printed.call_args.kwargs["file"], sys.stderr)  # dump-snapshot's standard output holds only its JSON
        core.note_hook_event(make_event("Stop", 101, sent_at=110.0, background_tasks=[{"id": "b1", "type": "shell", "status": "running"}]))
        self.assertEqual(count_with_the_shells_running(120.0), (0, False))  # nothing runs after the turn


if __name__ == "__main__":
    unittest.main()
