"""The Memory and CPU readings (DashboardCore.read_resource_usage_periodically): they start as soon
as a column or sort order needs them, and one under way while another agent takes the tab is dropped."""

import asyncio
import threading
import time
import unittest
import unittest.mock

from agent_command_tab import dashboard_core
from agent_command_tab.dashboard_core import DashboardCore
from condition_waiting import SECONDS_TO_WAIT_FOR_A_REAL_PROCESS, wait_until_in_a_coroutine


class ResourceUsageReadingTests(unittest.TestCase):
    def test_turning_the_readings_on_gives_memory_at_once_and_cpu_use_right_after(self):
        core = DashboardCore(lambda snapshot: None, lambda message: None)
        core.agent_sessions = {"s1": {"process_id": 4242}}
        started_at = time.monotonic()
        cpu_seconds = iter(range(100))
        second_reading_may_be_taken = threading.Event()
        session_reading_after_the_first_reading = {}

        def read_process_tree_resource_usage(process_id):
            cpu_seconds_so_far = next(cpu_seconds) * 0.1
            if cpu_seconds_so_far:  # a later reading waits until what the first one gave has been looked at
                second_reading_may_be_taken.wait(SECONDS_TO_WAIT_FOR_A_REAL_PROCESS)
            return (512 * 1024 ** 2, cpu_seconds_so_far)  # memory in bytes, CPU seconds so far

        def read_session_reading():
            return core.session_readings_by_session_id.get("s1", {})

        async def turn_the_readings_on_then_wait_for_memory_and_then_cpu_use():
            reading_task = asyncio.ensure_future(core.read_resource_usage_periodically())
            await asyncio.sleep(0.01)  # the loop is waiting out its (long) interval
            core.set_core_settings({"left_out_agent_tool_names": [], "resource_usage_is_read": True, "codex_command_path": None})
            await wait_until_in_a_coroutine(lambda: read_session_reading().get("memory_bytes") is not None)
            session_reading_after_the_first_reading.update(read_session_reading())
            second_reading_may_be_taken.set()
            await wait_until_in_a_coroutine(lambda: read_session_reading().get("cpu_percent") is not None)
            reading_task.cancel()

        with unittest.mock.patch.object(dashboard_core, "RESOURCE_USAGE_CHECK_INTERVAL_SECONDS", 30), \
                unittest.mock.patch.object(dashboard_core, "SECONDS_BETWEEN_THE_FIRST_TWO_CPU_READINGS", 0.05), \
                unittest.mock.patch.object(dashboard_core, "read_process_tree_resource_usage", read_process_tree_resource_usage):
            asyncio.run(turn_the_readings_on_then_wait_for_memory_and_then_cpu_use())
        self.assertLess(time.monotonic() - started_at, 2)  # not the 30-second interval
        # The first reading gives the memory alone: CPU use takes two readings of the CPU time.
        self.assertEqual([session_reading_after_the_first_reading.get(reading_name) for reading_name in ("memory_bytes", "cpu_percent")],
                         [512 * 1024 ** 2, None])
        self.assertEqual(core.session_readings_by_session_id["s1"]["memory_bytes"], 512 * 1024 ** 2)
        self.assertIsNotNone(core.session_readings_by_session_id["s1"]["cpu_percent"])

    def test_a_reading_under_way_while_another_agent_takes_the_tab_is_dropped(self):
        # The processes are read in a worker thread; a snapshot published meanwhile may find another agent in the tab.
        core = DashboardCore(lambda snapshot: None, lambda message: None)
        core.agent_sessions = {"s1": {"process_id": 101}}
        core.resource_usage_is_read = True
        memory_noted_when_the_next_agent_was_read = []

        def read_process_tree_resource_usage(process_id):
            if process_id == 202:
                memory_noted_when_the_next_agent_was_read.append(core.session_readings_by_session_id.get("s1", {}).get("memory_bytes"))
                return (256 * 1024 ** 2, 0.0)
            core.agent_sessions = {"s1": {"process_id": 202}}  # 202 took the place of 101 in the tab
            return (512 * 1024 ** 2, 0.0)

        async def read_until_the_next_agent_was_read():
            reading_task = asyncio.ensure_future(core.read_resource_usage_periodically())
            await wait_until_in_a_coroutine(lambda: bool(memory_noted_when_the_next_agent_was_read))
            reading_task.cancel()
        with unittest.mock.patch.object(dashboard_core, "RESOURCE_USAGE_CHECK_INTERVAL_SECONDS", 0.01), \
                unittest.mock.patch.object(dashboard_core, "read_process_tree_resource_usage", read_process_tree_resource_usage):
            asyncio.run(read_until_the_next_agent_was_read())
        self.assertEqual(memory_noted_when_the_next_agent_was_read[:1], [None])  # not the 512 MB read of 101


if __name__ == "__main__":
    unittest.main()
