"""Phase 0 experiments on typing slash commands into an agent, and on its screens during a long turn.

Usage: slash_commands_and_long_turn_screens_spike.py OUTPUT_FOLDER claude|codex

Starts the agent tool in a throwaway iTerm2 window, in a test project folder with async
logging hooks (shared with codex_agent_experiments_spike.py), and records:
1. the screen at an empty input box, and with a draft typed but not submitted (then erased
   with Backspace);
2. the screen every 3 seconds during a 45-second command that needs no approval (stuck
   detection must ignore the lines that change while nothing happens);
3. what happens when `/compact` and then `/clear` are typed, followed after a pause by a
   separate Enter: the screen with the slash-command menu open, the screen afterwards, and
   the hook events.
The prompts (the long command's and /exit) are typed through
codex_agent_experiments_spike.type_prompt_and_submit, which types nothing while Codex shows a
menu; it doesn't recognize Claude Code's menus (marked "❯"), and the draft, the Backspaces and
the slash commands are typed without that check.
"""

import asyncio
import json
import os
import sys
import time

import iterm2
import iterm2.auth

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import codex_agent_experiments_spike as shared  # noqa: E402  (reuses its helpers, not its main run)

iterm2.auth.gAppKitAvailable = False

AGENT_TOOL = sys.argv[2]
CLAUDE_TEST_PROJECT_FOLDER = os.path.join(shared.OUTPUT_FOLDER, "phase0_claude_test_project")
PAUSE_BEFORE_ENTER_SECONDS = {"claude": 0.15, "codex": 0.3}[AGENT_TOOL]
LONG_COMMAND_PROMPT = {
    # Claude Code refuses a standalone `sleep 45`; python3 is on this user's allow list, so it runs without asking.
    "claude": "Use the Bash tool to run exactly this command: python3 -c \"import time; time.sleep(45)\"",
    "codex": "Run exactly this shell command inside the sandbox (it needs no escalation): sleep 45",
}[AGENT_TOOL]


def write_claude_test_project_with_logging_hooks():
    """Creates a Claude Code test project whose .claude/settings.json runs the shared hook logger."""
    shared.write_test_project_with_logging_hooks()
    os.makedirs(os.path.join(CLAUDE_TEST_PROJECT_FOLDER, ".claude"), exist_ok=True)
    logger_path = os.path.join(shared.OUTPUT_FOLDER, "hook_logger.py")
    events = ["SessionStart", "UserPromptSubmit", "PreToolUse", "PermissionRequest", "PostToolUse", "PreCompact",
              "Stop", "SessionEnd", "Notification"]
    hooks = {event: [{"matcher": "", "hooks": [{"type": "command", "async": True, "timeout": 10,
                                                  "command": f"/usr/bin/python3 -I -S {logger_path} {event} {shared.HOOK_LOG_PATH}"}]}]
             for event in events}
    with open(os.path.join(CLAUDE_TEST_PROJECT_FOLDER, ".claude", "settings.json"), "w") as settings_file:
        json.dump({"hooks": hooks}, settings_file, indent=1)


def build_launch_command():
    """Returns the shell command that starts the agent tool in its test project folder."""
    if AGENT_TOOL == "claude":
        return (f"cd {CLAUDE_TEST_PROJECT_FOLDER} && claude --permission-mode default --model haiku "
                "--name phase0-test-claude-slash-commands")
    return (f"cd {shared.TEST_PROJECT_FOLDER} && codex --dangerously-bypass-hook-trust "
            "-c 'model_reasoning_effort=\"low\"' -c check_for_update_on_startup=false "
            "-c 'approvals_reviewer=\"user\"' -a on-request -s read-only")


async def record_input_line_styles(session, recorder, step):
    """Records each input-looking line (starting with ❯ or ›) with one letter per character:
    f for faint, n for normal, so a dimmed suggestion can be told apart from typed text."""
    contents = await session.async_get_screen_contents()
    input_lines = []
    for index in range(contents.number_of_lines):
        line = contents.line(index)
        if line.string.strip().startswith(("❯", "›")):
            styles = [line.style_at(x) for x in range(len(line.string))]
            input_lines.append({"text": line.string.rstrip(),
                                "faint": "".join("f" if style and style.faint else "n" for style in styles).rstrip()})
    recorder.write({"kind": "input line styles", "step": step, "lines": input_lines[-3:]})


async def answer_codex_trust_question_if_shown(session, recorder):
    """Presses Enter at Codex's "Trust this folder?" question when its first option is selected."""
    for _ in range(30):
        await asyncio.sleep(1)
        screen_lines = await shared.read_screen_lines(session)
        if any("Trust this folder?" in line for line in screen_lines):
            if not any(line.strip().startswith("› 1. Trust and continue") for line in screen_lines):
                raise RuntimeError(f"unexpected trust menu, nothing typed: {screen_lines[-6:]}")
            await recorder.mark("trust question shown")
            await session.async_send_text("\r", suppress_broadcast=True)
            return
        if any("Ask Codex to do anything" in line for line in screen_lines):
            return


async def type_slash_command_with_separate_enter(session, recorder, command):
    """Types command, records the screen with its menu open, then sends Enter after a pause."""
    step_start = time.time()
    await session.async_send_text(command, suppress_broadcast=True)
    await asyncio.sleep(PAUSE_BEFORE_ENTER_SECONDS)
    await recorder.mark(f"{command} typed, before Enter")
    await session.async_send_text("\r", suppress_broadcast=True)
    for wait_seconds in (2, 6, 12):
        await asyncio.sleep(wait_seconds - (time.time() - step_start - PAUSE_BEFORE_ENTER_SECONDS))
        await recorder.mark(f"{command} about {wait_seconds} s after Enter")
    recorder.write({"kind": f"{command} hooks", "events": [
        (shared.hook_event_name(record), record["payload"].get("source"), record["payload"].get("reason"),
         record["payload"].get("trigger"))
        for record in shared.read_hook_events_since(step_start)]})


async def run_experiments(connection):
    """Runs the three experiments for one agent tool."""
    write_claude_test_project_with_logging_hooks()
    with open(shared.CODEX_CONFIG_PATH) as config_file:
        codex_config_before = config_file.read()
    app = await iterm2.async_get_app(connection)
    original_window = app.current_terminal_window
    original_session = original_window.current_tab.current_session if original_window else None
    launched_at = time.time()  # the hook log keeps earlier runs' records
    test_window = await iterm2.Window.async_create(
        connection, profile_customizations=iterm2.LocalWriteOnlyProfile({"Initial Text": build_launch_command()}))
    test_session_ids = {test_window.tabs[0].sessions[0].session_id}
    session = test_window.tabs[0].sessions[0]
    recorder = shared.TimelineRecorder(session)
    polling_task = asyncio.ensure_future(recorder.poll_forever())
    try:
        if AGENT_TOOL == "codex":
            await answer_codex_trust_question_if_shown(session, recorder)
        elif not await shared.wait_for_hook_event("SessionStart", launched_at, 30):
            # Claude Code runs no project hooks while its folder trust question is up.
            raise RuntimeError("no SessionStart from Claude Code within 30 s (a folder trust question?); "
                               "stopping so nothing is typed into a menu")
        await asyncio.sleep(3)
        if AGENT_TOOL not in (await session.async_get_variable("commandLine") or ""):
            raise RuntimeError("the agent is not running in the test tab; stopping")

        # 1. Empty input box, then a draft, then the draft erased.
        await recorder.mark("empty input box")
        await record_input_line_styles(session, recorder, "empty input box")
        draft = "phase0 draft text"
        await session.async_send_text(draft, suppress_broadcast=True)
        await asyncio.sleep(0.8)
        await recorder.mark("draft typed, not submitted")
        await record_input_line_styles(session, recorder, "draft typed, not submitted")
        await session.async_send_text("\x7f" * len(draft), suppress_broadcast=True)
        await asyncio.sleep(0.8)
        await recorder.mark("draft erased with Backspace")

        # 2. A long command that needs no approval; screens every 3 seconds.
        step_start = time.time()
        await shared.type_prompt_and_submit(session, LONG_COMMAND_PROMPT)
        for index in range(20):
            await asyncio.sleep(3)
            await recorder.mark(f"long turn screen {index + 1} ({time.time() - step_start:.0f} s)")
            if any(shared.hook_event_name(record) == "Stop" for record in shared.read_hook_events_since(step_start)):
                break
        await asyncio.sleep(2)
        await recorder.mark("after the long turn")
        await record_input_line_styles(session, recorder, "after the long turn")

        # 3. /compact, then /clear, each followed by a separate Enter.
        await type_slash_command_with_separate_enter(session, recorder, "/compact")
        for _ in range(60):
            if await session.async_get_variable("status") in ("idle", "done", None):
                break
            await asyncio.sleep(1)
        await asyncio.sleep(3)
        await recorder.mark("before /clear")
        await type_slash_command_with_separate_enter(session, recorder, "/clear")

        await shared.type_prompt_and_submit(session, "/exit" if AGENT_TOOL == "claude" else "/quit")
        await asyncio.sleep(8)
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
        with open(shared.CODEX_CONFIG_PATH) as config_file:
            print("codex config.toml changed during the run:", config_file.read() != codex_config_before)
    print("timeline:", shared.TIMELINE_PATH)


if __name__ == "__main__":
    iterm2.run_until_complete(run_experiments)
