"""Opens (or closes) throwaway iTerm2 windows with stand-in agent tabs, for trying the list by hand.

Usage: open_test_windows_for_manual_ui_check.py open|close STATE_FOLDER

`open` creates two windows titled "TEST window A" (two stand-in agent tabs and one plain shell
tab) and "TEST window B" (one stand-in agent tab). A stand-in agent is a link named `claude`
that points to /bin/sleep, so the Claude Code adapter lists the tab; status reports are
injected so the tabs show working and asking. The created session IDs are saved in
STATE_FOLDER. `close` closes exactly those tabs.
"""

import asyncio
import json
import os
import sys

import iterm2
import iterm2.auth

iterm2.auth.gAppKitAvailable = False

COMMAND, STATE_FOLDER = sys.argv[1], os.path.realpath(sys.argv[2])
SESSION_IDS_PATH = os.path.join(STATE_FOLDER, "test_window_session_ids.json")


async def open_test_windows(connection):
    os.makedirs(STATE_FOLDER, exist_ok=True)
    stand_in_link = os.path.join(STATE_FOLDER, "claude")
    if not os.path.lexists(stand_in_link):
        os.symlink("/bin/sleep", stand_in_link)
    app = await iterm2.async_get_app(connection)
    original_window = app.current_terminal_window
    original_session = original_window.current_tab.current_session if original_window else None
    profile = iterm2.LocalWriteOnlyProfile({"Initial Text": f"cd {STATE_FOLDER} && PATH={STATE_FOLDER}:$PATH claude 7200"})
    window_a = await iterm2.Window.async_create(connection, profile_customizations=profile)
    await window_a.async_create_tab(profile_customizations=profile)
    await window_a.async_create_tab()
    window_b = await iterm2.Window.async_create(connection, profile_customizations=profile)
    await window_a.async_set_title("TEST window A")
    await window_b.async_set_title("TEST window B")
    await asyncio.sleep(3)
    await app.async_refresh()
    window_a = app.get_window_by_id(window_a.window_id)
    window_b = app.get_window_by_id(window_b.window_id)
    session_ids = [tab.sessions[0].session_id for tab in window_a.tabs + window_b.tabs]
    with open(SESSION_IDS_PATH, "w") as session_ids_file:
        json.dump(session_ids, session_ids_file)
    await window_a.tabs[0].async_set_title("TEST working agent")
    await window_a.tabs[1].async_set_title("TEST asking agent")
    await window_b.tabs[0].async_set_title("TEST idle agent")
    await window_a.tabs[0].sessions[0].async_inject(b"\x1b]7501;state=working\x1b\\")
    await window_a.tabs[1].sessions[0].async_inject(b"\x1b]7501;state=blocked:kind=permission\x1b\\")
    await window_b.tabs[0].sessions[0].async_inject(b"\x1b]7501;state=idle\x1b\\")
    if original_session is not None:
        await original_session.async_activate(select_tab=True, order_window_front=True)
    print("opened", len(session_ids), "test tabs")


async def close_test_windows(connection):
    with open(SESSION_IDS_PATH) as session_ids_file:
        session_ids = set(json.load(session_ids_file))
    app = await iterm2.async_get_app(connection)
    closed_count = 0
    for window in app.terminal_windows:
        for tab in window.tabs:
            if tab.sessions and tab.sessions[0].session_id in session_ids:
                await tab.async_close(force=True)
                closed_count += 1
    print("closed", closed_count, "test tabs")


if __name__ == "__main__":
    iterm2.run_until_complete(open_test_windows if COMMAND == "open" else close_test_windows)
