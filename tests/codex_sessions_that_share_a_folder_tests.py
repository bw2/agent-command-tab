"""Codex agent sessions that share a folder (their working folder, with the same Codex home
folder): their conversation files, matched to a process by
folder and start time only, can't be told apart (DashboardCore.codex_conversation_may_be_another_sessions),
also once one of them has ended, and after the dashboard restarted
(remember_codex_sessions_that_share_a_folder, codex_processes_that_shared_their_folder_store)."""

import asyncio
import json
import os
import shutil
import tempfile
import unittest
import unittest.mock

from agent_command_tab import dashboard_core
from agent_command_tab.agent_adapters.codex import CodexAdapter
from agent_command_tab.agent_process_tree import ProcessInfo
from agent_command_tab.codex_processes_that_shared_their_folder_store import (load_codex_processes_that_shared_their_folder,
                                                                              save_codex_processes_that_shared_their_folder)
from agent_command_tab.dashboard_core import DashboardCore
from agent_command_tab.dashboard_core_api import ActionRequest
from agent_command_tab.snapshot_builder import turns_of_the_agent_run_outside_its_tab
from gathered_window_builders import make_agent_session
from tab_duplicating import find_launch_command_of_a_duplicate_of_the_tab_of_s1

# A conversation Codex really wrote, with the prompt "... merge the VCF files ..." and 23,304 context tokens.
RECORDED_CODEX_ROLLOUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "recorded_agent_files", "codex", "0.169.0",
                                      "rollout.jsonl")
CONVERSATION_ID = "01a1ed0b-56b8-7e70-827b-aa432bf4d531"  # the one its first record gives, which Codex names its file after
TEXT_IN_THE_CONVERSATION = "merge the VCF files"


class CodexAdapterThatFindsOneConversationFile(CodexAdapter):
    """Finds one file as the conversation of every Codex process: its own for a session alone in
    its folder, and for a session with no file yet, that of a later session in its folder."""

    def __init__(self, conversation_file):
        super().__init__()
        self.conversation_file = conversation_file
        self.turns_run_in_the_shared_server = False
        # Run while a file is read for where its turns run, as a snapshot published meanwhile would.
        self.while_reading_where_the_turns_run = None

    def find_conversation_files(self, agent_process_id):
        return [self.conversation_file]

    def find_codex_home_folder_of_process(self, agent_process_id):
        return "/Users/me/.codex"

    def read_turns_run_outside_the_tab(self, conversation_file_path, agent_process_id):
        if self.while_reading_where_the_turns_run is not None:
            self.while_reading_where_the_turns_run()
        return self.turns_run_in_the_shared_server


class CodexSessionsInOneFolderTestCase(unittest.TestCase):
    """A core with one Codex session, s1 (process 4242), in /p; process 4343 is a later Codex
    session in /p once a test lists it as s2 (list_the_later_session). The processes of
    started_at_by_process_id run, and no others. Each one's environment sets CODEX_HOME to
    /Users/me/.codex, or to what codex_home_folder_by_process_id gives for it (None: its
    environment can't be read)."""

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.conversation_file = os.path.join(self.folder.name, f"rollout-2026-11-18T05-05-06-{CONVERSATION_ID}.jsonl")
        shutil.copy(RECORDED_CODEX_ROLLOUT, self.conversation_file)
        self.processes_file_path = os.path.join(self.folder.name, "codex_processes_that_shared_their_folder.json")
        self.adapter = CodexAdapterThatFindsOneConversationFile(self.conversation_file)
        self.started_at_by_process_id = {4242: 1000.25, 4343: 2000.5}
        self.codex_home_folder_by_process_id = {}

        def read_process_arguments_and_environment(process_id):
            codex_home_folder = self.codex_home_folder_by_process_id.get(process_id, "/Users/me/.codex")
            return (["codex"], {"CODEX_HOME": codex_home_folder}) if codex_home_folder else ([], {})
        for name, replacement in (
                ("read_process_info", lambda process_id: (ProcessInfo(process_id, 1, "codex", self.started_at_by_process_id[process_id])
                                                          if process_id in self.started_at_by_process_id else None)),
                ("read_process_arguments_and_environment", read_process_arguments_and_environment),
                ("process_is_alive", lambda process_id: process_id in self.started_at_by_process_id)):
            patch = unittest.mock.patch.object(dashboard_core, name, replacement)
            patch.start()
            self.addCleanup(patch.stop)
        self.core = self.make_core()
        self.core.agent_sessions = {"s1": self.make_codex_session(4242)}

    def make_core(self):
        """Returns a core that keeps the processes that shared their folder in the test's file, as the app's does."""
        return DashboardCore(lambda snapshot: None, lambda message: None,
                             codex_processes_that_shared_their_folder_file_path=self.processes_file_path)

    def make_codex_session(self, process_id):
        return make_agent_session(process_id, self.adapter, "Codex", working_folder="/p",
                                  started_at=self.started_at_by_process_id[process_id])

    def list_the_later_session(self):
        """What the snapshot that first lists the later session in the same folder does."""
        self.core.agent_sessions["s2"] = self.make_codex_session(4343)
        self.core.remember_codex_sessions_that_share_a_folder()

    def read_the_conversation_file(self):
        asyncio.run(self.core.update_conversation_file_readings("s1", self.core.agent_sessions["s1"]))

    def find_sessions_whose_conversation_holds_the_text(self):
        return asyncio.run(self.core.perform_action(ActionRequest(
            "find_agent_sessions_whose_conversations_contain", None, {"text": TEXT_IN_THE_CONVERSATION}))).details["session_ids"]


class ConversationOfASessionThatComesToShareItsFolderTests(CodexSessionsInOneFolderTestCase):
    def test_its_conversation_leaves_its_row_and_the_search_at_once(self):
        self.read_the_conversation_file()
        self.assertEqual(self.find_sessions_whose_conversation_holds_the_text(), ["s1"])
        self.assertEqual(self.core.session_readings_by_session_id["s1"]["context_tokens"], 23304)
        search_text_revision_before = self.core.conversation_search_text_revision
        self.list_the_later_session()
        # Not at the file's next lookup, up to a minute later.
        self.assertEqual(self.find_sessions_whose_conversation_holds_the_text(), [])
        self.assertIsNone(self.core.session_readings_by_session_id["s1"]["context_tokens"])
        self.assertEqual(self.core.conversation_search_text_revision, search_text_revision_before + 1)  # the list searches again
        self.read_the_conversation_file()  # looks the file up again at once, and finds none it can tell is its own
        self.assertIsNone(self.core.conversation_files_by_session_id["s1"]["path"])
        self.assertEqual(self.find_sessions_whose_conversation_holds_the_text(), [])

    def test_a_read_under_way_when_the_later_session_is_listed_is_dropped(self):
        def list_the_later_session_once():
            self.adapter.while_reading_where_the_turns_run = None
            self.list_the_later_session()
        self.adapter.while_reading_where_the_turns_run = list_the_later_session_once
        self.read_the_conversation_file()
        self.assertEqual(self.find_sessions_whose_conversation_holds_the_text(), [])
        self.assertIsNone(self.core.session_readings_by_session_id["s1"].get("context_tokens"))

    def test_a_lookup_under_way_when_the_later_session_starts_finds_no_file(self):
        def find_the_file_while_the_later_session_starts(agent_process_id):
            self.core.agent_sessions["s2"] = self.make_codex_session(4343)
            return [self.conversation_file]
        self.adapter.find_conversation_files = find_the_file_while_the_later_session_starts
        self.read_the_conversation_file()
        self.assertIsNone(self.core.conversation_files_by_session_id["s1"]["path"])
        self.assertEqual(self.find_sessions_whose_conversation_holds_the_text(), [])

    def test_a_duplicate_doesnt_fork_a_conversation_when_the_later_session_is_listed_during_one_of_its_lookups(self):
        for lookup_during_which_the_later_session_is_listed in (1, 2):  # of the conversation's ID, then of its file
            self.core.agent_sessions = {"s1": self.make_codex_session(4242)}
            lookups_made = []

            def find_the_file_while_the_later_session_is_listed(agent_process_id):
                lookups_made.append(agent_process_id)
                if len(lookups_made) == lookup_during_which_the_later_session_is_listed:
                    self.core.agent_sessions["s2"] = self.make_codex_session(4343)
                return [self.conversation_file]
            self.adapter.find_conversation_files = find_the_file_while_the_later_session_is_listed
            # The one file that fits may be the later session's: the copy starts fresh.
            self.assertEqual((find_launch_command_of_a_duplicate_of_the_tab_of_s1(self.core), len(lookups_made)),
                             ("codex", lookup_during_which_the_later_session_is_listed))


class CodexHomeFoldersOfSessionsInOneFolderTests(CodexSessionsInOneFolderTestCase):
    """Each session's conversation files are looked for only in its own Codex home's sessions
    folder, so two sessions in one working folder with different homes can't take each other's."""

    def list_which_sessions_conversations_may_be_another_sessions(self):
        return [session_id for session_id, agent_session in self.core.agent_sessions.items()
                if self.core.codex_conversation_may_be_another_sessions(session_id, agent_session)]

    def test_a_session_keeps_its_conversation_when_the_later_session_in_its_folder_has_another_codex_home(self):
        self.codex_home_folder_by_process_id[4343] = "/Users/me/.codex-work"
        self.read_the_conversation_file()
        self.list_the_later_session()
        self.assertEqual(self.core.session_readings_by_session_id["s1"]["context_tokens"], 23304)
        self.assertEqual(self.find_sessions_whose_conversation_holds_the_text(), ["s1"])
        self.assertEqual(asyncio.run(self.core.look_up_conversation_file("s1", self.core.agent_sessions["s1"])), self.conversation_file)
        self.assertEqual(self.list_which_sessions_conversations_may_be_another_sessions(), [])
        # Nor is either remembered as having shared its folder, which would last for as long as it runs.
        self.assertEqual(self.core.codex_process_ids_that_shared_their_folder, set())
        self.assertFalse(os.path.exists(self.processes_file_path))

    def test_only_the_sessions_with_the_same_codex_home_share_their_folder(self):
        self.started_at_by_process_id[4444] = 3000.0
        self.codex_home_folder_by_process_id.update({4343: "/Users/me/.codex-work", 4444: "/Users/me/.codex-work/"})  # one folder
        self.core.agent_sessions["s3"] = self.make_codex_session(4444)
        self.assertEqual(self.list_which_sessions_conversations_may_be_another_sessions(), [])  # s1 and s3: two homes
        self.list_the_later_session()
        self.assertEqual(self.list_which_sessions_conversations_may_be_another_sessions(), ["s3", "s2"])
        self.assertEqual(self.core.codex_process_ids_that_shared_their_folder, {4343, 4444})

    def test_a_codex_home_that_isnt_known_counts_as_the_same_as_any_other(self):
        self.codex_home_folder_by_process_id[4242] = "/Users/me/.codex-work"
        # A session whose process ID the terminal hasn't given: nothing tells where it keeps its files.
        self.core.agent_sessions["s3"] = make_agent_session(None, self.adapter, "Codex", working_folder="/p")
        self.assertEqual(self.list_which_sessions_conversations_may_be_another_sessions(), ["s1", "s3"])
        del self.core.agent_sessions["s3"]
        self.assertEqual(self.list_which_sessions_conversations_may_be_another_sessions(), [])
        self.codex_home_folder_by_process_id[4343] = None  # its environment can't be read
        self.list_the_later_session()
        self.assertEqual(self.core.codex_process_ids_that_shared_their_folder, {4242, 4343})


class SharedServerReadingAcrossASnapshotTests(CodexSessionsInOneFolderTestCase):
    """Whether the turns of a session that shares its folder run in Codex's shared server is read
    in a worker thread, and snapshots are published meanwhile."""

    def setUp(self):
        super().setUp()
        self.list_the_later_session()
        self.adapter.turns_run_in_the_shared_server = True
        self.agent_session_the_reading_began_with = self.core.agent_sessions["s1"]

    def read_while(self, what_happens_during_the_read):
        self.adapter.while_reading_where_the_turns_run = what_happens_during_the_read
        asyncio.run(self.core.update_conversation_file_readings("s1", self.agent_session_the_reading_began_with))
        return self.core.session_readings_by_session_id.get("s1", {}).get("process_id_whose_turns_run_outside_the_tab")

    def test_a_snapshot_published_during_the_read_keeps_the_session_unpausable(self):
        # Each snapshot builds the record of the tab's agent anew, for the same process.
        self.assertEqual(self.read_while(lambda: self.core.agent_sessions.update(s1=dict(self.agent_session_the_reading_began_with))),
                         4242)
        self.assertTrue(turns_of_the_agent_run_outside_its_tab(self.core.session_readings_by_session_id["s1"], 4242))

    def test_a_reading_of_an_agent_that_another_replaced_in_the_tab_meanwhile_is_dropped(self):
        self.started_at_by_process_id[4444] = 3000.0
        self.assertIsNone(self.read_while(lambda: self.core.agent_sessions.update(s1=self.make_codex_session(4444))))


class ProcessesThatSharedTheirFolderAcrossARestartTests(CodexSessionsInOneFolderTestCase):
    def close_the_later_sessions_tab(self):
        """The later session's tab closes and its process ends; its conversation file stays."""
        del self.core.agent_sessions["s2"]
        del self.started_at_by_process_id[4343]
        self.core.remember_codex_sessions_that_share_a_folder()

    def look_up_the_conversation_file_and_duplicate(self, core):
        """Returns (the conversation file a lookup finds for s1, the launch command a Duplicate of its tab would run)."""
        return (asyncio.run(core.look_up_conversation_file("s1", core.agent_sessions["s1"])),
                find_launch_command_of_a_duplicate_of_the_tab_of_s1(core))

    def test_a_conversation_that_may_be_a_later_neighbors_is_neither_shown_nor_forked_also_after_it_closed_and_a_restart(self):
        self.assertEqual(self.look_up_the_conversation_file_and_duplicate(self.core),  # alone: its own
                         (self.conversation_file, f"CODEX_HOME=/Users/me/.codex codex fork {CONVERSATION_ID}"))
        # The later session writes the one file that fits both, while this one has none yet.
        self.list_the_later_session()
        self.assertEqual(self.look_up_the_conversation_file_and_duplicate(self.core), (None, "codex"))
        self.close_the_later_sessions_tab()
        self.assertEqual(self.look_up_the_conversation_file_and_duplicate(self.core), (None, "codex"))
        self.assertEqual(load_codex_processes_that_shared_their_folder(self.processes_file_path), {4242: 1000.25})
        restarted_core = self.make_core()
        # Its first snapshot may come before the tab's terminal is gathered: the process still runs, so it isn't forgotten.
        restarted_core.remember_codex_sessions_that_share_a_folder()
        restarted_core.agent_sessions = {"s1": self.make_codex_session(4242)}
        restarted_core.remember_codex_sessions_that_share_a_folder()
        # No file and no fork: the one file that fits may be the closed session's.
        self.assertEqual(self.look_up_the_conversation_file_and_duplicate(restarted_core), (None, "codex"))

    def test_a_process_that_ended_leaves_the_file_and_a_later_one_with_its_id_isnt_taken_for_it(self):
        self.list_the_later_session()
        self.assertEqual(load_codex_processes_that_shared_their_folder(self.processes_file_path), {4242: 1000.25, 4343: 2000.5})
        self.close_the_later_sessions_tab()
        self.assertEqual(load_codex_processes_that_shared_their_folder(self.processes_file_path), {4242: 1000.25})
        self.started_at_by_process_id = {4242: 3000.0}  # the app restarts after 4242 ended, and another process got its ID
        self.assertEqual(self.make_core().codex_process_ids_that_shared_their_folder, set())

    def test_dump_snapshot_starts_from_the_saved_processes_without_writing_the_file(self):
        self.list_the_later_session()
        core_of_dump_snapshot = DashboardCore(
            lambda snapshot: None, lambda message: None,
            started_at_by_codex_process_id_that_shared_its_folder_to_start_with=load_codex_processes_that_shared_their_folder(
                self.processes_file_path))
        self.assertEqual(core_of_dump_snapshot.codex_process_ids_that_shared_their_folder, {4242, 4343})
        with unittest.mock.patch.object(dashboard_core, "save_codex_processes_that_shared_their_folder") as save:  # to any file
            core_of_dump_snapshot.remember_codex_sessions_that_share_a_folder()
            del self.started_at_by_process_id[4343]
            core_of_dump_snapshot.remember_codex_sessions_that_share_a_folder()
        self.assertEqual(core_of_dump_snapshot.codex_process_ids_that_shared_their_folder, {4242})
        save.assert_not_called()

    def test_a_file_that_cant_be_written_is_reported_rather_than_ending_the_core(self):
        not_a_folder = os.path.join(self.folder.name, "a file")
        with open(not_a_folder, "w"):
            pass
        self.processes_file_path = os.path.join(not_a_folder, "codex_processes_that_shared_their_folder.json")
        self.core = self.make_core()
        self.core.agent_sessions = {"s1": self.make_codex_session(4242)}
        with unittest.mock.patch("builtins.print") as print_mock:
            self.list_the_later_session()
        self.assertIn("could not save the Codex processes that shared their folder", print_mock.call_args.args[0])
        self.assertEqual(self.core.codex_process_ids_that_shared_their_folder, {4242, 4343})


class CodexProcessesThatSharedTheirFolderFileTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.file_path = os.path.join(self.folder.name, "nested", "codex_processes_that_shared_their_folder.json")

    def test_saved_processes_load_back(self):
        save_codex_processes_that_shared_their_folder(self.file_path, {4343: 2000.5, 4242: 1000.25})
        self.assertEqual(load_codex_processes_that_shared_their_folder(self.file_path), {4242: 1000.25, 4343: 2000.5})

    def test_a_missing_unreadable_or_malformed_file_gives_no_processes(self):
        self.assertEqual(load_codex_processes_that_shared_their_folder(self.file_path), {})
        os.makedirs(os.path.dirname(self.file_path))
        for content in ("{not json", json.dumps({"process_id": 4242, "started_at": 1000.25})):  # the second: not a list
            with open(self.file_path, "w") as file:
                file.write(content)
            self.assertEqual(load_codex_processes_that_shared_their_folder(self.file_path), {}, content)
        with open(self.file_path, "w") as file:
            json.dump([{"process_id": 4242, "started_at": 1000.25}, {"process_id": "4343", "started_at": 2000.5},
                       {"process_id": True, "started_at": 1.0}, {"process_id": 4444}, "x"], file)
        self.assertEqual(load_codex_processes_that_shared_their_folder(self.file_path), {4242: 1000.25})


if __name__ == "__main__":
    unittest.main()
