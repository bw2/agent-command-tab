import unittest

from agent_command_tab.session_status_computation import (compute_session_status_from_sources,
                                                          map_iterm2_status_value_to_state,
                                                          permission_dialog_was_answered,
                                                          pick_most_urgent_state,
                                                          status_change_ends_a_turn,
                                                          summarize_state_counts_in_urgency_order,
                                                          track_status_value,
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


def compute_state(iterm2_status_value, status_change_time, last_seen=None, seen_now=False, screen_reading=None, now=200,
                  last_turn_ended_at=None):
    return compute_session_status_from_sources(iterm2_status_value, status_change_time, last_turn_ended_at, last_seen,
                                               seen_now, screen_reading, STUCK_THRESHOLD_SECONDS, now)[0]


class ComputeSessionStatusTests(unittest.TestCase):
    def test_no_status_is_unknown(self):
        self.assertEqual(compute_state(None, 100), "unknown")

    # A status of done always comes with its turn's end (track_status_value), here at 100.
    def test_done_while_never_seen(self):
        self.assertEqual(compute_state("done", 100, last_turn_ended_at=100), "done")

    def test_done_after_last_seen_stays_done(self):
        self.assertEqual(compute_state("done", 100, last_seen=90, last_turn_ended_at=100), "done")

    def test_done_then_seen_becomes_idle(self):
        self.assertEqual(compute_state("done", 100, last_seen=110, last_turn_ended_at=100), "idle")

    def test_done_while_being_seen_is_idle(self):
        self.assertEqual(compute_state("done", 100, seen_now=True, last_turn_ended_at=100), "idle")

    def test_done_lasts_until_seen_when_the_status_then_says_idle(self):
        # The turn ended at 100; the status changed to idle at 150 without the tab being seen.
        self.assertEqual(compute_state("idle", 150, last_turn_ended_at=100), "done")
        self.assertEqual(compute_state("idle", 150, last_seen=90, last_turn_ended_at=100), "done")
        self.assertEqual(compute_state("idle", 150, last_seen=160, last_turn_ended_at=100), "idle")
        self.assertEqual(compute_state("idle", 150, seen_now=True, last_turn_ended_at=100), "idle")

    def test_a_turn_ending_in_idle_is_done_until_seen(self):
        # Codex goes from working straight to idle when its turn ends.
        self.assertEqual(compute_state("idle", 100, last_turn_ended_at=100), "done")
        self.assertEqual(compute_state("idle", 100, last_seen=100, last_turn_ended_at=100), "idle")

    def test_idle_without_an_ended_turn_is_idle(self):
        self.assertEqual(compute_state("idle", 100), "idle")

    def test_a_session_first_seen_as_done_stays_done_after_the_status_says_idle(self):
        tracking = track_status_value(None, "done", 100)  # the turn ended before the dashboard started
        self.assertEqual(tracking["last_turn_ended_at"], 100)
        tracking = track_status_value(tracking, "idle", 150)
        self.assertEqual(compute_state("idle", tracking["status_change_time"],
                                       last_turn_ended_at=tracking["last_turn_ended_at"]), "done")
        self.assertNotIn("last_turn_ended_at", track_status_value(None, "idle", 100))  # unknown: idle

    def test_tracking_records_turn_starts_and_ends_and_keeps_unchanged_values(self):
        tracking = track_status_value(None, "idle", 100)
        self.assertIs(track_status_value(tracking, "idle", 110), tracking)
        tracking = track_status_value(tracking, "working", 120)
        self.assertEqual((tracking["status_change_time"], tracking["turn_started_at"]), (120, 120))
        tracking = track_status_value(tracking, "waiting", 130)  # a permission prompt: the same turn
        tracking = track_status_value(tracking, "working", 140)
        self.assertEqual(tracking["turn_started_at"], 120)
        tracking = track_status_value(dict(tracking, last_time_tab_was_seen=125), "idle", 150)  # Codex's end of a turn
        self.assertEqual((tracking["last_turn_ended_at"], tracking["last_time_tab_was_seen"]), (150, 125))

    def test_a_permission_prompt_answered_no_or_esc_ends_the_turn_so_the_next_working_is_a_new_one(self):  # code review, 2026-10-28
        tracking = track_status_value(track_status_value(None, "idle", 100), "working", 120)
        tracking = track_status_value(tracking, "waiting", 142)
        answered_no = {"permission_dialog_is_showing": False, "permission_dialog_gone_since": 143,
                       "turn_was_interrupted": True}
        self.assertEqual(track_status_value(tracking, "working", 145, dict(answered_no, permission_dialog_checked_at=143))
                         ["turn_started_at"], 145)
        # A screen check from before this prompt appeared belongs to an earlier one.
        self.assertEqual(track_status_value(tracking, "working", 145, dict(answered_no, permission_dialog_checked_at=141))
                         ["turn_started_at"], 120)

    def test_status_changes_that_end_a_turn(self):
        self.assertTrue(status_change_ends_a_turn("working", "done"))
        self.assertTrue(status_change_ends_a_turn("working", "idle"))
        self.assertTrue(status_change_ends_a_turn("asking", "idle"))  # Codex after "No" or Esc
        self.assertTrue(status_change_ends_a_turn(None, "done"))
        self.assertFalse(status_change_ends_a_turn("done", "idle"))  # the turn ended at done
        self.assertFalse(status_change_ends_a_turn(None, "idle"))  # a new session at its input box
        self.assertFalse(status_change_ends_a_turn("idle", "working"))

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

    def test_waiting_on_a_long_command_is_never_stuck(self):
        reading = {"fingerprint_changed_at": 150, "running_command": ("python3", 160), "command_last_seen_running_at": 2000}
        self.assertEqual(compute_session_status_from_sources("working", 100, None, None, False, reading, 600, 2000),
                         ("working", "running python3"))

    def test_waiting_on_an_open_tool_call_is_stuck_only_for_a_tool_that_finishes_in_seconds(self):  # user request, 2026-10-30
        reading = {"fingerprint_changed_at": 150, "running_command": None,
                   "conversation_preview": {"open_tool_name": "WebFetch", "open_tool_started_at": 1500}}
        self.assertEqual(compute_session_status_from_sources("working", 100, None, None, False, reading, 600, 2000),
                         ("working", "running WebFetch"))  # open for 500 seconds, under the threshold
        self.assertEqual(compute_session_status_from_sources("working", 100, None, None, False, reading, 600, 2220),
                         ("stuck", "waiting on WebFetch for 12 min"))
        for tool_that_may_run_long in ("mcp__github__search_code", "Agent"):  # never stuck, however long
            reading["conversation_preview"] = {"open_tool_name": tool_that_may_run_long, "open_tool_started_at": 160}
            self.assertEqual(compute_session_status_from_sources("working", 100, None, None, False, reading, 600, 5000),
                             ("working", f"running {tool_that_may_run_long}"))
        reading["conversation_preview"] = {"open_tool_name": None, "open_tool_started_at": None}  # the call returned
        self.assertEqual(compute_state("working", 100, screen_reading=reading, now=2000), "stuck")

    def test_the_stuck_clock_restarts_when_the_command_ends(self):
        # A silent 30-minute command ended at 1900; the agent has been thinking for 100 seconds since.
        reading = {"fingerprint_changed_at": 150, "running_command": None, "command_last_seen_running_at": 1900}
        self.assertEqual(compute_state("working", 100, screen_reading=reading, now=2000), "working")
        self.assertEqual(compute_state("working", 100, screen_reading=reading, now=2500), "stuck")

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
