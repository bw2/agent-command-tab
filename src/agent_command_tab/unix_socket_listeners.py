"""Whether a process listens on a Unix socket, rather than the socket being a file a process left
behind when it ended (the app's hook events socket, a tmux server's socket)."""

import socket

SECONDS_TO_REACH_A_LISTENING_PROCESS = 0.5


def another_process_listens_at(socket_path):
    """Returns whether a process accepts connections on this Unix socket (not just a file left by
    a process that ended)."""
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(SECONDS_TO_REACH_A_LISTENING_PROCESS)
            connection.connect(socket_path)
        return True
    except OSError:  # no file, or no one listening (ConnectionRefusedError)
        return False
