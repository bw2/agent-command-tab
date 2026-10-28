"""The command the app's hooks run (hook_event_forwarder): what it keeps of a hook's payload, and
that it sends one line to the app's socket and never fails or prints."""

import io
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
import unittest.mock

from agent_command_tab import hook_event_forwarder
from agent_command_tab.hook_event_forwarder import list_ancestor_processes, reduce_hook_payload

# Trimmed from the payloads Claude Code 2.1.311 sent on 2026-10-27 (spike run in a throwaway folder).
BACKGROUND_SHELL_LAUNCHED = {
    "session_id": "7699d33f", "hook_event_name": "PostToolUse", "tool_name": "Bash",
    "tool_input": {"command": "python3 -c 'import time; time.sleep(15)'", "description": "Sleep 15 seconds in background",
                   "run_in_background": True},
    "tool_response": {"stdout": "", "stderr": "", "interrupted": False, "backgroundTaskId": "bs6f2whbf"},
}
BACKGROUND_AGENT_LAUNCHED = {
    "session_id": "7699d33f", "hook_event_name": "PostToolUse", "tool_name": "Agent",
    "tool_input": {"description": "Reply with pong", "prompt": "Reply with just the word pong.", "run_in_background": True},
    "tool_response": {"isAsync": True, "status": "async_launched", "agentId": "a3494730375529fee", "description": "Reply with pong"},
}
TURN_ENDED = {
    "session_id": "7699d33f", "hook_event_name": "Stop", "last_assistant_message": "done " * 5000,
    "background_tasks": [{"id": "bs6f2whbf", "type": "shell", "status": "running", "description": "Sleep 15 seconds",
                          "command": "python3 -c 'import time; time.sleep(15)'"}],
}


def reduce(payload):
    return reduce_hook_payload(payload, "Claude Code", [[27904, "sh"]], 100.0)


class ReduceHookPayloadTests(unittest.TestCase):
    def test_a_background_shell_or_agent_launch_is_named(self):
        self.assertEqual(reduce(BACKGROUND_SHELL_LAUNCHED)["background_task_launched_id"], "bs6f2whbf")
        self.assertEqual(reduce(BACKGROUND_AGENT_LAUNCHED)["background_task_launched_id"], "a3494730375529fee")
        self.assertIsNone(reduce(dict(BACKGROUND_SHELL_LAUNCHED, tool_response={"stdout": "ok"}))["background_task_launched_id"])

    def test_only_the_fields_the_app_uses_are_kept_with_where_the_event_came_from(self):
        event = reduce(TURN_ENDED)
        self.assertEqual(set(event), {"agent_tool_name", "hook_event_name", "reason", "source", "agent_id",
                                      "background_task_launched_id", "background_tasks", "ancestor_processes", "sent_at"})
        self.assertEqual(event["background_tasks"], [{"id": "bs6f2whbf", "status": "running"}])
        self.assertEqual((event["hook_event_name"], event["agent_tool_name"], event["ancestor_processes"], event["sent_at"]),
                         ("Stop", "Claude Code", [[27904, "sh"]], 100.0))
        self.assertIsNone(reduce({"hook_event_name": "SessionEnd", "reason": "other"})["background_tasks"])  # not a list: not known

    def test_long_texts_are_cut(self):
        event = reduce({"hook_event_name": "SessionEnd", "reason": "x" * 5000})
        self.assertEqual(len(event["reason"]), hook_event_forwarder.LONGEST_TEXT_KEPT)


class ListAncestorProcessesTests(unittest.TestCase):
    def test_this_process_and_its_parent(self):
        ancestors = list_ancestor_processes(os.getpid(), 2)
        self.assertEqual([process_id for process_id, _ in ancestors], [os.getpid(), os.getppid()])
        self.assertTrue(ancestors[0][1])  # its executable's name


class SendingTests(unittest.TestCase):
    def test_one_line_reaches_the_apps_socket(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        socket_path = os.path.join(folder.name, "hook_events.sock")
        received_lines = []
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
            server.bind(socket_path)
            server.listen(1)

            def accept_one():
                connection, _ = server.accept()
                with connection, connection.makefile() as lines:
                    received_lines.extend(lines)
            accepting_thread = threading.Thread(target=accept_one)
            accepting_thread.start()
            with unittest.mock.patch.object(hook_event_forwarder, "HOOK_EVENTS_SOCKET_PATH", socket_path), \
                    unittest.mock.patch.object(sys, "stdin", io.StringIO(json.dumps(TURN_ENDED))), \
                    unittest.mock.patch.object(sys, "argv", ["hook_event_forwarder.py", "Claude Code"]):
                hook_event_forwarder.main()
            accepting_thread.join(5)
        self.assertEqual(len(received_lines), 1)
        event = json.loads(received_lines[0])
        self.assertEqual((event["hook_event_name"], event["ancestor_processes"][0][0]), ("Stop", os.getppid()))

    def test_without_the_app_it_prints_nothing_and_succeeds(self):
        # As the installed hooks run it: isolated, without site packages.
        home = tempfile.TemporaryDirectory()
        self.addCleanup(home.cleanup)
        completed = subprocess.run([sys.executable, "-I", "-S", hook_event_forwarder.__file__, "Claude Code"],
                                   input=json.dumps(TURN_ENDED), capture_output=True, text=True, timeout=30,
                                   env=dict(os.environ, HOME=home.name))
        self.assertEqual((completed.returncode, completed.stdout, completed.stderr), (0, "", ""))
        completed = subprocess.run([sys.executable, "-I", "-S", hook_event_forwarder.__file__], input="not json",
                                   capture_output=True, text=True, timeout=30)
        self.assertEqual((completed.returncode, completed.stdout, completed.stderr), (0, "", ""))


if __name__ == "__main__":
    unittest.main()
