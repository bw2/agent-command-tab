"""Runs the list's Duplicate on a core's tab, for the tests of which conversation the copy continues."""

import asyncio
import unittest.mock

from agent_command_tab.dashboard_core_api import ActionRequest, ActionResult
from gathered_window_builders import make_gathered_tab, make_gathered_window


def find_launch_command_of_a_duplicate_of_the_tab_of_s1(core):
    """Returns the launch command that a Duplicate of the tab of the core's agent session s1 would
    run, from the launch command "codex". The core's gathered windows become one iTerm2 window
    whose one tab, t1, runs that agent, and the start of the copy is recorded rather than made.
    Raises ValueError unless the Duplicate starts exactly one copy."""
    core.gathered_windows = [make_gathered_window("w1", [
        make_gathered_tab("t1", "s1", "codex", core.agent_sessions["s1"]["process_id"])])]
    launch_commands = []

    async def record_the_start_of_the_copy(window_id, folder, launch_command, after_tab_id=None):
        launch_commands.append(launch_command)
        return ActionResult(True)
    with unittest.mock.patch.object(core.terminal_backend_by_name["iTerm2"], "start_agent_session", record_the_start_of_the_copy):
        asyncio.run(core.handle_duplicate_agent_session(
            ActionRequest("duplicate_agent_session", {"tab_id": "t1"}, {"folder": "/", "launch_command": "codex"})))
    (launch_command,) = launch_commands  # one start: not also a fork of a conversation that isn't the tab's
    return launch_command
