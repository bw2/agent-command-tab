import unittest

from llm_sidebar_dashboard.session_status_computation import (compute_session_status_from_sources,
                                                              map_iterm2_status_value_to_state,
                                                              permission_dialog_was_answered,
                                                              pick_most_urgent_state,
                                                              summarize_state_counts_in_urgency_order,
                                                              update_permission_dialog_reading)


class MapIterm2StatusValueTests(unittest.TestCase):
    def test_osc_7501_words(self):
        self.assertEqual(map_iterm2_status_value_to_state("idle"), "idle")
        self.assertEqual(map_iterm2_status_value_to_state("working"), "working")
        self.assertEqual(map_iterm2_status_value_to_state("waiting"), "asking")
        self.assertEqual(map_iterm2_status_value_to_state("done"), "done")
        self.assertEqual(map_iterm2_status_value_to_state("error"), "error")

    def test_osc_21337_words_case_insensitive(self):
        self.assertEqual(map_iterm2_status_value_to_state("Working"), "working")
        self.assertEqual(map_iterm2_status_value_to_state(" Waiting "), "asking")

    def test_free_text_and_unset_are_no_signal(self):
        self.assertIsNone(map_iterm2_status_value_to_state("Compiling"))
        self.assertIsNone(map_iterm2_status_value_to_state(""))
        self.assertIsNone(map_iterm2_status_value_to_state(None))


STUCK_THRESHOLD_SECONDS = 600


def compute_state(iterm2_status_value, status_change_time, last_seen=None, seen_now=False, screen_reading=None, now=200):
    return compute_session_status_from_sources(iterm2_status_value, status_change_time, last_seen, seen_now,
                                               screen_reading, STUCK_THRESHOLD_SECONDS, now)[0]


class ComputeSessionStatusTests(unittest.TestCase):
    def test_no_status_is_unknown(self):
        self.assertEqual(compute_state(None, 100), "unknown")

    def test_done_while_never_seen(self):
        self.assertEqual(compute_state("done", 100), "done")

    def test_done_after_last_seen_stays_done(self):
        self.assertEqual(compute_state("done", 100, last_seen=90), "done")

    def test_done_then_seen_becomes_idle(self):
        self.assertEqual(compute_state("done", 100, last_seen=110), "idle")

    def test_done_while_being_seen_is_idle(self):
        self.assertEqual(compute_state("done", 100, seen_now=True), "idle")

    def test_working_and_asking_pass_through(self):
        self.assertEqual(compute_state("working", 100, last_seen=110, seen_now=True), "working")
        self.assertEqual(compute_state("waiting", 100), "asking")

    def test_asking_ends_when_the_permission_dialog_is_gone(self):
        answered = {"permission_dialog_is_showing": False, "permission_dialog_checked_at": 105,
                    "permission_dialog_gone_since": 105}
        self.assertEqual(compute_state("waiting", 100, screen_reading=answered), "working")

    def test_asking_stays_while_the_dialog_was_never_recognized(self):
        never_seen = {"permission_dialog_is_showing": False, "permission_dialog_checked_at": 105}
        self.assertEqual(compute_state("waiting", 100, screen_reading=never_seen), "asking")

    def test_asking_stays_while_the_dialog_shows_or_before_any_check_after_the_change(self):
        self.assertEqual(compute_state("waiting", 100, screen_reading={"permission_dialog_is_showing": True,
                                                                       "permission_dialog_checked_at": 105}), "asking")
        self.assertEqual(compute_state("waiting", 100, screen_reading={"permission_dialog_is_showing": False,
                                                                       "permission_dialog_checked_at": 95}), "asking")

    def test_stuck_after_the_threshold_without_screen_change(self):
        self.assertEqual(compute_state("working", 100, screen_reading={"fingerprint_changed_at": 150}, now=749), "working")
        self.assertEqual(compute_state("working", 100, screen_reading={"fingerprint_changed_at": 150}, now=750), "stuck")

    def test_a_new_working_period_starts_the_stuck_clock_again(self):
        self.assertEqual(compute_state("working", 1000, screen_reading={"fingerprint_changed_at": 100}, now=1100), "working")

    def test_answered_permission_can_become_stuck(self):
        reading = {"permission_dialog_is_showing": False, "permission_dialog_checked_at": 105,
                   "permission_dialog_gone_since": 104, "fingerprint_changed_at": 100}
        self.assertEqual(compute_state("waiting", 100, screen_reading=reading, now=800), "stuck")

    def test_stuck_clock_after_an_approval_starts_when_the_dialog_went_away(self):
        # The prompt waited 11 minutes for the user; the approved tool has only run for 2 minutes since.
        reading = {"permission_dialog_is_showing": False, "permission_dialog_checked_at": 762,
                   "permission_dialog_gone_since": 760, "fingerprint_changed_at": 90}
        self.assertEqual(compute_state("waiting", 100, screen_reading=reading, now=880), "working")

    def test_denied_permission_waits_for_instructions(self):
        reading = {"permission_dialog_is_showing": False, "permission_dialog_checked_at": 105,
                   "permission_dialog_gone_since": 105, "turn_was_interrupted": True}
        self.assertEqual(compute_state("waiting", 100, screen_reading=reading), "idle")


class PermissionDialogReadingTests(unittest.TestCase):
    """One asking period: iTerm2's status turned "waiting" at time 100; checks follow every few seconds."""

    def check(self, reading, dialog_is_showing, interrupted_line_count, now):
        return update_permission_dialog_reading(reading, dialog_is_showing, interrupted_line_count, 100, now)

    def answered(self, reading):
        return permission_dialog_was_answered("asking", 100, reading)

    def test_dialog_seen_then_gone_means_approved(self):
        reading = self.check({}, True, 1, 101)
        self.assertFalse(self.answered(reading))
        reading = self.check(reading, False, 1, 103)
        self.assertTrue(self.answered(reading))
        self.assertEqual((reading["permission_dialog_gone_since"], reading["turn_was_interrupted"]), (103, False))
        self.assertEqual(self.check(reading, False, 1, 105)["permission_dialog_gone_since"], 103)

    def test_dialog_seen_then_new_interrupted_line_means_denied(self):
        reading = self.check(self.check({}, True, 1, 101), False, 2, 103)
        self.assertTrue(self.answered(reading))
        self.assertTrue(reading["turn_was_interrupted"])

    def test_dialog_never_recognized_keeps_asking(self):
        reading = self.check(self.check({}, False, 0, 101), False, 0, 103)
        self.assertFalse(self.answered(reading))

    def test_denied_before_the_dialog_was_ever_recognized(self):
        reading = self.check(self.check({}, False, 0, 100.1), False, 1, 102)
        self.assertTrue(self.answered(reading))
        self.assertTrue(reading["turn_was_interrupted"])

    def test_dialog_shown_again_is_asking_again(self):
        reading = self.check(self.check(self.check({}, True, 0, 101), False, 0, 103), True, 0, 105)
        self.assertFalse(self.answered(reading))

    def test_a_new_prompt_forgets_the_last_answer(self):
        earlier_period = update_permission_dialog_reading(update_permission_dialog_reading({}, True, 0, 90, 91), False, 0, 90, 93)
        reading = self.check(earlier_period, False, 0, 101)
        self.assertFalse(self.answered(reading))
        self.assertNotIn("permission_dialog_gone_since", reading)
        self.assertIn("permission_dialog_gone_since", earlier_period)  # the input is not changed


class UrgencyTests(unittest.TestCase):
    def test_most_urgent(self):
        self.assertEqual(pick_most_urgent_state(["idle", "working", "asking"]), "asking")
        self.assertEqual(pick_most_urgent_state(["done", "idle"]), "done")
        self.assertEqual(pick_most_urgent_state([]), "unknown")

    def test_counts_in_urgency_order(self):
        self.assertEqual(summarize_state_counts_in_urgency_order(["idle", "working", "idle", "asking"]),
                         "1 asking, 1 working, 2 idle")


if __name__ == "__main__":
    unittest.main()
