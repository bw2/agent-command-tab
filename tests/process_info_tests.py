"""A sub row's Info and Kill (user request, 2026-10-29): the core reads and ends real processes
this test starts, only those a tab row lists, and the Info window shows what it read."""

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

from agent_command_tab import iterm2_connection_thread
from agent_command_tab.agent_process_tree import list_descendant_processes, process_is_alive, read_process_details
from agent_command_tab.dashboard_core_api import ActionRequest
from agent_command_tab.iterm2_connection_thread import DashboardCore
from agent_command_tab.snapshot_data_classes import ChildProcessEntry
from agent_command_tab.user_interface.process_info_alert import (LARGEST_ACCESSORY_HEIGHT, LONGEST_COMMAND_IN_AN_ALERT,
                                                                 build_process_info_alert, list_process_info_lines,
                                                                 shorten_command_for_alert)

SECONDS_TO_WAIT_FOR_A_PROCESS = 5


def wait_until(condition):
    deadline = time.time() + SECONDS_TO_WAIT_FOR_A_PROCESS
    while not condition():
        if time.time() > deadline:
            raise AssertionError("timed out")
        time.sleep(0.05)


def start_claude_code_style_shell(folder, agent_command="sleep 60"):
    """Starts bash as Claude Code's shell tool does, running agent_command in folder, and waits
    until the command runs below it; returns the bash process, reaped as soon as it ends, as an
    agent reaps its shells."""
    shell = subprocess.Popen(["/bin/bash", "-c", f"true && eval '{agent_command}' < /dev/null && pwd -P >| {folder}/cwd"],
                             cwd=folder)
    threading.Thread(target=shell.wait, daemon=True).start()
    wait_until(lambda: list_descendant_processes(shell.pid))
    return shell


def make_core_listing(session_id, process_id):
    """Returns a core whose session_id tab row lists the process as a sub row."""
    core = DashboardCore(lambda snapshot: None, lambda message: None)
    core.screen_readings_by_session_id = {session_id: {"child_processes": (
        ChildProcessEntry(process_id, "sleep 60", time.time(), True, None, None),)}}
    return core


class ProcessDetailsTests(unittest.TestCase):
    def test_the_details_of_a_claude_code_shell_name_the_agents_command_and_the_shells_command_line(self):
        with tempfile.TemporaryDirectory() as folder:
            shell = start_claude_code_style_shell(folder)
            try:
                details = read_process_details(shell.pid)
            finally:
                shell.kill()
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
                shell.kill()
        self.assertTrue(action_result.succeeded)
        self.assertEqual(action_result.details["cpu_percent"], 0)  # sleeping, and rounded as the row's is
        self.assertEqual(action_result.details["memory_bytes"] % 1024 ** 2, 0)
        self.assertEqual((unlisted_result.succeeded, unlisted_result.message),
                         (False, "that process is no longer listed under its tab"))


class KillProcessTests(unittest.TestCase):
    def test_kill_ends_the_shell_and_the_command_running_in_it(self):
        with tempfile.TemporaryDirectory() as folder:
            shell = start_claude_code_style_shell(folder)
            command_process_id = list_descendant_processes(shell.pid)[0].process_id
            core = make_core_listing("s1", shell.pid)
            started_at = time.time()
            action_result = asyncio.run(core.handle_end_child_process(
                ActionRequest("end_child_process", {"session_id": "s1"}, {"process_id": shell.pid})))
            wait_until(lambda: shell.returncode is not None)
        self.assertTrue(action_result.succeeded)
        self.assertEqual(shell.returncode, -signal.SIGTERM)  # SIGTERM was enough: no SIGKILL
        self.assertLess(time.time() - started_at, iterm2_connection_thread.SECONDS_AN_AGENT_GETS_TO_END_BEFORE_IT_IS_KILLED)
        wait_until(lambda: not process_is_alive(command_process_id))  # launchd reaps the orphaned sleep

    def test_what_ignores_sigterm_gets_sigkill(self):
        with tempfile.TemporaryDirectory() as folder:
            shell = start_claude_code_style_shell(folder, agent_command="trap \"\" TERM; sleep 60")
            core = make_core_listing("s1", shell.pid)
            with unittest.mock.patch.object(iterm2_connection_thread, "SECONDS_AN_AGENT_GETS_TO_END_BEFORE_IT_IS_KILLED", 0.5):
                asyncio.run(core.handle_end_child_process(
                    ActionRequest("end_child_process", {"session_id": "s1"}, {"process_id": shell.pid})))
            wait_until(lambda: shell.returncode is not None)
        self.assertEqual(shell.returncode, -signal.SIGKILL)

    def test_kill_leaves_a_process_its_tab_row_doesnt_list(self):
        with tempfile.TemporaryDirectory() as folder:
            shell = start_claude_code_style_shell(folder)
            try:
                action_result = asyncio.run(make_core_listing("s1", shell.pid + 1).handle_end_child_process(
                    ActionRequest("end_child_process", {"session_id": "s1"}, {"process_id": shell.pid})))
                self.assertIsNone(shell.poll())
            finally:
                shell.kill()
        self.assertFalse(action_result.succeeded)


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
            ("Started", "Oct 29, 7:14:47 AM (19m ago)"),
            ("Working directory", "/Users/someone/code"),
            ("Subprocesses", "python3 check.py (61170)\nsleep 600 (61171)"),
            ("CPU", "12% (total, including subprocesses)"),
            ("CPU time", "1m (total, including subprocesses)"),
            ("Memory", "3M (total, including subprocesses)"),
            ("Command", "cat <<EOF\nline two\nEOF"),
            ("Full command line", "/bin/bash -c true && eval 'cat <<EOF\nline two\nEOF'")])
        details = self.make_details(agent_command=None, command_line="clangd --background-index", subprocesses=[],
                                    working_folder="")
        lines = dict(list_process_info_lines(details, details["started_at"]))
        self.assertNotIn("Command", lines)  # not a Claude Code shell: the command line is the command
        self.assertNotIn("Subprocesses", lines)
        self.assertEqual((lines["CPU"], lines["Working directory"]), ("12%", "unknown"))

    def test_the_window_shows_the_lines_in_text_that_can_be_selected_but_not_edited(self):
        details = self.make_details()
        alert = build_process_info_alert(details, details["started_at"])
        text_view = alert.accessoryView().documentView()
        self.assertEqual(alert.messageText(), "Process info")
        self.assertFalse(text_view.isEditable())
        self.assertTrue(text_view.isSelectable())
        self.assertIn("Full command line\t/bin/bash -c true && eval 'cat <<EOF\nline two\nEOF'", text_view.string())
        self.assertLessEqual(alert.accessoryView().frame().size.height, LARGEST_ACCESSORY_HEIGHT)
        long_details = self.make_details(command_line="x " * 20000)
        self.assertEqual(build_process_info_alert(long_details, details["started_at"]).accessoryView().frame().size.height,
                         LARGEST_ACCESSORY_HEIGHT)  # a longer one scrolls

    def test_a_command_named_in_the_kill_question_is_one_line_and_cut_when_long(self):
        self.assertEqual(shorten_command_for_alert("sleep 60"), "sleep 60")
        self.assertEqual(shorten_command_for_alert("cat <<EOF\n  two\nEOF"), "cat <<EOF two EOF")
        long_command = shorten_command_for_alert("x" * 500)
        self.assertEqual((len(long_command), long_command[-1]), (LONGEST_COMMAND_IN_AN_ALERT, "…"))


if __name__ == "__main__":
    unittest.main()
