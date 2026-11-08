"""Save session and Restore sessions in the core (user request, 2026-11-06), with a stand-in
terminal (no real iTerm2 or tmux): which sessions can be saved, and where each saved session is
resumed: its own tab, a new tab, or a new window."""

import asyncio
import os
import tempfile
import unittest
import unittest.mock

from agent_command_tab import dashboard_core
from agent_command_tab.agent_adapters.claude_code import ClaudeCodeAdapter
from agent_command_tab.agent_adapters.codex import CodexAdapter
from agent_command_tab.agent_adapters.generic import GenericAdapter
from agent_command_tab.dashboard_core import DashboardCore
from agent_command_tab.dashboard_core_api import ActionRequest, ActionResult
from agent_command_tab.snapshot_data_classes import AgentSessionEntry, ComputerSnapshot, MonitorEntry, WindowEntry
from agent_command_tab.terminal_backends.base import TerminalBackend

MONITOR = {"monitor_id": "1", "name": "Built-in", "frame": (0, 0, 2000, 1300), "visible_frame": (0, 0, 2000, 1260)}


class StandInTerminalSession:
    def __init__(self, typed_texts, session_id):
        self.typed_texts = typed_texts
        self.session_id = session_id

    async def send_text(self, text):
        self.typed_texts.append((self.session_id, text))


class StandInTerminalBackend(TerminalBackend):
    """A terminal with fixed windows; records the tabs it opens, what it types, renames and frames."""

    def __init__(self, terminal_name, gathered_windows):
        super().__init__(lambda: None, lambda: None, lambda terminal_name, message: None)
        self.terminal_name = terminal_name
        self.gathered_windows = gathered_windows
        self.windows_have_frames = terminal_name == "iTerm2"
        self.ids_are_never_reused = True
        self.actions = []
        self.typed_texts = []

    def is_connected(self):
        return True

    def terminal_runs(self):
        return True

    async def gather_windows(self):
        return self.gathered_windows

    def find_session(self, session_id):
        return StandInTerminalSession(self.typed_texts, session_id)

    async def start_agent_session(self, window_id, folder, launch_command, after_tab_id=None):
        self.actions.append(("start", window_id, folder, launch_command))
        number = len(self.actions)
        if window_id is None:
            return ActionResult(True, details={"window_id": f"new-w{number}", "tab_id": f"new-t{number}"})
        return ActionResult(True, details={"tab_id": f"new-t{number}"})

    async def rename_tab(self, tab_id, new_title):
        self.actions.append(("rename_tab", tab_id, new_title))
        return ActionResult(True)

    async def rename_window(self, window_id, new_title):
        self.actions.append(("rename_window", window_id, new_title))
        return ActionResult(True)

    async def set_window_frame(self, window_id, frame):
        self.actions.append(("set_frame", window_id, frame))

    async def read_window_frame(self, window_id):
        return (30, 40, 800, 600)  # moved since the last snapshot, which iTerm2 doesn't report


def make_gathered_tab(tab_id, session_id, command_line, job_pid=None):
    return {"tab_id": tab_id, "title": "", "title_override_format": None,
            "first_session": {"session_id": session_id, "command_line": command_line, "job_pid": job_pid, "job_folder": "/p",
                              "job_started_at": 1.0, "status_set_in_iterm2": None, "status_set_in_iterm2_at": None,
                              "status": None, "path": "/p"}}


def make_entry(session_id, title, tool="Claude Code", status="idle", title_set_by_user=False):
    return AgentSessionEntry(tab_id=f"t-{session_id}", session_id=session_id, tab_title=title,
                             tab_title_set_by_user=title_set_by_user, agent_session_name=title, agent_tool_name=tool,
                             working_folder="/entry-folder", status=status)


def make_core(folder):
    """iTerm2 window w1: s1 runs Claude Code in conversation conv-1, s2 is a shell at its prompt,
    s3 runs aider (a tool that can't resume), s4 runs Claude Code that was sent no message, s5
    runs a Claude Code a restore started with `--resume conv-9`, which Claude Code doesn't list yet."""
    core = DashboardCore(lambda snapshot: None, lambda message: None)
    iterm2 = StandInTerminalBackend("iTerm2", [{
        "window_id": "w1", "terminal_name": "iTerm2", "frame": (0, 0, 800, 600), "title": "w1", "title_override_format": None,
        "tabs": [make_gathered_tab("t-s1", "s1", "claude", 101), make_gathered_tab("t-s2", "s2", "-bash"),
                 make_gathered_tab("t-s3", "s3", "aider", 301), make_gathered_tab("t-s4", "s4", "claude", 104),
                 make_gathered_tab("t-s5", "s5", "claude --resume conv-9", 105)]}])
    core.terminal_backends = [iterm2]
    core.terminal_backend_by_name = {"iTerm2": iterm2}
    core.gathered_windows = iterm2.gathered_windows
    core.monitors = [MONITOR]
    core.agent_sessions = {
        session_id: {"adapter": adapter, "process_id": process_id, "is_in_background": False, "is_stopped": False,
                     "agent_tool_name": tool, "working_folder": folder, "started_at": 1.0}
        for session_id, adapter, process_id, tool in (("s1", ClaudeCodeAdapter(), 101, "Claude Code"),
                                                      ("s3", GenericAdapter(), 301, "aider"),
                                                      ("s4", ClaudeCodeAdapter(), 104, "Claude Code"),
                                                      ("s5", ClaudeCodeAdapter(), 105, "Claude Code"))}
    core.claude_agents_by_process_id = {101: {"sessionId": "conv-1", "config_folder": None},
                                        104: {"sessionId": "conv-4", "config_folder": None}}

    async def find_current_conversation_file(session_id, agent_session):
        return "/transcripts/conv-1.jsonl" if session_id == "s1" else None
    core.find_current_conversation_file = find_current_conversation_file
    core.an_agent_runs_in_the_tab = lambda tab_id: True  # each restored agent starts at once
    window_entry = WindowEntry("w1", "my-project", True, (10, 20, 800, 600), 5, (
        make_entry("s1", "planning", title_set_by_user=True), make_entry("s3", "aider-tab", tool="aider"),
        make_entry("s4", "fresh"), make_entry("s5", "restored")))
    core.last_published_snapshot_without_time = ComputerSnapshot("c", "Test Mac", 0, (MonitorEntry(
        "1", "Built-in", MONITOR["frame"], MONITOR["visible_frame"], (window_entry,)),))
    return core, iterm2


def make_saved_session(conversation_id, folder, terminal_session_id="gone-session", name="", name_was_set_by_user=False,
                       agent_tool_name="Claude Code", claude_config_folder=None):
    return {"conversation_id": conversation_id, "agent_tool_name": agent_tool_name, "name": name,
            "name_was_set_by_user": name_was_set_by_user, "folder": folder, "claude_config_folder": claude_config_folder,
            "terminal_session_id": terminal_session_id, "saved_at": 1.0}


def make_saved_window(saved_sessions, terminal_window_id="gone-window", frame=(100, 200, 900, 700), name="saved",
                      name_was_set_by_user=False):
    return {"terminal_name": "iTerm2", "terminal_window_id": terminal_window_id, "terminal_window_opened_at": None, "name": name,
            "name_was_set_by_user": name_was_set_by_user, "frame": list(frame) if frame else None,
            "saved_sessions": saved_sessions}


def restore(core, saved_windows, launch_commands=None):
    return asyncio.run(core.handle_restore_saved_agent_sessions(ActionRequest("restore_saved_agent_sessions", {}, {
        "saved_windows": saved_windows,
        "launch_command_by_agent_tool_name": {"Claude Code": "yc", "Codex": "codex"} if launch_commands is None else launch_commands})))


class SaveAgentSessionsTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp()
        self.core, self.iterm2 = make_core(self.folder)

    def tearDown(self):
        os.rmdir(self.folder)

    def test_saves_the_sessions_it_can_and_says_why_not_for_the_others(self):
        with unittest.mock.patch.object(dashboard_core.time, "time", return_value=1000.0):
            action_result = asyncio.run(self.core.handle_save_agent_sessions(
                ActionRequest("save_agent_sessions", {"session_ids": ["s3", "s1", "s4", "gone"]})))
        self.assertFalse(action_result.succeeded)
        self.assertEqual(action_result.message,
                         '"aider-tab": aider can\'t resume a saved conversation; '
                         '"fresh": it has no conversation yet (no message was sent to it); '
                         '"a tab": that tab no longer runs an agent')
        self.assertEqual(action_result.details, {"saved_windows": [{
            "terminal_name": "iTerm2", "terminal_window_id": "w1", "terminal_window_opened_at": None, "name": "my-project",
            "name_was_set_by_user": True,
            "frame": [30, 40, 800, 600], "saved_sessions": [{  # where the window is now, not the snapshot's
                "conversation_id": "conv-1", "agent_tool_name": "Claude Code", "name": "planning", "name_was_set_by_user": True,
                "folder": self.folder, "claude_config_folder": None, "terminal_session_id": "s1", "saved_at": 1000.0}]}]})

    def test_a_cleared_conversation_with_no_new_file_isnt_saved(self):
        self.core.conversation_files_by_session_id["s1"] = {"key": None, "path": None, "modified_at": None,
                                                            "cleared_path": "/transcripts/conv-1.jsonl"}
        action_result = asyncio.run(self.core.handle_save_agent_sessions(
            ActionRequest("save_agent_sessions", {"session_ids": ["s1"]})))
        self.assertEqual((action_result.message, action_result.details),
                         ('"planning": it has had no conversation since its /clear', {"saved_windows": []}))

    def test_a_codex_session_that_started_another_conversation_isnt_saved_and_says_so(self):
        # After Codex's /new and a message, two conversation files fit its process, the newer first.
        codex_adapter = CodexAdapter()
        self.core.agent_sessions["s1"].update(adapter=codex_adapter, agent_tool_name="Codex")
        with unittest.mock.patch.object(codex_adapter, "find_conversation_files", return_value=[
                    "/sessions/rollout-2026-11-06T09-00-00-new.jsonl", "/sessions/rollout-2026-11-06T08-00-00-old.jsonl"]), \
                unittest.mock.patch.object(self.core, "codex_conversation_may_be_another_sessions", return_value=False):
            action_result = asyncio.run(self.core.handle_save_agent_sessions(
                ActionRequest("save_agent_sessions", {"session_ids": ["s1"]})))
        self.assertEqual(action_result.message,
                         '"planning": it has started more than one conversation, and which one runs now can\'t be told')


class RestoreSavedAgentSessionsTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp()
        self.core, self.iterm2 = make_core(self.folder)

    def tearDown(self):
        os.rmdir(self.folder)

    def test_a_session_whose_tab_came_back_at_a_shell_prompt_is_resumed_there(self):
        action_result = restore(self.core, [make_saved_window([make_saved_session(
            "conv-7", self.folder, terminal_session_id="s2", name="reviews", name_was_set_by_user=True)])])
        self.assertEqual(action_result, ActionResult(True, details={"restored_conversation_ids": ["conv-7"]}))
        self.assertEqual(self.iterm2.typed_texts, [("s2", f"\x05\x15cd {self.folder} && yc --resume conv-7\r")])
        self.assertEqual(self.iterm2.actions, [("rename_tab", "t-s2", "reviews")])

    def test_a_tab_running_an_agent_gets_a_new_tab_beside_it(self):
        restore(self.core, [make_saved_window([make_saved_session("conv-7", self.folder, terminal_session_id="s4")],
                                              terminal_window_id="w1")])
        self.assertEqual((self.iterm2.typed_texts, self.iterm2.actions), ([], [("start", "w1", self.folder, "yc --resume conv-7")]))

    def test_in_a_terminal_that_reuses_ids_neither_the_tab_nor_the_window_is_taken_for_the_saved_one(self):
        self.iterm2.ids_are_never_reused = False  # tmux: "s2" and "w1" may be another pane and session since the reboot
        restore(self.core, [make_saved_window([make_saved_session("conv-8", self.folder, terminal_session_id="s2")],
                                              terminal_window_id="w1", frame=None)])
        self.assertEqual((self.iterm2.typed_texts, self.iterm2.actions), ([], [("start", None, self.folder, "yc --resume conv-8")]))

    def test_in_a_terminal_that_reuses_ids_a_session_still_running_since_the_save_takes_the_saved_tab_and_window(self):
        # tmux: the session with the saved ID was created when the saved one was, so it is the same.
        self.iterm2.ids_are_never_reused = False
        self.iterm2.find_when_the_window_was_opened = lambda window_id: 1793977852 if window_id == "w1" else None
        restore(self.core, [dict(make_saved_window([make_saved_session("conv-7", self.folder, terminal_session_id="s2"),
                                                    make_saved_session("conv-8", self.folder)], terminal_window_id="w1"),
                                 terminal_window_opened_at=1793977852)])
        self.assertEqual(self.iterm2.typed_texts, [("s2", f"\x05\x15cd {self.folder} && yc --resume conv-7\r")])
        self.assertEqual(self.iterm2.actions, [("start", "w1", self.folder, "yc --resume conv-8")])

    def test_a_terminal_failing_to_start_one_session_doesnt_end_the_restore(self):
        # tmux refuses to type a launch command into a window with synchronize-panes on.
        start_agent_session = self.iterm2.start_agent_session

        async def refuse_the_first(window_id, folder, launch_command, after_tab_id=None):
            if "conv-7" in launch_command:
                raise dashboard_core.TerminalTypingRefused("its window has synchronize-panes on")
            return await start_agent_session(window_id, folder, launch_command, after_tab_id)
        self.iterm2.start_agent_session = refuse_the_first
        action_result = restore(self.core, [make_saved_window([make_saved_session("conv-7", self.folder, name="first"),
                                                               make_saved_session("conv-8", self.folder)])])
        self.assertEqual(action_result, ActionResult(False, '"first": its window has synchronize-panes on',
                                                     details={"restored_conversation_ids": ["conv-8"]}))

    def test_a_saved_window_that_is_gone_opens_again_with_its_frame_names_and_tabs(self):
        action_result = restore(self.core, [make_saved_window([
            make_saved_session("conv-7", self.folder, name="planning", name_was_set_by_user=True),
            make_saved_session("conv-8", self.folder, name="✳ agent's own title", claude_config_folder="/Users/me/.claude_work")],
            name="my-project", name_was_set_by_user=True)])
        self.assertEqual(action_result, ActionResult(True, details={"restored_conversation_ids": ["conv-7", "conv-8"]}))
        self.assertEqual(self.iterm2.actions, [
            ("start", None, self.folder, "yc --resume conv-7"),
            ("set_frame", "new-w1", (100, 200, 900, 700)),
            ("rename_tab", "new-t1", "planning"),
            ("start", "new-w1", self.folder, "CLAUDE_CONFIG_DIR=/Users/me/.claude_work yc --resume conv-8"),
            ("rename_window", "new-w1", "my-project")])

    def test_a_frame_off_every_monitor_is_left_to_the_terminal(self):  # its monitor may be unplugged
        restore(self.core, [make_saved_window([make_saved_session("conv-7", self.folder)], frame=(5000, 0, 800, 600))])
        self.assertEqual(self.iterm2.actions, [("start", None, self.folder, "yc --resume conv-7")])

    def test_sessions_after_one_resumed_in_its_own_tab_open_beside_it(self):
        restore(self.core, [make_saved_window([make_saved_session("conv-7", self.folder, terminal_session_id="s2"),
                                               make_saved_session("conv-8", self.folder)])])
        self.assertEqual(self.iterm2.actions, [("start", "w1", self.folder, "yc --resume conv-8")])

    def test_of_two_sessions_saved_from_one_tab_only_the_first_is_typed_into_it(self):
        # Saved before and after a /clear in s2, which waits at its shell prompt now.
        action_result = restore(self.core, [make_saved_window([make_saved_session("conv-7", self.folder, terminal_session_id="s2"),
                                                               make_saved_session("conv-8", self.folder, terminal_session_id="s2")])])
        self.assertEqual(action_result.details, {"restored_conversation_ids": ["conv-7", "conv-8"]})
        self.assertEqual(self.iterm2.typed_texts, [("s2", f"\x05\x15cd {self.folder} && yc --resume conv-7\r")])
        self.assertEqual(self.iterm2.actions, [("start", "w1", self.folder, "yc --resume conv-8")])

    def test_a_conversation_named_only_in_a_command_line_that_doesnt_run_it_is_restored(self):
        # s1 was started with --resume conv-7 and ran /clear since (its tool names conv-1); s5 is a
        # Duplicate of conv-8 that Claude Code doesn't list yet, which runs a new conversation.
        gathered_tabs = self.core.gathered_windows[0]["tabs"]
        gathered_tabs[0]["first_session"]["command_line"] = "claude --resume conv-7"
        gathered_tabs[4]["first_session"]["command_line"] = "claude --resume conv-8 --fork-session"
        action_result = restore(self.core, [make_saved_window([make_saved_session("conv-7", self.folder),
                                                               make_saved_session("conv-8", self.folder)])])
        self.assertEqual(action_result, ActionResult(True, details={"restored_conversation_ids": ["conv-7", "conv-8"]}))

    def test_what_cant_be_restored_is_named_and_the_rest_is_restored(self):
        action_result = restore(self.core, [make_saved_window([
            make_saved_session("conv-1", self.folder, name="already running"),  # s1's, from claude agents
            make_saved_session("conv-9", self.folder, name="just restored"),  # in s5's command line
            make_saved_session("conv-10", "/nonexistent/folder", name="folder gone"),
            make_saved_session("conv-11", self.folder, name="aider", agent_tool_name="aider"),
            make_saved_session("conv-12", self.folder, name="codex", agent_tool_name="Codex"),
            make_saved_session("conv-13", self.folder, name="restored")])], launch_commands={"Claude Code": "yc"})
        self.assertFalse(action_result.succeeded)
        self.assertEqual(action_result.message,
                         '"already running": its conversation already runs in a tab; '
                         '"just restored": its conversation already runs in a tab; '
                         '"folder gone": the folder /nonexistent/folder doesn\'t exist; '
                         '"aider": aider can\'t resume a saved conversation; '
                         '"codex": Settings has no launch command for Codex')
        self.assertEqual(self.iterm2.actions, [("start", None, self.folder, "yc --resume conv-13"),
                                               ("set_frame", "new-w1", (100, 200, 900, 700))])
        self.assertEqual(action_result.details, {"restored_conversation_ids": ["conv-13"]})  # the others stay saved

    def test_the_conversation_a_tab_ended_with_clear_can_be_restored(self):
        # Codex keeps its process at a /clear, and its lookup still finds the old conversation's file.
        self.core.conversation_was_cleared_and_the_next_has_no_file_yet = lambda session_id: session_id == "s1"
        action_result = restore(self.core, [make_saved_window([make_saved_session("conv-1", self.folder)])])
        self.assertEqual(action_result.details, {"restored_conversation_ids": ["conv-1"]})

    def test_a_second_restore_of_a_conversation_being_restored_doesnt_start_it_again(self):
        self.core.an_agent_runs_in_the_tab = lambda tab_id: False  # not yet seen when the second restore comes
        request = ActionRequest("restore_saved_agent_sessions", {}, {
            "saved_windows": [make_saved_window([make_saved_session("conv-7", self.folder, name="planning")])],
            "launch_command_by_agent_tool_name": {"Claude Code": "yc"}})

        async def restore_twice():
            first = asyncio.ensure_future(self.core.handle_restore_saved_agent_sessions(request))
            await asyncio.sleep(0.02)  # the first is waiting for its agent
            return await self.core.handle_restore_saved_agent_sessions(request), await first
        with unittest.mock.patch.object(dashboard_core, "SECONDS_A_RESTORED_AGENT_HAS_TO_START", 0.1), \
                unittest.mock.patch.object(dashboard_core, "SECONDS_BETWEEN_CHECKS_FOR_A_RESTORED_AGENT", 0.01):
            second_result, _ = asyncio.run(restore_twice())
        self.assertEqual(second_result.message, '"planning": another restore is starting it')
        self.assertEqual([action for action in self.iterm2.actions if action[0] == "start"],
                         [("start", None, self.folder, "yc --resume conv-7")])
        self.assertEqual(self.core.conversation_ids_being_restored, set())  # released when it ended

    def test_a_restore_meanwhile_of_another_session_from_the_same_tab_opens_a_new_tab(self):
        # Saved before and after a /clear in s2; each restored by its own request.
        self.core.an_agent_runs_in_the_tab = lambda tab_id: False  # not yet seen when the second restore comes

        def make_request(conversation_id):
            return ActionRequest("restore_saved_agent_sessions", {}, {
                "saved_windows": [make_saved_window([make_saved_session(conversation_id, self.folder, terminal_session_id="s2")],
                                                    terminal_window_id="w1")],
                "launch_command_by_agent_tool_name": {"Claude Code": "yc"}})

        async def restore_both():
            first = asyncio.ensure_future(self.core.handle_restore_saved_agent_sessions(make_request("conv-7")))
            await asyncio.sleep(0.02)  # the first is waiting for its agent
            await self.core.handle_restore_saved_agent_sessions(make_request("conv-8"))
            await first
        with unittest.mock.patch.object(dashboard_core, "SECONDS_A_RESTORED_AGENT_HAS_TO_START", 0.1), \
                unittest.mock.patch.object(dashboard_core, "SECONDS_BETWEEN_CHECKS_FOR_A_RESTORED_AGENT", 0.01):
            asyncio.run(restore_both())
        self.assertEqual(self.iterm2.typed_texts, [("s2", f"\x05\x15cd {self.folder} && yc --resume conv-7\r")])
        self.assertEqual(self.iterm2.actions, [("start", "w1", self.folder, "yc --resume conv-8")])
        self.assertEqual(self.core.terminal_session_ids_restores_typed_into, set())  # released when they ended

    def test_a_session_whose_agent_doesnt_start_in_its_tab_stays_saved(self):
        # Its launch command names a program that isn't installed: the shell in the tab says so.
        self.core.an_agent_runs_in_the_tab = lambda tab_id: tab_id != "new-t1"
        with unittest.mock.patch.object(dashboard_core, "SECONDS_A_RESTORED_AGENT_HAS_TO_START", 0.05), \
                unittest.mock.patch.object(dashboard_core, "SECONDS_BETWEEN_CHECKS_FOR_A_RESTORED_AGENT", 0.01):
            action_result = restore(self.core, [make_saved_window([make_saved_session("conv-7", self.folder, name="planning"),
                                                                   make_saved_session("conv-8", self.folder)])])
        self.assertEqual(action_result.details, {"restored_conversation_ids": ["conv-8"]})
        self.assertIn('"planning": its agent didn\'t start in its tab', action_result.message)

    def test_a_restore_asks_for_gathers_until_the_agent_shows_in_its_tab(self):
        looks = []

        def an_agent_runs_in_the_tab(tab_id):  # found by the third gather
            looks.append(tab_id)
            return len(looks) >= 3
        self.core.an_agent_runs_in_the_tab = an_agent_runs_in_the_tab
        with unittest.mock.patch.object(dashboard_core, "SECONDS_BETWEEN_CHECKS_FOR_A_RESTORED_AGENT", 0.01):
            action_result = restore(self.core, [make_saved_window([make_saved_session("conv-7", self.folder)])])
        self.assertEqual((action_result.details, len(looks)), ({"restored_conversation_ids": ["conv-7"]}, 3))
        self.assertTrue(self.core.regather_needed.is_set())

    def test_an_agent_runs_in_a_tab_once_a_gather_found_its_process(self):
        core, _ = make_core(self.folder)
        self.assertEqual([DashboardCore.an_agent_runs_in_the_tab(core, tab_id) for tab_id in ("t-s1", "t-s2", "gone")],
                         [True, False, False])

    def test_a_session_whose_new_tab_closed_at_once_stays_saved(self):
        async def start_a_session_that_ends_at_once(window_id, folder, launch_command, after_tab_id=None):
            return ActionResult(True)  # iTerm2 gave no window: its session ended right away
        self.iterm2.start_agent_session = start_a_session_that_ends_at_once
        action_result = restore(self.core, [make_saved_window([make_saved_session("conv-7", self.folder, name="planning")])])
        self.assertEqual(action_result, ActionResult(False, '"planning": its new tab closed at once',
                                                     details={"restored_conversation_ids": []}))

    def test_an_open_window_none_of_whose_sessions_was_restored_keeps_its_name(self):
        action_result = restore(self.core, [make_saved_window([make_saved_session("conv-1", self.folder)],  # s1 runs it
                                                              terminal_window_id="w1", name="Archived", name_was_set_by_user=True)])
        self.assertEqual((action_result.succeeded, self.iterm2.actions), (False, []))

    def test_a_codex_conversation_is_not_started_again_while_a_codex_session_that_may_run_it_does(self):
        # Another Codex session started in its folder since: two conversation files fit its process.
        codex_adapter = CodexAdapter()
        self.core.agent_sessions["s1"].update(adapter=codex_adapter, agent_tool_name="Codex")
        self.core.gathered_windows[0]["tabs"][0]["first_session"]["command_line"] = "codex"
        conversation_files = [f"/sessions/rollout-2026-11-06T09-00-00-{conversation_id}.jsonl" for conversation_id in (
            "0199a1b2-0000-7000-8000-00000000000b", "0199a1b2-0000-7000-8000-00000000000a")]
        with unittest.mock.patch.object(codex_adapter, "find_conversation_files", return_value=conversation_files):
            action_result = restore(self.core, [make_saved_window([make_saved_session(
                "0199a1b2-0000-7000-8000-00000000000a", self.folder, name="A", agent_tool_name="Codex")])])
        self.assertEqual(action_result.message, '"A": its conversation already runs in a tab')

    def test_a_name_the_terminal_refuses_doesnt_end_the_restore(self):
        # tmux refuses a session name another session has ("duplicate session: research").
        async def refuse_the_name(window_id, new_title):
            raise dashboard_core.TerminalRequestFailed(f"duplicate session: {new_title}")
        self.iterm2.rename_window = refuse_the_name
        with unittest.mock.patch("sys.stderr") as printed:
            action_result = restore(self.core, [
                make_saved_window([make_saved_session("conv-7", self.folder)], name="research", name_was_set_by_user=True),
                make_saved_window([make_saved_session("conv-8", self.folder)], terminal_window_id="other-gone-window")])
        self.assertEqual(action_result, ActionResult(True, details={"restored_conversation_ids": ["conv-7", "conv-8"]}))
        self.assertIn("duplicate session: research", "".join(call.args[0] for call in printed.write.call_args_list))

    def test_a_conversation_is_started_once_even_when_two_saved_windows_hold_it(self):
        saved_session = make_saved_session("conv-7", self.folder)
        action_result = restore(self.core, [make_saved_window([saved_session]), make_saved_window([saved_session], frame=None)])
        self.assertEqual(action_result.message, '"conv-7": its conversation already runs in a tab')
        self.assertEqual([action for action in self.iterm2.actions if action[0] == "start"],
                         [("start", None, self.folder, "yc --resume conv-7")])


if __name__ == "__main__":
    unittest.main()
