"""Actions that change iTerm2: focus, rename, tab moves, new agent sessions and typed slash commands (PLAN.md sections 8 and 9).

Each function takes the connection's iterm2.App and returns an ActionResult. After any action
the core re-reads iTerm2 and publishes a new snapshot; the views never guess the result.
"""

import asyncio
import os

import iterm2

from llm_sidebar_dashboard.dashboard_core_api import ActionResult
from llm_sidebar_dashboard.monitor_assignment import (assign_windows_to_monitors_by_largest_overlap,
                                                      compute_window_frame_on_another_monitor)
from llm_sidebar_dashboard.screen_readings import input_area_is_empty
from llm_sidebar_dashboard.tab_order_computation import compute_full_tab_order_after_move

SECONDS_FOR_FULL_SCREEN_ANIMATION = 1.0


def escape_text_for_iterm2_interpolated_string(text):
    """Returns text with backslashes doubled, since iTerm2 reads titles as interpolated strings
    (where a backslash starts an expression such as \\(session.name))."""
    return text.replace("\\", "\\\\")


async def focus_agent_session(app, session_id):
    """Brings the session's window and tab to the front, without raising iTerm2's other windows.

    Args:
        app: The iterm2.App.
        session_id: The tab's first session.
    """
    session = app.get_session_by_id(session_id)
    if session is None:
        return ActionResult(False, "that tab no longer exists")
    await session.async_activate(select_tab=True, order_window_front=True)
    await app.async_activate(raise_all_windows=False)
    return ActionResult(True)


async def focus_window(app, window_id):
    """Brings a window to the front with the tab it already shows (which may run no agent),
    without raising iTerm2's other windows."""
    window = app.get_window_by_id(window_id)
    if window is None:
        return ActionResult(False, "that window no longer exists")
    await window.async_activate()
    await app.async_activate(raise_all_windows=False)
    return ActionResult(True)


async def put_back_tabs_and_window_after_peek(app, selected_tab_id_by_window_id, window_id_to_bring_back):
    """Undoes a peek (pressing and holding a tab row): each window gets back the tab it had
    selected, and, when iTerm2 was in front before, its front window comes back to the front.

    Args:
        app: The iterm2.App.
        selected_tab_id_by_window_id: {window ID: selected tab ID} from before the peek.
        window_id_to_bring_back: The window that was in front before, or None when another app was.
    """
    for window in app.terminal_windows:
        tab_id_before = selected_tab_id_by_window_id.get(window.window_id)
        if tab_id_before and window.current_tab is not None and window.current_tab.tab_id != tab_id_before:
            tab_before = app.get_tab_by_id(tab_id_before)
            if tab_before is not None:
                await tab_before.async_select(order_window_front=False)
    window_to_bring_back = app.get_window_by_id(window_id_to_bring_back) if window_id_to_bring_back else None
    if window_to_bring_back is not None:
        await window_to_bring_back.async_activate()
    return ActionResult(True)


async def rename_tab(app, tab_id, new_title):
    """Sets a tab's title as iTerm2's Edit Tab Title does; an empty title restores the default name."""
    tab = app.get_tab_by_id(tab_id)
    if tab is None:
        return ActionResult(False, "that tab no longer exists")
    await tab.async_set_title(escape_text_for_iterm2_interpolated_string(new_title.strip()))
    return ActionResult(True)


async def rename_window(app, window_id, new_title):
    """Sets a window's title as iTerm2's Edit Window Title does; an empty title restores the default name."""
    window = app.get_window_by_id(window_id)
    if window is None:
        return ActionResult(False, "that window no longer exists")
    await window.async_set_title(escape_text_for_iterm2_interpolated_string(new_title.strip()))
    return ActionResult(True)


async def close_window(app, window_id):
    """Closes a window and ends the programs in its tabs. The dashboard has already asked the
    user, so iTerm2 is told not to ask again."""
    window = app.get_window_by_id(window_id)
    if window is None:
        return ActionResult(False, "that window no longer exists")
    await window.async_close(force=True)
    return ActionResult(True)


async def move_tab_to_window_position(app, tab_id, target_window_id, drop_before_tab_id):
    """Moves a tab within its window or into another window, keeping its program running.

    Args:
        app: The iterm2.App.
        tab_id: The dragged tab.
        target_window_id: The window it was dropped into (its own window for a reorder).
        drop_before_tab_id: The listed tab it was dropped before, or None for after the last tab.
    """
    tab = app.get_tab_by_id(tab_id)
    target_window = app.get_window_by_id(target_window_id)
    if tab is None or target_window is None:
        return ActionResult(False, "that tab or window no longer exists")
    tabs_by_tab_id = {target_tab.tab_id: target_tab for target_tab in target_window.tabs}
    tabs_by_tab_id[tab_id] = tab
    new_tab_order = compute_full_tab_order_after_move([target_tab.tab_id for target_tab in target_window.tabs],
                                                      tab_id, drop_before_tab_id)
    await target_window.async_set_tabs([tabs_by_tab_id[ordered_tab_id] for ordered_tab_id in new_tab_order])
    return ActionResult(True)


async def move_window_to_monitor(app, window_id, monitors, target_monitor_id):
    """Moves a window to another monitor, keeping its size if it fits and its relative position (R32).

    A full-screen window can't simply get a new frame, so it leaves full screen, moves, and
    enters full screen again on the target monitor (PLAN.md section 10; not yet tried on a real
    second monitor).

    Args:
        app: The iterm2.App.
        window_id: The window to move.
        monitors: Monitor dicts with "monitor_id" and "visible_frame" (see DashboardCore.set_monitors).
        target_monitor_id: The monitor to move it to.
    """
    window = app.get_window_by_id(window_id)
    target_monitor = next((monitor for monitor in monitors if monitor["monitor_id"] == target_monitor_id), None)
    if window is None or target_monitor is None:
        return ActionResult(False, "that window or monitor no longer exists")
    was_full_screen = await window.async_get_fullscreen()
    if was_full_screen:
        await window.async_set_fullscreen(False)
        await asyncio.sleep(SECONDS_FOR_FULL_SCREEN_ANIMATION)
    frame = await window.async_get_frame()
    window_frame = (frame.origin.x, frame.origin.y, frame.size.width, frame.size.height)
    source_index = assign_windows_to_monitors_by_largest_overlap(
        {window_id: window_frame}, [monitor["frame"] for monitor in monitors])[window_id]
    new_frame = compute_window_frame_on_another_monitor(window_frame, monitors[source_index]["visible_frame"],
                                                        target_monitor["visible_frame"])
    await window.async_set_frame(iterm2.Frame(iterm2.Point(new_frame[0], new_frame[1]), iterm2.Size(new_frame[2], new_frame[3])))
    if was_full_screen:
        await asyncio.sleep(SECONDS_FOR_FULL_SCREEN_ANIMATION)
        await window.async_set_fullscreen(True)
    return ActionResult(True)


def build_new_agent_session_profile(folder, launch_command):
    """Returns profile customizations that start launch_command in folder (PLAN.md section 8).

    iTerm2's "send text at start" profile setting ("Initial Text", Phase 0) types the command
    once the shell is ready, so the tab keeps a normal shell and stays open if the agent exits.
    """
    profile = iterm2.LocalWriteOnlyProfile({"Initial Text": launch_command})
    profile.set_initial_directory_mode(iterm2.InitialWorkingDirectory.INITIAL_WORKING_DIRECTORY_CUSTOM)
    profile.set_custom_directory(folder)
    return profile


async def start_agent_session(connection, app, window_id, folder, launch_command, tab_index=None):
    """Opens a new tab in a window (or a new window when window_id is None) running an agent tool.

    Args:
        connection: The iTerm2 connection.
        app: The iterm2.App.
        window_id: The window for the new tab, or None for a new window.
        folder: The folder the agent starts in ("~" is expanded).
        launch_command: The shell command that starts the agent tool, for example "claude".
        tab_index: Where the new tab goes among the window's tabs; None puts it last.
    """
    folder = os.path.expanduser(folder)
    if not os.path.isdir(folder):
        return ActionResult(False, f"the folder {folder} doesn't exist")
    if not launch_command.strip():
        return ActionResult(False, "the agent tool has no launch command (see Settings)")
    profile = build_new_agent_session_profile(folder, launch_command)
    if window_id is None:
        await iterm2.Window.async_create(connection, profile_customizations=profile)
        return ActionResult(True)
    window = app.get_window_by_id(window_id)
    if window is None:
        return ActionResult(False, "that window no longer exists")
    await window.async_create_tab(profile_customizations=profile, index=tab_index)
    return ActionResult(True)


async def duplicate_agent_session(connection, app, tab_id, folder, launch_command_for_the_copy):
    """Opens a tab right after tab_id, in its window, running launch_command_for_the_copy in folder
    (like Chrome's Duplicate; the core builds the command that continues a copy of the conversation)."""
    window = app.get_window_for_tab(tab_id)
    if window is None:
        return ActionResult(False, "that tab no longer exists")
    tab_index = [tab.tab_id for tab in window.tabs].index(tab_id)
    return await start_agent_session(connection, app, window.window_id, folder, launch_command_for_the_copy, tab_index + 1)


async def move_tab_to_new_window(app, tab_id):
    """Moves a tab into a new window of its own (iTerm2 refuses this for a window's only tab)."""
    tab = app.get_tab_by_id(tab_id)
    if tab is None:
        return ActionResult(False, "that tab no longer exists")
    window = app.get_window_for_tab(tab_id)
    if window is not None and len(window.tabs) < 2:
        return ActionResult(False, "the tab is already its window's only tab")
    await tab.async_move_to_window()
    return ActionResult(True)


async def read_screen_lines_with_faint_flags(session):
    """Returns the session's visible lines as (text, faint_flags), for screen_readings.input_area_is_empty."""
    contents = await session.async_get_screen_contents()
    screen_lines_with_faint_flags = []
    for index in range(contents.number_of_lines):
        line = contents.line(index)
        screen_lines_with_faint_flags.append(
            (line.string, [bool(style is not None and style.faint) for style in map(line.style_at, range(len(line.string)))]))
    return screen_lines_with_faint_flags


async def type_slash_command(app, session_id, command, adapter):
    """Types a slash command into the agent's empty input box, then Enter after the adapter's
    pause (R15.1; PLAN.md section 8, "Sending /compact and /clear"). Refuses when the box holds
    unsent text or can't be found on screen, since the command would join the draft.

    Args:
        app: The iterm2.App.
        session_id: The tab's first session.
        command: One of the adapter's slash_commands, for example "/compact".
        adapter: The session's agent tool adapter (its input line marker, input area end and pause).
    """
    session = app.get_session_by_id(session_id)
    if session is None:
        return ActionResult(False, "that tab no longer exists")
    if command not in adapter.slash_commands:
        return ActionResult(False, f"{command} can't be sent to {adapter.agent_tool_name or 'this agent'}")
    if not input_area_is_empty(await read_screen_lines_with_faint_flags(session), adapter.input_line_marker,
                               adapter.input_area_end_pattern):
        return ActionResult(False, "the tab has unsent text in its input box, or the input box isn't on screen")
    await session.async_send_text(command, suppress_broadcast=True)
    await asyncio.sleep(adapter.seconds_before_enter)
    await session.async_send_text("\r", suppress_broadcast=True)
    return ActionResult(True)
