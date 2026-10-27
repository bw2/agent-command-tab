"""End-to-end check of the app's hooks with a real Claude Code session in a throwaway iTerm2 window:
background task lights, child process rows, and the "exited" row of a crashed agent (user
requests, 2026-10-27).

Usage: hooks_and_exited_rows_live_check.py

Runs its own core (not the menu bar app) and a throwaway project folder whose own
.claude/settings.json holds the app's hooks (hooks_installer.add_the_apps_hooks), so your settings
are not changed. The hooks send their events to this check's own socket: their command sets HOME
to a short temporary folder, under which the forwarder looks for the socket, so the running app
doesn't get them. Claude Code (Haiku) asks to trust the folder once; the check answers. Steps:
1. Claude Code starts: its SessionStart event arrives from its own process.
2. It runs a command in the background: the row counts one background task, and lists the
   command among its child processes, marked as in the background.
3. Its process is killed (SIGKILL, as a crash): the row stays, "exited".
4. Claude Code starts again in that tab, and /exit ends it: its row goes, with no "exited" row.
Closes its window and ends the background command at the end.
"""

import asyncio
import json
import os
import signal
import sys
import tempfile
import threading
import time

import iterm2

from agent_command_tab.agent_process_tree import list_descendant_processes
from agent_command_tab.hooks_installer import HOOK_EVENTS_BY_AGENT_TOOL_NAME, add_the_apps_hooks, build_forwarder_command
from agent_command_tab.iterm2_connection_thread import Iterm2ConnectionThread, read_claude_code_session_status
from agent_command_tab.snapshot_data_classes import iterate_agent_session_entries

# Short, since a socket's path can't be longer than 104 bytes.
FORWARDER_HOME_FOLDER = tempfile.mkdtemp(prefix="hooks_check_", dir="/tmp")
SOCKET_PATH = os.path.join(FORWARDER_HOME_FOLDER, "Library/Application Support/agent-command-tab/hook_events.sock")
PROJECT_FOLDER = tempfile.mkdtemp(prefix="hooks_and_exited_rows_live_check_")
LAUNCH_COMMAND = "claude --model haiku"
# Not `sleep`: Claude Code 2.1.310 and later refuse a lone sleep of 25 seconds or more (PHASE0_RESULTS.md).
BACKGROUND_COMMAND = "python3 -c 'import time; time.sleep(300)'"
PROMPT = (f"LIVE CHECK. Use the Bash tool with run_in_background set to true to run exactly this command: "
          f"{BACKGROUND_COMMAND} Then reply with only the word STARTED, without waiting for it.")


def write_project_settings():
    """The project's own settings: the app's hooks, sending to this check's socket, and permission
    for the background command."""
    command = f"HOME={FORWARDER_HOME_FOLDER} {build_forwarder_command('Claude Code')}"
    settings = add_the_apps_hooks({"permissions": {"allow": ["Bash(python3:*)"]}}, command, HOOK_EVENTS_BY_AGENT_TOOL_NAME["Claude Code"])
    os.makedirs(os.path.join(PROJECT_FOLDER, ".claude"))
    with open(os.path.join(PROJECT_FOLDER, ".claude", "settings.json"), "w") as settings_file:
        json.dump(settings, settings_file, indent=2)


def main():
    write_project_settings()
    snapshots = []
    snapshot_arrived = threading.Condition()

    def publish_snapshot(snapshot):
        with snapshot_arrived:
            snapshots.append(snapshot)
            snapshot_arrived.notify_all()

    connection_thread = Iterm2ConnectionThread(publish_snapshot, lambda message: print("connection lost:", message),
                                               hook_events_socket_path=SOCKET_PATH)
    connection_thread.start()
    connection_thread.core_ready.wait()
    core = connection_thread.core
    while core.app is None:
        time.sleep(0.1)
    windows = []
    process_ids_to_end = []

    def run_on_core_loop(coroutine, timeout=60):
        return asyncio.run_coroutine_threadsafe(coroutine, connection_thread.event_loop).result(timeout)

    def entry_of(session_id):
        return next((entry for _, _, entry in iterate_agent_session_entries(snapshots[-1]) if entry.session_id == session_id),
                    None) if snapshots else None

    def wait_until(condition, timeout):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if condition():
                return True
            with snapshot_arrived:
                snapshot_arrived.wait(0.5)
        return condition()

    async def open_window():
        profile = iterm2.LocalWriteOnlyProfile({"Initial Text": LAUNCH_COMMAND})
        profile.set_initial_directory_mode(iterm2.InitialWorkingDirectory.INITIAL_WORKING_DIRECTORY_CUSTOM)
        profile.set_custom_directory(PROJECT_FOLDER)
        window = await iterm2.Window.async_create(core.connection, profile_customizations=profile)
        windows.append(window)
        return window.tabs[0].sessions[0].session_id

    async def type_into(session_id, text):
        await core.app.get_session_by_id(session_id).async_send_text(text, suppress_broadcast=True)

    async def read_screen_text(session_id):
        contents = await core.app.get_session_by_id(session_id).async_get_screen_contents()
        return "\n".join(contents.line(index).string.replace("\x00", " ") for index in range(contents.number_of_lines))

    def agent_process_id_of(session_id):
        agent_session = core.agent_sessions.get(session_id)
        return agent_session["process_id"] if agent_session else None

    def hook_state_of(session_id):
        return core.hook_state_by_agent_process_id.get(agent_process_id_of(session_id)) or {}

    def start_claude_code_and_wait_for_its_prompt(session_id):
        """Answers the folder trust question if it shows; returns whether the agent's SessionStart arrived."""
        def trust_question_answered_or_started():
            screen_text = run_on_core_loop(read_screen_text(session_id))
            if "Yes, I trust this folder" in screen_text:  # below "No, exit", which Enter alone would choose
                run_on_core_loop(type_into(session_id, "\x1b[B"))
                time.sleep(0.5)
                run_on_core_loop(type_into(session_id, "\r"))
                time.sleep(1)
            return hook_state_of(session_id).get("background_task_ids") is not None  # set by SessionStart's "startup"
        return wait_until(trust_question_answered_or_started, 60)

    results = {}
    try:
        session_id = run_on_core_loop(open_window())
        results["1. SessionStart arrived from the tab's own Claude Code"] = start_claude_code_and_wait_for_its_prompt(session_id)
        results["   the row is listed"] = wait_until(lambda: entry_of(session_id) is not None, 30)
        run_on_core_loop(type_into(session_id, PROMPT))
        time.sleep(0.5)
        run_on_core_loop(type_into(session_id, "\r"))
        results["2. one background task is counted"] = wait_until(
            lambda: entry_of(session_id) is not None and entry_of(session_id).background_task_count == 1, 120)
        results["   the background command is a child process, in the background"] = wait_until(
            lambda: entry_of(session_id) is not None and any(
                "time.sleep(300)" in child_process.command_text and child_process.is_background_shell
                for child_process in entry_of(session_id).child_processes), 60)
        print("child processes:", [(child_process.command_text, child_process.is_background_shell)
                                   for child_process in entry_of(session_id).child_processes])
        for child_process in entry_of(session_id).child_processes:  # the shell and the python3 in it
            if "time.sleep(300)" in child_process.command_text:
                process_ids_to_end.extend([child_process.process_id] + [
                    process.process_id for process in list_descendant_processes(child_process.process_id)])
        statuses_seen = []

        def turn_ended():
            entry = entry_of(session_id)
            if not statuses_seen or statuses_seen[-1] != (entry.status, entry.running_command_name, entry.status_reason):
                statuses_seen.append((entry.status, entry.running_command_name, entry.status_reason))
            return entry.status in ("idle", "done")
        results["   the turn ended"] = wait_until(turn_ended, 60)
        print("statuses seen:", statuses_seen)

        async def read_status_sources():
            session = core.app.get_session_by_id(session_id)
            return (await session.async_get_variable("status"),
                    await session.async_invoke_function(f'iterm2.get_session_status(session_id: "{session_id}")'))
        print("iTerm2's status variable and tab bar:", run_on_core_loop(read_status_sources()))
        print("Claude Code's own report:", read_claude_code_session_status(None, agent_process_id_of(session_id)))
        os.kill(agent_process_id_of(session_id), signal.SIGKILL)
        results["3. the killed agent's row shows exited"] = wait_until(
            lambda: entry_of(session_id) is not None and entry_of(session_id).status == "exited", 30)
        if entry_of(session_id) is not None:
            print("exited row:", entry_of(session_id).status, entry_of(session_id).ended_abnormally_at)
        time.sleep(2)  # the shell is back at its prompt
        run_on_core_loop(type_into(session_id, LAUNCH_COMMAND + "\r"))
        results["4. Claude Code started again in the tab"] = wait_until(
            lambda: entry_of(session_id) is not None and entry_of(session_id).status != "exited"
            and hook_state_of(session_id).get("background_task_ids") is not None, 60)
        run_on_core_loop(type_into(session_id, "/exit"))
        time.sleep(1)
        run_on_core_loop(type_into(session_id, "\r"))
        results["   after /exit its row goes"] = wait_until(lambda: entry_of(session_id) is None, 30)
        time.sleep(5)
        results["   and no exited row comes"] = entry_of(session_id) is None
    finally:
        for check, passed in results.items():
            print(("PASS  " if passed else "FAIL  ") + check)
        for process_id in process_ids_to_end:
            try:
                os.kill(process_id, signal.SIGTERM)
            except ProcessLookupError:
                pass

        async def close_windows():
            for window in windows:
                if core.app.get_window_by_id(window.window_id) is not None:
                    await window.async_close(force=True)
        run_on_core_loop(close_windows())
    return 0 if results and all(results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
