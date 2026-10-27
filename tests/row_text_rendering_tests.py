import time
import unittest

from agent_command_tab.snapshot_data_classes import AgentSessionEntry, MonitorEntry, WindowEntry
from agent_command_tab.user_interface.row_text_rendering import (DOT_GLYPH_AND_COLOR_BY_STATE,
                                                                 LONGEST_TEXT_BY_COLUMN_FIELD_NAME,
                                                                 build_agent_session_row_text_parts,
                                                                 build_monitor_row_text_parts,
                                                                 build_tree_connector_text,
                                                                 build_tree_continuation_text,
                                                                 build_window_row_text_parts,
                                                                 format_clock_time, format_context_text,
                                                                 format_elapsed_time, format_memory_size)


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
        self.assertEqual(format_memory_size(512 * 1024 ** 2), "512M")
        self.assertEqual(format_memory_size(int(1.43 * 1024 ** 3)), "1.4G")
        self.assertEqual(format_elapsed_time(-3), "0s")
        self.assertEqual(format_elapsed_time(45), "45s")
        self.assertEqual(format_elapsed_time(185), "3m")
        self.assertEqual(format_elapsed_time(3600), "1h")
        self.assertEqual(format_elapsed_time(3900), "1h 5m")
        self.assertEqual(format_elapsed_time(2 * 86400 + 5), "2d")


class LongestColumnTextTests(unittest.TestCase):
    def test_no_column_text_is_longer_than_its_longest_text(self):
        """The dropdown's width stays put only if each format stays within its column's longest text."""
        longest_length = {field_name: len(text) for field_name, text in LONGEST_TEXT_BY_COLUMN_FIELD_NAME.items()}
        elapsed_seconds_samples = list(range(0, 2 * 86400, 7)) + [999 * 86400 + 5]
        self.assertLessEqual(max(len(format_elapsed_time(seconds)) for seconds in elapsed_seconds_samples),
                             min(longest_length["elapsed_time_text"], longest_length["existence_duration_text"]))
        self.assertLessEqual(max(len(format_context_text(context_tokens, context_fill_percent))
                                 for context_tokens in range(0, 2_000_000, 37)  # context windows hold up to 1M
                                 for context_fill_percent in (None, 9, 100)),
                             longest_length["context_text"])
        memory_bytes_samples = [megabytes * 1024 ** 2 for megabytes in range(0, 100 * 1024 - 52)] + [1024 ** 3 - 1]
        self.assertLessEqual(max(len(format_memory_size(memory_bytes)) for memory_bytes in memory_bytes_samples),
                             longest_length["memory_text"])
        day_start = time.mktime((2026, 10, 25, 0, 0, 0, 0, 0, -1))
        self.assertLessEqual(max(len(format_clock_time(day_start + minutes * 60, day_start + 12 * 3600))
                                 for minutes in range(0, 365 * 1440, 13)),
                             longest_length["created_time_text"])
        self.assertLessEqual(max(len(state) for state in list(DOT_GLYPH_AND_COLOR_BY_STATE) + ["running"]),
                             longest_length["state_text"])


class TreeConnectorTests(unittest.TestCase):
    def test_connectors(self):
        self.assertEqual(build_tree_connector_text([]), "")
        self.assertEqual(build_tree_connector_text([False]), "├─ ")
        self.assertEqual(build_tree_connector_text([True]), "└─ ")
        self.assertEqual(build_tree_connector_text([False, True]), "│  └─ ")
        self.assertEqual(build_tree_connector_text([True, False]), "   ├─ ")

    def test_continuation_under_a_row_carries_the_vertical_lines_down(self):
        self.assertEqual(build_tree_continuation_text([False]), "│  ")
        self.assertEqual(build_tree_continuation_text([True]), "   ")
        self.assertEqual(build_tree_continuation_text([False, True]), "│     ")


class RowTextPartsTests(unittest.TestCase):
    def test_session_row(self):
        parts = build_agent_session_row_text_parts(make_entry("working", status_since=100.0), [True], 280.0)
        self.assertEqual(parts.connector_text, "└─ ")
        self.assertEqual((parts.dot_glyph, parts.dot_color_name, parts.dot_blinks), ("●", "green", True))
        self.assertEqual(parts.name_text, "reviews")
        self.assertEqual((parts.agent_tool_name, parts.details_text, parts.state_text, parts.elapsed_time_text),
                         ("Claude Code", "", "working", "3m"))
        self.assertEqual(parts.context_text, "")  # the context's size isn't known yet
        filled_entry = make_entry("working", status_since=100.0)._replace(context_tokens=84_000)
        self.assertEqual(build_agent_session_row_text_parts(filled_entry, [True], 280.0).context_text, "84k")  # nor the window's
        filled_entry = filled_entry._replace(context_fill_percent=42)
        self.assertEqual(build_agent_session_row_text_parts(filled_entry, [True], 280.0).context_text, "84k (42%)")

    def test_a_session_waiting_on_its_command_shows_running_and_the_commands_time(self):
        entry = make_entry("working", status_since=100.0)._replace(running_command_name="python3",
                                                                    running_command_started_at=220.0)
        parts = build_agent_session_row_text_parts(entry, [True], 280.0)
        self.assertEqual((parts.state_text, parts.elapsed_time_text), ("running", "1m"))

    def test_created_time_and_existence_duration(self):
        now = time.mktime((2026, 10, 19, 15, 30, 0, 0, 0, -1))  # local time
        started_today = time.mktime((2026, 10, 19, 6, 8, 0, 0, 0, -1))
        parts = build_agent_session_row_text_parts(make_entry("idle")._replace(started_at=started_today), [True], now)
        self.assertEqual((parts.created_time_text, parts.existence_duration_text), ("6:08 AM", "9h 22m"))
        started_earlier = time.mktime((2026, 10, 7, 23, 0, 0, 0, 0, -1))
        parts = build_agent_session_row_text_parts(make_entry("idle")._replace(started_at=started_earlier), [True], now)
        self.assertEqual((parts.created_time_text, parts.existence_duration_text), ("Oct 7", "11d"))
        parts = build_agent_session_row_text_parts(make_entry("idle"), [True], now)  # start unknown
        self.assertEqual((parts.created_time_text, parts.existence_duration_text), ("", ""))

    def test_asking_dot(self):
        parts = build_agent_session_row_text_parts(make_entry("asking"), [False], 1.0)
        self.assertEqual((parts.dot_glyph, parts.dot_color_name, parts.dot_blinks), ("●", "light_blue", False))

    def test_window_row_expanded_and_collapsed(self):
        window = make_window(["idle", "asking", "idle"])
        expanded_parts = build_window_row_text_parts(window, [], True)
        self.assertEqual((expanded_parts.dot_color_name, expanded_parts.details_text), ("light_blue", ""))
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
