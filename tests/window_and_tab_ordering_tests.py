import unittest

from agent_command_tab.tab_order_computation import compute_full_tab_order_after_move
from agent_command_tab.window_list_ordering import move_window_in_window_list_order, update_window_list_order


class UpdateWindowListOrderTests(unittest.TestCase):
    def test_startup_uses_given_order(self):
        self.assertEqual(update_window_list_order([], ["a", "b"]), ["a", "b"])

    def test_known_windows_keep_their_order_regardless_of_iterm2_order(self):
        self.assertEqual(update_window_list_order(["b", "a"], ["a", "b"]), ["b", "a"])

    def test_new_windows_go_on_top_and_closed_ones_drop_out(self):
        self.assertEqual(update_window_list_order(["b", "a", "c"], ["a", "c", "d", "e"]), ["d", "e", "a", "c"])


class MoveWindowTests(unittest.TestCase):
    def test_move_before_another(self):
        self.assertEqual(move_window_in_window_list_order(["a", "b", "c"], "c", "a"), ["c", "a", "b"])

    def test_move_to_end(self):
        self.assertEqual(move_window_in_window_list_order(["a", "b", "c"], "a", None), ["b", "c", "a"])

    def test_unknown_window_or_drop_on_itself_changes_nothing(self):
        self.assertEqual(move_window_in_window_list_order(["a", "b"], "z", "a"), ["a", "b"])
        self.assertEqual(move_window_in_window_list_order(["a", "b"], "b", "b"), ["a", "b"])


class ComputeFullTabOrderTests(unittest.TestCase):
    def test_hidden_tab_keeps_its_neighbor(self):
        # Tab "h" runs no agent and is not listed; it stays right after "a".
        self.assertEqual(compute_full_tab_order_after_move(["a", "h", "b", "c"], "c", "b"), ["a", "h", "c", "b"])

    def test_move_to_front(self):
        self.assertEqual(compute_full_tab_order_after_move(["a", "h", "b"], "b", "a"), ["b", "a", "h"])

    def test_drop_after_last(self):
        self.assertEqual(compute_full_tab_order_after_move(["a", "b", "h"], "a", None), ["b", "h", "a"])

    def test_tab_from_another_window(self):
        self.assertEqual(compute_full_tab_order_after_move(["x", "y"], "moved", "y"), ["x", "moved", "y"])


if __name__ == "__main__":
    unittest.main()
