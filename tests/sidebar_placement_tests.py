import unittest

from llm_sidebar_dashboard.user_interface.sidebar_placement import (choose_sidebar_edge, compute_dragged_sidebar_width,
                                                                    compute_sidebar_frame,
                                                                    describe_sidebar_placement_fallback,
                                                                    find_outer_screen_edges, pointer_is_at_edge)

LAPTOP = {"name": "Built-in", "frame": (0, 0, 1500, 1000), "visible_frame": (0, 0, 1500, 970)}
RIGHT_DISPLAY = {"name": "Studio", "frame": (1500, -200, 2500, 1400), "visible_frame": (1500, -200, 2500, 1400)}
LEFT_DISPLAY = {"name": "Dell", "frame": (-1920, 0, 1920, 1080), "visible_frame": (-1920, 0, 1920, 1080)}
DISPLAY_ABOVE = {"name": "Above", "frame": (0, 1000, 1500, 900), "visible_frame": (0, 1000, 1500, 900)}


class FindOuterScreenEdgesTests(unittest.TestCase):
    def test_one_screen_has_both_edges(self):
        self.assertEqual(find_outer_screen_edges([LAPTOP["frame"]]), [(0, "right"), (0, "left")])

    def test_shared_edge_is_dropped(self):
        self.assertEqual(find_outer_screen_edges([LAPTOP["frame"], RIGHT_DISPLAY["frame"]]), [(0, "left"), (1, "right")])

    def test_screen_stacked_above_shares_no_side_edge(self):
        self.assertEqual(len(find_outer_screen_edges([LAPTOP["frame"], DISPLAY_ABOVE["frame"]])), 4)


class ChooseSidebarEdgeTests(unittest.TestCase):
    def test_default_is_the_right_edge_of_the_menu_bar_monitor(self):
        self.assertEqual(tuple(choose_sidebar_edge("", "right", [LAPTOP])), (0, "right", True))

    def test_saved_monitor_and_edge(self):
        self.assertEqual(tuple(choose_sidebar_edge("Studio", "right", [LAPTOP, RIGHT_DISPLAY])), (1, "right", True))

    def test_saved_edge_no_longer_outer_uses_the_other_edge_of_that_monitor(self):
        self.assertEqual(tuple(choose_sidebar_edge("", "right", [LAPTOP, RIGHT_DISPLAY])), (0, "left", False))

    def test_unplugged_saved_monitor_falls_back_to_the_menu_bar_monitor(self):
        self.assertEqual(tuple(choose_sidebar_edge("Studio", "right", [LAPTOP])), (0, "right", False))

    def test_menu_bar_monitor_in_the_middle_of_three(self):
        placement = choose_sidebar_edge("", "right", [LAPTOP, LEFT_DISPLAY, RIGHT_DISPLAY])
        self.assertEqual(tuple(placement), (2, "right", False))

    def test_no_screens(self):
        self.assertIsNone(choose_sidebar_edge("", "right", []))


class PlacementFallbackNoteTests(unittest.TestCase):
    def test_no_note_when_the_saved_choice_works(self):
        self.assertEqual(describe_sidebar_placement_fallback("Studio", "right", [LAPTOP, RIGHT_DISPLAY]), "")

    def test_unplugged_monitor(self):
        self.assertEqual(describe_sidebar_placement_fallback("Studio", "right", [LAPTOP]),
                         "Studio isn't connected, so the sidebar is on the right edge of Built-in until it is.")

    def test_edge_that_borders_another_monitor(self):
        self.assertEqual(describe_sidebar_placement_fallback("", "right", [LAPTOP, RIGHT_DISPLAY]),
                         "The saved edge borders another monitor, so the sidebar is on the left edge of Built-in.")


class SidebarFrameAndEdgeTests(unittest.TestCase):
    def test_frames(self):
        self.assertEqual(compute_sidebar_frame((0, 0, 1500, 970), (0, 0, 1500, 1000), "right", 300, True), (1200, 0, 300, 970))
        self.assertEqual(compute_sidebar_frame((0, 0, 1500, 970), (0, 0, 1500, 1000), "right", 300, False), (1500, 0, 300, 970))
        self.assertEqual(compute_sidebar_frame((-1920, 0, 1920, 1080), (-1920, 0, 1920, 1080), "left", 300, True), (-1920, 0, 300, 1080))
        self.assertEqual(compute_sidebar_frame((-1920, 0, 1920, 1080), (-1920, 0, 1920, 1080), "left", 300, False), (-2220, 0, 300, 1080))

    def test_hidden_sidebar_parks_past_a_dock_on_its_edge(self):
        usable_area_left_of_dock = (0, 0, 1430, 970)
        self.assertEqual(compute_sidebar_frame(usable_area_left_of_dock, (0, 0, 1500, 1000), "right", 400, True), (1030, 0, 400, 970))
        self.assertEqual(compute_sidebar_frame(usable_area_left_of_dock, (0, 0, 1500, 1000), "right", 400, False), (1500, 0, 400, 970))

    def test_dragged_width(self):
        self.assertEqual(compute_dragged_sidebar_width(400, 1100, 1000, "right", 200, 1200), 500)
        self.assertEqual(compute_dragged_sidebar_width(400, 1100, 1250.4, "right", 200, 1200), 250)
        self.assertEqual(compute_dragged_sidebar_width(400, 400, 500, "left", 200, 1200), 500)
        self.assertEqual(compute_dragged_sidebar_width(400, 1100, 1490, "right", 200, 1200), 200)
        self.assertEqual(compute_dragged_sidebar_width(400, 400, 2000, "left", 200, 1200), 1200)

    def test_pointer_at_edge(self):
        self.assertTrue(pointer_is_at_edge((1499.5, 500), LAPTOP["frame"], "right"))
        self.assertFalse(pointer_is_at_edge((1490, 500), LAPTOP["frame"], "right"))
        self.assertFalse(pointer_is_at_edge((1499.5, 1500), LAPTOP["frame"], "right"))
        self.assertTrue(pointer_is_at_edge((-1920, 10), LEFT_DISPLAY["frame"], "left"))


if __name__ == "__main__":
    unittest.main()
