import contextlib
import io
import os
import subprocess
import tempfile
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


class FakeAppProcess:
    """Stands in for subprocess.Popen: each wait() returns or raises the next planned outcome."""

    def __init__(self, planned_outcomes):
        self.planned_outcomes = list(planned_outcomes)
        self.wait_timeouts = []
        self.was_killed = False

    def wait(self, timeout=None):
        self.wait_timeouts.append(timeout)
        outcome = self.planned_outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    def kill(self):
        self.was_killed = True


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
            with unittest.mock.patch.object(command_line, "take_the_running_lock", return_value=None), \
                    unittest.mock.patch.object(command_line, "RUNNING_LOCK_FILE_PATH", lock_file_path), \
                    unittest.mock.patch.object(command_line.subprocess, "Popen", side_effect=AssertionError("started the app")), \
                    contextlib.redirect_stdout(printed):
                self.assertEqual(command_line.run_with_restarts(verbose=False), 0)
            self.assertIn("already running (process 4242)", printed.getvalue())


if __name__ == "__main__":
    unittest.main()
