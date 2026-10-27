"""End-to-end check of /compact from the menu of several selected rows, for agent sessions in the middle of a turn.

Usage: slash_commands_for_busy_sessions_live_check.py CLAUDE_CODE_FOLDER CODEX_FOLDER   (folders each tool already trusts)

Starts a Claude Code (Haiku) session and a Codex session in throwaway iTerm2 windows, each
running a 30-second command, then sends one send_slash_command_to_agent_sessions action with
/compact while both are busy:
1. Claude Code gets it at once and shows it among its queued messages;
2. Codex, which refuses commands during a task, gets it only after its turn;
3. both compact their conversations.
Closes its windows at the end. Types only into its own windows.
"""

import asyncio
import os
import sys
import threading
import time

import iterm2

from agent_command_tab.agent_process_tree import list_descendant_processes, read_process_arguments_and_environment
from agent_command_tab.dashboard_core_api import ActionRequest
from agent_command_tab.iterm2_connection_thread import Iterm2ConnectionThread

CLAUDE_CODE_FOLDER = os.path.realpath(sys.argv[1])
CODEX_FOLDER = os.path.realpath(sys.argv[2])
CLAUDE_BUSY_LAUNCH_COMMAND = ("claude --model haiku 'Use the Bash tool to run exactly this command: "
                              "python3 -c \"import time; time.sleep(30)\" then reply with only the word FINISHED.'")
CODEX_BUSY_LAUNCH_COMMAND = ("codex -s read-only -a never -c check_for_update_on_startup=false "
                             "'Run exactly this shell command inside the sandbox (it needs no escalation): sleep 30. "
                             "Then reply with only the word FINISHED.'")


def main():
    connection_thread = Iterm2ConnectionThread(lambda snapshot: None, lambda message: print("connection lost:", message))
    connection_thread.start()
    connection_thread.core_ready.wait()
    core = connection_thread.core
    while core.app is None:
        time.sleep(0.1)
    windows = []

    def run_on_core_loop(coroutine, timeout=60):
        return asyncio.run_coroutine_threadsafe(coroutine, connection_thread.event_loop).result(timeout)

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

    def screen_shows(session_id, text):
        return any(text in line for line in run_on_core_loop(read_screen_lines(session_id)))

    def claude_code_runs_its_command():
        # Looked for below the agent: the command's text is on screen before it runs, in the launch
        # command and in Claude Code's echo of the prompt.
        agent_session = core.agent_sessions.get(claude_session_id)
        return bool(agent_session) and any(
            "import time; time.sleep(30)" in read_process_arguments_and_environment(process.process_id)[0]
            for process in list_descendant_processes(agent_session["process_id"]))

    def wait_until(condition, timeout):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if condition():
                return True
            time.sleep(0.5)
        return condition()

    results = {}
    try:
        claude_session_id = run_on_core_loop(open_window(CLAUDE_CODE_FOLDER, CLAUDE_BUSY_LAUNCH_COMMAND))
        codex_session_id = run_on_core_loop(open_window(CODEX_FOLDER, CODEX_BUSY_LAUNCH_COMMAND))
        results["both agent sessions are found"] = wait_until(
            lambda: core.agent_sessions.get(claude_session_id) and core.agent_sessions.get(codex_session_id), 40)
        results["both run their command"] = wait_until(
            lambda: claude_code_runs_its_command() and screen_shows(codex_session_id, "esc to interrupt"), 40)
        action_result_by_name = {}
        action_done = threading.Event()

        def send():
            action_result_by_name["result"] = connection_thread.submit_action(ActionRequest(
                "send_slash_command_to_agent_sessions", {"session_ids": [claude_session_id, codex_session_id]},
                {"command": "/compact"})).result(300)
            action_done.set()
        threading.Thread(target=send, daemon=True).start()
        results["Claude Code queues it during its turn"] = wait_until(
            lambda: screen_shows(claude_session_id, "Press up to edit queued messages"), 15)
        results["Codex didn't refuse it"] = not screen_shows(codex_session_id, "is disabled while a task is in progress")
        results["the action ends once Codex got it"] = action_done.wait(200)
        print("action:", action_result_by_name.get("result"))
        results["the action succeeded"] = bool(action_result_by_name.get("result") and action_result_by_name["result"].succeeded)
        results["Codex never refused it"] = not screen_shows(codex_session_id, "is disabled while a task is in progress")
        results["Claude Code compacted"] = wait_until(lambda: screen_shows(claude_session_id, "Compacted"), 120)
        results["Codex compacted"] = wait_until(lambda: screen_shows(codex_session_id, "Context compacted"), 120)
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
