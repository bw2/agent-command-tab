import datetime
import json
import os
import tempfile
import time
import unittest

from agent_command_tab.context_window_usage import (choose_claude_code_context_window_tokens, find_claude_code_transcript,
                                                    find_codex_conversation_files, format_token_count,
                                                    read_claude_code_model_setting,
                                                    read_context_tokens_from_claude_code_transcript,
                                                    read_context_tokens_from_codex_conversation_file,
                                                    read_context_window_tokens_from_codex_conversation_file, read_last_lines,
                                                    read_last_records,
                                                    read_turn_started_at_from_claude_code_transcript,
                                                    read_turn_started_at_from_codex_conversation_file)


class ContextWindowUsageTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.folder.cleanup()

    def write_lines(self, relative_path, records):
        path = os.path.join(self.folder.name, relative_path)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as file:
            for record in records:
                file.write((record if isinstance(record, str) else json.dumps(record)) + "\n")
        return path

    def test_claude_code_transcript_uses_the_latest_replys_input_and_cache_tokens(self):
        path = self.write_lines("projects/-Users-me-code/abc.jsonl", [
            {"type": "assistant", "message": {"usage": {"input_tokens": 5, "cache_read_input_tokens": 1000}}},
            {"type": "user", "message": {"content": "next"}},
            {"type": "assistant", "message": {"usage": {"input_tokens": 2, "cache_creation_input_tokens": 2082,
                                                        "cache_read_input_tokens": 671846, "output_tokens": 696}}},
            # An API error written as a reply: skipped, so the last real reply's size stays.
            {"type": "assistant", "message": {"model": "<synthetic>", "usage": {"input_tokens": 0, "output_tokens": 0}}},
            "{not json",
        ])
        self.assertEqual(read_context_tokens_from_claude_code_transcript(path), 673930)
        self.assertEqual(find_claude_code_transcript(["/no/such/folder", self.folder.name], "abc"), path)
        self.assertIsNone(find_claude_code_transcript([self.folder.name], "other"))

    def test_after_a_compaction_the_size_is_the_compacted_one_until_the_next_reply(self):
        reply_before = {"type": "assistant", "message": {"usage": {"input_tokens": 709217}}}
        compaction = {"type": "system", "subtype": "compact_boundary", "compactMetadata": {"preTokens": 709217, "postTokens": 20183}}
        path = self.write_lines("compacted.jsonl", [reply_before, compaction,
                                                    {"type": "user", "isCompactSummary": True, "message": {"content": "Summary"}}])
        self.assertEqual(read_context_tokens_from_claude_code_transcript(path), 20183)
        path = self.write_lines("replied.jsonl", [reply_before, compaction,
                                                  {"type": "assistant", "message": {"usage": {"input_tokens": 73525}}}])
        self.assertEqual(read_context_tokens_from_claude_code_transcript(path), 73525)

    def test_codex_conversation_file_uses_the_latest_token_count(self):
        path = self.write_lines("rollout.jsonl", [
            {"type": "event_msg", "payload": {"type": "token_count", "info": {"last_token_usage": {"total_tokens": 100}}}},
            {"type": "event_msg", "payload": {"type": "token_count", "info": {"last_token_usage": {"total_tokens": 40546}}}},
            {"type": "response_item", "payload": {"type": "message"}},
        ])
        self.assertEqual(read_context_tokens_from_codex_conversation_file(path), 40546)

    def test_codex_reports_its_context_window_with_its_usage(self):
        path = self.write_lines("rollout_with_window.jsonl", [
            {"type": "event_msg", "payload": {"type": "token_count",
                                              "info": {"last_token_usage": {"total_tokens": 40546}, "model_context_window": 258400}}},
            {"type": "event_msg", "payload": {"type": "token_count", "info": None}},  # a rate-limit-only report
        ])
        self.assertEqual(read_context_window_tokens_from_codex_conversation_file(path), 258400)

    def test_a_claude_code_sessions_context_window_follows_the_model_it_was_started_with(self):
        self.write_lines("config/settings.json", [{"model": "opus[1m]"}])
        config_folder = os.path.join(self.folder.name, "config")
        environment = {"ANTHROPIC_MODEL": "sonnet[1m]"}  # "ANTHROPIC_MODEL=sonnet[1m] claude"
        self.assertEqual(read_claude_code_model_setting(["claude", "--model", "sonnet"], environment, config_folder), "sonnet")
        self.assertEqual(read_claude_code_model_setting(["claude", "--model=haiku"], {}, config_folder), "haiku")
        self.assertEqual(read_claude_code_model_setting(["claude", "--verbose"], environment, config_folder), "sonnet[1m]")
        self.assertEqual(read_claude_code_model_setting(["claude", "--verbose"], {}, config_folder), "opus[1m]")
        self.assertIsNone(read_claude_code_model_setting(["claude"], {}, os.path.join(self.folder.name, "no-config")))
        self.assertEqual(choose_claude_code_context_window_tokens("opus[1m]", 40_000), 1_000_000)
        self.assertEqual(choose_claude_code_context_window_tokens("sonnet", 40_000), 200_000)
        self.assertEqual(choose_claude_code_context_window_tokens(None, 250_000), 1_000_000)  # more than 200k fits only in 1M

    def test_files_without_usage_or_missing_files_give_none(self):
        self.assertIsNone(read_context_tokens_from_claude_code_transcript(self.write_lines("empty.jsonl", [{"type": "user"}])))
        self.assertIsNone(read_context_tokens_from_codex_conversation_file(os.path.join(self.folder.name, "missing.jsonl")))

    def test_only_the_end_of_a_long_file_is_read_and_its_cut_first_line_is_dropped(self):
        path = self.write_lines("long.jsonl", ["x" * 100, "first", "second"])
        self.assertEqual(read_last_lines(path, byte_count=10), ["second"])  # the 10 bytes start inside "first"
        self.assertEqual(read_last_lines(path, byte_count=1000), ["second", "first", "x" * 100])

    def test_records_are_the_json_objects_newest_first(self):
        path = self.write_lines("mixed.jsonl", [{"type": "user"}, "not json", "[1, 2]", '"text"', {"type": "assistant"}])
        self.assertEqual(list(read_last_records(path)), [{"type": "assistant"}, {"type": "user"}])
        self.assertIsNone(read_context_tokens_from_claude_code_transcript(self.write_lines("list.jsonl", ["[1, 2]"])))

    def test_codex_conversation_file_is_found_by_folder_and_start_time(self):
        now = time.time()
        today = datetime.date.fromtimestamp(now)
        day_folder = f"sessions/{today.year:04d}/{today.month:02d}/{today.day:02d}"

        def write_conversation(name, folder, started_at, source="cli"):
            started_at_text = datetime.datetime.fromtimestamp(started_at, datetime.timezone.utc).isoformat().replace("+00:00", "Z")
            return self.write_lines(f"{day_folder}/{name}", [{"type": "session_meta", "payload": {
                "cwd": folder, "timestamp": started_at_text, "source": source}}])

        own_file = write_conversation("rollout-a-01a151f0-8f89-7711-a0cc-cfcae10915f8.jsonl", "/Users/me/code", now - 30)
        write_conversation("rollout-b-01a151f0-8f89-7711-a0cc-cfcae10915f9.jsonl", "/Users/me/other", now - 20)  # another folder
        write_conversation("rollout-c-01a151f0-8f89-7711-a0cc-cfcae10915fa.jsonl", "/Users/me/code", now - 3600)  # before the agent
        write_conversation("rollout-d-01a151f0-8f89-7711-a0cc-cfcae10915fb.jsonl", "/Users/me/code", now - 29,
                           source={"subagent": {"other": "guardian"}})  # the auto-review helper's own file
        sessions_folder = os.path.join(self.folder.name, "sessions")
        self.assertEqual(find_codex_conversation_files(sessions_folder, now - 60, "/Users/me/code", now), [own_file])
        self.assertEqual(find_codex_conversation_files(sessions_folder, now - 60, "/Users/me/nothing-here", now), [])

    def test_a_resumed_codex_conversation_is_found_in_its_own_older_day_folder(self):
        now = time.time()

        def write_conversation(name, started_at, modified_at):
            started_day = datetime.date.fromtimestamp(started_at)
            started_at_text = datetime.datetime.fromtimestamp(started_at, datetime.timezone.utc).isoformat().replace("+00:00", "Z")
            path = self.write_lines(f"sessions/{started_day.year:04d}/{started_day.month:02d}/{started_day.day:02d}/{name}",
                                    [{"type": "session_meta", "payload": {"cwd": "/Users/me/code", "timestamp": started_at_text,
                                                                          "source": "cli"}}])
            os.utime(path, (modified_at, modified_at))
            return path

        # `codex resume`, started a minute ago, has written to a conversation started 3 days ago.
        resumed_file = write_conversation("rollout-a-01a151f0-8f89-7711-a0cc-cfcae10915f8.jsonl", now - 3 * 86400, now - 10)
        write_conversation("rollout-b-01a151f0-8f89-7711-a0cc-cfcae10915f9.jsonl", now - 2 * 86400, now - 86400)  # not since
        sessions_folder = os.path.join(self.folder.name, "sessions")
        self.assertEqual(find_codex_conversation_files(sessions_folder, now - 60, "/Users/me/code", now), [])
        self.assertEqual(find_codex_conversation_files(sessions_folder, now - 60, "/Users/me/code", now,
                                                       agent_resumed_a_conversation=True), [resumed_file])

    def test_unicode_line_separators_inside_a_record_do_not_split_it(self):
        # Written raw: json.dumps would escape them, Claude Code's own writer doesn't.
        path = self.write_lines("separators.jsonl", ['{"type": "assistant", "message": {"content": "a b\u0085c", '
                                                     '"usage": {"input_tokens": 7, "cache_read_input_tokens": 3}}}'])
        self.assertEqual(read_context_tokens_from_claude_code_transcript(path), 10)

    def test_turn_start_is_the_latest_prompt_not_a_tool_result(self):
        claude_path = self.write_lines("claude.jsonl", [
            {"type": "user", "timestamp": "2026-10-19T02:40:00.000Z", "message": {"content": "an earlier prompt"}},
            {"type": "user", "timestamp": "2026-10-19T02:51:48.992Z", "message": {"content": "run the pipeline"}},
            {"type": "assistant", "timestamp": "2026-10-19T02:51:50.000Z", "message": {"usage": {"input_tokens": 1}}},
            {"type": "user", "timestamp": "2026-10-19T02:52:07.729Z", "message": {"content": [{"type": "tool_result"}]}},
            # Written in the middle of the turn: a skill's text and Claude Code's own output, which don't start one.
            {"type": "user", "timestamp": "2026-10-19T02:52:09.000Z", "isMeta": True,
             "message": {"content": "Base directory for this skill: /x"}},
            {"type": "user", "timestamp": "2026-10-19T02:52:10.000Z",
             "message": {"content": "<local-command-stdout>done</local-command-stdout>"}},
        ])
        self.assertEqual(read_turn_started_at_from_claude_code_transcript(claude_path),
                         datetime.datetime(2026, 10, 19, 2, 51, 48, 992000, datetime.timezone.utc).timestamp())
        codex_path = self.write_lines("codex.jsonl", [
            {"timestamp": "2026-10-19T02:43:17.092Z", "type": "event_msg", "payload": {"type": "task_started"}},
            {"timestamp": "2026-10-19T02:43:18.296Z", "type": "response_item", "payload": {"type": "message", "role": "user"}},
        ])
        self.assertEqual(read_turn_started_at_from_codex_conversation_file(codex_path),
                         datetime.datetime(2026, 10, 19, 2, 43, 17, 92000, datetime.timezone.utc).timestamp())

    def test_a_message_from_another_claude_code_session_starts_a_turn(self):  # code review, 2026-10-28
        claude_path = self.write_lines("claude_peer.jsonl", [
            {"type": "user", "timestamp": "2026-10-19T14:00:00.000Z", "message": {"content": "fix the bug"}},
            {"type": "user", "timestamp": "2026-10-19T14:28:36.000Z", "isMeta": True, "turnOrigin": "peer",
             "message": {"content": "Another Claude session sent a message: I renamed the column."}},
        ])
        self.assertEqual(read_turn_started_at_from_claude_code_transcript(claude_path),
                         datetime.datetime(2026, 10, 19, 14, 28, 36, tzinfo=datetime.timezone.utc).timestamp())

    def test_token_counts(self):
        self.assertEqual([format_token_count(count) for count in (425_300, 999, 999_499, 999_500, 1_234_567)],
                         ["425k", "1k", "999k", "1.0M", "1.2M"])


if __name__ == "__main__":
    unittest.main()
