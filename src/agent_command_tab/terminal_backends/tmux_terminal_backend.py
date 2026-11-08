"""tmux behind the terminal layer: the agents running in tmux, in any
terminal app, listed next to iTerm2's.

A tmux session is a window row, a tmux window is a tab row, and the window's first pane is its
session (terminal_backends.base). The default tmux server (its socket in $TMUX_TMPDIR or /tmp) is
polled every TMUX_POLL_INTERVAL_SECONDS with one `tmux list-panes -a` and `list-clients` run:
tmux pushes nothing to a program that isn't one of its clients. A change in the panes asks for a
regather, a change in the clients for a snapshot. Each pane's program in front comes from the
kernel (the foreground process group of its terminal, or a text editor its job opened in that
group), as iTerm2's jobPid does.

tmux reports no agent status, so the core takes each agent's own report instead (Claude Code's
session file; for Codex the turn timer on its screen). A tmux window shows only through a client
(`tmux attach`) running inside a terminal app, so bringing a tab to the front selects it in tmux,
switches a client to its session, and leaves bringing that client's terminal to the front to the
core (an iTerm2 tab) or the app (any other terminal app).

Needs tmux 3.2 or later (move-window -a and -b).
"""

import asyncio
import os
import re
import shlex

from agent_command_tab.agent_process_tree import (find_foreground_job_process_id, find_text_editor_opened_by_the_job,
                                                  read_process_arguments_and_environment, read_process_current_folder,
                                                  read_process_info)
from agent_command_tab.dashboard_core_api import ActionResult
from agent_command_tab.settings_store import find_program_on_path_or_in_the_usual_folders
from agent_command_tab.terminal_backends.base import (TerminalBackend, TerminalClient, TerminalRequestFailed,
                                                      TerminalSession, TerminalTypingRefused)
from agent_command_tab.unix_socket_listeners import another_process_listens_at

TMUX_TERMINAL_NAME = "tmux"
TMUX_POLL_INTERVAL_SECONDS = 1
TMUX_COMMAND_TIMEOUT_SECONDS = 5
FIELD_SEPARATOR = "\x1f"  # between the fields of one line of tmux's output: names can hold tabs, never this
# pane_current_command changes when the program in front of the pane runs another in its place
# (`exec claude` in the shell), which keeps its process ID (parse_pane_records_with_the_process_in_front
# notices the other changes); the poll must notice it even in a renamed window. pane_active marks
# the pane each window's keys go to, which a peek puts back.
PANE_FIELD_NAMES = ("session_id", "session_name", "session_created", "window_id", "window_active", "automatic-rename",
                    "window_name", "pane_id", "pane_index", "pane_pid", "pane_current_path", "pane_current_command", "pane_active")
CLIENT_FIELD_NAMES = ("client_pid", "client_tty", "session_id", "window_id", "client_activity")
# Followed by the pane's number: one buffer per pane, since tmux's buffers are the server's, and
# messages for several panes are pasted at the same time (a pane gets one paste at a time).
PASTE_BUFFER_NAME_START = "agent-command-tab-paste-"
# The key tmux's send-keys names for each control character that typing uses; any other control
# character from Ctrl+A to Ctrl+Z is named C-<letter>.
TMUX_KEY_NAME_BY_CONTROL_CHARACTER = {"\r": "Enter", "\t": "Tab", "\x1b": "Escape", "\x7f": "BSpace"}
CONTROL_CHARACTER_PATTERN = re.compile(r"[\x00-\x1f\x7f]")
# A control sequence in capture-pane -e's output: its parameters and final letter (an SGR when "m");
# or an operating system command (a hyperlink, say), or another escape, both skipped.
ESCAPE_SEQUENCE_PATTERN = re.compile(r"\x1b\[([0-9;:?<=>]*)[ -/]*([@-~])|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[^\[\]]")
FAINT_SGR_PARAMETER = 2
NORMAL_INTENSITY_SGR_PARAMETER = 22  # ends bold and faint
# SGR parameters followed by a color (38;5;n or 38;2;r;g;b), whose numbers aren't attributes.
COLOR_SGR_PARAMETERS = (38, 48, 58)


def find_default_tmux_socket_path():
    """Returns the socket of the tmux server that a plain `tmux` command uses."""
    return os.path.join(os.environ.get("TMUX_TMPDIR") or "/tmp", f"tmux-{os.getuid()}", "default")


def build_tmux_format(line_kind, field_names):
    """Returns a -F format printing one line: line_kind, then each field, FIELD_SEPARATOR between."""
    return FIELD_SEPARATOR.join([line_kind] + ["#{" + field_name + "}" for field_name in field_names])


def parse_tmux_output_lines(output, line_kind, field_names):
    """Returns a dict per output line of line_kind (build_tmux_format's), by field name."""
    records = []
    for line in output.splitlines():
        fields = line.split(FIELD_SEPARATOR)
        if fields[0] == line_kind and len(fields) == len(field_names) + 1:
            records.append(dict(zip(field_names, fields[1:])))
    return records


def escape_tmux_argument(argument):
    """Returns an argument as tmux reads it back unchanged: tmux takes an argument that ends with
    ";" for a separator between two commands, dropping the ";", unless it is written "\\;"."""
    return argument[:-1] + "\\;" if argument.endswith(";") else argument


def escape_tmux_format_argument(argument):
    """Returns an argument that tmux expands as a format (a start folder, a window or session
    name) as tmux reads it back unchanged: "#" starts a format there ("#S", "#{...}", "#(...)"),
    so each is written "##"; and as escape_tmux_argument."""
    return escape_tmux_argument(argument.replace("#", "##"))


def build_send_keys_commands(pane_id, text):
    """Returns the tmux commands (argument lists) that type text into a pane: runs of printable
    characters literally, and each control character as the key that produces it."""
    commands = []
    position = 0
    for match in CONTROL_CHARACTER_PATTERN.finditer(text):
        if match.start() > position:
            commands.append(["send-keys", "-t", pane_id, "-l", "--", escape_tmux_argument(text[position:match.start()])])
        character = match.group()
        key_name = TMUX_KEY_NAME_BY_CONTROL_CHARACTER.get(character)
        if key_name is None:
            key_name = f"C-{chr(ord(character) + 96)}" if "\x01" <= character <= "\x1a" else f"0x{ord(character):02x}"
        commands.append(["send-keys", "-t", pane_id, key_name])
        position = match.end()
    if position < len(text):
        commands.append(["send-keys", "-t", pane_id, "-l", "--", escape_tmux_argument(text[position:])])
    return commands


def join_tmux_commands(commands):
    """Returns the arguments that run several tmux commands, in order, in one tmux run."""
    arguments = []
    for command in commands:
        if arguments:
            arguments.append(";")
        arguments.extend(command)
    return arguments


def apply_sgr_parameters_to_faintness(parameters, is_faint):
    """Returns whether text is faint after an SGR escape ("\\x1b[<parameters>m"): 2 makes it
    faint, 22 and a reset (0, or no parameter) end that, and a color's numbers (38;2;10;2;30) are skipped."""
    values = parameters.split(";") if parameters else [""]
    index = 0
    while index < len(values):
        main_value = values[index].split(":")[0]
        number = int(main_value) if main_value.isdigit() else 0
        if number == 0 or number == NORMAL_INTENSITY_SGR_PARAMETER:
            is_faint = False
        elif number == FAINT_SGR_PARAMETER:
            is_faint = True
        elif number in COLOR_SGR_PARAMETERS and ":" not in values[index] and index + 1 < len(values):
            index += {"5": 2, "2": 4}.get(values[index + 1], 0)
        index += 1
    return is_faint


def parse_screen_lines_with_faint_flags(captured_text):
    """Returns capture-pane -e's output as (text, [whether each character is faint]) per line;
    an attribute carries over to the next line, as on screen."""
    screen_lines_with_faint_flags = []
    is_faint = False
    for line in captured_text.split("\n"):
        text_parts, faint_flags, position = [], [], 0
        for match in ESCAPE_SEQUENCE_PATTERN.finditer(line):
            text_parts.append(line[position:match.start()])
            faint_flags.extend([is_faint] * (match.start() - position))
            if match.group(2) == "m":
                is_faint = apply_sgr_parameters_to_faintness(match.group(1), is_faint)
            position = match.end()
        text_parts.append(line[position:])
        faint_flags.extend([is_faint] * (len(line) - position))
        screen_lines_with_faint_flags.append(("".join(text_parts), faint_flags))
    if captured_text.endswith("\n"):
        screen_lines_with_faint_flags.pop()  # the newline after the last line
    return screen_lines_with_faint_flags


def find_process_in_front_of_the_pane(pane_process_id):
    """Returns the ID of the program that gets the keys in a pane whose shell is pane_process_id:
    the terminal's foreground job, or a text editor that job opened in its own process group
    (find_text_editor_opened_by_the_job); None when the pane's shell is gone."""
    job_process_id = find_foreground_job_process_id(pane_process_id)
    if job_process_id is None:
        return None
    return find_text_editor_opened_by_the_job(job_process_id) or job_process_id


def build_list_panes_command():
    """Returns the tmux command that lists every pane of the server with PANE_FIELD_NAMES."""
    return ["list-panes", "-a", "-F", build_tmux_format("pane", PANE_FIELD_NAMES)]


def parse_pane_records_with_the_process_in_front(tmux_output):
    """Returns the pane records in build_list_panes_command's output (other commands' lines are
    skipped), each with "process_id_in_front", its pane's find_process_in_front_of_the_pane. The
    poll compares it with the last gather's, since tmux's pane_current_command names the leader of
    the foreground group, which stays the same when an agent opens an editor in its group (or a
    new agent of the same name replaces an old one). It reads the kernel: call it in a worker thread."""
    return [dict(pane_record, process_id_in_front=find_process_in_front_of_the_pane(int(pane_record["pane_pid"])))
            for pane_record in parse_tmux_output_lines(tmux_output, "pane", PANE_FIELD_NAMES)]


def describe_the_process_in_front(process_id_in_front):
    """Returns (its process ID, its command line, its folder, when it started) for the program in
    front of a pane, or (None, "", "", None) when there is none or it's gone."""
    process = read_process_info(process_id_in_front) if process_id_in_front else None
    if process is None:
        return None, "", "", None
    arguments = read_process_arguments_and_environment(process_id_in_front)[0]
    command_line = shlex.join(arguments) if arguments else process.name
    return process_id_in_front, command_line, read_process_current_folder(process_id_in_front), process.started_at


def build_gathered_windows(pane_records, describe_process=describe_the_process_in_front):
    """Returns gathered window dicts (snapshot_builder's module docstring) for list-panes's records:
    one per tmux session, a tab per tmux window, and the window's first pane as its session.

    A session named by its user (tmux names the others 0, 1, ...) and a window renamed by its user
    (which turns automatic-rename off) count as titles of their own; otherwise the list names the
    row after its agent session.

    Args:
        pane_records: parse_pane_records_with_the_process_in_front's records, in tmux's order.
        describe_process: describe_the_process_in_front, or a stand-in.
    """
    gathered_windows_by_session_id = {}
    first_pane_record_by_window_id = {}
    for pane_record in pane_records:
        session_id, window_id = pane_record["session_id"], pane_record["window_id"]
        if session_id not in gathered_windows_by_session_id:
            session_name = pane_record["session_name"]
            gathered_windows_by_session_id[session_id] = {
                "window_id": session_id, "terminal_name": TMUX_TERMINAL_NAME, "frame": None, "title": session_name,
                "title_override_format": None if session_name.isdigit() else session_name, "tabs": [],
                "selected_tab_id": None}
        if pane_record["window_active"] == "1":
            gathered_windows_by_session_id[session_id]["selected_tab_id"] = window_id
        first_pane_record = first_pane_record_by_window_id.get(window_id)
        if first_pane_record is None:
            gathered_windows_by_session_id[session_id]["tabs"].append({"tab_id": window_id})
        if first_pane_record is None or int(pane_record["pane_index"]) < int(first_pane_record["pane_index"]):
            first_pane_record_by_window_id[window_id] = pane_record
    for gathered_window in gathered_windows_by_session_id.values():
        for gathered_tab in gathered_window["tabs"]:
            pane_record = first_pane_record_by_window_id[gathered_tab["tab_id"]]
            window_was_renamed = pane_record["automatic-rename"] == "0"
            job_process_id, command_line, job_folder, job_started_at = describe_process(pane_record["process_id_in_front"])
            gathered_tab.update({
                "title": pane_record["window_name"],
                "title_override_format": pane_record["window_name"] if window_was_renamed else None,
                "first_session": {
                    "session_id": pane_record["pane_id"], "command_line": command_line, "job_pid": job_process_id,
                    "job_folder": job_folder, "job_started_at": job_started_at,
                    "status_set_in_iterm2": None, "status_set_in_iterm2_at": None, "status": None,
                    "path": pane_record["pane_current_path"]}})
    return list(gathered_windows_by_session_id.values())


class TmuxTerminalSession(TerminalSession):
    """A tmux pane behind the TerminalSession interface."""

    def __init__(self, tmux_backend, pane_id):
        self.tmux_backend = tmux_backend
        self.pane_id = pane_id

    async def refuse_typing_that_wouldnt_reach_the_agent_alone(self):
        """Raises TerminalTypingRefused when keys sent to the pane wouldn't reach its program
        alone: with synchronize-panes on, tmux sends them to the window's other panes too (a
        /clear would clear their agents' conversations); in copy mode (or another tmux mode), the
        mode takes them, while capture-pane still reads the program's screen, so a command would
        look typed."""
        synchronize_panes, pane_in_mode = (await self.tmux_backend.run_tmux(
            ["display-message", "-p", "-t", self.pane_id, FIELD_SEPARATOR.join(["#{synchronize-panes}", "#{pane_in_mode}"])]
        )).strip().split(FIELD_SEPARATOR)
        if synchronize_panes == "1":
            raise TerminalTypingRefused("its tmux window has synchronize-panes on, so the keys would reach the window's "
                                        "other panes too; turn it off to type into one pane")
        if pane_in_mode == "1":
            raise TerminalTypingRefused("its tmux pane is in copy mode, which would take the keys; leave it (q) and try again")

    async def send_text(self, text):
        await self.refuse_typing_that_wouldnt_reach_the_agent_alone()
        await self.tmux_backend.run_tmux(join_tmux_commands(build_send_keys_commands(self.pane_id, text)))

    async def paste_text(self, text):
        """Loads the text into the pane's own tmux buffer and pastes it with -p, which tmux sends
        as a bracketed paste to a program that asked for one (agents do), in one tmux run. -r
        keeps its newlines, which tmux otherwise turns into carriage returns (iTerm2's paste keeps them)."""
        await self.refuse_typing_that_wouldnt_reach_the_agent_alone()
        buffer_name = PASTE_BUFFER_NAME_START + self.pane_id.lstrip("%")
        await self.tmux_backend.run_tmux(
            join_tmux_commands([["load-buffer", "-b", buffer_name, "-"],
                                ["paste-buffer", "-p", "-r", "-d", "-b", buffer_name, "-t", self.pane_id]]), input_text=text)

    async def read_screen_lines(self):
        return [text for text, _ in parse_screen_lines_with_faint_flags(
            await self.tmux_backend.run_tmux(["capture-pane", "-p", "-t", self.pane_id]))]

    async def read_screen_lines_with_faint_flags(self):
        return parse_screen_lines_with_faint_flags(await self.tmux_backend.run_tmux(["capture-pane", "-p", "-e", "-t", self.pane_id]))


class TmuxTerminalBackend(TerminalBackend):
    """The default tmux server, through the tmux command."""

    terminal_name = TMUX_TERMINAL_NAME
    tab_kind_name = "tmux window"
    shows_windows_through_clients = True

    def __init__(self, request_regather, request_snapshot, report_problem, socket_path=None, tmux_program_path=None):
        """
        Args:
            socket_path: The tmux server's socket; the default server's when None.
            tmux_program_path: The tmux program; looked up on PATH and in the usual folders when None.
        """
        super().__init__(request_regather, request_snapshot, report_problem)
        self.socket_path = socket_path or find_default_tmux_socket_path()
        self.tmux_program_path = tmux_program_path or find_program_on_path_or_in_the_usual_folders("tmux")
        self.server_is_running = False
        self.pane_records_of_the_last_gather = None  # compared with each poll's, to notice changes
        self.client_records = []  # the most recently used first
        self.gathered_windows = []
        # The sessions and tmux windows start_agent_session opened since the last gather, which a
        # restore adds tabs to and renames before a gather lists them.
        self.window_and_tab_ids_opened_since_the_last_gather = set()
        self.active_pane_id_by_tab_id = {}  # the pane each tmux window's keys go to, at the last gather

    async def run_tmux(self, arguments, input_text=None):
        """Runs tmux (-u: its output is UTF-8 whatever the locale) on the server's socket with
        arguments; returns its output. Raises TerminalRequestFailed when it fails or takes longer
        than TMUX_COMMAND_TIMEOUT_SECONDS."""
        if self.tmux_program_path is None:
            raise TerminalRequestFailed("the tmux program isn't installed")
        try:
            process = await asyncio.create_subprocess_exec(
                self.tmux_program_path, "-u", "-S", self.socket_path, *arguments,
                stdin=asyncio.subprocess.PIPE if input_text is not None else asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        except OSError as error:  # the program found at start-up is gone (brew uninstall, or upgrade relinking it)
            raise TerminalRequestFailed(f"tmux couldn't be started: {error}") from error
        try:
            standard_output, standard_error = await asyncio.wait_for(
                process.communicate(input_text.encode() if input_text is not None else None), TMUX_COMMAND_TIMEOUT_SECONDS)
        except asyncio.TimeoutError as error:
            process.kill()
            await process.wait()
            raise TerminalRequestFailed(f"tmux {arguments[0]} took longer than {TMUX_COMMAND_TIMEOUT_SECONDS} seconds") from error
        if process.returncode != 0:
            raise TerminalRequestFailed(standard_error.decode(errors="replace").strip() or f"tmux {arguments[0]} failed")
        return standard_output.decode(errors="replace")

    async def read_pane_and_client_records(self):
        """Returns (list-panes's records, list-clients's records sorted the most recently used
        first), or None when no tmux server runs. While none ran at the last poll, tmux is run
        only once a server listens at the socket: the socket file stays after its server ends."""
        if self.tmux_program_path is None or (
                not self.server_is_running and not await asyncio.to_thread(another_process_listens_at, self.socket_path)):
            return None
        try:
            output = await self.run_tmux(join_tmux_commands([
                build_list_panes_command(), ["list-clients", "-F", build_tmux_format("client", CLIENT_FIELD_NAMES)]]))
        except TerminalRequestFailed:  # no server listens at the socket (it ended)
            return None
        client_records = parse_tmux_output_lines(output, "client", CLIENT_FIELD_NAMES)
        client_records.sort(key=lambda client_record: -int(client_record["client_activity"] or 0))
        return await asyncio.to_thread(parse_pane_records_with_the_process_in_front, output), client_records

    async def run(self):
        """Polls the server forever (poll_the_server_once)."""
        while True:
            await self.poll_the_server_once()
            await asyncio.sleep(TMUX_POLL_INTERVAL_SECONDS)

    async def poll_the_server_once(self):
        """Reads the server's panes and clients: a change in its panes (or the server starting or
        ending) asks for a regather, a change in its clients for a snapshot."""
        records = await self.read_pane_and_client_records()
        server_is_running = records is not None
        pane_records, client_records = records if records is not None else (None, [])
        if server_is_running != self.server_is_running or (
                server_is_running and pane_records != self.pane_records_of_the_last_gather):
            self.server_is_running = server_is_running
            self.request_regather()
        if [self.describe_client(client_record) for client_record in client_records] != [
                self.describe_client(client_record) for client_record in self.client_records]:
            self.request_snapshot()
        self.client_records = client_records

    @staticmethod
    def describe_client(client_record):
        """What a snapshot depends on in a client's record (not its time of last use, which every key changes)."""
        return client_record["client_pid"], client_record["session_id"], client_record["window_id"]

    def is_connected(self):
        return self.server_is_running

    def terminal_runs(self):
        return self.tmux_program_path is not None and another_process_listens_at(self.socket_path)

    async def gather_windows(self):
        # Those start_agent_session opens while this gather runs aren't in its records yet.
        ids_opened_before_this_gather = set(self.window_and_tab_ids_opened_since_the_last_gather)
        pane_records = await asyncio.to_thread(parse_pane_records_with_the_process_in_front,
                                               await self.run_tmux(build_list_panes_command()))
        self.pane_records_of_the_last_gather = pane_records
        self.active_pane_id_by_tab_id = {pane_record["window_id"]: pane_record["pane_id"] for pane_record in pane_records
                                         if pane_record["pane_active"] == "1"}
        self.gathered_windows = await asyncio.to_thread(build_gathered_windows, pane_records)
        self.window_and_tab_ids_opened_since_the_last_gather -= ids_opened_before_this_gather
        return self.gathered_windows

    def find_gathered_window(self, window_id):
        return next((gathered_window for gathered_window in self.gathered_windows if gathered_window["window_id"] == window_id),
                    None)

    def find_when_the_window_was_opened(self, window_id):
        """Returns when the tmux session was created, at the last gather."""
        return next((int(pane_record["session_created"]) for pane_record in self.pane_records_of_the_last_gather or ()
                     if pane_record["session_id"] == window_id), None)

    def find_gathered_window_of_tab(self, tab_id):
        return next((gathered_window for gathered_window in self.gathered_windows
                     if any(gathered_tab["tab_id"] == tab_id for gathered_tab in gathered_window["tabs"])), None)

    def find_tab_id_of_session(self, session_id):
        return next((gathered_tab["tab_id"] for gathered_window in self.gathered_windows for gathered_tab in gathered_window["tabs"]
                     if gathered_tab["first_session"]["session_id"] == session_id), None)

    def find_selected_tab_id(self, window_id):
        gathered_window = self.find_gathered_window(window_id)
        return gathered_window["selected_tab_id"] if gathered_window is not None else None

    def list_clients(self):
        return [TerminalClient(int(client_record["client_pid"]), client_record["window_id"])
                for client_record in self.client_records if client_record["client_pid"].isdigit()]

    def find_session(self, session_id):
        return TmuxTerminalSession(self, session_id) if self.find_tab_id_of_session(session_id) is not None else None

    def remember_what_is_in_front(self):
        """Returns each session's selected window, each window's active pane (a peek selects the
        agent's), and each client's session."""
        return {"selected_tab_id_by_window_id": {gathered_window["window_id"]: gathered_window["selected_tab_id"]
                                                 for gathered_window in self.gathered_windows},
                "active_pane_id_by_tab_id": dict(self.active_pane_id_by_tab_id),
                "window_id_by_client_tty": {client_record["client_tty"]: client_record["session_id"]
                                            for client_record in self.client_records}}

    async def put_back_what_was_in_front(self, what_was_in_front, bring_back_the_front_window):
        """Selects again each window's active pane and each session's window from before, and
        switches each client back to its session from before. Every one is sent, since the cached
        state lags a peek's own switch by up to a poll; tmux does nothing for a pane, window or
        session already shown."""
        if what_was_in_front is None:
            return ActionResult(True)
        commands = [["select-pane", "-t", pane_id] for tab_id, pane_id in what_was_in_front["active_pane_id_by_tab_id"].items()
                    if self.find_gathered_window_of_tab(tab_id) is not None]
        commands += [["select-window", "-t", tab_id] for window_id, tab_id in what_was_in_front["selected_tab_id_by_window_id"].items()
                     if tab_id and self.find_gathered_window(window_id) is not None]
        commands += [["switch-client", "-c", client_tty, "-t", window_id]
                     for client_tty, window_id in what_was_in_front["window_id_by_client_tty"].items()
                     if self.find_gathered_window(window_id) is not None]
        if commands:
            try:
                await self.run_tmux(join_tmux_commands(commands))
            except TerminalRequestFailed as error:  # a client that detached meanwhile, say
                return ActionResult(False, f"tmux couldn't show what it showed before: {error}")
        return ActionResult(True)

    # ----- actions -----

    async def show_session_in_a_client(self, window_id):
        """Switches a client to the tmux session window_id: one showing it at the last poll, else
        the most recently used one. Returns its process ID, or None when no client is attached.
        switch-client is sent every time, since the client's session may have changed since that
        poll (an earlier focus or peek); tmux does nothing for the session it already shows."""
        client_record = next((client_record for client_record in self.client_records if client_record["session_id"] == window_id),
                             self.client_records[0] if self.client_records else None)
        if client_record is None:
            return None
        await self.run_tmux(["switch-client", "-c", client_record["client_tty"], "-t", window_id])
        return int(client_record["client_pid"])

    async def show_session_and_name_its_client(self, window_id):
        """Shows the tmux session window_id in a client (show_session_in_a_client); returns an
        ActionResult with details {"terminal_client_process_id"} naming that client, or, with no
        client attached, a failure saying how to attach one."""
        client_process_id = await self.show_session_in_a_client(window_id)
        if client_process_id is not None:
            return ActionResult(True, details={"terminal_client_process_id": client_process_id})
        gathered_window = self.find_gathered_window(window_id)
        session_name = gathered_window["title"] if gathered_window is not None else window_id
        return ActionResult(False, f'no terminal shows tmux: attach to the session with tmux attach -t {shlex.quote(session_name)}')

    async def focus_session(self, session_id):
        """Selects the pane's window and the pane, then shows its session in a client
        (show_session_and_name_its_client)."""
        tab_id = self.find_tab_id_of_session(session_id)
        if tab_id is None:
            return ActionResult(False, "that tab no longer exists")
        await self.run_tmux(join_tmux_commands([["select-window", "-t", tab_id], ["select-pane", "-t", session_id]]))
        return await self.show_session_and_name_its_client(self.find_gathered_window_of_tab(tab_id)["window_id"])

    async def focus_window(self, window_id):
        if self.find_gathered_window(window_id) is None:
            return ActionResult(False, "that window no longer exists")
        return await self.show_session_and_name_its_client(window_id)

    async def rename_tab(self, tab_id, new_title):
        """Renames a tmux window, which turns its automatic naming off; an empty title turns it back on."""
        if self.find_gathered_window_of_tab(tab_id) is None and tab_id not in self.window_and_tab_ids_opened_since_the_last_gather:
            return ActionResult(False, "that tab no longer exists")
        if new_title.strip():
            await self.run_tmux(["rename-window", "-t", tab_id, "--", escape_tmux_format_argument(new_title.strip())])
        else:
            await self.run_tmux(["set-option", "-w", "-t", tab_id, "automatic-rename", "on"])
        return ActionResult(True)

    async def rename_window(self, window_id, new_title):
        """Renames a tmux session (it has no default name to go back to)."""
        if self.find_gathered_window(window_id) is None and window_id not in self.window_and_tab_ids_opened_since_the_last_gather:
            return ActionResult(False, "that window no longer exists")
        if not new_title.strip():
            return ActionResult(False, "a tmux session needs a name")
        await self.run_tmux(["rename-session", "-t", window_id, "--", escape_tmux_format_argument(new_title.strip())])
        return ActionResult(True)

    async def close_window(self, window_id):
        """Ends a tmux session and the programs in its windows."""
        if self.find_gathered_window(window_id) is None:
            return ActionResult(False, "that window no longer exists")
        await self.run_tmux(["kill-session", "-t", window_id])
        return ActionResult(True)

    async def move_tab(self, tab_id, target_window_id, drop_before_tab_id):
        """Moves a tmux window before drop_before_tab_id, or after the last window of the target
        session, without changing what the clients show."""
        target_window = self.find_gathered_window(target_window_id)
        if self.find_gathered_window_of_tab(tab_id) is None or target_window is None:
            return ActionResult(False, "that tab or window no longer exists")
        if drop_before_tab_id is not None:
            if drop_before_tab_id != tab_id:
                await self.run_tmux(["move-window", "-d", "-b", "-s", tab_id, "-t", drop_before_tab_id])
            return ActionResult(True)
        last_tab_id = target_window["tabs"][-1]["tab_id"]
        if last_tab_id != tab_id:
            await self.run_tmux(["move-window", "-d", "-a", "-s", tab_id, "-t", last_tab_id])
        return ActionResult(True)

    async def move_tab_to_new_window(self, tab_id):
        """Moves a tmux window into a new tmux session: tmux has no command for that, so the new
        session's own first window is closed once the moved one is in."""
        gathered_window = self.find_gathered_window_of_tab(tab_id)
        if gathered_window is None:
            return ActionResult(False, "that tab no longer exists")
        if len(gathered_window["tabs"]) < 2:
            return ActionResult(False, "the tab is already its window's only tab")
        new_session_id, placeholder_tab_id = (await self.run_tmux(
            ["new-session", "-d", "-P", "-F", FIELD_SEPARATOR.join(["#{session_id}", "#{window_id}"])])).strip().split(FIELD_SEPARATOR)
        await self.run_tmux(join_tmux_commands([["move-window", "-d", "-a", "-s", tab_id, "-t", placeholder_tab_id],
                                                ["kill-window", "-t", placeholder_tab_id]]))
        return ActionResult(True, details={"window_id": new_session_id})

    async def start_agent_session(self, window_id, folder, launch_command, after_tab_id=None):
        """Opens a tmux window in a session (a new session when window_id is None), right after
        after_tab_id when given, and types launch_command into its shell, so the window keeps a
        shell when the agent exits (as iTerm2's do). A new session is shown in the most recently
        used client, if any. Its result has details {"tab_id": the new tmux window's ID}, plus
        {"window_id"} for a new session and, when a client shows it, {"terminal_client_process_id"}.
        The core has checked the folder and the launch command."""
        start_folder = escape_tmux_format_argument(folder)  # tmux expands -c as a format ("C#Projects")
        opens_a_new_session = window_id is None
        if opens_a_new_session:
            window_id, tab_id, pane_id = (await self.run_tmux(
                ["new-session", "-d", "-c", start_folder, "-P", "-F",
                 FIELD_SEPARATOR.join(["#{session_id}", "#{window_id}", "#{pane_id}"])])).strip().split(FIELD_SEPARATOR)
            details = {"window_id": window_id, "tab_id": tab_id}
            self.window_and_tab_ids_opened_since_the_last_gather.add(window_id)
        else:
            if self.find_gathered_window(window_id) is None and window_id not in self.window_and_tab_ids_opened_since_the_last_gather:
                return ActionResult(False, "that window no longer exists")
            target_arguments = ["-a", "-t", after_tab_id] if after_tab_id is not None else ["-t", f"{window_id}:"]
            tab_id, pane_id = (await self.run_tmux(["new-window", "-c", start_folder, "-P", "-F",
                                                    FIELD_SEPARATOR.join(["#{window_id}", "#{pane_id}"])] + target_arguments)
                               ).strip().split(FIELD_SEPARATOR)
            details = {"tab_id": tab_id}
        self.window_and_tab_ids_opened_since_the_last_gather.add(tab_id)
        await TmuxTerminalSession(self, pane_id).send_text(launch_command + "\r")
        if opens_a_new_session:
            client_process_id = await self.show_session_in_a_client(window_id)
            if client_process_id is not None:
                details["terminal_client_process_id"] = client_process_id
        return ActionResult(True, details=details)

    async def move_window_to_monitor(self, window_id, monitors, target_monitor_id):
        return ActionResult(False, "a tmux session has no window of its own to move; move the terminal window that shows it")
