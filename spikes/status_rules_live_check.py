"""Checks the screen-based status rules with a live Claude Code session in a throwaway window.

Usage: status_rules_live_check.py OUTPUT_FOLDER

1. Asks Claude Code (Haiku) to run `perl -e 'sleep 30'`, which needs permission here, waits
   for the row to show "asking", approves, and measures how long until the row stops asking.
   Without the permission-dialog check it would stay "asking" until the command ends.
2. Sets a 40-second stuck limit and asks for a silent 2-minute command
   (`python3 -c "import time; time.sleep(120)"`, which runs without asking), and checks that the
   row stays "working" (running python3) well past the limit: an agent waiting on a command it
   started is never stuck. The stuck rule itself is covered by session_status_computation_tests.
Exits with 1 when a check fails.
Closes its window at the end. Touches nothing else.
"""

import asyncio
import os
import sys
import threading
import time

import iterm2

from agent_command_tab import iterm2_connection_thread
from agent_command_tab.iterm2_connection_thread import Iterm2ConnectionThread
from agent_command_tab.snapshot_data_classes import iterate_agent_session_entries

OUTPUT_FOLDER = os.path.realpath(sys.argv[1])


def main():
    project_folder = os.path.join(OUTPUT_FOLDER, "status_rules_test_project")
    os.makedirs(project_folder, exist_ok=True)
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
    while core.app is None:
        time.sleep(0.1)
    state = {}

    def run_on_core_loop(coroutine, timeout=60):
        return asyncio.run_coroutine_threadsafe(coroutine, connection_thread.event_loop).result(timeout)

    def find_status():
        for _, _, entry in iterate_agent_session_entries(snapshots[-1]) if snapshots else []:
            if entry.session_id == state.get("session_id"):
                return entry.status, entry.status_reason
        return None, None

    def wait_for_status(wanted_statuses, timeout):
        deadline = time.time() + timeout
        with snapshot_arrived:
            while time.time() < deadline:
                if find_status()[0] in wanted_statuses:
                    return time.time()
                snapshot_arrived.wait(0.5)
        return None

    async def open_window():
        profile = iterm2.LocalWriteOnlyProfile({"Initial Text": "claude --permission-mode default --model haiku --name status-rules-check"})
        profile.set_initial_directory_mode(iterm2.InitialWorkingDirectory.INITIAL_WORKING_DIRECTORY_CUSTOM)
        profile.set_custom_directory(project_folder)
        window = await iterm2.Window.async_create(core.connection, profile_customizations=profile)
        state["window"] = window
        state["session_id"] = window.tabs[0].sessions[0].session_id

    async def type_prompt(text):
        session = core.app.get_session_by_id(state["session_id"])
        await session.async_send_text(text, suppress_broadcast=True)
        await asyncio.sleep(0.3)
        await session.async_send_text("\r", suppress_broadcast=True)

    async def press(key):
        await core.app.get_session_by_id(state["session_id"]).async_send_text(key, suppress_broadcast=True)

    run_on_core_loop(open_window())
    results = {}
    try:
        results["listed and idle"] = wait_for_status({"idle", "done"}, 60) is not None
        if not results["listed and idle"]:
            return 1  # not at its prompt (its folder trust question, say), so nothing is typed
        run_on_core_loop(type_prompt("Use the Bash tool to run exactly this command: perl -e 'sleep 30'"))
        results["row shows asking"] = wait_for_status({"asking"}, 90) is not None
        if not results["row shows asking"]:
            return 1  # nothing to approve, so no key is typed
        time.sleep(1.5)
        approved_at = time.time()
        run_on_core_loop(press("1"))
        stopped_asking_at = wait_for_status({"working", "stuck"}, 40)
        print("seconds from approval until the row stops asking (command runs 30 s):",
              round(stopped_asking_at - approved_at, 1) if stopped_asking_at else None)
        # Long before the command ends, since the permission dialog's going ends "asking".
        results["row stops asking within 10 s of the approval"] = (stopped_asking_at is not None
                                                                   and stopped_asking_at - approved_at < 10)
        results["back to idle or done after the command"] = wait_for_status({"idle", "done"}, 60) is not None

        iterm2_connection_thread.STUCK_THRESHOLD_SECONDS = 40  # read at each snapshot; 10 minutes in the app
        run_on_core_loop(type_prompt("Use the Bash tool to run exactly this command: python3 -c \"import time; time.sleep(120)\""))
        working_at = wait_for_status({"working"}, 60)
        turned_stuck_at = wait_for_status({"stuck"}, 70)  # 30 seconds past the limit, while the command still runs
        results["row still working, not stuck, 70 s into a silent command with a 40-second limit"] = (
            working_at is not None and turned_stuck_at is None and find_status()[0] == "working")
        print("reason:", find_status()[1])
        run_on_core_loop(press("\x1b"))
        time.sleep(3)
    finally:
        for check, passed in results.items():
            print(("PASS  " if passed else "FAIL  ") + check)

        async def close():
            await state["window"].async_close(force=True)
        run_on_core_loop(close())
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
