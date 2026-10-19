"""Throwaway iTerm2 windows for trying drags in the dashboard by hand or with real mouse events.

Usage:
  drag_test_windows.py open CLAUDE_TEST_FOLDER STATE_FILE
      Opens windows "drag-test-A" and "drag-test-B", each with a Claude Code (Haiku) tab
      ("drag-test-a1", "drag-test-b1"), and has the agent in drag-test-a1 run a 90-second command
      that prints a number each second, so a drag can happen while it runs. Saves the IDs.
  drag_test_windows.py check STATE_FILE
      Prints which window holds each test tab now, in what tab order, and the end of drag-test-a1's screen.
  drag_test_windows.py close STATE_FILE
      Closes the test windows that still exist. Touches nothing else.
"""

import asyncio
import json
import sys

import iterm2

CLAUDE_LAUNCH_COMMAND = "claude --permission-mode default --model haiku"
COUNTING_COMMAND = 'python3 -c "import time; [print(i, flush=True) or time.sleep(1) for i in range(90)]"'
SECONDS_TO_WAIT_FOR_CLAUDE_CODE_TO_START = 30


async def read_screen_lines(session):
    contents = await session.async_get_screen_contents()
    return [contents.line(index).string for index in range(contents.number_of_lines)]


async def open_test_windows(connection, claude_test_folder, state_file_path):
    app = await iterm2.async_get_app(connection)
    test_ids = {}
    for label in ("A", "B"):
        profile = iterm2.LocalWriteOnlyProfile({"Initial Text": CLAUDE_LAUNCH_COMMAND})
        profile.set_initial_directory_mode(iterm2.InitialWorkingDirectory.INITIAL_WORKING_DIRECTORY_CUSTOM)
        profile.set_custom_directory(claude_test_folder)
        window = await iterm2.Window.async_create(connection, profile_customizations=profile)
        await window.async_set_title(f"drag-test-{label}")
        tab = window.tabs[0]
        await tab.async_set_title(f"drag-test-{label.lower()}1")
        test_ids[label] = {"window_id": window.window_id, "tab_id": tab.tab_id, "session_id": tab.sessions[0].session_id}
    with open(state_file_path, "w") as state_file:
        json.dump(test_ids, state_file)
    session = app.get_session_by_id(test_ids["A"]["session_id"])
    for _ in range(SECONDS_TO_WAIT_FOR_CLAUDE_CODE_TO_START):
        if any("for shortcuts" in line for line in await read_screen_lines(session)):
            break
        await asyncio.sleep(1)
    await session.async_send_text(f"Use the Bash tool to run exactly this command: {COUNTING_COMMAND}", suppress_broadcast=True)
    await asyncio.sleep(0.3)
    await session.async_send_text("\r", suppress_broadcast=True)
    print(json.dumps(test_ids))


async def check_test_windows(connection, state_file_path):
    app = await iterm2.async_get_app(connection)
    with open(state_file_path) as state_file:
        test_ids = json.load(state_file)
    test_session_ids = {ids["session_id"]: f"drag-test-{label.lower()}1" for label, ids in test_ids.items()}
    for window in app.terminal_windows:
        tab_names = [next((test_session_ids[session.session_id] for session in tab.sessions
                           if session.session_id in test_session_ids), f"other tab {tab.tab_id}") for tab in window.tabs]
        if any(name.startswith("drag-test") for name in tab_names):
            print(f"window {window.window_id} ({await window.async_get_variable('titleOverride') or ''}): tabs {tab_names}")
    session = app.get_session_by_id(test_ids["A"]["session_id"])
    if session is None:
        print("drag-test-a1's session is gone")
        return
    non_blank_lines = [line for line in await read_screen_lines(session) if line.strip()]
    print("end of drag-test-a1's screen:", non_blank_lines[-12:])


async def close_test_windows(connection, state_file_path):
    app = await iterm2.async_get_app(connection)
    with open(state_file_path) as state_file:
        test_ids = json.load(state_file)
    for ids in test_ids.values():
        window = app.get_window_by_id(ids["window_id"])
        if window is not None:
            await window.async_close(force=True)
            print("closed", ids["window_id"])


def main():
    command = sys.argv[1]
    if command == "open":
        iterm2.run_until_complete(lambda connection: open_test_windows(connection, sys.argv[2], sys.argv[3]))
    elif command == "check":
        iterm2.run_until_complete(lambda connection: check_test_windows(connection, sys.argv[2]))
    elif command == "close":
        iterm2.run_until_complete(lambda connection: close_test_windows(connection, sys.argv[2]))
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
