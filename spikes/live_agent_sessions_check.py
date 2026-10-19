"""End-to-end check with real agent sessions in throwaway iTerm2 windows.

Usage: live_agent_sessions_check.py CLAUDE_TEST_FOLDER CODEX_TRUSTED_FOLDER

1. Claude Code (Haiku) runs a 20-second command that prints a number each second. While it
   runs, the dashboard's own move_tab action (what a drag between windows does) moves the tab
   into a second window. The check confirms the row shows "running", the tab now sits in the
   other window with the same session, and the command finishes (the turn ends and the screen
   shows the last number), so the move didn't interrupt the agent.
2. Codex starts in a folder its config already trusts (no trust question, no config change),
   is asked to reply "OK", and the check confirms it is listed as Codex and goes from working
   back to idle.
Closes its windows at the end. Touches nothing else.
"""

import asyncio
import os
import sys
import threading
import time

import iterm2

from llm_sidebar_dashboard.dashboard_core_api import ActionRequest
from llm_sidebar_dashboard.iterm2_connection_thread import Iterm2ConnectionThread
from llm_sidebar_dashboard.snapshot_data_classes import iterate_agent_session_entries

CLAUDE_TEST_FOLDER = os.path.realpath(sys.argv[1])
CODEX_TRUSTED_FOLDER = os.path.realpath(sys.argv[2])
COUNTING_COMMAND = 'python3 -c "import time; [print(i, flush=True) or time.sleep(1) for i in range(20)]"'


def main():
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
    windows = []

    def run_on_core_loop(coroutine, timeout=60):
        return asyncio.run_coroutine_threadsafe(coroutine, connection_thread.event_loop).result(timeout)

    def find_entry(session_id):
        for _, window_entry, entry in iterate_agent_session_entries(snapshots[-1]) if snapshots else []:
            if entry.session_id == session_id:
                return window_entry, entry
        return None, None

    def wait_until(condition, timeout):
        deadline = time.time() + timeout
        with snapshot_arrived:
            while time.time() < deadline:
                if condition():
                    return True
                snapshot_arrived.wait(0.5)
        return False

    async def open_window(initial_text, folder):
        profile = iterm2.LocalWriteOnlyProfile({"Initial Text": initial_text} if initial_text else {})
        profile.set_initial_directory_mode(iterm2.InitialWorkingDirectory.INITIAL_WORKING_DIRECTORY_CUSTOM)
        profile.set_custom_directory(folder)
        window = await iterm2.Window.async_create(core.connection, profile_customizations=profile)
        windows.append(window)
        return window.window_id, window.tabs[0].tab_id, window.tabs[0].sessions[0].session_id

    async def type_prompt(session_id, text):
        session = core.app.get_session_by_id(session_id)
        await session.async_send_text(text, suppress_broadcast=True)
        await asyncio.sleep(0.3)
        await session.async_send_text("\r", suppress_broadcast=True)

    async def read_screen(session_id):
        contents = await core.app.get_session_by_id(session_id).async_get_screen_contents()
        return "\n".join(contents.line(index).string for index in range(contents.number_of_lines))

    results = {}
    try:
        _, claude_tab_id, claude_session_id = run_on_core_loop(open_window(
            "claude --permission-mode default --model haiku --name live-move-check", CLAUDE_TEST_FOLDER))
        target_window_id, _, _ = run_on_core_loop(open_window(None, CLAUDE_TEST_FOLDER))
        results["claude listed and idle"] = wait_until(
            lambda: (find_entry(claude_session_id)[1] or ()) and find_entry(claude_session_id)[1].status in ("idle", "done"), 60)
        run_on_core_loop(type_prompt(claude_session_id, f"Use the Bash tool to run exactly this command: {COUNTING_COMMAND}"))
        results["row shows running"] = wait_until(
            lambda: find_entry(claude_session_id)[1] is not None and find_entry(claude_session_id)[1].running_command_name != "", 60)
        print("running command:", find_entry(claude_session_id)[1].running_command_name)
        move_result = connection_thread.submit_action(ActionRequest(
            "move_tab", {"tab_id": claude_tab_id}, {"target_window_id": target_window_id, "drop_before_tab_id": None})).result(15)
        results["move action succeeded"] = move_result.succeeded
        results["tab is in the other window with the same session"] = wait_until(
            lambda: find_entry(claude_session_id)[0] is not None and find_entry(claude_session_id)[0].window_id == target_window_id, 15)
        results["still working right after the move"] = find_entry(claude_session_id)[1].status in ("working",)
        results["turn ended after the command"] = wait_until(
            lambda: find_entry(claude_session_id)[1].status in ("idle", "done"), 90)
        results["screen shows the command's last number (19)"] = "19" in run_on_core_loop(read_screen(claude_session_id))

        _, _, codex_session_id = run_on_core_loop(open_window(
            "codex -c check_for_update_on_startup=false", CODEX_TRUSTED_FOLDER))
        results["codex listed as Codex"] = wait_until(
            lambda: find_entry(codex_session_id)[1] is not None and find_entry(codex_session_id)[1].agent_tool_name == "Codex", 60)
        time.sleep(3)  # its prompt box draws
        run_on_core_loop(type_prompt(codex_session_id, "Reply with just the word OK."))
        results["codex row shows working"] = wait_until(lambda: find_entry(codex_session_id)[1].status == "working", 60)
        results["codex back to idle or done"] = wait_until(lambda: find_entry(codex_session_id)[1].status in ("idle", "done"), 120)
        results["codex context size known"] = wait_until(lambda: find_entry(codex_session_id)[1].context_tokens, 40)
        print("codex context tokens:", find_entry(codex_session_id)[1].context_tokens)
        conversation_id = run_on_core_loop(core.agent_sessions[codex_session_id]["adapter"].find_conversation_id(
            core.agent_sessions[codex_session_id]["process_id"], None))
        results["codex conversation ID found (for Duplicate)"] = bool(conversation_id)
    finally:
        for check, passed in results.items():
            print(("PASS  " if passed else "FAIL  ") + check)

        async def close_windows():
            for window in windows:
                if core.app.get_window_by_id(window.window_id) is not None:  # the emptied first window closed itself
                    await window.async_close(force=True)
        run_on_core_loop(close_windows())
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
