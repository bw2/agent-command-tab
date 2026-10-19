"""Phase 0 experiment: how long does a standard-library-only hook forwarder take per event?

Starts a throwaway Unix socket listener in a temporary folder, then runs a minimal forwarder
process 30 times with a fake hook payload on standard input, with the listener up and with
it down. Touches no agent settings and no iTerm2 state.
"""

import json
import os
import socket
import statistics
import subprocess
import sys
import tempfile
import threading
import time

FORWARDER_SOURCE = r'''
import json, os, socket, sys, time
started = time.time()
event = json.loads(sys.stdin.read() or "{}")
for field in ("tool_response",):
    event.pop(field, None)
for key, value in list(event.items()):
    if isinstance(value, str) and len(value) > 2000:
        event[key] = value[:2000]
event["iterm2_session_guid"] = os.environ.get("ITERM_SESSION_ID", "").partition(":")[2]
event["forwarder_started_at"] = started
event["forwarder_parent_pid"] = os.getppid()
line = (json.dumps(event) + "\n").encode()
try:
    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    connection.settimeout(0.1)
    connection.connect(sys.argv[1])
    connection.sendall(line)
    connection.close()
except OSError:
    pass
'''

NUMBER_OF_RUNS = 30


def accept_and_discard_connections(listening_socket):
    """Accepts connections forever and reads them to the end."""
    while True:
        try:
            connection, _ = listening_socket.accept()
        except OSError:
            return
        while connection.recv(65536):
            pass
        connection.close()


def time_forwarder_runs(forwarder_path, socket_path, payload):
    """Runs the forwarder repeatedly and returns its wall-clock durations in milliseconds."""
    durations = []
    for _ in range(NUMBER_OF_RUNS):
        start = time.perf_counter()
        subprocess.run([sys.executable, "-I", "-S", forwarder_path, socket_path], input=payload, check=False)
        durations.append((time.perf_counter() - start) * 1000)
    return durations


with tempfile.TemporaryDirectory() as temporary_folder:
    forwarder_path = os.path.join(temporary_folder, "forwarder.py")
    with open(forwarder_path, "w") as forwarder_file:
        forwarder_file.write(FORWARDER_SOURCE)
    socket_path = os.path.join(temporary_folder, "events.sock")
    payload = json.dumps({"hook_event_name": "PostToolUse", "tool_name": "Write",
                          "tool_input": {"content": "x" * 50000}, "tool_response": "y" * 50000}).encode()

    listening_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listening_socket.bind(socket_path)
    listening_socket.listen(16)
    threading.Thread(target=accept_and_discard_connections, args=(listening_socket,), daemon=True).start()
    with_listener = time_forwarder_runs(forwarder_path, socket_path, payload)
    listening_socket.close()
    os.remove(socket_path)
    without_listener = time_forwarder_runs(forwarder_path, socket_path, payload)

for label, durations in (("app listening", with_listener), ("app not running", without_listener)):
    print(f"{label}: median {statistics.median(durations):.1f} ms, max {max(durations):.1f} ms")
