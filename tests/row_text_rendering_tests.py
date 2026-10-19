import unittest

from llm_sidebar_dashboard.snapshot_data_classes import AgentSessionEntry, MonitorEntry, WindowEntry
from llm_sidebar_dashboard.user_interface.row_text_rendering import (build_agent_session_row_text_parts,
                                                                     build_monitor_row_text_parts,
                                                                     build_tree_connector_text,
                                                                     build_window_row_text_parts,
                                                                     format_elapsed_time)


def make_entry(status, tab_title="reviews", status_since=0.0):
    return AgentSessionEntry(tab_id="1", session_id="s1", tab_title=tab_title, tab_title_set_by_user=False,
                             agent_session_name="", agent_tool_name="Claude Code", working_folder="/x",
                             status=status, status_since=status_since)


def make_window(statuses):
    return WindowEntry(window_id="w", title="project", window_title_set_by_user=False, frame=(0, 0, 1, 1),
                       last_prompt_time=None, total_tab_count=len(statuses),
                       agent_sessions=tuple(make_entry(status) for status in statuses))


class FormatElapsedTimeTests(unittest.TestCase):
    def test_ranges(self):
        self.assertEqual(format_elapsed_time(-3), "0s")
        self.assertEqual(format_elapsed_time(45), "45s")
        self.assertEqual(format_elapsed_time(185), "3m")
        self.assertEqual(format_elapsed_time(3600), "1h")
        self.assertEqual(format_elapsed_time(3900), "1h 5m")
        self.assertEqual(format_elapsed_time(2 * 86400 + 5), "2d")


class TreeConnectorTests(unittest.TestCase):
    def test_connectors(self):
        self.assertEqual(build_tree_connector_text([]), "")
        self.assertEqual(build_tree_connector_text([False]), "├─ ")
        self.assertEqual(build_tree_connector_text([True]), "└─ ")
        self.assertEqual(build_tree_connector_text([False, True]), "│  └─ ")
        self.assertEqual(build_tree_connector_text([True, False]), "   ├─ ")


class RowTextPartsTests(unittest.TestCase):
    def test_session_row(self):
        parts = build_agent_session_row_text_parts(make_entry("working", status_since=100.0), [True], 280.0)
        self.assertEqual(parts.connector_text, "└─ ")
        self.assertEqual((parts.dot_glyph, parts.dot_color_name, parts.dot_blinks), ("●", "green", True))
        self.assertEqual(parts.name_text, "reviews")
        self.assertEqual((parts.agent_tool_name, parts.details_text, parts.state_text, parts.elapsed_time_text),
                         ("Claude Code", "", "working", "3m"))

    def test_asking_dot(self):
        parts = build_agent_session_row_text_parts(make_entry("asking"), [False], 1.0)
        self.assertEqual((parts.dot_glyph, parts.dot_color_name, parts.dot_blinks), ("●", "purple", False))

    def test_window_row_expanded_and_collapsed(self):
        window = make_window(["idle", "asking", "idle"])
        expanded_parts = build_window_row_text_parts(window, [], True)
        self.assertEqual((expanded_parts.dot_color_name, expanded_parts.details_text), ("purple", ""))
        collapsed_parts = build_window_row_text_parts(window, [], False)
        self.assertEqual(collapsed_parts.details_text, "1 asking, 2 idle")

    def test_window_dot_blinks_while_its_most_urgent_tab_is_working(self):
        self.assertTrue(build_window_row_text_parts(make_window(["idle", "working"]), [], True).dot_blinks)
        self.assertFalse(build_window_row_text_parts(make_window(["asking", "working"]), [], True).dot_blinks)
        self.assertFalse(build_window_row_text_parts(make_window(["idle"]), [], True).dot_blinks)

    def test_monitor_row(self):
        monitor = MonitorEntry("m", "Built-in", (0, 0, 1, 1), (0, 0, 1, 1), (make_window(["idle"]),))
        self.assertEqual(build_monitor_row_text_parts(monitor).details_text, "1 window")


if __name__ == "__main__":
    unittest.main()
