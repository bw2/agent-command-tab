"""How the core keeps a conversation that /clear ended from coming back (DashboardCore.forget_the_cleared_conversation)."""

import asyncio
import os
import tempfile
import unittest
import unittest.mock

from llm_sidebar_dashboard import iterm2_actions
from llm_sidebar_dashboard.dashboard_core_api import ActionRequest, ActionResult
from llm_sidebar_dashboard.iterm2_connection_thread import DashboardCore


class StandInAdapter:
    """Finds the conversation file at self.path and reads its name back as the preview."""

    def __init__(self, path):
        self.path = path
        self.read_paths = []
        self.while_reading = None  # run during the first read, as a /clear finishing meanwhile would

    async def find_conversation_file(self, agent_process_id, claude_agent_record, claude_config_folders):
        return self.path

    def read_context_tokens(self, conversation_file_path):
        self.read_paths.append(conversation_file_path)
        if self.while_reading is not None:
            self.while_reading()
        return 1000

    def read_preview_fields(self, conversation_file_path):
        self.read_paths.append(conversation_file_path)
        return {"last_prompt": os.path.basename(conversation_file_path)}

    async def find_conversation_id(self, agent_process_id, claude_agent_record):
        return os.path.basename(self.path)  # Codex still finds the old file until the new chat's first prompt

    def build_launch_command_for_a_copy(self, launch_command, conversation_id, claude_agent_record=None):
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

    def read_preview(self):
        return self.core.screen_readings_by_session_id.get("s1", {}).get("conversation_preview")

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
        self.assertIsNone(self.read_preview())
        self.adapter.path = self.write_file("new.jsonl")  # the new chat's first prompt
        self.update_readings_with_a_lookup_due()
        self.assertEqual(self.read_preview(), {"last_prompt": "new.jsonl"})

    def test_a_clear_after_reading_forgets_the_old_preview(self):
        self.update_readings_with_a_lookup_due()
        self.assertEqual(self.read_preview(), {"last_prompt": "old.jsonl"})
        self.clear()
        self.assertIsNone(self.read_preview())
        self.update_readings_with_a_lookup_due()
        self.assertIsNone(self.read_preview())

    def test_a_clear_during_the_reads_drops_their_results_instead_of_stopping_the_core(self):
        self.adapter.while_reading = lambda: self.core.forget_the_cleared_conversation("s1", self.agent_session, self.old_path)
        self.update_readings_with_a_lookup_due()  # raised TypeError when the second read took the cleared path
        self.assertEqual(self.adapter.read_paths, [self.old_path, self.old_path])
        self.assertIsNone(self.read_preview())

    def duplicate(self):
        """Returns the launch command a Duplicate of the tab would run."""
        self.core.gathered_windows = [{"window_id": "w1", "tabs": [{"tab_id": "t1", "first_session": {"session_id": "s1"}}]}]
        launch_commands = []

        async def record_duplicate(connection, app, tab_id, folder, launch_command):
            launch_commands.append(launch_command)
            return ActionResult(True)
        with unittest.mock.patch.object(iterm2_actions, "duplicate_agent_session", record_duplicate):
            asyncio.run(self.core.handle_duplicate_agent_session(
                ActionRequest("duplicate_agent_session", {"tab_id": "t1"}, {"folder": "/x", "launch_command": "codex"})))
        return launch_commands[-1]

    def test_duplicating_a_cleared_tab_starts_a_new_conversation_until_the_next_one_has_a_file(self):
        self.assertEqual(self.duplicate(), "codex fork old.jsonl")
        self.clear()
        self.assertEqual(self.duplicate(), "codex")  # forked the cleared conversation before
        self.adapter.path = self.write_file("new.jsonl")
        self.update_readings_with_a_lookup_due()
        self.assertEqual(self.duplicate(), "codex fork new.jsonl")

    def test_commands_are_not_typed_into_a_suspended_agent(self):
        self.agent_session["is_in_background"] = True
        self.core.displayed_state_by_session_id["s1"] = ("idle", 0)  # what its row still shows
        action_result = asyncio.run(self.core.handle_send_slash_command(
            ActionRequest("send_slash_command", {"session_id": "s1"}, {"command": "/clear"})))
        self.assertFalse(action_result.succeeded)
        self.assertIn("suspended", action_result.message)


if __name__ == "__main__":
    unittest.main()
