"""What the core asks of a terminal: the terminal layer that lets the
dashboard list agent sessions from iTerm2 and tmux at the same time.

A terminal backend gathers its windows, tabs and sessions into plain dicts (the shape
snapshot_builder's module docstring describes), hands out a TerminalSession for typing into one
session and reading its screen, and carries out the list's actions on its windows and tabs. Every
ID is the terminal's own (iTerm2's window, tab and session IDs; tmux's session "$1", window "@2"
and pane "%3" IDs, which can't be mistaken for iTerm2's), so the core routes each action to the
backend whose gathered windows hold the item.

In tmux terms: a tmux session is a window row, a tmux window is a tab row, and its first pane is
the tab's session, as an iTerm2 tab's first pane is.
"""

import collections

TerminalClient = collections.namedtuple("TerminalClient", ["process_id", "front_tab_id"])
TerminalClient.__doc__ = ("A program showing a terminal backend's windows from inside another terminal (a tmux "
                          "client): its process ID, and the tab it shows (a tmux window ID).")


class TerminalRequestFailed(Exception):
    """A request to a terminal failed: its window, tab or session is gone, or the terminal refused it."""


class TerminalTypingRefused(TerminalRequestFailed):
    """The session is there, but keys typed into it wouldn't reach its program alone (tmux's
    synchronize-panes, or a pane in copy mode); the message says why."""


class TerminalConnectionLost(Exception):
    """An established connection to a terminal ended (iTerm2 quit): the app then exits, so its
    supervisor restarts it, since the iterm2 package can't reconnect within one process."""


class TerminalSession:
    """One session, the first pane of a tab, to type into and to read the screen of. Every method
    raises TerminalRequestFailed when the session is gone."""

    async def send_text(self, text):
        """Types text as keys: printable characters as typed, and control characters as the keys
        that produce them ("\\r" Enter, "\\x15" Ctrl+U, "\\x1a" Ctrl+Z), into this session's program
        alone; raises TerminalTypingRefused, typing nothing, when the keys wouldn't reach it alone."""
        raise NotImplementedError

    async def paste_text(self, text):
        """Pastes text as one bracketed paste, so the program takes its newlines as part of the
        text rather than as Enter; into this session alone, as send_text."""
        raise NotImplementedError

    async def read_screen_lines(self):
        """Returns the text of each visible line."""
        raise NotImplementedError

    async def read_screen_lines_with_faint_flags(self):
        """Returns each visible line as (text, [whether each character is faint]), for
        screen_readings.input_area_is_empty."""
        raise NotImplementedError


class TerminalBackend:
    """One terminal the dashboard lists agent sessions from.

    Attributes:
        terminal_name: The terminal's name, shown in the list ("iTerm2", "tmux").
        tab_kind_name: How a message to an agent names one of its tabs ("iTerm2 tab", "tmux window").
        reports_agent_status: Whether its sessions carry the status the agents report (iTerm2's
            `status` variable); for the others the core takes each agent's own report.
        windows_have_frames: Whether its windows are on screen with frames that can be read and
            set (for the monitor groups and the docked sidebar).
        shows_windows_through_clients: Whether its windows show only through clients running
            inside other terminals (tmux), listed by list_clients.
        ids_are_never_reused: Whether its window and session IDs each name the same window or
            tab for good, also after the terminal or the Mac restarts (iTerm2's, which it keeps for
            the windows and tabs it brings back), so a restore may type into the tab a saved
            session ID names and open tabs in the window a saved window ID names. tmux numbers
            its sessions and panes again from $0 and %0 in each new server, so there a restore
            does so only while the saved session's creation time still matches
            (find_when_the_window_was_opened).
    """

    terminal_name = ""
    tab_kind_name = "tab"
    reports_agent_status = False
    windows_have_frames = False
    shows_windows_through_clients = False
    ids_are_never_reused = False

    def __init__(self, request_regather, request_snapshot, report_problem):
        """
        Args:
            request_regather: Called (on the core's loop) when its windows, tabs or sessions changed.
            request_snapshot: Called when something the snapshot shows changed without a regather
                (a status, the selected tab, which window has focus).
            report_problem: Called with (terminal name, a message) when the terminal can't be
                reached though it runs, and with (terminal name, None) once it can or no longer runs.
        """
        self.request_regather = request_regather
        self.request_snapshot = request_snapshot
        self.report_problem = report_problem

    async def run(self):
        """Keeps the backend connected and its windows current. Returns or raises only when an
        established connection is lost (TerminalConnectionLost), which ends the core."""
        raise NotImplementedError

    def is_connected(self):
        """Whether gather_windows can run now."""
        raise NotImplementedError

    def terminal_runs(self):
        """Whether the terminal runs, connected yet or not (dump-snapshot waits for those that run)."""
        raise NotImplementedError

    async def gather_windows(self):
        """Returns its windows as gathered window dicts (snapshot_builder's module docstring),
        each with "terminal_name"."""
        raise NotImplementedError

    def find_selected_tab_id(self, window_id):
        """Returns the tab a window shows (which may run no agent), or None."""
        raise NotImplementedError

    def find_front_tab_id(self):
        """Returns the selected tab of the terminal's front window, or None."""
        return None

    def a_terminal_window_has_keyboard_focus(self):
        """Whether the terminal is the active app and one of its terminal windows has keyboard focus."""
        return False

    def list_application_process_ids(self):
        """Returns the process IDs of the terminal's app (for a terminal with windows of its own)."""
        return []

    def list_clients(self):
        """Returns the TerminalClient of each program showing its windows, the most recently used
        first (shows_windows_through_clients only)."""
        return []

    def find_session(self, session_id):
        """Returns the TerminalSession of a gathered session, or None when it is gone."""
        raise NotImplementedError

    def find_when_the_window_was_opened(self, window_id):
        """Returns when a gathered window was opened (seconds since 1970), which tells it apart
        from a later window given the same ID in a terminal whose IDs are reused (tmux); None in
        one whose IDs aren't (ids_are_never_reused), or for a window that is gone."""
        return None

    def remember_what_is_in_front(self):
        """Returns what a peek (pressing and holding a tab row) changes, for put_back_what_was_in_front."""
        return None

    async def put_back_what_was_in_front(self, what_was_in_front, bring_back_the_front_window):
        """Undoes a peek: each window shows its tab from before, and, when bring_back_the_front_window,
        the front window from before comes back to the front. Returns an ActionResult."""
        raise NotImplementedError

    # ----- actions, each returning an ActionResult (dashboard_core_api) -----

    async def focus_session(self, session_id):
        """Brings the session's tab and window to the front. A backend that shows its windows
        through clients returns details {"terminal_client_process_id": the client now showing it},
        so the core can bring that client's terminal to the front."""
        raise NotImplementedError

    async def focus_window(self, window_id):
        """Brings a window to the front with the tab it already shows."""
        raise NotImplementedError

    async def rename_tab(self, tab_id, new_title):
        """Sets a tab's title; an empty title restores the default name."""
        raise NotImplementedError

    async def rename_window(self, window_id, new_title):
        """Sets a window's title; an empty title restores the default name where there is one."""
        raise NotImplementedError

    async def move_tab(self, tab_id, target_window_id, drop_before_tab_id):
        """Moves a tab within its window or into another of this terminal's windows, keeping its
        program running; drop_before_tab_id None puts it after the last tab."""
        raise NotImplementedError

    async def move_tab_to_new_window(self, tab_id):
        """Moves a tab into a new window of its own."""
        raise NotImplementedError

    async def start_agent_session(self, window_id, folder, launch_command, after_tab_id=None):
        """Opens a tab in a window (a new window when window_id is None), right after after_tab_id
        when given, whose shell types launch_command in folder (a folder that exists, and a
        launch command that isn't empty: the core checks both). Its result has details
        {"tab_id": the new tab's ID}, plus {"window_id": its ID} for a new window."""
        raise NotImplementedError

    async def close_window(self, window_id):
        """Closes a window and ends the programs in its tabs (the list has asked the user already)."""
        raise NotImplementedError

    async def move_window_to_monitor(self, window_id, monitors, target_monitor_id):
        """Moves a window to another monitor (windows_have_frames only)."""
        raise NotImplementedError

    # ----- window frames (windows_have_frames only) -----

    async def read_window_frame(self, window_id):
        """Returns a window's frame as (x, y, width, height)."""
        raise NotImplementedError

    async def set_window_frame(self, window_id, frame):
        """Gives a window the frame (x, y, width, height)."""
        raise NotImplementedError

    async def window_is_full_screen(self, window_id):
        """Whether a window is full screen."""
        raise NotImplementedError
