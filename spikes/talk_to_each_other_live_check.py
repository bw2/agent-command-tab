"""End-to-end check of Talk to Each Other with real agent sessions in throwaway iTerm2 windows.

Usage: talk_to_each_other_live_check.py CLAUDE_CODE_FOLDER CODEX_FOLDER   (folders each tool already trusts)

Starts a Claude Code (Haiku) session idle at its prompt, a Codex session that has answered a
first prompt (so it has a conversation ID), and then a second Claude Code session running a
25-second command.
Then one ask_agent_sessions_to_talk_to_each_other action asks each to reply RECEIVED with the
addresses of the others, without messaging anyone:
1. the idle Claude Code session gets its message at once, and the busy one only after its command;
2. the Codex session gets its message through `codex queue`;
3. each one answers with the addresses the others' messages gave for its peers.
Closes its windows at the end. Types only into its own windows.
"""

import asyncio
import os
import sys
import threading
import time

import iterm2

from agent_command_tab import iterm2_connection_thread
from agent_command_tab.dashboard_core_api import ActionRequest
from agent_command_tab.iterm2_connection_thread import Iterm2ConnectionThread
from agent_command_tab.snapshot_data_classes import iterate_agent_session_entries

CLAUDE_CODE_FOLDER = os.path.realpath(sys.argv[1])
CODEX_FOLDER = os.path.realpath(sys.argv[2])
CLAUDE_IDLE_LAUNCH_COMMAND = "claude --model haiku"
# Not `sleep 25`: Claude Code 2.1.310 and later refuse a lone sleep of 25 seconds or more (PHASE0_RESULTS.md).
CLAUDE_BUSY_LAUNCH_COMMAND = ("claude --model haiku 'Use the Bash tool to run exactly this command: "
                              "python3 -c \"import time; time.sleep(25)\"'")
CODEX_LAUNCH_COMMAND = 'codex -s read-only -a never -c check_for_update_on_startup=false "Reply with only the word READY."'
# The answer starts with "received" in lower case, which the note itself never shows, so the
# note on screen is never taken for the answer.
NOTE = ("LIVE CHECK of a dashboard feature. Do not message anyone and do not run any commands. Reply with only the "
        "word RECEIVED in lower case letters, then the addresses of the other sessions listed below (a Claude Code "
        "session's name, a Codex session's conversation ID), separated by spaces.")
SECONDS_TO_HOLD_A_MESSAGE_IN_THIS_CHECK = 240  # rather than 30 minutes, should a permission prompt hold up the busy session


def main():
    iterm2_connection_thread.SECONDS_A_MESSAGE_WAITS_FOR_AN_AGENT_AT_ITS_PROMPT = SECONDS_TO_HOLD_A_MESSAGE_IN_THIS_CHECK
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

    def status_of(session_id):
        return next((entry.status for _, _, entry in iterate_agent_session_entries(snapshots[-1]) if entry.session_id == session_id),
                    None) if snapshots else None

    def wait_until(condition, timeout):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if condition():
                return True
            with snapshot_arrived:
                snapshot_arrived.wait(0.5)
        return condition()

    async def open_window(folder, launch_command):
        profile = iterm2.LocalWriteOnlyProfile({"Initial Text": launch_command})
        profile.set_initial_directory_mode(iterm2.InitialWorkingDirectory.INITIAL_WORKING_DIRECTORY_CUSTOM)
        profile.set_custom_directory(folder)
        window = await iterm2.Window.async_create(core.connection, profile_customizations=profile)
        windows.append(window)
        return window.tabs[0].sessions[0].session_id

    async def read_screen_lines(session_id):
        contents = await core.app.get_session_by_id(session_id).async_get_screen_contents()
        return [contents.line(index).string.replace("\x00", " ") for index in range(contents.number_of_lines)]

    def find_answer_line(session_id):
        """The agent's answer: the last line holding "received", with the 3 lines after it (a
        long answer wraps), or None."""
        lines = run_on_core_loop(read_screen_lines(session_id))
        index = next((index for index in reversed(range(len(lines))) if "received" in lines[index]), None)
        return " ".join(lines[index:index + 4]) if index is not None else None

    def find_address(session_id):
        agent_session_for_messages, _ = run_on_core_loop(core.describe_agent_session_for_messages(session_id))
        return agent_session_for_messages.address if agent_session_for_messages is not None else None

    def screen_shows(session_id, text):
        return any(text in line for line in run_on_core_loop(read_screen_lines(session_id)))

    results = {}
    try:
        idle_session_id = run_on_core_loop(open_window(CLAUDE_CODE_FOLDER, CLAUDE_IDLE_LAUNCH_COMMAND))
        codex_session_id = run_on_core_loop(open_window(CODEX_FOLDER, CODEX_LAUNCH_COMMAND))
        # Codex 0.155 doesn't report its state to iTerm2, so its answer is looked for on screen.
        results["Codex answered its first prompt"] = wait_until(lambda: screen_shows(codex_session_id, "• READY"), 120)
        # Started last, so its 25-second command still runs when the message is sent.
        busy_session_id = run_on_core_loop(open_window(CLAUDE_CODE_FOLDER, CLAUDE_BUSY_LAUNCH_COMMAND))
        session_ids = [idle_session_id, busy_session_id, codex_session_id]
        results["every session has its address"] = wait_until(lambda: all(find_address(session_id) for session_id in session_ids), 60)
        results["the busy Claude Code session is working"] = wait_until(lambda: status_of(busy_session_id) == "working", 60)
        print("states when sent:", [status_of(session_id) for session_id in session_ids])
        address_by_session_id = {session_id: find_address(session_id) for session_id in session_ids}
        print("addresses:", address_by_session_id)

        sent_at = time.time()
        action_future = connection_thread.submit_action(ActionRequest(
            "ask_agent_sessions_to_talk_to_each_other", {"session_ids": session_ids}, {"note": NOTE}))
        results["the idle Claude Code session answers before the busy one's command ends"] = wait_until(
            lambda: find_answer_line(idle_session_id) is not None, 20)
        results["the busy Claude Code session hasn't got it yet"] = find_answer_line(busy_session_id) is None
        action_result = action_future.result(SECONDS_TO_HOLD_A_MESSAGE_IN_THIS_CHECK + 30)
        print(f"action: {action_result} after {time.time() - sent_at:.0f} s")
        results["the action succeeded"] = action_result.succeeded
        for session_id in session_ids:
            label = {idle_session_id: "idle Claude Code", busy_session_id: "busy Claude Code", codex_session_id: "Codex"}[session_id]
            results[f"{label} answered"] = wait_until(lambda: find_answer_line(session_id) is not None, 90)
            answer_without_spaces = "".join((find_answer_line(session_id) or "").split())
            print(f"{label}: {answer_without_spaces}")
            results[f"{label} named each of the others"] = all(
                address_by_session_id[other_session_id] in answer_without_spaces
                for other_session_id in session_ids if other_session_id != session_id)
    finally:
        for check, passed in results.items():
            print(("PASS  " if passed else "FAIL  ") + check)

        async def close_windows():
            for window in windows:
                if core.app.get_window_by_id(window.window_id) is not None:
                    await window.async_close(force=True)
        run_on_core_loop(close_windows())
    return 0 if results and all(results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
