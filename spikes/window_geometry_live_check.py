"""Checks the docked sidebar's room-making and the move-to-monitor action on a throwaway window.

1. Docked area: opens a window covering most of the screen, reserves a 400-point strip at the
   right edge through the core, checks the window was made narrower so it ends at the strip,
   then ends the reservation and checks the window got its old frame back.
2. Move to monitor: with only one display here, tells the core that the screen's left and right
   halves are two monitors, puts the window on the left half, moves it to the "right monitor"
   and checks it now lies within the right half with its size kept.
Closes its window at the end and restores the real monitor list.
"""

import asyncio
import sys
import time

import iterm2

from llm_sidebar_dashboard.dashboard_core_api import ActionRequest
from llm_sidebar_dashboard.iterm2_connection_thread import Iterm2ConnectionThread
from llm_sidebar_dashboard.user_interface.screen_layout import read_monitors_from_screens


def main():
    connection_thread = Iterm2ConnectionThread(lambda snapshot: None, lambda message: print("connection lost:", message))
    connection_thread.start()
    connection_thread.core_ready.wait()
    core = connection_thread.core
    while core.app is None:
        time.sleep(0.1)
    real_monitors = read_monitors_from_screens()
    connection_thread.send_monitors(real_monitors)
    visible_x, visible_y, visible_width, visible_height = real_monitors[0]["visible_frame"]
    state = {}

    def run_on_core_loop(coroutine, timeout=30):
        return asyncio.run_coroutine_threadsafe(coroutine, connection_thread.event_loop).result(timeout)

    async def open_window(frame):
        window = await iterm2.Window.async_create(core.connection)
        await window.async_set_frame(iterm2.Frame(iterm2.Point(frame[0], frame[1]), iterm2.Size(frame[2], frame[3])))
        state["window"] = window

    async def read_frame():
        frame = await state["window"].async_get_frame()
        return (frame.origin.x, frame.origin.y, frame.size.width, frame.size.height)

    async def set_frame(frame):
        await state["window"].async_set_frame(iterm2.Frame(iterm2.Point(frame[0], frame[1]), iterm2.Size(frame[2], frame[3])))

    def wait_for_frame(condition, timeout=8):
        deadline = time.time() + timeout
        while time.time() < deadline:
            frame = run_on_core_loop(read_frame())
            if condition(frame):
                return frame
            time.sleep(0.3)
        return run_on_core_loop(read_frame())

    wide_frame = (visible_x + 40, visible_y + 60, visible_width - 80, visible_height - 120)
    run_on_core_loop(open_window(wide_frame))
    try:
        time.sleep(1.5)
        before = run_on_core_loop(read_frame())
        strip = (visible_x + visible_width - 400, visible_y, 400, visible_height)
        connection_thread.send_reserved_screen_area(strip)
        docked = wait_for_frame(lambda frame: frame[0] + frame[2] <= strip[0] + 1)
        print("window ends at the docked strip:", docked[0] + docked[2] <= strip[0] + 1, "| before", before, "| docked", docked)
        connection_thread.send_reserved_screen_area(None)
        restored = wait_for_frame(lambda frame: abs(frame[2] - before[2]) <= 20)
        print("window got its room back:", abs(restored[2] - before[2]) <= 20, "| restored", restored)

        half_width = visible_width / 2
        left_half = {"monitor_id": "left-half", "name": "Left half", "frame": (visible_x, visible_y, half_width, visible_height),
                     "visible_frame": (visible_x, visible_y, half_width, visible_height)}
        right_half = {"monitor_id": "right-half", "name": "Right half", "frame": (visible_x + half_width, visible_y, half_width, visible_height),
                      "visible_frame": (visible_x + half_width, visible_y, half_width, visible_height)}
        connection_thread.send_monitors([left_half, right_half])
        small_frame = (visible_x + 50, visible_y + 100, 600, 400)
        run_on_core_loop(set_frame(small_frame))
        time.sleep(1)
        placed = run_on_core_loop(read_frame())
        connection_thread.submit_action(ActionRequest("move_window_to_monitor", {"window_id": state["window"].window_id},
                                                      {"monitor_id": "right-half"}))
        moved = wait_for_frame(lambda frame: frame[0] >= visible_x + half_width - 1)
        print("window moved into the right half with its size kept:",
              moved[0] >= visible_x + half_width - 1 and abs(moved[2] - placed[2]) <= 20, "| placed", placed, "| moved", moved)
    finally:
        connection_thread.send_monitors(real_monitors)

        async def close():
            await state["window"].async_close(force=True)
        run_on_core_loop(close())
    return 0


if __name__ == "__main__":
    sys.exit(main())
