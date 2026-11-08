"""The tmux terminal backend: its parsing of tmux's output and the keys it types (pure), and its
actions against a real tmux server of its own on a private socket (skipped where tmux isn't installed)."""

import asyncio
import os
import shutil
import subprocess
import tempfile
import time
import unittest

from agent_command_tab.settings_store import find_program_on_path_or_in_the_usual_folders
from agent_command_tab.terminal_backends.base import TerminalClient, TerminalTypingRefused
from agent_command_tab.terminal_backends.tmux_terminal_backend import (
    CLIENT_FIELD_NAMES, FIELD_SEPARATOR, PANE_FIELD_NAMES, TmuxTerminalBackend, TmuxTerminalSession,
    apply_sgr_parameters_to_faintness, build_gathered_windows, build_send_keys_commands, build_tmux_format,
    escape_tmux_argument, escape_tmux_format_argument, join_tmux_commands, parse_screen_lines_with_faint_flags,
    parse_tmux_output_lines)

TMUX_PROGRAM_PATH = find_program_on_path_or_in_the_usual_folders("tmux")  # as the backend finds it
SECONDS_TO_WAIT_FOR_A_PANE = 5


def make_pane_record(session_id, session_name, window_id, pane_id, pane_index="0", window_active="1",
                     automatic_rename="1", window_name="zsh", pane_pid="100"):
    return {"session_id": session_id, "session_name": session_name, "window_id": window_id, "window_active": window_active,
            "automatic-rename": automatic_rename, "window_name": window_name, "pane_id": pane_id, "pane_index": pane_index,
            "pane_pid": pane_pid, "pane_current_path": "/p", "pane_current_command": "zsh", "pane_active": "1",
            "process_id_in_front": int(pane_pid) + 1}


def describe_stand_in_process(process_id):
    return process_id, "claude --verbose", "/p/job", 1000.0


class ParsingTests(unittest.TestCase):
    def test_each_line_of_tmux_output_becomes_a_record_by_field_name(self):
        line = FIELD_SEPARATOR.join(["client", "4242", "/dev/ttys001", "$1", "@3", "1793564088"])
        output = line + "\n" + FIELD_SEPARATOR.join(["pane", "too", "few"]) + "\n"
        self.assertEqual(parse_tmux_output_lines(output, "client", CLIENT_FIELD_NAMES),
                         [{"client_pid": "4242", "client_tty": "/dev/ttys001", "session_id": "$1", "window_id": "@3",
                           "client_activity": "1793564088"}])
        self.assertEqual(parse_tmux_output_lines(output, "pane", PANE_FIELD_NAMES), [])  # a line with too few fields is left out
        self.assertEqual(build_tmux_format("client", ("client_pid", "client_tty")),
                         FIELD_SEPARATOR.join(["client", "#{client_pid}", "#{client_tty}"]))

    def test_a_session_is_a_window_a_tmux_window_a_tab_and_its_first_pane_the_session(self):
        pane_records = [
            make_pane_record("$1", "research", "@1", "%5", pane_index="1", pane_pid="200"),  # a split: the second pane
            make_pane_record("$1", "research", "@1", "%4", pane_index="0", pane_pid="100"),
            make_pane_record("$1", "research", "@2", "%6", window_active="0", automatic_rename="0", window_name="reviews"),
            make_pane_record("$2", "3", "@7", "%9")]  # a session tmux named itself
        gathered_windows = build_gathered_windows(pane_records, describe_stand_in_process)
        self.assertEqual([(gathered_window["window_id"], gathered_window["title_override_format"], gathered_window["selected_tab_id"],
                           [gathered_tab["tab_id"] for gathered_tab in gathered_window["tabs"]]) for gathered_window in gathered_windows],
                         [("$1", "research", "@1", ["@1", "@2"]), ("$2", None, "@7", ["@7"])])
        first_tab, renamed_tab = gathered_windows[0]["tabs"]
        self.assertEqual((first_tab["first_session"]["session_id"], first_tab["first_session"]["job_pid"],
                          first_tab["first_session"]["command_line"], first_tab["title_override_format"]),
                         ("%4", 101, "claude --verbose", None))
        self.assertEqual(renamed_tab["title_override_format"], "reviews")  # renamed by its user
        self.assertEqual({gathered_window["terminal_name"] for gathered_window in gathered_windows}, {"tmux"})
        self.assertIsNone(gathered_windows[0]["frame"])
        self.assertIsNone(first_tab["first_session"]["status_set_in_iterm2"])

    def test_faint_text_is_told_from_the_sgr_escapes_and_colors_numbers_are_skipped(self):
        captured = "\x1b[34mplain \x1b[2mfaint\x1b[0m\x1b[34m back \x1b[38;2;10;2;30mcolor\x1b[39m\n\x1b[2mstill\x1b[22m no\n"
        self.assertEqual([(text, "".join("f" if is_faint else "." for is_faint in faint_flags))
                          for text, faint_flags in parse_screen_lines_with_faint_flags(captured)],
                         [("plain faint back color", "......fffff..........."), ("still no", "fffff...")])
        self.assertTrue(apply_sgr_parameters_to_faintness("1;2", False))
        self.assertFalse(apply_sgr_parameters_to_faintness("", True))  # "\x1b[m" resets
        self.assertTrue(apply_sgr_parameters_to_faintness("48;5;2", True))  # the 2 is a color, nothing changes
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
        self.folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.folder, True)
        self.socket_path = os.path.join(self.folder, "s")
        self.environment = {"PATH": "/usr/bin:/bin", "HOME": self.folder, "SHELL": "/bin/sh", "TERM": "xterm-256color",
                            "LANG": "en_US.UTF-8"}
        self.run_tmux("-f", "/dev/null", "new-session", "-d", "-s", "research", "-x", "100", "-y", "20", "-c", self.folder)
        self.addCleanup(self.run_tmux, "kill-server")
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
        deadline = time.time() + SECONDS_TO_WAIT_FOR_A_PANE
        while time.time() < deadline:
            if text in self.run_tmux("capture-pane", "-p", "-t", pane_id):
                return True
            time.sleep(0.05)
        return False

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
        self.run_tmux("split-window", "-d", "-t", pane_id)
        self.run_tmux("set-window-option", "-t", pane_id, "synchronize-panes", "on")
        terminal_session = self.backend.find_session(pane_id)
        for type_or_paste in (lambda: terminal_session.send_text("echo typed\r"), lambda: terminal_session.paste_text("pasted")):
            with self.assertRaises(TerminalTypingRefused):
                asyncio.run(type_or_paste())
        time.sleep(0.2)
        self.assertNotIn("typed", self.run_tmux("capture-pane", "-p", "-t", pane_id))

    def test_nothing_is_typed_into_a_pane_in_copy_mode(self):
        # Copy mode would take the keys, while capture-pane still reads the program's screen.
        pane_id = self.gather()[0]["tabs"][0]["first_session"]["session_id"]
        self.assertTrue(self.wait_for_screen_text(pane_id, "$"))
        self.run_tmux("copy-mode", "-t", pane_id)
        with self.assertRaises(TerminalTypingRefused):
            asyncio.run(self.backend.find_session(pane_id).send_text("/clear\r"))
        self.assertEqual(self.run_tmux("display-message", "-p", "-t", pane_id, "#{pane_in_mode}").strip(), "1")

    def test_a_text_editor_the_job_opens_in_its_own_group_is_the_program_in_front(self):
        # As Claude Code's Ctrl+G opens one: the terminal's foreground group still names the job
        # (here sh -c, which `; true` keeps from running vi in its place).
        pane_id = self.gather()[0]["tabs"][0]["first_session"]["session_id"]
        self.assertTrue(self.wait_for_screen_text(pane_id, "$"))
        regather_requests = []
        self.backend.request_regather = lambda: regather_requests.append(True)
        self.run_tmux("send-keys", "-t", pane_id, "sh -c 'vi -u NONE -n prompt.md; true'", "Enter")
        self.addCleanup(self.run_tmux, "send-keys", "-t", pane_id, "Escape", ":q!", "Enter")
        command_line_in_front = ""
        deadline = time.time() + SECONDS_TO_WAIT_FOR_A_PANE
        while not command_line_in_front.startswith("vi ") and time.time() < deadline:
            asyncio.run(self.backend.poll_the_server_once())
            if regather_requests:  # the poll noticed a change, as the core would be told
                regather_requests.clear()
                command_line_in_front = asyncio.run(self.backend.gather_windows())[0]["tabs"][0]["first_session"]["command_line"]
            time.sleep(0.05)
        self.assertEqual(command_line_in_front, "vi -u NONE -n prompt.md")

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
        self.assertEqual(started.details, {"tab_id": second_tab_id})  # for a restored session's name (user request, 2026-11-06)
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
        self.assertTrue(asyncio.run(self.backend.close_window(moved.details["window_id"])).succeeded)
        self.assertEqual([gathered_window["window_id"] for gathered_window in self.gather()], [session_id])

    def test_a_new_session_and_tab_can_be_renamed_and_tell_when_they_were_opened_before_a_gather_lists_them(self):
        # As a restore gives a restored session's tab and window their saved names.
        started = asyncio.run(self.backend.start_agent_session(None, self.folder, "true"))
        self.assertTrue(started.succeeded, started)
        new_session_id = started.details["window_id"]
        added = asyncio.run(self.backend.start_agent_session(new_session_id, self.folder, "true"))
        self.assertTrue(added.succeeded, added)
        for tab_id in (started.details["tab_id"], added.details["tab_id"]):
            self.assertTrue(asyncio.run(self.backend.rename_tab(tab_id, f"tab {tab_id}")).succeeded)
        self.assertTrue(asyncio.run(self.backend.rename_window(new_session_id, "restored")).succeeded)
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
        self.assertTrue(self.wait_for_screen_text(second_pane_id, "second-9"))
        self.assertNotIn("second-", self.run_tmux("capture-pane", "-p", "-t", first_pane_id))
        self.assertNotIn("first-", self.run_tmux("capture-pane", "-p", "-t", second_pane_id))

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
        backend = make_backend_without_a_server()
        backend.active_pane_id_by_tab_id = {"@1": "%5"}  # the user's editor beside the agent's pane %1
        backend.gathered_windows = build_gathered_windows([make_pane_record("$1", "research", "@1", "%1"),
                                                           make_pane_record("$2", "second", "@2", "%2")], describe_stand_in_process)
        backend.client_records = [{"client_pid": "11", "client_tty": "/dev/ttys1", "session_id": "$1", "window_id": "@1",
                                   "client_activity": "1"}]
        sent_commands = []

        async def run_tmux(arguments, input_text=None):
            sent_commands.append(arguments)
            return ""
        backend.run_tmux = run_tmux
        what_was_in_front = backend.remember_what_is_in_front()
        self.assertTrue(asyncio.run(backend.focus_session("%2")).succeeded)  # switches the client to $2
        sent_commands.clear()
        self.assertTrue(asyncio.run(backend.put_back_what_was_in_front(what_was_in_front, False)).succeeded)
        self.assertEqual(sent_commands, [join_tmux_commands([["select-pane", "-t", "%5"], ["select-window", "-t", "@1"],
                                                             ["select-window", "-t", "@2"],
                                                             ["switch-client", "-c", "/dev/ttys1", "-t", "$1"]])])

    def test_showing_a_session_switches_the_client_though_the_cache_says_it_shows_it(self):
        # An earlier focus switched the client away within the last poll.
        backend = make_backend_without_a_server()
        backend.client_records = [{"client_pid": "11", "client_tty": "/dev/ttys1", "session_id": "$1", "window_id": "@1",
                                   "client_activity": "1"}]
        sent_commands = []

        async def run_tmux(arguments, input_text=None):
            sent_commands.append(arguments)
            return ""
        backend.run_tmux = run_tmux
        self.assertEqual(asyncio.run(backend.show_session_in_a_client("$1")), 11)
        self.assertEqual(sent_commands, [["switch-client", "-c", "/dev/ttys1", "-t", "$1"]])


def make_backend_without_a_server():
    return TmuxTerminalBackend(lambda: None, lambda: None, lambda terminal_name, message: None,
                               socket_path="/nonexistent/s", tmux_program_path="/nonexistent/tmux")


class GatherTests(unittest.TestCase):
    def test_a_tab_opened_while_a_gather_runs_stays_known_until_a_gather_lists_it(self):
        backend = make_backend_without_a_server()
        backend.window_and_tab_ids_opened_since_the_last_gather = {"$5"}  # opened before: its list-panes lists it

        async def run_tmux(arguments, input_text=None):
            backend.window_and_tab_ids_opened_since_the_last_gather.add("@9")  # a restore's new tab, opened meanwhile
            return ""
        backend.run_tmux = run_tmux
        asyncio.run(backend.gather_windows())
        self.assertEqual(backend.window_and_tab_ids_opened_since_the_last_gather, {"@9"})


class ClientTests(unittest.TestCase):
    def test_clients_are_read_the_most_recently_used_first(self):
        backend = make_backend_without_a_server()
        backend.server_is_running = True

        async def run_tmux(arguments, input_text=None):
            return "".join(FIELD_SEPARATOR.join(["client", process_id, f"/dev/ttys{process_id}", "$1", "@1", activity]) + "\n"
                           for process_id, activity in (("11", "100"), ("12", "300"), ("13", "200")))
        backend.run_tmux = run_tmux
        _, client_records = asyncio.run(backend.read_pane_and_client_records())
        self.assertEqual([client_record["client_pid"] for client_record in client_records], ["12", "13", "11"])

    def test_a_client_without_a_process_id_yet_is_left_out(self):
        backend = make_backend_without_a_server()
        backend.client_records = [{"client_pid": "11", "client_tty": "/dev/ttys1", "session_id": "$1", "window_id": "@1",
                                   "client_activity": "200"},
                                  {"client_pid": "", "client_tty": "/dev/ttys2", "session_id": "$1", "window_id": "@1",
                                   "client_activity": "100"}]  # a client still starting
        self.assertEqual(backend.list_clients(), [TerminalClient(11, "@1")])

    def test_a_socket_without_a_server_is_no_tmux_running(self):
        self.assertFalse(make_backend_without_a_server().terminal_runs())

    def test_a_tmux_program_gone_since_start_up_reads_as_no_server(self):
        backend = make_backend_without_a_server()
        backend.server_is_running = True  # the last poll found it
        self.assertIsNone(asyncio.run(backend.read_pane_and_client_records()))


if __name__ == "__main__":
    unittest.main()
