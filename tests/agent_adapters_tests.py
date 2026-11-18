import asyncio
import glob
import json
import os
import tempfile
import threading
import unittest
import unittest.mock

from agent_command_tab.agent_adapters import agy, claude_code, codex
from agent_command_tab.agent_adapters import (build_built_in_agent_tool_adapters, find_agent_tool_adapter_for_tab,
                                              list_agent_tool_names_whose_conversations_can_be_resumed)
from agent_command_tab.agent_adapters.agy import AgyAdapter, find_agy_conversation_and_transcript
from agent_command_tab.agent_adapters.base import (command_line_is_a_shell_with_no_script_or_command, command_line_runs_a_shell,
                                                   split_command_line_into_program_names)
from agent_command_tab.agent_adapters.claude_code import ClaudeCodeAdapter
from agent_command_tab.agent_adapters.codex import CONVERSATION_FILE_PATTERN, CodexAdapter
from agent_command_tab.agent_adapters.generic import GenericAdapter
from agent_command_tab.agent_process_tree import ProcessInfo


class ResumeCommandTests(unittest.TestCase):  # Save session and Restore sessions
    def test_claude_code_resumes_in_the_conversations_config_folder(self):
        adapter = ClaudeCodeAdapter()
        self.assertEqual(adapter.build_launch_command_to_resume("yc", "abc-123"), "yc --resume abc-123")
        self.assertEqual(adapter.build_launch_command_to_resume("CLAUDE_CONFIG_DIR=~/.claude_work yc", "abc-123"),
                         "yc --resume abc-123")  # a conversation of the default folder
        self.assertEqual(adapter.build_launch_command_to_resume("yc", "abc 123", "/Users/me/.claude_work"),
                         "CLAUDE_CONFIG_DIR=/Users/me/.claude_work yc --resume 'abc 123'")

    def test_codex_resumes_with_its_subcommand(self):
        self.assertEqual(CodexAdapter().build_launch_command_to_resume("/opt/homebrew/bin/codex", "0199-ab"),
                         "/opt/homebrew/bin/codex resume 0199-ab")

    def test_codex_resumes_and_forks_with_the_conversations_codex_home(self):  # Codex finds the conversation only there
        self.assertEqual(CodexAdapter().build_launch_command_to_resume("codex", "0199-ab", None, "/Users/me/Codex Work"),
                         "CODEX_HOME='/Users/me/Codex Work' codex resume 0199-ab")
        self.assertEqual(CodexAdapter().build_launch_command_for_a_copy("codex", "0199-ab", None, "/Users/me/.codex-work"),
                         "CODEX_HOME=/Users/me/.codex-work codex fork 0199-ab")
        # In place of one the launch command sets, which would come later and win.
        self.assertEqual(CodexAdapter().build_launch_command_to_resume("RUST_LOG=info CODEX_HOME=~/.codex-work codex", "0199-ab",
                                                                       None, "/Users/me/.codex"),
                         "CODEX_HOME=/Users/me/.codex RUST_LOG=info codex resume 0199-ab")
        self.assertEqual(CodexAdapter().build_launch_command_to_resume("CODEX_HOME=~/.codex-work codex", "0199-ab"),
                         "CODEX_HOME=~/.codex-work codex resume 0199-ab")  # not known (saved before it was kept)

    def test_agy_resumes_with_its_conversation_option(self):
        self.assertEqual(AgyAdapter().build_launch_command_to_resume("agy --model gemini-4-carbon-high", "5bb9d1c8-3df7"),
                         "agy --model gemini-4-carbon-high --conversation 5bb9d1c8-3df7")

    def test_only_claude_code_codex_and_agy_can_resume(self):
        self.assertEqual(list_agent_tool_names_whose_conversations_can_be_resumed(), {"Claude Code", "Codex", "agy"})


class DuplicateLaunchCommandTests(unittest.TestCase):
    def test_claude_code_forks_the_conversation(self):
        self.assertEqual(ClaudeCodeAdapter().build_launch_command_for_a_copy("/Users/me/.local/bin/claude", "abc-123"),
                         "/Users/me/.local/bin/claude --resume abc-123 --fork-session")

    def test_claude_code_copy_uses_the_originals_config_folder_when_it_isnt_the_default(self):
        self.assertEqual(ClaudeCodeAdapter().build_launch_command_for_a_copy(
            "yc", "abc-123", {"sessionId": "abc-123", "config_folder": "/Users/me/.claude_work"}),
            "CLAUDE_CONFIG_DIR=/Users/me/.claude_work yc --resume abc-123 --fork-session")
        self.assertEqual(ClaudeCodeAdapter().build_launch_command_for_a_copy("yc", "abc-123", {"config_folder": None}),
                         "yc --resume abc-123 --fork-session")

    def test_a_config_folder_set_in_the_launch_command_gives_way_to_the_originals(self):
        launch_command = "ANTHROPIC_MODEL=x CLAUDE_CONFIG_DIR=~/.claude_work claude"  # the shell applies the last setting
        self.assertEqual(ClaudeCodeAdapter().build_launch_command_for_a_copy(
            launch_command, "abc-123", {"config_folder": "/Users/me/.claude_personal"}),
            "CLAUDE_CONFIG_DIR=/Users/me/.claude_personal ANTHROPIC_MODEL=x claude --resume abc-123 --fork-session")
        self.assertEqual(ClaudeCodeAdapter().build_launch_command_for_a_copy(launch_command, "abc-123", {"config_folder": None}),
                         "ANTHROPIC_MODEL=x claude --resume abc-123 --fork-session")
        self.assertEqual(ClaudeCodeAdapter().build_launch_command_for_a_copy(
            "CLAUDE_CONFIG_DIR=\"$HOME/my claude\" claude --verbose", "abc-123", {"config_folder": None}),
            "claude --verbose --resume abc-123 --fork-session")

    def test_codex_forks_the_conversation(self):
        self.assertEqual(CodexAdapter().build_launch_command_for_a_copy("codex", "abc-123"), "codex fork abc-123")

    def test_without_a_conversation_id_or_for_other_tools_the_copy_starts_fresh(self):
        self.assertEqual(ClaudeCodeAdapter().build_launch_command_for_a_copy("claude", None), "claude")
        self.assertEqual(GenericAdapter().build_launch_command_for_a_copy("aider", "abc"), "aider")
        self.assertEqual(AgyAdapter().build_launch_command_for_a_copy("agy", "5bb9d1c8-3df7"), "agy")  # agy can't fork one


class AgyConversationLookupTests(unittest.TestCase):
    CONVERSATION_ID = "5bb9d1c8-3df7-4e4d-b96d-0155db6b8a5a"
    OTHER_CONVERSATION_ID = "1b5f3f86-1b68-4a7d-aa5c-81e46434075e"

    def setUp(self):
        self.agy_folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.agy_folder.cleanup)

    def build_open_file_paths(self, *conversation_ids):
        """Files an agy process has open (agy 1.5.10): after a /new, the earlier conversation's
        folders and database stay open, but only the current one's presence lock does."""
        paths = [os.path.join(self.agy_folder.name, "log", "cli-20261117_103516.log")]
        for conversation_id in (self.OTHER_CONVERSATION_ID, *conversation_ids):
            paths += [os.path.join(self.agy_folder.name, "brain", conversation_id),
                      os.path.join(self.agy_folder.name, "conversations", conversation_id + ".db")]
        return paths + [os.path.join(self.agy_folder.name, "presence", conversation_id + ".lock")
                        for conversation_id in conversation_ids]

    def write_transcript(self, conversation_id):
        # Where agy itself writes it, spelled out rather than built by the code the lookup uses.
        transcript_path = os.path.join(self.agy_folder.name, "brain", conversation_id, ".system_generated", "logs", "transcript.jsonl")
        os.makedirs(os.path.dirname(transcript_path))
        open(transcript_path, "w").close()
        return transcript_path

    def test_the_presence_lock_names_the_conversation_whose_transcript_comes_with_its_first_message(self):
        open_file_paths = self.build_open_file_paths(self.CONVERSATION_ID)
        self.assertEqual(find_agy_conversation_and_transcript(open_file_paths), (self.CONVERSATION_ID, None))
        transcript_path = self.write_transcript(self.CONVERSATION_ID)
        self.assertEqual(find_agy_conversation_and_transcript(open_file_paths), (self.CONVERSATION_ID, transcript_path))

    def test_the_whole_transcript_is_the_one_read_once_agy_wrote_it_beside_the_one_with_long_texts_cut_short(self):
        transcript_path = self.write_transcript(self.CONVERSATION_ID)
        whole_transcript_path = os.path.join(os.path.dirname(transcript_path), "transcript_full.jsonl")
        open(whole_transcript_path, "w").close()
        self.assertEqual(find_agy_conversation_and_transcript(self.build_open_file_paths(self.CONVERSATION_ID)),
                         (self.CONVERSATION_ID, whole_transcript_path))

    def test_no_lock_is_no_conversation_yet_and_files_that_cant_be_read_or_two_locks_tell_nothing(self):
        self.assertEqual(find_agy_conversation_and_transcript(self.build_open_file_paths()), (None, None))
        self.assertIsNone(find_agy_conversation_and_transcript(None))
        self.assertIsNone(find_agy_conversation_and_transcript(
            self.build_open_file_paths(self.CONVERSATION_ID, "0054ada5-9370-4f9d-9b01-3cd280ddea3b")))

    def test_the_adapter_asks_the_agy_process_for_its_open_files(self):
        adapter = AgyAdapter()

        def ask_the_adapter(open_file_paths):
            with unittest.mock.patch.object(agy, "list_the_files_a_process_has_open", return_value=(
                    None if open_file_paths is None else dict(enumerate(open_file_paths)))):
                return (asyncio.run(adapter.find_conversation_id(4242, None)),
                        asyncio.run(adapter.find_conversation_file(4242, None, [])),
                        asyncio.run(adapter.find_whether_no_message_was_sent_in_its_conversation(4242)))
        self.assertEqual(ask_the_adapter(self.build_open_file_paths()), (None, None, True))  # a fresh agy
        open_file_paths = self.build_open_file_paths(self.CONVERSATION_ID)
        self.assertEqual(ask_the_adapter(open_file_paths), (self.CONVERSATION_ID, None, True))  # after /new
        transcript_path = self.write_transcript(self.CONVERSATION_ID)
        self.assertEqual(ask_the_adapter(open_file_paths), (self.CONVERSATION_ID, transcript_path, False))
        self.assertEqual(ask_the_adapter(None), (None, None, None))
        self.assertIsNone(asyncio.run(adapter.find_whether_no_message_was_sent_in_its_conversation(None)))

    def test_the_adapter_looks_for_the_transcript_on_disk_in_a_worker_thread(self):
        # The lookup runs on the core's event loop, which a slow disk must not hold up.
        ids_of_the_threads_that_looked_on_disk = []
        is_file = os.path.isfile

        def note_the_thread_and_look(path):
            ids_of_the_threads_that_looked_on_disk.append(threading.get_ident())
            return is_file(path)

        async def ask_the_adapter_and_return_the_event_loops_thread_id():
            await AgyAdapter().find_conversation_file(4242, None, [])
            return threading.get_ident()
        with unittest.mock.patch.object(agy, "list_the_files_a_process_has_open",
                                        return_value=dict(enumerate(self.build_open_file_paths(self.CONVERSATION_ID)))), \
                unittest.mock.patch.object(agy.os.path, "isfile", note_the_thread_and_look):
            event_loop_thread_id = asyncio.run(ask_the_adapter_and_return_the_event_loops_thread_id())
        self.assertTrue(ids_of_the_threads_that_looked_on_disk)
        self.assertNotIn(event_loop_thread_id, ids_of_the_threads_that_looked_on_disk)


class CodexConversationLookupTests(unittest.TestCase):
    def test_codex_conversation_file_names(self):
        path = "/Users/me/.codex/sessions/2026/10/18/rollout-2026-10-18T15-04-05-0199e1b2-7c3d-7a10-9f00-1234567890ab.jsonl"
        self.assertEqual(CONVERSATION_FILE_PATTERN.search(path).group(1), "0199e1b2-7c3d-7a10-9f00-1234567890ab")
        self.assertIsNone(CONVERSATION_FILE_PATTERN.search("/Users/me/.codex/config.toml"))

    def test_a_codex_conversation_is_looked_for_in_the_codex_home_of_the_agents_own_environment(self):
        # Its launch command may set another CODEX_HOME than the app's ("CODEX_HOME=/x codex").
        sessions_folders = []

        def find_conversation_files(sessions_folder, *arguments, **keyword_arguments):
            sessions_folders.append(sessions_folder)
            return []
        for arguments_and_environment, expected_sessions_folder in (
                ((["codex"], {"CODEX_HOME": "/x"}), "/x/sessions"),
                ((["codex"], {}), os.path.expanduser("~/.codex/sessions")),  # the agent has none: Codex's default
                (([], {}), "/app-codex-home/sessions")):  # its environment can't be read: the app's own
            with unittest.mock.patch.object(codex, "read_process_info", return_value=ProcessInfo(301, 1, "codex", 10.0)), \
                    unittest.mock.patch.object(codex, "read_process_arguments_and_environment",
                                               return_value=arguments_and_environment) as read_arguments_and_environment, \
                    unittest.mock.patch.object(codex, "read_process_current_folder", return_value="/p"), \
                    unittest.mock.patch.object(codex, "find_codex_conversation_files", find_conversation_files), \
                    unittest.mock.patch.dict(os.environ, {"CODEX_HOME": "/app-codex-home"}):
                CodexAdapter().find_conversation_files(301)
            self.assertEqual(sessions_folders[-1], expected_sessions_folder, arguments_and_environment)
            read_arguments_and_environment.assert_called_once_with(301)  # one reading tells its home and whether it resumed

    def test_the_conversation_a_codex_process_was_started_to_resume_is_looked_for_by_its_id_and_only_once(self):
        # As Restore sessions starts it. Listing every conversation file Codex has kept takes longer the more there are.
        conversation_id = "01a151f0-8f89-7711-a0cc-cfcae10915f8"
        with tempfile.TemporaryDirectory() as codex_home_folder:
            day_folder = os.path.join(codex_home_folder, "sessions", "2026", "10", "18")
            os.makedirs(day_folder)
            resumed_file = os.path.join(day_folder, f"rollout-2026-10-18T15-04-05-{conversation_id}.jsonl")
            with open(resumed_file, "w") as conversation_file:
                json.dump({"type": "session_meta", "payload": {"cwd": "/p", "timestamp": "2026-10-18T15:04:05Z", "source": "cli",
                                                               "originator": "codex-tui"}}, conversation_file)
            adapter = CodexAdapter()
            with unittest.mock.patch.object(codex, "read_process_info",
                                            return_value=ProcessInfo(301, 1, "codex", os.path.getmtime(resumed_file) - 60)), \
                    unittest.mock.patch.object(codex, "read_process_arguments_and_environment",
                                               return_value=(["codex", "resume", conversation_id], {"CODEX_HOME": codex_home_folder})), \
                    unittest.mock.patch.object(codex, "read_process_current_folder", return_value="/p"), \
                    unittest.mock.patch.object(codex, "find_codex_conversation_file_by_id",
                                               wraps=codex.find_codex_conversation_file_by_id) as find_conversation_file_by_id, \
                    unittest.mock.patch.object(glob, "glob", wraps=glob.glob) as list_files:
                self.assertEqual(adapter.find_conversation_files(301), [resumed_file])
                self.assertEqual(adapter.find_conversation_files(301), [resumed_file])
            find_conversation_file_by_id.assert_called_once()
            self.assertNotIn(os.path.join(codex_home_folder, "sessions", "*", "*", "*", "rollout-*.jsonl"),
                             [call.args[0] for call in list_files.call_args_list])  # not every file of every day folder


class ClaudeCodeTranscriptLookupTests(unittest.TestCase):
    def test_the_records_own_config_folder_is_searched_even_if_no_list_has_it(self):
        with tempfile.TemporaryDirectory() as folder:
            new_config_folder = os.path.join(folder, ".claude_new")  # created after the app made its list of folders
            transcript_path = os.path.join(new_config_folder, "projects", "-Users-me-code", "abc-123.jsonl")
            os.makedirs(os.path.dirname(transcript_path))
            open(transcript_path, "w").close()
            record = {"sessionId": "abc-123", "config_folder": new_config_folder}
            self.assertEqual(asyncio.run(ClaudeCodeAdapter().find_conversation_file(4242, record, [])), transcript_path)


class ClaudeConfigFoldersTests(unittest.TestCase):
    def test_the_config_folder_the_claude_code_launch_command_sets_is_listed_wherever_it_is(self):
        # Its sessions are in no ~/.claude* folder, so `claude agents --json` and the hooks installer would miss them.
        with tempfile.TemporaryDirectory() as home_folder:
            home_folder = os.path.realpath(home_folder)
            settings_file_path = os.path.join(home_folder, "settings.json")
            config_folder = os.path.join(home_folder, "my claude")
            os.mkdir(config_folder)
            os.symlink(config_folder, os.path.join(home_folder, "link-to-my-claude"))
            # The default folder of this home: the adapter worked out the real home's when it was imported.
            default_config_folder = os.path.join(home_folder, ".claude")
            os.mkdir(default_config_folder)
            for launch_command, dashboards_own_config_folder, expected_folders in (
                    ("claude", "", [None]),
                    ('ANTHROPIC_MODEL=x CLAUDE_CONFIG_DIR="$HOME/my claude" claude --verbose', "", [None, config_folder]),
                    ("CLAUDE_CONFIG_DIR=~/link-to-my-claude claude", config_folder, [None, config_folder]),  # listed once
                    ("CLAUDE_CONFIG_DIR=~/.claude claude", "", [None]),  # the default, listed as None
                    ("CLAUDE_CONFIG_DIR=~/not-made-yet claude", "", [None])):
                with open(settings_file_path, "w") as settings_file:
                    json.dump({"agent_tools": [{"agent_tool_name": "Claude Code", "launch_command": launch_command}]}, settings_file)
                with unittest.mock.patch.dict(os.environ, {"HOME": home_folder, "CLAUDE_CONFIG_DIR": dashboards_own_config_folder}), \
                        unittest.mock.patch.object(claude_code, "DEFAULT_CLAUDE_CONFIG_FOLDER", default_config_folder):
                    self.assertEqual(claude_code.list_claude_config_folders(settings_file_path), expected_folders, launch_command)


class SplitCommandLineTests(unittest.TestCase):
    def test_login_shell_dash_is_removed(self):
        self.assertEqual(split_command_line_into_program_names("-bash --posix"), ["bash", "posix"])

    def test_full_paths_become_base_names(self):
        self.assertEqual(split_command_line_into_program_names("/opt/homebrew/bin/node /opt/homebrew/bin/codex -a never")[:2],
                         ["node", "codex"])

    def test_unbalanced_quotes_fall_back_to_spaces(self):
        self.assertEqual(split_command_line_into_program_names("claude 'unbalanced")[0], "claude")

    def test_empty_and_none(self):
        self.assertEqual(split_command_line_into_program_names(""), [])
        self.assertEqual(split_command_line_into_program_names(None), [])

    def test_shell_detection(self):
        self.assertTrue(command_line_runs_a_shell("-zsh"))
        self.assertTrue(command_line_runs_a_shell("/bin/bash -l"))
        self.assertFalse(command_line_runs_a_shell("vim notes.txt"))

    def test_a_shell_with_no_script_or_command_is_told_apart_from_one_running_something(self):
        for command_line in ("-zsh", "/bin/bash", "/bin/zsh -l", "bash --login", "zsh -il", "fish --interactive"):
            with self.subTest(command_line=command_line):
                self.assertTrue(command_line_is_a_shell_with_no_script_or_command(command_line))
        for command_line in ("/bin/bash ./job.sh", "zsh -c 'make all'", "bash -lc 'sleep 60'", "bash -l job.sh",
                             "sh -s", "bash -o vi", "vim notes.txt", "", None):
            with self.subTest(command_line=command_line):
                self.assertFalse(command_line_is_a_shell_with_no_script_or_command(command_line))


class FindAgentToolAdapterTests(unittest.TestCase):
    def setUp(self):
        self.specific_adapters, self.generic_adapter = build_built_in_agent_tool_adapters()

    def find_tool_name(self, command_line, status=None):
        adapter = find_agent_tool_adapter_for_tab(command_line, status, self.specific_adapters, self.generic_adapter)
        return adapter.name_agent_tool_for_command_line(command_line) if adapter else None

    def test_claude_code(self):
        self.assertEqual(self.find_tool_name("claude --dangerously-skip-permissions"), "Claude Code")
        self.assertEqual(self.find_tool_name("/Users/someone/.local/bin/claude"), "Claude Code")

    def test_codex_both_forms(self):
        self.assertEqual(self.find_tool_name("codex --dangerously-bypass-hook-trust -a on-request"), "Codex")
        self.assertEqual(self.find_tool_name("node /opt/homebrew/bin/codex -c 'model=\"o3\"'"), "Codex")

    def test_agy_also_before_it_reports_a_status(self):
        self.assertEqual(self.find_tool_name("agy"), "agy")
        self.assertEqual(self.find_tool_name("/Users/someone/.local/bin/agy --conversation 5bb9d1c8-3df7"), "agy")
        self.assertIsNone(self.find_tool_name("agyle --help"))

    def test_node_running_something_else_is_not_codex(self):
        self.assertIsNone(self.find_tool_name("node server.js"))

    def test_shell_is_never_an_agent_even_with_a_status(self):
        self.assertIsNone(self.find_tool_name("-bash --posix", status="working"))

    def test_unknown_tool_needs_a_recognized_status(self):
        self.assertIsNone(self.find_tool_name("aider --model x"))
        self.assertIsNone(self.find_tool_name("aider --model x", status="Compiling"))
        self.assertEqual(self.find_tool_name("aider --model x", status="Working"), "aider")


if __name__ == "__main__":
    unittest.main()
