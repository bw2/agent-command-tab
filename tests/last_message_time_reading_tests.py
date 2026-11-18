"""When the last message of a conversation was written: what each agent tool's reader finds in
made-up conversation files (last_message_time_reading; recorded_agent_files_tests reads the files
the tools really wrote), how the core passes it on to the tab's row
(DashboardCore.update_conversation_file_readings), and what the row of an agent that ended keeps of it."""

import asyncio
import json
import os
import tempfile
import unittest
import unittest.mock

from agent_command_tab import dashboard_core
from agent_command_tab.dashboard_core import DashboardCore
from agent_command_tab.last_message_time_reading import (read_last_message_at_from_agy_transcript,
                                                         read_last_message_at_from_claude_code_transcript,
                                                         read_last_message_at_from_codex_conversation_file)
from agent_command_tab.snapshot_builder import build_exited_agent_session_entry
from agent_command_tab.user_interface.row_text_rendering import build_agent_session_row_text_parts
from first_turn_of_an_agent_with_no_message_yet_tests import StandInAgyAdapter
from gathered_window_builders import make_agent_session_entry, make_gathered_tab, make_gathered_window
from recorded_agent_files_tests import read_time

# Four moments of a made-up conversation, in order.
FIRST_TIME, SECOND_TIME, THIRD_TIME, FOURTH_TIME = (
    "2026-11-03T14:05:09.250Z", "2026-11-03T14:05:12.000Z", "2026-11-03T14:07:30.500Z", "2026-11-03T14:20:00.000Z")


def write_conversation_file(folder, records, line_still_being_written=""):
    """Writes the records one per line (a str as it is: a line that isn't JSON), then
    line_still_being_written without a newline, as a write the agent hasn't finished leaves it.
    Returns the file's path."""
    path = os.path.join(folder, "conversation.jsonl")
    with open(path, "w") as file:
        for record in records:
            file.write((record if isinstance(record, str) else json.dumps(record)) + "\n")
        file.write(line_still_being_written)
    return path


class ReaderTestCase(unittest.TestCase):
    """Reads made-up records with read_last_message_at, which each subclass sets to its tool's reader."""

    read_last_message_at = None

    def read(self, records, line_still_being_written=""):
        with tempfile.TemporaryDirectory() as folder:
            return self.read_last_message_at(write_conversation_file(folder, records, line_still_being_written))

    def check_that_a_missing_or_empty_file_has_no_message(self):
        self.assertIsNone(self.read([]))
        with tempfile.TemporaryDirectory() as folder:
            self.assertIsNone(self.read_last_message_at(os.path.join(folder, "no-such-file.jsonl")))


def claude_code_record(record_type, timestamp, content, **extra_fields):
    return dict({"type": record_type, "timestamp": timestamp, "message": {"role": record_type, "content": content}}, **extra_fields)


CLAUDE_CODE_PROMPT = claude_code_record("user", FIRST_TIME, "Count the samples in each cohort")
CLAUDE_CODE_TOOL_CALL = claude_code_record("assistant", SECOND_TIME, [
    {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "wc -l cohorts/*.tsv"}}])
CLAUDE_CODE_TOOL_RESULT = claude_code_record("user", THIRD_TIME, [{"type": "tool_result", "tool_use_id": "t1", "content": "412 total"}])
CLAUDE_CODE_NOTE_ABOUT_AN_INTERRUPTED_TURN = claude_code_record("user", FOURTH_TIME, [
    {"type": "text", "text": "[Request interrupted by user]"}])
# What Claude Code keeps beside the conversation, written after the turn's last message.
CLAUDE_CODE_RECORDS_THAT_ARE_NO_MESSAGE = [
    {"type": "system", "subtype": "stop_hook_summary", "timestamp": FOURTH_TIME, "hookCount": 1},
    {"type": "attachment", "timestamp": FOURTH_TIME, "attachment": {"type": "hook_success"}},
    {"type": "queue-operation", "operation": "enqueue", "timestamp": FOURTH_TIME},
    {"type": "last-prompt", "lastPrompt": "Count the samples in each cohort"},
    {"type": "cost-state", "totalCostUSD": 0.02},
]


class ClaudeCodeLastMessageTests(ReaderTestCase):
    read_last_message_at = staticmethod(read_last_message_at_from_claude_code_transcript)

    def test_a_prompt_a_tool_call_its_result_and_a_note_about_an_interrupted_turn_each_move_the_time(self):
        records = [CLAUDE_CODE_PROMPT, CLAUDE_CODE_TOOL_CALL, CLAUDE_CODE_TOOL_RESULT, CLAUDE_CODE_NOTE_ABOUT_AN_INTERRUPTED_TURN]
        self.assertEqual([self.read(records[:count]) for count in range(1, len(records) + 1)],
                         [read_time(time_) for time_ in (FIRST_TIME, SECOND_TIME, THIRD_TIME, FOURTH_TIME)])

    def test_what_claude_code_keeps_beside_the_conversation_does_not_move_the_time(self):
        self.assertEqual(self.read([CLAUDE_CODE_PROMPT, CLAUDE_CODE_TOOL_CALL, *CLAUDE_CODE_RECORDS_THAT_ARE_NO_MESSAGE]),
                         read_time(SECOND_TIME))

    def test_a_subagents_record_does_not_move_the_time(self):
        subagent_reply = claude_code_record("assistant", FOURTH_TIME, [{"type": "text", "text": "412 samples"}], isSidechain=True)
        self.assertEqual(self.read([CLAUDE_CODE_PROMPT, subagent_reply]), read_time(FIRST_TIME))

    def test_a_line_still_being_written_and_a_line_that_is_not_json_leave_the_message_before_them(self):
        reply_line = json.dumps(claude_code_record("assistant", FOURTH_TIME, [{"type": "text", "text": "412 samples in 3 cohorts"}]))
        self.assertEqual(self.read([CLAUDE_CODE_PROMPT, "{not json"], line_still_being_written=reply_line[:40]),
                         read_time(FIRST_TIME))
        self.assertEqual(self.read([CLAUDE_CODE_PROMPT, "{not json", reply_line]), read_time(FOURTH_TIME))  # once it is written

    def test_no_message_yet_gives_none(self):
        self.check_that_a_missing_or_empty_file_has_no_message()
        self.assertIsNone(self.read(CLAUDE_CODE_RECORDS_THAT_ARE_NO_MESSAGE))

    def test_a_newest_message_without_a_time_that_can_be_read_gives_none_rather_than_an_earlier_messages_time(self):
        for timestamp in (None, "", "a moment ago", 1793185924):
            reply = claude_code_record("assistant", timestamp, [{"type": "text", "text": "412 samples in 3 cohorts"}])
            self.assertIsNone(self.read([CLAUDE_CODE_PROMPT, reply]), timestamp)
        reply_without_a_timestamp = {"type": "assistant", "message": {"role": "assistant", "content": "412 samples in 3 cohorts"}}
        self.assertIsNone(self.read([CLAUDE_CODE_PROMPT, reply_without_a_timestamp]))


def codex_record(record_type, timestamp, payload):
    return {"timestamp": timestamp, "type": record_type, "payload": payload}


def codex_item_completed(timestamp, item_type, text):
    return codex_record("event_msg", timestamp, {"type": "item_completed", "item": {
        "type": item_type, "content": [{"type": "text", "text": text}]}})


# What Codex gives the model itself, written as messages of the "developer" and of the "user".
CODEX_RECORDS_OF_WHAT_CODEX_GIVES_THE_MODEL = [
    codex_record("session_meta", FIRST_TIME, {"id": "0199a1b2-0000-7000-8000-000000000001", "source": "cli", "cwd": "/Users/me/project"}),
    codex_record("response_item", FIRST_TIME, {"type": "message", "role": "developer",
                                               "content": [{"type": "input_text", "text": "You are a coding agent."}]}),
    codex_record("response_item", FIRST_TIME, {"type": "message", "role": "user", "content": [
        {"type": "input_text", "text": "<environment_context>\n  <cwd>/Users/me/project</cwd>\n</environment_context>"}]}),
]
CODEX_PROMPT = codex_item_completed(SECOND_TIME, "UserMessage", "Count the samples in each cohort")
CODEX_REPLY = codex_item_completed(THIRD_TIME, "AgentMessage", "412 samples in 3 cohorts")
# The same reply as the model's message, the token count and the turn's end, written after the reply.
CODEX_RECORDS_THAT_FOLLOW_A_REPLY = [
    codex_record("response_item", FOURTH_TIME, {"type": "message", "role": "assistant", "phase": "final_answer",
                                                "content": [{"type": "output_text", "text": "412 samples in 3 cohorts"}]}),
    codex_record("event_msg", FOURTH_TIME, {"type": "token_count", "info": {"last_token_usage": {"total_tokens": 23000}}}),
    codex_record("event_msg", FOURTH_TIME, {"type": "task_complete", "last_agent_message": "412 samples in 3 cohorts"}),
]


class CodexLastMessageTests(ReaderTestCase):
    read_last_message_at = staticmethod(read_last_message_at_from_codex_conversation_file)

    def test_a_prompt_and_a_reply_each_move_the_time_and_what_follows_the_reply_does_not(self):
        self.assertEqual([self.read(records) for records in (
            [*CODEX_RECORDS_OF_WHAT_CODEX_GIVES_THE_MODEL, CODEX_PROMPT],
            [*CODEX_RECORDS_OF_WHAT_CODEX_GIVES_THE_MODEL, CODEX_PROMPT, CODEX_REPLY],
            [*CODEX_RECORDS_OF_WHAT_CODEX_GIVES_THE_MODEL, CODEX_PROMPT, CODEX_REPLY, *CODEX_RECORDS_THAT_FOLLOW_A_REPLY])],
            [read_time(time_) for time_ in (SECOND_TIME, THIRD_TIME, THIRD_TIME)])

    def test_the_events_earlier_versions_wrote_for_a_prompt_and_a_reply_count_too(self):
        records = [codex_record("event_msg", FIRST_TIME, {"type": "user_message", "message": "Count the samples in each cohort"}),
                   codex_record("event_msg", FOURTH_TIME, {"type": "agent_message", "message": "412 samples in 3 cohorts"})]
        self.assertEqual((self.read(records[:1]), self.read(records)), (read_time(FIRST_TIME), read_time(FOURTH_TIME)))

    def test_a_session_with_no_prompt_yet_gives_none_though_codex_wrote_what_it_gives_the_model(self):
        self.check_that_a_missing_or_empty_file_has_no_message()
        self.assertIsNone(self.read(CODEX_RECORDS_OF_WHAT_CODEX_GIVES_THE_MODEL))

    def test_a_newest_message_without_a_time_that_can_be_read_gives_none_rather_than_an_earlier_messages_time(self):
        self.assertIsNone(self.read([CODEX_PROMPT, codex_item_completed("a moment ago", "AgentMessage", "412 samples in 3 cohorts")]))
        reply_without_a_timestamp = {"type": "event_msg", "payload": {"type": "agent_message", "message": "412 samples in 3 cohorts"}}
        self.assertIsNone(self.read([CODEX_PROMPT, reply_without_a_timestamp]))


def agy_record(record_type, source, created_at, **extra_fields):
    return dict({"type": record_type, "source": source, "status": "DONE", "created_at": created_at}, **extra_fields)


AGY_PROMPT = agy_record("USER_INPUT", "USER_EXPLICIT", FIRST_TIME,
                        content="<USER_REQUEST>\nCount the samples in each cohort\n</USER_REQUEST>")
AGY_TOOL_CALL = agy_record("PLANNER_RESPONSE", "MODEL", SECOND_TIME,
                           tool_calls=[{"name": "run_command", "args": {"CommandLine": "wc -l cohorts/*.tsv"}}])
AGY_REPLY = agy_record("PLANNER_RESPONSE", "MODEL", FOURTH_TIME, content="412 samples in 3 cohorts")
# The tools' results and agy's own steps, written after the model's tool call.
AGY_RECORDS_THAT_ARE_NO_MESSAGE = [
    agy_record("RUN_COMMAND", "MODEL", THIRD_TIME, content="412 total"),
    agy_record("GENERIC", "MODEL", THIRD_TIME, content="Created At: 2026-11-03T14:07:30Z"),
    agy_record("SYSTEM_MESSAGE", "SYSTEM", THIRD_TIME, content="The following is a <SYSTEM_MESSAGE> not actually sent by the user."),
    agy_record("ERROR_MESSAGE", "SYSTEM", THIRD_TIME, error="API error (attempt 1): UNAVAILABLE (code 503)"),
    agy_record("CHECKPOINT", "SYSTEM", THIRD_TIME, content="{{ CHECKPOINT 0 }}"),
]


class AgyLastMessageTests(ReaderTestCase):
    read_last_message_at = staticmethod(read_last_message_at_from_agy_transcript)

    def test_a_prompt_and_a_model_answer_each_move_the_time_and_tool_results_and_agys_own_steps_do_not(self):
        self.assertEqual([self.read(records) for records in (
            [AGY_PROMPT], [AGY_PROMPT, AGY_TOOL_CALL], [AGY_PROMPT, AGY_TOOL_CALL, *AGY_RECORDS_THAT_ARE_NO_MESSAGE],
            [AGY_PROMPT, AGY_TOOL_CALL, *AGY_RECORDS_THAT_ARE_NO_MESSAGE, AGY_REPLY])],
            [read_time(time_) for time_ in (FIRST_TIME, SECOND_TIME, SECOND_TIME, FOURTH_TIME)])

    def test_a_time_written_with_a_utc_offset_is_the_same_moment(self):
        self.assertEqual(self.read([dict(AGY_PROMPT, created_at="2026-11-03T09:05:09-05:00")]), read_time("2026-11-03T14:05:09Z"))

    def test_no_message_yet_gives_none(self):
        self.check_that_a_missing_or_empty_file_has_no_message()
        self.assertIsNone(self.read(AGY_RECORDS_THAT_ARE_NO_MESSAGE))

    def test_a_newest_message_without_a_time_that_can_be_read_gives_none_rather_than_an_earlier_messages_time(self):
        self.assertIsNone(self.read([AGY_PROMPT, dict(AGY_TOOL_CALL, created_at="a moment ago")]))
        self.assertIsNone(self.read([AGY_PROMPT, {"type": "PLANNER_RESPONSE", "source": "MODEL", "content": "412 samples"}]))


class LastMessageTimeInTheCoreTests(unittest.TestCase):
    """A core with one iTerm2 tab, s1, running an agy as process 101, whose conversation has no
    transcript until a test writes to it."""

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.transcript_path = os.path.join(folder.name, "transcript_full.jsonl")
        self.transcript_modified_at = 1_700_000_000
        self.snapshots = []
        self.core = DashboardCore(self.snapshots.append, lambda message: None)
        self.adapter = StandInAgyAdapter()
        self.core.specific_adapters = [self.adapter]
        self.seen_session_id = None  # the tab isn't looked at until a test sets this to "s1"
        self.core.find_seen_session_id = lambda: self.seen_session_id
        self.core.gathered_windows = [make_gathered_window("w1", [make_gathered_tab("t1", "s1", "agy", 101)])]
        for name, replacement in (("process_is_alive", lambda process_id: True), ("process_is_stopped", lambda process_id: False),
                                  ("list_descendant_processes", lambda process_id, maximum_depth=None: [])):
            patch = unittest.mock.patch.object(dashboard_core, name, replacement)
            patch.start()
            self.addCleanup(patch.stop)

    def publish(self, status_set_in_iterm2):
        """Publishes a snapshot with this status value set in iTerm2 for the tab; returns the tab's row."""
        self.core.gathered_windows[0]["tabs"][0]["first_session"]["status_set_in_iterm2"] = status_set_in_iterm2
        self.core.publish_snapshot_if_changed()
        return self.snapshots[-1].monitors[0].windows[0].agent_sessions[0]

    def append_to_the_transcript_and_read_it(self, *records):
        """Appends the records as agy would, then runs one round of the core's periodic reading for
        the tab, with the lookup of its conversation file due."""
        with open(self.transcript_path, "a") as file:
            for record in records:
                file.write(json.dumps(record) + "\n")
        self.transcript_modified_at += 1  # a new modification time, which is what makes the core read the file again
        os.utime(self.transcript_path, (self.transcript_modified_at, self.transcript_modified_at))
        self.adapter.transcript_path = self.transcript_path
        if "s1" in self.core.conversation_files_by_session_id:
            self.core.conversation_files_by_session_id["s1"]["looked_up_at"] = 0
        asyncio.run(self.core.update_conversation_file_readings("s1", self.core.agent_sessions["s1"]))

    def test_a_tabs_row_gets_the_time_of_the_last_message_in_its_conversation_file(self):
        self.assertIsNone(self.publish("idle").last_message_at)  # no conversation file yet
        self.append_to_the_transcript_and_read_it(AGY_PROMPT, AGY_TOOL_CALL)
        self.assertEqual(self.publish("working").last_message_at, read_time(SECOND_TIME))
        self.append_to_the_transcript_and_read_it(*AGY_RECORDS_THAT_ARE_NO_MESSAGE)
        self.assertEqual(self.publish("working").last_message_at, read_time(SECOND_TIME))
        self.append_to_the_transcript_and_read_it(AGY_REPLY)
        self.assertEqual(self.publish("idle").last_message_at, read_time(FOURTH_TIME))

    def test_looking_at_a_tab_whose_turn_is_done_leaves_the_time_as_it_is(self):
        self.publish("working")
        self.append_to_the_transcript_and_read_it(AGY_PROMPT, AGY_REPLY)
        row = self.publish("idle")
        self.assertEqual((row.status, row.last_message_at), ("done", read_time(FOURTH_TIME)))
        self.seen_session_id = "s1"
        row = self.publish("idle")
        self.assertEqual((row.status, row.last_message_at), ("idle", read_time(FOURTH_TIME)))

    def test_a_conversation_that_is_no_longer_the_tabs_takes_its_time_with_it(self):
        self.publish("idle")
        self.append_to_the_transcript_and_read_it(AGY_PROMPT)
        self.assertEqual(self.publish("idle").last_message_at, read_time(FIRST_TIME))
        self.core.forget_conversation_file_readings("s1")  # as /clear and a new conversation do
        self.assertIsNone(self.publish("idle").last_message_at)


class LastMessageTimeOfAnExitedRowTests(unittest.TestCase):
    def test_the_row_of_an_agent_that_ended_while_working_keeps_the_time_and_shows_it(self):
        ended_at = read_time(FOURTH_TIME)
        last_entry = make_agent_session_entry("s1", "cohort counts", status="working", context_tokens=84_000,
                                              last_message_at=read_time(THIRD_TIME))
        self.assertEqual(build_agent_session_row_text_parts(last_entry, [True], ended_at).time_since_last_message_text, "")
        exited_entry = build_exited_agent_session_entry(last_entry, ended_at, None, make_gathered_tab("t-s1", "s1", "-zsh", 80), None)
        self.assertEqual((exited_entry.status, exited_entry.context_tokens, exited_entry.last_message_at),
                         ("exited", None, read_time(THIRD_TIME)))
        self.assertEqual(build_agent_session_row_text_parts(exited_entry, [True], ended_at).time_since_last_message_text, "12m")


if __name__ == "__main__":
    unittest.main()
