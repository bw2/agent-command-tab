"""Command line entry point: `agent-command-tab run | app | dump-snapshot | install-hooks | remove-hooks`."""

import argparse
import fcntl
import json
import os
import signal
import subprocess
import sys
import threading
import time

from agent_command_tab import __version__

RECONNECT_EXIT_STATUS = 75
LONGEST_RESTART_DELAY_SECONDS = 30
SECONDS_TO_WAIT_FOR_AGENT_NAMES = 2
# Before it exits, the app waits at most this long for docked windows to get their room back
# (app_controller); after Ctrl+C the supervisor gives it that and more before killing it.
SECONDS_TO_WAIT_FOR_ROOM_BACK_BEFORE_EXITING = 3
SECONDS_TO_WAIT_FOR_THE_APP_AFTER_CTRL_C = SECONDS_TO_WAIT_FOR_ROOM_BACK_BEFORE_EXITING + 7
# Held by the running supervisor and its app (take_the_running_lock), in the settings folder (settings_store.SETTINGS_FOLDER).
RUNNING_LOCK_FILE_PATH = os.path.expanduser("~/Library/Application Support/agent-command-tab/running.lock")
# Tells the app which of its open files is the lock file its supervisor handed it (run_with_restarts).
RUNNING_LOCK_FILE_DESCRIPTOR_VARIABLE = "AGENT_COMMAND_TAB_RUNNING_LOCK_FILE_DESCRIPTOR"


def dump_snapshot(verbose):
    """Connects to iTerm2, prints the current snapshot as JSON, and exits (for tests and bug reports).

    Waits a few seconds after the first snapshot, so that names from `claude agents --json`
    (read in the background) are included.
    """
    from agent_command_tab.hook_states_store import HOOK_STATES_FILE_PATH, load_hook_states
    from agent_command_tab.iterm2_connection_thread import Iterm2ConnectionThread
    from agent_command_tab.pause_records_store import PAUSE_RECORDS_FILE_PATH, load_pause_records
    from agent_command_tab.settings_store import build_core_settings, load_settings
    from agent_command_tab.snapshot_data_classes import convert_snapshot_to_json_ready_dict
    from agent_command_tab.user_interface.screen_layout import read_monitors_from_screens

    first_snapshot_arrived = threading.Event()
    received = {}

    def publish_snapshot(snapshot):
        received["snapshot"] = snapshot
        first_snapshot_arrived.set()

    def report_connection_lost(message):
        received.setdefault("problem", message)
        first_snapshot_arrived.set()

    # The app's pause records, so paused sessions are listed, and its hook states, so a turn the
    # hooks saw end shows as the app shows it (code review, 2026-10-29), kept in memory only: this
    # run never changes the app's files.
    connection_thread = Iterm2ConnectionThread(
        publish_snapshot, report_connection_lost, verbose,
        pause_record_by_tab_and_agent_to_start_with=load_pause_records(PAUSE_RECORDS_FILE_PATH),
        started_at_and_hook_state_by_agent_process_id_to_start_with=load_hook_states(HOOK_STATES_FILE_PATH))
    connection_thread.start()
    # The app's settings, so the agent tools left out in Settings are left out here too.
    connection_thread.send_core_settings(build_core_settings(load_settings()))
    connection_thread.send_monitors(read_monitors_from_screens())  # the same monitor groups as the dropdown
    if not first_snapshot_arrived.wait(timeout=30):
        print("no snapshot within 30 seconds", file=sys.stderr)
        return 1
    if "snapshot" not in received:
        print(received["problem"], file=sys.stderr)
        return 1
    time.sleep(SECONDS_TO_WAIT_FOR_AGENT_NAMES)
    print(json.dumps(convert_snapshot_to_json_ready_dict(received["snapshot"]), indent=1))
    return 0


def install_or_remove_hooks(install):
    """Adds the app's hooks to Claude Code's and Codex's settings, or takes them out, and prints
    which files changed (hooks_installer); returns the exit status."""
    from agent_command_tab.hooks_installer import HOOK_EVENTS_BY_AGENT_TOOL_NAME, install_hooks, remove_hooks
    exit_status = 0
    for agent_tool_name in HOOK_EVENTS_BY_AGENT_TOOL_NAME:
        try:
            changed_file_paths = (install_hooks if install else remove_hooks)(agent_tool_name)
        except (OSError, ValueError) as error:
            print(f"{agent_tool_name}: {error}", file=sys.stderr)
            exit_status = 1
            continue
        for file_path in changed_file_paths:
            print(f"{agent_tool_name}: {'added to' if install else 'removed from'} {file_path}")
        if not changed_file_paths:
            print(f"{agent_tool_name}: nothing to change")
        elif install:
            print(f"{agent_tool_name}: sessions started from now on use them"
                  + ("; Codex asks you to approve them first (type /hooks in Codex)" if agent_tool_name == "Codex" else ""))
    return exit_status


def run_app_process(verbose):
    """Runs the menu bar app in this process; returns its exit status."""
    from agent_command_tab.app_controller import run_menu_bar_app
    return run_menu_bar_app(verbose)


def wait_for_app_process(process):
    """Returns the app process's exit status.

    Ctrl+C in the terminal reaches the app process too, which then gives docked windows their
    room back (up to SECONDS_TO_WAIT_FOR_ROOM_BACK_BEFORE_EXITING) before exiting, so this waits
    for that rather than killing it at once
    (subprocess.call would kill it after a quarter of a second). It kills the app only if it
    hasn't exited after SECONDS_TO_WAIT_FOR_THE_APP_AFTER_CTRL_C. A plain `kill` of this
    supervisor (SIGTERM) reaches the app as Ctrl+C, so the app stops too rather than running on
    alone (code review, 2026-10-30).
    """
    def pass_a_kill_on_to_the_app_as_ctrl_c(signal_number, frame):
        process.send_signal(signal.SIGINT)
        raise KeyboardInterrupt
    handler_before = signal.signal(signal.SIGTERM, pass_a_kill_on_to_the_app_as_ctrl_c)
    try:
        return process.wait()
    except KeyboardInterrupt:
        try:
            return process.wait(timeout=SECONDS_TO_WAIT_FOR_THE_APP_AFTER_CTRL_C)
        except subprocess.TimeoutExpired:
            process.kill()
            return process.wait()
    finally:
        signal.signal(signal.SIGTERM, handler_before)


def take_the_running_lock(lock_file_path=RUNNING_LOCK_FILE_PATH):
    """Returns the lock file, open, once this process holds its lock, or None while another process
    holds it. macOS releases the lock once every process with the file open has ended, however it
    ended: the supervisor hands it to the app it starts (run_with_restarts). The file holds the
    process ID of the one that took it."""
    os.makedirs(os.path.dirname(lock_file_path), exist_ok=True)
    lock_file = open(lock_file_path, "a+")
    try:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock_file.close()
        return None
    lock_file.seek(0)
    lock_file.truncate()
    lock_file.write(f"{os.getpid()}\n")
    lock_file.flush()
    return lock_file


def take_the_running_lock_or_name_the_copy_holding_it():
    """Returns take_the_running_lock's open lock file, or None after printing that another copy
    holds the lock, with the process ID in the file. Moves the settings folder of the old name
    first: the lock file makes the new one, after which the move never happens (code review, 2026-10-29)."""
    from agent_command_tab.settings_store import move_the_settings_folder_of_the_old_name
    move_the_settings_folder_of_the_old_name()
    running_lock_file = take_the_running_lock()
    if running_lock_file is None:
        with open(RUNNING_LOCK_FILE_PATH) as lock_file:
            process_id_of_the_running_copy = lock_file.read().strip()
        print(f"agent-command-tab: already running (process {process_id_of_the_running_copy or 'unknown'}); "
              "to start it again, first choose Quit AgentCommandTab in its menu bar icon's menu")
    return running_lock_file


def run_with_restarts(verbose):
    """Starts the app process and restarts it, with a growing delay, whenever it exits to reconnect.

    The `iterm2` package can't reconnect within one process, so the app
    exits with RECONNECT_EXIT_STATUS when iTerm2's connection drops, and this supervisor starts
    it again (1 s, doubling up to 30 s, reset after a run that lasted a minute).

    Only one copy runs at a time (take_the_running_lock): another exits at once, so iTerm2
    starting the app when it opens (start_with_iterm2) adds no second copy to one still waiting to
    reconnect, and two copies never compete for the hook events (user request, 2026-10-28). The
    app it starts gets the open lock file too, so the lock stays held while either runs: an app
    left running after its supervisor ended still keeps a second copy out (code review, 2026-10-30).
    """
    from agent_command_tab.installed_copy_paths import find_python_of_this_copy
    running_lock_file = take_the_running_lock_or_name_the_copy_holding_it()  # held, open, until this supervisor returns
    if running_lock_file is None:
        return 0
    restart_delay_seconds = 1
    while True:
        started_at = time.time()
        # By the Python's path that survives `brew upgrade`, which deletes the version folder
        # sys.executable can be in (code review, 2026-10-30).
        command = [find_python_of_this_copy(), "-m", "agent_command_tab.command_line", "app"] + (["--verbose"] if verbose else [])
        exit_status = wait_for_app_process(subprocess.Popen(
            command, env=dict(os.environ, **{RUNNING_LOCK_FILE_DESCRIPTOR_VARIABLE: str(running_lock_file.fileno())}),
            pass_fds=[running_lock_file.fileno()]))
        if exit_status != RECONNECT_EXIT_STATUS:
            return exit_status
        if time.time() - started_at > 60:
            restart_delay_seconds = 1
        print(f"agent-command-tab: reconnecting in {restart_delay_seconds} s")
        time.sleep(restart_delay_seconds)
        restart_delay_seconds = min(restart_delay_seconds * 2, LONGEST_RESTART_DELAY_SECONDS)


def main(arguments=None):
    """Parses the command line and runs the chosen command."""
    parser = argparse.ArgumentParser(prog="agent-command-tab", description=__doc__)
    parser.add_argument("command", choices=["run", "app", "dump-snapshot", "install-hooks", "remove-hooks"], nargs="?",
                        default="run",
                        help="run: the menu bar app, restarted when iTerm2's connection drops (default); "
                             "app: the menu bar app without restarts; dump-snapshot: print the current list as JSON; "
                             "install-hooks, remove-hooks: add the app's hooks to Claude Code's and Codex's settings, "
                             "or take them out")
    parser.add_argument("--verbose", action="store_true", help="print diagnostics")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parsed_arguments = parser.parse_args(arguments)
    if parsed_arguments.command == "dump-snapshot":
        return dump_snapshot(parsed_arguments.verbose)
    if parsed_arguments.command in ("install-hooks", "remove-hooks"):
        return install_or_remove_hooks(parsed_arguments.command == "install-hooks")
    if parsed_arguments.command == "app":
        # Started by the supervisor, the app holds the running lock through the open lock file it
        # was handed, which no program it starts gets in turn; started directly, it takes the lock
        # itself, so it can't run next to another copy (code review, 2026-10-30).
        lock_file_descriptor_handed_over = os.environ.pop(RUNNING_LOCK_FILE_DESCRIPTOR_VARIABLE, None)
        if lock_file_descriptor_handed_over is not None:
            os.set_inheritable(int(lock_file_descriptor_handed_over), False)
        else:
            running_lock_file = take_the_running_lock_or_name_the_copy_holding_it()  # held, open, until the app ends
            if running_lock_file is None:
                return 0
        return run_app_process(parsed_arguments.verbose)
    return run_with_restarts(parsed_arguments.verbose)


if __name__ == "__main__":
    sys.exit(main())
