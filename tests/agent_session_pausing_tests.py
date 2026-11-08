"""Pausing and resuming agent sessions: with patched process calls (no real processes), which
processes get which signal and in what order, and what is recorded; then a real bash in a
terminal of its own running `cat` as the agent, to check that its shell never notices the pause,
and a real tmux pane, whose agent isn't paused (skipped where tmux isn't installed)."""

import asyncio
import collections
import json
import os
import pty
import select
import signal
import subprocess
import tempfile
import time
import unittest
import unittest.mock

from agent_command_tab import agent_session_pausing, dashboard_core
from agent_command_tab.agent_process_tree import ProcessInfo, list_descendant_processes, read_process_info
from agent_command_tab.agent_session_pausing import PauseRecord
from agent_command_tab.dashboard_core_api import ActionRequest, ActionResult
from agent_command_tab.dashboard_core import DashboardCore
from agent_command_tab.pause_records_store import load_pause_records, save_pause_records
from agent_command_tab.session_status_computation import split_agent_sessions_into_pausable_and_resumable
from agent_command_tab.terminal_backends import iterm2_terminal_backend
from condition_waiting import SECONDS_TO_WAIT_FOR_A_REAL_PROCESS, wait_for_the_process_named_below, wait_until
from private_tmux_server import TMUX_PROGRAM_PATH, start_a_private_tmux_server

AGENT_PROCESS_ID = 100
SHELL_PROCESS_ID = 50  # the shell that runs the agent as a job
OUTER_SHELL_PROCESS_ID = 40  # the tab's first shell, when the agent was started from a second one
TOOL = "Claude Code"
STARTED_AT = 1000.0  # every fake process's start time
NOT_A_PROCESS_ID = 99999  # above macOS's highest process ID, 99998
Entry = collections.namedtuple("Entry", ["session_id", "status", "is_paused_with_the_processes_below_it",
                                         "can_be_paused_while_it_runs", "turns_run_outside_the_tab"], defaults=[False, True, False])


def make_pause_record():
    return PauseRecord((AGENT_PROCESS_ID, STARTED_AT), TOOL, [(SHELL_PROCESS_ID, STARTED_AT)],
                       [(101, STARTED_AT), (102, STARTED_AT)])


class FakeProcesses:
    """Which fake processes are stopped, which have their terminal, which hold a terminal that has
    hung up, the agent's job leader and shells, whether it runs in a tmux pane, and the signals
    sent to them."""

    def __init__(self, stopped_process_ids=(), process_ids_below=(101, 102), process_ids_with_their_terminal=(AGENT_PROCESS_ID,),
                 other_users_process_ids_below=(), job_leader_id=AGENT_PROCESS_ID, shell_process_ids=(SHELL_PROCESS_ID,)):
        self.stopped_process_ids = set(stopped_process_ids)
        self.process_ids_below = list(process_ids_below)  # below the job leader
        self.other_users_process_ids_below = list(other_users_process_ids_below)  # run with sudo
        self.dashboard_and_its_ancestor_ids = [999]  # the dashboard's process, not below the agent
        self.process_ids_with_their_terminal = set(process_ids_with_their_terminal)
        self.process_ids_whose_terminal_has_hung_up = set()
        self.job_leader_id = job_leader_id
        self.shell_process_ids = list(shell_process_ids)  # outermost first; none: no job control shell
        self.runs_in_a_tmux_pane = False
        self.start_time_by_process_id = {}  # STARTED_AT unless set
        self.gone_process_ids = set()  # ended after they were read, so a signal no longer reaches them
        self.process_ids_that_no_longer_exist = set()  # ended before they were read
        self.signals = []

    def kill(self, process_id, signal_number):
        if process_id in self.gone_process_ids:
            raise ProcessLookupError(3, "No such process")
        self.signals.append((process_id, signal_number))
        if signal_number == signal.SIGSTOP:
            self.stopped_process_ids.add(process_id)
        elif signal_number == signal.SIGCONT:
            self.stopped_process_ids.discard(process_id)

    def make_process_info(self, process_id):
        if process_id in self.process_ids_that_no_longer_exist:
            return None
        return ProcessInfo(process_id, AGENT_PROCESS_ID, "node", self.start_time_by_process_id.get(process_id, STARTED_AT))

    def patch(self):
        return [unittest.mock.patch.object(agent_session_pausing, "process_is_stopped",
                                           side_effect=lambda process_id: process_id in self.stopped_process_ids),
                unittest.mock.patch.object(agent_session_pausing, "process_has_its_terminal",
                                           side_effect=lambda process_id: process_id in self.process_ids_with_their_terminal),
                unittest.mock.patch.object(agent_session_pausing, "process_terminal_has_hung_up",
                                           side_effect=lambda process_id: process_id in self.process_ids_whose_terminal_has_hung_up),
                unittest.mock.patch.object(agent_session_pausing, "find_the_agents_job_leader_id",
                                           side_effect=lambda process_id: self.job_leader_id),
                unittest.mock.patch.object(agent_session_pausing, "list_the_shells_the_job_runs_under", side_effect=lambda process_id: [
                    self.make_process_info(shell_process_id) for shell_process_id in self.shell_process_ids]),
                unittest.mock.patch.object(agent_session_pausing, "process_runs_in_a_tmux_pane",
                                           side_effect=lambda process_id: self.runs_in_a_tmux_pane),
                unittest.mock.patch.object(
                    agent_session_pausing, "list_descendant_processes_and_other_users_process_ids",
                    side_effect=lambda process_id, maximum_depth=None: (
                        ([self.make_process_info(below) for below in self.process_ids_below], self.other_users_process_ids_below)
                        if process_id == self.job_leader_id else ([], []))),
                unittest.mock.patch.object(agent_session_pausing, "list_the_process_and_its_ancestor_ids",
                                           side_effect=lambda process_id: self.dashboard_and_its_ancestor_ids),
                unittest.mock.patch.object(agent_session_pausing, "read_name_of_any_process", return_value="sudo"),
                unittest.mock.patch.object(agent_session_pausing, "read_process_info", side_effect=self.make_process_info),
                unittest.mock.patch.object(agent_session_pausing.os, "kill", side_effect=self.kill)]


def run_with_patches(processes, function, *arguments, **keyword_arguments):
    patches = processes.patch()
    for patch in patches:
        patch.start()
    try:
        return function(*arguments, **keyword_arguments)
    finally:
        for patch in reversed(patches):
            patch.stop()


class SplitTests(unittest.TestCase):
    def test_paused_sessions_are_resumed_the_others_paused_and_exited_ones_left_alone(self):
        entries = [Entry("a", "working"), Entry("b", "paused", True), Entry("c", "exited"), Entry("d", "idle"),
                   Entry("e", "paused"),  # e: suspended with Ctrl+Z by hand, so the processes below it still run
                   Entry("f", "working", can_be_paused_while_it_runs=False),  # in tmux
                   Entry("g", "paused", can_be_paused_while_it_runs=False),  # in tmux, suspended with Ctrl+Z by hand
                   # Codex sessions run by its shared background server, running and suspended with Ctrl+Z by hand
                   Entry("h", "working", turns_run_outside_the_tab=True), Entry("i", "paused", turns_run_outside_the_tab=True)]
        sessions_to_pause, sessions_to_resume = split_agent_sessions_into_pausable_and_resumable(entries)
        self.assertEqual([entry.session_id for entry in sessions_to_pause], ["a", "d", "e", "g"])
        self.assertEqual([entry.session_id for entry in sessions_to_resume], ["b", "e", "g", "i"])


class PauseTests(unittest.TestCase):
    def test_the_shell_is_stopped_first_then_the_agent_then_every_process_below_it_and_all_are_recorded(self):
        processes = FakeProcesses()
        action_result, pause_record = run_with_patches(processes, agent_session_pausing.pause_agent_session, AGENT_PROCESS_ID, TOOL)
        self.assertTrue(action_result.succeeded)
        self.assertEqual(processes.signals, [(SHELL_PROCESS_ID, signal.SIGSTOP), (AGENT_PROCESS_ID, signal.SIGSTOP),
                                             (101, signal.SIGSTOP), (102, signal.SIGSTOP)])
        self.assertEqual(pause_record, make_pause_record())

    def test_the_rest_of_the_agents_job_is_stopped_and_recorded_too(self):
        # Codex: `node .../codex` leads the job and runs the codex program, the agent, below it.
        processes = FakeProcesses(job_leader_id=90, process_ids_below=(AGENT_PROCESS_ID, 101))
        _, pause_record = run_with_patches(processes, agent_session_pausing.pause_agent_session, AGENT_PROCESS_ID, "Codex")
        self.assertEqual(processes.signals, [(SHELL_PROCESS_ID, signal.SIGSTOP), (AGENT_PROCESS_ID, signal.SIGSTOP),
                                             (90, signal.SIGSTOP), (101, signal.SIGSTOP)])
        self.assertEqual(pause_record.stopped_processes, [(90, STARTED_AT), (101, STARTED_AT)])

    def test_the_detached_processes_its_conversation_started_are_stopped_and_recorded_too(self):
        processes = FakeProcesses()
        detached_process = ProcessInfo(300, 1, "python3", STARTED_AT)  # launchd adopted it when its shell ended
        action_result, pause_record = run_with_patches(processes, agent_session_pausing.pause_agent_session, AGENT_PROCESS_ID, TOOL,
                                                       detached_processes=[detached_process])
        self.assertTrue(action_result.succeeded)
        self.assertEqual(processes.signals[2:], [(101, signal.SIGSTOP), (102, signal.SIGSTOP), (300, signal.SIGSTOP)])
        self.assertEqual(pause_record.stopped_processes, [(101, STARTED_AT), (102, STARTED_AT), (300, STARTED_AT)])

    def test_an_agent_started_from_a_second_shell_gets_both_shells_stopped_the_outer_one_first(self):
        # `bash` typed in a zsh tab: zsh would otherwise see its job, the inner bash, stop.
        processes = FakeProcesses(shell_process_ids=(OUTER_SHELL_PROCESS_ID, SHELL_PROCESS_ID))
        _, pause_record = run_with_patches(processes, agent_session_pausing.pause_agent_session, AGENT_PROCESS_ID, TOOL)
        self.assertEqual(processes.signals[:3], [(OUTER_SHELL_PROCESS_ID, signal.SIGSTOP), (SHELL_PROCESS_ID, signal.SIGSTOP),
                                                 (AGENT_PROCESS_ID, signal.SIGSTOP)])
        self.assertEqual(pause_record.shell_processes, [(OUTER_SHELL_PROCESS_ID, STARTED_AT), (SHELL_PROCESS_ID, STARTED_AT)])
        processes.signals.clear()
        run_with_patches(processes, agent_session_pausing.continue_what_the_pause_stopped, pause_record)
        self.assertEqual(processes.signals[2:], [(AGENT_PROCESS_ID, signal.SIGCONT), (SHELL_PROCESS_ID, signal.SIGCONT),
                                                 (OUTER_SHELL_PROCESS_ID, signal.SIGCONT)])

    def test_an_agent_in_a_tmux_pane_is_left_running(self):  # tmux would continue the pane's shell at once
        processes = FakeProcesses()
        processes.runs_in_a_tmux_pane = True
        action_result, pause_record = run_with_patches(processes, agent_session_pausing.pause_agent_session, AGENT_PROCESS_ID, TOOL)
        self.assertEqual((action_result.succeeded, pause_record, processes.signals), (False, None, []))
        self.assertIn("tmux", action_result.message)

    def test_an_agent_whose_turns_run_outside_the_tab_is_left_running(self):
        # Codex 0.165 and later run a terminal session's turns in their shared background server.
        processes = FakeProcesses()
        action_result, pause_record = run_with_patches(processes, agent_session_pausing.pause_agent_session, AGENT_PROCESS_ID,
                                                       "Codex", turns_run_outside_the_tab=True)
        self.assertEqual((action_result.succeeded, pause_record, processes.signals), (False, None, []))
        self.assertIn("shared background server", action_result.message)
        # Also after a Ctrl+Z, which stops only the tab's client of the server.
        processes = FakeProcesses(stopped_process_ids={AGENT_PROCESS_ID}, process_ids_with_their_terminal=())
        action_result, pause_record = run_with_patches(processes, agent_session_pausing.pause_agent_session, AGENT_PROCESS_ID,
                                                       "Codex", turns_run_outside_the_tab=True)
        self.assertEqual((action_result.succeeded, pause_record, processes.signals), (False, None, []))

    def test_an_agent_under_no_job_control_shell_is_paused_without_one(self):  # the tab's own command
        processes = FakeProcesses(shell_process_ids=())
        _, pause_record = run_with_patches(processes, agent_session_pausing.pause_agent_session, AGENT_PROCESS_ID, TOOL)
        self.assertEqual(processes.signals, [(AGENT_PROCESS_ID, signal.SIGSTOP), (101, signal.SIGSTOP), (102, signal.SIGSTOP)])
        self.assertEqual(pause_record, make_pause_record()._replace(shell_processes=[]))

    def test_an_agent_suspended_with_control_z_gets_only_the_rest_stopped_and_a_record_without_its_shell(self):
        # Its shell has the terminal already, and stopping it now would change nothing. In a tmux
        # pane too, where a running agent is never paused.
        for runs_in_a_tmux_pane in (False, True):
            with self.subTest(runs_in_a_tmux_pane=runs_in_a_tmux_pane):
                processes = FakeProcesses(stopped_process_ids={AGENT_PROCESS_ID}, process_ids_with_their_terminal=())
                processes.runs_in_a_tmux_pane = runs_in_a_tmux_pane
                action_result, pause_record = run_with_patches(processes, agent_session_pausing.pause_agent_session,
                                                               AGENT_PROCESS_ID, TOOL)
                self.assertTrue(action_result.succeeded)
                self.assertEqual(processes.signals, [(101, signal.SIGSTOP), (102, signal.SIGSTOP)])
                self.assertEqual(pause_record, make_pause_record()._replace(shell_processes=[]))

    def test_an_agent_that_ended_meanwhile_leaves_its_shell_running(self):
        processes = FakeProcesses()
        processes.gone_process_ids = {AGENT_PROCESS_ID}
        action_result, pause_record = run_with_patches(processes, agent_session_pausing.pause_agent_session, AGENT_PROCESS_ID, TOOL)
        self.assertEqual((action_result.succeeded, pause_record), (False, None))
        self.assertEqual(processes.signals, [(SHELL_PROCESS_ID, signal.SIGSTOP), (SHELL_PROCESS_ID, signal.SIGCONT)])

    def test_a_process_this_user_may_not_signal_is_left_out_of_the_record_and_reported(self):
        processes = FakeProcesses(process_ids_below=(101, 102, 103))
        signal_process = processes.kill

        def kill(process_id, signal_number):
            if process_id == 102:  # run with sudo
                raise PermissionError(1, "Operation not permitted")
            signal_process(process_id, signal_number)
        processes.kill = kill
        action_result, pause_record = run_with_patches(processes, agent_session_pausing.pause_agent_session, AGENT_PROCESS_ID, TOOL)
        self.assertFalse(action_result.succeeded)
        self.assertIn("keep running: node", action_result.message)
        self.assertEqual(pause_record.stopped_processes, [(101, STARTED_AT), (103, STARTED_AT)])  # still continued later

    def test_a_process_started_before_its_parent_was_stopped_is_stopped_and_recorded_too(self):
        processes = FakeProcesses()
        signal_process = processes.kill

        def kill(process_id, signal_number):
            if (process_id, signal_number) == (102, signal.SIGSTOP):  # a build that started a compiler just before
                processes.process_ids_below.append(103)
            signal_process(process_id, signal_number)
        processes.kill = kill
        action_result, pause_record = run_with_patches(processes, agent_session_pausing.pause_agent_session, AGENT_PROCESS_ID, TOOL)
        self.assertTrue(action_result.succeeded)
        self.assertEqual(processes.signals[2:], [(101, signal.SIGSTOP), (102, signal.SIGSTOP), (103, signal.SIGSTOP)])
        self.assertEqual(pause_record.stopped_processes, [(101, STARTED_AT), (102, STARTED_AT), (103, STARTED_AT)])

    def test_another_users_process_below_the_agent_is_named_and_left_alone(self):
        processes = FakeProcesses(other_users_process_ids_below=(104,))  # sudo, which this user can't even read
        action_result, pause_record = run_with_patches(processes, agent_session_pausing.pause_agent_session, AGENT_PROCESS_ID, TOOL)
        self.assertEqual((action_result.succeeded, action_result.message[-len("keep running: sudo"):]),
                         (False, "keep running: sudo"))
        self.assertEqual(processes.signals[2:], [(101, signal.SIGSTOP), (102, signal.SIGSTOP)])
        self.assertEqual(pause_record, make_pause_record())

    def test_the_dashboard_started_from_the_agents_commands_is_not_stopped(self):
        processes = FakeProcesses(process_ids_below=(101, 102, 103))
        processes.dashboard_and_its_ancestor_ids = [102, 101, AGENT_PROCESS_ID]  # the agent's shell tool started the dashboard
        action_result, pause_record = run_with_patches(processes, agent_session_pausing.pause_agent_session, AGENT_PROCESS_ID, TOOL)
        self.assertTrue(action_result.succeeded)
        self.assertEqual(processes.signals, [(SHELL_PROCESS_ID, signal.SIGSTOP), (AGENT_PROCESS_ID, signal.SIGSTOP),
                                             (103, signal.SIGSTOP)])
        self.assertEqual(pause_record.stopped_processes, [(103, STARTED_AT)])

    def test_a_pause_ends_once_its_agent_runs_again_is_gone_or_its_terminal_has_hung_up(self):
        for stopped_process_ids, start_time_by_process_id, process_ids_that_no_longer_exist, terminal_has_hung_up, has_ended in (
                ({AGENT_PROCESS_ID}, {}, set(), False, False),
                (set(), {}, set(), False, True),  # fg typed by hand after a Ctrl+Z, or a CONT signal from elsewhere
                ({AGENT_PROCESS_ID}, {}, {AGENT_PROCESS_ID}, False, True),  # gone
                ({AGENT_PROCESS_ID}, {AGENT_PROCESS_ID: STARTED_AT + 9}, set(), False, True),  # gone, its process ID reused
                ({AGENT_PROCESS_ID}, {}, set(), True, True)):  # its tab closed, or iTerm2 quit
            processes = FakeProcesses(stopped_process_ids=stopped_process_ids)
            processes.start_time_by_process_id = start_time_by_process_id
            processes.process_ids_that_no_longer_exist = process_ids_that_no_longer_exist
            if terminal_has_hung_up:
                processes.process_ids_whose_terminal_has_hung_up.add(AGENT_PROCESS_ID)
            self.assertEqual(run_with_patches(processes, agent_session_pausing.pause_has_ended, make_pause_record()),
                             has_ended, (stopped_process_ids, terminal_has_hung_up))

    def test_what_a_pause_stopped_is_continued_the_shell_last_unless_it_runs_or_its_process_id_was_reused(self):
        # 101 was continued by hand, and 102 ended and its process ID went to another stopped process.
        processes = FakeProcesses(stopped_process_ids={SHELL_PROCESS_ID, AGENT_PROCESS_ID, 102, 103})
        processes.start_time_by_process_id = {102: STARTED_AT + 5}
        pause_record = make_pause_record()._replace(stopped_processes=[(101, STARTED_AT), (102, STARTED_AT), (103, STARTED_AT)])
        run_with_patches(processes, agent_session_pausing.continue_what_the_pause_stopped, pause_record)
        self.assertEqual(processes.signals, [(103, signal.SIGCONT), (AGENT_PROCESS_ID, signal.SIGCONT),
                                             (SHELL_PROCESS_ID, signal.SIGCONT)])

    def test_a_record_saved_before_pauses_stopped_the_shell_loads_without_one(self):
        # It holds the shell's prompt that the pause, which pressed Ctrl+Z, read; its shell has the terminal.
        with tempfile.TemporaryDirectory() as folder:
            records_file_path = os.path.join(folder, "paused_agent_sessions.json")
            with open(records_file_path, "w") as records_file:
                json.dump([{"agent_process": [AGENT_PROCESS_ID, STARTED_AT], "agent_tool_name": TOOL,
                            "text_around_the_cursor": ["mac:~/code $ ", ""], "stopped_processes": [[101, STARTED_AT]],
                            "session_id": "s1"}], records_file)
            self.assertEqual(load_pause_records(records_file_path), {("s1", AGENT_PROCESS_ID): PauseRecord(
                (AGENT_PROCESS_ID, STARTED_AT), TOOL, [], [(101, STARTED_AT)])})
            save_pause_records(records_file_path, {("s1", AGENT_PROCESS_ID): make_pause_record()})
            self.assertEqual(load_pause_records(records_file_path), {("s1", AGENT_PROCESS_ID): make_pause_record()})


class ResumeTests(unittest.TestCase):
    def test_a_paused_agent_is_continued_with_what_its_pause_stopped_the_shell_last(self):
        processes = FakeProcesses(stopped_process_ids={SHELL_PROCESS_ID, AGENT_PROCESS_ID, 101, 102})
        action_result = run_with_patches(processes, agent_session_pausing.resume_agent_session, AGENT_PROCESS_ID, make_pause_record())
        self.assertTrue(action_result.succeeded)
        self.assertEqual(processes.signals, [(101, signal.SIGCONT), (102, signal.SIGCONT), (AGENT_PROCESS_ID, signal.SIGCONT),
                                             (SHELL_PROCESS_ID, signal.SIGCONT)])

    def test_an_agent_paused_while_a_program_in_a_group_of_its_own_was_in_front_is_continued(self):
        # The program, stopped with the rest, still has the terminal, so the agent doesn't.
        processes = FakeProcesses(stopped_process_ids={SHELL_PROCESS_ID, AGENT_PROCESS_ID, 101, 102},
                                  process_ids_with_their_terminal=(101,))
        action_result = run_with_patches(processes, agent_session_pausing.resume_agent_session, AGENT_PROCESS_ID, make_pause_record())
        self.assertTrue(action_result.succeeded)
        self.assertEqual(processes.signals[-1], (SHELL_PROCESS_ID, signal.SIGCONT))

    def test_an_agent_suspended_with_control_z_is_left_for_fg_typed_in_the_tab(self):
        # By hand (no record), or by a pause of version 0.3.0 or earlier (a record without its shell).
        for pause_record in (None, make_pause_record()._replace(shell_processes=[])):
            processes = FakeProcesses(stopped_process_ids={AGENT_PROCESS_ID, 101, 102}, process_ids_with_their_terminal=())
            action_result = run_with_patches(processes, agent_session_pausing.resume_agent_session, AGENT_PROCESS_ID, pause_record)
            self.assertFalse(action_result.succeeded)
            self.assertIn("type fg in the tab", action_result.message)
            self.assertEqual(processes.signals, [])

    def test_an_agent_stopped_from_elsewhere_and_never_paused_gets_a_cont_signal(self):
        processes = FakeProcesses(stopped_process_ids={AGENT_PROCESS_ID})
        action_result = run_with_patches(processes, agent_session_pausing.resume_agent_session, AGENT_PROCESS_ID, None)
        self.assertTrue(action_result.succeeded)
        self.assertEqual(processes.signals, [(AGENT_PROCESS_ID, signal.SIGCONT)])


class PausingInTheCoreTests(unittest.TestCase):
    def make_core_with_one_agent_tab(self):
        core = DashboardCore(lambda snapshot: None, lambda message: None)
        core.agent_sessions = {"s1": {"process_id": AGENT_PROCESS_ID, "is_in_background": False, "is_stopped": False,
                                      "agent_tool_name": TOOL}}
        core.gathered_windows = [{"window_id": "w1", "terminal_name": "iTerm2", "tabs": [
            {"tab_id": "t1", "first_session": {"session_id": "s1", "command_line": "claude"}}]}]
        return core

    def test_the_core_continues_what_a_pause_stopped_once_the_agent_runs_again_or_is_gone_and_saves_the_rest(self):
        with tempfile.TemporaryDirectory() as folder:
            records_file_path = os.path.join(folder, "paused_agent_sessions.json")
            core = DashboardCore(lambda snapshot: None, lambda message: None, pause_records_file_path=records_file_path)
            still_paused, ended = make_pause_record(), make_pause_record()._replace(agent_process=(200, STARTED_AT))
            core.pause_record_by_tab_and_agent = {("still-paused", AGENT_PROCESS_ID): still_paused, ("tab-closed", 200): ended}
            core.agent_sessions = {"still-paused": {"process_id": AGENT_PROCESS_ID, "is_stopped": True},
                                   "tab-closed": {"process_id": 200, "is_stopped": True}}
            with unittest.mock.patch.object(dashboard_core, "pause_has_ended",
                                            side_effect=lambda pause_record: pause_record is ended), \
                    unittest.mock.patch.object(dashboard_core, "continue_what_the_pause_stopped") as continue_what_it_stopped:
                core.continue_the_processes_of_agents_no_longer_paused()
            self.assertEqual(continue_what_it_stopped.call_args_list, [unittest.mock.call(ended)])
            self.assertEqual(load_pause_records(records_file_path), {("still-paused", AGENT_PROCESS_ID): still_paused})  # what a restart finds
            self.assertEqual([agent_session["is_paused_with_the_processes_below_it"]
                              for agent_session in core.agent_sessions.values()], [True, False])

    def test_a_pause_records_file_that_cant_be_written_is_reported_rather_than_ending_the_core(self):
        with tempfile.TemporaryDirectory() as folder:
            not_a_folder = os.path.join(folder, "a file")
            open(not_a_folder, "w").close()
            core = DashboardCore(lambda snapshot: None, lambda message: None,
                                 pause_records_file_path=os.path.join(not_a_folder, "paused_agent_sessions.json"))
            core.pause_record_by_tab_and_agent = {("s1", AGENT_PROCESS_ID): make_pause_record()}
            with unittest.mock.patch("sys.stdout") as printed:
                core.save_pause_records()
        self.assertIn("could not save the pause records", "".join(call.args[0] for call in printed.write.call_args_list))

    def test_what_ended_pauses_stopped_is_continued_before_connecting_to_iterm2(self):
        # iTerm2 quit: the terminals of its paused agents have hung up, and no connection can be made (the core waits for it to run).
        core = DashboardCore(lambda snapshot: None, lambda message: None,
                             pause_record_by_tab_and_agent_to_start_with={("s1", AGENT_PROCESS_ID): make_pause_record()})
        # Away from the machine's own tmux server and Claude Code sessions.
        core.terminal_backends = [core.terminal_backend_by_name["iTerm2"]]
        core.claude_command_path = None

        async def run_the_core_for_a_moment():
            try:
                await asyncio.wait_for(core.run(), 0.2)
            except asyncio.TimeoutError:
                pass
        with unittest.mock.patch.object(dashboard_core, "pause_has_ended", return_value=True), \
                unittest.mock.patch.object(dashboard_core, "continue_what_the_pause_stopped") as continue_what_it_stopped, \
                unittest.mock.patch.object(iterm2_terminal_backend, "list_iterm2_process_ids", return_value=[]), \
                unittest.mock.patch.object(dashboard_core, "SNAPSHOT_COALESCING_DELAY_SECONDS", 10):  # no snapshot does it instead
            asyncio.run(run_the_core_for_a_moment())
        self.assertEqual(continue_what_it_stopped.call_args_list, [unittest.mock.call(make_pause_record())])
        self.assertEqual(core.pause_record_by_tab_and_agent, {})

    def test_agents_paused_before_a_restart_are_listed_again_from_their_records(self):
        # Codex run through Node ("node .../codex") is named only in the record, not by its process's name.
        records = {("s1", AGENT_PROCESS_ID): make_pause_record(),
                   ("s1", 200): make_pause_record()._replace(agent_process=(200, STARTED_AT + 1)),  # paused later
                   ("s2", AGENT_PROCESS_ID): make_pause_record()._replace(agent_tool_name="Codex"),
                   ("s3", AGENT_PROCESS_ID): make_pause_record()._replace(agent_tool_name="aider"),
                   ("closed-tab", AGENT_PROCESS_ID): make_pause_record()}
        with tempfile.TemporaryDirectory() as folder:
            records_file_path = os.path.join(folder, "paused_agent_sessions.json")
            save_pause_records(records_file_path, records)
            core = DashboardCore(lambda snapshot: None, lambda message: None, pause_records_file_path=records_file_path)
        core.gathered_windows = [{"tabs": [{"first_session": {"session_id": session_id, "command_line": "-zsh"}}
                                           for session_id in ("s1", "s2", "s3")]}]
        with unittest.mock.patch.object(dashboard_core, "pause_has_ended", return_value=False), \
                unittest.mock.patch.object(dashboard_core, "read_process_current_folder", return_value="/x"):
            core.list_again_the_paused_agents_that_lost_their_row()
        agent_session = core.agent_sessions["s1"]
        self.assertEqual((agent_session["agent_tool_name"], agent_session["process_id"], agent_session["is_stopped"],
                          agent_session["is_in_background"], agent_session["working_folder"]),
                         ("Claude Code", 200, True, True, "/x"))
        self.assertEqual([(core.agent_sessions[session_id]["agent_tool_name"], core.agent_sessions[session_id]["adapter"].agent_tool_name)
                          for session_id in ("s2", "s3")], [("Codex", "Codex"), ("aider", "Agent")])  # aider: the generic adapter
        self.assertNotIn("closed-tab", core.agent_sessions)
        self.assertEqual(core.pause_record_by_tab_and_agent, records)

    def test_a_paused_agent_is_listed_again_once_a_newer_agent_in_its_tab_has_ended(self):
        core = self.make_core_with_one_agent_tab()
        core.agent_sessions["s1"]["process_id"] = 200  # started at the shell after the agent 100 was paused
        core.pause_record_by_tab_and_agent = {("s1", AGENT_PROCESS_ID): make_pause_record()}
        with unittest.mock.patch.object(dashboard_core, "pause_has_ended", return_value=False), \
                unittest.mock.patch.object(dashboard_core, "read_process_current_folder", return_value="/x"):
            core.list_again_the_paused_agents_that_lost_their_row()
            self.assertEqual(core.agent_sessions["s1"]["process_id"], 200)  # the newer agent keeps the row while it runs
            del core.agent_sessions["s1"]  # what resolve_agent_sessions leaves once it has ended
            core.list_again_the_paused_agents_that_lost_their_row()
        self.assertEqual(core.agent_sessions["s1"]["process_id"], AGENT_PROCESS_ID)

    def test_a_pause_from_the_list_records_the_pause_agent_session_result(self):
        core = self.make_core_with_one_agent_tab()
        with unittest.mock.patch.object(dashboard_core, "pause_agent_session",
                                        return_value=(ActionResult(True), make_pause_record())) as pause:
            action_result = asyncio.run(core.handle_pause_agent_sessions(ActionRequest("pause_agent_sessions", {"session_ids": ["s1"]})))
        self.assertEqual((action_result.succeeded, pause.call_args.args, core.pause_record_by_tab_and_agent),
                         (True, (AGENT_PROCESS_ID, TOOL), {("s1", AGENT_PROCESS_ID): make_pause_record()}))

    def test_a_newer_agent_in_a_tab_whose_older_agent_is_paused_gets_its_own_pause(self):
        core = self.make_core_with_one_agent_tab()
        core.agent_sessions["s1"]["process_id"] = 200  # started at the shell after the agent 100 was paused
        older_pause_record = make_pause_record()
        core.pause_record_by_tab_and_agent = {("s1", AGENT_PROCESS_ID): older_pause_record}
        newer_pause_record = make_pause_record()._replace(agent_process=(200, STARTED_AT))
        with unittest.mock.patch.object(dashboard_core, "pause_agent_session",
                                        return_value=(ActionResult(True), newer_pause_record)) as pause:
            asyncio.run(core.handle_pause_agent_sessions(ActionRequest("pause_agent_sessions", {"session_ids": ["s1"]})))
            asyncio.run(core.handle_pause_agent_sessions(ActionRequest("pause_agent_sessions", {"session_ids": ["s1"]})))
        self.assertEqual(pause.call_count, 1)  # the second finds it paused already
        self.assertEqual(core.pause_record_by_tab_and_agent,
                         {("s1", AGENT_PROCESS_ID): older_pause_record, ("s1", 200): newer_pause_record})

    def test_a_pause_from_the_list_stops_the_detached_processes_its_sub_rows_show_too(self):
        core = self.make_core_with_one_agent_tab()
        core.agent_sessions["s1"]["started_at"] = STARTED_AT
        core.claude_agents_by_process_id = {AGENT_PROCESS_ID: {"sessionId": "conversation-1"}}
        detached_process = ProcessInfo(300, 1, "python3", STARTED_AT + 5)  # launchd adopted it when its shell ended
        other_detached_process = ProcessInfo(301, 1, "python3", STARTED_AT + 6)  # another conversation's
        with unittest.mock.patch.object(dashboard_core, "list_detached_processes", return_value=[
                    (detached_process, ["python3", "/private/tmp/conversation-1/job.py"], None),
                    (other_detached_process, ["python3", "/private/tmp/conversation-2/job.py"], None)]), \
                unittest.mock.patch.object(dashboard_core, "pause_agent_session",
                                           return_value=(ActionResult(True), make_pause_record())) as pause:
            asyncio.run(core.handle_pause_agent_sessions(ActionRequest("pause_agent_sessions", {"session_ids": ["s1"]})))
        self.assertEqual(pause.call_args.kwargs["detached_processes"], [detached_process])

    def test_a_codex_run_by_its_shared_server_stays_unpausable_when_its_conversation_is_no_longer_known(self):
        # Another Codex in its folder makes the lookup ambiguous, which clears the conversation's readings.
        core = self.make_core_with_one_agent_tab()
        core.note_reading_value("s1", "process_id_whose_turns_run_outside_the_tab", AGENT_PROCESS_ID)
        core.forget_conversation_file_readings("s1")
        with unittest.mock.patch.object(dashboard_core, "pause_agent_session",
                                        return_value=(ActionResult(False, "can't be paused"), None)) as pause:
            asyncio.run(core.handle_pause_agent_sessions(ActionRequest("pause_agent_sessions", {"session_ids": ["s1"]})))
        self.assertTrue(pause.call_args.kwargs["turns_run_outside_the_tab"])
        # A new agent in the tab doesn't take it over.
        core.agent_sessions["s1"]["process_id"] = AGENT_PROCESS_ID + 1
        with unittest.mock.patch.object(dashboard_core, "pause_agent_session",
                                        return_value=(ActionResult(True), make_pause_record())) as pause:
            asyncio.run(core.handle_pause_agent_sessions(ActionRequest("pause_agent_sessions", {"session_ids": ["s1"]})))
        self.assertFalse(pause.call_args.kwargs["turns_run_outside_the_tab"])

    def test_a_pause_waits_for_a_message_being_typed_into_the_tab(self):  # its paste and Enter stay together
        core = self.make_core_with_one_agent_tab()
        events = []

        def pause_stand_in(agent_process_id, agent_tool_name, detached_processes=(), turns_run_outside_the_tab=False):
            events.append("paused")
            return ActionResult(True), make_pause_record()

        async def type_a_message_then_pause():
            lock = core.message_typing_lock_by_session_id.setdefault("s1", asyncio.Lock())
            async with lock:
                pause = asyncio.ensure_future(core.handle_pause_agent_sessions(
                    ActionRequest("pause_agent_sessions", {"session_ids": ["s1"]})))
                await asyncio.sleep(0.01)
                events.append("message typed")
            return await pause
        with unittest.mock.patch.object(dashboard_core, "pause_agent_session", side_effect=pause_stand_in):
            action_result = asyncio.run(type_a_message_then_pause())
        self.assertEqual((action_result.succeeded, events), (True, ["message typed", "paused"]))

    def test_resume_drops_the_record_only_when_it_succeeds(self):
        for resume_result, records_left in ((ActionResult(True), {}),
                                            (ActionResult(False, "type fg in the tab"), {("s1", AGENT_PROCESS_ID): make_pause_record()})):
            core = self.make_core_with_one_agent_tab()
            core.pause_record_by_tab_and_agent = {("s1", AGENT_PROCESS_ID): make_pause_record()}
            with unittest.mock.patch.object(dashboard_core, "resume_agent_session", return_value=resume_result) as resume:
                asyncio.run(core.handle_resume_agent_sessions(ActionRequest("resume_agent_sessions", {"session_ids": ["s1"]})))
            self.assertEqual((resume.call_args.args, core.pause_record_by_tab_and_agent),
                             ((AGENT_PROCESS_ID, make_pause_record()), records_left))

    def test_kill_session_continues_a_paused_agents_shell_too(self):
        # Otherwise the stopped shell would show no prompt once the agent has ended.
        core = self.make_core_with_one_agent_tab()
        core.pause_record_by_tab_and_agent = {("s1", AGENT_PROCESS_ID): make_pause_record()}
        signals = []
        with unittest.mock.patch.object(dashboard_core.os, "kill", side_effect=lambda process_id, signal_number: signals.append(
                    (process_id, signal_number))), \
                unittest.mock.patch.object(dashboard_core, "list_descendant_processes", return_value=[]), \
                unittest.mock.patch.object(dashboard_core, "process_is_alive", return_value=False), \
                unittest.mock.patch.object(dashboard_core, "continue_what_the_pause_stopped") as continue_what_it_stopped:
            action_result = asyncio.run(core.handle_end_agent_process(ActionRequest("end_agent_process", {"session_id": "s1"})))
        self.assertTrue(action_result.succeeded)
        self.assertEqual(signals, [(AGENT_PROCESS_ID, signal.SIGTERM), (AGENT_PROCESS_ID, signal.SIGCONT)])
        self.assertEqual(continue_what_it_stopped.call_args_list, [unittest.mock.call(make_pause_record())])


def read_terminal_output(terminal_file_descriptor, seconds, until_text=None):
    """Returns what the terminal printed within seconds, or as soon as it holds until_text."""
    output = b""
    deadline = time.time() + seconds
    while time.time() < deadline and not (until_text and until_text in output.decode(errors="replace")):
        if select.select([terminal_file_descriptor], [], [], 0.05)[0]:
            try:
                data = os.read(terminal_file_descriptor, 4096)
            except OSError:  # the shell has ended
                break
            if not data:
                break
            output += data
    return output.decode(errors="replace")


class RealShellTests(unittest.TestCase):
    """A real bash in a terminal of its own runs `cat` as the agent, alone, below a wrapper in its
    job, as Codex runs below `node .../codex`, or below a second bash typed at the first."""

    def start_bash_running(self, command_lines):
        """Returns (bash's process ID, cat's process ID, the terminal's other end), once cat runs
        after the command lines were typed in turn, each at a shell's prompt."""
        shell_process_id, terminal_file_descriptor = pty.fork()
        if shell_process_id == 0:
            os.execve("/bin/bash", ["/bin/bash", "--norc", "--noprofile", "-i"], {"PS1": "PROMPT> ", "PATH": "/usr/bin:/bin"})
        self.addCleanup(self.end_processes, shell_process_id)
        for command_line in command_lines:
            self.assertIn("PROMPT> ", read_terminal_output(terminal_file_descriptor, SECONDS_TO_WAIT_FOR_A_REAL_PROCESS, "PROMPT> "))
            os.write(terminal_file_descriptor, command_line.encode() + b"\n")
        cat_process = wait_for_the_process_named_below("cat", shell_process_id)
        self.assertIsNotNone(cat_process)
        cat_process_id = cat_process.process_id
        read_terminal_output(terminal_file_descriptor, 1, command_lines[-1] + "\r\n")  # the command line's echo
        return shell_process_id, cat_process_id, terminal_file_descriptor

    def end_processes(self, shell_process_id):
        for process in list_descendant_processes(shell_process_id) + [read_process_info(shell_process_id)]:
            if process is not None:
                for signal_number in (signal.SIGKILL, signal.SIGCONT):
                    try:
                        os.kill(process.process_id, signal_number)
                    except ProcessLookupError:
                        pass
        try:
            os.waitpid(shell_process_id, 0)
        except ChildProcessError:
            pass

    def pause(self, cat_process_id):
        """pause_agent_session, by a dashboard that didn't start the shell: this test did, and
        the pause leaves out the processes the dashboard started."""
        with unittest.mock.patch.object(agent_session_pausing.os, "getpid", return_value=NOT_A_PROCESS_ID):
            return agent_session_pausing.pause_agent_session(cat_process_id, "cat")

    def test_the_shells_never_notice_the_pause_and_the_agent_reads_its_keys_again_after_the_resume(self):
        for command_lines in (["cat"], ["sh -c 'cat; true'"], ["bash --norc --noprofile -i", "cat"]):
            with self.subTest(command_lines):
                shell_process_id, cat_process_id, terminal_file_descriptor = self.start_bash_running(command_lines)
                action_result, pause_record = self.pause(cat_process_id)
                self.assertTrue(action_result.succeeded)
                expected_shell_process_ids = [shell_process_id]
                if command_lines[0].startswith("sh "):  # the wrapper leads the job and is stopped too
                    wrapper_process_id = read_process_info(cat_process_id).parent_process_id
                    self.assertEqual([process_id for process_id, _ in pause_record.stopped_processes], [wrapper_process_id])
                elif len(command_lines) == 2:  # the second bash runs cat as its job
                    expected_shell_process_ids.append(read_process_info(cat_process_id).parent_process_id)
                self.assertEqual([process_id for process_id, _ in pause_record.shell_processes], expected_shell_process_ids)
                for process_id in expected_shell_process_ids + [cat_process_id]:
                    self.assertTrue(agent_session_pausing.process_is_stopped(process_id))
                os.write(terminal_file_descriptor, b"typed while paused\n")
                self.assertNotIn("Stopped", read_terminal_output(terminal_file_descriptor, 0.5))  # no bash took the terminal
                self.assertFalse(agent_session_pausing.pause_has_ended(pause_record))
                self.assertTrue(agent_session_pausing.resume_agent_session(cat_process_id, pause_record).succeeded)
                os.write(terminal_file_descriptor, b"after the resume\n")
                output = read_terminal_output(terminal_file_descriptor, SECONDS_TO_WAIT_FOR_A_REAL_PROCESS,
                                              "after the resume\r\nafter the resume\r\n")  # the terminal's echo, then cat's
                self.assertEqual((output.count("typed while paused"), output.count("after the resume")), (1, 2))
                os.write(terminal_file_descriptor, b"\x04")  # Ctrl+D ends cat
                for shell_count in range(len(expected_shell_process_ids)):
                    if shell_count:
                        os.write(terminal_file_descriptor, b"exit\n")  # back to the first bash
                    self.assertIn("PROMPT> ", read_terminal_output(terminal_file_descriptor, SECONDS_TO_WAIT_FOR_A_REAL_PROCESS,
                                                                   "PROMPT> "))
                    os.write(terminal_file_descriptor, b"jobs\n")
                    self.assertIn("jobs\r\nPROMPT> ", read_terminal_output(  # it lists no job
                        terminal_file_descriptor, SECONDS_TO_WAIT_FOR_A_REAL_PROCESS, "PROMPT> "))
                os.close(terminal_file_descriptor)

    @unittest.skipIf(TMUX_PROGRAM_PATH is None, "tmux isn't installed")
    def test_an_agent_in_a_tmux_pane_is_left_running(self):
        _, socket_path, environment = start_a_private_tmux_server(self, "/bin/sh")
        tmux_command = [TMUX_PROGRAM_PATH, "-S", socket_path]
        pane_process_id = int(subprocess.run(tmux_command + ["display-message", "-p", "#{pane_pid}"], env=environment,
                                             capture_output=True, text=True).stdout)
        subprocess.run(tmux_command + ["send-keys", "cat", "Enter"], env=environment, check=True)
        cat_process = wait_for_the_process_named_below("cat", pane_process_id)
        self.assertIsNotNone(cat_process)
        action_result, pause_record = self.pause(cat_process.process_id)
        self.assertEqual((action_result.succeeded, pause_record), (False, None))
        self.assertFalse(agent_session_pausing.process_is_stopped(pane_process_id))
        self.assertFalse(agent_session_pausing.process_is_stopped(cat_process.process_id))

    def test_a_paused_agent_whose_tab_closes_ends_once_what_its_pause_stopped_is_continued(self):
        shell_process_id, cat_process_id, terminal_file_descriptor = self.start_bash_running(["cat"])
        _, pause_record = self.pause(cat_process_id)
        self.assertFalse(agent_session_pausing.pause_has_ended(pause_record))
        os.close(terminal_file_descriptor)  # the tab closes; stopped, bash and cat can't act on the hangup
        self.assertTrue(wait_until(lambda: agent_session_pausing.pause_has_ended(pause_record)))
        self.assertTrue(agent_session_pausing.process_is_stopped(cat_process_id))
        agent_session_pausing.continue_what_the_pause_stopped(pause_record)

        def shell_has_ended():
            try:
                return os.waitpid(shell_process_id, os.WNOHANG)[0] == shell_process_id
            except ChildProcessError:  # reaped already
                return True
        self.assertTrue(wait_until(shell_has_ended))
        self.assertTrue(wait_until(lambda: read_process_info(cat_process_id) is None))


if __name__ == "__main__":
    unittest.main()
