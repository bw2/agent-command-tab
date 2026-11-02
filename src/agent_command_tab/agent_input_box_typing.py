"""Typing a slash command into an agent's input box and pasting a message there, in any terminal
(a terminal_backends.base.TerminalSession): only into an empty box, checked on screen before and
after, since text typed into a draft would join it."""

import asyncio

from agent_command_tab.dashboard_core_api import ActionResult
from agent_command_tab.screen_readings import count_lines_matching, input_area_is_empty, read_typed_input_area_text
from agent_command_tab.terminal_backends.base import TerminalRequestFailed

SECONDS_BEFORE_CHECKING_THAT_A_COMMAND_WAS_SUBMITTED = 0.5  # the box empties within a redraw of the Enter
SECONDS_BETWEEN_A_PASTE_AND_ITS_ENTER = 0.5  # the pause of the live check (2026-10-21)


async def type_slash_command(terminal_session, command, adapter, agent_is_in_the_middle_of_a_turn=False):
    """Types a slash command into the agent's empty input box, then Enter after the adapter's
    pause. Types nothing when the box
    holds unsent text or can't be found on screen, since the command would join the draft.
    Reports a failure, without retrying, when the command is still in the box a moment after Enter.

    Args:
        terminal_session: The tab's TerminalSession.
        command: One of the adapter's slash_commands, for example "/compact".
        adapter: The session's agent tool adapter (its input line marker, input area end and pause).
        agent_is_in_the_middle_of_a_turn: Whether the agent is busy (Claude Code queues a command
            typed then). Enter then goes only while the command is still in the box, since the
            agent may open a permission prompt meanwhile, which would take the Enter as its answer;
            otherwise a failure is reported. Not checked for an agent at its prompt, which opens
            no prompt on its own: in a live check on 2026-10-22, Codex's input box couldn't be found
            on screen right after the command was typed (its slash-command menu was open).

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
        typed_text = read_typed_input_area_text(await terminal_session.read_screen_lines_with_faint_flags(),
                                                adapter.input_line_marker, adapter.input_area_end_pattern)
        if typed_text is None or command not in typed_text:
            return ActionResult(False, f"{command} wasn't submitted: the tab's input box changed before Enter")
    await terminal_session.send_text("\r")
    await asyncio.sleep(SECONDS_BEFORE_CHECKING_THAT_A_COMMAND_WAS_SUBMITTED)
    try:
        screen_lines_after_enter = await terminal_session.read_screen_lines_with_faint_flags()
    except TerminalRequestFailed:
        return ActionResult(True)  # the tab closed meanwhile
    refusal_pattern = adapter.slash_command_refused_during_a_turn_pattern
    if count_lines_matching([text for text, _ in screen_lines_after_enter], refusal_pattern) > count_lines_matching(
            [text for text, _ in screen_lines_before_typing], refusal_pattern):
        return None
    typed_text = read_typed_input_area_text(screen_lines_after_enter, adapter.input_line_marker, adapter.input_area_end_pattern)
    # A box that is off screen (the tool redrawing) can't be checked; the command was sent.
    if typed_text is not None and command in typed_text:
        return ActionResult(False, f"{command} may not have been submitted: it is still in the tab's input box")
    return ActionResult(True)


async def paste_message_into_empty_input_box(terminal_session, message, adapter):
    """Pastes a message of one or more lines into the agent's empty input box as one bracketed
    paste, then presses Enter ("Talk to Each Other"; checked live with Claude Code 2.1.306 on
    2026-10-21: a three-line message was submitted as one prompt). Reports a failure, without
    retrying, when the box isn't empty a moment after Enter.

    Args:
        terminal_session: The tab's TerminalSession.
        message: The text to submit.
        adapter: The session's agent tool adapter (its input line marker and input area end).

    Returns:
        None when nothing was pasted because the box holds unsent text or can't be found on
        screen (the caller may try again later), otherwise an ActionResult.
    """
    if not input_area_is_empty(await terminal_session.read_screen_lines_with_faint_flags(), adapter.input_line_marker,
                               adapter.input_area_end_pattern):
        return None
    await terminal_session.paste_text(message)
    await asyncio.sleep(SECONDS_BETWEEN_A_PASTE_AND_ITS_ENTER)
    await terminal_session.send_text("\r")
    await asyncio.sleep(SECONDS_BEFORE_CHECKING_THAT_A_COMMAND_WAS_SUBMITTED)
    try:
        typed_text = read_typed_input_area_text(await terminal_session.read_screen_lines_with_faint_flags(),
                                                adapter.input_line_marker, adapter.input_area_end_pattern)
    except TerminalRequestFailed:
        return ActionResult(True)  # the tab closed meanwhile
    if typed_text:  # None: the box is off screen (the tool redrawing), so it can't be checked
        return ActionResult(False, "the message may not have been submitted: the tab's input box isn't empty after Enter")
    return ActionResult(True)
