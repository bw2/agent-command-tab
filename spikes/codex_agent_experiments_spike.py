"""Phase 0 experiments with a live Codex session in throwaway iTerm2 windows.

Uses a temporary project folder whose own .codex/hooks.json installs async logging hooks
for every Codex hook event. Codex only loads a project's hooks when the folder is trusted.
Codex 0.163 ignores a per-run `-c projects."<folder>".trust_level` override and asks "Trust
this folder?", so the script answers "Trust and continue", which adds one
`[projects."<folder>"]` entry to ~/.codex/config.toml; that entry is removed by hand after the
run (approved by the user). `--dangerously-bypass-hook-trust` lets the test folder's hooks run
without being approved in /hooks, so no hook approvals are written. The run also turns off
Codex's update check (`check_for_update_on_startup=false`) and never types while the screen
shows a menu: an earlier run typed Enter into Codex's "Update available" menu, which ran the
update. The script compares ~/.codex/config.toml before and after and reports any change.

Records the tab's jobName, commandLine and `status` variable over time, every hook event
(with all payload fields, long values shortened), and the screen at each step:
Codex 0.154 accepts only `-a on-request` or `-a never`, so the run uses the read-only sandbox and
asks the model to request escalated permissions, which makes Codex show its approval prompt.
A. Approve a 10-second command; record status right after approval; move the tab to another
   window while the command runs; confirm the command's output appears.
B. Approve a 40-second command, then press Esc; record hooks and status for 8 seconds.
C. At an approval prompt, record the menu, choose the "No" option, then type a new prompt.
D. While idle, queue a message with `codex queue --thread <session id>`; record what happens.
E. Type /quit; record when commandLine changes and when SessionEnd arrives.
"""

import asyncio
import json
import os
import re
import subprocess
import sys
import textwrap
import time

import iterm2
import iterm2.auth

iterm2.auth.gAppKitAvailable = False

OUTPUT_FOLDER = os.path.realpath(sys.argv[1])
TEST_PROJECT_FOLDER = os.path.join(OUTPUT_FOLDER, "phase0_codex_test_project")
HOOK_LOG_PATH = os.path.join(OUTPUT_FOLDER, "hook_events.jsonl")
TIMELINE_PATH = os.path.join(OUTPUT_FOLDER, "timeline.jsonl")
CODEX_CONFIG_PATH = os.path.expanduser("~/.codex/config.toml")
# "user" shows Codex's own approval menu; "auto_review" (this user's setting) lets a reviewer model decide.
APPROVALS_REVIEWER = sys.argv[2] if len(sys.argv) > 2 else "user"
CODEX_HOOK_EVENTS = ["SessionStart", "UserPromptSubmit", "PreToolUse", "PermissionRequest", "PostToolUse",
                     "PreCompact", "PostCompact", "Stop", "SubagentStart", "SubagentStop", "Interrupt", "SessionEnd"]
ESCALATED_COMMAND_REQUEST = ("Run exactly this shell command and nothing else, outside the sandbox, by requesting "
                             "escalated permissions for it (I will approve): ")
MENU_SCREEN_PATTERN = re.compile(r"Press enter to continue|^\s*›\s*\d\.")
APPROVAL_SCREEN_PATTERN = re.compile(r"(Would you like to run|Allow command|proceed\?|Yes, proceed)", re.IGNORECASE)

HOOK_LOGGER_SOURCE = textwrap.dedent('''
    import json, os, sys, time
    received_at = time.time()
    raw = sys.stdin.read()
    try:
        event = json.loads(raw or "{}")
    except ValueError:
        event = {"unparsed_payload": raw[:300]}
    def shorten(value):
        if isinstance(value, str):
            return value[:300]
        if isinstance(value, dict):
            return {key: shorten(item) for key, item in value.items()}
        if isinstance(value, list):
            return [shorten(item) for item in value[:10]]
        return value
    record = {"received_at": received_at, "registered_as": sys.argv[1], "parent_pid": os.getppid(),
              "iterm_session_id": os.environ.get("ITERM_SESSION_ID"), "payload": shorten(event)}
    with open(sys.argv[2], "a") as log_file:
        log_file.write(json.dumps(record) + "\\n")
''')


def write_test_project_with_logging_hooks():
    """Creates the test project folder, its hook logger and its .codex/hooks.json."""
    os.makedirs(os.path.join(TEST_PROJECT_FOLDER, ".codex"), exist_ok=True)
    logger_path = os.path.join(OUTPUT_FOLDER, "hook_logger.py")
    with open(logger_path, "w") as logger_file:
        logger_file.write(HOOK_LOGGER_SOURCE)
    hooks = {event: [{"hooks": [{"type": "command", "async": True, "timeout": 10,
                                 "command": f"/usr/bin/python3 -I -S {logger_path} {event} {HOOK_LOG_PATH}"}]}]
             for event in CODEX_HOOK_EVENTS}
    with open(os.path.join(TEST_PROJECT_FOLDER, ".codex", "hooks.json"), "w") as hooks_file:
        json.dump({"hooks": hooks}, hooks_file, indent=1)


def read_hook_events_since(start_time):
    """Returns hook log records received at or after start_time."""
    if not os.path.exists(HOOK_LOG_PATH):
        return []
    with open(HOOK_LOG_PATH) as log_file:
        records = [json.loads(line) for line in log_file if line.strip()]
    return [record for record in records if record["received_at"] >= start_time]


def hook_event_name(record):
    """Returns the event name a hook record reports (or the name it was registered under)."""
    return record["payload"].get("hook_event_name") or record["registered_as"]


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
        """Records jobName, commandLine, jobPid and status whenever one of them changes."""
        while not self.stopped:
            try:
                values = {name: await self.session.async_get_variable(name)
                          for name in ("jobName", "commandLine", "jobPid", "status")}
            except Exception as error:
                values = {"error": repr(error)}
            if values != self.last_values:
                self.write({"kind": "variables", **values})
                self.last_values = values
            await asyncio.sleep(0.3)

    async def mark(self, name):
        """Records a named step together with the bottom of the screen."""
        self.write({"kind": "mark", "step": name, "screen_bottom": (await read_screen_lines(self.session))[-16:]})


async def read_screen_lines(session):
    """Returns the non-blank lines of the session's visible screen."""
    contents = await session.async_get_screen_contents()
    return [contents.line(index).string for index in range(contents.number_of_lines)
            if contents.line(index).string.strip()]


async def wait_for_hook_event(event_name, start_time, timeout_seconds):
    """Waits for a hook event of the given name after start_time; returns it or None."""
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        for record in read_hook_events_since(start_time):
            if hook_event_name(record) == event_name:
                return record
        await asyncio.sleep(0.2)
    return None


async def wait_for_approval_prompt(session, start_time, timeout_seconds):
    """Waits until a PermissionRequest hook arrives or the screen shows an approval prompt.

    Returns a short description of what was seen first, or None on timeout.
    """
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if any(hook_event_name(record) == "PermissionRequest" for record in read_hook_events_since(start_time)):
            return "PermissionRequest hook"
        if any(APPROVAL_SCREEN_PATTERN.search(line) for line in (await read_screen_lines(session))[-20:]):
            return "approval text on screen"
        await asyncio.sleep(0.3)
    return None


async def type_prompt_and_submit(session, prompt_text):
    """Types prompt_text into the agent's input box, then presses Enter separately.

    Raises instead of typing when the bottom of the screen shows a menu (for example Codex's
    "Update available" menu, where Enter would run the update).
    """
    bottom_lines = (await read_screen_lines(session))[-12:]
    if any(MENU_SCREEN_PATTERN.search(line) for line in bottom_lines):
        raise RuntimeError(f"a menu is showing, so nothing was typed: {bottom_lines}")
    await session.async_send_text(prompt_text, suppress_broadcast=True)
    await asyncio.sleep(0.3)
    await session.async_send_text("\r", suppress_broadcast=True)


def find_option_key(menu_lines, option_pattern):
    """Returns the number or shortcut letter of the menu option matching option_pattern, or None."""
    for line in menu_lines:
        if re.search(option_pattern, line, re.IGNORECASE):
            shortcut = re.search(r"\((\w)\)", line)
            if shortcut:
                return shortcut.group(1)
            number = re.search(r"(\d)\.", line)
            if number:
                return number.group(1)
    return None


def list_child_processes_with_open_session_files(parent_pid):
    """Returns the descendants of parent_pid and the Codex session files each has open."""
    process_table = subprocess.run(["ps", "-A", "-o", "pid=,ppid=,command="], capture_output=True, text=True).stdout
    children_by_parent = {}
    commands = {}
    for line in process_table.splitlines():
        fields = line.split(None, 2)
        if len(fields) == 3:
            children_by_parent.setdefault(int(fields[1]), []).append(int(fields[0]))
            commands[int(fields[0])] = fields[2][:200]
    found = []
    pending = [parent_pid]
    while pending:
        pid = pending.pop()
        open_files = subprocess.run(["lsof", "-p", str(pid), "-Fn"], capture_output=True, text=True).stdout
        session_files = [line[1:] for line in open_files.splitlines() if line.startswith("n") and "/.codex/" in line]
        found.append({"pid": pid, "command": commands.get(pid), "open_codex_files": session_files[:10]})
        pending.extend(children_by_parent.get(pid, []))
    return found


async def run_experiments(connection):
    """Runs steps A to E and records everything in the timeline and hook log."""
    write_test_project_with_logging_hooks()
    with open(CODEX_CONFIG_PATH) as config_file:
        codex_config_before = config_file.read()
    app = await iterm2.async_get_app(connection)
    original_window = app.current_terminal_window
    original_session = original_window.current_tab.current_session if original_window else None
    launch_command = (f"cd {TEST_PROJECT_FOLDER} && codex --dangerously-bypass-hook-trust "
                      "-c 'model_reasoning_effort=\"low\"' -c check_for_update_on_startup=false "
                      f"-c 'approvals_reviewer=\"{APPROVALS_REVIEWER}\"' -a on-request -s read-only")
    test_window = await iterm2.Window.async_create(
        connection, profile_customizations=iterm2.LocalWriteOnlyProfile({"Initial Text": launch_command}))
    second_window = await iterm2.Window.async_create(connection)
    test_session_ids = {test_window.tabs[0].sessions[0].session_id, second_window.tabs[0].sessions[0].session_id}
    session = test_window.tabs[0].sessions[0]
    test_tab = test_window.tabs[0]
    recorder = TimelineRecorder(session)
    polling_task = asyncio.ensure_future(recorder.poll_forever())
    try:
        # Start-up: answer the folder trust question (only if its first option is selected), then
        # wait for the input box.
        for _ in range(30):
            await asyncio.sleep(1)
            screen_lines = await read_screen_lines(session)
            if any("Trust this folder?" in line for line in screen_lines):
                if not any(re.search(r"^\s*›\s*1\.\s*Trust and continue", line) for line in screen_lines):
                    raise RuntimeError(f"unexpected trust menu, nothing typed: {screen_lines[-6:]}")
                await recorder.mark("trust question shown")
                await session.async_send_text("\r", suppress_broadcast=True)
                break
        session_start = await wait_for_hook_event("SessionStart", 0, 25)
        await asyncio.sleep(2)
        await recorder.mark(f"ready (SessionStart hook seen: {bool(session_start)})")
        if "codex" not in (await session.async_get_variable("commandLine") or ""):
            raise RuntimeError("Codex is not running in the test tab; stopping")
        codex_session_id = (session_start or {}).get("payload", {}).get("session_id")
        recorder.write({"kind": "processes at start",
                        "processes": list_child_processes_with_open_session_files(await session.async_get_variable("jobPid") or 0)})

        # A: approve, then move the tab while the command runs.
        step_start = time.time()
        await type_prompt_and_submit(session, ESCALATED_COMMAND_REQUEST + "perl -e 'sleep 10; print \"phase0-codex-A-done\\n\"'")
        seen = await wait_for_approval_prompt(session, step_start, 90)
        await asyncio.sleep(0.8)
        menu_lines = await read_screen_lines(session)
        await recorder.mark(f"A approval prompt ({seen})")
        if not seen:
            raise RuntimeError("no approval prompt in step A; stopping so no stray keys are typed")
        approve_key = find_option_key(menu_lines[-12:], r"Yes, proceed|^\W*1\.\s*Yes") or "y"
        recorder.write({"kind": "A approve key", "key": approve_key, "status before approval": await session.async_get_variable("status")})
        await session.async_send_text(approve_key, suppress_broadcast=True)
        for _ in range(8):
            await asyncio.sleep(0.25)
            recorder.write({"kind": "status right after approval", "status": await session.async_get_variable("status")})
        await second_window.async_set_tabs([second_window.tabs[0], test_tab])
        await recorder.mark("A tab moved while the command runs")
        await wait_for_hook_event("Stop", step_start, 90)
        await asyncio.sleep(1)
        recorder.write({"kind": "A output seen after move",
                        "seen": any("phase0-codex-A-done" in line for line in await read_screen_lines(session))})
        await recorder.mark("A finished")

        # B: approve a long command, then press Esc.
        step_start = time.time()
        await type_prompt_and_submit(session, ESCALATED_COMMAND_REQUEST + "perl -e 'sleep 40'")
        if not await wait_for_approval_prompt(session, step_start, 90):
            raise RuntimeError("no approval prompt in step B; stopping so no stray keys are typed")
        await asyncio.sleep(0.8)
        await session.async_send_text(find_option_key((await read_screen_lines(session))[-12:], r"Yes, proceed|^\W*1\.\s*Yes") or "y",
                                      suppress_broadcast=True)
        await asyncio.sleep(4)
        await recorder.mark("B before Esc")
        escape_time = time.time()
        await session.async_send_text("\x1b", suppress_broadcast=True)
        await asyncio.sleep(8)
        recorder.write({"kind": "B hooks after Esc", "events": [hook_event_name(record) for record in read_hook_events_since(escape_time)]})
        await recorder.mark("B 8 s after Esc")

        # C: deny, then type a new prompt.
        step_start = time.time()
        await type_prompt_and_submit(session, ESCALATED_COMMAND_REQUEST + "perl -e 'print \"phase0-codex-C-first\\n\"'")
        if not await wait_for_approval_prompt(session, step_start, 90):
            raise RuntimeError("no approval prompt in step C; stopping so no stray keys are typed")
        await asyncio.sleep(0.8)
        menu_lines = await read_screen_lines(session)
        no_key = find_option_key(menu_lines[-12:], r"\bNo\b")
        recorder.write({"kind": "C approval menu", "lines": menu_lines[-14:], "chosen No key": no_key})
        denial_time = time.time()
        await session.async_send_text(no_key or "\x1b", suppress_broadcast=True)
        await asyncio.sleep(2)
        await recorder.mark("C after choosing No")
        await type_prompt_and_submit(session, "do not run anything; just reply with the word skipped")
        await wait_for_hook_event("Stop", denial_time + 2.5, 60)
        await asyncio.sleep(1)
        recorder.write({"kind": "C hooks after denial", "events": [hook_event_name(record) for record in read_hook_events_since(denial_time)]})
        await recorder.mark("C finished")

        # D: queue a message from outside while the session is idle.
        queue_time = time.time()
        codex_session_id = codex_session_id or next(
            (record["payload"]["session_id"] for record in read_hook_events_since(0) if record["payload"].get("session_id")), None)
        recorder.write({"kind": "D codex session id", "session_id": codex_session_id})
        if codex_session_id:
            queue_result = subprocess.run(["codex", "queue", "--thread", codex_session_id,
                                           "--message", "reply with the single word queued"],
                                          capture_output=True, text=True, timeout=60)
            recorder.write({"kind": "D codex queue result", "exit code": queue_result.returncode,
                            "stdout": queue_result.stdout[-1000:], "stderr": queue_result.stderr[-1000:]})
            await asyncio.sleep(12)
            recorder.write({"kind": "D hooks after queue", "events": [hook_event_name(record) for record in read_hook_events_since(queue_time)]})
        await recorder.mark("D after queue")
        recorder.write({"kind": "processes before exit",
                        "processes": list_child_processes_with_open_session_files(await session.async_get_variable("jobPid") or 0)})

        # E: quit.
        exit_time = time.time()
        await type_prompt_and_submit(session, "/quit")
        command_line_changed_at = None
        for _ in range(60):
            await asyncio.sleep(0.25)
            command_line = await session.async_get_variable("commandLine")
            if command_line and "codex" not in command_line:
                command_line_changed_at = time.time()
                break
        session_end = await wait_for_hook_event("SessionEnd", exit_time, 15)
        recorder.write({"kind": "E exit timing",
                        "commandLine changed after s": command_line_changed_at - exit_time if command_line_changed_at else None,
                        "SessionEnd after s": session_end["received_at"] - exit_time if session_end else None,
                        "SessionEnd payload": session_end["payload"] if session_end else None})
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
        with open(CODEX_CONFIG_PATH) as config_file:
            config_changed = config_file.read() != codex_config_before
        recorder.write({"kind": "codex config.toml changed during the run", "changed": config_changed})
        print("codex config.toml changed during the run:", config_changed)
    print("timeline:", TIMELINE_PATH)


if __name__ == "__main__":
    iterm2.run_until_complete(run_experiments)
