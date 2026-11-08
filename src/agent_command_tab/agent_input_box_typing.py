"""Typing a slash command into an agent's input box and pasting a message there, in any terminal
(a terminal_backends.base.TerminalSession): only into an empty box, checked on screen before and
after, since text typed into a draft would join it."""

import asyncio
import time

from agent_command_tab.dashboard_core_api import ActionResult
from agent_command_tab.screen_readings import count_lines_matching, input_area_is_empty, read_typed_input_area_text
from agent_command_tab.terminal_backends.base import TerminalRequestFailed

SECONDS_BEFORE_CHECKING_THAT_A_COMMAND_WAS_SUBMITTED = 0.5  # the box empties within a redraw of the Enter
SECONDS_BETWEEN_A_PASTE_AND_ITS_ENTER = 0.5  # the pause that worked in a live check
# iTerm2 refreshes its copy of the screen of a tab that isn't in front only about once a second, so
# a check of what typing did reads the screen again for this long before concluding anything.
SECONDS_A_HIDDEN_TABS_SCREEN_MAY_LAG = 1.5
SECONDS_BETWEEN_SCREEN_READS = 0.1


async def read_screen_until(terminal_session, screen_shows_it):
    """Reads the tab's screen (with faint flags) until screen_shows_it(screen lines) is true or
    SECONDS_A_HIDDEN_TABS_SCREEN_MAY_LAG have passed, and returns the last screen read."""
    deadline = time.monotonic() + SECONDS_A_HIDDEN_TABS_SCREEN_MAY_LAG
    while True:
        screen_lines = await terminal_session.read_screen_lines_with_faint_flags()
        if screen_shows_it(screen_lines) or time.monotonic() >= deadline:
            return screen_lines
        await asyncio.sleep(SECONDS_BETWEEN_SCREEN_READS)


def read_typed_text(screen_lines, adapter):
    """Returns the text typed into the agent's input box (read_typed_input_area_text), or None
    when the box isn't on screen."""
    return read_typed_input_area_text(screen_lines, adapter.input_line_marker, adapter.input_area_end_pattern)


async def type_slash_command(terminal_session, command, adapter, agent_is_in_the_middle_of_a_turn=False,
                             agent_asks_right_now=lambda: False):
    """Types a slash command into the agent's empty input box, then Enter after the adapter's
    pause. Types nothing when the box
    holds unsent text or can't be found on screen, since the command would join the draft.
    Reports a failure, without retrying, when the command is still in the box a moment after Enter
    (read_screen_until, since a hidden tab's screen lags).

    Args:
        terminal_session: The tab's TerminalSession.
        command: One of the adapter's slash_commands, for example "/compact".
        adapter: The session's agent tool adapter (its input line marker, input area end and pause).
        agent_is_in_the_middle_of_a_turn: Whether the agent is busy (Claude Code queues a command
            typed then). Enter then goes only while the command is still in the box, since the
            agent may open a permission prompt meanwhile, which would take the Enter as its answer;
            otherwise a failure is reported, also when agent_asks_right_now says a prompt has
            opened (a hidden tab's screen lags behind it). Not checked for an agent at its prompt, which opens
            no prompt on its own: in a live check, Codex's input box couldn't be found
            on screen right after the command was typed (its slash-command menu was open).
        agent_asks_right_now: Returns whether the agent asks a question or for permission now,
            from a signal fresher than the screen (DashboardCore.agent_asks_right_now).

    Returns:
        None when nothing was typed because the box holds unsent text or can't be found on screen,
        or when the agent refused the command because a turn is under way (Codex, whose turn can
        look over on screen while answer text streams); the caller may try again later. Otherwise
        an ActionResult.
    """
    if command not in adapter.slash_commands:
        return ActionResult(False, f"{command} can't be sent to {adapter.agent_tool_name or 'this agent'}")
    screen_lines_before_typing = await terminal_session.read_screen_lines_with_faint_flags()
    if not input_area_is_empty(screen_lines_before_typing, adapter.input_line_marker, adapter.input_area_end_pattern):
        return None
    await terminal_session.send_text(command)
    await asyncio.sleep(adapter.seconds_before_enter)
    if agent_is_in_the_middle_of_a_turn:
        typed_text = read_typed_text(await read_screen_until(
            terminal_session, lambda screen_lines: command in (read_typed_text(screen_lines, adapter) or "")), adapter)
        if typed_text is None or command not in typed_text:
            return ActionResult(False, f"{command} wasn't submitted: the tab's input box changed before Enter")
        if agent_asks_right_now():  # its permission prompt would take the Enter as its answer
            return ActionResult(False, f"{command} wasn't submitted: the agent asked a question before Enter")
    await terminal_session.send_text("\r")
    await asyncio.sleep(SECONDS_BEFORE_CHECKING_THAT_A_COMMAND_WAS_SUBMITTED)

    def count_refusals(screen_lines):
        return count_lines_matching([text for text, _ in screen_lines], adapter.slash_command_refused_during_a_turn_pattern)
    refusal_count_before_typing = count_refusals(screen_lines_before_typing)

    def screen_shows_what_enter_did(screen_lines):
        # A hidden tab's copy of the screen can still be the one from before the typing, or from
        # before Enter with Codex's slash-command menu covering the box: neither shows a refusal
        # or the command still in the box, so they are read again.
        if screen_lines == screen_lines_before_typing:
            return False
        if count_refusals(screen_lines) > refusal_count_before_typing:
            return True
        typed_text = read_typed_text(screen_lines, adapter)
        return typed_text is not None and command not in typed_text
    try:
        screen_lines_after_enter = await read_screen_until(terminal_session, screen_shows_what_enter_did)
    except TerminalRequestFailed:
        return ActionResult(True)  # the tab closed meanwhile
    if count_refusals(screen_lines_after_enter) > refusal_count_before_typing:
        return None
    typed_text = read_typed_text(screen_lines_after_enter, adapter)
    # A box that is off screen (the tool redrawing) can't be checked; the command was sent.
    if typed_text is not None and command in typed_text:
        return ActionResult(False, f"{command} may not have been submitted: it is still in the tab's input box")
    return ActionResult(True)


async def paste_message_into_empty_input_box(terminal_session, message, adapter):
    """Pastes a message of one or more lines into the agent's empty input box as one bracketed
    paste, then presses Enter ("Talk to Each Other"; checked live with Claude Code 2.1.306: a
    three-line message was submitted as one prompt). Reports a failure, without
    retrying, when the box isn't empty a moment after Enter.

    Args:
        terminal_session: The tab's TerminalSession.
        message: The text to submit.
        adapter: The session's agent tool adapter (its input line marker and input area end).

    Returns:
        None when nothing was pasted because the box holds unsent text or can't be found on
        screen (the caller may try again later), otherwise an ActionResult.
    """
    screen_lines_before_pasting = await terminal_session.read_screen_lines_with_faint_flags()
    if not input_area_is_empty(screen_lines_before_pasting, adapter.input_line_marker, adapter.input_area_end_pattern):
        return None
    await terminal_session.paste_text(message)
    await asyncio.sleep(SECONDS_BETWEEN_A_PASTE_AND_ITS_ENTER)
    await terminal_session.send_text("\r")
    await asyncio.sleep(SECONDS_BEFORE_CHECKING_THAT_A_COMMAND_WAS_SUBMITTED)
    try:
        # As for a slash command, a copy of the screen from before the paste is read again.
        typed_text = read_typed_text(await read_screen_until(terminal_session, lambda screen_lines: (
            screen_lines != screen_lines_before_pasting and not read_typed_text(screen_lines, adapter))), adapter)
    except TerminalRequestFailed:
        return ActionResult(True)  # the tab closed meanwhile
    if typed_text:  # None: the box is off screen (the tool redrawing), so it can't be checked
        return ActionResult(False, "the message may not have been submitted: the tab's input box isn't empty after Enter")
    return ActionResult(True)
