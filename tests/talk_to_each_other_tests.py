"""Talk to Each Other in the core, with stand-in tabs and a stand-in `codex` command (no real
agents or iTerm2 sessions): who gets which message, how, when a Claude Code session's message is
held, and how failures are named; plus the actions on several selected sessions."""

import asyncio
import os
import stat
import tempfile
import unittest
import unittest.mock

from agent_command_tab import iterm2_actions, iterm2_connection_thread
from agent_command_tab.agent_adapters.claude_code import ClaudeCodeAdapter
from agent_command_tab.agent_adapters.codex import CodexAdapter
from agent_command_tab.agent_adapters.generic import GenericAdapter
from agent_command_tab.dashboard_core_api import ActionRequest, ActionResult
from agent_command_tab.iterm2_connection_thread import DashboardCore, queue_message_for_codex_session
from agent_command_tab.iterm2_snapshot_builder import DisplayedState
from agent_command_tab.snapshot_data_classes import AgentSessionEntry, ComputerSnapshot, MonitorEntry, WindowEntry

NOTE = "Please sync up."
TAB_TITLE_BY_SESSION_ID = {"s1": "planning", "s2": "reviews", "s3": "codex tests", "s4": "aider tab", "s5": "new claude"}
EMPTY_CLAUDE_CODE_INPUT_BOX = [("❯ ", [False, False]), ("─" * 20, [False] * 20)]


class StandInCodexAdapter(CodexAdapter):
    """Finds the conversation ID of process 301 only (Codex starts its conversation at the first prompt)."""

    async def find_conversation_id(self, agent_process_id, claude_agent_record):
        return {301: "codex-conversation-1"}.get(agent_process_id)


def make_agent_session(adapter, process_id, agent_tool_name, working_folder="/p"):
    return {"adapter": adapter, "process_id": process_id, "is_in_background": False, "agent_tool_name": agent_tool_name,
            "working_folder": working_folder}


def make_core():
    """s1 and s2: Claude Code (s1 idle, s2 working); s3: Codex; s4: an agent tool without messages;
    s5: Claude Code whose name `claude agents --json` hasn't reported yet."""
    core = DashboardCore(lambda snapshot: None, lambda message: None)
    core.app = unittest.mock.Mock()
    core.codex_command_path = "/stand-in/codex"
    core.agent_sessions = {
        "s1": make_agent_session(ClaudeCodeAdapter(), 101, "Claude Code"),
        "s2": make_agent_session(ClaudeCodeAdapter(), 102, "Claude Code"),
        "s3": make_agent_session(StandInCodexAdapter(), 301, "Codex", "/p/codex"),
        "s4": make_agent_session(GenericAdapter(), 401, "aider"),
        "s5": make_agent_session(ClaudeCodeAdapter(), 501, "Claude Code"),
    }
    core.claude_agents_by_process_id = {101: {"name": "planning-3a", "status": "idle"},
                                        102: {"name": "reviews-7f", "status": "busy"}}
    core.displayed_state_by_session_id = {"s1": DisplayedState("idle", 0, 0), "s2": DisplayedState("working", 0, 0),
                                          "s3": DisplayedState("working", 0, 0), "s5": DisplayedState("idle", 0, 0)}
    entries = tuple(AgentSessionEntry(tab_id=session_id, session_id=session_id, tab_title=tab_title, tab_title_set_by_user=False,
                                      agent_session_name="", agent_tool_name="", working_folder="/p", status="idle",
                                      status_since=0.0)
                    for session_id, tab_title in TAB_TITLE_BY_SESSION_ID.items())
    core.last_published_snapshot_without_time = ComputerSnapshot("c", "Test Mac", 0, (MonitorEntry(
        "1", "Built-in", (0, 0, 100, 100), (0, 0, 100, 100), (WindowEntry("w1", "w", False, (0, 0, 10, 10), 5, entries),)),))
    return core


def ask_to_talk(core, session_ids):
    return core.handle_ask_agent_sessions_to_talk_to_each_other(
        ActionRequest("ask_agent_sessions_to_talk_to_each_other", {"session_ids": session_ids}, {"note": NOTE}))


class TalkToEachOtherTests(unittest.TestCase):
    def setUp(self):
        self.pasted = []  # (session ID, message), in order
        self.paste_result = ActionResult(True)
        self.queued = []  # (conversation ID, message)
        self.claude_code_status_by_process_id = {}  # what Claude Code's session files say right now; none by default
        self.claude_code_statuses_at_each_paste = []

        async def paste(app, session_id, message, adapter):
            self.claude_code_statuses_at_each_paste.append(dict(self.claude_code_status_by_process_id))
            if self.paste_result is not None:
                self.pasted.append((session_id, message))
            return self.paste_result

        async def queue(codex_command_path, conversation_id, message):
            self.queued.append((conversation_id, message))
            return ActionResult(True)
        for patcher in (unittest.mock.patch.object(iterm2_actions, "paste_message_into_empty_input_box", paste),
                        unittest.mock.patch.object(iterm2_connection_thread, "queue_message_for_codex_session", queue),
                        unittest.mock.patch.object(iterm2_connection_thread, "read_claude_code_session_status",
                                                   lambda config_folder, process_id: self.claude_code_status_by_process_id.get(process_id)),
                        unittest.mock.patch.object(iterm2_connection_thread, "SECONDS_BETWEEN_CHECKS_FOR_AN_AGENT_AT_ITS_PROMPT", 0.01)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_codex_gets_its_message_through_codex_queue_and_a_working_claude_code_once_it_is_idle(self):
        core = make_core()

        async def run():
            # s2 is working; it reaches its prompt a moment later.
            asyncio.get_running_loop().call_later(
                0.05, lambda: core.displayed_state_by_session_id.__setitem__("s2", DisplayedState("idle", 0, 0)))
            return await ask_to_talk(core, ["s1", "s2", "s3"])
        self.assertEqual(asyncio.run(run()), ActionResult(True))
        self.assertEqual([session_id for session_id, _ in self.pasted], ["s1", "s2"])  # s1 at once, s2 once idle
        message_for_planning = self.pasted[0][1]
        self.assertTrue(message_for_planning.startswith(NOTE + "\n\nYou are the Claude Code session \"planning-3a\" (process ID 101"))
        self.assertIn('use your SendMessage tool with to: "reviews-7f"', message_for_planning)
        self.assertIn("codex queue --thread codex-conversation-1", message_for_planning)
        self.assertNotIn('with to: "planning-3a"', message_for_planning)  # not told to message itself
        self.assertEqual([conversation_id for conversation_id, _ in self.queued], ["codex-conversation-1"])
        self.assertIn('iTerm2 tab "codex tests"', self.queued[0][1])
        self.assertIn("can't hear from you directly", self.queued[0][1])  # Codex can't reach Claude Code sessions

    def test_a_row_shown_idle_waits_while_claude_code_itself_reports_it_busy(self):
        # The row's state may come from a `claude agents --json` record up to a minute old.
        core = make_core()
        self.claude_code_status_by_process_id[101] = "shell"  # anything but idle, here a command it runs

        async def run():
            asyncio.get_running_loop().call_later(0.05, lambda: self.claude_code_status_by_process_id.__setitem__(101, "idle"))
            return await ask_to_talk(core, ["s1", "s3"])
        self.assertEqual(asyncio.run(run()), ActionResult(True))
        self.assertEqual([session_id for session_id, _ in self.pasted], ["s1"])
        self.assertEqual(self.claude_code_statuses_at_each_paste, [{101: "idle"}])  # pasted only once Claude Code said idle
        self.claude_code_status_by_process_id[101] = "busy"
        core.agent_sessions["s1"]["adapter"] = ClaudeCodeAdapter()
        action_result = asyncio.run(core.handle_send_slash_command(
            ActionRequest("send_slash_command", {"session_id": "s1"}, {"command": "/compact"})))
        self.assertEqual(action_result, ActionResult(
            False, "Claude Code reports the session busy; commands are typed only while it waits at its prompt"))

    def test_a_second_message_held_for_the_same_session_waits_for_the_turn_the_first_one_starts(self):
        core = make_core()

        async def paste_starting_a_turn(app, session_id, message, adapter):
            self.pasted.append((session_id, message))
            await asyncio.sleep(0.02)  # pasting takes a moment, holding the session's typing lock
            core.displayed_state_by_session_id[session_id] = DisplayedState("working", 0, 0)  # the turn starts after Enter
            return ActionResult(True)

        async def run():
            return await asyncio.gather(ask_to_talk(core, ["s1", "s3"]), ask_to_talk(core, ["s1", "s3"]))
        with unittest.mock.patch.object(iterm2_actions, "paste_message_into_empty_input_box", paste_starting_a_turn), \
                unittest.mock.patch.object(iterm2_connection_thread, "SECONDS_A_MESSAGE_WAITS_FOR_AN_AGENT_AT_ITS_PROMPT", 0.2):
            first_result, second_result = asyncio.run(run())
        self.assertEqual([session_id for session_id, _ in self.pasted], ["s1"])  # never pasted into the turn under way
        self.assertTrue(first_result.succeeded)
        self.assertIn("\"planning\": it didn't wait at its prompt", second_result.message)

    def test_a_row_whose_state_is_unknown_is_never_typed_into(self):
        core = make_core()
        core.displayed_state_by_session_id["s2"] = DisplayedState("unknown", 0, 0)
        with unittest.mock.patch.object(iterm2_connection_thread, "SECONDS_A_MESSAGE_WAITS_FOR_AN_AGENT_AT_ITS_PROMPT", 0.05):
            action_result = asyncio.run(ask_to_talk(core, ["s1", "s2"]))
        self.assertIn("\"reviews\": it didn't wait at its prompt", action_result.message)
        self.assertEqual([session_id for session_id, _ in self.pasted], ["s1"])

    def test_sessions_that_cant_take_part_are_named_and_the_others_still_talk(self):
        action_result = asyncio.run(ask_to_talk(make_core(), ["s1", "s3", "s4", "s5"]))
        self.assertFalse(action_result.succeeded)
        self.assertEqual(action_result.message,
                         "\"aider tab\": aider sessions can't take messages from other sessions; "
                         "\"new claude\": its Claude Code session name isn't known yet")
        self.assertEqual([session_id for session_id, _ in self.pasted], ["s1"])
        self.assertEqual(len(self.queued), 1)
        self.assertNotIn("new claude", self.pasted[0][1])  # left out of the others' lists too

    def test_a_codex_session_takes_part_only_while_its_conversation_can_be_identified(self):
        core = make_core()
        # Right after /clear, the lookup would still find the ended conversation's file.
        core.conversation_files_by_session_id["s3"] = {"key": (301, None), "path": None, "looked_up_at": 0,
                                                       "modified_at": None, "cleared_path": "/codex/ended.jsonl"}
        action_result = asyncio.run(ask_to_talk(core, ["s1", "s3"]))
        self.assertIn("\"codex tests\": its conversation was just cleared", action_result.message)
        self.assertEqual((self.pasted, self.queued), ([], []))  # nobody left to talk to
        core = make_core()
        core.agent_sessions["s3"]["process_id"] = 302  # no single conversation file fits
        action_result = asyncio.run(ask_to_talk(core, ["s1", "s3"]))
        self.assertIn("\"codex tests\": its Codex conversation can't be identified", action_result.message)
        core = make_core()
        # Another Codex session in the same folder, even one not selected: the earlier of the two
        # would find the later one's conversation file.
        core.agent_sessions["s6"] = make_agent_session(StandInCodexAdapter(), 601, "Codex", "/p/codex")
        action_result = asyncio.run(ask_to_talk(core, ["s1", "s3"]))
        self.assertIn("\"codex tests\": another Codex session runs or ran in the same folder", action_result.message)

    def test_nothing_is_sent_when_fewer_than_two_can_take_part(self):
        action_result = asyncio.run(ask_to_talk(make_core(), ["s1", "s4"]))
        self.assertEqual(action_result.message, "\"planning\": no other selected session can take part; "
                                                "\"aider tab\": aider sessions can't take messages from other sessions")
        self.assertEqual((self.pasted, self.queued), ([], []))

    def test_a_message_held_too_long_is_given_up_on(self):
        self.paste_result = None  # the input box keeps holding a draft
        with unittest.mock.patch.object(iterm2_connection_thread, "SECONDS_A_MESSAGE_WAITS_FOR_AN_AGENT_AT_ITS_PROMPT", 0.05):
            action_result = asyncio.run(ask_to_talk(make_core(), ["s1", "s3"]))
        self.assertIn("\"planning\": it didn't wait at its prompt with an empty input box", action_result.message)
        self.assertEqual(self.pasted, [])
        self.assertEqual(len(self.queued), 1)

    def test_a_held_message_is_dropped_when_its_agent_ends(self):
        core = make_core()

        async def run():
            asyncio.get_running_loop().call_later(0.05, lambda: core.agent_sessions["s2"].__setitem__("process_id", 999))
            return await ask_to_talk(core, ["s1", "s2"])
        self.assertEqual(asyncio.run(run()).message, "\"reviews\": its agent ended before the message could be pasted")
        self.assertEqual([session_id for session_id, _ in self.pasted], ["s1"])

    def test_actions_on_several_sessions_name_each_tab_that_failed(self):
        core = make_core()

        async def handle_one(action_request):
            session_id = action_request.target["session_id"]
            if session_id == "s3":
                raise RuntimeError("tab gone")
            return ActionResult(session_id == "s1", "the agent is working")
        action_result = asyncio.run(core.run_for_each_agent_session(
            ActionRequest("send_slash_command_to_agent_sessions", {"session_ids": ["s1", "s2", "s3"]}, {"command": "/compact"}),
            handle_one))
        self.assertEqual(action_result, ActionResult(
            False, "\"reviews\": the agent is working; \"codex tests\": it failed: RuntimeError('tab gone')"))


class SlashCommandsForSeveralSessionsTests(unittest.TestCase):
    """/compact and /clear from the menu of several selected rows reach sessions in the middle of a turn too."""

    def setUp(self):
        self.typed = []  # (session ID, command), in order
        self.input_box_is_ready = True

        async def type_command(app, session_id, command, adapter, agent_is_in_the_middle_of_a_turn=False):
            if not self.input_box_is_ready:
                return None
            self.typed.append((session_id, command))
            return ActionResult(True)
        for patcher in (unittest.mock.patch.object(iterm2_actions, "type_slash_command", type_command),
                        unittest.mock.patch.object(iterm2_connection_thread, "read_claude_code_session_status",
                                                   lambda config_folder, process_id: None),
                        unittest.mock.patch.object(iterm2_connection_thread, "SECONDS_BETWEEN_CHECKS_FOR_AN_AGENT_AT_ITS_PROMPT", 0.01)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def send(self, core, session_ids, command):
        return core.handle_send_slash_command_to_agent_sessions(
            ActionRequest("send_slash_command_to_agent_sessions", {"session_ids": session_ids}, {"command": command}))

    def test_a_working_claude_code_session_gets_it_at_once_and_a_working_codex_session_once_it_is_idle(self):
        core = make_core()
        core.find_current_conversation_file = unittest.mock.AsyncMock(return_value="/old-conversation.jsonl")
        core.forget_the_cleared_conversation = unittest.mock.Mock()

        async def run():
            # s1 is idle, s2 (Claude Code) and s3 (Codex) are working; s3 reaches its prompt a moment later.
            asyncio.get_running_loop().call_later(
                0.05, lambda: core.displayed_state_by_session_id.__setitem__("s3", DisplayedState("idle", 0, 0)))
            return await self.send(core, ["s1", "s2", "s3"], "/clear")
        self.assertEqual(asyncio.run(run()), ActionResult(True))
        self.assertEqual(sorted(self.typed[:2]), [("s1", "/clear"), ("s2", "/clear")])
        self.assertEqual(self.typed[2], ("s3", "/clear"))  # Codex refuses commands during a task
        # Only a /clear typed at the prompt ends the conversation at once; s2's waits in Claude Code's queue.
        self.assertEqual([call.args[0] for call in core.forget_the_cleared_conversation.call_args_list], ["s1", "s3"])

    def test_it_waits_while_the_input_box_holds_unsent_text_and_gives_up_in_the_end(self):
        core = make_core()
        self.input_box_is_ready = False

        async def run():
            asyncio.get_running_loop().call_later(0.05, lambda: setattr(self, "input_box_is_ready", True))
            return await self.send(core, ["s2"], "/compact")
        self.assertEqual(asyncio.run(run()), ActionResult(True))
        self.assertEqual(self.typed, [("s2", "/compact")])
        self.input_box_is_ready = False
        with unittest.mock.patch.object(iterm2_connection_thread, "SECONDS_A_MESSAGE_WAITS_FOR_AN_AGENT_AT_ITS_PROMPT", 0.05):
            action_result = asyncio.run(self.send(core, ["s2"], "/compact"))
        self.assertIn("\"reviews\": it didn't wait at its prompt with an empty input box", action_result.message)

    def test_a_codex_row_without_a_state_gets_it_once_its_screen_shows_no_turn(self):
        core = make_core()
        core.displayed_state_by_session_id["s3"] = DisplayedState("unknown", 0, 0)  # Codex 0.155.1 reports no state
        screen_lines = [("• Working (12s • esc to interrupt)", []), ("› ", [])]

        async def read_screen_lines(session):
            return screen_lines

        async def run():
            asyncio.get_running_loop().call_later(0.05, lambda: screen_lines.pop(0))  # the turn ends
            return await self.send(core, ["s3"], "/compact")
        with unittest.mock.patch.object(iterm2_actions, "read_screen_lines_with_faint_flags", read_screen_lines):
            self.assertEqual(asyncio.run(run()), ActionResult(True))
        self.assertEqual(self.typed, [("s3", "/compact")])
        with unittest.mock.patch.object(iterm2_actions, "read_screen_lines_with_faint_flags", read_screen_lines):
            self.assertTrue(asyncio.run(core.agent_whose_state_is_unknown_shows_no_turn_on_screen("s3", core.agent_sessions["s3"])))
            screen_lines.insert(0, ("•\x00Working\x00(3s\x00•\x00esc\x00to\x00interrupt)", []))  # iTerm2's empty cells are NULs
            self.assertFalse(asyncio.run(core.agent_whose_state_is_unknown_shows_no_turn_on_screen("s3", core.agent_sessions["s3"])))
            screen_lines.pop(0)
            core.displayed_state_by_session_id["s3"] = DisplayedState("working", 0, 0)  # a state wins over the screen
            self.assertFalse(asyncio.run(core.agent_whose_state_is_unknown_shows_no_turn_on_screen("s3", core.agent_sessions["s3"])))

    def test_a_suspended_agent_fails_at_once_and_the_others_still_get_the_command(self):
        core = make_core()
        core.agent_sessions["s1"]["is_in_background"] = True
        action_result = asyncio.run(self.send(core, ["s1", "s2"], "/compact"))
        self.assertEqual(action_result, ActionResult(False, "\"planning\": the agent is suspended or another program is in front of it"))
        self.assertEqual(self.typed, [("s2", "/compact")])


class ReadClaudeCodeSessionStatusTests(unittest.TestCase):
    def test_the_status_comes_from_the_session_file_of_its_config_folder(self):
        with tempfile.TemporaryDirectory() as config_folder:
            os.mkdir(os.path.join(config_folder, "sessions"))
            with open(os.path.join(config_folder, "sessions", "4242.json"), "w") as session_file:
                session_file.write('{"pid": 4242, "status": "busy"}')
            with open(os.path.join(config_folder, "sessions", "4343.json"), "w") as session_file:
                session_file.write("{not json")
            self.assertEqual(iterm2_connection_thread.read_claude_code_session_status(config_folder, 4242), "busy")
            self.assertIsNone(iterm2_connection_thread.read_claude_code_session_status(config_folder, 4343))
            self.assertIsNone(iterm2_connection_thread.read_claude_code_session_status(config_folder, 4444))  # no file


class QueueMessageForCodexSessionTests(unittest.TestCase):
    def write_stand_in_codex(self, folder, script_body):
        path = os.path.join(folder, "codex")
        with open(path, "w") as script:
            script.write("#!/bin/sh\n" + script_body)
        os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR)
        return path

    def test_the_message_is_attached_to_its_option_as_one_argument(self):
        # Attached, a note starting with a bullet ("- ...") isn't taken for an unknown option.
        with tempfile.TemporaryDirectory() as folder:
            arguments_path = os.path.join(folder, "arguments")
            codex_path = self.write_stand_in_codex(folder, f"printf '%s\\0' \"$@\" > {arguments_path}\n")
            action_result = asyncio.run(queue_message_for_codex_session(codex_path, "conversation-1", "- point one\n- point two"))
            with open(arguments_path) as arguments_file:
                arguments = arguments_file.read().split("\0")[:-1]
        self.assertEqual(action_result, ActionResult(True))
        self.assertEqual(arguments, ["queue", "--thread", "conversation-1", "--message=- point one\n- point two"])

    def test_a_failure_reports_what_codex_said(self):
        with tempfile.TemporaryDirectory() as folder:
            codex_path = self.write_stand_in_codex(folder, "echo 'no thread with that ID' >&2\nexit 1\n")
            action_result = asyncio.run(queue_message_for_codex_session(codex_path, "conversation-1", "hi"))
        self.assertEqual(action_result, ActionResult(False, "codex queue failed: no thread with that ID"))
        self.assertEqual(asyncio.run(queue_message_for_codex_session(None, "conversation-1", "hi")),
                         ActionResult(False, "the codex command wasn't found"))


class PasteMessageIntoEmptyInputBoxTests(unittest.TestCase):
    """iterm2_actions.paste_message_into_empty_input_box, with screens read from a list."""

    def paste(self, screens):
        typed_texts = []

        class StandInSession:
            async def async_send_text(self, text, suppress_broadcast=False):
                typed_texts.append(text)
        app = unittest.mock.Mock(get_session_by_id=lambda session_id: StandInSession())
        with unittest.mock.patch.object(iterm2_actions, "read_screen_lines_with_faint_flags",
                                        unittest.mock.AsyncMock(side_effect=screens)), \
                unittest.mock.patch.object(iterm2_actions, "SECONDS_BETWEEN_A_PASTE_AND_ITS_ENTER", 0), \
                unittest.mock.patch.object(iterm2_actions, "SECONDS_BEFORE_CHECKING_THAT_A_COMMAND_WAS_SUBMITTED", 0):
            return asyncio.run(iterm2_actions.paste_message_into_empty_input_box(
                app, "s1", "line one\nline two", ClaudeCodeAdapter())), typed_texts

    def test_the_message_is_one_bracketed_paste_then_enter(self):
        action_result, typed_texts = self.paste([EMPTY_CLAUDE_CODE_INPUT_BOX, EMPTY_CLAUDE_CODE_INPUT_BOX])
        self.assertEqual(action_result, ActionResult(True))
        self.assertEqual(typed_texts, ["\x1b[200~line one\nline two\x1b[201~", "\r"])

    def test_a_draft_in_the_box_means_nothing_is_typed_yet(self):
        draft = [("❯ half a thought", [False] * 16), ("─" * 20, [False] * 20)]
        self.assertEqual(self.paste([draft]), (None, []))

    def test_text_left_in_the_box_after_enter_is_a_failure(self):
        still_there = [("❯ [Pasted text #1 +2 lines]", [False] * 27), ("─" * 20, [False] * 20)]
        action_result, typed_texts = self.paste([EMPTY_CLAUDE_CODE_INPUT_BOX, still_there])
        self.assertFalse(action_result.succeeded)
        self.assertEqual(len(typed_texts), 2)


class TypeSlashCommandTests(unittest.TestCase):
    """iterm2_actions.type_slash_command presses Enter into an agent in the middle of a turn only
    while the command is still in the input box (Claude Code may open a permission prompt
    meanwhile), and leaves a command Codex refused during a turn for a later try."""

    def type_compact(self, screen_after_typing, agent_is_in_the_middle_of_a_turn=True, adapter=None,
                     empty_input_box=EMPTY_CLAUDE_CODE_INPUT_BOX, screen_after_enter=None):
        """Types /compact into a stand-in tab whose empty box (empty_input_box) shows
        screen_after_typing once the command is typed and screen_after_enter (by default the empty
        box again) after Enter; returns (ActionResult or None, sent texts)."""
        sent_texts = []

        class StandInSession:
            async def async_send_text(self, text, suppress_broadcast=False):
                sent_texts.append(text)

        async def read_screen_lines(session):
            return [empty_input_box, screen_after_typing, screen_after_enter or empty_input_box][len(sent_texts)]
        app = unittest.mock.Mock(get_session_by_id=lambda session_id: StandInSession())
        with unittest.mock.patch.object(iterm2_actions, "read_screen_lines_with_faint_flags", read_screen_lines), \
                unittest.mock.patch.object(iterm2_actions, "SECONDS_BEFORE_CHECKING_THAT_A_COMMAND_WAS_SUBMITTED", 0):
            return asyncio.run(iterm2_actions.type_slash_command(
                app, "s1", "/compact", adapter or ClaudeCodeAdapter(), agent_is_in_the_middle_of_a_turn)), sent_texts

    def test_enter_follows_the_command_while_it_is_in_the_box(self):
        self.assertEqual(self.type_compact([("❯ /compact", [False] * 10), ("─" * 20, [False] * 20)]),
                         (ActionResult(True), ["/compact", "\r"]))

    def test_no_enter_when_a_permission_prompt_opened_meanwhile(self):
        permission_prompt = [(" Do you want to proceed?", []), (" ❯ 1. Yes", []), ("   2. No", [])]
        self.assertEqual(self.type_compact(permission_prompt),
                         (ActionResult(False, "/compact wasn't submitted: the tab's input box changed before Enter"),
                          ["/compact"]))

    def test_an_agent_at_its_prompt_gets_enter_without_the_check(self):
        # Codex's input box couldn't be found with its slash-command menu open (live check, 2026-10-22).
        self.assertEqual(self.type_compact([("menu covering the box", [])], agent_is_in_the_middle_of_a_turn=False),
                         (ActionResult(True), ["/compact", "\r"]))

    def test_a_command_codex_refused_during_a_turn_is_left_for_a_later_try(self):
        # Codex 0.155.1 hides its turn timer while answer text streams, so the turn looked over on screen.
        empty_codex_input_box = [("› ", [False, False]), ("  gpt-5 low · /p", [False] * 16)]
        # iTerm2's empty cells are NULs
        refused = [("■\x00'/compact'\x00is\x00disabled\x00while\x00a\x00task\x00is\x00in\x00progress.", [])] + empty_codex_input_box
        self.assertEqual(self.type_compact([("menu covering the box", [])], agent_is_in_the_middle_of_a_turn=False,
                                           adapter=CodexAdapter(), empty_input_box=empty_codex_input_box,
                                           screen_after_enter=refused),
                         (None, ["/compact", "\r"]))


if __name__ == "__main__":
    unittest.main()
