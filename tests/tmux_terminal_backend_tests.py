"""The tmux terminal backend: its parsing of tmux's output and the keys it types (pure), and its
actions against a real tmux server of its own on a private socket (skipped where tmux isn't installed)."""

import asyncio
import os
import socket
import subprocess
import tempfile
import time
import unittest
import unittest.mock

from agent_command_tab.terminal_backends import tmux_terminal_backend
from agent_command_tab.terminal_backends.base import TerminalClient, TerminalRequestFailed, TerminalTypingRefused
from agent_command_tab.terminal_backends.tmux_terminal_backend import (
    CLIENT_FIELD_NAMES, FIELD_SEPARATOR, PANE_FIELD_NAMES, TmuxTerminalBackend, TmuxTerminalSession,
    apply_sgr_parameters_to_faintness, build_gathered_windows, build_send_keys_commands, build_tmux_format,
    escape_tmux_argument, escape_tmux_format_argument, join_tmux_commands, parse_screen_lines_with_faint_flags,
    parse_tmux_output_lines)
from condition_waiting import wait_until
from private_tmux_server import TMUX_PROGRAM_PATH, start_a_private_tmux_server


def make_pane_record(session_id, session_name, window_id, pane_id, pane_index="0", window_active="1",
                     automatic_rename="1", window_name="zsh", pane_pid="100", pane_active="1", window_zoomed_flag="0"):
    return {"session_id": session_id, "session_name": session_name, "window_id": window_id, "window_active": window_active,
            "automatic-rename": automatic_rename, "window_name": window_name, "pane_id": pane_id, "pane_index": pane_index,
            "pane_pid": pane_pid, "pane_current_path": "/p", "pane_current_command": "zsh", "pane_active": pane_active,
            "window_zoomed_flag": window_zoomed_flag, "process_id_in_front": int(pane_pid) + 1}


def build_tmux_output_line(line_kind, field_names, record):
    """Returns the line tmux prints for a record (build_tmux_format's); a field the record lacks is "0"."""
    return FIELD_SEPARATOR.join([line_kind] + [record.get(field_name, "0") for field_name in field_names]) + "\n"


def describe_stand_in_process(process_id):
    return process_id, "claude --verbose", "/p/job", 1000.0


class ParsingTests(unittest.TestCase):
    def test_each_line_of_tmux_output_becomes_a_record_by_field_name(self):
        line = FIELD_SEPARATOR.join(["client", "4242", "/dev/ttys001", "$1", "@3", "1793564088", "research"])
        output = line + "\n" + FIELD_SEPARATOR.join(["pane", "too", "few"]) + "\n"
        self.assertEqual(parse_tmux_output_lines(output, "client", CLIENT_FIELD_NAMES),
                         [{"client_pid": "4242", "client_name": "/dev/ttys001", "session_id": "$1", "window_id": "@3",
                           "client_activity": "1793564088", "client_last_session": "research"}])
        self.assertEqual(parse_tmux_output_lines(output, "pane", PANE_FIELD_NAMES), [])  # a line with too few fields is left out
        self.assertEqual(build_tmux_format("client", ("client_pid", "client_name")),
                         FIELD_SEPARATOR.join(["client", "#{client_pid}", "#{client_name}"]))

    def test_a_session_is_a_window_a_tmux_window_a_tab_and_its_first_pane_the_session(self):
        pane_records = [
            make_pane_record("$1", "research", "@1", "%5", pane_index="1", pane_pid="200"),  # a split: the second pane
            make_pane_record("$1", "research", "@1", "%4", pane_index="0", pane_pid="100"),
            make_pane_record("$1", "research", "@2", "%6", window_active="0", automatic_rename="0", window_name="reviews"),
            make_pane_record("$3", "3", "@7", "%9"),  # a session tmux named itself: after the number in its ID
            make_pane_record("$4", "2026", "@8", "%10")]  # another number is a name its user gave it
        gathered_windows = build_gathered_windows(pane_records, describe_stand_in_process)
        self.assertEqual([(gathered_window["window_id"], gathered_window["title_override_format"], gathered_window["selected_tab_id"],
                           [gathered_tab["tab_id"] for gathered_tab in gathered_window["tabs"]]) for gathered_window in gathered_windows],
                         [("$1", "research", "@1", ["@1", "@2"]), ("$3", None, "@7", ["@7"]), ("$4", "2026", "@8", ["@8"])])
        first_tab, renamed_tab = gathered_windows[0]["tabs"]
        self.assertEqual((first_tab["first_session"]["session_id"], first_tab["first_session"]["job_pid"],
                          first_tab["first_session"]["command_line"], first_tab["title_override_format"]),
                         ("%4", 101, "claude --verbose", None))
        self.assertEqual(renamed_tab["title_override_format"], "reviews")  # renamed by its user
        self.assertEqual({gathered_window["terminal_name"] for gathered_window in gathered_windows}, {"tmux"})
        self.assertIsNone(gathered_windows[0]["frame"])
        self.assertIsNone(first_tab["first_session"]["status_set_in_iterm2"])

    def test_a_first_pane_that_another_pane_zoomed_to_fill_the_window_covers_is_marked_hidden(self):
        def first_pane_is_hidden(active_pane_id, window_zoomed_flag):
            pane_records = [make_pane_record("$1", "research", "@1", pane_id, pane_index, pane_active="1" if pane_id == active_pane_id else "0",
                                             window_zoomed_flag=window_zoomed_flag) for pane_id, pane_index in (("%4", "0"), ("%5", "1"))]
            return build_gathered_windows(pane_records, describe_stand_in_process)[0]["tabs"][0]["first_session"][
                "is_hidden_behind_a_zoomed_pane"]
        self.assertTrue(first_pane_is_hidden(active_pane_id="%5", window_zoomed_flag="1"))
        self.assertFalse(first_pane_is_hidden(active_pane_id="%4", window_zoomed_flag="1"))  # it is the zoomed pane itself
        self.assertFalse(first_pane_is_hidden(active_pane_id="%5", window_zoomed_flag="0"))  # both show side by side

    def test_faint_text_is_told_from_the_sgr_escapes_and_colors_numbers_are_skipped(self):
        captured = "\x1b[34mplain \x1b[2mfaint\x1b[0m\x1b[34m back \x1b[38;2;10;2;30mcolor\x1b[39m\n\x1b[2mstill\x1b[22m no\n"
        self.assertEqual([(text, "".join("f" if is_faint else "." for is_faint in faint_flags))
                          for text, faint_flags in parse_screen_lines_with_faint_flags(captured)],
                         [("plain faint back color", "......fffff..........."), ("still no", "fffff...")])
        self.assertTrue(apply_sgr_parameters_to_faintness("1;2", False))
        self.assertFalse(apply_sgr_parameters_to_faintness("", True))  # "\x1b[m" resets
        self.assertFalse(apply_sgr_parameters_to_faintness("48;5;2", False))  # the 2 is a color, not "faint": nothing changes
        self.assertFalse(apply_sgr_parameters_to_faintness("38:2::1:2:3;22", True))

    def test_hyperlinks_and_other_escapes_are_left_out_of_the_text(self):
        captured = "\x1b]8;;https://example.com\x1b\\link\x1b]8;;\x1b\\ end\n"
        self.assertEqual(parse_screen_lines_with_faint_flags(captured), [("link end", [False] * 8)])


class TypingTests(unittest.TestCase):
    def test_printable_runs_go_literally_and_control_characters_as_keys(self):
        self.assertEqual(build_send_keys_commands("%3", "\x15fg\r"), [
            ["send-keys", "-t", "%3", "C-u"], ["send-keys", "-t", "%3", "-l", "--", "fg"], ["send-keys", "-t", "%3", "Enter"]])
        self.assertEqual(build_send_keys_commands("%3", "\x1a"), [["send-keys", "-t", "%3", "C-z"]])
        self.assertEqual(join_tmux_commands([["a", "1"], ["b"]]), ["a", "1", ";", "b"])

    def test_an_argument_ending_with_a_semicolon_keeps_it(self):
        # tmux reads a final ";" as the end of a command, dropping it, unless it is written "\;".
        self.assertEqual(escape_tmux_argument("echo hi;"), "echo hi\\;")
        self.assertEqual(escape_tmux_argument("a;b"), "a;b")
        self.assertEqual(build_send_keys_commands("%3", "/compact;")[0][-1], "/compact\\;")
        # A start folder or a name, which tmux expands as a format, keeps its "#" too.
        self.assertEqual(escape_tmux_format_argument("~/C#/#S;"), "~/C##/##S\\;")


@unittest.skipIf(TMUX_PROGRAM_PATH is None, "tmux isn't installed")
class TmuxServerTests(unittest.TestCase):
    """Against a tmux server of the test's own, on a private socket, whose panes run /bin/sh."""

    def setUp(self):
        self.folder, self.socket_path, self.environment = start_a_private_tmux_server(
            self, "-s", "research", "-x", "100", "-y", "20")
        self.backend = TmuxTerminalBackend(lambda: None, lambda: None, lambda terminal_name, message: None,
                                           socket_path=self.socket_path, tmux_program_path=TMUX_PROGRAM_PATH)

    def run_tmux(self, *arguments):
        return subprocess.run([TMUX_PROGRAM_PATH, "-S", self.socket_path, *arguments], env=self.environment,
                              capture_output=True, text=True).stdout

    def gather(self):
        """Polls the server as the backend's run() does, then gathers as the core does: only while it runs."""
        asyncio.run(self.backend.poll_the_server_once())
        return asyncio.run(self.backend.gather_windows()) if self.backend.is_connected() else []

    def wait_for_screen_text(self, pane_id, text):
        return wait_until(lambda: text in self.run_tmux("capture-pane", "-p", "-t", pane_id))

    def attach_a_control_mode_client(self, session_name):
        """Attaches a client to the session as a program that runs `tmux -C attach` through pipes
        does: tmux reports no terminal for it, only the name "client-<its process ID>". Returns
        its process, which ends with the test, once tmux lists the client."""
        control_client = subprocess.Popen([TMUX_PROGRAM_PATH, "-S", self.socket_path, "-C", "attach", "-t", session_name],
                                          env=self.environment, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                          stderr=subprocess.DEVNULL)
        self.addCleanup(control_client.wait)
        self.addCleanup(control_client.kill)
        self.addCleanup(control_client.stdin.close)
        self.assertTrue(wait_until(
            lambda: f"client-{control_client.pid}" in self.run_tmux("list-clients", "-F", "#{client_name}").split()))
        return control_client

    def test_a_server_ending_leaves_no_windows(self):
        self.assertEqual([gathered_window["title"] for gathered_window in self.gather()], ["research"])
        self.assertTrue(self.backend.terminal_runs())
        self.run_tmux("kill-server")
        self.assertEqual(self.gather(), [])
        self.assertFalse(self.backend.is_connected())
        self.assertFalse(self.backend.terminal_runs())  # whether or not tmux left its socket file behind

    def test_nothing_is_typed_into_a_pane_whose_window_has_synchronized_panes(self):
        pane_id = self.gather()[0]["tabs"][0]["first_session"]["session_id"]
        self.assertTrue(self.wait_for_screen_text(pane_id, "$"))
        other_pane_id = self.run_tmux("split-window", "-d", "-P", "-F", "#{pane_id}", "-t", pane_id).strip()
        self.assertTrue(self.wait_for_screen_text(other_pane_id, "$"))
        self.run_tmux("set-window-option", "-t", pane_id, "synchronize-panes", "on")
        terminal_session = self.backend.find_session(pane_id)
        for type_or_paste in (lambda: terminal_session.send_text("echo typed\r"), lambda: terminal_session.paste_text("pasted")):
            with self.assertRaises(TerminalTypingRefused):
                asyncio.run(type_or_paste())
        time.sleep(0.2)
        for pane_id_to_read in (pane_id, other_pane_id):
            screen_text = self.run_tmux("capture-pane", "-p", "-t", pane_id_to_read)
            self.assertIn("$", screen_text)  # the pane's screen was read: it shows the shell's prompt
            self.assertNotIn("typed", screen_text)
            self.assertNotIn("pasted", screen_text)

    def test_nothing_is_typed_into_a_pane_in_copy_mode(self):
        # Copy mode would take the keys, while capture-pane still reads the program's screen.
        pane_id = self.gather()[0]["tabs"][0]["first_session"]["session_id"]
        self.assertTrue(self.wait_for_screen_text(pane_id, "$"))
        self.run_tmux("copy-mode", "-t", pane_id)
        with self.assertRaises(TerminalTypingRefused):
            asyncio.run(self.backend.find_session(pane_id).send_text("/clear\r"))
        self.assertEqual(self.run_tmux("display-message", "-p", "-t", pane_id, "#{pane_in_mode}").strip(), "1")

    def test_nothing_is_typed_into_a_pane_with_one_mode_opened_over_another(self):
        # tmux counts the pane's modes: copy mode over the tree of sessions makes two.
        pane_id = self.gather()[0]["tabs"][0]["first_session"]["session_id"]
        self.assertTrue(self.wait_for_screen_text(pane_id, "$"))
        self.run_tmux("choose-tree", "-t", pane_id)
        self.run_tmux("copy-mode", "-t", pane_id)
        self.assertEqual(self.run_tmux("display-message", "-p", "-t", pane_id, "#{pane_in_mode}").strip(), "2")
        terminal_session = self.backend.find_session(pane_id)
        for type_or_paste in (lambda: terminal_session.send_text("/clear\r"), lambda: terminal_session.paste_text("pasted")):
            with self.assertRaises(TerminalTypingRefused):
                asyncio.run(type_or_paste())

    def test_typing_into_a_pane_closed_since_the_gather_is_a_failed_request(self):
        # The server still runs its first window, and tmux's display-message doesn't fail for a pane that is gone.
        pane_id = self.run_tmux("new-window", "-d", "-P", "-F", "#{pane_id}", "-t", "research:").strip()
        self.gather()
        terminal_session = self.backend.find_session(pane_id)
        self.run_tmux("kill-pane", "-t", pane_id)
        for type_or_paste in (lambda: terminal_session.send_text("/clear\r"), lambda: terminal_session.paste_text("pasted")):
            with self.assertRaises(TerminalRequestFailed) as raised:
                asyncio.run(type_or_paste())
            self.assertNotIsInstance(raised.exception, TerminalTypingRefused)  # which would say the pane is in copy mode
            self.assertIn(pane_id, str(raised.exception))

    def test_a_session_given_a_number_as_its_name_keeps_it_as_its_users_title(self):
        # tmux names a session nobody named after the number in its ID, so only that number counts as no name.
        started = asyncio.run(self.backend.start_agent_session(None, self.folder, "true"))
        new_session_id = started.details["window_id"]

        def find_title_the_user_gave():
            return next(gathered_window["title_override_format"] for gathered_window in self.gather()
                        if gathered_window["window_id"] == new_session_id)
        self.assertIsNone(find_title_the_user_gave())  # tmux's own name for it
        self.assertTrue(asyncio.run(self.backend.rename_window(new_session_id, "2026")).succeeded)
        self.assertEqual(find_title_the_user_gave(), "2026")

    def test_a_windows_only_pane_is_typed_into_though_its_window_has_synchronized_panes(self):
        # No other pane gets its keys; and with the option on for every window, a start still types into its new window.
        gathered_window = self.gather()[0]
        pane_id = gathered_window["tabs"][0]["first_session"]["session_id"]
        self.assertTrue(self.wait_for_screen_text(pane_id, "$"))
        self.run_tmux("set-option", "-g", "-w", "synchronize-panes", "on")
        asyncio.run(self.backend.find_session(pane_id).send_text("echo typed-$((1 + 1))\r"))
        self.assertTrue(self.wait_for_screen_text(pane_id, "typed-2"))
        for window_id in (None, gathered_window["window_id"]):  # a new session, and a new window in the session there is
            started = asyncio.run(self.backend.start_agent_session(window_id, self.folder, "echo started-$((1 + 1))"))
            self.assertTrue(started.succeeded, started)
            self.assertTrue(self.wait_for_screen_text(started.details["tab_id"], "started-2"))

    def test_a_start_whose_typing_is_refused_leaves_no_window_or_session_behind(self):
        session_id = self.gather()[0]["window_id"]
        for hook_name in ("after-new-session", "after-new-window"):  # the pane of every new window is then in copy mode
            self.run_tmux("set-hook", "-g", hook_name, "copy-mode")
        for window_id in (None, session_id):  # a new session, and a new window in the session there is
            with self.assertRaises(TerminalTypingRefused):
                asyncio.run(self.backend.start_agent_session(window_id, self.folder, "true"))
        self.assertEqual(self.run_tmux("list-windows", "-a", "-F", "#{session_name}").split(), ["research"])
        self.assertEqual(self.backend.window_and_tab_ids_opened_since_the_last_gather, set())

    def test_ending_a_peek_at_another_pane_zooms_the_pane_that_was_zoomed_again(self):
        gathered_tab = self.gather()[0]["tabs"][0]
        agent_pane_id = gathered_tab["first_session"]["session_id"]
        zoomed_pane_id = self.run_tmux("split-window", "-P", "-F", "#{pane_id}", "-t", agent_pane_id).strip()  # the active pane now
        self.run_tmux("resize-pane", "-Z", "-t", zoomed_pane_id)  # it fills the window

        def read_active_pane_and_whether_the_window_is_zoomed():
            return self.run_tmux("display-message", "-p", "-t", gathered_tab["tab_id"], "#{pane_id} #{window_zoomed_flag}").strip()
        self.assertTrue(self.gather()[0]["tabs"][0]["first_session"]["is_hidden_behind_a_zoomed_pane"])  # so it isn't seen
        what_was_in_front = asyncio.run(self.backend.remember_what_is_in_front())
        asyncio.run(self.backend.focus_session(agent_pane_id))  # as a peek does: selecting the agent's pane ends the zoom
        self.assertEqual(read_active_pane_and_whether_the_window_is_zoomed(), f"{agent_pane_id} 0")
        self.assertFalse(self.gather()[0]["tabs"][0]["first_session"]["is_hidden_behind_a_zoomed_pane"])
        # The second time the window is still zoomed, as after a peek at another window: it stays so.
        for _ in range(2):
            self.assertTrue(asyncio.run(self.backend.put_back_what_was_in_front(what_was_in_front, False)).succeeded)
            self.assertEqual(read_active_pane_and_whether_the_window_is_zoomed(), f"{zoomed_pane_id} 1")

    def test_ending_a_peek_puts_back_the_window_selected_since_the_last_gather(self):
        # As one selected in tmux, or by the peek before, within the last poll: the peek asks tmux what it shows now.
        gathered_window = self.gather()[0]
        session_id, agent_tab = gathered_window["window_id"], gathered_window["tabs"][0]
        tab_id_selected_since = self.run_tmux("new-window", "-P", "-F", "#{window_id}", "-t", f"{session_id}:").strip()

        def read_selected_tab_id():
            return self.run_tmux("display-message", "-p", "-t", f"{session_id}:", "#{window_id}").strip()
        what_was_in_front = asyncio.run(self.backend.remember_what_is_in_front())
        asyncio.run(self.backend.focus_session(agent_tab["first_session"]["session_id"]))  # as a peek does
        self.assertEqual(read_selected_tab_id(), agent_tab["tab_id"])
        self.assertTrue(asyncio.run(self.backend.put_back_what_was_in_front(what_was_in_front, False)).succeeded)
        self.assertEqual(read_selected_tab_id(), tab_id_selected_since)

    def test_ending_a_peek_gives_the_session_back_its_last_window(self):
        # The one tmux's last-window command goes to: the window used before the one shown, not the one the peek showed.
        self.attach_a_control_mode_client("research")  # asked about a closed window, tmux answers for the one a client shows
        gathered_window = self.gather()[0]
        session_id, agent_tab = gathered_window["window_id"], gathered_window["tabs"][0]
        # Each new window is selected as it opens, so the first of the two is the session's last window.
        last_tab_id, selected_tab_id = (self.run_tmux("new-window", "-P", "-F", "#{window_id}", "-t", f"{session_id}:").strip()
                                        for _ in range(2))

        def read_selected_and_last_tab_ids():
            return tuple(self.run_tmux("list-windows", "-t", session_id, "-F", "#{?" + flag_name + ",#{window_id},}").strip()
                         for flag_name in ("window_active", "window_last_flag"))

        def peek_at_the_agent_tab():
            what_was_in_front = asyncio.run(self.backend.remember_what_is_in_front())
            asyncio.run(self.backend.focus_session(agent_tab["first_session"]["session_id"]))
            self.assertEqual(read_selected_and_last_tab_ids(), (agent_tab["tab_id"], selected_tab_id))
            return what_was_in_front
        self.assertEqual(read_selected_and_last_tab_ids(), (selected_tab_id, last_tab_id))
        what_was_in_front = peek_at_the_agent_tab()
        # The second time the session shows what it showed before, as after a peek at a tab of another session: nothing changes.
        for _ in range(2):
            self.assertTrue(asyncio.run(self.backend.put_back_what_was_in_front(what_was_in_front, False)).succeeded)
            self.assertEqual(read_selected_and_last_tab_ids(), (selected_tab_id, last_tab_id))
        # A last window closed during the peek is left out, and the window shown before still comes back.
        what_was_in_front = peek_at_the_agent_tab()
        self.run_tmux("kill-window", "-t", last_tab_id)
        self.assertTrue(asyncio.run(self.backend.put_back_what_was_in_front(what_was_in_front, False)).succeeded)
        self.assertEqual(read_selected_and_last_tab_ids()[0], selected_tab_id)

    def test_ending_a_peek_gives_the_window_back_its_last_pane(self):
        # The one tmux's last-pane command goes to: the pane used before the active one, not the agent's, which the peek selected.
        self.attach_a_control_mode_client("research")  # asked about a closed pane, tmux answers for the one a client shows
        gathered_tab = self.gather()[0]["tabs"][0]
        agent_pane_id = gathered_tab["first_session"]["session_id"]
        # Each new pane is the active one as it opens, so the first of the two is the window's last pane.
        last_pane_id, active_pane_id = (self.run_tmux("split-window", "-P", "-F", "#{pane_id}", "-t", agent_pane_id).strip()
                                        for _ in range(2))

        def read_active_and_last_pane_ids():
            return tuple(self.run_tmux("list-panes", "-t", gathered_tab["tab_id"], "-F", "#{?" + flag_name + ",#{pane_id},}").strip()
                         for flag_name in ("pane_active", "pane_last"))

        def peek_at_the_agent_pane():
            what_was_in_front = asyncio.run(self.backend.remember_what_is_in_front())
            asyncio.run(self.backend.focus_session(agent_pane_id))
            self.assertEqual(read_active_and_last_pane_ids(), (agent_pane_id, active_pane_id))
            return what_was_in_front
        self.assertEqual(read_active_and_last_pane_ids(), (active_pane_id, last_pane_id))
        what_was_in_front = peek_at_the_agent_pane()
        # The second time the window's panes are as they were before, as after a peek at another window: nothing changes.
        for _ in range(2):
            self.assertTrue(asyncio.run(self.backend.put_back_what_was_in_front(what_was_in_front, False)).succeeded)
            self.assertEqual(read_active_and_last_pane_ids(), (active_pane_id, last_pane_id))
        # A last pane closed during the peek is left out, and the pane that was active still comes back.
        what_was_in_front = peek_at_the_agent_pane()
        self.run_tmux("kill-pane", "-t", last_pane_id)
        self.assertTrue(asyncio.run(self.backend.put_back_what_was_in_front(what_was_in_front, False)).succeeded)
        self.assertEqual(read_active_and_last_pane_ids()[0], active_pane_id)

    def test_ending_a_peek_gives_the_client_back_its_last_session(self):
        # The one tmux's switch-client -l goes to: the session the client showed before the one it shows, not the one the peek showed.
        for session_name in ("second", "third"):
            self.run_tmux("new-session", "-d", "-s", session_name)
        # Each client is switched to another session, so the one it was attached to is its last session.
        peeking_client_name, other_client_name = (f"client-{self.attach_a_control_mode_client(session_name).pid}"
                                                  for session_name in ("third", "research"))
        self.run_tmux("switch-client", "-c", peeking_client_name, "-t", "research")
        self.run_tmux("switch-client", "-c", other_client_name, "-t", "second")

        def read_shown_and_last_session_names_by_client_name():
            return {client_name: (session_name, last_session_name) for client_name, session_name, last_session_name in (
                line.split(" ") for line in self.run_tmux(
                    "list-clients", "-F", "#{client_name} #{session_name} #{client_last_session}").splitlines())}

        def peek_at_the_second_session():
            what_was_in_front = asyncio.run(self.backend.remember_what_is_in_front())
            self.run_tmux("switch-client", "-c", peeking_client_name, "-t", "second")  # as a peek does to the client it picks
            self.assertEqual(read_shown_and_last_session_names_by_client_name()[peeking_client_name], ("second", "research"))
            return what_was_in_front
        session_names_before = {peeking_client_name: ("research", "third"), other_client_name: ("second", "research")}
        self.assertEqual(read_shown_and_last_session_names_by_client_name(), session_names_before)
        self.gather()
        what_was_in_front = peek_at_the_second_session()
        # From here on, tmux notes each session a client is switched to in an option of the test's own.
        self.run_tmux("set-hook", "-g", "client-session-changed", 'set-option -gaF @session_changes "#{hook_client}>#{session_name} "')
        self.assertTrue(asyncio.run(self.backend.put_back_what_was_in_front(what_was_in_front, False)).succeeded)
        self.assertEqual(read_shown_and_last_session_names_by_client_name(), session_names_before)
        # Only the client that the peek switched went by its last session.
        session_changes = self.run_tmux("show-options", "-gv", "@session_changes").split()
        self.assertIn(f"{peeking_client_name}>third", session_changes)
        self.assertNotIn(f"{other_client_name}>research", session_changes)
        # A last session closed during the peek is left out, and the session shown before still comes back.
        what_was_in_front = peek_at_the_second_session()
        self.run_tmux("kill-session", "-t", "third")
        self.gather()
        self.assertTrue(asyncio.run(self.backend.put_back_what_was_in_front(what_was_in_front, False)).succeeded)
        self.assertEqual(read_shown_and_last_session_names_by_client_name()[peeking_client_name][0], "research")

    def test_a_control_mode_client_without_a_terminal_is_switched_and_put_back_by_its_name(self):
        control_client = self.attach_a_control_mode_client("research")
        self.run_tmux("new-session", "-d", "-s", "second")
        self.assertTrue(wait_until(lambda: self.gather() and self.backend.client_records))
        self.assertEqual(self.run_tmux("list-clients", "-F", "#{client_tty}").strip(), "")
        second_session_id = next(gathered_window["window_id"] for gathered_window in self.backend.gathered_windows
                                 if gathered_window["title"] == "second")

        def read_name_of_the_session_the_client_shows():
            return self.run_tmux("list-clients", "-F", "#{session_name}").strip()
        what_was_in_front = asyncio.run(self.backend.remember_what_is_in_front())
        focused = asyncio.run(self.backend.focus_window(second_session_id))
        self.assertEqual((focused.succeeded, focused.details), (True, {"terminal_client_process_id": control_client.pid}))
        self.assertEqual(read_name_of_the_session_the_client_shows(), "second")
        self.assertTrue(asyncio.run(self.backend.put_back_what_was_in_front(what_was_in_front, False)).succeeded)
        self.assertEqual(read_name_of_the_session_the_client_shows(), "research")

    def test_a_tmux_program_installed_after_the_backend_was_made_is_found_once_a_server_listens(self):
        with unittest.mock.patch.object(tmux_terminal_backend, "find_program_on_path_or_in_the_usual_folders",
                                        return_value=None) as find_program:
            backend = TmuxTerminalBackend(lambda: None, lambda: None, lambda terminal_name, message: None,
                                          socket_path=self.socket_path)
            asyncio.run(backend.poll_the_server_once())
            self.assertFalse(backend.is_connected())  # a server listens, with no tmux program to ask it
            find_program.return_value = TMUX_PROGRAM_PATH  # installed now
            asyncio.run(backend.poll_the_server_once())
        self.assertTrue(backend.is_connected())
        self.assertEqual([gathered_window["title"] for gathered_window in asyncio.run(backend.gather_windows())], ["research"])

    def test_a_text_editor_the_job_opens_in_its_own_group_is_the_program_in_front(self):
        # As Claude Code's Ctrl+G opens one: the terminal's foreground group still names the job
        # (here sh -c, which `; true` keeps from running vi in its place). The job is gathered while it waits for a
        # line, and its window keeps its name (tmux renames a new window after its program half a second after it
        # opens: a change a poll would notice too), so that the editor opening is the only change left to notice.
        pane_id = self.gather()[0]["tabs"][0]["first_session"]["session_id"]
        self.run_tmux("set-option", "-w", "-t", pane_id, "automatic-rename", "off")
        self.assertTrue(self.wait_for_screen_text(pane_id, "$"))
        self.run_tmux("send-keys", "-t", pane_id, "sh -c 'echo waits-$((1 + 1)); read line; vi -u NONE -n prompt.md; true'", "Enter")
        self.addCleanup(self.run_tmux, "send-keys", "-t", pane_id, "Escape", ":q!", "Enter")
        self.assertTrue(self.wait_for_screen_text(pane_id, "waits-2"))
        self.assertTrue(self.gather()[0]["tabs"][0]["first_session"]["command_line"].startswith("sh -c "))  # the waiting job
        regather_requests = []
        self.backend.request_regather = lambda: regather_requests.append(True)

        def a_poll_asks_for_a_regather():
            asyncio.run(self.backend.poll_the_server_once())
            return bool(regather_requests)
        self.assertFalse(a_poll_asks_for_a_regather())  # nothing has changed since the gather
        self.run_tmux("send-keys", "-t", pane_id, "Enter")  # the line the job waits for
        self.assertTrue(wait_until(a_poll_asks_for_a_regather))
        self.assertEqual(asyncio.run(self.backend.gather_windows())[0]["tabs"][0]["first_session"]["command_line"],
                         "vi -u NONE -n prompt.md")

    def test_typing_pasting_and_reading_the_screen(self):
        pane_id = self.gather()[0]["tabs"][0]["first_session"]["session_id"]
        terminal_session = self.backend.find_session(pane_id)
        self.assertTrue(self.wait_for_screen_text(pane_id, "$"))
        asyncio.run(terminal_session.send_text("printf 'a \\033[2mdim\\033[22m b;'; echo\r"))
        self.assertTrue(self.wait_for_screen_text(pane_id, "a dim b;"))
        line_and_faint_flags = next(line for line in asyncio.run(terminal_session.read_screen_lines_with_faint_flags())
                                    if line[0].startswith("a dim"))
        self.assertEqual(line_and_faint_flags[1][:6], [False, False, True, True, True, False])
        asyncio.run(terminal_session.paste_text("echo pasted\n"))  # /bin/sh asks for no bracketed paste: plain text
        self.assertTrue(self.wait_for_screen_text(pane_id, "\npasted"))
        self.assertIn("pasted", "\n".join(asyncio.run(terminal_session.read_screen_lines())))
        self.assertIsNone(self.backend.find_session("%999"))

    def test_renaming_moving_starting_and_closing(self):
        gathered_window = self.gather()[0]
        session_id, first_tab_id = gathered_window["window_id"], gathered_window["tabs"][0]["tab_id"]
        started = asyncio.run(self.backend.start_agent_session(session_id, self.folder, "echo started-$((1 + 1))",
                                                               after_tab_id=first_tab_id))
        self.assertTrue(started.succeeded, started)
        gathered_window = self.gather()[0]
        second_tab_id = gathered_window["tabs"][1]["tab_id"]
        self.assertEqual(started.details, {"tab_id": second_tab_id})  # for a restored session's name
        self.assertTrue(self.wait_for_screen_text(gathered_window["tabs"][1]["first_session"]["session_id"], "started-2"))
        self.assertTrue(asyncio.run(self.backend.rename_tab(second_tab_id, "reviews;")).succeeded)
        self.assertTrue(asyncio.run(self.backend.rename_window(session_id, "renamed")).succeeded)
        gathered_window = self.gather()[0]
        self.assertEqual((gathered_window["title_override_format"], gathered_window["tabs"][1]["title_override_format"]),
                         ("renamed", "reviews;"))
        self.assertFalse(asyncio.run(self.backend.rename_window(session_id, " ")).succeeded)  # a session needs a name
        self.assertTrue(asyncio.run(self.backend.rename_tab(second_tab_id, "")).succeeded)  # back to tmux's own name
        self.assertIsNone(self.gather()[0]["tabs"][1]["title_override_format"])
        self.assertTrue(asyncio.run(self.backend.move_tab(second_tab_id, session_id, first_tab_id)).succeeded)
        self.assertEqual([gathered_tab["tab_id"] for gathered_tab in self.gather()[0]["tabs"]], [second_tab_id, first_tab_id])
        moved = asyncio.run(self.backend.move_tab_to_new_window(second_tab_id))
        self.assertTrue(moved.succeeded, moved)
        gathered_windows = self.gather()
        self.assertEqual(sorted([gathered_tab["tab_id"] for gathered_tab in gathered_window["tabs"]] for gathered_window in gathered_windows),
                         sorted([[first_tab_id], [second_tab_id]]))
        self.assertFalse(asyncio.run(self.backend.move_tab_to_new_window(first_tab_id)).succeeded)  # its window's only tab
        # A new tab goes last, also where the tab moved away left a free index before the others.
        appended = asyncio.run(self.backend.start_agent_session(session_id, self.folder, "echo appended"))
        self.assertTrue(appended.succeeded, appended)
        self.assertEqual(next([gathered_tab["tab_id"] for gathered_tab in gathered_window["tabs"]]
                              for gathered_window in self.gather() if gathered_window["window_id"] == session_id),
                         [first_tab_id, appended.details["tab_id"]])
        self.assertTrue(asyncio.run(self.backend.close_window(moved.details["window_id"])).succeeded)
        self.assertEqual([gathered_window["window_id"] for gathered_window in self.gather()], [session_id])

    def test_a_new_session_and_tab_can_be_renamed_before_a_gather_lists_them_and_tell_when_they_were_opened_after_it(self):
        # As a restore gives a restored session's tab and window their saved names.
        started = asyncio.run(self.backend.start_agent_session(None, self.folder, "true"))
        self.assertTrue(started.succeeded, started)
        new_session_id = started.details["window_id"]
        added = asyncio.run(self.backend.start_agent_session(new_session_id, self.folder, "true"))
        self.assertTrue(added.succeeded, added)
        for tab_id in (started.details["tab_id"], added.details["tab_id"]):
            self.assertTrue(asyncio.run(self.backend.rename_tab(tab_id, f"tab {tab_id}")).succeeded)
        self.assertTrue(asyncio.run(self.backend.rename_window(new_session_id, "restored")).succeeded)
        self.assertIsNone(self.backend.find_when_the_window_was_opened(new_session_id))  # a gather's records tell it
        gathered_window = next(gathered_window for gathered_window in self.gather() if gathered_window["window_id"] == new_session_id)
        self.assertEqual((gathered_window["title"], [gathered_tab["title"] for gathered_tab in gathered_window["tabs"]]),
                         ("restored", [f"tab {started.details['tab_id']}", f"tab {added.details['tab_id']}"]))
        self.assertEqual(self.backend.find_when_the_window_was_opened(new_session_id),
                         int(self.run_tmux("display-message", "-p", "-t", new_session_id, "#{session_created}")))
        self.assertIsNone(self.backend.find_when_the_window_was_opened("$999"))

    def test_a_hash_sign_in_a_folder_or_a_title_stays_as_it_is(self):
        # tmux expands formats ("#S", "#{...}") in a start folder and in window and session names.
        folder = os.path.join(self.folder, "C#Projects")
        os.mkdir(folder)
        session_id = self.gather()[0]["window_id"]
        started = asyncio.run(self.backend.start_agent_session(None, folder, "true"))
        self.assertTrue(started.succeeded, started)
        new_session_id = started.details["window_id"]
        self.assertEqual(os.path.realpath(self.run_tmux("display-message", "-p", "-t", new_session_id, "#{pane_current_path}").strip()),
                         os.path.realpath(folder))
        tab_id = next(gathered_window["tabs"][0]["tab_id"] for gathered_window in self.gather()
                      if gathered_window["window_id"] == session_id)
        self.assertTrue(asyncio.run(self.backend.rename_tab(tab_id, "Fix ##12 #S")).succeeded)
        self.assertTrue(asyncio.run(self.backend.rename_window(session_id, "C# work")).succeeded)
        gathered_window = next(gathered_window for gathered_window in self.gather() if gathered_window["window_id"] == session_id)
        self.assertEqual((gathered_window["title"], gathered_window["tabs"][0]["title"]), ("C# work", "Fix ##12 #S"))

    def test_messages_pasted_into_two_panes_at_once_each_reach_their_own_pane(self):
        first_pane_id = self.gather()[0]["tabs"][0]["first_session"]["session_id"]
        second_pane_id = self.run_tmux("split-window", "-d", "-P", "-F", "#{pane_id}", "-t", first_pane_id).strip()
        self.gather()
        for pane_id in (first_pane_id, second_pane_id):
            self.assertTrue(self.wait_for_screen_text(pane_id, "$"))

        async def paste_into_both(round_number):
            await asyncio.gather(*(TmuxTerminalSession(self.backend, pane_id).paste_text(f"echo {name}-{round_number}\n")
                                   for name, pane_id in (("first", first_pane_id), ("second", second_pane_id))))
        for round_number in range(10):
            asyncio.run(paste_into_both(round_number))
            # Each round's message reaches its own pane, before the next round can push it out of sight.
            self.assertTrue(self.wait_for_screen_text(first_pane_id, f"first-{round_number}"), round_number)
            self.assertTrue(self.wait_for_screen_text(second_pane_id, f"second-{round_number}"), round_number)
        # Read with their history (-S -), where the first rounds have scrolled to by now.
        first_pane_text, second_pane_text = (self.run_tmux("capture-pane", "-p", "-S", "-", "-t", pane_id)
                                             for pane_id in (first_pane_id, second_pane_id))
        self.assertIn("first-0", first_pane_text)
        self.assertNotIn("second-", first_pane_text)
        self.assertIn("second-0", second_pane_text)
        self.assertNotIn("first-", second_pane_text)

    def test_a_new_session_and_focus_without_a_client(self):
        started = asyncio.run(self.backend.start_agent_session(None, self.folder, "echo hello"))
        self.assertTrue(started.succeeded, started)
        self.assertNotIn("terminal_client_process_id", started.details)  # no client shows it
        self.assertEqual(len(self.gather()), 2)
        new_gathered_window = next(gathered_window for gathered_window in self.gather()
                                   if gathered_window["window_id"] == started.details["window_id"])
        self.assertEqual(started.details["tab_id"], new_gathered_window["tabs"][0]["tab_id"])
        focused = asyncio.run(self.backend.focus_window(started.details["window_id"]))
        self.assertFalse(focused.succeeded)
        self.assertIn("tmux attach -t", focused.message)
        self.assertEqual(self.backend.list_clients(), [])
        self.assertFalse(asyncio.run(self.backend.move_window_to_monitor(started.details["window_id"], [], "m")).succeeded)


class PeekTests(unittest.TestCase):
    def test_ending_a_peek_switches_the_client_back_though_the_cache_hasnt_seen_the_switch(self):
        pane_records = [make_pane_record("$1", "research", "@1", "%1", pane_active="0"),
                        make_pane_record("$1", "research", "@1", "%5", pane_index="1"),  # the user's editor beside the agent's pane %1
                        make_pane_record("$2", "second", "@2", "%2")]
        backend, sent_commands = make_backend_that_records_tmux_commands("".join(
            [build_tmux_output_line("pane", PANE_FIELD_NAMES, pane_record) for pane_record in pane_records]
            + [build_tmux_output_line("client", CLIENT_FIELD_NAMES, make_client_record())]))
        backend.gathered_windows = build_gathered_windows(pane_records, describe_stand_in_process)
        backend.client_records = [make_client_record()]
        what_was_in_front = asyncio.run(backend.remember_what_is_in_front())
        self.assertTrue(asyncio.run(backend.focus_session("%2")).succeeded)  # switches the client to $2
        sent_commands.clear()
        self.assertTrue(asyncio.run(backend.put_back_what_was_in_front(what_was_in_front, False)).succeeded)
        self.assertEqual(sent_commands, [join_tmux_commands([["select-pane", "-t", "%5"], ["select-pane", "-t", "%2"],
                                                             ["select-window", "-t", "@1"], ["select-window", "-t", "@2"],
                                                             ["switch-client", "-c", "/dev/ttys1", "-t", "$1"]])])

    def test_a_peek_while_tmux_cant_be_asked_puts_nothing_back(self):
        backend = make_backend_without_a_server()  # as when the server ended since the last poll
        what_was_in_front = asyncio.run(backend.remember_what_is_in_front())
        self.assertIsNone(what_was_in_front)
        self.assertTrue(asyncio.run(backend.put_back_what_was_in_front(what_was_in_front, False)).succeeded)

    def test_showing_a_session_switches_the_client_though_the_cache_says_it_shows_it(self):
        # An earlier focus switched the client away within the last poll.
        backend, sent_commands = make_backend_that_records_tmux_commands()
        backend.client_records = [make_client_record()]
        self.assertEqual(asyncio.run(backend.show_session_in_a_client("$1")), 11)
        self.assertEqual(sent_commands, [["switch-client", "-c", "/dev/ttys1", "-t", "$1"]])


def make_backend_without_a_server(socket_path="/nonexistent/s"):
    return TmuxTerminalBackend(lambda: None, lambda: None, lambda terminal_name, message: None,
                               socket_path=socket_path, tmux_program_path="/nonexistent/tmux")


def make_backend_that_records_tmux_commands(tmux_output=""):
    """Returns (a backend without a server, the list each tmux command it runs is added to);
    each of its tmux runs prints tmux_output."""
    backend = make_backend_without_a_server()
    sent_commands = []

    async def run_tmux(arguments, input_text=None):
        sent_commands.append(arguments)
        return tmux_output
    backend.run_tmux = run_tmux
    return backend, sent_commands


def make_client_record(client_pid="11", client_name="/dev/ttys1", client_activity="1"):
    """Returns the record of a tmux client that shows window @1 of session $1, and showed no session before it."""
    return {"client_pid": client_pid, "client_name": client_name, "session_id": "$1", "window_id": "@1",
            "client_activity": client_activity, "client_last_session": ""}


class GatherTests(unittest.TestCase):
    def test_a_tab_opened_while_a_gather_runs_stays_known_until_a_gather_lists_it(self):
        backend = make_backend_without_a_server()
        backend.window_and_tab_ids_opened_since_the_last_gather = {"$5"}  # opened before: its list-panes lists it
        pane_records_of_the_server = [make_pane_record("$5", "restored", "@8", "%8")]

        async def run_tmux(arguments, input_text=None):
            output = "".join(build_tmux_output_line("pane", PANE_FIELD_NAMES, pane_record) for pane_record in pane_records_of_the_server)
            if len(pane_records_of_the_server) == 1:  # a restore opens tab @9 just after tmux listed the panes for the first gather
                pane_records_of_the_server.append(make_pane_record("$5", "restored", "@9", "%9"))
                backend.window_and_tab_ids_opened_since_the_last_gather.add("@9")
            return output
        backend.run_tmux = run_tmux

        def gather_and_list_tab_ids_by_window():
            return [(gathered_window["window_id"], [gathered_tab["tab_id"] for gathered_tab in gathered_window["tabs"]])
                    for gathered_window in asyncio.run(backend.gather_windows())]
        # No real process is read for the process IDs of the stand-in panes.
        with unittest.mock.patch.object(tmux_terminal_backend, "find_process_in_front_of_the_pane", return_value=None):
            self.assertEqual(gather_and_list_tab_ids_by_window(), [("$5", ["@8"])])
            self.assertEqual(backend.window_and_tab_ids_opened_since_the_last_gather, {"@9"})  # $5 is listed now, @9 not yet
            self.assertEqual(gather_and_list_tab_ids_by_window(), [("$5", ["@8", "@9"])])
            self.assertEqual(backend.window_and_tab_ids_opened_since_the_last_gather, set())  # @9 is listed now too


class ClientTests(unittest.TestCase):
    def test_clients_are_read_the_most_recently_used_first(self):
        backend = make_backend_without_a_server()
        backend.server_is_running = True

        async def run_tmux(arguments, input_text=None):
            return "".join(build_tmux_output_line("client", CLIENT_FIELD_NAMES,
                                                  make_client_record(process_id, f"/dev/ttys{process_id}", activity))
                           for process_id, activity in (("11", "100"), ("12", "300"), ("13", "200")))
        backend.run_tmux = run_tmux
        _, client_records = asyncio.run(backend.read_pane_and_client_records())
        self.assertEqual([client_record["client_pid"] for client_record in client_records], ["12", "13", "11"])

    def test_a_client_without_a_process_id_yet_is_left_out(self):
        backend = make_backend_without_a_server()
        backend.client_records = [make_client_record(client_activity="200"),
                                  make_client_record("", "/dev/ttys2", "100")]  # a client still starting
        self.assertEqual(backend.list_clients(), [TerminalClient(11, "@1")])

    def test_a_socket_without_a_server_is_no_tmux_running(self):
        with tempfile.TemporaryDirectory() as folder:
            socket_path = os.path.join(folder, "s")
            backend = make_backend_without_a_server(socket_path)
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listening_socket:  # stands in for a tmux server
                listening_socket.bind(socket_path)
                listening_socket.listen()
                self.assertTrue(backend.terminal_runs())
            self.assertTrue(os.path.exists(socket_path))  # closed, the socket leaves its file, as a tmux server that crashed does
            self.assertFalse(backend.terminal_runs())
        self.assertFalse(make_backend_without_a_server().terminal_runs())  # no file at all

    def test_a_tmux_program_gone_since_start_up_reads_as_no_server(self):
        backend = make_backend_without_a_server()
        backend.server_is_running = True  # the last poll found it
        self.assertIsNone(asyncio.run(backend.read_pane_and_client_records()))


if __name__ == "__main__":
    unittest.main()
