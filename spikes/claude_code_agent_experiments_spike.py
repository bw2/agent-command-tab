"""Phase 0 experiments with a live Claude Code session in throwaway iTerm2 windows.

Uses a temporary project folder whose own .claude/settings.json installs logging hooks
(the user's global settings are not changed). Runs Claude Code on the Haiku model with
permission prompts on, drives it by typing into its own test tab, and records:
- the tab's jobName, commandLine and `status` variable over time;
- every hook event with its arrival time, parent process, ITERM_SESSION_ID and ids;
- the screen at each step.

Steps: a tool call that needs permission (approved) while the tab is moved to another
window and renamed; an interrupted turn (Esc); a denied tool call with a comment; a nested
`claude -p`; then /exit. Prints a summary and writes the full record to the output folder.
"""

import asyncio
import json
import os
import sys
import textwrap
import time

import iterm2
import iterm2.auth

iterm2.auth.gAppKitAvailable = False

OUTPUT_FOLDER = sys.argv[1]
TEST_PROJECT_FOLDER = os.path.join(OUTPUT_FOLDER, "phase0_test_project")
HOOK_LOG_PATH = os.path.join(OUTPUT_FOLDER, "hook_events.jsonl")
TIMELINE_PATH = os.path.join(OUTPUT_FOLDER, "timeline.jsonl")
HOOK_EVENTS = ["SessionStart", "UserPromptSubmit", "PreToolUse", "PermissionRequest", "PostToolUse",
               "PostToolUseFailure", "Stop", "StopFailure", "SessionEnd", "Notification", "SubagentStop"]

HOOK_LOGGER_SOURCE = textwrap.dedent('''
    import json, os, sys, time
    received_at = time.time()
    event = json.loads(sys.stdin.read() or "{}")
    record = {
        "received_at": received_at,
        "registered_as": sys.argv[1],
        "hook_event_name": event.get("hook_event_name"),
        "session_id": event.get("session_id"),
        "agent_id": event.get("agent_id"),
        "tool_name": event.get("tool_name"),
        "tool_use_id": event.get("tool_use_id"),
        "tool_input_command": (event.get("tool_input") or {}).get("command") if isinstance(event.get("tool_input"), dict) else None,
        "reason": event.get("reason"),
        "notification_type": event.get("notification_type"),
        "message": (event.get("message") or "")[:200],
        "has_last_assistant_message": bool(event.get("last_assistant_message")),
        "payload_keys": sorted(event.keys()),
        "parent_pid": os.getppid(),
        "iterm_session_id": os.environ.get("ITERM_SESSION_ID"),
    }
    with open(sys.argv[2], "a") as log_file:
        log_file.write(json.dumps(record) + "\\n")
''')


def write_test_project_with_logging_hooks():
    """Creates the test project folder, its hook logger and its project-level hook settings."""
    os.makedirs(os.path.join(TEST_PROJECT_FOLDER, ".claude"), exist_ok=True)
    logger_path = os.path.join(OUTPUT_FOLDER, "hook_logger.py")
    with open(logger_path, "w") as logger_file:
        logger_file.write(HOOK_LOGGER_SOURCE)
    hooks = {event: [{"matcher": "", "hooks": [{"type": "command", "async": True, "timeout": 10,
                                                  "command": f"/usr/bin/python3 -I -S {logger_path} {event} {HOOK_LOG_PATH}"}]}]
             for event in HOOK_EVENTS}
    with open(os.path.join(TEST_PROJECT_FOLDER, ".claude", "settings.json"), "w") as settings_file:
        json.dump({"hooks": hooks}, settings_file, indent=1)


class TimelineRecorder:
    """Polls the test session's variables and writes every change, plus named marks, to the timeline."""

    def __init__(self, session):
        self.session = session
        self.last_values = None
        self.stopped = False

    def write(self, record):
        """Appends one timeline record with the current time."""
        record["time"] = time.time()
        with open(TIMELINE_PATH, "a") as timeline_file:
            timeline_file.write(json.dumps(record, default=str) + "\n")

    async def poll_forever(self):
        """Records jobName, commandLine and status whenever one of them changes."""
        while not self.stopped:
            try:
                values = {name: await self.session.async_get_variable(name) for name in ("jobName", "commandLine", "status")}
            except Exception as error:
                values = {"error": repr(error)}
            if values != self.last_values:
                self.write({"kind": "variables", **values})
                self.last_values = values
            await asyncio.sleep(0.3)

    async def mark(self, name):
        """Records a named step together with the bottom of the screen."""
        contents = await self.session.async_get_screen_contents()
        lines = [contents.line(index).string for index in range(contents.number_of_lines)]
        non_blank_lines = [line for line in lines if line.strip()]
        self.write({"kind": "mark", "step": name, "screen_bottom": non_blank_lines[-14:]})


async def wait_for_status(session, wanted_values, timeout_seconds):
    """Waits until the session's status variable is one of wanted_values; returns the value or None."""
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        value = await session.async_get_variable("status")
        if value in wanted_values:
            return value
        await asyncio.sleep(0.3)
    return None


async def screen_contains(session, text):
    """Returns whether the session's visible screen contains text."""
    contents = await session.async_get_screen_contents()
    return any(text in contents.line(index).string for index in range(contents.number_of_lines))


async def type_prompt_and_submit(session, prompt_text):
    """Types prompt_text into the agent's input box, then presses Enter separately."""
    await session.async_send_text(prompt_text, suppress_broadcast=True)
    await asyncio.sleep(0.3)
    await session.async_send_text("\r", suppress_broadcast=True)


async def run_experiments(connection):
    """Runs the Claude Code experiment steps and prints a summary."""
    write_test_project_with_logging_hooks()
    app = await iterm2.async_get_app(connection)
    original_window = app.current_terminal_window
    original_session = original_window.current_tab.current_session if original_window else None
    launch_command = f"cd {TEST_PROJECT_FOLDER} && claude --permission-mode default --model haiku --name phase0-test-claude"
    profile = iterm2.LocalWriteOnlyProfile({"Initial Text": launch_command})
    test_window = await iterm2.Window.async_create(connection, profile_customizations=profile)
    second_window = await iterm2.Window.async_create(connection)
    test_session_ids = {test_window.tabs[0].sessions[0].session_id, second_window.tabs[0].sessions[0].session_id}
    session = test_window.tabs[0].sessions[0]
    test_tab = test_window.tabs[0]
    recorder = TimelineRecorder(session)
    polling_task = asyncio.ensure_future(recorder.poll_forever())
    try:
        # Start-up: answer the folder trust question if it appears, then wait for the prompt.
        for _ in range(60):
            await asyncio.sleep(1)
            if await screen_contains(session, "trust"):
                await recorder.mark("trust question shown")
                await session.async_send_text("\r", suppress_broadcast=True)
                await asyncio.sleep(3)
                break
            if await session.async_get_variable("status") in ("idle", "done"):
                break
        await asyncio.sleep(3)
        await recorder.mark("ready")

        # Step 1: a tool call needing permission; approve it; move and rename the tab mid-turn.
        await type_prompt_and_submit(session, "Use the Bash tool to run exactly this command: sleep 6 && echo phase0-step1-done")
        status_after_prompt = await wait_for_status(session, ("waiting",), 60)
        await recorder.mark(f"step 1 permission prompt (status {status_after_prompt})")
        await session.async_send_text("1", suppress_broadcast=True)
        await asyncio.sleep(1.5)
        await recorder.mark("step 1 approved")
        await second_window.async_set_tabs([second_window.tabs[0], test_tab])
        await recorder.mark("step 1 tab moved to second window")
        await test_tab.async_set_title("phase0 renamed claude tab")
        await wait_for_status(session, ("idle", "done"), 90)
        await asyncio.sleep(2)
        await recorder.mark("step 1 finished")
        recorder.write({"kind": "tab title after turn", "title": await test_tab.async_get_variable("title"),
                        "titleOverrideFormat": await test_tab.async_get_variable("titleOverrideFormat")})

        # Step 2: interrupt a long command with Esc.
        await type_prompt_and_submit(session, "Use the Bash tool to run exactly this command: sleep 40")
        await wait_for_status(session, ("waiting",), 60)
        await session.async_send_text("1", suppress_broadcast=True)
        await asyncio.sleep(4)
        await recorder.mark("step 2 before Esc")
        await session.async_send_text("\x1b", suppress_broadcast=True)
        await asyncio.sleep(4)
        await recorder.mark("step 2 after Esc")

        # Step 3: deny a tool call with a comment.
        await type_prompt_and_submit(session, "Use the Bash tool to run exactly this command: echo phase0-step3-first")
        await wait_for_status(session, ("waiting",), 60)
        await recorder.mark("step 3 permission prompt")
        await session.async_send_text("3", suppress_broadcast=True)
        await asyncio.sleep(1.5)
        await recorder.mark("step 3 after choosing No")
        await type_prompt_and_submit(session, "run echo phase0-step3-second instead")
        await asyncio.sleep(6)
        await recorder.mark("step 3 after comment")
        if await session.async_get_variable("status") == "waiting":
            await session.async_send_text("1", suppress_broadcast=True)
        await wait_for_status(session, ("idle", "done"), 90)
        await recorder.mark("step 3 finished")

        # Step 4: a nested claude -p run started by the agent's Bash tool.
        await type_prompt_and_submit(session, "Use the Bash tool to run exactly this command: claude -p 'reply with the single word pong' --model haiku")
        await wait_for_status(session, ("waiting",), 60)
        await session.async_send_text("1", suppress_broadcast=True)
        await wait_for_status(session, ("idle", "done"), 120)
        await asyncio.sleep(2)
        await recorder.mark("step 4 finished")

        # Step 5: exit normally.
        await type_prompt_and_submit(session, "/exit")
        await asyncio.sleep(6)
        await recorder.mark("step 5 after /exit")
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
    print("timeline:", TIMELINE_PATH)
    print("hook events:", HOOK_LOG_PATH)


if __name__ == "__main__":
    iterm2.run_until_complete(run_experiments)
