"""The core's side of the app's hooks (DashboardCore): hook events received on its socket, the
"exited" row of an agent that ended without a normal session end (R14.1), the background task
counts (R26.2) and the child processes of each agent (user requests, 2026-10-27)."""

import asyncio
import io
import json
import os
import sys
import tempfile
import time
import unittest
import unittest.mock

from agent_command_tab import hook_event_forwarder, iterm2_connection_thread
from agent_command_tab.agent_adapters.claude_code import ClaudeCodeAdapter
from agent_command_tab.agent_process_tree import ProcessInfo
from agent_command_tab.iterm2_connection_thread import EXITED_ROW_SECONDS, DashboardCore, another_process_listens_at
from agent_command_tab.snapshot_data_classes import AgentSessionEntry, ChildProcessEntry


def make_event(hook_event_name, agent_process_id, **fields):
    return dict({"agent_tool_name": "Claude Code", "hook_event_name": hook_event_name, "sent_at": 50.0,
                 "ancestor_processes": [[agent_process_id + 1000, "sh"], [agent_process_id, "2.1.311"]],
                 "background_tasks": None, "background_task_launched_id": None}, **fields)


def make_agent_session(process_id):
    return {"adapter": ClaudeCodeAdapter(), "process_id": process_id, "is_in_background": False, "is_stopped": False,
            "agent_tool_name": "Claude Code", "working_folder": "/x", "started_at": 10.0}


def make_entry(session_id, tab_title):
    return AgentSessionEntry(tab_id=f"t-{session_id}", session_id=session_id, tab_title=tab_title, tab_title_set_by_user=False,
                             agent_session_name="", agent_tool_name="Claude Code", working_folder="/x", status="working")


class ExitedRowTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        # Two tabs run Claude Code: s1's agent is process 101, s2's 102.
        self.core = DashboardCore(lambda snapshot: None, lambda message: None)
        self.core.gathered_windows = [{"window_id": "w1", "tabs": [
            {"tab_id": f"t-{session_id}", "first_session": {"session_id": session_id}} for session_id in ("s1", "s2")]}]
        self.agent_sessions_before = {"s1": make_agent_session(101), "s2": make_agent_session(102)}
        self.core.last_agent_session_entry_by_session_id = {"s1": make_entry("s1", "planning"), "s2": make_entry("s2", "api")}
        self.alive_process_ids = {102}  # 101 is gone
        patch = unittest.mock.patch.object(iterm2_connection_thread, "process_is_alive",
                                           lambda process_id: process_id in self.alive_process_ids)
        patch.start()
        self.addCleanup(patch.stop)

    def notice_the_end_of_s1(self, now=100.0):
        """The snapshot after s1's agent ended: its tab shows its shell, so it runs no agent."""
        self.core.agent_sessions = {"s2": self.agent_sessions_before["s2"]}
        self.core.note_agents_that_ended_without_a_session_end(self.agent_sessions_before, now)
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
        self.core.hook_state_by_agent_process_id.clear()  # no hook event at all: it can't be told apart
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
        self.core.note_hook_event(make_event("SessionEnd", 101, reason="other"))  # it arrived after the process was gone
        self.assertEqual(self.core.exited_agent_sessions_by_session_id, {})
        self.core.hook_state_by_agent_process_id = {101: {"session_ended_at": None, "background_task_ids": None}}
        self.notice_the_end_of_s1()
        self.core.agent_sessions["s1"] = make_agent_session(103)  # a new agent in the tab
        self.core.note_agents_that_ended_without_a_session_end({}, 110.0)
        self.assertEqual(self.core.exited_agent_sessions_by_session_id, {})
        del self.core.agent_sessions["s1"]
        self.notice_the_end_of_s1()
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
        self.core.screen_readings_by_session_id = {"s1": {"background_shell_count": 5}, "s2": {"background_shell_count": 1}}
        self.core.note_background_task_counts()
        self.assertEqual(self.core.screen_readings_by_session_id["s1"]["background_task_count"], 2)
        self.assertEqual(self.core.screen_readings_by_session_id["s2"]["background_task_count"], 1)  # no hook list

    def test_an_agent_inside_a_tabs_agent_has_its_own_hook_state(self):
        # A `claude -p` (process 650) run by s1's agent's shell tool ends normally; s1's agent goes on.
        self.core.note_hook_event(make_event("SessionEnd", 650, reason="other", ancestor_processes=[
            [700, "sh"], [650, "2.1.311"], [640, "bash"], [101, "2.1.311"]]))
        self.assertEqual(set(self.core.hook_state_by_agent_process_id), {650})

    def test_a_clear_typed_in_the_terminal_asks_for_the_next_conversations_id_at_once(self):  # code review, 2026-10-28
        self.core.process_ids_asked_about_in_claude_agents = {101, 102}
        self.core.note_hook_event(make_event("SessionStart", 101, source="compact"))  # the same conversation goes on
        self.assertFalse(self.core.regather_needed.is_set())
        self.core.note_hook_event(make_event("SessionEnd", 101, reason="clear"))
        self.core.note_hook_event(make_event("SessionStart", 101, source="clear"))
        self.assertEqual(self.core.process_ids_asked_about_in_claude_agents, {102})  # the next gather runs `claude agents --json`
        self.assertTrue(self.core.regather_needed.is_set())

    async def test_events_arrive_through_the_socket_from_the_forwarder(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.core.hook_events_socket_path = os.path.join(folder.name, "hook_events.sock")
        listening_task = asyncio.ensure_future(self.core.listen_for_hook_events())
        try:
            while not os.path.exists(self.core.hook_events_socket_path):
                await asyncio.sleep(0.01)
            payload = {"hook_event_name": "SessionEnd", "reason": "prompt_input_exit", "session_id": "x"}
            with unittest.mock.patch.object(hook_event_forwarder, "HOOK_EVENTS_SOCKET_PATH", self.core.hook_events_socket_path), \
                    unittest.mock.patch.object(sys, "stdin", io.StringIO(json.dumps(payload))), \
                    unittest.mock.patch.object(sys, "argv", ["hook_event_forwarder.py", "Claude Code"]):
                await asyncio.to_thread(hook_event_forwarder.main)
            for _ in range(200):
                if self.core.hook_state_by_agent_process_id:
                    break
                await asyncio.sleep(0.01)
        finally:
            listening_task.cancel()
        # The test process's parent stands for the agent: the forwarder's nearest process that isn't a shell.
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
            while not os.path.exists(socket_path):
                await asyncio.sleep(0.01)
            inode_of_the_first = os.stat(socket_path).st_ino
            with unittest.mock.patch.object(iterm2_connection_thread, "SECONDS_BETWEEN_CHECKS_FOR_ANOTHER_HOOK_EVENTS_LISTENER", 0.05):
                second_listening_task = asyncio.ensure_future(second_core.listen_for_hook_events())
                await asyncio.sleep(0.3)
                self.assertEqual(os.stat(socket_path).st_ino, inode_of_the_first)  # not taken over
                # Once the first copy ends, the second takes the socket over (code review, 2026-10-28).
                first_listening_task.cancel()
                until = time.time() + 5
                while time.time() < until and not (os.path.exists(socket_path) and os.stat(socket_path).st_ino != inode_of_the_first
                                                   and another_process_listens_at(socket_path)):
                    await asyncio.sleep(0.02)
                self.assertTrue(another_process_listens_at(socket_path))
                self.assertNotEqual(os.stat(socket_path).st_ino, inode_of_the_first)
        finally:
            first_listening_task.cancel()
            if second_listening_task is not None:
                second_listening_task.cancel()


class TurnEndWhileABackgroundTaskRunsTests(unittest.TestCase):
    """iTerm2's Claude Code integration keeps the status at working while a background task runs
    after the turn ended; the tab agent's Stop event ends the turn (checked live on 2026-10-27)."""

    def setUp(self):
        self.core = DashboardCore(lambda snapshot: None, lambda message: None)
        self.core.agent_sessions = {"s1": make_agent_session(101)}
        self.first_session = {"session_id": "s1", "status_set_in_iterm2": "working", "status": "working", "job_pid": 101}
        self.core.gathered_windows = [{"window_id": "w1", "tabs": [{"tab_id": "t1", "first_session": self.first_session}]}]
        self.list_descendant_processes = unittest.mock.Mock(return_value=[])
        for name, replacement in (("list_descendant_processes", self.list_descendant_processes),
                                  ("process_is_stopped", lambda process_id: False)):
            patch = unittest.mock.patch.object(iterm2_connection_thread, name, replacement)
            patch.start()
            self.addCleanup(patch.stop)

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
        self.core.claude_agents_by_process_id = {101: {"config_folder": None}}
        agent_session = self.core.agent_sessions["s1"]
        with unittest.mock.patch.object(iterm2_connection_thread, "read_claude_code_session_status",
                                        lambda config_folder, process_id: "shell"):
            self.track(1.0)
            self.assertTrue(self.core.claude_code_reports_the_session_busy("s1", agent_session))  # a command in the turn
            self.core.note_hook_event(make_event("Stop", 101, sent_at=5.0))
            self.track(6.0)
            self.assertFalse(self.core.claude_code_reports_the_session_busy("s1", agent_session))
            self.core.note_hook_event(make_event("UserPromptSubmit", 101, sent_at=8.0))
            self.assertTrue(self.core.claude_code_reports_the_session_busy("s1", agent_session))

    def test_a_value_iterm2_sets_after_the_stop_counts(self):
        self.track(1.0)
        self.core.note_hook_event(make_event("Stop", 101, sent_at=5.0))
        self.assertEqual(self.track(6.0, "waiting"), "waiting")
        self.assertEqual(self.track(7.0, "working"), "working")  # set after the Stop (another Stop hook kept the turn going)


class ChildProcessReadingTests(unittest.TestCase):
    def test_a_paused_agent_is_still_in_the_turn_its_tracking_last_saw(self):  # code review, 2026-10-28
        core = DashboardCore(lambda snapshot: None, lambda message: None)
        core.agent_sessions = {"paused": make_agent_session(101), "idle": make_agent_session(102)}
        # The paused tab's shell is in front, so iTerm2 sets no status: its tracking keeps the turn's.
        core.gathered_windows = [{"tabs": [{"first_session": {"session_id": "paused", "status": None}},
                                           {"first_session": {"session_id": "idle", "status": "idle"}}]}]
        core.session_tracking_by_session_id = {"paused": {"status_value": "working", "turn_started_at": 900.0},
                                               "idle": {"status_value": "idle"}}
        self.assertEqual(core.list_agent_processes_and_their_turns(),
                         {"paused": (101, True, 900.0, None, 10.0), "idle": (102, False, None, None, 10.0)})

    def test_entries_with_their_cpu_use_since_the_reading_before(self):
        core = DashboardCore(lambda snapshot: None, lambda message: None)
        core.agent_sessions = {"s1": make_agent_session(101)}
        child = ProcessInfo(201, 101, "bash", 5.0)

        def read_once(cpu_seconds, now, cpu_time_readings_by_child):
            return core.note_child_processes({"s1": [
                {"child": child, "command_text": "python3 -m unittest", "command_started_at": 50.0, "is_background_shell": True,
                 "is_detached": False, "resource_usage": (300 * 1024 ** 2 + 12345, cpu_seconds)}],
                "s-closed": []}, cpu_time_readings_by_child, now)
        cpu_time_readings_by_child = read_once(10.0, 100.0, {})
        self.assertEqual(core.screen_readings_by_session_id["s1"]["child_processes"],
                         (ChildProcessEntry(201, "python3 -m unittest", 50.0, True, 300 * 1024 ** 2, None),))
        read_once(11.5, 103.0, cpu_time_readings_by_child)  # 1.5 CPU seconds in 3 seconds
        self.assertEqual(core.screen_readings_by_session_id["s1"]["child_processes"][0].cpu_percent, 50)
        self.assertEqual(core.screen_readings_by_session_id["s1"]["background_shell_count"], 1)
        self.assertNotIn("s-closed", core.screen_readings_by_session_id)

    def test_detached_jobs_add_to_claude_codes_own_count_of_background_tasks(self):  # user request, 2026-10-28
        core = DashboardCore(lambda snapshot: None, lambda message: None)
        core.agent_sessions = {"s1": make_agent_session(101)}
        background_shell, detached_job = ProcessInfo(201, 101, "bash", 5.0), ProcessInfo(202, 1, "bash", 6.0)
        core.note_child_processes({"s1": [
            {"child": child, "command_text": "sleep 30", "command_started_at": 5.0, "is_background_shell": True,
             "is_detached": is_detached, "resource_usage": None}
            for child, is_detached in ((background_shell, False), (detached_job, True))]}, {}, 100.0)
        core.note_background_task_counts()  # no hooks: the background shells seen, detached jobs included
        self.assertEqual(core.screen_readings_by_session_id["s1"]["background_task_count"], 2)
        # Claude Code's hooks list only the shell it started, not the job that shell left behind.
        core.hook_state_by_agent_process_id = {101: {"background_task_ids": {"shell-1"}}}
        core.note_background_task_counts()
        self.assertEqual(core.screen_readings_by_session_id["s1"]["background_task_count"], 2)


if __name__ == "__main__":
    unittest.main()
