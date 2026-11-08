"""Lets the tests that open windows skip themselves when Codex runs them inside its sandbox."""

import os

# Codex sets CODEX_SANDBOX ("seatbelt" on macOS) in the commands it runs inside its sandbox. That sandbox blocks the
# connection to the window server, so NSApplication.sharedApplication() calls abort() instead of raising: the whole
# test run dies, and macOS shows a "Python quit unexpectedly" dialog.
RUNNING_IN_CODEX_SANDBOX = "CODEX_SANDBOX" in os.environ
REASON_FOR_SKIPPING_WINDOW_TESTS_IN_CODEX_SANDBOX = "Codex's sandbox blocks the window server, so AppKit would abort the test run"
