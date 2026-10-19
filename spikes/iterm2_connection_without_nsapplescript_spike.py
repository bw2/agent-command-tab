"""Phase 0 experiment: connect to iTerm2 from a background thread without NSAppleScript.

Read-only: lists sessions and changes nothing in iTerm2.

Checks:
1. Setting iterm2.auth.gAppKitAvailable = False makes the package fetch its access token
   with the osascript subprocess runner, never with Foundation.NSAppleScript.
2. A stale token (as after an iTerm2 restart) is recovered by the package's own retry,
   still without NSAppleScript.
3. All of this works from a background thread running its own asyncio loop, as the app's
   core will.
"""

import asyncio
import os
import threading

import iterm2
import iterm2.auth

iterm2.auth.gAppKitAvailable = False


def fail_if_nsapplescript_runner_is_used(self):
    """Replaces AppKitApplescriptRunner.execute so any use of it is reported."""
    raise AssertionError("NSAppleScript runner was used")


iterm2.auth.AppKitApplescriptRunner.execute = fail_if_nsapplescript_runner_is_used


async def connect_and_count_sessions():
    """Connects once and returns the number of sessions iTerm2 reports."""
    connection = await iterm2.Connection.async_create()
    app = await iterm2.async_get_app(connection)
    session_count = sum(len(tab.sessions) for window in app.terminal_windows for tab in window.tabs)
    await connection.websocket.close()
    return session_count


def run_check_on_background_thread(results, start_with_stale_token):
    """Runs one connection check on a fresh asyncio loop and stores its outcome.

    Each process runs only one check, because the iterm2 package keeps process-wide state
    (App.instance) bound to the first connection, so a second connection in the same
    process fails. That is why the app restarts its process to reconnect.
    """
    try:
        os.environ.pop("ITERM2_COOKIE", None)
        os.environ.pop("ITERM2_KEY", None)
        if start_with_stale_token:
            os.environ["ITERM2_COOKIE"] = "0" * 32
            os.environ["ITERM2_KEY"] = "0" * 32
        label = "stale token" if start_with_stale_token else "fresh token"
        results[f"{label}, sessions seen"] = asyncio.run(connect_and_count_sessions())
    except Exception as error:
        results["error"] = repr(error)


results = {}
background_thread = threading.Thread(
    target=run_check_on_background_thread, args=(results, os.environ.get("SPIKE_START_WITH_STALE_TOKEN") == "1"))
background_thread.start()
background_thread.join(timeout=60)
print("background thread finished:", not background_thread.is_alive())
for key, value in results.items():
    print(f"{key}: {value}")
