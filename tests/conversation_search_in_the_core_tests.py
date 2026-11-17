"""How the core keeps each session's conversation text for the list's search and answers it
(DashboardCore.update_conversation_file_readings, handle_find_agent_sessions_whose_conversations_contain)."""

import asyncio
import json
import os
import tempfile
import unittest

from agent_command_tab.dashboard_core import DashboardCore
from agent_command_tab.dashboard_core_api import ActionRequest

from cleared_conversation_tests import StandInAdapter


class TextReadingStandInAdapter(StandInAdapter):
    """Reads each record's "text" as its search text."""

    def extract_search_texts_from_conversation_record(self, record):
        return [record["text"]] if "text" in record else []


class ConversationSearchInTheCoreTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = os.path.join(folder.name, "conversation.jsonl")
        self.modified_at = 1_700_000_000
        self.append_lines("Merge the VCF files")
        self.adapter = TextReadingStandInAdapter(self.path)
        self.core = DashboardCore(lambda snapshot: None, lambda message: None)
        self.agent_session = {"adapter": self.adapter, "process_id": 4242, "is_in_background": False}
        self.core.agent_sessions = {"s1": self.agent_session}

    def append_lines(self, *texts):
        with open(self.path, "a") as file:
            for text in texts:
                file.write(json.dumps({"text": text}) + "\n")
        self.modified_at += 1  # a new modification time, which is what makes the core read the file again
        os.utime(self.path, (self.modified_at, self.modified_at))

    def update_readings(self):
        asyncio.run(self.core.update_conversation_file_readings("s1", self.agent_session))

    def search(self, text, is_case_sensitive=False):
        async def perform_the_search():
            action_result = await self.core.perform_action(ActionRequest(
                "find_agent_sessions_whose_conversations_contain", None, {"text": text, "is_case_sensitive": is_case_sensitive}))
            return action_result, self.core.regather_needed.is_set()
        action_result, regather_was_asked_for = asyncio.run(perform_the_search())
        self.assertTrue(action_result.succeeded)
        self.assertFalse(regather_was_asked_for)  # a search reads nothing from the terminals
        return action_result.details["session_ids"]

    def test_the_text_is_read_once_then_only_the_lines_added_and_searched_as_one_phrase(self):
        self.assertEqual(self.search("vcf"), [])  # nothing read yet
        self.update_readings()
        self.assertEqual(self.search("vcf"), ["s1"])
        self.assertEqual(self.search("vcf", is_case_sensitive=True), [])
        self.assertEqual(self.search("VCF", is_case_sensitive=True), ["s1"])
        read_up_to_byte = self.core.conversation_search_texts_by_session_id["s1"].read_up_to_byte
        revision = self.core.conversation_search_text_revision
        self.core.snapshot_needed.clear()
        self.append_lines("bcftools merge -Oz")
        self.update_readings()
        # The next snapshot tells a list with a search to ask again, though nothing it shows changed.
        self.assertEqual(self.core.conversation_search_text_revision, revision + 1)
        self.assertTrue(self.core.snapshot_needed.is_set())
        conversation_search_text = self.core.conversation_search_texts_by_session_id["s1"]
        self.assertEqual(conversation_search_text.text, "\nMerge the VCF files\nbcftools merge -Oz")
        self.assertGreater(conversation_search_text.read_up_to_byte, read_up_to_byte)
        self.assertEqual(self.search("bcftools"), ["s1"])
        self.assertEqual(self.search("files bcftools"), [])
        self.assertEqual(self.search(""), [])

    def test_a_clear_forgets_the_text(self):
        self.update_readings()
        self.core.forget_the_cleared_conversation("s1", self.agent_session, self.path)
        self.assertEqual(self.search("vcf"), [])
        self.assertNotIn("s1", self.core.conversation_search_texts_by_session_id)

    def test_other_actions_still_ask_for_a_fresh_gather(self):
        async def perform_another_action():
            await self.core.perform_action(ActionRequest("an_action_no_one_registered", None))
            return self.core.regather_needed.is_set()
        self.assertTrue(asyncio.run(perform_another_action()))


if __name__ == "__main__":
    unittest.main()
