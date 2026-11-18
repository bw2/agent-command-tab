"""The app's readers on files that agent tools really wrote (tests/recorded_agent_files, recorded
by dev/record_agent_conversations.py): conversation files read whole and as they grow, and
Claude Code's hook payloads."""

import datetime
import glob
import json
import os
import re
import tempfile
import unittest

from agent_command_tab.agent_adapters import build_built_in_agent_tool_adapters
from agent_command_tab.agent_adapters.agy import AgyAdapter
from agent_command_tab.agent_adapters.claude_code import ClaudeCodeAdapter
from agent_command_tab.agent_adapters.codex import CodexAdapter, codex_process_runs_its_turns_in_the_shared_server
from agent_command_tab.context_window_usage import (read_codex_session_header,
                                                    read_context_tokens_and_model_from_claude_code_transcript)
from agent_command_tab.conversation_search_texts import read_new_search_texts
from agent_command_tab.dashboard_core import DashboardCore
from agent_command_tab.hook_event_forwarder import reduce_hook_payload
from agent_command_tab.hook_event_tracking import list_background_task_ids_known_so_far

RECORDED_AGENT_FILES_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "recorded_agent_files")
CLAUDE_CODE_TRANSCRIPT = os.path.join(RECORDED_AGENT_FILES_FOLDER, "claude_code", "2.1.327", "transcript.jsonl")
CLAUDE_CODE_HOOK_PAYLOADS = os.path.join(RECORDED_AGENT_FILES_FOLDER, "claude_code", "2.1.327", "hook_payloads.jsonl")
CODEX_ROLLOUT = os.path.join(RECORDED_AGENT_FILES_FOLDER, "codex", "0.169.0", "rollout.jsonl")
AGY_TRANSCRIPT = os.path.join(RECORDED_AGENT_FILES_FOLDER, "agy", "1.5.10", "transcript.jsonl")
AGY_WHOLE_TRANSCRIPT = os.path.join(RECORDED_AGENT_FILES_FOLDER, "agy", "1.5.10", "transcript_full.jsonl")
FIRST_PROMPT = ("Write a file named notes.txt containing the line: merge the VCF files. Then run the shell "
                "command cat notes.txt, and finish by replying with exactly: Saved the merge note.")


def read_time(timestamp):
    """Returns the epoch seconds of a recorded ISO 8601 time such as "2026-11-18T05:04:57.479Z"."""
    return datetime.datetime.fromisoformat(timestamp.replace("Z", "+00:00")).timestamp()


def read_lines(path):
    """Returns the file's lines, each one record: readlines ends a line at its newline only, where
    splitlines would also split a record at a line separator (such as U+2028) inside one of its texts."""
    with open(path) as file:
        return file.readlines()


def list_readings_as_the_file_grows(path, read):
    """Returns read(a copy of the file) after each of its lines is written, one by one."""
    readings = []
    with tempfile.TemporaryDirectory() as folder:
        growing_file_path = os.path.join(folder, os.path.basename(path))
        with open(growing_file_path, "w") as growing_file:
            for line in read_lines(path):
                growing_file.write(line)
                growing_file.flush()
                readings.append(read(growing_file_path))
    return readings


def list_line_numbers_where(readings, value):
    """Returns the numbers (from 1) of the lines after which the reading was value."""
    return [line_number for line_number, reading in enumerate(readings, 1) if reading == value]


def list_line_numbers_and_readings_where_the_reading_changes(readings):
    """Returns [(line number from 1, the reading after that line)] for the lines after which the
    reading isn't what it was before them (None before the first line)."""
    return [(line_number, reading) for line_number, (reading_before, reading) in enumerate(zip([None] + readings, readings), 1)
            if reading != reading_before]


def read_times_of_the_records_on_lines(path, line_numbers, time_field_name="timestamp"):
    """Returns the time (epoch seconds) that the file's record on each of these lines (numbered
    from 1) holds as time_field_name."""
    lines = read_lines(path)
    return [read_time(json.loads(lines[line_number - 1])[time_field_name]) for line_number in line_numbers]


def read_all_search_texts(path, adapter):
    texts, _, _ = read_new_search_texts(path, 0, adapter.extract_search_texts_from_conversation_record)
    return texts


def list_adapters_by_recording_folder_name():
    """Returns {the folder of an agent tool's recordings ("claude_code"): its adapter}, for every built-in adapter."""
    specific_adapters, _ = build_built_in_agent_tool_adapters()
    return {re.sub(r"\W+", "_", adapter.agent_tool_name.lower()): adapter for adapter in specific_adapters}


def list_recorded_conversation_files():
    """Returns [(adapter, path)] for every recorded conversation file, of every version."""
    return [(adapter, path) for folder_name, adapter in list_adapters_by_recording_folder_name().items()
            for path in sorted(glob.glob(os.path.join(glob.escape(RECORDED_AGENT_FILES_FOLDER), folder_name, "*", "*.jsonl")))
            if os.path.basename(path) != "hook_payloads.jsonl"]


class EveryAgentToolHasARecordingTests(unittest.TestCase):
    def test_every_built_in_adapter_has_a_recording_and_every_recording_an_adapter(self):
        folder_names = set(list_adapters_by_recording_folder_name())
        # Folders only: not the README, nor a file the Finder leaves there (.DS_Store).
        self.assertEqual({name for name in os.listdir(RECORDED_AGENT_FILES_FOLDER)
                          if os.path.isdir(os.path.join(RECORDED_AGENT_FILES_FOLDER, name))}, folder_names)
        self.assertEqual({re.sub(r"\W+", "_", adapter.agent_tool_name.lower()) for adapter, _ in list_recorded_conversation_files()},
                         folder_names)

    def test_every_recording_is_read_whole_and_as_it_grows_alike(self):
        # Whatever version recorded it: its first prompt is found, and the size of its context.
        for adapter, path in list_recorded_conversation_files():
            texts = read_all_search_texts(path, adapter)
            self.assertEqual(texts[0], FIRST_PROMPT, path)
            self.assertGreater(adapter.read_context_tokens_and_context_window_tokens(path, os.getpid(), None, None)[0] or 0, 0, path)
            # Read on from where the last read ended, each line written in two parts, as a write the
            # agent hasn't finished leaves it: the same texts as one read of the whole file.
            texts_read_as_it_grows, read_up_to_byte = [], 0
            with tempfile.TemporaryDirectory() as folder:
                growing_file_path = os.path.join(folder, "conversation.jsonl")
                with open(growing_file_path, "w") as growing_file:
                    for line in read_lines(path):
                        for part in (line[:len(line) // 2], line[len(line) // 2:]):
                            growing_file.write(part)
                            growing_file.flush()
                            new_texts, read_up_to_byte, started_over = read_new_search_texts(
                                growing_file_path, read_up_to_byte, adapter.extract_search_texts_from_conversation_record)
                            self.assertFalse(started_over)
                            texts_read_as_it_grows += new_texts
            self.assertEqual(texts_read_as_it_grows, texts, path)


class RecordingsHoldNothingPersonalTests(unittest.TestCase):
    def test_no_home_folder_but_the_stand_in_no_email_address_and_no_api_key(self):
        for path in glob.glob(os.path.join(glob.escape(RECORDED_AGENT_FILES_FOLDER), "**", "*.jsonl"), recursive=True):
            with open(path) as file:
                text = file.read()
            self.assertEqual(re.findall(r"/Users/(?!me\b)[^/\"]+|/home/[^/\"]+|/var/folders/", text), [], path)
            self.assertEqual(re.findall(r"[\w.+-]+@[\w-]+\.[\w.]+", text), [], path)
            self.assertEqual(re.findall(r"\bsk-[A-Za-z0-9_-]{16,}", text), [], path)


class ClaudeCodeTranscriptTests(unittest.TestCase):
    adapter = ClaudeCodeAdapter()

    def test_search_finds_the_prompts_replies_and_shell_commands(self):
        # Not the commands' output, nor Claude Code's notice that the background command ended.
        self.assertEqual(read_all_search_texts(CLAUDE_CODE_TRANSCRIPT, self.adapter), [
            FIRST_PROMPT, "printf 'merge the VCF files\\n' > notes.txt && cat notes.txt", "Saved the merge note.",
            "Run sleep 3 as a background shell command (run_in_background), then run ls -1 in the foreground and reply "
            "with the number of files listed.",
            "sleep 3", "ls -1",
            "The `ls -1` in the working directory lists 1 file: `notes.txt`. The `sleep 3` is running in the background "
            "and will notify me when it finishes.",
            "The background `sleep 3` finished with exit code 0. Nothing else is pending. The working directory still "
            "lists 1 file, `notes.txt`."])

    def test_the_context_size_and_the_model_come_from_the_last_reply(self):
        # Not its window's size, which also goes by the process and by the Claude Code settings of the Mac the test runs on.
        self.assertEqual(
            self.adapter.read_context_tokens_and_context_window_tokens(CLAUDE_CODE_TRANSCRIPT, os.getpid(), None, None)[0], 20752)
        self.assertEqual(read_context_tokens_and_model_from_claude_code_transcript(CLAUDE_CODE_TRANSCRIPT),
                         (20752, "claude-haiku-5-5"))

    def test_as_the_file_grows_a_tool_call_is_open_until_its_result_and_each_prompt_starts_a_turn(self):
        open_tool_names = list_readings_as_the_file_grows(
            CLAUDE_CODE_TRANSCRIPT, lambda path: self.adapter.read_open_tool_call(path)["open_tool_name"])
        # Line 25 is the first turn's Bash call, answered on line 26; lines 47 and 48 the second turn's
        # two (sleep in the background, ls), answered on lines 49 and 50.
        self.assertEqual(list_line_numbers_where(open_tool_names, "Bash"), [25, 47, 48, 49])
        turn_starts = list_readings_as_the_file_grows(CLAUDE_CODE_TRANSCRIPT, self.adapter.read_turn_started_at)
        # No turn before the first prompt (line 4). Each turn's start is then read on every line up to the
        # next one's: the second prompt (line 43), then Claude Code's notice that the background command
        # ended (line 60), which starts a turn too.
        self.assertEqual([list_line_numbers_where(turn_starts, turn_start)
                          for turn_start in (None, read_time("2026-11-18T05:04:43.347Z"), read_time("2026-11-18T05:04:50.333Z"),
                                             read_time("2026-11-18T05:04:57.479Z"))],
                         [[1, 2, 3], list(range(4, 43)), list(range(43, 60)), list(range(60, len(turn_starts) + 1))])

    def test_as_the_file_grows_the_last_message_is_the_newest_prompt_tool_call_tool_result_or_reply(self):
        last_message_times = list_readings_as_the_file_grows(CLAUDE_CODE_TRANSCRIPT, self.adapter.read_last_message_at)
        # Each prompt (lines 4 and 43), tool call (25, 47, 48), tool result (26, 49, 50) and reply (36, 56, 63), and the
        # notice that the background command ended (60), moves it to its own record's time. None of the other 55 lines
        # does: the queue operations, attachments, hook summaries and costs that Claude Code keeps beside the conversation.
        message_line_numbers = [4, 25, 26, 36, 43, 47, 48, 49, 50, 56, 60, 63]
        self.assertEqual(list_line_numbers_and_readings_where_the_reading_changes(last_message_times),
                         list(zip(message_line_numbers, read_times_of_the_records_on_lines(CLAUDE_CODE_TRANSCRIPT, message_line_numbers))))
        self.assertEqual(last_message_times[-1], read_time("2026-11-18T05:04:58.841Z"))  # the last reply's, 4 lines before the end


class CodexRolloutTests(unittest.TestCase):
    adapter = CodexAdapter()

    def test_search_finds_the_prompt_replies_and_the_command_once(self):
        # Codex records the command both as a call of its exec tool and as a CommandExecution item.
        self.assertEqual(read_all_search_texts(CODEX_ROLLOUT, self.adapter), [
            FIRST_PROMPT, "I’ll write `notes.txt` and run `cat notes.txt` to verify it.",
            "printf '%s\\n' 'merge the VCF files.' > notes.txt && cat notes.txt", "Saved the merge note."])

    def test_the_context_size_window_and_where_its_turns_run(self):
        self.assertEqual(self.adapter.read_context_tokens_and_context_window_tokens(CODEX_ROLLOUT, None, None, None), (23304, 475000))
        self.assertFalse(codex_process_runs_its_turns_in_the_shared_server(  # `codex exec` runs them itself
            read_codex_session_header(CODEX_ROLLOUT), ["codex", "exec"], a_shared_server_runs=lambda: True))

    def test_as_the_file_grows_the_turn_is_under_way_until_it_completes_with_exec_open_until_its_output(self):
        line_count = len(read_lines(CODEX_ROLLOUT))
        turn_is_under_way = list_readings_as_the_file_grows(CODEX_ROLLOUT, self.adapter.read_turn_is_under_way)
        # Line 2 is the turn's task_started, the last line its task_complete.
        self.assertEqual(list_line_numbers_where(turn_is_under_way, True), list(range(2, line_count)))
        self.assertEqual(turn_is_under_way[-1], False)
        open_tool_names = list_readings_as_the_file_grows(
            CODEX_ROLLOUT, lambda path: self.adapter.read_open_tool_call(path)["open_tool_name"])
        self.assertEqual(list_line_numbers_where(open_tool_names, "exec"), [16, 17, 18])  # its output is line 19

    def test_as_the_file_grows_the_last_message_is_the_newest_prompt_or_reply(self):
        last_message_times = list_readings_as_the_file_grows(CODEX_ROLLOUT, self.adapter.read_last_message_at)
        # The prompt (line 13) and the two replies (14 and 21) move it to their own records' times. What Codex gave the
        # model before the prompt (lines 3 to 8) doesn't, nor do the command, the token counts and the turn's end.
        message_line_numbers = [13, 14, 21]
        self.assertEqual(list_line_numbers_and_readings_where_the_reading_changes(last_message_times),
                         list(zip(message_line_numbers, read_times_of_the_records_on_lines(CODEX_ROLLOUT, message_line_numbers))))
        self.assertEqual(last_message_times[-1], read_time("2026-11-18T05:05:12.959Z"))  # the last reply's, 4 lines before the end


class AgyTranscriptTests(unittest.TestCase):
    adapter = AgyAdapter()

    def test_search_finds_the_prompts_replies_and_shell_commands(self):
        # In the whole transcript, which the app reads when agy wrote one, as in transcript.jsonl.
        for path in (AGY_WHOLE_TRANSCRIPT, AGY_TRANSCRIPT):
            self.assertEqual(read_all_search_texts(path, self.adapter), [
                FIRST_PROMPT, "cat notes.txt", "Saved the merge note.",
                "Run ls -1 with the shell and reply with the number of files listed.", "ls -1", "1"], path)

    def test_the_context_size_comes_from_the_last_reply(self):
        for path in (AGY_WHOLE_TRANSCRIPT, AGY_TRANSCRIPT):
            self.assertEqual(self.adapter.read_context_tokens_and_context_window_tokens(path, None, None, None), (9482, None), path)

    def test_as_the_file_grows_the_last_message_is_the_newest_prompt_or_model_answer(self):
        for path in (AGY_WHOLE_TRANSCRIPT, AGY_TRANSCRIPT):
            last_message_times = list_readings_as_the_file_grows(path, self.adapter.read_last_message_at)
            # The prompts are lines 1 and 7 and the model's answers lines 2, 4, 6, 9 and 11. After a tool's result
            # (lines 3, 5 and 10) and after agy's own system message (line 8), it is still the time of the line before.
            self.assertEqual(last_message_times, read_times_of_the_records_on_lines(
                path, [1, 2, 2, 4, 4, 6, 7, 7, 9, 9, 11], time_field_name="created_at"), path)
            self.assertEqual(last_message_times[-1], read_time("2026-11-18T05:05:34Z"), path)  # when the model began its last reply


class ClaudeCodeHookPayloadsTests(unittest.TestCase):
    FIRST_RUNS_AGENT_PROCESS_ID = 4001
    SECOND_RUNS_AGENT_PROCESS_ID = 4002

    def reduce_each_payload(self):
        """Returns the event the forwarder makes of each recorded payload, sent a second apart from
        100.0 on, by the agent process of its run: the recorder ran Claude Code twice (the second
        time with --continue), so two processes sent them, each one up to its SessionEnd. Each ran
        its hooks in an "sh", the forwarder's parent, below its own program, named for its version."""
        events, agent_process_id = [], self.FIRST_RUNS_AGENT_PROCESS_ID
        for index, line in enumerate(read_lines(CLAUDE_CODE_HOOK_PAYLOADS)):
            payload = json.loads(line)
            events.append(reduce_hook_payload(payload, "Claude Code", [[agent_process_id + 1000, "sh"], [agent_process_id, "2.1.327"]],
                                              100.0 + index))
            if payload["hook_event_name"] == "SessionEnd":
                agent_process_id = self.SECOND_RUNS_AGENT_PROCESS_ID
        return events

    def test_the_events_of_two_runs_with_a_background_command(self):
        events = self.reduce_each_payload()
        self.assertEqual([(event["hook_event_name"], event["source"] or event["reason"]) for event in events], [
            ("SessionStart", "startup"), ("UserPromptSubmit", None), ("PreToolUse", None), ("PostToolUse", None),
            ("Stop", None), ("SessionEnd", "other"),
            ("SessionStart", "resume"), ("UserPromptSubmit", None), ("PreToolUse", None), ("PreToolUse", None),
            ("PostToolUse", None), ("PostToolUse", None), ("Stop", None),
            ("UserPromptSubmit", None), ("Stop", None), ("SessionEnd", "other")])
        background_shell_launch, = [event for event in events if event["background_task_launched_id"]]
        self.assertEqual((background_shell_launch["background_task_launched_id"],
                          background_shell_launch["background_task_launched_type"]), ("b7x1ehsbe", "shell"))
        # The second run's turn ends with it running, and the turn its end notice starts ends without it.
        self.assertEqual([event["background_tasks"] for event in events if event["hook_event_name"] == "Stop"],
                         [[], [{"id": "b7x1ehsbe", "type": "shell", "status": "running"}], []])

    def test_the_core_keeps_a_hook_state_for_each_runs_process(self):
        core = DashboardCore(lambda snapshot: None, lambda message: None)
        events = self.reduce_each_payload()
        background_task_ids_after_each_turn = []
        for event in events:
            core.note_hook_event(event)
            if event["hook_event_name"] == "Stop":
                background_task_ids_after_each_turn.append(
                    set(core.hook_state_by_agent_process_id[event["ancestor_processes"][1][0]]["background_task_ids"]))
            if event["background_task_launched_id"]:
                hook_state_after_the_background_shell_started = core.hook_state_by_agent_process_id[self.SECOND_RUNS_AGENT_PROCESS_ID]
        self.assertEqual(background_task_ids_after_each_turn, [set(), {"b7x1ehsbe"}, set()])
        # The second run reported no startup, so its tasks weren't known before its turn ended: all but the shell it reported starting.
        self.assertIsNone(hook_state_after_the_background_shell_started["background_task_ids"])
        self.assertEqual(list_background_task_ids_known_so_far(hook_state_after_the_background_shell_started), {"b7x1ehsbe"})
        self.assertEqual(core.hook_state_by_agent_process_id, {
            self.FIRST_RUNS_AGENT_PROCESS_ID: {
                "agent_tool_name": "Claude Code", "startup_reported_at": 100.0, "last_prompt_at": 101.0,
                "last_turn_end_at": 104.0, "last_main_agent_tool_event_at": 103.0, "session_ended_at": 105.0,
                "background_task_ids": set(), "background_shells_reported_at_by_task_id": {}},
            # It resumed the first run's conversation, so it reported no startup: its SessionStart's source is "resume".
            self.SECOND_RUNS_AGENT_PROCESS_ID: {
                "agent_tool_name": "Claude Code", "last_prompt_at": 113.0,
                "last_turn_end_at": 114.0, "last_main_agent_tool_event_at": 111.0, "session_ended_at": 115.0,
                "background_task_ids": set(), "background_shells_reported_at_by_task_id": {}}})


if __name__ == "__main__":
    unittest.main()
