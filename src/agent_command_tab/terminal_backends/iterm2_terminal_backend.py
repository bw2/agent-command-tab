"""iTerm2 behind the terminal layer: its connection and subscriptions, gathering its windows and
tabs, typing into and reading its sessions, and the list's actions on its windows and tabs.

Everything here runs on the core's event loop. iTerm2 pushes its changes (new sessions, layout
changes, ended sessions, focus changes, and the variables in SESSION_VARIABLES_TO_WATCH), so
nothing is polled but the window frames (DashboardCore.poll_window_frames_when_they_matter).
"""

import asyncio
import json
import time

import iterm2
import iterm2.auth
import iterm2.rpc
from AppKit import NSRunningApplication
from iterm2 import api_pb2
from iterm2.notifications import async_subscribe_to_variable_change_notification

from agent_command_tab.agent_adapters.base import command_line_runs_a_shell
from agent_command_tab.agent_process_tree import read_process_current_folder, read_process_info
from agent_command_tab.dashboard_core_api import ActionResult
from agent_command_tab.monitor_assignment import (assign_windows_to_monitors_by_largest_overlap,
                                                  compute_window_frame_on_another_monitor)
from agent_command_tab.terminal_backends.base import (TerminalBackend, TerminalConnectionLost, TerminalRequestFailed,
                                                      TerminalSession)
from agent_command_tab.window_list_ordering import move_id_before_another

ITERM2_TERMINAL_NAME = "iTerm2"
ITERM2_BUNDLE_IDENTIFIER = "com.googlecode.iterm2"
SESSION_VARIABLES_TO_WATCH = ("commandLine", "status")
SECONDS_TO_WAIT_FOR_THE_STATUS_IN_ITERM2S_TAB_BAR = 2
SECONDS_FOR_FULL_SCREEN_ANIMATION = 1.0
# Asking the iterm2 package to connect while iTerm2 isn't running would start iTerm2 (it asks
# for its cookie through AppleScript), so the backend waits for iTerm2 to run, checking this often.
SECONDS_BETWEEN_CHECKS_FOR_ITERM2_RUNNING = 3
SECONDS_BEFORE_CONNECTING_AGAIN_AFTER_A_FAILURE = 30  # iTerm2 runs, but refused (its Python API is off, say)
# A bracketed paste: the agent takes the text between these as one paste, newlines included,
# rather than as typed keys (where a newline would be Enter).
BRACKETED_PASTE_START = "\x1b[200~"
BRACKETED_PASTE_END = "\x1b[201~"


def list_running_iterm2_applications():
    """Returns the NSRunningApplication of each iTerm2 app that runs (it may be asked from any thread)."""
    return list(NSRunningApplication.runningApplicationsWithBundleIdentifier_(ITERM2_BUNDLE_IDENTIFIER))


def list_iterm2_process_ids():
    """Returns the process IDs of the iTerm2 apps that run."""
    return [application.processIdentifier() for application in list_running_iterm2_applications()]


def escape_text_for_iterm2_interpolated_string(text):
    """Returns text with backslashes doubled, since iTerm2 reads titles as interpolated strings
    (where a backslash starts an expression such as \\(session.name))."""
    return text.replace("\\", "\\\\")


def parse_job_process_id(job_pid_value):
    """Returns iTerm2's `jobPid` variable as an int, or None."""
    try:
        return int(job_pid_value)
    except (TypeError, ValueError):
        return None


async def read_iterm2_variables(connection, names, session_id=None, tab_id=None, window_id=None):
    """Returns the values of several variables of one session, tab or window, read in a single
    request (the iterm2 package's async_get_variable sends one request per variable).

    Raises:
        iterm2.RPCException: The session, tab or window is gone.
    """
    response = await iterm2.rpc.async_variable(connection, session_id, [], names, tab_id=tab_id, window_id=window_id)
    status = response.variable_response.status
    if status != api_pb2.VariableResponse.Status.Value("OK"):
        raise iterm2.RPCException(api_pb2.VariableResponse.Status.Name(status))
    return [json.loads(value) for value in response.variable_response.values]


async def read_status_shown_in_iterm2s_tab_bar(session):
    """Returns the status iTerm2 shows for a session in its tab bar ("idle", "working", ...), from
    its built-in function iterm2.get_session_status (not in iTerm2's documentation), or None.

    After iTerm2 restarts, its tab bar still shows each tab's status, and this function returns
    it, but the session's `status` variable stays empty until the agent's next hook event (seen
    with iTerm2 3.7.6; user request, 2026-10-21).
    """
    try:
        result = await session.async_invoke_function(f'iterm2.get_session_status(session_id: "{session.session_id}")',
                                                     timeout=SECONDS_TO_WAIT_FOR_THE_STATUS_IN_ITERM2S_TAB_BAR)
    except iterm2.RPCException:
        return None
    return result.get("status") if isinstance(result, dict) else None


async def read_window_frame(window):
    """Returns an iTerm2 window's frame as (x, y, width, height)."""
    frame = await window.async_get_frame()
    return (frame.origin.x, frame.origin.y, frame.size.width, frame.size.height)


def convert_frame_to_iterm2_frame(frame):
    """Returns (x, y, width, height) as an iterm2.Frame."""
    return iterm2.Frame(iterm2.Point(frame[0], frame[1]), iterm2.Size(frame[2], frame[3]))


def build_new_agent_session_profile(folder, launch_command):
    """Returns profile customizations that start launch_command in folder.

    iTerm2's "send text at start" profile setting ("Initial Text", Phase 0) types the command
    once the shell is ready, so the tab keeps a normal shell and stays open if the agent exits.
    """
    profile = iterm2.LocalWriteOnlyProfile({"Initial Text": launch_command})
    profile.set_initial_directory_mode(iterm2.InitialWorkingDirectory.INITIAL_WORKING_DIRECTORY_CUSTOM)
    profile.set_custom_directory(folder)
    return profile


class Iterm2TerminalSession(TerminalSession):
    """An iterm2.Session behind the TerminalSession interface; its RPCExceptions (the session is
    gone) become TerminalRequestFailed."""

    def __init__(self, session):
        self.session = session

    async def send_text(self, text):
        try:
            await self.session.async_send_text(text, suppress_broadcast=True)
        except iterm2.RPCException as error:
            raise TerminalRequestFailed(str(error)) from error

    async def paste_text(self, text):
        await self.send_text(BRACKETED_PASTE_START + text + BRACKETED_PASTE_END)

    async def read_screen_contents(self):
        try:
            return await self.session.async_get_screen_contents()
        except iterm2.RPCException as error:
            raise TerminalRequestFailed(str(error)) from error

    async def read_screen_lines(self):
        contents = await self.read_screen_contents()
        return [contents.line(index).string for index in range(contents.number_of_lines)]

    async def read_screen_lines_with_faint_flags(self):
        contents = await self.read_screen_contents()
        screen_lines_with_faint_flags = []
        for index in range(contents.number_of_lines):
            line = contents.line(index)
            screen_lines_with_faint_flags.append(
                (line.string, [bool(style is not None and style.faint) for style in map(line.style_at, range(len(line.string)))]))
        return screen_lines_with_faint_flags


class Iterm2TerminalBackend(TerminalBackend):
    """iTerm2, through its Python API (the iterm2 package)."""

    terminal_name = ITERM2_TERMINAL_NAME
    tab_kind_name = "iTerm2 tab"
    reports_agent_status = True
    windows_have_frames = True
    # Its IDs are UUIDs, and the windows and tabs it brought back after a reboot kept theirs (seen 2026-11-06).
    ids_are_never_reused = True

    def __init__(self, request_regather, request_snapshot, report_problem):
        super().__init__(request_regather, request_snapshot, report_problem)
        self.connection = None
        self.app = None  # the iterm2.App, once connected
        self.gathered_windows = []  # this backend's windows from the last gather (the core lists the same dicts)
        self.gather_is_in_progress = False
        # {session ID: when iTerm2 last reported a change of the session's status variable}; a value
        # already set when the dashboard connected has none (find_status_set_in_iterm2_since_the_job_started).
        self.status_set_in_iterm2_at_by_session_id = {}
        self.key_terminal_window_id = None  # None while no terminal window (for example iTerm2's Settings) has keyboard focus
        self.iterm2_process_ids = []  # the app's, read when connecting (a connection lasts no longer than its iTerm2)

    # ----- connection -----

    async def run(self):
        """Waits until iTerm2 runs, connects, subscribes, and watches until the connection is lost.

        A connection that can't be made while iTerm2 runs (its Python API is off, or the user
        refused it) is reported and tried again later, while the other terminals keep being
        listed. A connection that was made and then lost raises TerminalConnectionLost, since the
        iterm2 package can't reconnect within one process.
        """
        iterm2.auth.gAppKitAvailable = False  # fetch the token with osascript, never NSAppleScript
        while True:
            while not list_iterm2_process_ids():
                self.report_problem(self.terminal_name, None)  # a refused connection is no problem once iTerm2 quit
                await asyncio.sleep(SECONDS_BETWEEN_CHECKS_FOR_ITERM2_RUNNING)
            try:
                # The cookie comes through osascript, which waits for iTerm2 (and for macOS's
                # question whether the app may control it): in a worker thread, so the other
                # terminals' rows and the list's actions go on meanwhile. async_create then finds
                # it in the environment and doesn't ask again.
                await asyncio.to_thread(iterm2.auth.authenticate)
                self.connection = await iterm2.Connection.async_create()
                self.iterm2_process_ids = list_iterm2_process_ids()
                break
            except Exception as error:
                # The user's wording for the hint, 2026-10-28.
                self.report_problem(self.terminal_name, f"could not connect to iTerm2 ({error}). Retrying soon. Make sure "
                                                        "iTerm2's Settings > General > Magic > Enable Python API is on.")
                await asyncio.sleep(SECONDS_BEFORE_CONNECTING_AGAIN_AFTER_A_FAILURE)
        # The watchdog starts at once: a request whose answer never comes (iTerm2 quit meanwhile)
        # would otherwise hang the start-up forever.
        connection_loss_task = asyncio.ensure_future(self.watch_for_connection_loss())
        start_up_task = asyncio.ensure_future(self.start_up_after_connecting())
        await asyncio.wait([start_up_task, connection_loss_task], return_when=asyncio.FIRST_COMPLETED)
        if not start_up_task.done():
            start_up_task.cancel()
            raise TerminalConnectionLost("the iTerm2 connection closed during start-up")
        if start_up_task.exception() is not None:
            raise TerminalConnectionLost(f"the iTerm2 connection could not start: {start_up_task.exception()!r}")
        self.report_problem(self.terminal_name, None)
        watching_tasks = [asyncio.ensure_future(coroutine) for coroutine in (
            self.watch_layout_changes(), self.watch_new_sessions(), self.watch_session_terminations(),
            self.watch_focus_changes())]
        done_tasks, _ = await asyncio.wait(watching_tasks + [connection_loss_task], return_when=asyncio.FIRST_COMPLETED)
        failure_message = "the iTerm2 connection closed"
        for done_task in done_tasks:
            if done_task is not connection_loss_task and not done_task.cancelled() and done_task.exception():
                failure_message = f"watching iTerm2 stopped: {done_task.exception()!r}"
        raise TerminalConnectionLost(failure_message)

    def is_connected(self):
        return self.app is not None

    def terminal_runs(self):
        return bool(list_iterm2_process_ids())

    async def watch_for_connection_loss(self):
        """Returns when the websocket closes or the package's dispatch task ends."""
        dispatch_future = getattr(self.connection, "_Connection__dispatch_forever_future", None)
        waiters = [asyncio.ensure_future(self.connection.websocket.wait_closed())]
        if dispatch_future is not None:
            waiters.append(dispatch_future)
        await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)

    async def start_up_after_connecting(self):
        """Reads the App object and the current focus, subscribes to variable changes, and asks for the first gather."""
        app = await iterm2.async_get_app(self.connection)
        # FocusMonitor reports only later changes, so read the current focus once. Its window
        # entry says whether that terminal window has keyboard focus or only is current (for
        # example while iTerm2's Settings window has focus).
        focus_info = await iterm2.rpc.async_get_focus_info(self.connection)
        for notification in focus_info.focus_response.notifications:
            if notification.HasField("window"):
                self.update_key_terminal_window(iterm2.FocusUpdateWindowChanged(
                    notification.window.window_id, iterm2.FocusUpdateWindowChanged.Reason(notification.window.window_status)))
        session_scope, window_scope = api_pb2.VariableScope.Value("SESSION"), api_pb2.VariableScope.Value("WINDOW")
        for variable_name in SESSION_VARIABLES_TO_WATCH:
            await async_subscribe_to_variable_change_notification(
                self.connection, self.handle_session_variable_change, session_scope, variable_name, "all")
        # Titles set by the user. iTerm2 rejects "all" for the tab scope, but a session's
        # tab.titleOverrideFormat reports its tab's title (checked against iTerm2 3.7.6).
        await async_subscribe_to_variable_change_notification(
            self.connection, self.handle_title_change, session_scope, "tab.titleOverrideFormat", "all")
        await async_subscribe_to_variable_change_notification(
            self.connection, self.handle_title_change, window_scope, "titleOverrideFormat", "all")
        self.app = app
        self.request_regather()

    # ----- subscriptions -----

    async def handle_session_variable_change(self, connection, notification):
        """Updates the gathered commandLine or status of one session, then asks for a snapshot.

        A change that arrives during a gather would be written into the list that the gather
        is about to replace, so it asks for another gather instead. The time of a status change
        is recorded either way, since a gather reads only the value.
        """
        if notification.name == "status":
            self.status_set_in_iterm2_at_by_session_id[notification.identifier] = time.time()
        if self.gather_is_in_progress:
            self.request_regather()
            return
        new_value = json.loads(notification.json_new_value) if notification.json_new_value else None
        field_name = {"commandLine": "command_line", "status": "status_set_in_iterm2"}[notification.name]
        for gathered_window in self.gathered_windows:
            for gathered_tab in gathered_window["tabs"]:
                first_session = gathered_tab["first_session"]
                if first_session and first_session["session_id"] == notification.identifier:
                    first_session[field_name] = new_value
                    if field_name == "command_line":
                        self.request_regather()  # the job process ID and folder change with the job
                    else:
                        first_session["status_set_in_iterm2_at"] = self.status_set_in_iterm2_at_by_session_id[notification.identifier]
        self.request_snapshot()

    async def handle_title_change(self, connection, notification):
        """A tab or window title changed (here, in iTerm2's own dialogs, or by a program): re-read."""
        self.request_regather()

    async def watch_layout_changes(self):
        async with iterm2.LayoutChangeMonitor(self.connection) as monitor:
            while True:
                await monitor.async_get()
                self.request_regather()

    async def watch_new_sessions(self):
        async with iterm2.NewSessionMonitor(self.connection) as monitor:
            while True:
                await monitor.async_get()
                self.request_regather()

    async def watch_session_terminations(self):
        async with iterm2.SessionTerminationMonitor(self.connection) as monitor:
            while True:
                await monitor.async_get()
                self.request_regather()

    async def watch_focus_changes(self):
        """Tracks which terminal window has keyboard focus, and asks for a snapshot on any focus change.

        The front tab and whether iTerm2 is active come from the iterm2 App object, which handles
        the same notifications itself; the snapshot is built after the coalescing delay, by which
        time it has.
        """
        async with iterm2.FocusMonitor(self.connection) as monitor:
            while True:
                focus_update = await monitor.async_get_next_update()
                if focus_update.window_changed is not None:
                    self.update_key_terminal_window(focus_update.window_changed)
                self.request_snapshot()

    def update_key_terminal_window(self, window_changed):
        """Tracks which terminal window has keyboard focus, whatever order iTerm2 reports the
        old window resigning and the new one becoming key in."""
        reasons = iterm2.FocusUpdateWindowChanged.Reason
        if window_changed.event == reasons.TERMINAL_WINDOW_BECAME_KEY:
            self.key_terminal_window_id = window_changed.window_id
        elif window_changed.event == reasons.TERMINAL_WINDOW_IS_CURRENT:
            self.key_terminal_window_id = None  # a non-terminal window such as Settings has keyboard focus
        elif window_changed.event == reasons.TERMINAL_WINDOW_RESIGNED_KEY and self.key_terminal_window_id == window_changed.window_id:
            self.key_terminal_window_id = None

    # ----- gathering -----

    async def gather_tab(self, tab):
        """Reads one tab and its first-pane session into a plain dict, one request for each."""
        first_session = tab.sessions[0] if tab.sessions else None
        tab_variables_reading = read_iterm2_variables(self.connection, ["title", "titleOverrideFormat"], tab_id=tab.tab_id)
        gathered_session = None
        if first_session is None:
            title, title_override_format = await tab_variables_reading
        else:
            (title, title_override_format), (job_pid, command_line, status, path) = await asyncio.gather(
                tab_variables_reading, read_iterm2_variables(self.connection, ["jobPid", "commandLine", "status", "path"],
                                                             session_id=first_session.session_id))
            if not status and command_line and not command_line_runs_a_shell(command_line):
                # As after iTerm2 restarts: the status its tab bar shows (the variable then takes over).
                status = await read_status_shown_in_iterm2s_tab_bar(first_session)
            job_process_id = parse_job_process_id(job_pid)
            job_process = read_process_info(job_process_id) if job_process_id else None
            gathered_session = {
                "session_id": first_session.session_id,
                "command_line": command_line,
                "job_pid": job_process_id,
                "job_folder": read_process_current_folder(job_process_id),
                "job_started_at": job_process.started_at if job_process is not None else None,
                "status_set_in_iterm2": status,
                "status_set_in_iterm2_at": self.status_set_in_iterm2_at_by_session_id.get(first_session.session_id),
                "status": status,  # the one the row goes by (DashboardCore.update_session_tracking)
                "path": path or "",
            }
        return {
            "tab_id": tab.tab_id,
            "title": title or "",
            "title_override_format": title_override_format,
            "first_session": gathered_session,
        }

    async def gather_window(self, window):
        """Reads one window and its tabs into a plain dict; a tab that closes meanwhile is left
        out (and another gather is requested). Raises if the window itself is gone."""

        async def gather_tab_unless_it_closed(tab):
            try:
                return await self.gather_tab(tab)
            except iterm2.RPCException:
                self.request_regather()
                return None

        frame, (title, title_override_format), *gathered_tabs = await asyncio.gather(
            read_window_frame(window),
            read_iterm2_variables(self.connection, ["title", "titleOverrideFormat"], window_id=window.window_id),
            *(gather_tab_unless_it_closed(tab) for tab in window.tabs))
        return {
            "window_id": window.window_id,
            "terminal_name": self.terminal_name,
            "frame": frame,
            "title": title or "",
            "title_override_format": title_override_format,
            "tabs": [gathered_tab for gathered_tab in gathered_tabs if gathered_tab is not None],
        }

    async def gather_windows(self):
        """Reads every window, tab and first-pane session that the snapshot needs into plain dicts.

        All windows and tabs are read at the same time, so a gather takes about as long as one
        round trip to iTerm2 rather than one per tab (20 windows with 100 tabs stay quick). A
        window or tab that closes while it is being read makes iTerm2 answer with an error; it
        is skipped, and another gather is requested so the list catches up.
        """

        async def gather_window_unless_it_closed(window):
            try:
                return await self.gather_window(window)
            except (iterm2.RPCException, iterm2.GetPropertyException):
                self.request_regather()
                return None

        self.gather_is_in_progress = True
        try:
            try:
                await self.app.async_refresh()
            except iterm2.RPCException as error:  # for example the app list changed under the refresh
                raise TerminalRequestFailed(f"iTerm2's windows couldn't be read: {error!r}") from error
            gathered_windows = await asyncio.gather(*(gather_window_unless_it_closed(window)
                                                      for window in self.app.terminal_windows))
        finally:
            self.gather_is_in_progress = False
        self.gathered_windows = [gathered_window for gathered_window in gathered_windows if gathered_window is not None]
        live_session_ids = {gathered_tab["first_session"]["session_id"] for gathered_window in self.gathered_windows
                            for gathered_tab in gathered_window["tabs"] if gathered_tab["first_session"]}
        self.status_set_in_iterm2_at_by_session_id = {
            session_id: changed_at for session_id, changed_at in self.status_set_in_iterm2_at_by_session_id.items()
            if session_id in live_session_ids}
        return self.gathered_windows

    def find_selected_tab_id(self, window_id):
        """The App object follows tab switches itself, without a regather."""
        window = self.app.get_window_by_id(window_id) if self.app is not None else None
        return window.current_tab.tab_id if window is not None and window.current_tab is not None else None

    def find_front_tab_id(self):
        """Returns the selected tab of iTerm2's current terminal window, as the iterm2 App object
        tracks it from focus notifications (it ignores windows resigning focus), or None."""
        current_window = self.app.current_terminal_window if self.app is not None else None
        if current_window is None or current_window.current_tab is None:
            return None
        return current_window.current_tab.tab_id

    def a_terminal_window_has_keyboard_focus(self):
        """Whether iTerm2 is active and a terminal window (not, for example, iTerm2's Settings) has keyboard focus."""
        return self.app is not None and self.app.app_active and self.key_terminal_window_id is not None

    def list_application_process_ids(self):
        return self.iterm2_process_ids

    def find_session(self, session_id):
        session = self.app.get_session_by_id(session_id) if self.app is not None else None
        return Iterm2TerminalSession(session) if session is not None else None

    def remember_what_is_in_front(self):
        """Returns the current terminal window and each window's selected tab. The App's current
        window, not the key window: while the dropdown is open the dashboard is active and no
        iTerm2 window is key."""
        if self.app is None:
            return None
        current_window = self.app.current_terminal_window
        return {"key_window_id": current_window.window_id if current_window is not None else None,
                "selected_tab_id_by_window_id": {window.window_id: window.current_tab.tab_id
                                                 for window in self.app.terminal_windows if window.current_tab is not None}}

    async def put_back_what_was_in_front(self, what_was_in_front, bring_back_the_front_window):
        """Each window gets back the tab it had selected, and, when iTerm2 was in front before,
        its front window comes back to the front, with iTerm2 active again: a peek at a tmux tab
        shown in another terminal app made that app active."""
        if what_was_in_front is None or self.app is None:
            return ActionResult(True)
        for window in self.app.terminal_windows:
            tab_id_before = what_was_in_front["selected_tab_id_by_window_id"].get(window.window_id)
            if tab_id_before and window.current_tab is not None and window.current_tab.tab_id != tab_id_before:
                tab_before = self.app.get_tab_by_id(tab_id_before)
                if tab_before is not None:
                    await tab_before.async_select(order_window_front=False)
        window_id_to_bring_back = what_was_in_front["key_window_id"] if bring_back_the_front_window else None
        window_to_bring_back = self.app.get_window_by_id(window_id_to_bring_back) if window_id_to_bring_back else None
        if window_to_bring_back is not None:
            await window_to_bring_back.async_activate()
            await self.app.async_activate(raise_all_windows=False)  # the window's own activation leaves the app as it is
        return ActionResult(True)

    # ----- actions -----

    async def focus_session(self, session_id):
        """Brings the session's window and tab to the front, without raising iTerm2's other windows."""
        session = self.app.get_session_by_id(session_id)
        if session is None:
            return ActionResult(False, "that tab no longer exists")
        await session.async_activate(select_tab=True, order_window_front=True)
        await self.app.async_activate(raise_all_windows=False)
        return ActionResult(True)

    async def focus_window(self, window_id):
        """Brings a window to the front with the tab it already shows (which may run no agent),
        without raising iTerm2's other windows."""
        window = self.app.get_window_by_id(window_id)
        if window is None:
            return ActionResult(False, "that window no longer exists")
        await window.async_activate()
        await self.app.async_activate(raise_all_windows=False)
        return ActionResult(True)

    async def rename_tab(self, tab_id, new_title):
        """Sets a tab's title as iTerm2's Edit Tab Title does; an empty title restores the default name."""
        tab = self.app.get_tab_by_id(tab_id)
        if tab is None:
            return ActionResult(False, "that tab no longer exists")
        await tab.async_set_title(escape_text_for_iterm2_interpolated_string(new_title.strip()))
        return ActionResult(True)

    async def rename_window(self, window_id, new_title):
        """Sets a window's title as iTerm2's Edit Window Title does; an empty title restores the default name."""
        window = self.app.get_window_by_id(window_id)
        if window is None:
            return ActionResult(False, "that window no longer exists")
        await window.async_set_title(escape_text_for_iterm2_interpolated_string(new_title.strip()))
        return ActionResult(True)

    async def close_window(self, window_id):
        """Closes a window and ends the programs in its tabs. The dashboard has already asked the
        user, so iTerm2 is told not to ask again."""
        window = self.app.get_window_by_id(window_id)
        if window is None:
            return ActionResult(False, "that window no longer exists")
        await window.async_close(force=True)
        return ActionResult(True)

    async def move_tab(self, tab_id, target_window_id, drop_before_tab_id):
        """Moves a tab within its window or into another window, keeping its program running.

        Args:
            tab_id: The dragged tab.
            target_window_id: The window it was dropped into (its own window for a reorder).
            drop_before_tab_id: The listed tab it was dropped before, or None for after the last tab.
        """
        tab = self.app.get_tab_by_id(tab_id)
        target_window = self.app.get_window_by_id(target_window_id)
        if tab is None or target_window is None:
            return ActionResult(False, "that tab or window no longer exists")
        tabs_by_tab_id = {target_tab.tab_id: target_tab for target_tab in target_window.tabs}
        tabs_by_tab_id[tab_id] = tab
        # Every tab of the window, those the list hides (no agent) too: iTerm2 needs them all, and a
        # hidden tab keeps its place next to its neighbors. A tab from another window is new to them.
        new_tab_order = move_id_before_another([target_tab.tab_id for target_tab in target_window.tabs], tab_id, drop_before_tab_id)
        await target_window.async_set_tabs([tabs_by_tab_id[ordered_tab_id] for ordered_tab_id in new_tab_order])
        return ActionResult(True)

    async def move_tab_to_new_window(self, tab_id):
        """Moves a tab into a new window of its own (iTerm2 refuses this for a window's only tab)."""
        tab = self.app.get_tab_by_id(tab_id)
        if tab is None:
            return ActionResult(False, "that tab no longer exists")
        window = self.app.get_window_for_tab(tab_id)
        if window is not None and len(window.tabs) < 2:
            return ActionResult(False, "the tab is already its window's only tab")
        await tab.async_move_to_window()
        return ActionResult(True)

    async def start_agent_session(self, window_id, folder, launch_command, after_tab_id=None):
        """Opens a new tab in a window (or a new window when window_id is None) running an agent
        tool, right after after_tab_id when given (Duplicate, like Chrome's).

        Args:
            window_id: The window for the new tab, or None for a new window.
            folder: The folder the agent starts in (one that exists; the core checks it).
            launch_command: The shell command that starts the agent tool, for example "claude".
            after_tab_id: The tab the new one goes right after, or None to put it last.

        Returns:
            An ActionResult with details {"tab_id": the new tab's ID}, plus {"window_id": its ID}
            for a new window; None when iTerm2 gives no tab or window (its session ended right away).
        """
        profile = build_new_agent_session_profile(folder, launch_command)
        if window_id is None:
            # Its ID lets the lists select it once its agent shows (user request, 2026-10-30), and the
            # tab's lets a restored session's tab get its saved name (user request, 2026-11-06).
            window = await iterm2.Window.async_create(self.connection, profile_customizations=profile)
            return ActionResult(True, details={"window_id": window.window_id, "tab_id": window.current_tab.tab_id}
                                if window is not None and window.current_tab is not None else None)
        window = self.app.get_window_by_id(window_id)
        if window is None:
            return ActionResult(False, "that window no longer exists")
        tab_index = None
        if after_tab_id is not None:
            tab_ids = [tab.tab_id for tab in window.tabs]
            if after_tab_id not in tab_ids:
                return ActionResult(False, "that tab no longer exists")
            tab_index = tab_ids.index(after_tab_id) + 1
        tab = await window.async_create_tab(profile_customizations=profile, index=tab_index)
        return ActionResult(True, details={"tab_id": tab.tab_id} if tab is not None else None)

    async def move_window_to_monitor(self, window_id, monitors, target_monitor_id):
        """Moves a window to another monitor, keeping its size if it fits and its relative position.

        A full-screen window can't simply get a new frame, so it leaves full screen, moves, and
        enters full screen again on the target monitor (not yet tried on a real
        second monitor).

        Args:
            window_id: The window to move.
            monitors: Monitor dicts with "monitor_id" and "visible_frame" (see DashboardCore.set_monitors).
            target_monitor_id: The monitor to move it to.
        """
        window = self.app.get_window_by_id(window_id)
        target_monitor = next((monitor for monitor in monitors if monitor["monitor_id"] == target_monitor_id), None)
        if window is None or target_monitor is None:
            return ActionResult(False, "that window or monitor no longer exists")
        was_full_screen = await window.async_get_fullscreen()
        if was_full_screen:
            await window.async_set_fullscreen(False)
            await asyncio.sleep(SECONDS_FOR_FULL_SCREEN_ANIMATION)
        window_frame = await read_window_frame(window)
        source_index = assign_windows_to_monitors_by_largest_overlap(
            {window_id: window_frame}, [monitor["frame"] for monitor in monitors])[window_id]
        new_frame = compute_window_frame_on_another_monitor(window_frame, monitors[source_index]["visible_frame"],
                                                            target_monitor["visible_frame"])
        await window.async_set_frame(convert_frame_to_iterm2_frame(new_frame))
        if was_full_screen:
            await asyncio.sleep(SECONDS_FOR_FULL_SCREEN_ANIMATION)
            await window.async_set_fullscreen(True)
        return ActionResult(True)

    # ----- window frames -----

    def find_window(self, window_id):
        """Returns the iterm2.Window, or raises TerminalRequestFailed when it is gone."""
        window = self.app.get_window_by_id(window_id) if self.app is not None else None
        if window is None:
            raise TerminalRequestFailed(f"window {window_id} no longer exists")
        return window

    async def read_window_frame(self, window_id):
        try:
            return await read_window_frame(self.find_window(window_id))
        except (iterm2.RPCException, iterm2.GetPropertyException) as error:
            raise TerminalRequestFailed(repr(error)) from error

    async def set_window_frame(self, window_id, frame):
        try:
            await self.find_window(window_id).async_set_frame(convert_frame_to_iterm2_frame(frame))
        except (iterm2.RPCException, iterm2.SetPropertyException) as error:
            raise TerminalRequestFailed(repr(error)) from error

    async def window_is_full_screen(self, window_id):
        try:
            return await self.find_window(window_id).async_get_fullscreen()
        except (iterm2.RPCException, iterm2.GetPropertyException) as error:
            raise TerminalRequestFailed(repr(error)) from error
