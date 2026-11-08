"""Waits for what a real process or terminal does, for the tests that run them."""

import asyncio
import time

from agent_command_tab.agent_process_tree import list_descendant_processes

SECONDS_TO_WAIT_FOR_A_REAL_PROCESS = 5


def wait_until(condition, seconds=SECONDS_TO_WAIT_FOR_A_REAL_PROCESS):
    """Checks condition() every 0.05 s until it is true or seconds have passed; returns whether it came true."""
    deadline = time.time() + seconds
    while not condition():
        if time.time() >= deadline:
            return False
        time.sleep(0.05)
    return True


def wait_for_the_process_named_below(process_name, ancestor_process_id, maximum_depth=6):
    """Returns the ProcessInfo of a process named process_name below ancestor_process_id
    (list_descendant_processes's, down to maximum_depth levels) once one runs, or None when none
    does within SECONDS_TO_WAIT_FOR_A_REAL_PROCESS."""
    found = []

    def a_process_of_that_name_runs():
        found[:] = [process for process in list_descendant_processes(ancestor_process_id, maximum_depth)
                    if process.name == process_name]
        return bool(found)
    return found[0] if wait_until(a_process_of_that_name_runs) else None


async def wait_until_in_a_coroutine(condition, seconds=SECONDS_TO_WAIT_FOR_A_REAL_PROCESS):
    """wait_until for a coroutine: awaits between the checks, so the event loop runs meanwhile."""
    deadline = time.time() + seconds
    while not condition():
        if time.time() >= deadline:
            return False
        await asyncio.sleep(0.05)
    return True
