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

    def test_memory_usage_puts_the_largest_first_and_unknown_usage_last(self):
        entries = [ENTRIES[0]._replace(memory_bytes=200 * 1024 ** 2), ENTRIES[1], ENTRIES[2]._replace(memory_bytes=900 * 1024 ** 2),
                   ENTRIES[3]._replace(memory_bytes=0)]
        self.assertEqual([entry.tab_id for entry in sort_agent_session_entries(entries, "memory_usage")], ["3", "1", "4", "2"])

    def test_cpu_usage_puts_the_busiest_first_and_unknown_usage_last(self):
        entries = [ENTRIES[0]._replace(cpu_percent=0), ENTRIES[1]._replace(cpu_percent=150), ENTRIES[2],
                   ENTRIES[3]._replace(cpu_percent=16)]
        self.assertEqual([entry.tab_id for entry in sort_agent_session_entries(entries, "cpu_usage")], ["2", "4", "1", "3"])

    def test_tokens_put_the_fullest_context_window_first_and_unknown_shares_last(self):
        # 120k of a 200k window is fuller than 467k of a 1M one.
        entries = [ENTRIES[0]._replace(context_tokens=120_000, context_fill_percent=60), ENTRIES[1],
                   ENTRIES[2]._replace(context_tokens=467_000, context_fill_percent=47),
                   ENTRIES[3]._replace(context_tokens=30_000, context_fill_percent=3)]
        self.assertEqual([entry.tab_id for entry in sort_agent_session_entries(entries, "context_percent")], ["1", "3", "4", "2"])

    def test_status_change_puts_the_latest_change_first(self):
        self.assertEqual([entry.tab_id for entry in sort_agent_session_entries(ENTRIES, "state_duration")], ["1", "4", "2", "3"])

    def test_status_change_goes_by_a_running_commands_start_as_the_row_shows(self):
        # Reviews has worked since 50 and runs pytest since 120, so its row shows the shortest time.
        entries = [ENTRIES[0], ENTRIES[1]._replace(running_command_name="pytest", running_command_started_at=120.0), *ENTRIES[2:]]
        self.assertEqual([entry.tab_id for entry in sort_agent_session_entries(entries, "state_duration")], ["2", "1", "4", "3"])
        # Activity too: among the two working, Reviews's shown change (120) is now later than docs's (80).
        self.assertEqual([entry.tab_id for entry in sort_agent_session_entries(entries, "activity")], ["3", "2", "4", "1"])

    def test_reversed_orders_keep_unknown_values_last_and_equal_ones_in_tab_order(self):
        entries = [ENTRIES[0]._replace(memory_bytes=200), ENTRIES[1], ENTRIES[2]._replace(memory_bytes=900),
                   ENTRIES[3]._replace(memory_bytes=200)]
        self.assertEqual([entry.tab_id for entry in sort_agent_session_entries(entries, "memory_usage", is_reversed=True)],
                         ["1", "4", "3", "2"])
        self.assertEqual([entry.tab_title for entry in sort_agent_session_entries(ENTRIES, "alphabet", is_reversed=True)],
                         ["Reviews", "planning", "docs", "api"])
        self.assertEqual([entry.tab_id for entry in sort_agent_session_entries(ENTRIES, "manual", is_reversed=True)],
                         ["1", "2", "3", "4"])  # the manual order has no other direction

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

    def test_memory_usage_orders_windows_by_their_largest_tab(self):
        windows = [make_window("w1", "one", [ENTRIES[0]._replace(memory_bytes=300)]),
                   make_window("w2", "two", [ENTRIES[1]._replace(memory_bytes=100), ENTRIES[2]._replace(memory_bytes=500)]),
                   make_window("w3", "three", [ENTRIES[3]]), make_window("w4", "empty", [])]
        self.assertEqual([window.window_id for window in sort_window_entries(windows, "memory_usage")], ["w2", "w1", "w3", "w4"])

    def test_reversed_orders_rank_windows_by_their_first_tab_in_that_direction(self):
        # w1's 300 bytes lie between w2's tabs, so w2 goes first both ways only if it is ranked by
        # its largest tab, then by its smallest.
        windows = [make_window("w1", "one", [ENTRIES[0]._replace(memory_bytes=300)]),
                   make_window("w2", "two", [ENTRIES[1]._replace(memory_bytes=100), ENTRIES[2]._replace(memory_bytes=500)]),
                   make_window("w3", "three", [ENTRIES[3]]), make_window("w4", "empty", [])]
        self.assertEqual([window.window_id for window in sort_window_entries(windows, "memory_usage")], ["w2", "w1", "w3", "w4"])
        # The smallest first: w2's smallest tab (100), then w1; unknown usage and no tabs still last.
        self.assertEqual([window.window_id for window in sort_window_entries(windows, "memory_usage", is_reversed=True)],
                         ["w2", "w1", "w3", "w4"])
        self.assertEqual([window.title for window in sort_window_entries(windows, "alphabet", is_reversed=True)],
                         ["two", "three", "one", "empty"])

    def test_a_window_ranks_by_the_agent_sessions_the_list_shows(self):
        # With Hide Idle on, w1's idle tab (the largest) is hidden, so w2's 800M tab comes first.
        windows = [make_window("w1", "one", [ENTRIES[0]._replace(memory_bytes=1200), ENTRIES[1]._replace(memory_bytes=300)]),
                   make_window("w2", "two", [ENTRIES[2]._replace(memory_bytes=800)])]
        self.assertEqual([window.window_id for window in sort_window_entries(windows, "memory_usage")], ["w1", "w2"])
        self.assertEqual([window.window_id for window in sort_window_entries(
            windows, "memory_usage", lambda window_entry: [entry for entry in window_entry.agent_sessions if entry.status != "idle"])],
            ["w2", "w1"])

    def test_alphabet_by_window_title(self):
        self.assertEqual([window.title for window in sort_window_entries(self.windows, "alphabet")],
                         ["Alpha", "empty", "middle", "zebra"])


if __name__ == "__main__":
    unittest.main()
