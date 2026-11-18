"""The first turn of an agent whose files showed no message in its conversation (agy, whose
conversation has no transcript before its first message): once the turn is seen, its row isn't
"new" again when the turn ends, and its conversation file is looked up at once
(DashboardCore.note_that_the_agent_has_had_a_message)."""

import asyncio
import os
import unittest
import unittest.mock

from agent_command_tab import dashboard_core
from agent_command_tab.agent_adapters.agy import AgyAdapter
from agent_command_tab.dashboard_core import DashboardCore
from gathered_window_builders import make_gathered_tab, make_gathered_window

# A conversation agy really wrote, whose last reply gives 9,482 context tokens.
RECORDED_AGY_TRANSCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "recorded_agent_files", "agy", "1.5.10",
                                       "transcript.jsonl")


class StandInAgyAdapter(AgyAdapter):
    """An agy in no conversation until transcript_path is set, as its first message does."""

    transcript_path = None
    lookup_count = 0

    async def find_conversation_and_transcript(self, agent_process_id):
        self.lookup_count += 1
        return (None, None) if self.transcript_path is None else ("its-conversation", self.transcript_path)


class FirstTurnOfAnAgentWithNoMessageYetTests(unittest.TestCase):
    """A core with one iTerm2 tab, s1, in front all along (so a turn that ends is seen, and its
    row "idle" rather than "done"), running a fresh agy as process 101, whose files have been
    read once: they show no message."""

    def setUp(self):
        self.snapshots = []
        self.core = DashboardCore(self.snapshots.append, lambda message: None)
        self.adapter = StandInAgyAdapter()
        self.core.specific_adapters = [self.adapter]
        self.core.find_seen_session_id = lambda: "s1"
        self.core.gathered_windows = [make_gathered_window("w1", [make_gathered_tab("t1", "s1", "agy", 101)])]
        for name, replacement in (("process_is_alive", lambda process_id: True), ("process_is_stopped", lambda process_id: False),
                                  ("list_descendant_processes", lambda process_id, maximum_depth=None: [])):
            patch = unittest.mock.patch.object(dashboard_core, name, replacement)
            patch.start()
            self.addCleanup(patch.stop)
        self.publish()
        self.read_the_conversation_file()
        self.core.conversation_file_reading_needed.clear()  # as the periodic reading does once it has read

    def publish(self, status_set_in_iterm2=None):
        """Publishes a snapshot with this status value set in iTerm2 for the tab; returns the tab's row."""
        self.core.gathered_windows[0]["tabs"][0]["first_session"]["status_set_in_iterm2"] = status_set_in_iterm2
        self.core.publish_snapshot_if_changed()
        return self.snapshots[-1].monitors[0].windows[0].agent_sessions[0]

    def read_the_conversation_file(self):
        """One round of the periodic reading (read_context_window_usage_periodically) for the tab."""
        asyncio.run(self.core.update_conversation_file_readings("s1", self.core.agent_sessions["s1"]))

    def test_its_row_isnt_new_again_when_its_first_turn_ends_before_the_next_lookup_of_its_conversation_file(self):
        self.assertEqual(self.publish().status, "new")
        self.assertEqual(self.publish("working").status, "working")
        # No lookup since: a short turn ends within the time between two of them.
        row = self.publish("idle")
        self.assertEqual((row.status, row.status_reason), ("idle", "turn finished and seen"))

    def test_its_conversation_file_is_looked_up_at_once_when_its_first_turn_starts(self):
        lookup_count_before = self.adapter.lookup_count
        self.read_the_conversation_file()
        self.assertEqual(self.adapter.lookup_count, lookup_count_before)  # not due again yet: it was just looked up
        self.assertFalse(self.core.conversation_file_reading_needed.is_set())
        self.adapter.transcript_path = RECORDED_AGY_TRANSCRIPT  # its first message brings the transcript
        self.publish("working")
        self.assertTrue(self.core.conversation_file_reading_needed.is_set())  # the periodic reading runs now
        self.read_the_conversation_file()
        self.assertGreater(self.adapter.lookup_count, lookup_count_before)
        self.assertEqual(self.publish("working").context_tokens, 9482)

    def test_a_first_turn_whose_start_finds_no_transcript_yet_still_ends_idle_with_its_file_looked_up_again(self):
        self.publish("working")
        self.read_the_conversation_file()  # agy hasn't written the transcript yet: its files still show no message
        self.assertTrue(self.core.session_readings_by_session_id["s1"]["no_message_was_sent_in_its_conversation"])
        self.core.conversation_file_reading_needed.clear()
        self.adapter.transcript_path = RECORDED_AGY_TRANSCRIPT
        self.assertEqual(self.publish("idle").status, "idle")
        self.assertTrue(self.core.conversation_file_reading_needed.is_set())
        self.read_the_conversation_file()
        self.assertEqual(self.publish("idle").context_tokens, 9482)

    def test_a_status_change_that_is_no_turn_leaves_its_row_new(self):
        # As a tab whose screen is first read in a terminal that reports no status: idle, with no message sent.
        self.assertEqual(self.publish("idle").status, "new")
        self.assertFalse(self.core.conversation_file_reading_needed.is_set())


if __name__ == "__main__":
    unittest.main()
