"""Checks the core's actions against real iTerm2 windows, without the UI. Touches only its own test windows.

Opens two throwaway windows whose tabs run a stand-in agent: a link named `claude` that points
to /bin/sleep, so iTerm2's commandLine reads `claude 900` and the Claude Code adapter lists the
tab. Status reports are injected as OSC 7501 output. Then it runs rename, move and focus
actions through DashboardCore and checks that the published snapshots follow. Closes its
windows at the end and brings the previously front window back.
"""

import asyncio
import os
import sys
import tempfile
import threading
import time

import iterm2

from agent_command_tab.dashboard_core_api import ActionRequest
from agent_command_tab.iterm2_connection_thread import Iterm2ConnectionThread
from agent_command_tab.snapshot_data_classes import iterate_agent_session_entries


def main():
    stand_in_folder = tempfile.mkdtemp(prefix="core_actions_check_")
    os.symlink("/bin/sleep", os.path.join(stand_in_folder, "claude"))
    snapshots = []
    snapshot_arrived = threading.Condition()

    def publish_snapshot(snapshot):
        with snapshot_arrived:
            snapshots.append(snapshot)
            snapshot_arrived.notify_all()

    connection_thread = Iterm2ConnectionThread(publish_snapshot, lambda message: print("connection lost:", message))
    connection_thread.start()
    connection_thread.core_ready.wait()
    core = connection_thread.core
    loop = connection_thread.event_loop

    def run_on_core_loop(coroutine, timeout=30):
        return asyncio.run_coroutine_threadsafe(coroutine, loop).result(timeout)

    def wait_for_snapshot(condition, timeout=20):
        deadline = time.time() + timeout
        with snapshot_arrived:
            while time.time() < deadline:
                if snapshots and condition(snapshots[-1]):
                    return snapshots[-1]
                snapshot_arrived.wait(0.2)
        print("  timed out; test tabs in the newest snapshot:", describe_test_entries(snapshots[-1]) if snapshots else None)
        return None

    def describe_test_entries(snapshot):
        return [(window_entry.window_id[:8], window_entry.title, entry.tab_id, entry.tab_title, entry.status)
                for window_entry, entry in find_test_entries(snapshot).values()]

    def find_test_entries(snapshot):
        return {entry.session_id: (window_entry, entry) for _, window_entry, entry in iterate_agent_session_entries(snapshot)
                if entry.session_id in test_session_ids}

    while core.app is None:
        time.sleep(0.1)
    results = {}
    test_session_ids = set()

    async def open_test_windows():
        original_window = core.app.current_terminal_window
        original_session = original_window.current_tab.current_session if original_window else None
        profile = iterm2.LocalWriteOnlyProfile({"Initial Text": f"cd {stand_in_folder} && PATH={stand_in_folder}:$PATH claude 900"})
        window_a = await iterm2.Window.async_create(core.connection, profile_customizations=profile)
        await window_a.async_create_tab(profile_customizations=profile)
        await window_a.async_create_tab()  # a plain shell tab, hidden from the list
        window_b = await iterm2.Window.async_create(core.connection, profile_customizations=profile)
        await asyncio.sleep(2.5)
        await core.app.async_refresh()
        window_a = core.app.get_window_by_id(window_a.window_id)
        window_b = core.app.get_window_by_id(window_b.window_id)
        for tab in window_a.tabs + window_b.tabs:
            test_session_ids.add(tab.sessions[0].session_id)
        # Report statuses only once the stand-in runs: iTerm2 clears `status` when the foreground job changes.
        for _ in range(40):
            command_lines = [await tab.sessions[0].async_get_variable("commandLine") for tab in window_a.tabs[:2]]
            if all((command_line or "").startswith("claude") for command_line in command_lines):
                break
            await asyncio.sleep(0.25)
        await window_a.tabs[0].sessions[0].async_inject(b"\x1b]7501;state=working\x1b\\")
        await window_a.tabs[1].sessions[0].async_inject(b"\x1b]7501;state=blocked:kind=permission\x1b\\")
        return original_session, window_a.window_id, window_b.window_id, [tab.tab_id for tab in window_a.tabs], window_b.tabs[0].tab_id

    original_session, window_a_id, window_b_id, window_a_tab_ids, window_b_tab_id = run_on_core_loop(open_test_windows())
    try:
        snapshot = wait_for_snapshot(lambda snapshot: len(find_test_entries(snapshot)) == 3)
        entries = find_test_entries(snapshot) if snapshot else {}
        results["listed stand-in agent tabs (expect 3; the shell tab is hidden)"] = len(entries)
        snapshot = wait_for_snapshot(lambda snapshot: sorted(entry.status for _, entry in find_test_entries(snapshot).values())
                                     == ["asking", "unknown", "working"])
        results["states reach asking, unknown, working (expect True)"] = snapshot is not None
        results["window A total_tab_count (expect 3)"] = next(
            (window_entry.total_tab_count for window_entry, _ in entries.values() if window_entry.window_id == window_a_id), None)

        connection_thread.submit_action(ActionRequest("rename_tab", {"tab_id": window_a_tab_ids[0]}, {"new_title": "renamed \\(tab)"}))
        snapshot = wait_for_snapshot(lambda snapshot: any(entry.tab_title == "renamed \\(tab)" for _, entry in find_test_entries(snapshot).values()))
        results["rename tab with a backslash (expect True)"] = snapshot is not None

        connection_thread.submit_action(ActionRequest("rename_window", {"window_id": window_b_id}, {"new_title": "Window B renamed"}))
        snapshot = wait_for_snapshot(lambda snapshot: any(window_entry.title == "Window B renamed" and window_entry.window_title_set_by_user
                                                          for window_entry, _ in find_test_entries(snapshot).values()))
        results["rename window (expect True)"] = snapshot is not None

        connection_thread.submit_action(ActionRequest("move_tab", {"tab_id": window_a_tab_ids[1]},
                                                      {"target_window_id": window_b_id, "drop_before_tab_id": window_b_tab_id}))
        snapshot = wait_for_snapshot(lambda snapshot: any(
            window_entry.window_id == window_b_id and [entry.tab_id for entry in window_entry.agent_sessions][:1] == [window_a_tab_ids[1]]
            for window_entry, _ in find_test_entries(snapshot).values()))
        results["move tab to the front of window B (expect True)"] = snapshot is not None

        connection_thread.submit_action(ActionRequest("move_tab", {"tab_id": window_a_tab_ids[1]},
                                                      {"target_window_id": window_b_id, "drop_before_tab_id": None}))
        snapshot = wait_for_snapshot(lambda snapshot: any(
            window_entry.window_id == window_b_id and [entry.tab_id for entry in window_entry.agent_sessions][-1:] == [window_a_tab_ids[1]]
            for window_entry, _ in find_test_entries(snapshot).values()))
        results["reorder within window B (expect True)"] = snapshot is not None

        connection_thread.submit_action(ActionRequest("focus_agent_session", {"session_id": sorted(test_session_ids)[0]}))
        time.sleep(1.5)
        results["focus action ran without error (see output above)"] = True

        before = [window_entry.window_id for window_entry in snapshots[-1].monitors[0].windows]
        connection_thread.submit_action(ActionRequest("reorder_window_list", {"window_id": before[-1]}, {"drop_before_window_id": before[0]}))
        snapshot = wait_for_snapshot(lambda snapshot: [window_entry.window_id for window_entry in snapshot.monitors[0].windows][:1] == before[-1:])
        results["reorder window list (expect True)"] = snapshot is not None

        def count_stand_in_sessions(snapshot):
            return sum(1 for _, _, entry in iterate_agent_session_entries(snapshot)
                       if os.path.realpath(entry.working_folder or "/") == os.path.realpath(stand_in_folder))

        stand_in_launch_command = f"PATH={stand_in_folder}:$PATH claude 900"
        count_before = count_stand_in_sessions(snapshots[-1])
        connection_thread.submit_action(ActionRequest("start_agent_session", {"window_id": window_a_id},
                                                      {"folder": stand_in_folder, "launch_command": stand_in_launch_command}))
        snapshot = wait_for_snapshot(lambda snapshot: count_stand_in_sessions(snapshot) == count_before + 1)
        results["new agent session in a tab, in the chosen folder (expect True)"] = snapshot is not None
        connection_thread.submit_action(ActionRequest("start_agent_session", {},
                                                      {"folder": stand_in_folder, "launch_command": stand_in_launch_command}))
        snapshot = wait_for_snapshot(lambda snapshot: count_stand_in_sessions(snapshot) == count_before + 2)
        results["new agent session in a new window (expect True)"] = snapshot is not None
        test_session_ids.update(entry.session_id for _, _, entry in iterate_agent_session_entries(snapshots[-1])
                                if os.path.realpath(entry.working_folder or "/") == os.path.realpath(stand_in_folder))
    finally:
        async def close_test_windows():
            await core.app.async_refresh()
            for window in core.app.terminal_windows:
                for tab in window.tabs:
                    if tab.sessions and tab.sessions[0].session_id in test_session_ids:
                        await tab.async_close(force=True)
            if original_session is not None:
                await original_session.async_activate(select_tab=True, order_window_front=True)
        run_on_core_loop(close_test_windows())
    for question, answer in results.items():
        print(f"{question}: {answer}")
    os.remove(os.path.join(stand_in_folder, "claude"))
    os.rmdir(stand_in_folder)
    return 0


if __name__ == "__main__":
    sys.exit(main())
