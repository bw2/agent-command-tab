import contextlib
import functools
import io
import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import unittest.mock

from agent_command_tab import __version__, command_line, hooks_installer, start_with_iterm2
from agent_command_tab.command_line import (SECONDS_TO_WAIT_FOR_THE_APP_AFTER_CTRL_C, choose_signal_that_stops_the_copy, main,
                                            stop_the_running_copy, take_the_running_lock, the_running_lock_is_held,
                                            wait_for_app_process)
from agent_command_tab.hooks_installer import HOOKS_ARE_INSTALLED, HOOKS_ARE_MISSING, check_hooks, install_hooks
from agent_command_tab.settings_store import load_settings
from agent_command_tab.snapshot_data_classes import ComputerSnapshot


class VersionOptionTests(unittest.TestCase):
    def test_version_prints_the_package_version_and_exits(self):
        printed = io.StringIO()
        with contextlib.redirect_stdout(printed), self.assertRaises(SystemExit) as raised:
            main(["--version"])
        self.assertEqual(raised.exception.code, 0)
        self.assertEqual(printed.getvalue(), f"agent-command-tab {__version__}\n")


class DumpSnapshotTests(unittest.TestCase):
    def dump_snapshot_with_a_stand_in_core(self, settings_file_path, seconds_to_wait_for_agent_names,
                                           reason_the_core_stops_right_after_its_first_snapshot=None):
        """Runs dump-snapshot with a core that publishes one snapshot without monitors and then,
        given a reason, stops for it a moment later, while dump-snapshot waits for the agents' names.

        Returns:
            (the exit status, what it printed on stdout, what it printed on stderr).
        """
        def make_stand_in_core_thread(publish_snapshot, report_connection_lost, verbose, **keyword_arguments):
            def start():
                publish_snapshot(ComputerSnapshot("computer", "Mac", 0, []))
                if reason_the_core_stops_right_after_its_first_snapshot is not None:
                    threading.Timer(0.05, report_connection_lost, [reason_the_core_stops_right_after_its_first_snapshot]).start()
            stand_in_core_thread = unittest.mock.Mock(start=start)
            stand_in_core_thread.core.terminal_backends = []
            return stand_in_core_thread
        printed, printed_errors = io.StringIO(), io.StringIO()
        # Neither the app's own files nor the screens are read.
        with unittest.mock.patch("agent_command_tab.dashboard_core.DashboardCoreThread", make_stand_in_core_thread), \
                unittest.mock.patch("agent_command_tab.settings_store.load_settings",
                                    functools.partial(load_settings, settings_file_path)), \
                unittest.mock.patch("agent_command_tab.pause_records_store.load_pause_records", return_value={}), \
                unittest.mock.patch("agent_command_tab.hook_states_store.load_hook_states", return_value={}), \
                unittest.mock.patch("agent_command_tab.codex_processes_that_shared_their_folder_store"
                                    ".load_codex_processes_that_shared_their_folder", return_value={}), \
                unittest.mock.patch("agent_command_tab.user_interface.screen_layout.read_monitors_from_screens", return_value=[]), \
                unittest.mock.patch.object(command_line, "SECONDS_TO_WAIT_FOR_AGENT_NAMES", seconds_to_wait_for_agent_names), \
                contextlib.redirect_stdout(printed), contextlib.redirect_stderr(printed_errors):
            exit_status = main(["dump-snapshot"])
        return exit_status, printed.getvalue(), printed_errors.getvalue()

    def test_stdout_holds_only_the_json_when_the_settings_file_cant_be_read(self):
        with tempfile.TemporaryDirectory() as folder:
            settings_file_path = os.path.join(folder, "settings.json")
            with open(settings_file_path, "w") as settings_file:
                settings_file.write("{not json")
            exit_status, printed, printed_errors = self.dump_snapshot_with_a_stand_in_core(settings_file_path, 0.01)
        self.assertEqual(exit_status, 0)
        self.assertEqual(json.loads(printed)["computer_name"], "Mac")
        self.assertIn(f"settings file {settings_file_path} could not be read", printed_errors)

    def test_a_core_that_stops_after_its_first_snapshot_gives_its_reason_instead_of_the_snapshot(self):
        # As when iTerm2 quits meanwhile. The wait for the agents' names, far longer here, ends as the core stops.
        started_at = time.time()
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(self.dump_snapshot_with_a_stand_in_core(os.path.join(folder, "settings.json"), 60,
                                                                     "the iTerm2 connection closed"),
                             (1, "", "the iTerm2 connection closed\n"))
        self.assertLess(time.time() - started_at, 30)


KILL_THE_SUPERVISOR = "a plain kill of this process"  # a planned outcome: SIGTERM to the test's own process


class FakeAppProcess:
    """Stands in for subprocess.Popen: each wait() returns or raises the next planned outcome."""

    def __init__(self, planned_outcomes):
        self.planned_outcomes = list(planned_outcomes)
        self.wait_timeouts = []
        self.was_killed = False
        self.signals_sent = []

    def wait(self, timeout=None):
        self.wait_timeouts.append(timeout)
        outcome = self.planned_outcomes.pop(0)
        if outcome == KILL_THE_SUPERVISOR:
            os.kill(os.getpid(), signal.SIGTERM)
            time.sleep(5)  # the handler interrupts this
            return "the kill didn't interrupt the wait"
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    def kill(self):
        self.was_killed = True

    def send_signal(self, signal_number):
        self.signals_sent.append(signal_number)


class WaitForAppProcessTests(unittest.TestCase):
    def test_returns_the_exit_status_of_an_app_that_exits_by_itself(self):
        process = FakeAppProcess([75])
        self.assertEqual(wait_for_app_process(process), 75)
        self.assertFalse(process.was_killed)

    def test_after_ctrl_c_the_app_gets_time_to_give_the_room_back(self):
        process = FakeAppProcess([KeyboardInterrupt(), 0])
        self.assertEqual(wait_for_app_process(process), 0)
        self.assertEqual(process.wait_timeouts, [None, SECONDS_TO_WAIT_FOR_THE_APP_AFTER_CTRL_C])
        self.assertFalse(process.was_killed)

    def test_an_app_that_hangs_after_ctrl_c_is_killed(self):
        process = FakeAppProcess([KeyboardInterrupt(), subprocess.TimeoutExpired("app", 10), -9])
        self.assertEqual(wait_for_app_process(process), -9)
        self.assertTrue(process.was_killed)

    def test_a_plain_kill_of_the_supervisor_reaches_the_app_as_ctrl_c(self):
        # It ended the supervisor alone, leaving the app running without the lock.
        def fail_instead_of_ending_the_test_run(signal_number, frame):
            raise AssertionError("wait_for_app_process left SIGTERM unhandled")
        handler_before = signal.signal(signal.SIGTERM, fail_instead_of_ending_the_test_run)
        self.addCleanup(signal.signal, signal.SIGTERM, handler_before)
        process = FakeAppProcess([KILL_THE_SUPERVISOR, 0])
        self.assertEqual(wait_for_app_process(process), 0)
        self.assertEqual(process.signals_sent, [signal.SIGINT])
        self.assertEqual(process.wait_timeouts, [None, SECONDS_TO_WAIT_FOR_THE_APP_AFTER_CTRL_C])
        self.assertIs(signal.getsignal(signal.SIGTERM), fail_instead_of_ending_the_test_run)  # put back as it was


class RunningLockTests(unittest.TestCase):  # one copy at a time
    def test_only_one_holder_at_a_time_and_the_file_names_it(self):
        with tempfile.TemporaryDirectory() as folder:
            lock_file_path = os.path.join(folder, "agent-command-tab", "running.lock")  # its folder made if missing
            lock_file = take_the_running_lock(lock_file_path)
            self.assertIsNotNone(lock_file)
            self.addCleanup(lock_file.close)  # closing it twice is harmless
            with open(lock_file_path) as written_lock_file:
                self.assertEqual(written_lock_file.read(), f"{os.getpid()}\n")
            self.assertIsNone(take_the_running_lock(lock_file_path))  # another open of the file, as another process's
            lock_file.close()  # as when the holder ends
            lock_file_taken_again = take_the_running_lock(lock_file_path)
            self.assertIsNotNone(lock_file_taken_again)
            lock_file_taken_again.close()

    def test_a_second_copy_exits_at_once_without_starting_the_app(self):
        with tempfile.TemporaryDirectory() as folder:
            lock_file_path = os.path.join(folder, "running.lock")
            with open(lock_file_path, "w") as lock_file:
                lock_file.write("4242\n")
            printed = io.StringIO()
            steps = []
            with unittest.mock.patch("agent_command_tab.settings_store.move_the_settings_folder_of_the_old_name",
                                     side_effect=lambda: steps.append("move the old-name settings folder")), \
                    unittest.mock.patch.object(command_line, "take_the_running_lock",
                                               side_effect=lambda: steps.append("take the lock")), \
                    unittest.mock.patch.object(command_line, "RUNNING_LOCK_FILE_PATH", lock_file_path), \
                    unittest.mock.patch.object(command_line.subprocess, "Popen", side_effect=AssertionError("started the app")), \
                    contextlib.redirect_stdout(printed):
                self.assertEqual(command_line.run_with_restarts(verbose=False), 0)
            self.assertIn("already running (process 4242)", printed.getvalue())
            # The move comes first: the lock file makes the new folder, after which the move never
            # happens.
            self.assertEqual(steps, ["move the old-name settings folder", "take the lock"])

    def test_the_supervisor_hands_the_lock_to_the_app_it_starts(self):
        # So an app left running after its supervisor ended still keeps a second copy out.
        with tempfile.TemporaryFile("a+") as lock_file, \
                unittest.mock.patch.object(command_line, "take_the_running_lock_or_name_the_copy_holding_it",
                                           return_value=lock_file), \
                unittest.mock.patch("agent_command_tab.installed_copy_paths.find_python_of_this_copy",
                                    return_value="/opt/homebrew/opt/agent-command-tab/libexec/bin/python"), \
                unittest.mock.patch(  # not the real login shell, which a bare PATH (launchd's) would ask
                    "agent_command_tab.login_shell_environment.add_the_login_shells_environment_when_started_without_it"), \
                unittest.mock.patch.object(command_line.subprocess, "Popen", return_value=FakeAppProcess([0])) as start_the_app:
            self.assertEqual(command_line.run_with_restarts(verbose=False), 0)
            start_the_app.assert_called_once()
            # By the Python's path that survives `brew upgrade`, not sys.executable.
            self.assertEqual(start_the_app.call_args.args[0][0], "/opt/homebrew/opt/agent-command-tab/libexec/bin/python")
            self.assertEqual(start_the_app.call_args.kwargs["pass_fds"], [lock_file.fileno()])
            self.assertEqual(start_the_app.call_args.kwargs["env"][command_line.RUNNING_LOCK_FILE_DESCRIPTOR_VARIABLE],
                             str(lock_file.fileno()))

    def test_the_app_started_directly_takes_the_lock_itself(self):
        with unittest.mock.patch.dict(os.environ), \
                unittest.mock.patch.object(command_line, "take_the_running_lock_or_name_the_copy_holding_it",
                                           return_value=None) as take_the_lock, \
                unittest.mock.patch.object(command_line, "run_app_process", side_effect=AssertionError("started the app")):
            os.environ.pop(command_line.RUNNING_LOCK_FILE_DESCRIPTOR_VARIABLE, None)
            self.assertEqual(main(["app"]), 0)  # another copy holds it
            take_the_lock.assert_called_once_with()
        # Started by the supervisor, it holds the lock already, and no program it starts gets the file.
        with tempfile.TemporaryFile("a+") as lock_file, unittest.mock.patch.dict(os.environ), \
                unittest.mock.patch.object(command_line, "take_the_running_lock_or_name_the_copy_holding_it") as take_the_lock, \
                unittest.mock.patch.object(command_line, "run_app_process", return_value=0):
            os.set_inheritable(lock_file.fileno(), True)
            os.environ[command_line.RUNNING_LOCK_FILE_DESCRIPTOR_VARIABLE] = str(lock_file.fileno())
            self.assertEqual(main(["app"]), 0)
            take_the_lock.assert_not_called()
            self.assertFalse(os.get_inheritable(lock_file.fileno()))
            self.assertNotIn(command_line.RUNNING_LOCK_FILE_DESCRIPTOR_VARIABLE, os.environ)


# Stands in for the app started with `app`: holds the running lock until Ctrl+C's SIGINT ends it.
STAND_IN_FOR_THE_APP = ("import sys, time\n"
                        "from agent_command_tab.command_line import take_the_running_lock\n"
                        "lock_file = take_the_running_lock(sys.argv[1])\n"
                        "print('locked', flush=True)\n"
                        "time.sleep(60)\n")


@contextlib.contextmanager
def keep_the_cleanup_away_from_the_users_files_and_running_copy(claude_settings_path, codex_hooks_path, autolaunch_folder,
                                                                result_of_stopping_the_running_copy):
    """While open, the hooks are read and written in these two files and the AutoLaunch script is
    looked for in this folder, instead of the user's own, and no running copy is stopped:
    stop_the_running_copy returns result_of_stopping_the_running_copy."""
    with unittest.mock.patch.object(hooks_installer, "list_settings_file_paths", lambda agent_tool_name: {
                "Claude Code": [claude_settings_path], "Codex": [codex_hooks_path]}[agent_tool_name]), \
            unittest.mock.patch.object(start_with_iterm2, "find_iterm2_autolaunch_folder", lambda: autolaunch_folder), \
            unittest.mock.patch.object(command_line, "stop_the_running_copy", lambda: result_of_stopping_the_running_copy):
        yield


class CleanUpBeforeUninstallingTests(unittest.TestCase):
    def test_the_signal_that_stops_a_copy_follows_what_the_process_is(self):
        self.assertEqual(choose_signal_that_stops_the_copy(["/venv/bin/python", "-m", "agent_command_tab.command_line", "app"]),
                         signal.SIGINT)  # the app gives docked windows their room back
        for supervisor_arguments in (["/venv/bin/python", "-m", "agent_command_tab.command_line", "run"],  # started by iTerm2
                                     ["/venv/bin/python", "/venv/bin/agent-command-tab"]):  # started from a shell
            self.assertEqual(choose_signal_that_stops_the_copy(supervisor_arguments), signal.SIGTERM)
        self.assertIsNone(choose_signal_that_stops_the_copy(["-bash"]))  # the lock file's process ID was reused
        self.assertIsNone(choose_signal_that_stops_the_copy([]))  # a process that can't be read

    def test_the_running_copy_is_stopped_and_its_lock_found_free(self):
        with tempfile.TemporaryDirectory() as folder:
            lock_file_path = os.path.join(folder, "running.lock")
            self.assertEqual(stop_the_running_copy(lock_file_path), (True, "AgentCommandTab isn't running"))
            copy = subprocess.Popen([sys.executable, "-c", STAND_IN_FOR_THE_APP, lock_file_path],
                                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
            try:
                self.assertEqual(copy.stdout.readline(), "locked\n")
                self.assertTrue(the_running_lock_is_held(lock_file_path))
                # The stand-in was started as `python -c <its source>`, so it is given the arguments the app is started with.
                with unittest.mock.patch("agent_command_tab.agent_process_tree.read_process_arguments_and_environment", return_value=(
                        ["/venv/bin/python", "-m", "agent_command_tab.command_line", "app"], {})) as read_process_arguments:
                    self.assertEqual(stop_the_running_copy(lock_file_path, seconds_to_wait=10),
                                     (True, f"stopped AgentCommandTab (process {copy.pid})"))
                read_process_arguments.assert_called_once_with(copy.pid)  # the process the lock file names
                self.assertEqual(copy.wait(timeout=5), -signal.SIGINT)  # ended by the signal chosen for the app
            finally:
                copy.kill()
                copy.wait()
                copy.stdout.close()

    def test_the_hooks_and_the_autolaunch_script_are_taken_out(self):
        with tempfile.TemporaryDirectory() as folder:
            claude_settings_path = os.path.join(folder, "settings.json")
            codex_home_folder = os.path.join(folder, "codex")
            autolaunch_folder = os.path.join(folder, "AutoLaunch")
            os.mkdir(codex_home_folder)
            os.mkdir(autolaunch_folder)
            autolaunch_script_path = os.path.join(autolaunch_folder, start_with_iterm2.AUTOLAUNCH_SCRIPT_FILE_NAME)
            open(autolaunch_script_path, "w").close()
            codex_hooks_path = os.path.join(codex_home_folder, "hooks.json")
            with open(codex_hooks_path, "w") as codex_hooks_file:  # a hook an earlier version added to Codex
                json.dump({"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "/venv/bin/python -I -S /f/hook_event_forwarder.py Codex",
                                                          "statusMessage": "Report to AgentCommandTab"}]}]}}, codex_hooks_file)
            printed = io.StringIO()
            with keep_the_cleanup_away_from_the_users_files_and_running_copy(
                        claude_settings_path, codex_hooks_path, autolaunch_folder, (True, "AgentCommandTab isn't running")), \
                    contextlib.redirect_stdout(printed):
                install_hooks("Claude Code")
                self.assertEqual(check_hooks("Claude Code"), HOOKS_ARE_INSTALLED)
                self.assertEqual(main(["clean-up-before-uninstalling"]), 0)
                self.assertEqual(check_hooks("Claude Code"), HOOKS_ARE_MISSING)
            with open(codex_hooks_path) as codex_hooks_file:
                self.assertEqual(json.load(codex_hooks_file), {})
            self.assertIn(f"Codex: removed from {codex_hooks_path}", printed.getvalue())
            self.assertFalse(os.path.exists(autolaunch_script_path))
            self.assertIn(f"removed the script that started AgentCommandTab with iTerm2: {autolaunch_script_path}", printed.getvalue())
            self.assertIn("pipx uninstall agent-command-tab", printed.getvalue())

    def test_uninstalling_isnt_suggested_while_a_copy_still_runs_or_hooks_couldnt_be_removed(self):
        # The uninstall would delete the files that the running copy and the hooks left behind use.
        with tempfile.TemporaryDirectory() as folder:
            codex_hooks_path = os.path.join(folder, "hooks.json")
            for copy_stopped, codex_hooks_text, expected_steps in (
                    (False, "{}", "first quit AgentCommandTab (it still runs), then run"),
                    (True, '{"hooks": ', "first fix the hooks file named above (its hooks couldn't be removed), then run"),  # cut short
                    (False, '{"hooks": ', "first quit AgentCommandTab (it still runs) and fix the hooks file named above")):
                with open(codex_hooks_path, "w") as codex_hooks_file:
                    codex_hooks_file.write(codex_hooks_text)
                printed, printed_errors = io.StringIO(), io.StringIO()
                with keep_the_cleanup_away_from_the_users_files_and_running_copy(
                            os.path.join(folder, "settings.json"), codex_hooks_path, folder,
                            (copy_stopped, "what happened to the running copy")), \
                        contextlib.redirect_stdout(printed), contextlib.redirect_stderr(printed_errors):
                    self.assertEqual(main(["clean-up-before-uninstalling"]), 1)
                self.assertNotIn("uninstall agent-command-tab", printed.getvalue(), expected_steps)
                self.assertIn(expected_steps, printed_errors.getvalue())
                self.assertIn("then run agent-command-tab clean-up-before-uninstalling again", printed_errors.getvalue())


if __name__ == "__main__":
    unittest.main()
