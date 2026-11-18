"""How the core keeps a conversation that /clear ended from coming back (DashboardCore.forget_the_cleared_conversation)."""

import asyncio
import os
import tempfile
import types
import unittest
import unittest.mock

from agent_command_tab import dashboard_core
from agent_command_tab.agent_adapters.base import AgentToolAdapter
from agent_command_tab.dashboard_core import DashboardCore
from tab_duplicating import find_launch_command_of_a_duplicate_of_the_tab_of_s1


class StandInAdapter(AgentToolAdapter):
    """Finds the conversation file at self.path and reads its name back as the open tool call's."""

    can_resume_conversations = True  # as CodexAdapter

    def __init__(self, path):
        self.path = path
        self.paths_that_fit = [path]  # the files that may be the process's conversation, newest first
        self.while_reading = None  # run during the first read, as a /clear finishing meanwhile would

    async def find_conversation_file(self, agent_process_id, claude_agent_record, claude_config_folders):
        return self.path

    def read_context_tokens_and_context_window_tokens(self, conversation_file_path, agent_process_id, claude_agent_record,
                                                      context_tokens_read_before):
        if self.while_reading is not None:
            self.while_reading()
        return 1000, 200_000

    def read_open_tool_call(self, conversation_file_path):
        return {"open_tool_name": os.path.basename(conversation_file_path)}

    async def find_conversation_id(self, agent_process_id, claude_agent_record):
        # As CodexAdapter: only when exactly one file fits, so a copy never forks another conversation.
        return os.path.basename(self.paths_that_fit[0]) if len(self.paths_that_fit) == 1 else None

    def build_launch_command_for_a_copy(self, launch_command, conversation_id, claude_agent_record=None, codex_home_folder=None):
        return f"{launch_command} fork {conversation_id}" if conversation_id else launch_command


class ClearedConversationTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.old_path = self.write_file("old.jsonl")
        self.adapter = StandInAdapter(self.old_path)
        self.core = DashboardCore(lambda snapshot: None, lambda message: None)
        self.agent_session = {"adapter": self.adapter, "process_id": 4242, "is_in_background": False}
        self.core.agent_sessions = {"s1": self.agent_session}

    def write_file(self, name):
        path = os.path.join(self.folder.name, name)
        with open(path, "w") as file:
            file.write("{}\n")
        return path

    def find_the_noted_open_tool_call(self):
        return self.core.session_readings_by_session_id.get("s1", {}).get("open_tool_call")

    def update_readings_with_a_lookup_due(self):
        if "s1" in self.core.conversation_files_by_session_id:
            self.core.conversation_files_by_session_id["s1"]["looked_up_at"] = 0
        asyncio.run(self.core.update_conversation_file_readings("s1", self.agent_session))

    def clear(self):
        async def clear_the_conversation():
            cleared_path = await self.core.find_current_conversation_file("s1", self.agent_session)
            self.core.forget_the_cleared_conversation("s1", self.agent_session, cleared_path)
        asyncio.run(clear_the_conversation())

    def test_a_clear_before_the_first_lookup_keeps_the_old_file_away_until_a_new_one_appears(self):
        self.clear()
        self.update_readings_with_a_lookup_due()  # Codex's lookup still finds the old file
        self.assertIsNone(self.find_the_noted_open_tool_call())
        self.adapter.path = self.write_file("new.jsonl")  # the new chat's first prompt
        self.update_readings_with_a_lookup_due()
        self.assertEqual(self.find_the_noted_open_tool_call(), {"open_tool_name": "new.jsonl"})

    def test_a_clear_after_reading_forgets_the_old_readings(self):
        self.update_readings_with_a_lookup_due()
        self.assertEqual(self.find_the_noted_open_tool_call(), {"open_tool_name": "old.jsonl"})
        self.clear()
        self.assertIsNone(self.find_the_noted_open_tool_call())
        self.update_readings_with_a_lookup_due()
        self.assertIsNone(self.find_the_noted_open_tool_call())

    def test_a_clear_during_the_reads_drops_their_results_instead_of_stopping_the_core(self):
        self.adapter.while_reading = lambda: self.core.forget_the_cleared_conversation("s1", self.agent_session, self.old_path)
        self.update_readings_with_a_lookup_due()
        self.assertTrue(self.core.conversation_was_cleared_and_the_next_has_no_file_yet("s1"))  # the clear came during a read
        session_reading = self.core.session_readings_by_session_id.get("s1", {})
        for reading_name in ("open_tool_call", "context_tokens", "context_window_tokens"):
            self.assertIsNone(session_reading.get(reading_name), reading_name)

    def test_a_conversation_file_that_doesnt_name_the_conversation_is_looked_up_again(self):
        # agy's /new: another conversation in the same process, which the file found before doesn't follow.
        self.update_readings_with_a_lookup_due()  # old.jsonl
        self.adapter.path = self.write_file("new.jsonl")
        self.adapter.paths_that_fit = [self.adapter.path]
        self.assertEqual(asyncio.run(self.core.find_conversation_id_and_file_known_to_be_its_own("s1", self.agent_session)),
                         ("new.jsonl", self.adapter.path, ""))

    def test_duplicating_a_cleared_tab_starts_a_new_conversation(self):
        self.assertEqual(find_launch_command_of_a_duplicate_of_the_tab_of_s1(self.core), "codex fork old.jsonl")
        self.clear()
        # Codex's lookup still finds the old file: it forked that before.
        self.assertEqual(find_launch_command_of_a_duplicate_of_the_tab_of_s1(self.core), "codex")
        self.adapter.path = self.write_file("new.jsonl")  # the new chat's first prompt
        self.update_readings_with_a_lookup_due()
        self.assertFalse(self.core.conversation_was_cleared_and_the_next_has_no_file_yet("s1"))  # the new file ends it
        self.adapter.paths_that_fit = [self.adapter.path]
        self.assertEqual(find_launch_command_of_a_duplicate_of_the_tab_of_s1(self.core), "codex fork new.jsonl")
        self.adapter.paths_that_fit = [self.adapter.path, self.old_path]
        # Both files fit the process, so which one is its isn't known.
        self.assertEqual(find_launch_command_of_a_duplicate_of_the_tab_of_s1(self.core), "codex")

    def test_a_snapshot_is_asked_for_once_an_open_tool_call_is_old_enough_to_show_as_running(self):
        # The conversation file may not change again by then, so the row showed "thinking" until
        # some other change asked for a snapshot.
        time_of_the_reading, seconds_the_tool_call_has_run = 1000.0, 2
        self.adapter.read_open_tool_call = lambda conversation_file_path: {
            "open_tool_name": "WebFetch", "open_tool_started_at": time_of_the_reading - seconds_the_tool_call_has_run}

        async def read_the_file_and_list_the_timers_it_sets():
            event_loop = asyncio.get_running_loop()
            with unittest.mock.patch.object(event_loop, "call_later", wraps=event_loop.call_later) as call_later:
                await self.core.update_conversation_file_readings("s1", self.agent_session)
            return [call.args for call in call_later.call_args_list]
        # Only the core's own name for the clock is replaced, by one that stands still, so the tool
        # call's age at the reading is exact.
        with unittest.mock.patch.object(dashboard_core, "time", types.SimpleNamespace(time=lambda: time_of_the_reading)):
            ((seconds_until_the_timer_fires, called_when_the_timer_fires),) = asyncio.run(read_the_file_and_list_the_timers_it_sets())
        seconds_until_the_tool_call_shows_as_running = (
            dashboard_core.SECONDS_BEFORE_AN_OPEN_TOOL_CALL_SHOWS_AS_RUNNING - seconds_the_tool_call_has_run)
        self.assertGreaterEqual(seconds_until_the_timer_fires, seconds_until_the_tool_call_shows_as_running)  # not while it is too young
        self.assertLess(seconds_until_the_timer_fires, seconds_until_the_tool_call_shows_as_running + 1)  # and soon after
        self.core.snapshot_needed.clear()  # the new reading asked for one at once
        called_when_the_timer_fires()
        self.assertTrue(self.core.snapshot_needed.is_set())


if __name__ == "__main__":
    unittest.main()
