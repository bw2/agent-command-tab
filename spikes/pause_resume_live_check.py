"""End-to-end check of Pause and Resume with real agent sessions in throwaway iTerm2 windows.

Usage: pause_resume_live_check.py TEST_FOLDER   (a folder both Claude Code and Codex already trust)

1. Claude Code (Haiku) runs a 120-second counting command and Codex writes a long answer. Both
   are paused with one pause_agent_sessions action: both rows show "paused", and the counting
   command's process is stopped.
2. After 20 seconds, one resume_agent_sessions action resumes both: the rows leave "paused",
   the counting process runs again, and the count goes on past where it stopped.
3. Claude Code is paused again, text is typed at the shell prompt in its tab, and resume
   refuses without typing anything; once that text is deleted again, resume works.
Closes its windows at the end. Types only into its own windows.
"""

import asyncio
import os
import re
import sys
import threading
import time

import iterm2

from agent_command_tab.agent_process_tree import list_descendant_processes, process_is_stopped
from agent_command_tab.agent_session_pausing import read_text_around_the_cursor
from agent_command_tab.dashboard_core_api import ActionRequest
from agent_command_tab.iterm2_connection_thread import Iterm2ConnectionThread
from agent_command_tab.snapshot_data_classes import iterate_agent_session_entries

TEST_FOLDER = os.path.realpath(sys.argv[1])
COUNTING_COMMAND = "python3 -c 'import time; [print(i, flush=True) or time.sleep(1) for i in range(120)]'"
CLAUDE_LAUNCH_COMMAND = f'claude --model haiku "Use the Bash tool to run exactly this command: {COUNTING_COMMAND}"'
CODEX_LAUNCH_COMMAND = ('codex -s read-only -a never -c check_for_update_on_startup=false '
                        '"Write the numbers from 1 to 3000, one per line, and nothing else. Do not run any commands."')


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
        for _, _, entry in iterate_agent_session_entries(snapshots[-1]) if snapshots else []:
            if entry.session_id == session_id:
                return entry
        return None

    def status_of(session_id):
        entry = find_entry(session_id)
        return entry.status if entry is not None else None

    def wait_until(condition, timeout):
        """Checks condition() without holding the snapshot lock: some conditions wait on the core,
        which takes that lock to publish a snapshot."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if condition():
                return True
            with snapshot_arrived:
                snapshot_arrived.wait(0.5)
        return condition()

    async def open_window(initial_text):
        profile = iterm2.LocalWriteOnlyProfile({"Initial Text": initial_text})
        profile.set_initial_directory_mode(iterm2.InitialWorkingDirectory.INITIAL_WORKING_DIRECTORY_CUSTOM)
        profile.set_custom_directory(TEST_FOLDER)
        window = await iterm2.Window.async_create(core.connection, profile_customizations=profile)
        windows.append(window)
        return window.tabs[0].sessions[0].session_id

    async def type_text(session_id, text):
        await core.app.get_session_by_id(session_id).async_send_text(text, suppress_broadcast=True)

    async def read_largest_number_on_screen(session_id):
        contents = await core.app.get_session_by_id(session_id).async_get_screen_contents()
        lines = [contents.line(index).string.replace("\x00", " ").strip().lstrip("⎿").strip()
                 for index in range(contents.number_of_lines)]
        return max((int(line) for line in lines if re.fullmatch(r"\d{1,4}", line)), default=None)

    def find_counting_process_id(session_id):
        agent_process_id = core.agent_sessions.get(session_id, {}).get("process_id")
        return next((process.process_id for process in list_descendant_processes(agent_process_id or 0, maximum_depth=None)
                     if "python" in process.name.lower()), None) if agent_process_id else None

    def submit(action_name, session_ids):
        return connection_thread.submit_action(ActionRequest(action_name, {"session_ids": session_ids})).result(30)

    results = {}
    try:
        claude_session_id = run_on_core_loop(open_window(CLAUDE_LAUNCH_COMMAND))
        codex_session_id = run_on_core_loop(open_window(CODEX_LAUNCH_COMMAND))
        results["claude's counting command runs"] = wait_until(lambda: find_counting_process_id(claude_session_id), 120)
        results["codex is writing numbers"] = wait_until(
            lambda: (run_on_core_loop(read_largest_number_on_screen(codex_session_id)) or 0) >= 20, 180)
        time.sleep(5)
        counting_process_id = find_counting_process_id(claude_session_id)

        pause_result = submit("pause_agent_sessions", [claude_session_id, codex_session_id])
        results["pause action succeeded"] = pause_result.succeeded
        print("pause:", pause_result)
        results["both rows show paused"] = wait_until(
            lambda: status_of(claude_session_id) == "paused" and status_of(codex_session_id) == "paused", 15)
        results["the counting command is stopped"] = bool(counting_process_id) and process_is_stopped(counting_process_id)
        number_at_pause = run_on_core_loop(read_largest_number_on_screen(codex_session_id))
        time.sleep(20)
        results["codex wrote nothing while paused"] = (
            run_on_core_loop(read_largest_number_on_screen(codex_session_id)) == number_at_pause)

        resume_result = submit("resume_agent_sessions", [claude_session_id, codex_session_id])
        results["resume action succeeded"] = resume_result.succeeded
        print("resume:", resume_result)
        results["both rows left paused"] = wait_until(
            lambda: status_of(claude_session_id) not in ("paused", None) and status_of(codex_session_id) not in ("paused", None), 15)
        results["the counting command runs again"] = not process_is_stopped(counting_process_id)
        results["codex carries on writing"] = wait_until(
            lambda: (run_on_core_loop(read_largest_number_on_screen(codex_session_id)) or 0) > (number_at_pause or 0), 60)

        results["second pause succeeded"] = submit("pause_agent_sessions", [claude_session_id]).succeeded
        typed_text = "echo typed-at-the-prompt"
        run_on_core_loop(type_text(claude_session_id, typed_text))
        time.sleep(0.5)
        refused = submit("resume_agent_sessions", [claude_session_id])
        print("resume with text at the prompt:", refused)
        results["resume refuses while text is typed at the prompt"] = not refused.succeeded and "type fg" in refused.message
        results["the agent stays paused then"] = process_is_stopped(core.agent_sessions[claude_session_id]["process_id"])
        # Deletes just the typed text, so the line looks as right after the pause, which may have
        # left a terminal reply there (Resume's Ctrl+U clears that).
        run_on_core_loop(type_text(claude_session_id, "\x7f" * len(typed_text)))
        time.sleep(0.5)
        print("prompt recorded at the pause:", core.pause_record_by_tab_and_agent[
            (claude_session_id, core.agent_sessions[claude_session_id]["process_id"])].text_around_the_cursor)
        print("cursor line now:", run_on_core_loop(read_text_around_the_cursor(core.app.get_session_by_id(claude_session_id))))
        results["resume works once the line is cleared"] = submit("resume_agent_sessions", [claude_session_id]).succeeded
        results["the agent runs again"] = wait_until(lambda: status_of(claude_session_id) not in ("paused", None), 15)
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
