"""How the core keeps each session's conversation text for the list's search and answers it
(DashboardCore.update_conversation_file_readings, handle_find_agent_sessions_whose_conversations_contain),
and how it reads the saved sessions' conversations for the Saved sessions window's search
(handle_find_saved_agent_sessions_whose_conversations_contain)."""

import asyncio
import json
import os
import tempfile
import unittest
import unittest.mock

from agent_command_tab.dashboard_core import DashboardCore
from agent_command_tab.dashboard_core_api import ActionRequest

from cleared_conversation_tests import StandInAdapter
from saved_session_builders import build_saved_session_record


class TextReadingStandInAdapter(StandInAdapter):
    """Reads each record's "text" as its search text, and keeps the texts it read, in order
    (texts_read_from_records): a line read twice is there twice."""

    def __init__(self, path):
        super().__init__(path)
        self.texts_read_from_records = []

    def extract_search_texts_from_conversation_record(self, record):
        texts = [record["text"]] if "text" in record else []
        self.texts_read_from_records += texts
        return texts


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
        self.assertEqual(self.adapter.texts_read_from_records, ["Merge the VCF files", "bcftools merge -Oz"])  # the first line once
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


CLAUDE_CODE_CONVERSATION_ID = "11111111-aaaa-4bbb-8ccc-000000000001"
CODEX_CONVERSATION_ID = "22222222-aaaa-4bbb-8ccc-000000000002"
AGY_CONVERSATION_ID = "33333333-aaaa-4bbb-8ccc-000000000003"


class SavedConversationSearchInTheCoreTests(unittest.TestCase):
    """Three saved sessions, one per agent tool, each with its conversation file where its tool
    keeps it, in folders of the test's own: a Claude Code config folder, a CODEX_HOME and agy's folder."""

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.claude_config_folder = os.path.join(folder.name, "claude_config")
        self.codex_home_folder = os.path.join(folder.name, "codex_home")
        agy_folder = os.path.join(folder.name, "agy")
        self.claude_code_file = os.path.join(self.claude_config_folder, "projects", "-repo", CLAUDE_CODE_CONVERSATION_ID + ".jsonl")
        self.codex_file = os.path.join(self.codex_home_folder, "sessions", "2026", "11", "18",
                                       f"rollout-2026-11-18T15-04-05-{CODEX_CONVERSATION_ID}.jsonl")
        self.agy_file = os.path.join(agy_folder, "brain", AGY_CONVERSATION_ID, ".system_generated", "logs", "transcript_full.jsonl")
        self.write_records(self.claude_code_file, [
            {"type": "user", "message": {"role": "user", "content": "Merge the VCF files"}},
            {"type": "assistant", "message": {"content": [
                {"type": "text", "text": "Merging them now."},
                {"type": "tool_use", "name": "Bash", "input": {"command": "bcftools merge -Oz a.vcf.gz b.vcf.gz"}}]}}])
        self.write_records(self.codex_file, [
            {"type": "event_msg", "payload": {"type": "item_completed", "item": {
                "type": "UserMessage", "content": [{"type": "text", "text": "Sort the BED file"}]}}}])
        self.write_records(self.agy_file, [
            {"type": "USER_INPUT", "content": "<USER_REQUEST>\nIndex the BAM file\n</USER_REQUEST>\nthe time agy adds"}])
        for patch in (unittest.mock.patch("agent_command_tab.agent_adapters.agy.DEFAULT_AGY_FOLDER", agy_folder),
                      # Only the folder each saved session names is looked through, not this Mac's own.
                      unittest.mock.patch("agent_command_tab.dashboard_core.list_claude_config_folder_paths", return_value=[])):
            patch.start()
            self.addCleanup(patch.stop)
        self.saved_sessions = [
            build_saved_session_record(CLAUDE_CODE_CONVERSATION_ID, "/repo", "session-1", "merging",
                                       claude_config_folder=self.claude_config_folder),
            build_saved_session_record(CODEX_CONVERSATION_ID, "/repo", "session-2", "sorting", agent_tool_name="Codex",
                                       codex_home_folder=self.codex_home_folder),
            build_saved_session_record(AGY_CONVERSATION_ID, "/repo", "session-3", "indexing", agent_tool_name="agy")]
        self.core = DashboardCore(lambda snapshot: None, lambda message: None)

    def write_records(self, path, records, mode="w"):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, mode) as file:
            for record in records:
                file.write(json.dumps(record) + "\n")

    def search(self, text, is_case_sensitive=False, saved_sessions=None):
        async def perform_the_search():
            action_result = await self.core.perform_action(ActionRequest(
                "find_saved_agent_sessions_whose_conversations_contain", None,
                {"text": text, "is_case_sensitive": is_case_sensitive,
                 "saved_sessions": self.saved_sessions if saved_sessions is None else saved_sessions}))
            return action_result, self.core.regather_needed.is_set()
        action_result, regather_was_asked_for = asyncio.run(perform_the_search())
        self.assertTrue(action_result.succeeded)
        self.assertFalse(regather_was_asked_for)  # a search reads nothing from the terminals
        return action_result.details["conversation_ids"]

    def test_each_tools_saved_conversation_is_found_by_its_id_and_searched_as_one_phrase(self):
        self.assertEqual(self.search("file"), [CLAUDE_CODE_CONVERSATION_ID, CODEX_CONVERSATION_ID, AGY_CONVERSATION_ID])
        self.assertEqual(self.search("bcftools merge"), [CLAUDE_CODE_CONVERSATION_ID])  # a command the agent ran
        self.assertEqual(self.search("merging them"), [CLAUDE_CODE_CONVERSATION_ID])  # its reply, whatever the case
        self.assertEqual(self.search("merging them", is_case_sensitive=True), [])
        self.assertEqual(self.search("sort the bed"), [CODEX_CONVERSATION_ID])
        self.assertEqual(self.search("index the bam"), [AGY_CONVERSATION_ID])
        self.assertEqual(self.search("the time agy adds"), [])  # not the user's own words
        self.assertEqual(self.search("files bcftools"), [])
        self.assertEqual(self.search(""), [])

    def test_a_file_is_read_once_then_only_the_lines_added_and_its_text_kept_while_its_session_is_saved(self):
        self.assertEqual(self.search("vcf"), [CLAUDE_CODE_CONVERSATION_ID])
        text_read_first = self.core.saved_conversation_search_texts_by_conversation_id[CLAUDE_CODE_CONVERSATION_ID]
        self.assertEqual(text_read_first.path, self.claude_code_file)
        with unittest.mock.patch.object(self.core.find_adapter_of_agent_tool("Claude Code"), "find_conversation_file_by_id") as find_file:
            self.assertEqual(self.search("tabix"), [])
            # The session's agent went on meanwhile (it is saved while it runs, too).
            self.write_records(self.claude_code_file, [{"type": "user", "message": {"content": "Now run tabix on it"}}], mode="a")
            self.assertEqual(self.search("tabix"), [CLAUDE_CODE_CONVERSATION_ID])
            find_file.assert_not_called()  # the file found for the first search
        text_read_last = self.core.saved_conversation_search_texts_by_conversation_id[CLAUDE_CODE_CONVERSATION_ID]
        self.assertEqual(text_read_last.text, text_read_first.text + "\nNow run tabix on it")
        self.assertGreater(text_read_last.read_up_to_byte, text_read_first.read_up_to_byte)
        # A session no longer saved (deleted, or restored) is no longer searched, and its text is let go.
        self.assertEqual(self.search("file", saved_sessions=self.saved_sessions[1:]), [CODEX_CONVERSATION_ID, AGY_CONVERSATION_ID])
        self.assertEqual(sorted(self.core.saved_conversation_search_texts_by_conversation_id),
                         [CODEX_CONVERSATION_ID, AGY_CONVERSATION_ID])

    def test_a_saved_session_whose_file_is_gone_or_whose_tool_has_no_adapter_never_matches_and_a_file_that_comes_back_does(self):
        other_tools_session = build_saved_session_record("some-id", "/repo", "session-4", "other", agent_tool_name="aider")
        os.rename(self.codex_file, self.codex_file + ".moved")
        self.assertEqual(self.search("file", saved_sessions=self.saved_sessions + [other_tools_session]),
                         [CLAUDE_CODE_CONVERSATION_ID, AGY_CONVERSATION_ID])
        os.rename(self.codex_file + ".moved", self.codex_file)
        self.assertEqual(self.search("bed"), [CODEX_CONVERSATION_ID])  # looked for again
        os.remove(self.codex_file)  # deleted after it was read
        self.assertEqual(self.search("bed"), [])
        self.assertNotIn(CODEX_CONVERSATION_ID, self.core.saved_conversation_search_texts_by_conversation_id)

    def test_a_codex_session_saved_without_its_codex_home_is_looked_for_in_the_apps_own(self):
        saved_session = build_saved_session_record(CODEX_CONVERSATION_ID, "/repo", "session-2", "sorting", agent_tool_name="Codex")
        with unittest.mock.patch.dict(os.environ, {"CODEX_HOME": self.codex_home_folder}):
            self.assertEqual(self.search("bed", saved_sessions=[saved_session]), [CODEX_CONVERSATION_ID])


if __name__ == "__main__":
    unittest.main()
