import json
import os
import tempfile
import unittest

from agent_command_tab.conversation_search_texts import (add_texts_to_conversation_search_text,
                                                         build_empty_conversation_search_text,
                                                         extract_search_texts_from_agy_record,
                                                         extract_search_texts_from_claude_code_record,
                                                         extract_search_texts_from_codex_record,
                                                         find_session_ids_whose_conversations_contain, read_new_search_texts)


class ClaudeCodeRecordTests(unittest.TestCase):
    def test_prompts_replies_and_shell_commands_count_but_not_tool_output_or_files(self):
        records_and_texts = [
            ({"type": "user", "message": {"role": "user", "content": "Find the VCF merge bug"}}, ["Find the VCF merge bug"]),
            ({"type": "user", "message": {"content": [{"type": "text", "text": "and the BED one"}]}}, ["and the BED one"]),
            # A slash command or skill the user typed, as they typed it.
            ({"type": "user", "message": {"content": "<command-message>code-review-local</command-message>\n"
                                                     "<command-name>/code-review-local</command-name>\n"
                                                     "<command-args>run_all_steps.py</command-args>"}},
             ["/code-review-local run_all_steps.py"]),
            ({"type": "user", "message": {"content": "<command-name>/clear</command-name>\n<command-message>clear</command-message>\n"
                                                     "<command-args></command-args>"}}, ["/clear"]),
            ({"type": "assistant", "message": {"content": [
                {"type": "thinking", "thinking": "secret plan"},
                {"type": "text", "text": "Looking at merge_vcfs.py."},
                {"type": "tool_use", "name": "Bash", "input": {"command": "grep -n bcftools merge_vcfs.py"}},
                {"type": "tool_use", "name": "Read", "input": {"file_path": "/repo/merge_vcfs.py"}},
            ]}}, ["Looking at merge_vcfs.py.", "grep -n bcftools merge_vcfs.py"]),
            # A tool's output, a subagent's reply, Claude Code's own texts and meta records don't.
            ({"type": "user", "message": {"content": [{"type": "tool_result", "content": "bcftools output"}]}}, []),
            ({"type": "assistant", "isSidechain": True, "message": {"content": [{"type": "text", "text": "subagent says"}]}}, []),
            ({"type": "user", "message": {"content": "<local-command-stdout>Compacted</local-command-stdout>"}}, []),
            ({"type": "user", "isMeta": True, "message": {"content": "a skill's text"}}, []),
            ({"type": "system", "subtype": "compact_boundary"}, []),
        ]
        for record, expected_texts in records_and_texts:
            self.assertEqual(extract_search_texts_from_claude_code_record(record), expected_texts, record)


class CodexRecordTests(unittest.TestCase):
    def test_prompts_replies_and_commands_count_from_items_and_from_earlier_versions_events(self):
        records_and_texts = [
            ({"type": "event_msg", "payload": {"type": "item_completed", "item": {
                "type": "UserMessage", "content": [{"type": "text", "text": "status?"}]}}}, ["status?"]),
            ({"type": "event_msg", "payload": {"type": "item_completed", "item": {
                "type": "AgentMessage", "content": [{"type": "Text", "text": "Nothing runs."}]}}}, ["Nothing runs."]),
            # The whole command a shell ran, so a search for "pwd && ls" finds it.
            ({"type": "event_msg", "payload": {"type": "item_completed", "item": {
                "type": "CommandExecution", "command": ["/bin/bash", "-lc", "pwd && ls"],
                "parsed_cmd": [{"type": "unknown", "cmd": "pwd"}, {"type": "list_files", "cmd": "ls"}]}}}, ["pwd && ls"]),
            ({"type": "event_msg", "payload": {"type": "item_completed", "item": {
                "type": "CommandExecution", "command": ["/bin/zsh", "-c", "make test"]}}}, ["make test"]),
            # Without the command, the parts Codex parsed it into.
            ({"type": "event_msg", "payload": {"type": "item_completed", "item": {
                "type": "CommandExecution", "parsed_cmd": [{"type": "unknown", "cmd": "pwd"}]}}}, ["pwd"]),
            ({"type": "event_msg", "payload": {"type": "user_message", "message": "an earlier version's prompt"}},
             ["an earlier version's prompt"]),
            ({"type": "event_msg", "payload": {"type": "agent_message", "message": "an earlier reply"}}, ["an earlier reply"]),
            ({"type": "response_item", "payload": {"type": "function_call", "name": "shell",
                                                   "arguments": json.dumps({"command": ["bash", "-lc", "git status"]})}},
             ["git status"]),
            ({"type": "response_item", "payload": {"type": "function_call", "name": "exec_command",
                                                   "arguments": json.dumps({"cmd": "rg TODO"})}}, ["rg TODO"]),
            # Reasoning, other tools, injected instructions and token counts don't.
            ({"type": "event_msg", "payload": {"type": "item_completed", "item": {"type": "Reasoning", "summary_text": ["x"]}}}, []),
            ({"type": "response_item", "payload": {"type": "custom_tool_call", "name": "exec", "input": "tools.exec_command(...)"}}, []),
            ({"type": "response_item", "payload": {"type": "message", "role": "user",
                                                   "content": [{"type": "input_text", "text": "# AGENTS.md instructions"}]}}, []),
            ({"type": "response_item", "payload": {"type": "function_call", "name": "shell", "arguments": "not JSON"}}, []),
            ({"type": "event_msg", "payload": {"type": "token_count"}}, []),
        ]
        for record, expected_texts in records_and_texts:
            self.assertEqual(extract_search_texts_from_codex_record(record), expected_texts, record)


class AgyRecordTests(unittest.TestCase):
    def test_the_request_without_its_metadata_the_reply_and_run_command_count(self):
        records_and_texts = [
            ({"type": "USER_INPUT", "content": "<USER_REQUEST>\nWhat does foo.py do?\n</USER_REQUEST>\n"
                                               "<ADDITIONAL_METADATA>\nThe current local time is: 2026-11-17.\n</ADDITIONAL_METADATA>"},
             ["What does foo.py do?"]),
            ({"type": "USER_INPUT", "content": "a request without the tag"}, ["a request without the tag"]),
            ({"type": "PLANNER_RESPONSE", "content": "It adds numbers.", "tool_calls": [
                {"name": "run_command", "args": {"CommandLine": "\"pytest -q\"", "toolSummary": "\"Run the tests\""}},
                {"name": "read_file", "args": {"Path": "\"/repo/foo.py\""}}]}, ["It adds numbers.", "pytest -q"]),
            ({"type": "PLANNER_RESPONSE", "tool_calls": [{"name": "run_command", "args": {"CommandLine": "ls -la"}}]}, ["ls -la"]),
            ({"type": "GENERIC", "content": "a tool's output"}, []),
        ]
        for record, expected_texts in records_and_texts:
            self.assertEqual(extract_search_texts_from_agy_record(record), expected_texts, record)


class ReadingNewLinesTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = os.path.join(folder.name, "conversation.jsonl")

    def write(self, text, mode="a"):
        with open(self.path, mode) as file:
            file.write(text)

    def read_texts(self, start_byte):
        return read_new_search_texts(self.path, start_byte, lambda record: [record.get("text", "")])

    def test_only_complete_lines_are_read_and_the_next_read_starts_after_them(self):
        self.write(json.dumps({"text": "one"}) + "\n" + "not JSON\n" + "[1, 2]\n" + '{"text": "tw')
        texts, read_up_to_byte, started_over = self.read_texts(0)
        self.assertEqual((texts, started_over), (["one"], False))  # the line still being written waits
        self.assertEqual(read_up_to_byte, len(json.dumps({"text": "one"}) + "\n" + "not JSON\n" + "[1, 2]\n"))
        self.write("o\"}\n" + json.dumps({"text": "three"}) + "\n")
        texts, read_up_to_byte, started_over = self.read_texts(read_up_to_byte)
        self.assertEqual((texts, started_over, read_up_to_byte), (["two", "three"], False, os.path.getsize(self.path)))
        self.assertEqual(self.read_texts(read_up_to_byte), ([], read_up_to_byte, False))

    def test_a_file_that_got_shorter_is_read_from_its_start_and_a_missing_one_gives_none(self):
        self.write(json.dumps({"text": "new"}) + "\n", mode="w")
        self.assertEqual(self.read_texts(10_000), (["new"], os.path.getsize(self.path), True))
        self.assertIsNone(read_new_search_texts(self.path + ".gone", 0, lambda record: []))


class MatchingTests(unittest.TestCase):
    def test_a_phrase_matches_ignoring_case_unless_case_counts(self):
        first = add_texts_to_conversation_search_text(build_empty_conversation_search_text("/a"), ["Merge the VCF files"], 10)
        first = add_texts_to_conversation_search_text(first, ["bcftools merge"], 20)
        second = add_texts_to_conversation_search_text(build_empty_conversation_search_text("/b"), ["vcf-to-bed"], 5)
        self.assertEqual((first.path, first.read_up_to_byte), ("/a", 20))
        session_texts = [("s1", first), ("s2", second), ("s3", build_empty_conversation_search_text("/c"))]
        for search_text, is_case_sensitive, expected_session_ids in (
                ("vcf", False, ["s1", "s2"]),
                ("VCF", True, ["s1"]),
                ("vcf", True, ["s2"]),
                ("the vcf", False, ["s1"]),  # as one phrase
                ("files bcftools", False, []),  # texts are joined by a line break, so two don't run together
                ("merge", False, ["s1"]),
                ("", False, [])):  # an empty search matches nothing
            self.assertEqual(find_session_ids_whose_conversations_contain(session_texts, search_text, is_case_sensitive),
                             expected_session_ids, (search_text, is_case_sensitive))


if __name__ == "__main__":
    unittest.main()
