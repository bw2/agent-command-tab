import unittest

from agent_command_tab.list_sorting import sort_agent_session_entries, sort_window_entries
from agent_command_tab.snapshot_data_classes import AgentSessionEntry, WindowEntry


def make_entry(tab_id, tab_title, status, status_since):
    return AgentSessionEntry(tab_id=tab_id, session_id=f"s{tab_id}", tab_title=tab_title, tab_title_set_by_user=False,
                             agent_session_name="", agent_tool_name="Claude Code", working_folder="/x",
                             status=status, status_since=status_since)


def make_window(window_id, title, agent_sessions):
    return WindowEntry(window_id=window_id, title=title, window_title_set_by_user=False, frame=(0, 0, 1, 1),
                       last_prompt_time=None, total_tab_count=len(agent_sessions), agent_sessions=tuple(agent_sessions))


# In iTerm2's tab order.
ENTRIES = [make_entry("1", "planning", "idle", 100.0), make_entry("2", "Reviews", "working", 50.0),
           make_entry("3", "api", "asking", 10.0), make_entry("4", "docs", "working", 80.0)]


class SortAgentSessionEntriesTests(unittest.TestCase):
    def test_manual_keeps_the_tab_order(self):
        self.assertEqual([entry.tab_id for entry in sort_agent_session_entries(ENTRIES, "manual")], ["1", "2", "3", "4"])

    def test_activity_puts_the_most_urgent_first_then_the_latest_change(self):
        # asking, then the two working (docs changed state later than Reviews), then idle
        self.assertEqual([entry.tab_id for entry in sort_agent_session_entries(ENTRIES, "activity")], ["3", "4", "2", "1"])

    def test_alphabet_ignores_case(self):
        self.assertEqual([entry.tab_title for entry in sort_agent_session_entries(ENTRIES, "alphabet")],
                         ["api", "docs", "planning", "Reviews"])

    def test_created_time_puts_the_newest_first_and_unknown_starts_last(self):
        entries = [ENTRIES[0]._replace(started_at=1000.0), ENTRIES[1], ENTRIES[2]._replace(started_at=3000.0),
                   ENTRIES[3]._replace(started_at=2000.0)]
        self.assertEqual([entry.tab_id for entry in sort_agent_session_entries(entries, "created_time")], ["3", "4", "1", "2"])

    def test_sorting_leaves_the_manual_order_alone(self):
        sort_agent_session_entries(ENTRIES, "alphabet")
        self.assertEqual([entry.tab_id for entry in ENTRIES], ["1", "2", "3", "4"])


class SortWindowEntriesTests(unittest.TestCase):
    def setUp(self):
        self.windows = [make_window("w1", "zebra", [ENTRIES[0]]), make_window("w2", "Alpha", [ENTRIES[1], ENTRIES[0]]),
                        make_window("w3", "middle", [ENTRIES[2]]), make_window("w4", "empty", [])]

    def test_manual_keeps_the_dragged_order(self):
        self.assertEqual([window.window_id for window in sort_window_entries(self.windows, "manual")], ["w1", "w2", "w3", "w4"])

    def test_activity_orders_windows_by_their_most_active_tab(self):
        self.assertEqual([window.window_id for window in sort_window_entries(self.windows, "activity")], ["w3", "w2", "w1", "w4"])

    def test_alphabet_by_window_title(self):
        self.assertEqual([window.title for window in sort_window_entries(self.windows, "alphabet")],
                         ["Alpha", "empty", "middle", "zebra"])


if __name__ == "__main__":
    unittest.main()
