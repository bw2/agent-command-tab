import asyncio
import datetime
import json
import os
import shlex
import tempfile
import time
import unittest
import unittest.mock

from agent_command_tab import context_window_usage, dashboard_core
from agent_command_tab.agent_adapters import claude_code, codex
from agent_command_tab.agent_adapters.claude_code import ClaudeCodeAdapter
from agent_command_tab.agent_adapters.codex import (CodexAdapter, codex_process_runs_its_turns_in_the_shared_server,
                                                    find_conversation_id_named_after_resume)
from agent_command_tab.agent_process_tree import ProcessInfo
from agent_command_tab.context_window_usage import (choose_claude_code_context_window_tokens,
                                                    claude_model_has_a_200k_context_window, find_claude_code_transcript,
                                                    find_codex_conversation_file_by_id, find_codex_conversation_files,
                                                    format_token_count, read_claude_code_model_setting,
                                                    read_context_tokens_and_context_window_tokens_from_codex_conversation_file,
                                                    read_context_tokens_and_model_from_claude_code_transcript,
                                                    read_context_tokens_from_agy_transcript, read_last_lines, read_last_records,
                                                    read_turn_is_under_way_from_codex_conversation_file,
                                                    read_turn_started_at_from_claude_code_transcript,
                                                    read_turn_started_at_from_codex_conversation_file)
from agent_command_tab.dashboard_core import DashboardCore
from agent_command_tab.dashboard_core_api import ActionRequest, ActionResult
from gathered_window_builders import make_agent_session
from tab_duplicating import find_launch_command_of_a_duplicate_of_the_tab_of_s1


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
        self.assertEqual(read_context_tokens_and_model_from_claude_code_transcript(path), (673930, None))
        self.assertEqual(find_claude_code_transcript(["/no/such/folder", self.folder.name], "abc"), path)
        self.assertIsNone(find_claude_code_transcript([self.folder.name], "other"))

    def test_after_a_compaction_the_size_is_the_compacted_one_until_the_next_reply(self):
        reply_before = {"type": "assistant", "message": {"model": "claude-opus-5-5", "usage": {"input_tokens": 709217}}}
        compaction = {"type": "system", "subtype": "compact_boundary", "compactMetadata": {"preTokens": 709217, "postTokens": 20183}}
        path = self.write_lines("compacted.jsonl", [reply_before, compaction,
                                                    {"type": "user", "isCompactSummary": True, "message": {"content": "Summary"}}])
        # The size from the compaction, the model from the reply before it.
        self.assertEqual(read_context_tokens_and_model_from_claude_code_transcript(path), (20183, "claude-opus-5-5"))
        path = self.write_lines("replied.jsonl", [reply_before, compaction,
                                                  {"type": "assistant", "message": {"model": "claude-sonnet-5-5",
                                                                                    "usage": {"input_tokens": 73525}}}])
        self.assertEqual(read_context_tokens_and_model_from_claude_code_transcript(path), (73525, "claude-sonnet-5-5"))

    def test_a_subagents_replies_dont_count_as_the_main_agents(self):
        path = self.write_lines("with_a_subagent.jsonl", [
            {"type": "assistant", "message": {"model": "claude-opus-5-5", "usage": {"input_tokens": 300000}}},
            {"type": "assistant", "isSidechain": True,
             "message": {"model": "claude-haiku-5-5", "usage": {"input_tokens": 12000}}},
        ])
        self.assertEqual(read_context_tokens_and_model_from_claude_code_transcript(path), (300000, "claude-opus-5-5"))

    def test_a_codex_turn_is_under_way_from_its_task_started_until_its_task_complete(self):
        started = {"type": "event_msg", "payload": {"type": "task_started", "turn_id": "t1"}}
        streaming = {"type": "response_item", "payload": {"type": "message"}}
        completed = {"type": "event_msg", "payload": {"type": "task_complete", "turn_id": "t1"}}
        self.assertTrue(read_turn_is_under_way_from_codex_conversation_file(self.write_lines("a.jsonl", [started, streaming])))
        self.assertFalse(read_turn_is_under_way_from_codex_conversation_file(
            self.write_lines("b.jsonl", [started, streaming, completed])))
        self.assertIsNone(read_turn_is_under_way_from_codex_conversation_file(self.write_lines("c.jsonl", [streaming])))

    def test_codex_conversation_file_uses_the_latest_token_count(self):
        path = self.write_lines("rollout.jsonl", [
            {"type": "event_msg", "payload": {"type": "token_count", "info": {"last_token_usage": {"total_tokens": 100}}}},
            {"type": "event_msg", "payload": {"type": "token_count", "info": {"last_token_usage": {"total_tokens": 40546}}}},
            {"type": "response_item", "payload": {"type": "message"}},
        ])
        self.assertEqual(read_context_tokens_and_context_window_tokens_from_codex_conversation_file(path), (40546, None))

    def test_agy_context_is_the_latest_replys_input_and_cache_read_tokens(self):  # records as agy 1.5.9 writes them
        path = self.write_lines("transcript.jsonl", [
            {"step_index": 0, "source": "USER_EXPLICIT", "type": "USER_INPUT", "status": "DONE", "content": "hi"},
            {"step_index": 1, "source": "MODEL", "type": "PLANNER_RESPONSE", "status": "DONE",
             "model": "gemini-4-carbon-medium", "input_tokens": 9827, "cache_read_tokens": 0, "output_tokens": 265},
            {"step_index": 2, "source": "MODEL", "type": "GENERIC", "status": "DONE", "content": "Created At: ..."},
            {"step_index": 3, "source": "MODEL", "type": "PLANNER_RESPONSE", "status": "DONE",
             "model": "gemini-4-carbon-medium", "input_tokens": 2402, "cache_read_tokens": 7773, "output_tokens": 357},
            {"step_index": 4, "source": "USER_EXPLICIT", "type": "USER_INPUT", "status": "DONE", "content": "next"},
        ])
        self.assertEqual(read_context_tokens_from_agy_transcript(path), 2402 + 7773)
        no_reply_yet = self.write_lines("transcript_before_a_reply.jsonl", [
            {"step_index": 0, "source": "USER_EXPLICIT", "type": "USER_INPUT", "status": "DONE", "content": "hi"}])
        self.assertIsNone(read_context_tokens_from_agy_transcript(no_reply_yet))

    def test_codex_reports_its_context_window_with_its_usage(self):
        path = self.write_lines("rollout_with_window.jsonl", [
            {"type": "event_msg", "payload": {"type": "token_count",
                                              "info": {"last_token_usage": {"total_tokens": 40546}, "model_context_window": 258400}}},
            {"type": "event_msg", "payload": {"type": "token_count", "info": None}},  # a rate-limit-only report
        ])
        self.assertEqual(read_context_tokens_and_context_window_tokens_from_codex_conversation_file(path), (40546, 258400))
        # Each from the newest event that has it: a later usage without a window leaves the window as it was reported.
        path = self.write_lines("rollout_with_a_later_usage.jsonl", [
            {"type": "event_msg", "payload": {"type": "token_count",
                                              "info": {"last_token_usage": {"total_tokens": 40546}, "model_context_window": 258400}}},
            {"type": "event_msg", "payload": {"type": "token_count", "info": {"last_token_usage": {"total_tokens": 51000}}}},
        ])
        self.assertEqual(read_context_tokens_and_context_window_tokens_from_codex_conversation_file(path), (51000, 258400))

    def test_the_codex_adapter_reads_the_files_end_once_for_the_context_size_and_the_windows(self):
        path = self.write_lines("rollout_read_once.jsonl", [
            {"type": "event_msg", "payload": {"type": "token_count",
                                              "info": {"last_token_usage": {"total_tokens": 40546}, "model_context_window": 258400}}}])
        with unittest.mock.patch.object(context_window_usage, "read_last_lines", wraps=read_last_lines) as read_the_files_end:
            self.assertEqual(CodexAdapter().read_context_tokens_and_context_window_tokens(path, 301, None, None), (40546, 258400))
        read_the_files_end.assert_called_once()

    def test_the_claude_code_adapter_reads_the_files_end_once_for_the_context_size_and_the_model(self):
        path = self.write_lines("transcript_read_once.jsonl", [
            {"type": "assistant", "message": {"model": "claude-opus-4-6", "usage": {"input_tokens": 40_000}}}])
        with unittest.mock.patch.object(context_window_usage, "read_last_lines", wraps=read_last_lines) as read_the_files_end, \
                unittest.mock.patch.object(claude_code, "read_process_arguments_and_environment",
                                           return_value=(["claude", "--model", "opus"], {})):
            self.assertEqual(ClaudeCodeAdapter().read_context_tokens_and_context_window_tokens(path, 301, None, None),
                             (40_000, 200_000))  # the window's size follows from the reply's model
        read_the_files_end.assert_called_once()

    def test_a_claude_code_sessions_context_window_follows_the_model_it_was_started_with(self):
        self.write_lines("config/settings.json", [{"model": "opus[1m]"}])
        config_folder = os.path.join(self.folder.name, "config")
        environment = {"ANTHROPIC_MODEL": "sonnet[1m]"}  # "ANTHROPIC_MODEL=sonnet[1m] claude"
        self.assertEqual(read_claude_code_model_setting(["claude", "--model", "sonnet"], environment, config_folder), "sonnet")
        self.assertEqual(read_claude_code_model_setting(["claude", "--model=haiku"], {}, config_folder), "haiku")
        self.assertEqual(read_claude_code_model_setting(["claude", "--verbose"], environment, config_folder), "sonnet[1m]")
        self.assertEqual(read_claude_code_model_setting(["claude", "--verbose"], {}, config_folder), "opus[1m]")
        self.assertIsNone(read_claude_code_model_setting(["claude"], {}, os.path.join(self.folder.name, "no-config")))
        self.assertEqual(choose_claude_code_context_window_tokens("opus[1m]", "claude-opus-4-6", 40_000), 1_000_000)
        self.assertEqual(choose_claude_code_context_window_tokens("sonnet", "claude-sonnet-4-6", 40_000), 200_000)
        self.assertEqual(choose_claude_code_context_window_tokens(None, "claude-opus-4-6", 250_000), 1_000_000)  # more than 200k fits only in 1M

    def test_a_claude_code_sessions_context_window_follows_its_latest_replys_model(self):
        path = self.write_lines("models.jsonl", [
            {"type": "assistant", "message": {"model": "claude-opus-4-6", "usage": {"input_tokens": 5}}},
            {"type": "assistant", "message": {"model": "claude-opus-5-5", "usage": {"input_tokens": 175_000}}},  # after /model
            {"type": "assistant", "message": {"model": "<synthetic>", "usage": {"input_tokens": 0}}},
        ])
        self.assertEqual(read_context_tokens_and_model_from_claude_code_transcript(path), (175_000, "claude-opus-5-5"))
        self.assertEqual(read_context_tokens_and_model_from_claude_code_transcript(
            self.write_lines("no_reply.jsonl", [{"type": "user"}])), (None, None))
        # Claude Code started with no model setting: its default model's own window.
        self.assertEqual(choose_claude_code_context_window_tokens(None, "claude-opus-5-5", 175_000), 1_000_000)
        self.assertEqual(choose_claude_code_context_window_tokens(None, "claude-haiku-4-5-20251001", 50_000), 200_000)
        # No reply in the file's end (a large paste): not known, so the core keeps the size it had.
        self.assertIsNone(choose_claude_code_context_window_tokens(None, None, 50_000))
        self.assertEqual(choose_claude_code_context_window_tokens("opus[1m]", None, 50_000), 1_000_000)  # the setting settles it
        self.assertEqual(choose_claude_code_context_window_tokens(None, None, 250_000), 1_000_000)  # so does the size

    def test_only_claude_3_models_and_those_up_to_opus_and_sonnet_4_6_and_haiku_4_5_have_a_200k_window(self):
        for model in ["claude-3-7-sonnet-20250219", "claude-opus-4-20250514", "claude-opus-4-1-20250805", "claude-opus-4-5",
                      "claude-sonnet-4-5-20250929", "claude-opus-4-6", "us.anthropic.claude-sonnet-4-6-v1:0",
                      "claude-haiku-4-5-20251001"]:
            self.assertTrue(claude_model_has_a_200k_context_window(model), model)
        for model in ["claude-opus-4-7", "claude-opus-4-8", "claude-opus-5", "claude-opus-5-5", "claude-sonnet-5-5",
                      "claude-haiku-5-5", "claude-fable-5-1", "claude-opus-4-10", ""]:
            self.assertFalse(claude_model_has_a_200k_context_window(model), model)

    def test_files_without_usage_or_missing_files_give_none(self):
        self.assertEqual(read_context_tokens_and_model_from_claude_code_transcript(
            self.write_lines("empty.jsonl", [{"type": "user"}])), (None, None))
        self.assertEqual(read_context_tokens_and_model_from_claude_code_transcript(
            os.path.join(self.folder.name, "missing.jsonl")), (None, None))
        self.assertEqual(read_context_tokens_and_context_window_tokens_from_codex_conversation_file(
            os.path.join(self.folder.name, "missing.jsonl")), (None, None))

    def test_only_the_end_of_a_long_file_is_read_and_its_cut_first_line_is_dropped(self):
        path = self.write_lines("long.jsonl", ["x" * 100, "first", "second"])
        self.assertEqual(read_last_lines(path, byte_count=10), ["second"])  # the 10 bytes start inside "first"
        self.assertEqual(read_last_lines(path, byte_count=1000), ["second", "first", "x" * 100])

    def test_a_newest_line_longer_than_the_bytes_read_is_read_whole(self):
        path = self.write_lines("long_last_line.jsonl", ["x" * 100, "first", "y" * 100])
        self.assertEqual(read_last_lines(path, byte_count=10), ["y" * 100, "first"])  # 10, then 40, then 160 of its 208 bytes

    def test_a_line_still_being_written_is_left_out(self):
        path = self.write_lines("being_written.jsonl", ["first", "y" * 100])
        with open(path, "a") as file:
            file.write('{"type": "assi')  # no newline yet: the agent is still writing this record
        self.assertEqual(read_last_lines(path, byte_count=1000), ["y" * 100, "first"])
        # Nor is it the complete line the search for one stops at: 10 bytes, then 40, then all 121.
        self.assertEqual(read_last_lines(path, byte_count=10), ["y" * 100, "first"])

    def test_records_are_the_json_objects_newest_first(self):
        path = self.write_lines("mixed.jsonl", [{"type": "user"}, "not json", "[1, 2]", '"text"', {"type": "assistant"}])
        self.assertEqual(list(read_last_records(path)), [{"type": "assistant"}, {"type": "user"}])
        self.assertEqual(read_context_tokens_and_model_from_claude_code_transcript(self.write_lines("list.jsonl", ["[1, 2]"])),
                         (None, None))

    def write_codex_conversation_in_its_start_days_folder(self, name, folder, started_at, source="cli", originator="codex-tui"):
        started_day = datetime.date.fromtimestamp(started_at)
        started_at_text = datetime.datetime.fromtimestamp(started_at, datetime.timezone.utc).isoformat().replace("+00:00", "Z")
        return self.write_lines(f"sessions/{started_day.year:04d}/{started_day.month:02d}/{started_day.day:02d}/{name}", [
            {"type": "session_meta", "payload": {"cwd": folder, "timestamp": started_at_text, "source": source,
                                                 "originator": originator}}])

    def write_codex_conversation_last_written_at(self, name, started_at, modified_at):
        """Writes a Codex conversation in /Users/me/code that started at started_at and whose file
        was last written at modified_at; returns its path."""
        path = self.write_codex_conversation_in_its_start_days_folder(name, "/Users/me/code", started_at)
        os.utime(path, (modified_at, modified_at))
        return path

    def test_a_codex_conversation_run_by_codexs_shared_server_says_so_in_its_header(self):
        adapter = CodexAdapter()
        shared_server_file = self.write_codex_conversation_in_its_start_days_folder("shared.jsonl", "/p", time.time(), "vscode")
        own_process_file = self.write_codex_conversation_in_its_start_days_folder("own.jsonl", "/p", time.time(), "cli")
        with unittest.mock.patch.object(codex, "read_process_arguments_and_environment", return_value=(["codex"], {})):
            self.assertEqual((adapter.read_turns_run_outside_the_tab(shared_server_file, 301),
                              adapter.read_turns_run_outside_the_tab(own_process_file, 301)), (True, False))
            self.assertFalse(adapter.read_turns_run_outside_the_tab(os.path.join(self.folder.name, "missing.jsonl"), 301))

    def test_a_resumed_codex_conversation_goes_by_how_its_process_was_started_not_by_the_header_it_kept(self):
        started_by_a_codex_that_ran_its_own_turns = {"source": "cli", "originator": "codex-tui"}
        started_through_the_shared_server = {"source": "vscode", "originator": "codex-tui"}
        resume_arguments = ["node", "/opt/homebrew/bin/codex", "resume", "01a151f0-8f89-7711-a0cc-cfcae10915f8"]
        # A later Codex resumed it through its shared server, where a pause of the tab's processes can't stop its work.
        self.assertTrue(codex_process_runs_its_turns_in_the_shared_server(
            started_by_a_codex_that_ran_its_own_turns, resume_arguments, lambda: True))
        # No shared server runs: an earlier Codex, which runs its turns itself.
        self.assertFalse(codex_process_runs_its_turns_in_the_shared_server(
            started_by_a_codex_that_ran_its_own_turns, resume_arguments, lambda: False))
        for header in (started_by_a_codex_that_ran_its_own_turns, started_through_the_shared_server):
            self.assertFalse(codex_process_runs_its_turns_in_the_shared_server(header, resume_arguments + ["--no-daemon"], lambda: True))
        # Nor does the header that a Codex run through the server left: with no shared server running, none runs this process's turns.
        self.assertFalse(codex_process_runs_its_turns_in_the_shared_server(
            started_through_the_shared_server, resume_arguments, lambda: False))
        # A conversation the process started itself goes by its own header, and no server is looked for.
        self.assertEqual([codex_process_runs_its_turns_in_the_shared_server(header, ["codex", "--model", "o3"], self.fail)
                          for header in (started_by_a_codex_that_ran_its_own_turns, started_through_the_shared_server)], [False, True])
        # The adapter reads the header, the process's arguments, and whether a shared server runs.
        resumed_file = self.write_codex_conversation_in_its_start_days_folder("resumed.jsonl", "/p", time.time(), "cli")
        with unittest.mock.patch.object(codex, "read_process_arguments_and_environment", return_value=(resume_arguments, {})), \
                unittest.mock.patch.object(codex, "a_codex_shared_background_server_runs", return_value=True):
            self.assertTrue(CodexAdapter().read_turns_run_outside_the_tab(resumed_file, 301))

    def test_the_conversation_id_a_codex_process_was_started_to_resume_is_read_from_its_arguments(self):
        conversation_id = "01a151f0-8f89-7711-a0cc-cfcae10915f8"
        for arguments, expected_conversation_id in (
                (["codex", "resume", conversation_id], conversation_id),
                (["node", "/opt/homebrew/bin/codex", "-m", "o3", "resume", "--no-daemon", conversation_id], conversation_id),
                (["codex", "resume", "--last"], None), (["codex", "resume"], None), (["codex", "resume", "my session"], None),
                (["codex", "fork", conversation_id], None), (["codex"], None), ([], None)):
            self.assertEqual(find_conversation_id_named_after_resume(arguments), expected_conversation_id, arguments)

    def test_codex_conversation_file_is_found_by_folder_and_start_time(self):
        now = time.time()
        write_conversation = self.write_codex_conversation_in_its_start_days_folder
        own_file = write_conversation("rollout-a-01a151f0-8f89-7711-a0cc-cfcae10915f8.jsonl", "/Users/me/code", now - 30)
        write_conversation("rollout-b-01a151f0-8f89-7711-a0cc-cfcae10915f9.jsonl", "/Users/me/other", now - 20)  # another folder
        write_conversation("rollout-c-01a151f0-8f89-7711-a0cc-cfcae10915fa.jsonl", "/Users/me/code", now - 3600)  # before the agent
        write_conversation("rollout-d-01a151f0-8f89-7711-a0cc-cfcae10915fb.jsonl", "/Users/me/code", now - 29,
                           source={"subagent": {"other": "guardian"}})  # the auto-review helper's own file
        sessions_folder = os.path.join(self.folder.name, "sessions")
        self.assertEqual(find_codex_conversation_files(sessions_folder, now - 60, "/Users/me/code", now), [own_file])
        self.assertEqual(find_codex_conversation_files(sessions_folder, now - 60, "/Users/me/nothing-here", now), [])

    def test_a_codex_0_165_terminal_session_is_found_through_its_originator(self):
        # Codex 0.165 runs its terminal sessions through its shared background server, whose
        # headers say source "vscode"; its subagents' say "codex-tui" too.
        now = time.time()
        write_conversation = self.write_codex_conversation_in_its_start_days_folder
        own_file = write_conversation("rollout-a-01a151f0-8f89-7711-a0cc-cfcae10915f8.jsonl", "/Users/me/code", now - 30,
                                      source="vscode")
        for name, source, originator in (("rollout-b-01a151f0-8f89-7711-a0cc-cfcae10915f9.jsonl", {"subagent": {"other": "guardian"}},
                                          "codex-tui"),
                                         ("rollout-c-01a151f0-8f89-7711-a0cc-cfcae10915fa.jsonl", "vscode", "codex_vscode"),
                                         ("rollout-d-01a151f0-8f89-7711-a0cc-cfcae10915fb.jsonl", "exec", "codex_exec")):
            write_conversation(name, "/Users/me/code", now - 29, source=source, originator=originator)
        sessions_folder = os.path.join(self.folder.name, "sessions")
        self.assertEqual(find_codex_conversation_files(sessions_folder, now - 60, "/Users/me/code", now), [own_file])

    def test_a_resumed_codex_conversation_is_found_in_its_own_older_day_folder(self):
        now = time.time()
        write_conversation = self.write_codex_conversation_last_written_at
        # `codex resume`, started a minute ago, has written to a conversation started 3 days ago.
        resumed_file = write_conversation("rollout-a-01a151f0-8f89-7711-a0cc-cfcae10915f8.jsonl", now - 3 * 86400, now - 10)
        write_conversation("rollout-b-01a151f0-8f89-7711-a0cc-cfcae10915f9.jsonl", now - 2 * 86400, now - 86400)  # not since
        sessions_folder = os.path.join(self.folder.name, "sessions")
        self.assertEqual(find_codex_conversation_files(sessions_folder, now - 60, "/Users/me/code", now), [])
        self.assertEqual(find_codex_conversation_files(sessions_folder, now - 60, "/Users/me/code", now,
                                                       agent_resumed_a_conversation=True), [resumed_file])

    def test_a_resumed_codex_conversation_whose_file_is_known_is_found_without_the_other_day_folders(self):
        now = time.time()
        write_conversation = self.write_codex_conversation_last_written_at
        # `codex resume <ID>`, started a minute ago, has written to a conversation started 3 days ago.
        resumed_file = write_conversation("rollout-a-01a151f0-8f89-7711-a0cc-cfcae10915f8.jsonl", now - 3 * 86400, now - 10)
        # Another earlier conversation in the folder, written since by another session: not this process's.
        write_conversation("rollout-b-01a151f0-8f89-7711-a0cc-cfcae10915f9.jsonl", now - 2 * 86400, now - 5)
        started_later_in_the_process = write_conversation("rollout-c-01a151f0-8f89-7711-a0cc-cfcae10915fa.jsonl", now - 20, now - 1)  # /new
        sessions_folder = os.path.join(self.folder.name, "sessions")
        self.assertEqual(find_codex_conversation_file_by_id(sessions_folder, "01a151f0-8f89-7711-a0cc-cfcae10915f8"), resumed_file)
        self.assertIsNone(find_codex_conversation_file_by_id(sessions_folder, "01a151f0-8f89-7711-a0cc-000000000000"))
        self.assertEqual(find_codex_conversation_files(sessions_folder, now - 60, "/Users/me/code", now, agent_resumed_a_conversation=True,
                                                       resumed_conversation_file=resumed_file),
                         [started_later_in_the_process, resumed_file])

    def test_the_conversation_a_codex_process_was_started_to_resume_is_its_conversation_before_its_next_prompt(self):
        now = time.time()
        sessions_folder = os.path.join(self.folder.name, "sessions")

        def find_files(working_folder="/Users/me/code", **how_the_process_was_started):
            return find_codex_conversation_files(sessions_folder, now - 60, working_folder, now, **how_the_process_was_started)
        # `codex resume <ID>`, started a minute ago, of a conversation started 3 days ago and last written a day ago.
        resumed_file = self.write_codex_conversation_last_written_at("rollout-a-01a151f0-8f89-7711-a0cc-cfcae10915f8.jsonl",
                                                                     now - 3 * 86400, now - 86400)
        started_to_resume_it = {"agent_resumed_a_conversation": True, "resumed_conversation_file": resumed_file}
        self.assertEqual(find_files(**started_to_resume_it), [resumed_file])
        self.assertEqual(find_files("/Users/me/other", **started_to_resume_it), [])  # its header has to fit, as once it is written
        # Not the conversation of a process started without resume, or to resume one that its arguments don't name.
        self.assertEqual((find_files(), find_files(agent_resumed_a_conversation=True)), ([], []))
        os.utime(resumed_file, (now - 10, now - 10))  # its next prompt
        self.assertEqual(find_files(**started_to_resume_it), [resumed_file])

    def test_a_resumed_codex_conversation_left_without_a_prompt_gives_way_to_the_one_the_process_started_after_it(self):
        now = time.time()
        write_conversation = self.write_codex_conversation_last_written_at
        # `codex resume <ID>`, started a minute ago, then /new or /clear and a prompt, with none in the resumed conversation.
        # With both files fitting, Save session and Duplicate couldn't tell which conversation runs now.
        resumed_file = write_conversation("rollout-a-01a151f0-8f89-7711-a0cc-cfcae10915f8.jsonl", now - 3 * 86400, now - 86400)
        started_later_in_the_process = write_conversation("rollout-c-01a151f0-8f89-7711-a0cc-cfcae10915fa.jsonl", now - 20, now - 1)
        self.assertEqual(find_codex_conversation_files(os.path.join(self.folder.name, "sessions"), now - 60, "/Users/me/code", now,
                                                       agent_resumed_a_conversation=True, resumed_conversation_file=resumed_file),
                         [started_later_in_the_process])

    def make_core_with_one_codex_session(self, arguments, started_at, a_shared_server_runs=False):
        """Returns a core whose one agent session, s1, is a Codex process (301) started with these
        arguments at started_at in /Users/me/code, with the test's folder as its Codex home. The
        process is made up: what the adapter and the core read of it, and whether Codex's shared
        background server runs, are replaced until the test ends."""
        arguments_and_environment = (arguments, {"CODEX_HOME": self.folder.name})
        for module, name, replacement in (
                (codex, "read_process_info", lambda process_id: ProcessInfo(process_id, 1, "codex", started_at)),
                (codex, "read_process_arguments_and_environment", lambda process_id: arguments_and_environment),
                (dashboard_core, "read_process_arguments_and_environment", lambda process_id: arguments_and_environment),
                (codex, "read_process_current_folder", lambda process_id: "/Users/me/code"),
                (codex, "a_codex_shared_background_server_runs", lambda: a_shared_server_runs)):
            patch = unittest.mock.patch.object(module, name, replacement)
            patch.start()
            self.addCleanup(patch.stop)
        core = DashboardCore(lambda snapshot: None, lambda message: None)
        core.agent_sessions = {"s1": make_agent_session(301, CodexAdapter(), "Codex", "/Users/me/code", started_at)}
        return core

    def test_a_restored_codex_sessions_conversation_is_known_to_save_session_duplicate_and_its_row_before_its_next_prompt(self):
        now = time.time()
        conversation_id = "01a151f0-8f89-7711-a0cc-cfcae10915f8"
        # Restore sessions started it a minute ago, as `codex resume <ID>`; its conversation was last written a day ago.
        resumed_file = self.write_codex_conversation_last_written_at(f"rollout-a-{conversation_id}.jsonl", now - 3 * 86400, now - 86400)
        with open(resumed_file, "a") as file:
            file.write(json.dumps({"type": "event_msg", "payload": {
                "type": "token_count", "info": {"last_token_usage": {"total_tokens": 23000}}}}) + "\n")
        os.utime(resumed_file, (now - 86400, now - 86400))
        core = self.make_core_with_one_codex_session(["codex", "resume", conversation_id], now - 60)
        self.assertEqual(asyncio.run(core.find_conversation_to_save_or_copy("s1", core.agent_sessions["s1"])),
                         (conversation_id, None, self.folder.name, ""))
        self.assertEqual(find_launch_command_of_a_duplicate_of_the_tab_of_s1(core),
                         f"CODEX_HOME={shlex.quote(self.folder.name)} codex fork {conversation_id}")
        asyncio.run(core.update_conversation_file_readings("s1", core.agent_sessions["s1"]))
        self.assertEqual(core.session_readings_by_session_id["s1"]["context_tokens"], 23000)

    def test_a_restored_codex_session_that_shares_its_folder_with_another_codex_session_still_has_no_conversation_it_can_tell(self):
        now = time.time()
        conversation_id = "01a151f0-8f89-7711-a0cc-cfcae10915f8"
        self.write_codex_conversation_last_written_at(f"rollout-a-{conversation_id}.jsonl", now - 3 * 86400, now - 86400)
        core = self.make_core_with_one_codex_session(["codex", "resume", conversation_id], now - 60)
        core.agent_sessions["s2"] = make_agent_session(302, CodexAdapter(), "Codex", "/Users/me/code", now - 30)
        self.assertEqual(asyncio.run(core.find_conversation_to_save_or_copy("s1", core.agent_sessions["s1"])), (
            None, None, None, "another Codex session runs or ran in the same folder, so its conversation can't be told apart"))
        self.assertIsNone(asyncio.run(core.look_up_conversation_file("s1", core.agent_sessions["s1"])))  # nor does its row show one

    def test_a_restored_codex_session_cleared_before_its_next_prompt_has_no_conversation_until_it_writes_the_new_one(self):
        now = time.time()
        resumed_conversation_id, next_conversation_id = "01a151f0-8f89-7711-a0cc-cfcae10915f8", "01a151f0-8f89-7711-a0cc-cfcae10915f9"
        self.write_codex_conversation_last_written_at(f"rollout-a-{resumed_conversation_id}.jsonl", now - 3 * 86400, now - 86400)
        core = self.make_core_with_one_codex_session(["codex", "resume", resumed_conversation_id], now - 60)
        agent_session = core.agent_sessions["s1"]

        async def clear_from_the_list():  # what DashboardCore.type_slash_command_into_agent_session does around its typing
            core.forget_the_cleared_conversation("s1", agent_session, await core.find_current_conversation_file("s1", agent_session))

        def look_up_and_read_its_conversation_file():
            core.conversation_files_by_session_id["s1"]["looked_up_at"] = 0  # a lookup is due
            asyncio.run(core.update_conversation_file_readings("s1", agent_session))
        asyncio.run(clear_from_the_list())
        look_up_and_read_its_conversation_file()  # still finds the resumed conversation's file, which the /clear ended
        self.assertIsNone(core.conversation_files_by_session_id["s1"]["path"])
        self.assertEqual(asyncio.run(core.find_conversation_to_save_or_copy("s1", agent_session)),
                         (None, None, None, "it has had no conversation since its /clear"))
        self.assertEqual(find_launch_command_of_a_duplicate_of_the_tab_of_s1(core), "codex")  # the copy starts fresh
        # The new conversation's first prompt writes its file, the only one that fits from then on.
        next_file = self.write_codex_conversation_last_written_at(f"rollout-b-{next_conversation_id}.jsonl", now - 20, now - 1)
        look_up_and_read_its_conversation_file()
        self.assertEqual(core.conversation_files_by_session_id["s1"]["path"], next_file)
        self.assertEqual(asyncio.run(core.find_conversation_to_save_or_copy("s1", agent_session)),
                         (next_conversation_id, None, self.folder.name, ""))

    def test_a_restored_codex_session_paused_before_its_next_prompt_is_asked_where_its_turns_run(self):
        now = time.time()
        conversation_id = "01a151f0-8f89-7711-a0cc-cfcae10915f8"
        self.write_codex_conversation_last_written_at(f"rollout-a-{conversation_id}.jsonl", now - 3 * 86400, now - 86400)
        for arguments, a_shared_server_runs, turns_run_outside_the_tab in (
                (["codex", "resume", conversation_id], True, True), (["codex", "resume", conversation_id], False, False),
                (["codex", "resume", "--no-daemon", conversation_id], True, False)):
            core = self.make_core_with_one_codex_session(arguments, now - 60, a_shared_server_runs)
            with unittest.mock.patch.object(dashboard_core, "pause_agent_session",
                                            return_value=(ActionResult(False, "can't be paused"), None)) as pause:
                asyncio.run(core.handle_pause_agent_sessions(ActionRequest("pause_agent_sessions", {"session_ids": ["s1"]})))
            self.assertEqual(pause.call_args.kwargs["turns_run_outside_the_tab"], turns_run_outside_the_tab, arguments)

    def test_a_long_codex_turn_stays_under_way_with_its_last_messages_time_while_the_files_end_holds_only_its_tool_output(self):
        now = time.time()
        conversation_name = "rollout-a-01a151f0-8f89-7711-a0cc-cfcae10915f8.jsonl"
        path = self.write_codex_conversation_last_written_at(conversation_name, now - 30, now - 30)
        core = self.make_core_with_one_codex_session(["codex"], now - 60)
        agent_session = core.agent_sessions["s1"]
        core.session_readings_by_session_id["s1"] = {"turn_shows_on_screen": False}  # Codex hides its timer while its answer streams
        modified_at = now - 30

        def append_records_and_read_the_file(*records):
            """Returns what the core then knows of the conversation: (whether a turn is under way,
            its last message's time, its context size, the state of the tab's agent)."""
            nonlocal modified_at
            with open(path, "a") as file:
                for record in records:
                    file.write(json.dumps(record) + "\n")
            modified_at += 1  # a new modification time, which is what makes the core read the file again
            os.utime(path, (modified_at, modified_at))
            asyncio.run(core.update_conversation_file_readings("s1", agent_session))
            reading = core.session_readings_by_session_id["s1"]
            return (reading.get("conversation_shows_a_turn_under_way"), reading.get("last_message_at"), reading.get("context_tokens"),
                    core.read_status_value_of_the_tabs_own_agent("s1", agent_session))
        prompt_written_at = datetime.datetime(2026, 11, 3, 14, 5, 12, tzinfo=datetime.timezone.utc).timestamp()
        self.assertEqual(append_records_and_read_the_file(
            {"timestamp": "2026-11-03T14:05:11.900Z", "type": "event_msg", "payload": {"type": "task_started", "turn_id": "t1"}},
            {"timestamp": "2026-11-03T14:05:12.000Z", "type": "event_msg", "payload": {"type": "item_completed", "item": {
                "type": "UserMessage", "content": [{"type": "text", "text": "Count the samples in each cohort"}]}}},
            {"timestamp": "2026-11-03T14:05:14.000Z", "type": "event_msg", "payload": {
                "type": "token_count", "info": {"last_token_usage": {"total_tokens": 23000}}}}),
            (True, prompt_written_at, 23000, "working"))
        # More tool output than the part of the file's end that is read: it holds no turn's start, message or usage.
        tool_output = {"timestamp": "2026-11-03T14:06:00.000Z", "type": "response_item", "payload": {
            "type": "function_call_output", "call_id": "c1", "output": "x" * 8000}}
        self.assertEqual(append_records_and_read_the_file(*[tool_output] * (context_window_usage.BYTES_TO_READ_FROM_THE_END // 8000 + 1)),
                         (True, prompt_written_at, 23000, "working"))
        # The turn's end does replace what was read before.
        self.assertEqual(append_records_and_read_the_file(
            {"timestamp": "2026-11-03T14:20:00.000Z", "type": "event_msg", "payload": {"type": "task_complete", "turn_id": "t1"}}),
            (False, prompt_written_at, 23000, "idle"))
        # Written anew and shorter (only its header): what its earlier content told is forgotten.
        self.write_codex_conversation_last_written_at(conversation_name, now - 30, now - 30)
        self.assertEqual(append_records_and_read_the_file(), (None, None, None, "idle"))

    def test_unicode_line_separators_inside_a_record_do_not_split_it(self):
        # Written raw: json.dumps would escape them, Claude Code's own writer doesn't.
        path = self.write_lines("separators.jsonl", ['{"type": "assistant", "message": {"content": "a b\u0085c", '
                                                     '"usage": {"input_tokens": 7, "cache_read_input_tokens": 3}}}'])
        self.assertEqual(read_context_tokens_and_model_from_claude_code_transcript(path)[0], 10)

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

    def test_a_message_from_another_claude_code_session_starts_a_turn(self):
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
