"""Phase 0 experiments on permission prompts, moving a tab mid-tool, Esc and /exit with Claude Code.

Like claude_code_agent_experiments_spike.py (same throwaway test project and logging hooks),
but each step waits for the real hook events instead of fixed delays, and uses perl
commands, which need permission (the user's settings allow echo, sleep and python3 without asking).

Steps:
A. Approve a 10-second tool call; record the status variable right after approval; move the
   tab to another window while the tool runs; confirm the tool's output appears.
B. Approve a 40-second tool call, then press Esc; record hooks and status for 8 seconds.
C. At a permission prompt, record the menu, choose the "No" option, and give a comment if a
   text field appears; record what follows.
D. Type /exit; record when commandLine changes and when SessionEnd arrives.
"""

import asyncio
import json
import os
import re
import sys
import time

import iterm2
import iterm2.auth

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import claude_code_agent_experiments_spike as shared  # noqa: E402  (reuses its helpers, not its main run)

iterm2.auth.gAppKitAvailable = False


def read_hook_events_since(start_time):
    """Returns hook log records received at or after start_time."""
    if not os.path.exists(shared.HOOK_LOG_PATH):
        return []
    with open(shared.HOOK_LOG_PATH) as log_file:
        records = [json.loads(line) for line in log_file if line.strip()]
    return [record for record in records if record["received_at"] >= start_time]


async def wait_for_hook_event(event_name, start_time, timeout_seconds, session_id=None):
    """Waits for a hook event of the given name after start_time; returns it or None."""
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        for record in read_hook_events_since(start_time):
            if record["hook_event_name"] == event_name and (session_id is None or record["session_id"] == session_id):
                return record
        await asyncio.sleep(0.2)
    return None


async def read_screen_lines(session):
    """Returns the non-blank lines of the session's visible screen."""
    contents = await session.async_get_screen_contents()
    return [contents.line(index).string for index in range(contents.number_of_lines)
            if contents.line(index).string.strip()]


async def run_experiments(connection):
    """Runs steps A to D and records everything in the timeline and hook log."""
    shared.write_test_project_with_logging_hooks()
    app = await iterm2.async_get_app(connection)
    original_window = app.current_terminal_window
    original_session = original_window.current_tab.current_session if original_window else None
    launch_command = (f"cd {shared.TEST_PROJECT_FOLDER} && claude --permission-mode default --model haiku "
                      "--name phase0-test-claude-permissions")
    test_window = await iterm2.Window.async_create(
        connection, profile_customizations=iterm2.LocalWriteOnlyProfile({"Initial Text": launch_command}))
    second_window = await iterm2.Window.async_create(connection)
    test_session_ids = {test_window.tabs[0].sessions[0].session_id, second_window.tabs[0].sessions[0].session_id}
    session = test_window.tabs[0].sessions[0]
    test_tab = test_window.tabs[0]
    recorder = shared.TimelineRecorder(session)
    polling_task = asyncio.ensure_future(recorder.poll_forever())
    try:
        session_start = await wait_for_hook_event("SessionStart", 0, 60)
        main_session_id = session_start["session_id"] if session_start else None
        await asyncio.sleep(2)
        await recorder.mark("ready")

        # A: approve, then move the tab while the tool runs.
        step_start = time.time()
        await shared.type_prompt_and_submit(session, "Use the Bash tool to run exactly this command: "
                                            "perl -e 'sleep 10; print \"phase0-A-done\\n\"'")
        permission = await wait_for_hook_event("PermissionRequest", step_start, 60, main_session_id)
        await asyncio.sleep(0.8)
        await recorder.mark(f"A permission prompt (hook seen: {bool(permission)})")
        if not permission:
            raise RuntimeError("no permission prompt in step A; stopping so no stray keys are typed")
        await session.async_send_text("1", suppress_broadcast=True)
        for _ in range(8):
            await asyncio.sleep(0.25)
            recorder.write({"kind": "status right after approval", "status": await session.async_get_variable("status")})
        await second_window.async_set_tabs([second_window.tabs[0], test_tab])
        await recorder.mark("A tab moved while the tool runs")
        await wait_for_hook_event("Stop", step_start, 60, main_session_id)
        await asyncio.sleep(1)
        recorder.write({"kind": "A output seen after move",
                        "seen": any("phase0-A-done" in line for line in await read_screen_lines(session))})
        await recorder.mark("A finished")

        # B: approve a long tool call, then press Esc.
        step_start = time.time()
        await shared.type_prompt_and_submit(session, "Use the Bash tool to run exactly this command: "
                                            "perl -e 'sleep 40'")
        if not await wait_for_hook_event("PermissionRequest", step_start, 60, main_session_id):
            raise RuntimeError("no permission prompt in step B; stopping so no stray keys are typed")
        await asyncio.sleep(0.8)
        await session.async_send_text("1", suppress_broadcast=True)
        await asyncio.sleep(4)
        await recorder.mark("B before Esc")
        escape_time = time.time()
        await session.async_send_text("\x1b", suppress_broadcast=True)
        await asyncio.sleep(8)
        recorder.write({"kind": "B hooks after Esc",
                        "events": [record["hook_event_name"] for record in read_hook_events_since(escape_time)]})
        await recorder.mark("B 8 s after Esc")

        # C: deny with a comment.
        step_start = time.time()
        await shared.type_prompt_and_submit(session, "Use the Bash tool to run exactly this command: "
                                            "perl -e 'print \"phase0-C-first\\n\"'")
        if not await wait_for_hook_event("PermissionRequest", step_start, 60, main_session_id):
            raise RuntimeError("no permission prompt in step C; stopping so no stray keys are typed")
        await asyncio.sleep(0.8)
        menu_lines = await read_screen_lines(session)
        recorder.write({"kind": "C permission menu", "lines": menu_lines[-12:]})
        no_option_number = None
        for line in menu_lines:
            match = re.search(r"(\d)\.\s*No\b", line)
            if match:
                no_option_number = match.group(1)
        recorder.write({"kind": "C chosen No option", "number": no_option_number})
        denial_time = time.time()
        if no_option_number:
            await session.async_send_text(no_option_number, suppress_broadcast=True)
            await asyncio.sleep(1.5)
            await recorder.mark("C after choosing No")
            await shared.type_prompt_and_submit(session, "do not run it; just reply with the word skipped")
            await asyncio.sleep(10)
        recorder.write({"kind": "C hooks after denial",
                        "events": [(record["hook_event_name"], record.get("tool_input_command")) for record in read_hook_events_since(denial_time)]})
        await recorder.mark("C finished")

        # D: exit.
        exit_time = time.time()
        await shared.type_prompt_and_submit(session, "/exit")
        command_line_changed_at = None
        for _ in range(60):
            await asyncio.sleep(0.25)
            command_line = await session.async_get_variable("commandLine")
            if command_line and "claude" not in command_line:
                command_line_changed_at = time.time()
                break
        session_end = await wait_for_hook_event("SessionEnd", exit_time, 15, main_session_id)
        recorder.write({"kind": "D exit timing",
                        "commandLine changed after s": command_line_changed_at - exit_time if command_line_changed_at else None,
                        "SessionEnd after s": session_end["received_at"] - exit_time if session_end else None,
                        "SessionEnd reason": session_end.get("reason") if session_end else None})
    finally:
        recorder.stopped = True
        await polling_task
        await app.async_refresh()
        for window in app.terminal_windows:
            for tab in window.tabs:
                if tab.sessions and tab.sessions[0].session_id in test_session_ids:
                    await tab.async_close(force=True)
        if original_session is not None:
            await original_session.async_activate(select_tab=True, order_window_front=True)
    print("timeline:", shared.TIMELINE_PATH)


if __name__ == "__main__":
    iterm2.run_until_complete(run_experiments)
