"""Phase 0 experiments that need throwaway iTerm2 windows. Touches no existing tab.

Opens two test windows whose tabs run a slow counter loop, then checks:
1. Does iTerm2's "send text at start" profile key ("Initial Text") start a command in a new tab?
2. Does Window.async_set_tabs move a tab to another window without interrupting its program?
3. Does a title set with Tab.async_set_title survive a program setting its own title?
4. What do OSC 7501 and OSC 21337 status reports put in the `status` variable and in
   iterm2.get_session_status?
5. Do window frames use a bottom-left origin (compared with AppleScript's top-left bounds)?
6. Does App.terminal_windows follow the front-to-back window order?
7. Does Tab.async_move_to_window fail for a window's only tab?

Closes the test windows at the end and brings the previously front window back.
"""

import asyncio
import base64
import json
import os
import re
import subprocess
import tempfile

import iterm2
import iterm2.auth

iterm2.auth.gAppKitAvailable = False

COUNTER_PATTERN = re.compile(r"phase0-test-counter-(\w+) (\d+)")


def build_counter_tab_profile(label, working_folder):
    """Returns profile customizations that start a counter loop through "Initial Text"."""
    counter_command = (f'i=0; while true; do i=$((i+1)); echo "phase0-test-counter-{label} $i"; '
                       'sleep 0.2; done')
    profile = iterm2.LocalWriteOnlyProfile({"Initial Text": counter_command})
    profile.set_initial_directory_mode(iterm2.InitialWorkingDirectory.INITIAL_WORKING_DIRECTORY_CUSTOM)
    profile.set_custom_directory(working_folder)
    return profile


async def read_last_counter_value(session):
    """Returns (label, value) of the newest counter line on the session's screen, or None."""
    contents = await session.async_get_screen_contents()
    last_match = None
    for line_index in range(contents.number_of_lines):
        for match in COUNTER_PATTERN.finditer(contents.line(line_index).string):
            last_match = (match.group(1), int(match.group(2)))
    return last_match


async def read_session_status(session):
    """Returns the session's `status` variable and the get_session_status result."""
    variable_value = await session.async_get_variable("status")
    function_result = await session.async_invoke_function(
        f'iterm2.get_session_status(session_id: "{session.session_id}")', timeout=5)
    return variable_value, function_result


def read_front_window_bounds_through_applescript():
    """Returns AppleScript's bounds (left, top, right, bottom; top-left origin) of iTerm2's front window."""
    output = subprocess.run(["osascript", "-e", 'tell application "iTerm2" to get bounds of front window'],
                            capture_output=True, text=True, check=False).stdout.strip()
    return [int(value) for value in output.split(", ")] if output else None


async def run_experiments(connection):
    """Runs every experiment and prints a JSON summary of the results."""
    results = {}
    app = await iterm2.async_get_app(connection)
    original_front_window = app.current_terminal_window
    original_front_session = original_front_window.current_tab.current_session if original_front_window else None
    working_folder = tempfile.mkdtemp(prefix="phase0_test_windows_")
    test_session_ids = set()
    try:
        window_a = await iterm2.Window.async_create(connection, profile_customizations=build_counter_tab_profile("A1", working_folder))
        test_session_ids.add(window_a.tabs[0].sessions[0].session_id)
        tab_a2_created = await window_a.async_create_tab(profile_customizations=build_counter_tab_profile("A2", working_folder))
        test_session_ids.add(tab_a2_created.sessions[0].session_id)
        window_b = await iterm2.Window.async_create(connection, profile_customizations=build_counter_tab_profile("B1", working_folder))
        test_session_ids.add(window_b.tabs[0].sessions[0].session_id)
        await asyncio.sleep(3)
        await app.async_refresh()

        tab_a1, tab_a2 = window_a.tabs[0], window_a.tabs[1]
        tab_b1 = window_b.tabs[0]
        session_a1, session_a2, session_b1 = tab_a1.sessions[0], tab_a2.sessions[0], tab_b1.sessions[0]
        results["1 initial text started the counters"] = {
            "A1": await read_last_counter_value(session_a1),
            "A2": await read_last_counter_value(session_a2),
            "B1": await read_last_counter_value(session_b1),
        }

        # 2. Move tab A2 into window B, after B1.
        pid_before = await session_a2.async_get_variable("jobPid")
        counter_before = await read_last_counter_value(session_a2)
        await window_b.async_set_tabs([tab_b1, tab_a2])
        await asyncio.sleep(2)
        await app.async_refresh()
        pid_after = await session_a2.async_get_variable("jobPid")
        counter_after = await read_last_counter_value(session_a2)
        results["2 move tab to another window"] = {
            "window B tab ids after move": [tab.tab_id for tab in window_b.tabs],
            "window A tab ids after move": [tab.tab_id for tab in window_a.tabs],
            "counter before": counter_before, "counter after 2 s": counter_after,
            "foreground job pid unchanged": pid_before == pid_after,
        }

        # 3. Rename tab A1, then let the program set its own title (OSC 0).
        await tab_a1.async_set_title("phase0 renamed tab")
        await asyncio.sleep(0.5)
        await session_a1.async_inject(b"\x1b]0;title-set-by-program\x07")
        await asyncio.sleep(0.5)
        results["3 rename survives a program title"] = {
            "tab title": await tab_a1.async_get_variable("title"),
            "tab titleOverrideFormat": await tab_a1.async_get_variable("titleOverrideFormat"),
            "session name": await session_a1.async_get_variable("name"),
        }

        # 4. Status reports.
        status_observations = []
        message = base64.b64encode(b"Run the test suite?").decode()
        osc_7501_reports = ["state=working:app=phase0", f"state=blocked:kind=permission:app=phase0:msg={message}",
                            "state=done:app=phase0", "state=failed:app=phase0", "state=idle:app=phase0"]
        for report in osc_7501_reports:
            await session_a1.async_inject(f"\x1b]7501;{report}\x1b\\".encode())
            await asyncio.sleep(0.7)
            variable_value, function_result = await read_session_status(session_a1)
            status_observations.append({"sent": "OSC 7501 " + report, "status variable": variable_value,
                                        "get_session_status": function_result})
        for status_text in ["Working", "Waiting", "Idle", "Compiling"]:
            await session_b1.async_inject(f"\x1b]21337;status={status_text}\x07".encode())
            await asyncio.sleep(0.7)
            variable_value, function_result = await read_session_status(session_b1)
            status_observations.append({"sent": "OSC 21337 status=" + status_text, "status variable": variable_value,
                                        "get_session_status": function_result})
        results["4 status reports"] = status_observations

        # 5. Coordinates: place window B at a known frame that is not full height.
        await window_b.async_activate()
        await app.async_activate(raise_all_windows=False)
        requested_frame = iterm2.Frame(iterm2.Point(100, 120), iterm2.Size(800, 500))
        await window_b.async_set_frame(requested_frame)
        await asyncio.sleep(1)
        frame_read_back = await window_b.async_get_frame()
        results["5 coordinates"] = {
            "requested origin and size": [100, 120, 800, 500],
            "read back": [frame_read_back.origin.x, frame_read_back.origin.y, frame_read_back.size.width, frame_read_back.size.height],
            "AppleScript bounds of front window (left, top, right, bottom)": read_front_window_bounds_through_applescript(),
        }

        # 6. Window order after bringing A, then B, to the front.
        order_observations = {}
        for front_window, name in ((window_a, "A"), (window_b, "B")):
            await front_window.async_activate()
            await asyncio.sleep(0.7)
            await app.async_refresh()
            order_observations[f"{name} in front"] = {
                "terminal_windows order": [window.window_id for window in app.terminal_windows],
                "current_terminal_window": app.current_terminal_window.window_id if app.current_terminal_window else None,
            }
        results["6 window order"] = {"window A id": window_a.window_id, "window B id": window_b.window_id, **order_observations}

        # 7. Move to new window for a window's only tab (window A has only A1 now).
        try:
            await tab_a1.async_move_to_window()
            results["7 move only tab to new window"] = "succeeded"
        except Exception as error:
            results["7 move only tab to new window"] = f"failed: {error!r}"
    finally:
        await app.async_refresh()
        for window in app.terminal_windows:
            for tab in window.tabs:
                if tab.sessions and tab.sessions[0].session_id in test_session_ids:
                    await tab.async_close(force=True)
        if original_front_session is not None:
            await original_front_session.async_activate(select_tab=True, order_window_front=True)
    print(json.dumps(results, indent=1, default=str))


iterm2.run_until_complete(run_experiments)
