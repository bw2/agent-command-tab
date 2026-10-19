"""Command line entry point: `llm-sidebar-dashboard run | app | dump-snapshot`."""

import argparse
import json
import os
import subprocess
import sys
import threading
import time

RECONNECT_EXIT_STATUS = 75
LONGEST_RESTART_DELAY_SECONDS = 30
SECONDS_TO_WAIT_FOR_AGENT_NAMES = 2


def dump_snapshot(verbose):
    """Connects to iTerm2, prints the current snapshot as JSON, and exits (for tests and bug reports).

    Waits a few seconds after the first snapshot, so that names from `claude agents --json`
    (read in the background) are included.
    """
    from llm_sidebar_dashboard.iterm2_connection_thread import Iterm2ConnectionThread
    from llm_sidebar_dashboard.snapshot_data_classes import convert_snapshot_to_json_ready_dict
    from llm_sidebar_dashboard.user_interface.screen_layout import read_monitors_from_screens

    first_snapshot_arrived = threading.Event()
    received = {}

    def publish_snapshot(snapshot):
        received["snapshot"] = snapshot
        first_snapshot_arrived.set()

    def report_connection_lost(message):
        received.setdefault("problem", message)
        first_snapshot_arrived.set()

    connection_thread = Iterm2ConnectionThread(publish_snapshot, report_connection_lost, verbose)
    connection_thread.start()
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


def run_app_process(verbose):
    """Runs the menu bar app in this process; returns its exit status."""
    from llm_sidebar_dashboard.app_controller import run_menu_bar_app
    return run_menu_bar_app(verbose)


def run_with_restarts(verbose):
    """Starts the app process and restarts it, with a growing delay, whenever it exits to reconnect.

    PLAN.md section 4: the `iterm2` package can't reconnect within one process, so the app
    exits with RECONNECT_EXIT_STATUS when iTerm2's connection drops, and this supervisor starts
    it again (1 s, doubling up to 30 s, reset after a run that lasted a minute).
    """
    restart_delay_seconds = 1
    while True:
        started_at = time.time()
        command = [sys.executable, "-m", "llm_sidebar_dashboard.command_line", "app"] + (["--verbose"] if verbose else [])
        exit_status = subprocess.call(command, env=os.environ)
        if exit_status != RECONNECT_EXIT_STATUS:
            return exit_status
        if time.time() - started_at > 60:
            restart_delay_seconds = 1
        print(f"llm-sidebar-dashboard: reconnecting in {restart_delay_seconds} s")
        time.sleep(restart_delay_seconds)
        restart_delay_seconds = min(restart_delay_seconds * 2, LONGEST_RESTART_DELAY_SECONDS)


def main(arguments=None):
    """Parses the command line and runs the chosen command."""
    parser = argparse.ArgumentParser(prog="llm-sidebar-dashboard", description=__doc__)
    parser.add_argument("command", choices=["run", "app", "dump-snapshot"], nargs="?", default="run",
                        help="run: the menu bar app, restarted when iTerm2's connection drops (default); "
                             "app: the menu bar app without restarts; dump-snapshot: print the current list as JSON")
    parser.add_argument("--verbose", action="store_true", help="print diagnostics")
    parsed_arguments = parser.parse_args(arguments)
    if parsed_arguments.command == "dump-snapshot":
        return dump_snapshot(parsed_arguments.verbose)
    if parsed_arguments.command == "app":
        return run_app_process(parsed_arguments.verbose)
    return run_with_restarts(parsed_arguments.verbose)


if __name__ == "__main__":
    sys.exit(main())
