"""DashboardCore making room for a docked sidebar and giving it back, with stand-ins for iTerm2 windows."""

import asyncio
import collections
import os
import tempfile
import unittest
import unittest.mock

import iterm2

from agent_command_tab.docked_window_frames_store import load_docked_window_frames
from agent_command_tab.dashboard_core import DashboardCore

Point = collections.namedtuple("Point", ["x", "y"])
Size = collections.namedtuple("Size", ["width", "height"])
Frame = collections.namedtuple("Frame", ["origin", "size"])

# A 2000 x 1200 monitor with the sidebar docked to its right edge, 400 points wide.
RESERVED_AREA = {"frame": (1600, 0, 400, 1200), "edge": "right", "monitor_visible_frame": (0, 0, 2000, 1200)}


class StandInWindow:
    def __init__(self, window_id, frame, closes_when_set=False):
        self.window_id = window_id
        self.frame = frame
        self.closes_when_set = closes_when_set
        self.is_full_screen = False

    async def async_get_frame(self):
        x, y, width, height = self.frame
        return Frame(Point(x, y), Size(width, height))

    async def async_set_frame(self, frame):
        if self.closes_when_set:
            raise iterm2.SetPropertyException("window closed")
        self.frame = (frame.origin.x, frame.origin.y, frame.size.width, frame.size.height)

    async def async_get_fullscreen(self):
        return self.is_full_screen


class StandInApp:
    def __init__(self, windows):
        self.windows_by_id = {window.window_id: window for window in windows}

    def get_window_by_id(self, window_id):
        return self.windows_by_id.get(window_id)


def make_gathered_window(window_id, frame):
    return {"window_id": window_id, "terminal_name": "iTerm2", "frame": frame, "tabs": []}


class DockedSidebarWindowRoomTests(unittest.TestCase):
    def make_core(self, windows, cached_frames=None, docked_window_frames_file_path=None):
        core = DashboardCore(lambda snapshot: None, lambda message: None,
                             docked_window_frames_file_path=docked_window_frames_file_path)
        core.terminal_backend_by_name["iTerm2"].app = StandInApp(windows)
        cached_frames = cached_frames or {}
        core.gathered_windows = [make_gathered_window(window.window_id, cached_frames.get(window.window_id, window.frame))
                                 for window in windows]
        core.windows_were_gathered = True  # as after the first gather
        return core

    def test_docking_narrows_a_window_and_undocking_restores_it(self):
        window = StandInWindow("w1", (100, 50, 1800, 1000))
        core = self.make_core([window])
        asyncio.run(core.change_reserved_screen_area(RESERVED_AREA))
        self.assertEqual(window.frame, (100, 50, 1500, 1000))
        asyncio.run(core.change_reserved_screen_area(None))
        self.assertEqual(window.frame, (100, 50, 1800, 1000))
        self.assertEqual(core.frames_before_making_room_by_window_id, {})

    def test_docking_reads_the_live_frame_not_a_stale_cached_one(self):
        window = StandInWindow("w1", (100, 50, 400, 1000))  # moved by hand, away from the strip
        core = self.make_core([window], cached_frames={"w1": (1500, 50, 400, 1000)})
        asyncio.run(core.change_reserved_screen_area(RESERVED_AREA))
        self.assertEqual(window.frame, (100, 50, 400, 1000))
        self.assertEqual(core.frames_before_making_room_by_window_id, {})

    def test_a_window_moved_by_hand_while_docked_is_restored_to_where_the_user_put_it(self):
        window = StandInWindow("w1", (100, 50, 1800, 1000))
        core = self.make_core([window])
        asyncio.run(core.change_reserved_screen_area(RESERVED_AREA))
        window.frame = (1500, 50, 400, 1000)  # the user drags it into the strip
        asyncio.run(core.make_room_for_reserved_area())
        self.assertEqual(window.frame, (1200, 50, 400, 1000))
        asyncio.run(core.change_reserved_screen_area(None))
        self.assertEqual(window.frame, (1500, 50, 400, 1000))

    def test_a_full_screen_window_keeps_its_record_until_a_later_undocking(self):
        window = StandInWindow("w1", (100, 50, 1800, 1000))
        core = self.make_core([window])
        asyncio.run(core.change_reserved_screen_area(RESERVED_AREA))
        window.is_full_screen, narrowed_frame, window.frame = True, window.frame, (0, 0, 2000, 1200)
        asyncio.run(core.change_reserved_screen_area(None))
        self.assertIn("w1", core.frames_before_making_room_by_window_id)
        window.is_full_screen, window.frame = False, narrowed_frame  # leaves full screen at the narrowed frame
        asyncio.run(core.change_reserved_screen_area(RESERVED_AREA))
        asyncio.run(core.change_reserved_screen_area(None))
        self.assertEqual(window.frame, (100, 50, 1800, 1000))

    def test_a_restarted_dashboard_restores_windows_from_the_saved_frames(self):
        with tempfile.TemporaryDirectory() as folder:
            file_path = os.path.join(folder, "docked_window_frames.json")
            window = StandInWindow("w1", (100, 50, 1800, 1000))
            asyncio.run(self.make_core([window], docked_window_frames_file_path=file_path)
                        .change_reserved_screen_area(RESERVED_AREA))
            # iTerm2 restarted too: the window comes back narrowed, with a new ID.
            restored_window = StandInWindow("w1-after-restart", window.frame)
            core_after_restart = self.make_core([restored_window], docked_window_frames_file_path=file_path)
            asyncio.run(core_after_restart.change_reserved_screen_area(RESERVED_AREA))
            asyncio.run(core_after_restart.change_reserved_screen_area(None))
            self.assertEqual(restored_window.frame, (100, 50, 1800, 1000))
            self.assertEqual(load_docked_window_frames(file_path), {})

    def test_an_area_message_before_the_first_gather_keeps_the_saved_frames(self):  # code review, 2026-10-28
        with tempfile.TemporaryDirectory() as folder:
            file_path = os.path.join(folder, "docked_window_frames.json")
            window = StandInWindow("w1", (100, 50, 1800, 1000))
            asyncio.run(self.make_core([window], docked_window_frames_file_path=file_path)
                        .change_reserved_screen_area(RESERVED_AREA))
            # iTerm2 restarted too, and the app says docking is off before the windows are gathered.
            restored_window = StandInWindow("w1-after-restart", window.frame)
            core_after_restart = self.make_core([restored_window], docked_window_frames_file_path=file_path)
            core_after_restart.gathered_windows, core_after_restart.windows_were_gathered = [], False
            asyncio.run(core_after_restart.change_reserved_screen_area(None))
            self.assertEqual(len(load_docked_window_frames(file_path)), 1)  # kept for the restored window
            core_after_restart.gathered_windows = [make_gathered_window("w1-after-restart", restored_window.frame)]
            core_after_restart.windows_were_gathered = True
            asyncio.run(core_after_restart.make_room_for_reserved_area())  # as after the first gather
            self.assertEqual(restored_window.frame, (100, 50, 1800, 1000))

    def test_after_a_restart_with_docking_off_owed_room_is_given_back_once_the_app_says_so(self):
        with tempfile.TemporaryDirectory() as folder:
            file_path = os.path.join(folder, "docked_window_frames.json")
            window = StandInWindow("w1", (100, 50, 1800, 1000))
            asyncio.run(self.make_core([window], docked_window_frames_file_path=file_path)
                        .change_reserved_screen_area(RESERVED_AREA))
            core_after_restart = self.make_core([window], docked_window_frames_file_path=file_path)
            asyncio.run(core_after_restart.make_room_for_reserved_area())  # a gather before the app said anything
            self.assertEqual(window.frame, (100, 50, 1500, 1000))
            asyncio.run(core_after_restart.change_reserved_screen_area(None))  # the app: not docked
            self.assertEqual(window.frame, (100, 50, 1800, 1000))
            self.assertEqual(load_docked_window_frames(file_path), {})

    def test_a_gather_before_iterm2_is_connected_keeps_the_saved_frames(self):
        # The core gathers tmux's windows before iTerm2 connects; iTerm2's windows aren't closed then.
        with tempfile.TemporaryDirectory() as folder:
            file_path = os.path.join(folder, "docked_window_frames.json")
            window = StandInWindow("w1", (100, 50, 1800, 1000))
            asyncio.run(self.make_core([window], docked_window_frames_file_path=file_path)
                        .change_reserved_screen_area(RESERVED_AREA))
            core_after_restart = DashboardCore(lambda snapshot: None, lambda message: None,
                                               docked_window_frames_file_path=file_path)
            asyncio.run(core_after_restart.change_reserved_screen_area(None))  # the app: not docked
            tmux = core_after_restart.terminal_backend_by_name["tmux"]  # iTerm2's isn't connected
            tmux.server_is_running = True
            tmux.gather_windows = unittest.mock.AsyncMock(
                return_value=[{"window_id": "$1", "terminal_name": "tmux", "frame": None, "tabs": []}])
            core_after_restart.gathered_windows = asyncio.run(core_after_restart.gather_every_terminals_windows())
            asyncio.run(core_after_restart.make_room_for_reserved_area())  # as after that gather
            self.assertFalse(core_after_restart.windows_were_gathered)
            self.assertEqual(len(load_docked_window_frames(file_path)), 1)

    def test_a_new_window_never_takes_over_the_record_of_a_window_closed_meanwhile(self):
        closed_window = StandInWindow("w1", (100, 50, 1800, 1000))
        core = self.make_core([closed_window])
        asyncio.run(core.change_reserved_screen_area(RESERVED_AREA))
        new_window = StandInWindow("w2", (100, 50, 1500, 1000))  # the same size, for example a tab moved out
        core.terminal_backend_by_name["iTerm2"].app = StandInApp([new_window])
        core.gathered_windows = [make_gathered_window("w2", new_window.frame)]
        asyncio.run(core.make_room_for_reserved_area())
        asyncio.run(core.change_reserved_screen_area(None))
        self.assertEqual(new_window.frame, (100, 50, 1500, 1000))
        self.assertEqual(core.frames_before_making_room_by_window_id, {})

    def test_a_window_that_closes_while_being_resized_does_not_stop_the_others(self):
        closing_window = StandInWindow("w1", (100, 50, 1800, 1000), closes_when_set=True)
        other_window = StandInWindow("w2", (200, 60, 1700, 900))
        core = self.make_core([closing_window, other_window])
        asyncio.run(core.change_reserved_screen_area(RESERVED_AREA))
        self.assertEqual(other_window.frame, (200, 60, 1400, 900))
        self.assertNotIn("w1", core.frames_before_making_room_by_window_id)


if __name__ == "__main__":
    unittest.main()
