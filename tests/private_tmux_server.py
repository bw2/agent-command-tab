"""Starts a tmux server of a test's own, on a private socket, for the tests that run real tmux panes."""

import os
import shutil
import subprocess
import tempfile

from agent_command_tab.settings_store import find_program_on_path_or_in_the_usual_folders

TMUX_PROGRAM_PATH = find_program_on_path_or_in_the_usual_folders("tmux")  # as the tmux backend finds it


def start_a_private_tmux_server(test_case, *new_session_arguments):
    """Starts a tmux server whose first session, made with new_session_arguments, starts in a new
    folder that is also tmux's HOME and holds its socket ("s"); test_case ends the server and
    deletes the folder when it is done.

    Returns:
        (the folder, the socket's path, the environment tmux runs with).
    """
    folder = tempfile.mkdtemp()
    test_case.addCleanup(shutil.rmtree, folder, True)
    socket_path = os.path.join(folder, "s")
    environment = {"PATH": "/usr/bin:/bin", "HOME": folder, "SHELL": "/bin/sh", "TERM": "xterm-256color", "LANG": "en_US.UTF-8"}
    tmux_command = [TMUX_PROGRAM_PATH, "-S", socket_path]
    subprocess.run(tmux_command + ["-f", "/dev/null", "new-session", "-d", "-c", folder, *new_session_arguments],
                   env=environment, check=True)
    test_case.addCleanup(subprocess.run, tmux_command + ["kill-server"], env=environment, capture_output=True)
    return folder, socket_path, environment
