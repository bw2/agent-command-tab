"""Pausing and resuming agent sessions, with a stand-in tab and patched process calls (no real
processes or iTerm2 sessions): what is pressed and typed, which processes get which signal, and
when nothing is typed because typing fg there wouldn't be safe."""

import asyncio
import collections
import os
import signal
import tempfile
import unittest
import unittest.mock

import iterm2

from agent_command_tab import agent_session_pausing, iterm2_connection_thread
from agent_command_tab.agent_process_tree import ProcessInfo
from agent_command_tab.agent_session_pausing import PauseRecord
from agent_command_tab.dashboard_core_api import ActionRequest, ActionResult
from agent_command_tab.iterm2_connection_thread import DashboardCore
from agent_command_tab.pause_records_store import load_pause_records, save_pause_records
from agent_command_tab.session_status_computation import split_agent_sessions_into_pausable_and_resumable

AGENT_PROCESS_ID = 100
SHELL_PROCESS_ID = 50  # the shell that runs the agent as a job
TOOL = "Claude Code"
PROMPT = "mac:~/code $ "
RIGHT_PROMPT = "    main 12:24"
STARTED_AT = 1000.0  # every fake process's start time
Entry = collections.namedtuple("Entry", ["session_id", "status", "is_paused_with_the_processes_below_it"], defaults=[False])


def make_pause_record(text_before_cursor=PROMPT, text_after_cursor=""):
    return PauseRecord((AGENT_PROCESS_ID, STARTED_AT), TOOL, (text_before_cursor, text_after_cursor),
                       [(101, STARTED_AT), (102, STARTED_AT)])


class FakeLine:
    def __init__(self, text, hard_eol=True):
        self.string = text
        self.hard_eol = hard_eol

    def string_at(self, x):
        if x >= len(self.string):
            raise IndexError(x)
        return self.string[x]


class FakeScreenContents:
    """One screen row per text; the cursor is on the last row, after cursor_text_before (all of it by default)."""

    def __init__(self, rows, cursor_x=None):
        self.lines = [FakeLine(text) for text in rows]
        self.number_of_lines = len(rows)
        self.cursor_coord = unittest.mock.Mock(x=len(rows[-1]) if cursor_x is None else cursor_x, y=40 + len(rows) - 1)
        self.windowed_coord_range = unittest.mock.Mock(coordRange=unittest.mock.Mock(start=unittest.mock.Mock(y=40)))

    def line(self, index):
        return self.lines[index]


class FakeTab:
    """Stands in for an iterm2.Session: records what is typed, and runs on_text after each typing."""

    def __init__(self, screen_rows, on_text=None, cursor_x=None):
        self.screen_rows = screen_rows
        self.cursor_x = cursor_x
        self.typed_texts = []
        self.on_text = on_text or (lambda text: None)

    async def async_send_text(self, text, suppress_broadcast=False):
        self.typed_texts.append(text)
        self.on_text(text)

    async def async_get_screen_contents(self):
        return FakeScreenContents(self.screen_rows, self.cursor_x)


class FakeProcesses:
    """Which fake processes are stopped, which have their terminal, which other jobs the shell
    has suspended, and the signals sent to them."""

    def __init__(self, stopped_process_ids=(), process_ids_below=(101, 102), process_ids_with_their_terminal=(),
                 other_stopped_jobs=(), other_users_process_ids_below=()):
        self.stopped_process_ids = set(stopped_process_ids)
        self.process_ids_below = list(process_ids_below)
        self.other_users_process_ids_below = list(other_users_process_ids_below)  # run with sudo
        self.dashboard_and_its_ancestor_ids = [999]  # the dashboard's process, not below the agent
        self.the_agents_shell_is_in_front = True  # once the agent is suspended
        self.process_ids_with_their_terminal = set(process_ids_with_their_terminal)
        self.other_stopped_jobs = list(other_stopped_jobs)
        self.start_time_by_process_id = {}  # STARTED_AT unless set
        self.signals = []

    def kill(self, process_id, signal_number):
        self.signals.append((process_id, signal_number))
        if signal_number in (signal.SIGSTOP, signal.SIGTSTP):
            self.stopped_process_ids.add(process_id)
        elif signal_number == signal.SIGCONT:
            self.stopped_process_ids.discard(process_id)

    def patch(self):
        def make_process_info(process_id):
            return ProcessInfo(process_id, AGENT_PROCESS_ID, "node", self.start_time_by_process_id.get(process_id, STARTED_AT))
        return [unittest.mock.patch.object(agent_session_pausing, "process_is_stopped",
                                           side_effect=lambda process_id: process_id in self.stopped_process_ids),
                unittest.mock.patch.object(agent_session_pausing, "process_has_its_terminal", side_effect=lambda process_id: (
                    process_id in self.process_ids_with_their_terminal
                    or (process_id == SHELL_PROCESS_ID and self.the_agents_shell_is_in_front))),
                unittest.mock.patch.object(agent_session_pausing, "find_the_shell_running_the_agent",
                                           return_value=SHELL_PROCESS_ID),
                unittest.mock.patch.object(agent_session_pausing, "list_other_stopped_jobs_of_the_agents_shell",
                                           side_effect=lambda process_id: self.other_stopped_jobs),
                unittest.mock.patch.object(
                    agent_session_pausing, "list_descendant_processes_and_other_users_process_ids",
                    side_effect=lambda process_id, maximum_depth=None: (
                        ([make_process_info(below) for below in self.process_ids_below], self.other_users_process_ids_below)
                        if process_id == AGENT_PROCESS_ID else ([], []))),
                unittest.mock.patch.object(agent_session_pausing, "list_the_process_and_its_ancestor_ids",
                                           side_effect=lambda process_id: self.dashboard_and_its_ancestor_ids),
                unittest.mock.patch.object(agent_session_pausing, "read_name_of_any_process", return_value="sudo"),
                unittest.mock.patch.object(agent_session_pausing, "read_process_info", side_effect=make_process_info),
                unittest.mock.patch.object(agent_session_pausing.os, "kill", side_effect=self.kill),
                unittest.mock.patch.object(agent_session_pausing, "SECONDS_BETWEEN_CHECKS", 0.01)]


def run_with_patches(processes, coroutine):
    patches = processes.patch()
    for patch in patches:
        patch.start()
    try:
        return asyncio.run(coroutine)
    finally:
        for patch in reversed(patches):
            patch.stop()


class SplitTests(unittest.TestCase):
    def test_paused_sessions_are_resumed_the_others_paused_and_exited_ones_left_alone(self):
        entries = [Entry("a", "working"), Entry("b", "paused", True), Entry("c", "exited"), Entry("d", "idle"),
                   Entry("e", "paused")]  # e: suspended with Ctrl+Z by hand, so the processes below it still run
        sessions_to_pause, sessions_to_resume = split_agent_sessions_into_pausable_and_resumable(entries)
        self.assertEqual([entry.session_id for entry in sessions_to_pause], ["a", "d", "e"])
        self.assertEqual([entry.session_id for entry in sessions_to_resume], ["b", "e"])


class PauseTests(unittest.TestCase):
    def test_control_z_suspends_the_agent_then_every_process_below_it_is_stopped_and_recorded(self):
        processes = FakeProcesses()
        tab = FakeTab([PROMPT + RIGHT_PROMPT], cursor_x=len(PROMPT),
                      on_text=lambda text: processes.stopped_process_ids.add(AGENT_PROCESS_ID))  # the agent takes Ctrl+Z
        action_result, pause_record = run_with_patches(processes, agent_session_pausing.pause_agent_session(tab, AGENT_PROCESS_ID, TOOL))
        self.assertTrue(action_result.succeeded)
        self.assertEqual(tab.typed_texts, ["\x1a"])
        self.assertEqual(processes.signals, [(101, signal.SIGSTOP), (102, signal.SIGSTOP)])
        self.assertEqual(pause_record, make_pause_record(PROMPT, RIGHT_PROMPT))

    def test_an_agent_that_ignores_control_z_gets_sigtstp_then_sigstop(self):
        processes = FakeProcesses()
        processes.kill = lambda process_id, signal_number: (
            processes.signals.append((process_id, signal_number)),
            signal_number == signal.SIGSTOP and processes.stopped_process_ids.add(process_id))
        tab = FakeTab([PROMPT])
        with unittest.mock.patch.object(agent_session_pausing, "SECONDS_TO_WAIT_FOR_CONTROL_Z", 0.05), \
                unittest.mock.patch.object(agent_session_pausing, "SECONDS_TO_WAIT_FOR_SIGTSTP", 0.05):
            action_result, _ = run_with_patches(processes, agent_session_pausing.pause_agent_session(tab, AGENT_PROCESS_ID, TOOL))
        self.assertTrue(action_result.succeeded)
        self.assertEqual(processes.signals[:2], [(AGENT_PROCESS_ID, signal.SIGTSTP), (AGENT_PROCESS_ID, signal.SIGSTOP)])

    def test_the_prompt_is_recorded_only_once_the_shell_has_the_terminal_and_its_line_settles(self):
        processes = FakeProcesses(process_ids_with_their_terminal={AGENT_PROCESS_ID})
        # The agent's own input line still shows, then the shell draws its prompt bit by bit.
        screens = [["❯ "], ["[1]+  Stopped  claude", "mac:~/co"], ["[1]+  Stopped  claude", PROMPT]]
        tab = FakeTab(screens[0])
        screen_reads = []

        def on_control_z(text):
            processes.stopped_process_ids.add(AGENT_PROCESS_ID)
            # The shell takes the terminal back a moment later; until then nothing is read.
            asyncio.get_running_loop().call_later(0.05, processes.process_ids_with_their_terminal.clear)
        tab.on_text = on_control_z

        async def read_next_screen():
            self.assertFalse(processes.process_ids_with_their_terminal)
            screen_reads.append(None)
            return FakeScreenContents(screens[min(len(screen_reads) - 1, len(screens) - 1)])
        tab.async_get_screen_contents = read_next_screen
        processes.kill = lambda process_id, signal_number: processes.signals.append((process_id, signal_number))
        _, pause_record = run_with_patches(processes, agent_session_pausing.pause_agent_session(tab, AGENT_PROCESS_ID, TOOL))
        self.assertEqual(pause_record.text_around_the_cursor, (PROMPT, ""))

    def test_a_tab_closed_while_the_pause_waits_for_the_prompt_still_gets_a_record(self):
        processes = FakeProcesses()
        tab = FakeTab([PROMPT], on_text=lambda text: processes.stopped_process_ids.add(AGENT_PROCESS_ID))

        async def tab_closed():
            raise iterm2.RPCException("SESSION_NOT_FOUND")
        tab.async_get_screen_contents = tab_closed
        action_result, pause_record = run_with_patches(processes, agent_session_pausing.pause_agent_session(tab, AGENT_PROCESS_ID, TOOL))
        self.assertTrue(action_result.succeeded)
        self.assertEqual(pause_record, make_pause_record()._replace(text_around_the_cursor=None))  # still continued later

    def test_no_prompt_is_recorded_when_the_shell_shows_none(self):
        processes = FakeProcesses()
        tab = FakeTab([""], on_text=lambda text: processes.stopped_process_ids.add(AGENT_PROCESS_ID))  # a slow prompt
        with unittest.mock.patch.object(agent_session_pausing, "SECONDS_TO_WAIT_FOR_THE_SHELL_PROMPT", 0.05):
            _, pause_record = run_with_patches(processes, agent_session_pausing.pause_agent_session(tab, AGENT_PROCESS_ID, TOOL))
        self.assertIsNone(pause_record.text_around_the_cursor)
        self.assertEqual(len(pause_record.stopped_processes), 2)  # still continued later

    def test_an_agent_suspended_by_hand_gets_the_processes_below_it_stopped_and_a_record_without_prompt(self):
        processes = FakeProcesses(stopped_process_ids={AGENT_PROCESS_ID})
        tab = FakeTab([PROMPT + "ls"])  # typed at the shell meanwhile
        action_result, pause_record = run_with_patches(processes, agent_session_pausing.pause_agent_session(tab, AGENT_PROCESS_ID, TOOL))
        self.assertTrue(action_result.succeeded)
        self.assertEqual(tab.typed_texts, [])  # no Ctrl+Z: the shell is in front
        self.assertEqual(processes.signals, [(101, signal.SIGSTOP), (102, signal.SIGSTOP)])
        self.assertEqual(pause_record, PauseRecord((AGENT_PROCESS_ID, STARTED_AT), TOOL, None,
                                                   [(101, STARTED_AT), (102, STARTED_AT)]))

    def test_a_pause_ends_once_its_agent_runs_again_or_is_gone(self):
        pause_record = make_pause_record()
        for stopped_process_ids, start_time_by_process_id, has_ended in (
                ({AGENT_PROCESS_ID}, {}, False),
                (set(), {}, True),  # fg typed by hand
                ({AGENT_PROCESS_ID}, {AGENT_PROCESS_ID: STARTED_AT + 9}, True)):  # gone, its process ID reused
            processes = FakeProcesses(stopped_process_ids=stopped_process_ids)
            processes.start_time_by_process_id = start_time_by_process_id
            results = []

            async def check():
                results.append(agent_session_pausing.pause_has_ended(pause_record))
            run_with_patches(processes, check())
            self.assertEqual(results, [has_ended], stopped_process_ids)


class ResumeTests(unittest.TestCase):
    def test_fg_is_typed_at_the_unchanged_prompt_then_the_stopped_processes_continue(self):
        for text_before_cursor in (PROMPT, PROMPT + "22;3;1R"):  # a terminal reply that reached the shell at the pause
            processes = FakeProcesses(stopped_process_ids={AGENT_PROCESS_ID, 101, 102})
            tab = FakeTab([text_before_cursor + RIGHT_PROMPT], cursor_x=len(text_before_cursor),
                          on_text=lambda text: processes.stopped_process_ids.discard(AGENT_PROCESS_ID))  # fg
            action_result = run_with_patches(processes, agent_session_pausing.resume_agent_session(
                tab, AGENT_PROCESS_ID, make_pause_record(text_before_cursor, RIGHT_PROMPT)))
            self.assertTrue(action_result.succeeded)
            self.assertEqual(tab.typed_texts, ["\x15fg\r"])  # Ctrl+U first clears what the line held at the pause
            self.assertEqual(processes.signals, [(101, signal.SIGCONT), (102, signal.SIGCONT)])  # after fg, not before

    def test_nothing_is_typed_and_nothing_continues_unless_typing_fg_is_safe(self):
        for screen_row, pause_record, other_stopped_jobs, words in (
                (PROMPT + "git push", make_pause_record(), [], "shell line has changed"),
                (PROMPT + "    main 12:25", make_pause_record(PROMPT, RIGHT_PROMPT), [], "shell line has changed"),
                (PROMPT + "echo review # ", None, [], "type fg in the tab"),  # suspended by hand: no prompt seen
                (PROMPT, PauseRecord((AGENT_PROCESS_ID, STARTED_AT), TOOL, None, []), [], "type fg in the tab"),  # no prompt showed
                (PROMPT, make_pause_record(), [555], "another job is suspended")):
            processes = FakeProcesses(stopped_process_ids={AGENT_PROCESS_ID, 101, 102}, other_stopped_jobs=other_stopped_jobs)
            tab = FakeTab([screen_row])
            action_result = run_with_patches(processes, agent_session_pausing.resume_agent_session(
                tab, AGENT_PROCESS_ID, pause_record))
            self.assertFalse(action_result.succeeded, screen_row)
            self.assertIn(words, action_result.message)
            self.assertEqual((tab.typed_texts, processes.signals), ([], []), screen_row)

    def test_nothing_is_typed_while_another_program_is_in_front_of_the_agents_shell(self):
        processes = FakeProcesses(stopped_process_ids={AGENT_PROCESS_ID, 101, 102})
        processes.the_agents_shell_is_in_front = False  # a zsh started at its prompt, showing the same prompt
        tab = FakeTab([PROMPT])
        action_result = run_with_patches(processes, agent_session_pausing.resume_agent_session(
            tab, AGENT_PROCESS_ID, make_pause_record()))
        self.assertFalse(action_result.succeeded)
        self.assertIn("another program is in front", action_result.message)
        self.assertEqual((tab.typed_texts, processes.signals), ([], []))

    def test_an_agent_that_still_has_its_terminal_gets_a_sigcont_instead_of_fg(self):
        processes = FakeProcesses(stopped_process_ids={AGENT_PROCESS_ID}, process_ids_with_their_terminal={AGENT_PROCESS_ID})
        tab = FakeTab(["whatever is on screen"])
        action_result = run_with_patches(processes, agent_session_pausing.resume_agent_session(tab, AGENT_PROCESS_ID, None))
        self.assertTrue(action_result.succeeded)
        self.assertEqual(tab.typed_texts, [])
        self.assertEqual(processes.signals, [(AGENT_PROCESS_ID, signal.SIGCONT)])

    def test_fg_that_doesnt_bring_back_the_agent_is_reported(self):
        processes = FakeProcesses(stopped_process_ids={AGENT_PROCESS_ID})
        tab = FakeTab([PROMPT])
        with unittest.mock.patch.object(agent_session_pausing, "SECONDS_TO_WAIT_FOR_FG", 0.05):
            action_result = run_with_patches(processes, agent_session_pausing.resume_agent_session(
                tab, AGENT_PROCESS_ID, make_pause_record()))
        self.assertFalse(action_result.succeeded)
        self.assertIn("fg %N", action_result.message)

    def test_a_process_this_user_may_not_signal_is_left_out_of_the_record_and_reported(self):
        processes = FakeProcesses(process_ids_below=(101, 102, 103))
        signal_process = processes.kill

        def kill(process_id, signal_number):
            if process_id == 102:  # run with sudo
                raise PermissionError(1, "Operation not permitted")
            signal_process(process_id, signal_number)
        processes.kill = kill
        tab = FakeTab([PROMPT], on_text=lambda text: processes.stopped_process_ids.add(AGENT_PROCESS_ID))
        action_result, pause_record = run_with_patches(processes, agent_session_pausing.pause_agent_session(tab, AGENT_PROCESS_ID, TOOL))
        self.assertFalse(action_result.succeeded)
        self.assertIn("keep running: node", action_result.message)
        self.assertEqual(pause_record.stopped_processes, [(101, STARTED_AT), (103, STARTED_AT)])  # still continued later

    def test_another_users_process_below_the_agent_is_named_and_left_alone(self):
        processes = FakeProcesses(other_users_process_ids_below=(104,))  # sudo, which this user can't even read
        tab = FakeTab([PROMPT], on_text=lambda text: processes.stopped_process_ids.add(AGENT_PROCESS_ID))
        action_result, pause_record = run_with_patches(processes, agent_session_pausing.pause_agent_session(tab, AGENT_PROCESS_ID, TOOL))
        self.assertEqual((action_result.succeeded, action_result.message[-len("keep running: sudo"):]),
                         (False, "keep running: sudo"))
        self.assertEqual(processes.signals, [(101, signal.SIGSTOP), (102, signal.SIGSTOP)])
        self.assertEqual(pause_record, make_pause_record())

    def test_the_dashboard_started_from_the_agents_commands_is_not_stopped(self):
        processes = FakeProcesses(process_ids_below=(101, 102, 103))
        processes.dashboard_and_its_ancestor_ids = [102, 101, AGENT_PROCESS_ID]  # the agent's shell started the dashboard
        tab = FakeTab([PROMPT], on_text=lambda text: processes.stopped_process_ids.add(AGENT_PROCESS_ID))
        action_result, pause_record = run_with_patches(processes, agent_session_pausing.pause_agent_session(tab, AGENT_PROCESS_ID, TOOL))
        self.assertTrue(action_result.succeeded)
        self.assertEqual(processes.signals, [(103, signal.SIGSTOP)])
        self.assertEqual(pause_record.stopped_processes, [(103, STARTED_AT)])

    def test_only_processes_still_stopped_with_the_same_start_time_are_continued(self):
        processes = FakeProcesses(stopped_process_ids={101, 103})
        processes.start_time_by_process_id = {103: STARTED_AT + 5}  # 103 ended, and its process ID was reused

        async def continue_them():  # called once the patches are in place
            agent_session_pausing.continue_processes_stopped_at_pause([(101, STARTED_AT), (102, STARTED_AT), (103, STARTED_AT)])
        run_with_patches(processes, continue_them())
        self.assertEqual(processes.signals, [(101, signal.SIGCONT)])

    def test_the_core_continues_what_a_pause_stopped_once_the_agent_runs_again_or_is_gone_and_saves_the_rest(self):
        with tempfile.TemporaryDirectory() as folder:
            records_file_path = os.path.join(folder, "paused_agent_sessions.json")
            core = DashboardCore(lambda snapshot: None, lambda message: None, pause_records_file_path=records_file_path)
            still_paused, ended = make_pause_record(), make_pause_record()._replace(agent_process=(200, STARTED_AT))
            core.pause_record_by_tab_and_agent = {("still-paused", AGENT_PROCESS_ID): still_paused, ("fg-typed-by-hand", 200): ended}
            core.agent_sessions = {"still-paused": {"process_id": AGENT_PROCESS_ID, "is_stopped": True},
                                   "fg-typed-by-hand": {"process_id": 200, "is_stopped": False}}
            with unittest.mock.patch.object(iterm2_connection_thread, "pause_has_ended",
                                            side_effect=lambda pause_record: pause_record is ended), \
                    unittest.mock.patch.object(iterm2_connection_thread, "continue_processes_stopped_at_pause") as continue_processes:
                core.continue_the_processes_of_agents_no_longer_paused()
            self.assertEqual(continue_processes.call_args_list, [unittest.mock.call(ended.stopped_processes)])
            self.assertEqual(load_pause_records(records_file_path), {("still-paused", AGENT_PROCESS_ID): still_paused})  # what a restart finds
            self.assertEqual([agent_session["is_paused_with_the_processes_below_it"]
                              for agent_session in core.agent_sessions.values()], [True, False])

    def test_what_ended_pauses_stopped_is_continued_before_connecting_to_iterm2(self):
        # iTerm2 quit: its paused agents are gone, and no connection can be made.
        core = DashboardCore(lambda snapshot: None, lambda message: None,
                             pause_record_by_tab_and_agent_to_start_with={("s1", AGENT_PROCESS_ID): make_pause_record()})
        with unittest.mock.patch.object(iterm2_connection_thread, "pause_has_ended", return_value=True), \
                unittest.mock.patch.object(iterm2_connection_thread, "continue_processes_stopped_at_pause") as continue_processes, \
                unittest.mock.patch.object(iterm2.Connection, "async_create", side_effect=OSError("iTerm2 isn't running")):
            asyncio.run(core.run())
        self.assertEqual(continue_processes.call_args_list, [unittest.mock.call(make_pause_record().stopped_processes)])
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
        with unittest.mock.patch.object(iterm2_connection_thread, "pause_has_ended", return_value=False), \
                unittest.mock.patch.object(iterm2_connection_thread, "read_process_current_folder", return_value="/x"):
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
        core = self.make_core_with_one_agent_tab("claude")
        core.agent_sessions["s1"]["process_id"] = 200  # started at the shell after the agent 100 was paused
        core.pause_record_by_tab_and_agent = {("s1", AGENT_PROCESS_ID): make_pause_record()}
        with unittest.mock.patch.object(iterm2_connection_thread, "pause_has_ended", return_value=False), \
                unittest.mock.patch.object(iterm2_connection_thread, "read_process_current_folder", return_value="/x"):
            core.list_again_the_paused_agents_that_lost_their_row()
            self.assertEqual(core.agent_sessions["s1"]["process_id"], 200)  # the newer agent keeps the row while it runs
            del core.agent_sessions["s1"]  # what resolve_agent_sessions leaves once it has ended
            core.list_again_the_paused_agents_that_lost_their_row()
        self.assertEqual(core.agent_sessions["s1"]["process_id"], AGENT_PROCESS_ID)

    def make_core_with_one_agent_tab(self, command_line_in_front):
        core = DashboardCore(lambda snapshot: None, lambda message: None)
        core.app = unittest.mock.Mock(get_session_by_id=lambda session_id: FakeTab([PROMPT]))
        core.agent_sessions = {"s1": {"process_id": AGENT_PROCESS_ID, "is_in_background": command_line_in_front != "claude",
                                      "is_stopped": False, "agent_tool_name": TOOL}}
        core.gathered_windows = [{"tabs": [{"first_session": {"session_id": "s1", "command_line": command_line_in_front}}]}]
        return core

    def test_pause_types_ctrl_z_only_when_the_agent_or_its_lagging_shell_is_in_front(self):
        for command_line_in_front, is_paused in (("claude", True), ("-zsh", True),  # iTerm2 still names the shell after fg
                                                 ("vim /tmp/prompt.md", False)):  # Claude Code's Ctrl+G editor
            core = self.make_core_with_one_agent_tab(command_line_in_front)

            async def pause_stand_in(session, agent_process_id, agent_tool_name):
                return ActionResult(True), make_pause_record()
            with unittest.mock.patch.object(iterm2_connection_thread, "process_is_stopped", return_value=False), \
                    unittest.mock.patch.object(iterm2_connection_thread, "process_has_its_terminal", return_value=True), \
                    unittest.mock.patch.object(iterm2_connection_thread, "pause_agent_session", side_effect=pause_stand_in):
                action_result = asyncio.run(core.handle_pause_agent_sessions(ActionRequest("pause_agent_sessions",
                                                                                           {"session_ids": ["s1"]})))
            self.assertEqual((action_result.succeeded, ("s1", AGENT_PROCESS_ID) in core.pause_record_by_tab_and_agent),
                             (is_paused, is_paused), command_line_in_front)

    def test_a_newer_agent_in_a_tab_whose_older_agent_is_paused_gets_its_own_pause(self):
        core = self.make_core_with_one_agent_tab("claude")
        core.agent_sessions["s1"]["process_id"] = 200  # started at the shell after the agent 100 was paused
        older_pause_record = make_pause_record()
        core.pause_record_by_tab_and_agent = {("s1", AGENT_PROCESS_ID): older_pause_record}
        newer_pause_record = make_pause_record()._replace(agent_process=(200, STARTED_AT))

        async def pause_stand_in(session, agent_process_id, agent_tool_name):
            return ActionResult(True), newer_pause_record
        with unittest.mock.patch.object(iterm2_connection_thread, "process_is_stopped", return_value=False), \
                unittest.mock.patch.object(iterm2_connection_thread, "process_has_its_terminal", return_value=True), \
                unittest.mock.patch.object(iterm2_connection_thread, "pause_agent_session", side_effect=pause_stand_in) as pause:
            asyncio.run(core.handle_pause_agent_sessions(ActionRequest("pause_agent_sessions", {"session_ids": ["s1"]})))
        self.assertEqual(pause.call_count, 1)
        self.assertEqual(core.pause_record_by_tab_and_agent,
                         {("s1", AGENT_PROCESS_ID): older_pause_record, ("s1", 200): newer_pause_record})

    def test_a_second_resume_while_the_first_is_under_way_does_nothing(self):
        core = self.make_core_with_one_agent_tab("-zsh")
        resume_calls = []

        async def resume_stand_in(session, agent_process_id, pause_record):
            resume_calls.append(agent_process_id)
            await asyncio.sleep(0.05)  # reading the screen, typing fg
            return ActionResult(True)

        async def click_twice():
            request = ActionRequest("resume_agent_sessions", {"session_ids": ["s1"]})
            return await asyncio.gather(core.handle_resume_agent_sessions(request), core.handle_resume_agent_sessions(request))
        with unittest.mock.patch.object(iterm2_connection_thread, "resume_agent_session", side_effect=resume_stand_in):
            action_results = asyncio.run(click_twice())
        self.assertEqual((resume_calls, [action_result.succeeded for action_result in action_results]),
                         ([AGENT_PROCESS_ID], [True, True]))

    def test_a_long_prompt_line_wrapped_over_two_rows_is_read_whole(self):
        contents = FakeScreenContents(["mac:~/a/very/long/folder/na", "me $ \x00\x00"], cursor_x=5)
        contents.lines[0].hard_eol = False
        tab = FakeTab([])
        tab.async_get_screen_contents = lambda: asyncio.sleep(0, contents)
        self.assertEqual(asyncio.run(agent_session_pausing.read_text_around_the_cursor(tab)),
                         ("mac:~/a/very/long/folder/name $ ", ""))


if __name__ == "__main__":
    unittest.main()
