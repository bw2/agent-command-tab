import unittest

from llm_sidebar_dashboard.monitor_assignment import (assign_windows_to_monitors_by_largest_overlap,
                                                      compute_frame_clear_of_area,
                                                      compute_window_frame_on_another_monitor)

LEFT_MONITOR = (0, 0, 1000, 800)
RIGHT_MONITOR = (1000, 0, 1000, 800)
MONITOR_LEFT_OF_MAIN = (-1200, 100, 1200, 700)


class AssignWindowsToMonitorsTests(unittest.TestCase):
    def test_largest_overlap_wins(self):
        assignment = assign_windows_to_monitors_by_largest_overlap(
            {"mostly_right": (900, 100, 600, 400), "left": (10, 10, 100, 100)}, [LEFT_MONITOR, RIGHT_MONITOR])
        self.assertEqual(assignment, {"mostly_right": 1, "left": 0})

    def test_negative_coordinates(self):
        assignment = assign_windows_to_monitors_by_largest_overlap({"w": (-1100, 200, 500, 300)}, [LEFT_MONITOR, MONITOR_LEFT_OF_MAIN])
        self.assertEqual(assignment, {"w": 1})

    def test_tie_goes_to_the_earlier_monitor(self):
        assignment = assign_windows_to_monitors_by_largest_overlap({"w": (900, 100, 200, 100)}, [LEFT_MONITOR, RIGHT_MONITOR])
        self.assertEqual(assignment, {"w": 0})

    def test_off_screen_window_goes_to_nearest_monitor(self):
        assignment = assign_windows_to_monitors_by_largest_overlap({"w": (2500, 0, 300, 300)}, [LEFT_MONITOR, RIGHT_MONITOR])
        self.assertEqual(assignment, {"w": 1})

    def test_no_monitors(self):
        self.assertEqual(assign_windows_to_monitors_by_largest_overlap({"w": (0, 0, 1, 1)}, []), {})


class ComputeWindowFrameOnAnotherMonitorTests(unittest.TestCase):
    def test_keeps_size_and_relative_position(self):
        # Top-left corner of a 1000x800 monitor moves to the top-left corner of a 2000x1000 one.
        frame = compute_window_frame_on_another_monitor((0, 400, 500, 400), (0, 0, 1000, 800), (1000, 0, 2000, 1000))
        self.assertEqual(frame, (1000, 600, 500, 400))

    def test_centered_stays_centered(self):
        frame = compute_window_frame_on_another_monitor((250, 200, 500, 400), (0, 0, 1000, 800), (1000, 0, 2000, 1000))
        self.assertEqual(frame, (1750, 300, 500, 400))

    def test_too_big_window_shrinks_to_fit(self):
        frame = compute_window_frame_on_another_monitor((0, 0, 1900, 1100), (0, 0, 2000, 1200), MONITOR_LEFT_OF_MAIN)
        self.assertEqual(frame, (-1200, 100, 1200, 700))

    def test_window_filling_its_monitor(self):
        frame = compute_window_frame_on_another_monitor((0, 0, 1000, 800), (0, 0, 1000, 800), (1000, 0, 2000, 1000))
        self.assertEqual(frame, (1500, 100, 1000, 800))


class ComputeFrameClearOfAreaTests(unittest.TestCase):
    MONITOR = (0, 0, 2000, 1200)
    RIGHT_SIDEBAR = (1600, 0, 400, 1200)
    LEFT_SIDEBAR = (0, 0, 400, 1200)

    def clear_of_right_sidebar(self, window_frame, sidebar=RIGHT_SIDEBAR):
        return compute_frame_clear_of_area(window_frame, sidebar, "right", self.MONITOR, 300)

    def test_window_reaching_under_a_right_sidebar_gets_narrower(self):
        self.assertEqual(self.clear_of_right_sidebar((100, 50, 1800, 1000)), (100, 50, 1500, 1000))

    def test_window_clear_of_the_sidebar_is_left_alone(self):
        self.assertIsNone(self.clear_of_right_sidebar((100, 50, 1500, 1000)))

    def test_narrow_window_moves_instead_of_shrinking_below_the_smallest_width(self):
        self.assertEqual(self.clear_of_right_sidebar((1500, 50, 400, 1000)), (1300, 50, 300, 1000))

    def test_window_in_the_outer_half_of_a_wide_strip_moves_inward_not_off_screen(self):
        self.assertEqual(self.clear_of_right_sidebar((1500, 50, 400, 1000), sidebar=(800, 0, 1200, 1200)), (500, 50, 300, 1000))
        self.assertEqual(self.clear_of_right_sidebar((1850, 50, 500, 600)), (1300, 50, 300, 600))

    def test_window_hanging_off_the_monitor_edge_is_kept_on_it(self):
        self.assertEqual(compute_frame_clear_of_area((-100, 0, 2200, 1200), self.LEFT_SIDEBAR, "left", self.MONITOR, 300),
                         (400, 0, 1600, 1200))

    def test_left_sidebar(self):
        self.assertEqual(compute_frame_clear_of_area((0, 0, 2000, 1200), self.LEFT_SIDEBAR, "left", self.MONITOR, 300),
                         (400, 0, 1600, 1200))

    def test_window_on_another_monitor_is_left_alone(self):
        self.assertIsNone(self.clear_of_right_sidebar((2100, 0, 800, 600)))


if __name__ == "__main__":
    unittest.main()
