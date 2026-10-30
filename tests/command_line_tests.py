import contextlib
import io
import os
import signal
import subprocess
import tempfile
import time
import unittest
import unittest.mock

from agent_command_tab import __version__, command_line
from agent_command_tab.command_line import (SECONDS_TO_WAIT_FOR_THE_APP_AFTER_CTRL_C, main, take_the_running_lock,
                                            wait_for_app_process)


class VersionOptionTests(unittest.TestCase):
    def test_version_prints_the_package_version_and_exits(self):
        printed = io.StringIO()
        with contextlib.redirect_stdout(printed), self.assertRaises(SystemExit) as raised:
            main(["--version"])
        self.assertEqual(raised.exception.code, 0)
        self.assertEqual(printed.getvalue(), f"agent-command-tab {__version__}\n")


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

    def test_a_plain_kill_of_the_supervisor_reaches_the_app_as_ctrl_c(self):  # code review, 2026-10-30
        # It ended the supervisor alone, leaving the app running without the lock.
        handler_before = signal.getsignal(signal.SIGTERM)
        process = FakeAppProcess([KILL_THE_SUPERVISOR, 0])
        self.assertEqual(wait_for_app_process(process), 0)
        self.assertEqual(process.signals_sent, [signal.SIGINT])
        self.assertEqual(process.wait_timeouts, [None, SECONDS_TO_WAIT_FOR_THE_APP_AFTER_CTRL_C])
        self.assertIs(signal.getsignal(signal.SIGTERM), handler_before)


class RunningLockTests(unittest.TestCase):  # one copy at a time (user request, 2026-10-28)
    def test_only_one_holder_at_a_time_and_the_file_names_it(self):
        with tempfile.TemporaryDirectory() as folder:
            lock_file_path = os.path.join(folder, "agent-command-tab", "running.lock")  # its folder made if missing
            lock_file = take_the_running_lock(lock_file_path)
            self.assertIsNotNone(lock_file)
            with open(lock_file_path) as written_lock_file:
                self.assertEqual(written_lock_file.read(), f"{os.getpid()}\n")
            self.assertIsNone(take_the_running_lock(lock_file_path))  # another open of the file, as another process's
            lock_file.close()  # as when the holder ends
            self.assertIsNotNone(take_the_running_lock(lock_file_path))

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
            # happens (code review, 2026-10-29).
            self.assertEqual(steps, ["move the old-name settings folder", "take the lock"])

    def test_the_supervisor_hands_the_lock_to_the_app_it_starts(self):  # code review, 2026-10-30
        # So an app left running after its supervisor ended still keeps a second copy out.
        popen_keyword_arguments = []
        commands = []

        def start_the_app(command, **keyword_arguments):
            commands.append(command)
            popen_keyword_arguments.append(keyword_arguments)
            return FakeAppProcess([0])
        with tempfile.TemporaryFile("a+") as lock_file, \
                unittest.mock.patch.object(command_line, "take_the_running_lock_or_name_the_copy_holding_it",
                                           return_value=lock_file), \
                unittest.mock.patch("agent_command_tab.installed_copy_paths.find_python_of_this_copy",
                                    return_value="/opt/homebrew/opt/agent-command-tab/libexec/bin/python"), \
                unittest.mock.patch.object(command_line.subprocess, "Popen", side_effect=start_the_app):
            self.assertEqual(command_line.run_with_restarts(verbose=False), 0)
            # By the Python's path that survives `brew upgrade`, not sys.executable (code review, 2026-10-30).
            self.assertEqual(commands[0][0], "/opt/homebrew/opt/agent-command-tab/libexec/bin/python")
            self.assertEqual(popen_keyword_arguments[0]["pass_fds"], [lock_file.fileno()])
            self.assertEqual(popen_keyword_arguments[0]["env"][command_line.RUNNING_LOCK_FILE_DESCRIPTOR_VARIABLE],
                             str(lock_file.fileno()))

    def test_the_app_started_directly_takes_the_lock_itself(self):  # code review, 2026-10-30
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


if __name__ == "__main__":
    unittest.main()
