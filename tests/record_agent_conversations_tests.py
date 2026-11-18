"""The parts of dev/record_agent_conversations.py that run no agent tool: what it removes from a
recorded file's records, how it writes the folders and the local times in one, the personal texts
it looks for and those it finds left in one, the command its Claude Code hooks run, which Codex
conversation file and which agy conversation are the recording's, whether a version gets recorded
at all, how a recording's files are saved (and that they aren't with something personal left in
them), and the exit status."""

import contextlib
import copy
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
import unittest.mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "dev"))

import record_agent_conversations  # noqa: E402

PROMPT = "Write a file named notes.txt containing the line: merge the VCF files."
REMOVED_TEXT = "(removed by dev/record_agent_conversations.py)"  # as the saved files and their README have it
ZERO_UUID = "00000000-0000-0000-0000-000000000000"
# A line of a person's own instructions to agents. Made up, like every ID, folder, name and address in these tests.
INSTRUCTIONS_LINE = "Always reply in French, and name every variable after a river."


class ScrubClaudeCodeRecordTests(unittest.TestCase):
    def test_a_request_sent_to_the_model_keeps_only_its_type_and_its_session(self):
        for kind in ("api-request", "api-request-blob", "api-request-shape"):
            with self.subTest(kind=kind):
                self.assertEqual(record_agent_conversations.scrub_claude_code_record(
                    {"type": kind, "sessionId": "abc-123", "requestId": "request-1",
                     "request": {"system": INSTRUCTIONS_LINE, "metadata": {"user_id": "someone@example.com"}}}),
                    {"type": kind, "sessionId": "abc-123", "removed": REMOVED_TEXT})

    def test_attached_context_keeps_its_type_but_neither_its_contents_nor_any_key_that_starts_with_rendered(self):
        self.assertEqual(record_agent_conversations.scrub_claude_code_record(
            {"parentUuid": "record-1", "type": "attachment", "uuid": "record-2", "cwd": "/Users/someone/project", "sessionId": "abc-123",
             "attachment": {"type": "skill_listing", "content": INSTRUCTIONS_LINE, "skillCount": 12},
             "renderedContent": INSTRUCTIONS_LINE, "renderedTokenCount": 14}),
            {"parentUuid": "record-1", "type": "attachment", "uuid": "record-2", "cwd": "/Users/someone/project", "sessionId": "abc-123",
             "attachment": {"type": "skill_listing"}})
        self.assertEqual(record_agent_conversations.scrub_claude_code_record({"type": "attachment", "attachment": None}),
                         {"type": "attachment", "attachment": {"type": None}})  # one whose contents are missing

    def test_the_accounts_ids_are_replaced_by_zeros(self):
        self.assertEqual(record_agent_conversations.scrub_claude_code_record(
            {"type": "session-owner", "accountUuid": "3f0e8a52-6c1d-4b7e-9a20-5d4c3b2a1f0e",
             "organizationUuid": "b7c1d9e4-2a3f-4c5d-8e6f-0a1b2c3d4e5f", "sessionId": "abc-123"}),
            {"type": "session-owner", "accountUuid": ZERO_UUID, "organizationUuid": ZERO_UUID, "sessionId": "abc-123"})

    def test_the_commands_of_the_hooks_that_ran_are_removed(self):
        self.assertEqual(record_agent_conversations.scrub_claude_code_record(
            {"type": "system", "subtype": "stop_hook_summary", "hookCount": 2, "hookErrors": [], "sessionId": "abc-123",
             "hookInfos": [{"command": "python3 /Users/someone/notify.py", "durationMs": 92}, {"command": "say done", "durationMs": 5}]}),
            {"type": "system", "subtype": "stop_hook_summary", "hookCount": 2, "hookErrors": [], "sessionId": "abc-123",
             "hookInfos": [{"command": REMOVED_TEXT, "durationMs": 92}, {"command": REMOVED_TEXT, "durationMs": 5}]})

    def test_the_conversations_own_records_are_kept_as_they_are(self):
        for record in ({"type": "user", "message": {"role": "user", "content": PROMPT}, "cwd": "/Users/someone/project"},
                       {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "Saved the note."}]}},
                       {"type": "system", "subtype": "compact_boundary", "sessionId": "abc-123"}):  # a system record that names no hooks
            with self.subTest(record=record):
                self.assertEqual(record_agent_conversations.scrub_claude_code_record(copy.deepcopy(record)), record)


def build_codex_message_record(role, content):
    """Returns a Codex record of a message with this role and these content items."""
    return {"timestamp": "2026-11-18T05:00:02.000Z", "type": "response_item",
            "payload": {"type": "message", "id": "msg_019a9d1c", "role": role, "content": content}}


class ScrubCodexRecordTests(unittest.TestCase):
    def test_the_header_gets_zeros_for_the_accounts_ids_and_loses_codexs_own_instructions(self):
        self.assertEqual(record_agent_conversations.scrub_codex_record(
            {"timestamp": "2026-11-18T05:00:01.000Z", "type": "session_meta",
             "payload": {"creator_user_id": "user-Zk3VqLm8", "creator_account_id": "3f0e8a52-6c1d-4b7e-9a20-5d4c3b2a1f0e",
                         "id": "019a9d1c-8f89-7711-a0cc-cfcae10915f8", "cwd": "/Users/someone/project", "source": "exec",
                         "base_instructions": {"text": "You are Codex, a coding agent."}}}),
            {"timestamp": "2026-11-18T05:00:01.000Z", "type": "session_meta",
             "payload": {"creator_user_id": "user-0", "creator_account_id": ZERO_UUID,
                         "id": "019a9d1c-8f89-7711-a0cc-cfcae10915f8", "cwd": "/Users/someone/project", "source": "exec",
                         "base_instructions": REMOVED_TEXT}})

    def test_instructions_and_the_accounts_plan_and_usage_are_removed_whatever_they_hold_and_wherever_a_record_holds_them(self):
        for key in ("base_instructions", "developer_instructions", "user_instructions", "agents_md", "rate_limits"):
            with self.subTest(key=key):
                self.assertEqual(record_agent_conversations.scrub_codex_record(
                    {"type": "event_msg",
                     "payload": {"type": "thread_settings_applied", key: INSTRUCTIONS_LINE,
                                 "thread_settings": {"model": "gpt-6.2-sol", "collaboration_mode": {"settings": {key: {"plan": "pro"}}}},
                                 "items": [{"id": "item-1", key: [INSTRUCTIONS_LINE]}]}}),
                    {"type": "event_msg",
                     "payload": {"type": "thread_settings_applied", key: REMOVED_TEXT,
                                 "thread_settings": {"model": "gpt-6.2-sol", "collaboration_mode": {"settings": {key: REMOVED_TEXT}}},
                                 "items": [{"id": "item-1", key: REMOVED_TEXT}]}})

    def test_the_world_state_keeps_no_state_and_the_additional_tools_no_tool(self):
        self.assertEqual(record_agent_conversations.scrub_codex_record(
            {"type": "world_state", "payload": {"full": True, "state": {"instructions": {"/Users/someone/AGENTS.md": INSTRUCTIONS_LINE}}}}),
            {"type": "world_state", "payload": {"full": True, "state": {}}})
        self.assertEqual(record_agent_conversations.scrub_codex_record(
            {"type": "response_item", "metadata": {"history_sequence": 1},
             "payload": {"type": "additional_tools", "id": "at_019a9d1c", "role": "developer",
                         "tools": [{"name": "search_notes", "description": "Searches the notes of someone@example.com."}]}}),
            {"type": "response_item", "metadata": {"history_sequence": 1},
             "payload": {"type": "additional_tools", "id": "at_019a9d1c", "role": "developer", "tools": []}})

    def test_a_developers_message_keeps_none_of_its_texts(self):
        self.assertEqual(record_agent_conversations.scrub_codex_record(build_codex_message_record("developer", [
            {"type": "input_text", "text": "You are Codex, a coding agent."},
            {"type": "input_text", "text": f"<skills_instructions>{INSTRUCTIONS_LINE}</skills_instructions>"}])),
            build_codex_message_record("developer", [{"type": "input_text", "text": REMOVED_TEXT},
                                                     {"type": "input_text", "text": REMOVED_TEXT}]))

    def test_a_users_message_loses_the_agents_md_text_and_names_utc_as_the_time_zone_but_keeps_the_rest(self):
        def build_users_message_record(first_text, time_zone):
            return build_codex_message_record("user", [
                {"type": "input_text", "text": first_text},
                {"type": "input_text", "text": "<environment_context>\n  <cwd>/Users/someone/project</cwd>\n"
                                               f"  <timezone>{time_zone}</timezone>\n</environment_context>"},
                {"type": "input_text", "text": PROMPT},
                {"type": "input_image", "image_url": "data:image/png;base64,iVBORw0KGgo="}])
        self.assertEqual(record_agent_conversations.scrub_codex_record(build_users_message_record(
            f"# AGENTS.md instructions for /Users/someone/project\n\n{INSTRUCTIONS_LINE}", "America/New_York")),
            build_users_message_record(REMOVED_TEXT, "UTC"))

    def test_an_assistants_message_and_the_other_records_are_kept_as_they_are(self):
        for record in (build_codex_message_record("assistant", [{"type": "output_text", "text": "Saved the merge note."}]),
                       {"type": "response_item", "payload": {"type": "custom_tool_call", "name": "exec", "input": "cat notes.txt"}},
                       {"type": "event_msg", "payload": {"type": "task_complete", "last_agent_message": "Saved the merge note."}},
                       {"type": "event_msg"}):  # without a payload to look into
            with self.subTest(record=record):
                self.assertEqual(record_agent_conversations.scrub_codex_record(copy.deepcopy(record)), record)


class ScrubJsonlTextTests(unittest.TestCase):
    def test_a_line_separator_inside_a_record_does_not_split_it_and_a_line_cut_short_is_left_out(self):
        # Written as they are: json.dumps would escape them, Claude Code's own writer doesn't.
        text = ('{"type":"assistant","message":{"content":"a\u2028b\u0085c"}}\n'
                '{"type":"user","cwd":"/run-folder/sub"}\n'
                '{"type":"assis')
        self.assertEqual(record_agent_conversations.scrub_jsonl_text(text, lambda record: record, "/run-folder", "/Users/someone"),
                         '{"type":"assistant","message":{"content":"a\u2028b\u0085c"}}\n'
                         '{"type":"user","cwd":"/Users/me/project/sub"}\n')

    def test_the_run_folder_is_replaced_as_given_as_resolved_and_as_claude_code_names_its_project_folder_and_so_is_the_home_folder(self):
        # A run folder reached through a link, as a Mac's /tmp is (it is /private/tmp): the resolved
        # path holds the given one, so it has to be replaced first.
        text = ('{"cwd":"/private/tmp/run.1/sub","transcript_path":"/Users/someone/.claude/projects/-private-tmp-run-1/abc-123.jsonl"}\n'
                '{"command":"cat /tmp/run.1/notes.txt /Users/someone/.claude/projects/-tmp-run-1/abc-123.jsonl"}\n')
        with unittest.mock.patch.object(record_agent_conversations.os.path, "realpath", return_value="/private/tmp/run.1"):
            scrubbed_text = record_agent_conversations.scrub_jsonl_text(text, lambda record: record, "/tmp/run.1", "/Users/someone")
        self.assertEqual(scrubbed_text,
                         '{"cwd":"/Users/me/project/sub","transcript_path":"/Users/me/.claude/projects/-Users-me-project/abc-123.jsonl"}\n'
                         '{"command":"cat /Users/me/project/notes.txt /Users/me/.claude/projects/-Users-me-project/abc-123.jsonl"}\n')

    def test_a_local_time_whose_utc_offset_tells_the_time_zone_is_written_as_the_same_moment_in_utc(self):
        # Not just without its offset: a local clock time beside the records' UTC times would tell the zone too.
        text = ('{"content":"Created At: 2026-11-17T23:05:20-05:00, sent 2026-11-18T04:05:20.250Z, 5-05:00 left, '
                'and 2026-11-18T09:35:20.5+05:30"}\n')
        self.assertEqual(record_agent_conversations.scrub_jsonl_text(text, lambda record: record, "/run-folder", "/Users/someone"),
                         '{"content":"Created At: 2026-11-18T04:05:20Z, sent 2026-11-18T04:05:20.250Z, 5-05:00 left, '
                         'and 2026-11-18T04:05:20.5Z"}\n')


class PersonalTextsToLookForTests(unittest.TestCase):
    def list_personal_texts(self, home_folder, output_of_git_config_user_email):
        """Returns list_personal_texts's result for the user "someone" of the Mac "someones-mac",
        with this home folder and with a stand-in for git, which printed this."""
        with unittest.mock.patch.dict(os.environ, {"HOME": home_folder}), \
                unittest.mock.patch.object(record_agent_conversations.getpass, "getuser", return_value="someone"), \
                unittest.mock.patch.object(record_agent_conversations.socket, "gethostname", return_value="someones-mac.local"), \
                unittest.mock.patch.object(record_agent_conversations.subprocess, "run", return_value=subprocess.CompletedProcess(
                    ["git", "config", "user.email"], 0, stdout=output_of_git_config_user_email, stderr="")):
            return record_agent_conversations.list_personal_texts()

    def test_they_are_the_user_name_the_macs_name_the_git_email_and_each_long_line_of_the_instruction_files(self):
        with tempfile.TemporaryDirectory() as home_folder:
            # No email address set in git, no instruction file.
            self.assertEqual(self.list_personal_texts(home_folder, ""), {"someone", "someones-mac"})
            other_instructions_line = "Never push to a branch that another person has pushed to."
            for folder_name, file_name, instructions in (
                    (".claude", "CLAUDE.md", f"# Instructions\n\n  - {INSTRUCTIONS_LINE}  \n- Be brief.\n"),
                    (".gemini", "GEMINI.md", other_instructions_line + "\n")):
                os.mkdir(os.path.join(home_folder, folder_name))
                with open(os.path.join(home_folder, folder_name, file_name), "w") as instructions_file:
                    instructions_file.write(instructions)
            self.assertEqual(self.list_personal_texts(home_folder, "someone@example.com\n"),
                             {"someone", "someones-mac", "someone@example.com", "- " + INSTRUCTIONS_LINE, other_instructions_line})


class PersonalTextsLeftTests(unittest.TestCase):
    def test_a_personal_text_is_found_also_with_its_quotes_and_backslashes_escaped_as_json_holds_them(self):
        instructions_line = 'Reply "done" once the merge has finished, and never write to C:\\data'
        text = json.dumps({"content": "Their instructions: " + instructions_line}, ensure_ascii=False) + "\n"
        self.assertNotIn(instructions_line, text)  # only its escaped form is
        self.assertEqual(record_agent_conversations.find_personal_texts_left(text, {instructions_line, "someone"}),
                         [instructions_line])
        self.assertEqual(record_agent_conversations.find_personal_texts_left(text, {"someone"}), [])

    def test_an_email_address_and_an_api_key_are_found_whoever_they_belong_to(self):
        # Not the second "sk-...", one character short of an API key, nor the one that is the end of another word.
        text = '{"content":"From a.colleague@example.com with sk-0123456789abcdef, not sk-0123456789abcde or risk-0123456789abcdef"}\n'
        self.assertEqual(record_agent_conversations.find_personal_texts_left(text, {"someone"}),
                         ["a.colleague@example.com", "sk-0123456789abcdef"])


class CommandThatSavesAHookPayloadTests(unittest.TestCase):
    def test_the_command_adds_each_payload_as_a_line_to_a_file_whose_path_holds_a_space(self):
        with tempfile.TemporaryDirectory() as folder:
            hook_payloads_path = os.path.join(folder, "hook payloads", "hook_payloads.jsonl")
            os.mkdir(os.path.dirname(hook_payloads_path))
            command = record_agent_conversations.build_command_that_saves_a_hook_payload(hook_payloads_path)
            for payload in ({"hook_event_name": "SessionStart", "source": "startup"}, {"hook_event_name": "Stop"}):
                subprocess.run(command, shell=True, input=json.dumps(payload), text=True, check=True, timeout=30)  # as Claude Code runs it
            self.assertEqual(os.listdir(folder), ["hook payloads"])
            with open(hook_payloads_path) as hook_payloads_file:
                self.assertEqual(hook_payloads_file.read(),
                                 '{"hook_event_name":"SessionStart","source":"startup"}\n{"hook_event_name":"Stop"}\n')


class CodexConversationFileOfTheRecordingTests(unittest.TestCase):
    RECORDED_CONVERSATION_ID = "019a9d1c-8f89-7711-a0cc-cfcae10915f8"
    OTHER_CONVERSATION_ID = "019a9d1c-8f89-7711-a0cc-cfcae10915f9"

    def record_codex_that_printed_the_conversation_id(self, conversation_id, codex_home_folder):
        """Returns record_codex's result for this Codex home folder, with a stand-in for the Codex
        run: its output, which names the conversation it held."""
        with unittest.mock.patch.object(record_agent_conversations, "run_agent",
                                        return_value=json.dumps({"type": "thread.started", "thread_id": conversation_id}) + "\n"), \
                unittest.mock.patch.dict(os.environ, {"CODEX_HOME": codex_home_folder}):
            return record_agent_conversations.record_codex("/run-folder")

    def test_it_is_found_by_its_id_in_a_codex_home_folder_whose_name_holds_brackets_and_a_missing_one_raises(self):
        with tempfile.TemporaryDirectory() as folder:
            codex_home_folder = os.path.join(folder, "codex [work]")  # found only with the brackets taken as they are
            day_folder = os.path.join(codex_home_folder, "sessions", "2026", "11", "18")
            os.makedirs(day_folder)
            for conversation_id in (self.RECORDED_CONVERSATION_ID, self.OTHER_CONVERSATION_ID):
                with open(os.path.join(day_folder, f"rollout-2026-11-18T05-04-57-{conversation_id}.jsonl"), "w") as rollout_file:
                    rollout_file.write(f"the conversation {conversation_id}\n")
            self.assertEqual(self.record_codex_that_printed_the_conversation_id(self.RECORDED_CONVERSATION_ID, codex_home_folder),
                             {"rollout.jsonl": (f"the conversation {self.RECORDED_CONVERSATION_ID}\n",
                                                record_agent_conversations.scrub_codex_record)})
            with self.assertRaises(RuntimeError):  # a conversation Codex wrote no file for
                self.record_codex_that_printed_the_conversation_id("019a9d1c-8f89-7711-a0cc-000000000000", codex_home_folder)


class AgyConversationOfTheRecordingTests(unittest.TestCase):
    EARLIER_CONVERSATION_ID = "0054ada5-9370-4f9d-9b01-3cd280ddea3b"
    RECORDED_CONVERSATION_ID = "5bb9d1c8-3df7-4e4d-b96d-0155db6b8a5a"
    OTHER_TABS_CONVERSATION_ID = "1b5f3f86-1b68-4a7d-aa5c-81e46434075e"
    OTHER_TABS_NEW_CONVERSATION_ID = "9d2c7a10-55aa-4c0e-8a53-6f0b0c1d2e3f"

    def setUp(self):
        agy_folder = tempfile.TemporaryDirectory()
        self.addCleanup(agy_folder.cleanup)
        self.agy_folder = agy_folder.name

    def write_transcript(self, conversation_id, prompt):
        transcript_path = os.path.join(self.agy_folder, "brain", conversation_id, ".system_generated", "logs", "transcript.jsonl")
        os.makedirs(os.path.dirname(transcript_path))
        with open(transcript_path, "w") as transcript_file:
            transcript_file.write(json.dumps({"type": "USER_INPUT", "content": f"<USER_REQUEST>\n{prompt}\n</USER_REQUEST>"}) + "\n")

    def test_it_is_the_new_conversation_that_holds_the_prompt_whichever_one_was_written_to_last(self):
        self.assertEqual(record_agent_conversations.list_agy_conversation_ids(self.agy_folder), set())  # agy never ran
        self.write_transcript(self.EARLIER_CONVERSATION_ID, PROMPT)  # an earlier recording
        conversation_ids_from_before = record_agent_conversations.list_agy_conversation_ids(self.agy_folder)
        self.assertEqual(conversation_ids_from_before, {self.EARLIER_CONVERSATION_ID})
        self.write_transcript(self.RECORDED_CONVERSATION_ID, PROMPT)
        os.mkdir(os.path.join(self.agy_folder, "brain", self.OTHER_TABS_NEW_CONVERSATION_ID))  # no message yet: no transcript
        self.write_transcript(self.OTHER_TABS_CONVERSATION_ID, "Summarize the README.")  # started meanwhile, written to last
        self.assertEqual(record_agent_conversations.find_the_new_agy_conversation_that_holds_the_prompt(
            self.agy_folder, conversation_ids_from_before, PROMPT), self.RECORDED_CONVERSATION_ID)

    def test_none_or_two_that_hold_the_prompt_raise(self):
        self.write_transcript(self.OTHER_TABS_CONVERSATION_ID, "Summarize the README.")
        with self.assertRaises(ValueError):
            record_agent_conversations.find_the_new_agy_conversation_that_holds_the_prompt(self.agy_folder, set(), PROMPT)
        for conversation_id in (self.EARLIER_CONVERSATION_ID, self.RECORDED_CONVERSATION_ID):
            self.write_transcript(conversation_id, PROMPT)
        with self.assertRaises(ValueError):
            record_agent_conversations.find_the_new_agy_conversation_that_holds_the_prompt(self.agy_folder, set(), PROMPT)

    def test_the_whole_transcript_is_recorded_with_transcript_jsonl_when_agy_wrote_one(self):
        with tempfile.TemporaryDirectory() as logs_folder:
            for file_name, text in (("transcript.jsonl", '{"content":"cut"}\n'), ("chunks.bin", "not a transcript")):
                with open(os.path.join(logs_folder, file_name), "w") as file:
                    file.write(text)
            self.assertEqual({file_name: text for file_name, (text, _) in
                              record_agent_conversations.read_agy_transcripts(logs_folder).items()},
                             {"transcript.jsonl": '{"content":"cut"}\n'})
            with open(os.path.join(logs_folder, "transcript_full.jsonl"), "w") as file:
                file.write('{"content":"whole"}\n')
            self.assertEqual({file_name: text for file_name, (text, _) in
                              record_agent_conversations.read_agy_transcripts(logs_folder).items()},
                             {"transcript.jsonl": '{"content":"cut"}\n', "transcript_full.jsonl": '{"content":"whole"}\n'})


class FolderForANewRecordingTests(unittest.TestCase):
    def setUp(self):
        recorded_agent_files_folder = tempfile.TemporaryDirectory()
        self.addCleanup(recorded_agent_files_folder.cleanup)
        self.recorded_agent_files_folder = recorded_agent_files_folder.name
        self.folder_of_the_installed_version = os.path.join(self.recorded_agent_files_folder, "codex", "0.169.0")

    def find_folder(self, replace_the_recording_of_this_version=False, codex_is_installed=True):
        """Returns find_folder_for_a_new_recording's result for Codex 0.169.0, installed or not,
        with the recordings in a folder of this test's, and whether Codex was asked for its version."""
        with unittest.mock.patch.object(record_agent_conversations.shutil, "which",
                                        return_value="/opt/homebrew/bin/codex" if codex_is_installed else None), \
                unittest.mock.patch.object(record_agent_conversations, "read_version", return_value="0.169.0") as read_version, \
                unittest.mock.patch.object(record_agent_conversations, "RECORDED_AGENT_FILES_FOLDER", self.recorded_agent_files_folder):
            return (record_agent_conversations.find_folder_for_a_new_recording("codex", replace_the_recording_of_this_version),
                    read_version.called)

    def test_a_version_without_a_recording_gets_a_folder_of_its_own(self):
        os.makedirs(os.path.join(self.recorded_agent_files_folder, "codex", "0.168.0"))  # an earlier version's
        self.assertEqual(self.find_folder(), ((self.folder_of_the_installed_version, None), True))

    def test_an_agent_tool_that_isnt_installed_is_not_recorded_nor_asked_for_its_version(self):
        self.assertEqual(self.find_folder(codex_is_installed=False), ((None, "codex isn't installed"), False))

    def test_a_version_whose_folder_holds_no_recorded_file_is_recorded(self):
        os.makedirs(self.folder_of_the_installed_version)  # as removing a recording's files leaves it
        self.assertEqual(self.find_folder(), ((self.folder_of_the_installed_version, None), True))

    def test_a_version_that_has_a_recording_is_recorded_again_only_when_asked_for(self):
        os.makedirs(self.folder_of_the_installed_version)
        with open(os.path.join(self.folder_of_the_installed_version, "rollout.jsonl"), "w") as rollout_file:
            rollout_file.write('{"type":"session_meta"}\n')
        (output_folder, reason_not_to_record), _ = self.find_folder()
        self.assertIsNone(output_folder)
        self.assertEqual(reason_not_to_record, f"{self.folder_of_the_installed_version} holds a recording of this version already "
                                               "(--replace records it again)")
        self.assertEqual(self.find_folder(replace_the_recording_of_this_version=True),
                         ((self.folder_of_the_installed_version, None), True))


class SavedRecordingTests(unittest.TestCase):
    TRANSCRIPT_TEXT = '{"type":"user"}\n'
    HOOK_PAYLOADS_TEXT = '{"hook_event_name":"Stop"}\n'

    def setUp(self):
        claude_code_recordings_folder = tempfile.TemporaryDirectory()
        self.addCleanup(claude_code_recordings_folder.cleanup)
        self.claude_code_recordings_folder = claude_code_recordings_folder.name
        self.output_folder = os.path.join(self.claude_code_recordings_folder, "2.1.327")

    def record_and_save(self, recorded_texts_by_file_name):
        """Returns record_and_save's result with a stand-in for the Claude Code run, which wrote
        these files, and with no personal text to look for in them."""
        def record_claude_code(run_folder):
            return {file_name: (text, lambda record: record) for file_name, text in recorded_texts_by_file_name.items()}
        with unittest.mock.patch.dict(record_agent_conversations.RECORDERS_BY_FOLDER_NAME,
                                      {"claude_code": ("claude", record_claude_code)}), \
                unittest.mock.patch.object(record_agent_conversations, "list_personal_texts", return_value=set()):
            return record_agent_conversations.record_and_save("claude_code", self.output_folder)

    def read_saved_texts_by_file_name(self):
        """Returns {the name of each file in the recording's folder: its text}."""
        saved_texts_by_file_name = {}
        for file_name in os.listdir(self.output_folder):
            with open(os.path.join(self.output_folder, file_name)) as saved_file:
                saved_texts_by_file_name[file_name] = saved_file.read()
        return saved_texts_by_file_name

    def test_a_recording_is_saved_in_place_of_the_whole_one_its_folder_held(self):
        self.assertTrue(self.record_and_save({"transcript.jsonl": self.TRANSCRIPT_TEXT, "hook_payloads.jsonl": self.HOOK_PAYLOADS_TEXT}))
        self.assertEqual(self.read_saved_texts_by_file_name(),
                         {"transcript.jsonl": self.TRANSCRIPT_TEXT, "hook_payloads.jsonl": self.HOOK_PAYLOADS_TEXT})
        self.assertTrue(self.record_and_save({"transcript.jsonl": '{"type":"assistant"}\n'}))
        self.assertEqual(self.read_saved_texts_by_file_name(), {"transcript.jsonl": '{"type":"assistant"}\n'})
        self.assertEqual(os.listdir(self.claude_code_recordings_folder), ["2.1.327"])  # not the folder it wrote them in

    def test_a_recording_with_something_personal_left_in_one_of_its_files_is_not_saved_and_what_is_left_is_printed(self):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertFalse(self.record_and_save({"transcript.jsonl": '{"type":"user","content":"Signed in as someone@example.com"}\n',
                                                   "hook_payloads.jsonl": self.HOOK_PAYLOADS_TEXT}))
        self.assertEqual(output.getvalue(), "claude_code: not saved, since these are still in it: "
                                            "{'transcript.jsonl': ['someone@example.com'], 'hook_payloads.jsonl': []}\n")
        self.assertEqual(os.listdir(self.claude_code_recordings_folder), [])

    def test_a_save_cut_short_after_its_first_file_leaves_the_version_without_a_folder_or_with_the_recording_it_had(self):
        # The second file can't be written: the folder its name puts it in is missing.
        files_the_second_of_which_is_not_saved = {"transcript.jsonl": '{"type":"assistant"}\n',
                                                  "missing-folder/hook_payloads.jsonl": self.HOOK_PAYLOADS_TEXT}
        with self.assertRaises(FileNotFoundError):
            self.record_and_save(files_the_second_of_which_is_not_saved)
        self.assertEqual(os.listdir(self.claude_code_recordings_folder), [])
        self.assertTrue(self.record_and_save({"transcript.jsonl": self.TRANSCRIPT_TEXT}))
        with self.assertRaises(FileNotFoundError):
            self.record_and_save(files_the_second_of_which_is_not_saved)
        self.assertEqual(os.listdir(self.claude_code_recordings_folder), ["2.1.327"])
        self.assertEqual(self.read_saved_texts_by_file_name(), {"transcript.jsonl": self.TRANSCRIPT_TEXT})


class ExitStatusTests(unittest.TestCase):
    def run_main(self, command_line_arguments, folder_names_left_out=(), folder_names_with_something_personal_left=()):
        """Returns (main's exit status, the folder names it recorded), with stand-ins for the two
        functions that would run an agent tool: the one that finds a recording's folder, which
        leaves out folder_names_left_out, and the one that records and saves, which saves all but
        folder_names_with_something_personal_left."""
        recorded_folder_names = []

        def find_folder_for_a_new_recording(folder_name, replace_the_recording_of_this_version):
            return (None, "it isn't installed") if folder_name in folder_names_left_out else (f"/recordings/{folder_name}", None)

        def record_and_save(folder_name, output_folder):
            recorded_folder_names.append(folder_name)
            return folder_name not in folder_names_with_something_personal_left
        with unittest.mock.patch.object(sys, "argv", ["record_agent_conversations.py"] + command_line_arguments), \
                unittest.mock.patch.object(record_agent_conversations, "find_folder_for_a_new_recording",
                                           find_folder_for_a_new_recording), \
                unittest.mock.patch.object(record_agent_conversations, "record_and_save", record_and_save), \
                contextlib.redirect_stdout(io.StringIO()):
            return record_agent_conversations.main(), recorded_folder_names

    def test_a_recording_not_saved_fails_the_run_once_the_other_agent_tools_are_recorded_too(self):
        self.assertEqual(self.run_main([], folder_names_with_something_personal_left={"claude_code"}),
                         (1, ["claude_code", "codex", "agy"]))

    def test_agent_tools_whose_recordings_are_saved_or_left_out_end_the_run_successfully(self):
        self.assertEqual(self.run_main(["codex", "agy"], folder_names_left_out={"codex"}), (0, ["agy"]))

    def test_a_name_that_is_not_an_agent_tools_ends_the_run_before_any_agent_tool_is_recorded(self):
        # "claude" is the program's name: its recordings, and so its name here, are "claude_code".
        with unittest.mock.patch.object(sys, "argv", ["record_agent_conversations.py", "codex", "claude"]), \
                unittest.mock.patch.object(record_agent_conversations, "find_folder_for_a_new_recording",
                                           return_value=("/recordings/codex", None)) as find_folder_for_a_new_recording, \
                unittest.mock.patch.object(record_agent_conversations, "record_and_save", return_value=True) as record_and_save, \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()) as error_output, \
                self.assertRaises(SystemExit) as raised:
            record_agent_conversations.main()
        self.assertEqual(raised.exception.code, 2)
        self.assertIn("unknown agent tool: claude (choose from claude_code, codex, agy)", error_output.getvalue())
        find_folder_for_a_new_recording.assert_not_called()
        record_and_save.assert_not_called()


if __name__ == "__main__":
    unittest.main()
