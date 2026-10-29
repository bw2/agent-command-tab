"""The Memory and CPU readings start as soon as a column or sort order needs them (DashboardCore.read_resource_usage_periodically)."""

import asyncio
import time
import unittest
import unittest.mock

from agent_command_tab import iterm2_connection_thread
from agent_command_tab.iterm2_connection_thread import DashboardCore


class ResourceUsageReadingTests(unittest.TestCase):  # code review, 2026-10-29
    def test_turning_the_readings_on_gives_memory_at_once_and_cpu_use_right_after(self):
        core = DashboardCore(lambda snapshot: None, lambda message: None)
        core.agent_sessions = {"s1": {"process_id": 4242}}
        started_at = time.monotonic()
        cpu_seconds = iter(range(100))

        def read_process_tree_resource_usage(process_id):
            return (512 * 1024 ** 2, next(cpu_seconds) * 0.1)  # memory in bytes, CPU seconds so far

        async def turn_the_readings_on_then_wait_for_cpu_use():
            reading_task = asyncio.ensure_future(core.read_resource_usage_periodically())
            await asyncio.sleep(0.01)  # the loop is waiting out its (long) interval
            core.set_core_settings({"left_out_agent_tool_names": [], "resource_usage_is_read": True, "codex_command_path": None})
            while core.screen_readings_by_session_id.get("s1", {}).get("cpu_percent") is None and time.monotonic() - started_at < 5:
                await asyncio.sleep(0.01)
            reading_task.cancel()

        with unittest.mock.patch.object(iterm2_connection_thread, "RESOURCE_USAGE_CHECK_INTERVAL_SECONDS", 30), \
                unittest.mock.patch.object(iterm2_connection_thread, "SECONDS_BETWEEN_THE_FIRST_TWO_CPU_READINGS", 0.05), \
                unittest.mock.patch.object(iterm2_connection_thread, "read_process_tree_resource_usage", read_process_tree_resource_usage):
            asyncio.run(turn_the_readings_on_then_wait_for_cpu_use())
        self.assertLess(time.monotonic() - started_at, 2)  # not the 30-second interval
        self.assertEqual(core.screen_readings_by_session_id["s1"]["memory_bytes"], 512 * 1024 ** 2)
        self.assertIsNotNone(core.screen_readings_by_session_id["s1"]["cpu_percent"])


if __name__ == "__main__":
    unittest.main()
