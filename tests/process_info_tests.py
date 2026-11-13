"""A sub row's Info and Kill: the core reads and ends real processes
this test starts, only those a tab row lists, and the Info window shows what it read; and a tab
row's Session info, read from a real process standing in for its agent."""

import asyncio
import os
import signal
import subprocess
import tempfile
import threading
import time
import unittest
import unittest.mock

from AppKit import NSApplication

from agent_command_tab import dashboard_core
from agent_command_tab.agent_process_tree import list_descendant_processes, process_is_alive, read_process_details
from agent_command_tab.dashboard_core_api import ActionRequest
from agent_command_tab.dashboard_core import DashboardCore
from agent_command_tab.snapshot_data_classes import ChildProcessEntry
from agent_command_tab.user_interface.process_and_session_info_alerts import (
    LARGEST_TEXT_HEIGHT, LONGEST_COMMAND_IN_AN_ALERT, ROOM_ABOVE_THE_BUTTONS, build_process_info_alert, build_session_info_alert,
    format_elapsed_time_in_words, list_process_info_lines, list_session_info_lines, shorten_command_for_alert)
from codex_sandbox_detection import REASON_FOR_SKIPPING_WINDOW_TESTS_IN_CODEX_SANDBOX, RUNNING_IN_CODEX_SANDBOX
from condition_waiting import wait_until
from gathered_window_builders import make_agent_session, make_agent_session_entry

def start_claude_code_style_shell(folder, agent_command="sleep 60"):
    """Starts bash as Claude Code's shell tool does, running agent_command, which ends by running
    sleep, in folder, and waits until sleep runs below it; returns the bash process, reaped as soon
    as it ends, as an agent reaps its shells.

    Waiting for any process below bash wasn't enough: bash's copy of itself, which becomes sleep a
    moment later, still had bash's command line when a slow GitHub runner read it. So this waits
    for a process below it that isn't bash, which is sleep under whatever name it has (GNU
    coreutils' sleep, when it is first on PATH, runs as "gsleep").

    In a process group of its own, so kill_the_shell_and_what_runs_in_it ends its sleep too."""
    shell = subprocess.Popen(["/bin/bash", "-c", f"true && eval '{agent_command}' < /dev/null && pwd -P >| {folder}/cwd"],
                             cwd=folder, start_new_session=True)
    threading.Thread(target=shell.wait, daemon=True).start()
    if not wait_until(lambda: any(process.name != "bash" for process in list_descendant_processes(shell.pid))):
        raise AssertionError("the shell's command didn't start")
    return shell


def kill_the_shell_and_what_runs_in_it(shell):
    """Kills a shell start_claude_code_style_shell started and the sleep running in it, which
    killing the shell alone leaves running."""
    try:
        os.killpg(shell.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def make_core_listing(session_id, process_id):
    """Returns a core whose session_id tab row lists the process as a sub row."""
    core = DashboardCore(lambda snapshot: None, lambda message: None)
    core.session_readings_by_session_id = {session_id: {"child_processes": (
        ChildProcessEntry(process_id, "sleep 60", time.time(), True, None, None),)}}
    return core


class ProcessDetailsTests(unittest.TestCase):
    def test_the_details_of_a_claude_code_shell_name_the_agents_command_and_the_shells_command_line(self):
        with tempfile.TemporaryDirectory() as folder:
            shell = start_claude_code_style_shell(folder)
            try:
                details = read_process_details(shell.pid)
            finally:
                kill_the_shell_and_what_runs_in_it(shell)
        self.assertEqual(details["agent_command"], "sleep 60")
        self.assertTrue(details["command_line"].startswith("/bin/bash -c true && eval 'sleep 60'"))
        self.assertEqual(details["working_folder"], os.path.realpath(folder))
        self.assertEqual([subprocess_details["command_text"] for subprocess_details in details["subprocesses"]], ["sleep 60"])
        self.assertGreater(details["memory_bytes"], 0)
        self.assertLess(abs(details["started_at"] - time.time()), 10)

    def test_a_process_that_is_gone_has_no_details(self):
        process = subprocess.Popen(["/usr/bin/true"])
        process.wait()
        self.assertIsNone(read_process_details(process.pid))

    def test_the_core_reads_the_details_of_a_listed_process_only(self):
        with tempfile.TemporaryDirectory() as folder:
            shell = start_claude_code_style_shell(folder)
            try:
                core = make_core_listing("s1", shell.pid)
                action_result = asyncio.run(core.handle_read_child_process_details(
                    ActionRequest("read_child_process_details", {"session_id": "s1"}, {"process_id": shell.pid})))
                unlisted_result = asyncio.run(core.handle_read_child_process_details(
                    ActionRequest("read_child_process_details", {"session_id": "s2"}, {"process_id": shell.pid})))
            finally:
                kill_the_shell_and_what_runs_in_it(shell)
        self.assertTrue(action_result.succeeded)
        self.assertEqual(action_result.details["cpu_percent"], 0)  # sleeping, and rounded as the row's is
        self.assertEqual(action_result.details["memory_bytes"] % 1024 ** 2, 0)
        self.assertEqual((unlisted_result.succeeded, unlisted_result.message),
                         (False, "that process is no longer listed under its tab"))

    def test_the_core_reads_the_details_of_a_tabs_agent_session(self):
        with tempfile.TemporaryDirectory() as folder:
            shell = start_claude_code_style_shell(folder)  # stands in for the agent's process
            try:
                core = DashboardCore(lambda snapshot: None, lambda message: None)
                core.agent_sessions = {"s1": make_agent_session(shell.pid)}
                core.claude_agents_by_process_id = {shell.pid: {"name": "planning-3a", "sessionId": "c1",
                                                                "config_folder": "/Users/someone/.claude_work"}}
                # Its conversation file as the core last found it, so the test doesn't search the real config folders.
                core.conversation_files_by_session_id = {"s1": dashboard_core.build_conversation_file_record(
                    (shell.pid, "c1"), "/Users/someone/.claude_work/projects/x/c1.jsonl", time.time(), None)}
                core.gathered_windows = [{"window_id": "w1", "terminal_name": "tmux", "tabs": [
                    {"tab_id": "t1", "first_session": {"session_id": "s1"}}]}]
                action_result = asyncio.run(core.handle_read_agent_session_details(
                    ActionRequest("read_agent_session_details", {"session_id": "s1"})))
                no_agent_result = asyncio.run(core.handle_read_agent_session_details(
                    ActionRequest("read_agent_session_details", {"session_id": "s2"})))
            finally:
                kill_the_shell_and_what_runs_in_it(shell)
        details = action_result.details
        self.assertEqual((details["process_id"], details["tab_kind_name"], details["conversation_id"], details["conversation_file"],
                          details["claude_config_folder"], details["codex_home_folder"]),
                         (shell.pid, "tmux window", "c1", "/Users/someone/.claude_work/projects/x/c1.jsonl",
                          "/Users/someone/.claude_work", None))
        self.assertEqual(details["cpu_percent"], 0)  # sleeping, and rounded as the row's is
        self.assertTrue(details["command_line"].startswith("/bin/bash -c "))
        self.assertEqual(no_agent_result.message, "that tab no longer runs an agent")


class KillProcessTests(unittest.TestCase):
    def test_kill_ends_the_shell_and_the_command_running_in_it(self):
        with tempfile.TemporaryDirectory() as folder:
            shell = start_claude_code_style_shell(folder)
            command_process_id = list_descendant_processes(shell.pid)[0].process_id
            core = make_core_listing("s1", shell.pid)
            started_at = time.time()
            action_result = asyncio.run(core.handle_end_child_process(
                ActionRequest("end_child_process", {"session_id": "s1"}, {"process_id": shell.pid})))
            self.assertTrue(wait_until(lambda: shell.returncode is not None))
        self.assertTrue(action_result.succeeded)
        self.assertEqual(shell.returncode, -signal.SIGTERM)  # SIGTERM was enough: no SIGKILL
        self.assertLess(time.time() - started_at, dashboard_core.SECONDS_AN_AGENT_GETS_TO_END_BEFORE_IT_IS_KILLED)
        self.assertTrue(wait_until(lambda: not process_is_alive(command_process_id)))  # launchd reaps the orphaned sleep

    def test_what_ignores_sigterm_gets_sigkill(self):
        with tempfile.TemporaryDirectory() as folder:
            shell = start_claude_code_style_shell(folder, agent_command="trap \"\" TERM; sleep 60")
            core = make_core_listing("s1", shell.pid)
            with unittest.mock.patch.object(dashboard_core, "SECONDS_AN_AGENT_GETS_TO_END_BEFORE_IT_IS_KILLED", 0.5):
                asyncio.run(core.handle_end_child_process(
                    ActionRequest("end_child_process", {"session_id": "s1"}, {"process_id": shell.pid})))
            self.assertTrue(wait_until(lambda: shell.returncode is not None))
        self.assertEqual(shell.returncode, -signal.SIGKILL)

    def test_kill_leaves_a_process_its_tab_row_doesnt_list(self):
        with tempfile.TemporaryDirectory() as folder:
            shell = start_claude_code_style_shell(folder)
            try:
                action_result = asyncio.run(make_core_listing("s1", shell.pid + 1).handle_end_child_process(
                    ActionRequest("end_child_process", {"session_id": "s1"}, {"process_id": shell.pid})))
                self.assertIsNone(shell.poll())
            finally:
                kill_the_shell_and_what_runs_in_it(shell)
        self.assertFalse(action_result.succeeded)


@unittest.skipIf(RUNNING_IN_CODEX_SANDBOX, REASON_FOR_SKIPPING_WINDOW_TESTS_IN_CODEX_SANDBOX)
class ProcessInfoWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        NSApplication.sharedApplication()

    def make_details(self, **changes):
        details = {"process_id": 61167, "command_line": "/bin/bash -c true && eval 'cat <<EOF\nline two\nEOF'",
                   "agent_command": "cat <<EOF\nline two\nEOF", "working_folder": "/Users/someone/code",
                   "started_at": time.mktime((2026, 10, 29, 7, 14, 47, 0, 0, -1)),
                   "subprocesses": [{"process_id": 61170, "command_text": "python3 check.py"},
                                    {"process_id": 61171, "command_text": "sleep 600"}],
                   "memory_bytes": 3 * 1024 ** 2, "cpu_seconds": 75.0, "cpu_percent": 12}
        return dict(details, **changes)

    def test_the_lines_say_what_the_process_is_its_subprocesses_and_what_they_use_in_all(self):
        details = self.make_details()
        self.assertEqual(list_process_info_lines(details, details["started_at"] + 19 * 60), [
            ("Process ID", "61167"),
            ("Started", "Oct 29, 7:14:47 AM (19 minutes ago)"),
            ("Working directory", "/Users/someone/code"),
            ("Subprocesses", "python3 check.py (61170)\nsleep 600 (61171)"),
            ("CPU", "12% (total, including subprocesses)"),
            ("CPU time", "1 minute (total, including subprocesses)"),
            ("Memory", "3M (total, including subprocesses)"),
            ("Command", "cat <<EOF\nline two\nEOF"),
            ("Full command line", "/bin/bash -c true && eval 'cat <<EOF\nline two\nEOF'")])
        details = self.make_details(agent_command=None, command_line="clangd --background-index", subprocesses=[],
                                    working_folder="")
        lines = dict(list_process_info_lines(details, details["started_at"]))
        self.assertNotIn("Command", lines)  # not a Claude Code shell: the command line is the command
        self.assertNotIn("Subprocesses", lines)
        self.assertEqual((lines["CPU"], lines["Working directory"]), ("12%", "unknown"))

    def test_durations_spell_out_their_units(self):
        self.assertEqual([format_elapsed_time_in_words(seconds) for seconds in (-3, 1, 45, 60, 185, 3600, 3900, 7260, 86400, 3 * 86400 + 600)],
                         ["0 seconds", "1 second", "45 seconds", "1 minute", "3 minutes", "1 hour", "1 hour 5 minutes",
                          "2 hours 1 minute", "1 day", "3 days"])

    def test_the_window_shows_the_lines_in_text_that_can_be_selected_but_not_edited_with_room_above_ok(self):
        details = self.make_details()
        alert = build_process_info_alert(details, details["started_at"])
        scroll_view, = alert.accessoryView().subviews()
        text_view = scroll_view.documentView()
        self.assertEqual(alert.messageText(), "Process info")
        self.assertFalse(text_view.isEditable())
        self.assertTrue(text_view.isSelectable())
        self.assertIn("Full command line\t/bin/bash -c true && eval 'cat <<EOF\nline two\nEOF'", text_view.string())
        self.assertEqual(scroll_view.frame().origin.y, ROOM_ABOVE_THE_BUTTONS)
        self.assertEqual(alert.accessoryView().frame().size.height, scroll_view.frame().size.height + ROOM_ABOVE_THE_BUTTONS)
        self.assertLessEqual(scroll_view.frame().size.height, LARGEST_TEXT_HEIGHT)
        long_details = self.make_details(command_line="x " * 20000)
        self.assertEqual(build_process_info_alert(long_details, details["started_at"]).accessoryView().frame().size.height,
                         LARGEST_TEXT_HEIGHT + ROOM_ABOVE_THE_BUTTONS)  # a longer one scrolls

    def make_session_details(self, **changes):
        details = dict(self.make_details(
            process_id=29769, command_line="claude --model opus", agent_command=None,
            subprocesses=[{"process_id": 29800, "command_text": "node mcp-server.js"}]),
            tab_kind_name="iTerm2 tab",
            conversation_id="2aa37546-5264-4b90-a553-5c668c422d40",
            conversation_file="/Users/someone/.claude/projects/x/2aa37546-5264-4b90-a553-5c668c422d40.jsonl",
            claude_config_folder=None, codex_home_folder=None)
        return dict(details, **changes)

    def test_the_session_info_lines_start_with_the_name_and_process_id_then_the_age(self):
        details = self.make_session_details()
        entry = make_agent_session_entry("s1", "agent-command-tab", agent_session_name="agent-command-tab-f7",
                                         working_folder="/Users/someone/code", context_tokens=134000, context_fill_percent=13)
        self.assertEqual(list_session_info_lines(entry, details, details["started_at"] + 3 * 86400 + 600), [
            ("Session", "agent-command-tab-f7 (process ID 29769)"),
            ("Age", "3 days (started Oct 29, 7:14:47 AM)"),
            ("Agent tool", "Claude Code"),
            ("Tab", 'iTerm2 tab "agent-command-tab"'),
            ("Working directory", "/Users/someone/code"),
            ("Conversation ID", "2aa37546-5264-4b90-a553-5c668c422d40"),
            ("Conversation file", "/Users/someone/.claude/projects/x/2aa37546-5264-4b90-a553-5c668c422d40.jsonl"),
            ("Context", "134k tokens (13% of the context window)"),
            ("CPU", "12% (total, including subprocesses)"),
            ("CPU time", "1 minute (total, including subprocesses)"),
            ("Memory", "3M (total, including subprocesses)"),
            ("Command line", "claude --model opus")])
        # A Codex session: no name, its CODEX_HOME, no conversation yet, and no context size or subprocesses.
        details = self.make_session_details(conversation_id=None, conversation_file=None,
                                            codex_home_folder="/Users/someone/.codex", subprocesses=[])
        lines = dict(list_session_info_lines(make_agent_session_entry("s3", "codex", agent_tool_name="Codex"), details,
                                             details["started_at"]))
        self.assertEqual((lines["Session"], lines["Conversation ID"], lines["Conversation file"], lines["CODEX_HOME"], lines["CPU"]),
                         ("process ID 29769", "unknown", "unknown", "/Users/someone/.codex", "12%"))
        self.assertNotIn("Context", lines)
        self.assertNotIn("Claude Code config folder", lines)
        lines = dict(list_session_info_lines(make_agent_session_entry("s1", "work"),
                                             self.make_session_details(claude_config_folder="/Users/someone/.claude_work"), 0))
        self.assertEqual(lines["Claude Code config folder"], "/Users/someone/.claude_work")

    def test_the_session_info_window_shows_its_lines_in_text_that_can_be_selected_but_not_edited(self):
        details = self.make_session_details()
        alert = build_session_info_alert(make_agent_session_entry("s1", "agent-command-tab", agent_session_name="agent-command-tab-f7"),
                                         details, details["started_at"])
        scroll_view, = alert.accessoryView().subviews()
        text_view = scroll_view.documentView()
        self.assertEqual(alert.messageText(), "Session info")
        self.assertFalse(text_view.isEditable())
        self.assertTrue(text_view.isSelectable())
        self.assertTrue(text_view.string().startswith("Session\tagent-command-tab-f7 (process ID 29769)\nAge\t0 seconds (started "))

    def test_a_command_named_in_the_kill_question_is_one_line_and_cut_when_long(self):
        self.assertEqual(shorten_command_for_alert("sleep 60"), "sleep 60")
        self.assertEqual(shorten_command_for_alert("cat <<EOF\n  two\nEOF"), "cat <<EOF two EOF")
        long_command = shorten_command_for_alert("x" * 500)
        self.assertEqual((len(long_command), long_command[-1]), (LONGEST_COMMAND_IN_AN_ALERT, "…"))


if __name__ == "__main__":
    unittest.main()
