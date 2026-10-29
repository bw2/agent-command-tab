import contextlib
import io
import subprocess
import unittest

from agent_command_tab import __version__
from agent_command_tab.command_line import SECONDS_TO_WAIT_FOR_THE_APP_AFTER_CTRL_C, main, wait_for_app_process


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


if __name__ == "__main__":
    unittest.main()
