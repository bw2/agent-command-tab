"""The state that tells whether an agent is in a turn for what the core reads of its processes
(DashboardCore.find_state_the_agents_processes_go_by): the command it waits on
(read_agent_screens_periodically, note_command_the_agent_is_waiting_on) and its child processes
(list_agent_processes_and_their_turns) go by the state its row shows."""

import asyncio
import unittest
import unittest.mock

from agent_command_tab import dashboard_core
from agent_command_tab.agent_adapters.claude_code import ClaudeCodeAdapter
from agent_command_tab.agent_process_tree import ProcessInfo
from agent_command_tab.dashboard_core import DashboardCore, build_conversation_file_record
from agent_command_tab.session_status_computation import (compute_session_status_from_sources,
                                                          update_permission_dialog_reading_from_claude_codes_own_status)
from agent_command_tab.snapshot_builder import DisplayedState
from condition_waiting import wait_until_in_a_coroutine
from gathered_window_builders import make_agent_session, make_gathered_tab, make_gathered_window
from hook_event_builders import make_event

COMMAND_PROCESS = ProcessInfo(301, 101, "pytest", 95.0)  # the command the agent, process 101, runs


class ClaudeCodeAdapterWithAKnownTurnStart(ClaudeCodeAdapter):
    """Gives latest_prompt_at as the time of the latest prompt in any conversation file."""

    latest_prompt_at = None

    def read_turn_started_at(self, conversation_file_path):
        return self.latest_prompt_at


class OneClaudeCodeTabTestCase(unittest.IsolatedAsyncioTestCase):
    """A core with one iTerm2 tab, s1, whose Claude Code agent (process 101) runs COMMAND_PROCESS,
    with stand-ins for what is read of the processes and of Claude Code's own report."""

    def setUp(self):
        self.core = DashboardCore(lambda snapshot: None, lambda message: None)
        self.adapter = ClaudeCodeAdapterWithAKnownTurnStart()
        self.core.agent_sessions = {"s1": make_agent_session(101, self.adapter)}
        self.core.gathered_windows = [make_gathered_window("w1", [make_gathered_tab("t1", "s1", "claude", 101)])]
        self.first_session = self.core.gathered_windows[0]["tabs"][0]["first_session"]
        self.core.claude_agents_by_process_id = {101: {"config_folder": None}}
        self.claude_code_status = None  # Claude Code's own report of the session (its session file)
        self.turn_starts_the_command_was_chosen_by = []

        def choose_command(descendant_processes, turn_started_at, *other_arguments):
            self.turn_starts_the_command_was_chosen_by.append(turn_started_at)
            return COMMAND_PROCESS, COMMAND_PROCESS.started_at
        for name, replacement in (
                ("list_descendant_processes", lambda process_id, maximum_depth=None: [COMMAND_PROCESS]),
                ("choose_command_the_agent_is_waiting_on", choose_command),
                ("read_name_the_process_was_started_as", lambda process_id: "pytest"),
                ("read_claude_code_session_status", lambda config_folder, process_id: self.claude_code_status)):
            patch = unittest.mock.patch.object(dashboard_core, name, replacement)
            patch.start()
            self.addCleanup(patch.stop)

    def set_status_in_iterm2(self, status_value, status_change_time, turn_started_at):
        """Has iTerm2 say status_value for the tab since status_change_time, in a turn begun at turn_started_at."""
        self.first_session.update(status_set_in_iterm2=status_value, status=status_value)
        self.core.session_tracking_by_session_id["s1"] = {
            "status_value": status_value, "status_change_time": status_change_time, "last_time_tab_was_seen": None,
            "turn_started_at": turn_started_at}

    async def note_the_command_once(self):
        """Runs the screen check until it has noted the tab's command once; returns the state it
        noted it by."""
        states_noted = []
        note_command = self.core.note_command_the_agent_is_waiting_on

        async def note_command_and_record_its_state(session_id, agent_session, reading, state, now):
            await note_command(session_id, agent_session, reading, state, now)
            states_noted.append(state)
        self.core.screen_check_needed.set()
        with unittest.mock.patch.object(self.core, "note_command_the_agent_is_waiting_on", note_command_and_record_its_state):
            screen_check = asyncio.ensure_future(self.core.read_agent_screens_periodically())
            try:
                self.assertTrue(await wait_until_in_a_coroutine(lambda: states_noted), "the screen check noted no command")
            finally:
                screen_check.cancel()
        return states_noted[0]

    def find_the_noted_command(self):
        return self.core.session_readings_by_session_id["s1"].get("running_command")

    def agent_is_in_a_turn(self):
        """Whether the child processes are read as those of an agent in a turn."""
        return self.core.list_agent_processes_and_their_turns()["s1"][1]


class AnsweredPermissionPromptTests(OneClaudeCodeTabTestCase):
    def setUp(self):
        super().setUp()
        self.set_status_in_iterm2("waiting", status_change_time=100.0, turn_started_at=90.0)  # where iTerm2's value stays

    async def test_an_approved_prompt_goes_on_with_the_turn(self):
        self.claude_code_status = "waiting"
        self.assertEqual(await self.note_the_command_once(), "asking")
        self.assertIsNone(self.find_the_noted_command())
        self.assertTrue(self.agent_is_in_a_turn())
        self.claude_code_status = "shell"  # approved: the tool runs
        self.assertEqual(await self.note_the_command_once(), "working")
        self.assertEqual(self.find_the_noted_command(), ("pytest", 95.0))
        self.assertEqual(self.turn_starts_the_command_was_chosen_by, [90.0])
        self.assertTrue(self.agent_is_in_a_turn())

    async def test_a_denied_prompt_ended_the_turn_for_the_processes_as_for_the_row(self):
        self.claude_code_status = "idle"  # answered No or Esc
        self.assertEqual(await self.note_the_command_once(), "idle")
        reading = self.core.session_readings_by_session_id["s1"]
        self.assertEqual(compute_session_status_from_sources("waiting", 100.0, None, None, False, reading), ("idle", "turn interrupted"))
        # Its shell still running is no command of a turn: it counts as a helper until the next turn,
        # and the child processes are read as between turns, where it is a background shell.
        self.assertIsNone(self.find_the_noted_command())
        self.assertEqual(reading["helper_process_ids"], frozenset({COMMAND_PROCESS.process_id}))
        self.assertFalse(self.agent_is_in_a_turn())

    def test_a_paused_agent_asked_before_its_pause_keeps_the_turn_it_was_in(self):
        self.core.agent_sessions["s1"]["is_stopped"] = True
        self.core.displayed_state_by_session_id = {"s1": DisplayedState("paused", 110.0, 100.0)}
        self.assertTrue(self.agent_is_in_a_turn())  # by the value its tracking kept
        self.core.session_readings_by_session_id["s1"] = update_permission_dialog_reading_from_claude_codes_own_status(
            {}, "idle", 100.0, 103.0)
        self.assertFalse(self.agent_is_in_a_turn())


class TabITerm2ReportsNoStatusForTests(OneClaudeCodeTabTestCase):
    """The tab's row goes by Claude Code's own report (snapshot_builder), and so does its command."""

    def setUp(self):
        super().setUp()
        self.core.session_tracking_by_session_id["s1"] = {"status_value": None, "status_change_time": 50.0,
                                                          "last_time_tab_was_seen": None}
        self.core.conversation_files_by_session_id["s1"] = build_conversation_file_record((101, None), "/c/101.jsonl", 50.0, None)

    def show_state_in_the_row(self, state):
        self.core.displayed_state_by_session_id = {"s1": DisplayedState(state, 50.0, 50.0)}

    async def test_the_command_of_a_working_row_is_noted(self):
        self.assertIsNone(await self.note_the_command_once())  # before the first snapshot: no state yet
        self.show_state_in_the_row("working")
        self.adapter.latest_prompt_at = 60.0
        self.assertEqual(await self.note_the_command_once(), "working")
        self.assertEqual(self.find_the_noted_command(), ("pytest", 95.0))

    async def test_each_turns_start_is_read_again_from_the_conversation_file(self):
        # No status change tells when a turn starts, so the first turn's start would stay for all the later ones.
        self.show_state_in_the_row("working")
        self.adapter.latest_prompt_at = 60.0
        await self.note_the_command_once()
        self.show_state_in_the_row("idle")
        self.assertEqual(await self.note_the_command_once(), "idle")
        self.assertIsNone(self.find_the_noted_command())
        self.assertEqual(self.core.session_tracking_by_session_id["s1"],
                         {"status_value": None, "status_change_time": 50.0, "last_time_tab_was_seen": None})
        self.show_state_in_the_row("working")
        self.adapter.latest_prompt_at = 200.0
        await self.note_the_command_once()
        self.assertEqual(self.turn_starts_the_command_was_chosen_by, [60.0, 200.0])

    async def test_the_turn_after_one_that_failed_on_an_api_error_has_its_own_start_read(self):
        self.show_state_in_the_row("working")
        self.adapter.latest_prompt_at = 60.0
        await self.note_the_command_once()
        # The hooks report the turn failed: the tracked value says so until the next prompt, with the agent at its prompt.
        self.core.session_tracking_by_session_id["s1"]["status_value"] = "failed"
        self.assertEqual(await self.note_the_command_once(), "error")
        self.assertIsNone(self.find_the_noted_command())
        self.assertEqual(self.core.session_readings_by_session_id["s1"].get("helper_process_ids"),
                         frozenset({COMMAND_PROCESS.process_id}))  # what still runs is no command of the next turn
        self.core.session_tracking_by_session_id["s1"]["status_value"] = None
        self.adapter.latest_prompt_at = 200.0
        await self.note_the_command_once()
        self.assertEqual(self.turn_starts_the_command_was_chosen_by, [60.0, 200.0])  # not the failed turn's start again


class BackgroundCommandStartedInTheTurnTests(unittest.IsolatedAsyncioTestCase):
    async def test_the_command_noted_is_not_one_the_hooks_list_as_running_in_the_background(self):
        # In the turn begun at 1000, the agent (process 101) started a command in the background, then the one it waits on.
        descendants = [ProcessInfo(3, 101, "bash", 1001.0), ProcessInfo(4, 3, "npm", 1001.1),
                       ProcessInfo(5, 101, "bash", 1050.0), ProcessInfo(6, 5, "pytest", 1050.1)]
        paths_written_to_by_process_id = {3: {1: "/tmp/claude-502/-repo/tasks/b7x1ehsbe.output"},
                                          5: {1: "/tmp/claude-502/-repo/tasks/bq2m4k0wd.output"}}
        core = DashboardCore(lambda snapshot: None, lambda message: None)
        agent_session = make_agent_session(101, ClaudeCodeAdapter())
        core.session_tracking_by_session_id["s1"] = {"turn_started_at": 1000.0}

        async def note_the_command():
            reading = {}
            with unittest.mock.patch.object(dashboard_core, "list_descendant_processes", lambda process_id: descendants), \
                    unittest.mock.patch.object(dashboard_core, "read_process_arguments_and_environment", lambda process_id: ([], {})), \
                    unittest.mock.patch.object(dashboard_core, "read_name_the_process_was_started_as",
                                               lambda process_id: {4: "npm", 6: "pytest"}[process_id]), \
                    unittest.mock.patch("agent_command_tab.agent_process_tree.list_the_files_a_process_has_open",
                                        lambda process_id, only_those_open_for_writing: paths_written_to_by_process_id.get(process_id)):
                await core.note_command_the_agent_is_waiting_on("s1", agent_session, reading, "working", 1100.0)
            return reading["running_command"]
        self.assertEqual(await note_the_command(), ("npm", 1001.0))  # without hooks, the older of the two
        core.hook_state_by_agent_process_id[101] = {"background_task_ids": {"b7x1ehsbe"}}
        self.assertEqual(await note_the_command(), ("pytest", 1050.0))
        # A process started to go on with an earlier conversation lists its tasks only when its first
        # turn ends: until then, the start of the background command that its hooks reported tells.
        del core.hook_state_by_agent_process_id[101]
        core.note_hook_event(make_event("SessionStart", 101, source="resume"))
        core.note_hook_event(make_event("PostToolUse", 101, background_task_launched_id="b7x1ehsbe", background_task_launched_type="shell"))
        self.assertIsNone(core.hook_state_by_agent_process_id[101]["background_task_ids"])
        self.assertEqual(await note_the_command(), ("pytest", 1050.0))


if __name__ == "__main__":
    unittest.main()
