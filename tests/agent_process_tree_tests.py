import contextlib
import os
import shlex
import signal
import subprocess
import sys
import tempfile
import time
import unittest
import unittest.mock
import uuid

from agent_command_tab.agent_process_tree import (ProcessInfo, an_agent_program_runs_through_a_shell_below,
                                                  build_command_text, child_process_is_a_background_shell,
                                                  choose_command_the_agent_is_waiting_on,
                                                  choose_detached_processes_of_the_conversation, choose_helper_process_ids,
                                                  compute_cpu_percent, find_process_ids_of_the_shells_of_claude_code_tasks,
                                                  find_the_command_a_claude_code_shell_runs,
                                                  list_child_process_ids, list_child_processes_of_the_agent, list_descendant_processes,
                                                  list_detached_processes, read_child_processes,
                                                  read_name_the_process_was_started_as,
                                                  read_process_info, read_process_resource_usage,
                                                  read_process_tree_resource_usage, list_the_files_a_process_has_open)
from condition_waiting import wait_for_the_process_named_below, wait_until

TURN_STARTED_AT = 1000.0
NOW = 1100.0
CONVERSATION_ID = "7c1b97ad-260c-4aa2-a4bd-546d7e9dfe1c"  # a Claude Code session's ID
# A Claude Code background wait loop from an earlier turn: its shell (started at 900, before the
# turn), the shell's arguments, and the sleep it runs now (started at 1050, in the turn).
WAIT_LOOP_SHELL_ARGUMENTS = ["/bin/bash", "-c", "source snapshot.sh && eval 'until ! kill -0 31762; do sleep 30; done' "
                                                "< /dev/null && pwd -P >| /tmp/claude-73e9-cwd"]
WAIT_LOOP_SHELL = ProcessInfo(4, 1, "bash", 900.0)
WAIT_LOOP_SLEEP = ProcessInfo(5, 4, "sleep", 1050.0)


@contextlib.contextmanager
def processes_below_the_agent(descendants, arguments_by_process_id):
    """Has agent_process_tree find these processes below the agent, with these arguments (none
    for the others) and no environment."""
    with unittest.mock.patch("agent_command_tab.agent_process_tree.list_descendant_processes",
                             lambda process_id, maximum_depth: descendants), \
            unittest.mock.patch("agent_command_tab.agent_process_tree.read_process_arguments_and_environment",
                                lambda process_id: (arguments_by_process_id.get(process_id, []), {})):
        yield


@contextlib.contextmanager
def sleep_running_eight_shells_down():
    """Starts `/bin/sleep 30` at the bottom of eight shells, each the child of the one before, in a
    process group of their own. Yields (the outermost shell's process ID, the sleep's ProcessInfo)
    once the sleep runs, and kills them all at the end."""
    command = "/bin/sleep 30"
    for _ in range(8):  # each shell waits on a background child, so none of them is replaced by its command
        command = f"/bin/sh -c {shlex.quote(command + ' & wait')}"
    chain = subprocess.Popen(command, shell=True, start_new_session=True)
    try:
        sleep_process = wait_for_the_process_named_below("sleep", chain.pid, maximum_depth=None)
        if sleep_process is None:
            raise AssertionError("the sleep at the bottom of the shells never ran")
        yield chain.pid, sleep_process
    finally:
        os.killpg(chain.pid, signal.SIGKILL)
        chain.wait()


class CommandTheAgentIsWaitingOnTests(unittest.TestCase):
    def test_a_command_started_in_this_turn_is_named_below_its_shell(self):
        python_process = ProcessInfo(5, 4, "python3", 1030.2)
        descendants = [ProcessInfo(2, 1, "node", 10.0),  # an MCP server started with the session
                       ProcessInfo(3, 1, "caffeinate", 1000.5),  # Claude Code's own helper
                       ProcessInfo(4, 1, "zsh", 1030.0), python_process]
        self.assertEqual(choose_command_the_agent_is_waiting_on(descendants, TURN_STARTED_AT, {"caffeinate"}, NOW),
                         (python_process, 1030.0))

    def test_only_helpers_and_older_processes_means_thinking(self):
        descendants = [ProcessInfo(2, 1, "node", 10.0), ProcessInfo(3, 1, "caffeinate", 1000.5)]
        self.assertIsNone(choose_command_the_agent_is_waiting_on(descendants, TURN_STARTED_AT, {"caffeinate"}, NOW))

    def test_a_shell_with_nothing_running_in_it_is_not_a_command(self):
        # Codex keeps a terminal open between commands.
        descendants = [ProcessInfo(4, 1, "bash", 1030.0)]
        self.assertIsNone(choose_command_the_agent_is_waiting_on(descendants, TURN_STARTED_AT, frozenset(), NOW))

    def test_a_hook_that_has_run_for_under_two_seconds_is_not_a_command(self):
        # iTerm2's Claude Code integration runs cc-status (with it2 and osascript) on every tool call.
        descendants = [ProcessInfo(6, 1, "cc-status", NOW - 0.4), ProcessInfo(7, 6, "osascript", NOW - 0.3)]
        self.assertIsNone(choose_command_the_agent_is_waiting_on(descendants, TURN_STARTED_AT, {"caffeinate"}, NOW))
        self.assertEqual(choose_command_the_agent_is_waiting_on(descendants, TURN_STARTED_AT, {"caffeinate"}, NOW + 2)[0].name,
                         "cc-status")

    def test_helpers_and_everything_below_them_are_not_commands(self):
        # Codex 0.163: its code mode host and an MCP server started around the turn's start.
        pytest_process = ProcessInfo(7, 6, "pytest", 1040.1)
        descendants = [ProcessInfo(2, 1, "codex-code-mode-host", 1006.9), ProcessInfo(3, 2, "node", 1007.0),
                       ProcessInfo(4, 1, "npm", 999.7), ProcessInfo(5, 4, "node", 999.8)]
        self.assertIsNone(choose_command_the_agent_is_waiting_on(
            descendants, TURN_STARTED_AT, {"codex-code-mode-host"}, NOW, helper_process_ids={4, 5}))
        self.assertEqual(choose_command_the_agent_is_waiting_on(
            descendants + [ProcessInfo(6, 1, "zsh", 1040.0), pytest_process],
            TURN_STARTED_AT, {"codex-code-mode-host"}, NOW, helper_process_ids={4, 5}), (pytest_process, 1040.0))


    def test_what_a_claude_code_command_left_from_an_earlier_turn_starts_now_isnt_this_turns_command(self):
        # A background wait loop from an earlier turn (its shell started at 900) starts its next sleep in this turn.
        arguments_by_process_id = {4: WAIT_LOOP_SHELL_ARGUMENTS}
        self.assertIsNone(choose_command_the_agent_is_waiting_on(
            [WAIT_LOOP_SHELL, WAIT_LOOP_SLEEP], TURN_STARTED_AT, frozenset(), NOW,
            read_process_arguments=lambda process_id: arguments_by_process_id.get(process_id, [])))
        # A terminal Codex keeps from an earlier turn still runs this turn's command.
        self.assertEqual(choose_command_the_agent_is_waiting_on(
            [WAIT_LOOP_SHELL, WAIT_LOOP_SLEEP], TURN_STARTED_AT, frozenset(), NOW, read_process_arguments=lambda process_id: ["zsh"]),
            (WAIT_LOOP_SLEEP, 1050.0))

    def test_codexs_own_program_below_its_node_launcher_is_not_a_command_but_what_it_starts_is(self):
        # Agent 1 is Codex's node launcher in a session started with its first prompt: its own
        # program (2) is as young as the turn.
        codex_program = ProcessInfo(2, 1, "codex", 1000.1)
        self.assertIsNone(choose_command_the_agent_is_waiting_on([codex_program], TURN_STARTED_AT, {"codex-code-mode-host"}, NOW))
        shell, pytest_process = ProcessInfo(3, 2, "zsh", 1040.0), ProcessInfo(4, 3, "pytest", 1040.1)
        self.assertEqual(choose_command_the_agent_is_waiting_on(
            [codex_program, shell, pytest_process], TURN_STARTED_AT, {"codex-code-mode-host"}, NOW), (pytest_process, 1040.0))
        # A `codex exec` that the agent's shell tool runs is a command: a shell started it, not the agent process.
        inner_shell, inner_codex = ProcessInfo(5, 2, "bash", 1050.0), ProcessInfo(6, 5, "codex", 1050.1)
        self.assertEqual(choose_command_the_agent_is_waiting_on(
            [codex_program, inner_shell, inner_codex], TURN_STARTED_AT, {"codex-code-mode-host"}, NOW), (inner_codex, 1050.0))

    def test_a_command_claude_code_runs_in_the_background_is_not_the_one_the_agent_waits_on(self):
        # Started in this turn: first a command in the background, then the one the agent waits on.
        background_shell, background_python = ProcessInfo(3, 1, "bash", 1001.0), ProcessInfo(4, 3, "python3", 1001.1)
        foreground_shell, foreground_python = ProcessInfo(5, 1, "bash", 1050.0), ProcessInfo(6, 5, "python3", 1050.1)
        descendants = [background_shell, background_python, foreground_shell, foreground_python]
        # By the processes alone, the older of the two is taken.
        self.assertEqual(choose_command_the_agent_is_waiting_on(descendants, TURN_STARTED_AT, frozenset(), NOW),
                         (background_python, 1001.0))
        # Each shell writes to its task's output file, and the hooks list the first task as running in the background.
        paths_written_to_by_process_id = {3: {1: "/tmp/claude-502/-repo/tasks/b7x1ehsbe.output", 2: "/tmp/claude-502/-repo/tasks/b7x1ehsbe.output"},
                                          5: {1: "/tmp/claude-502/-repo/tasks/bq2m4k0wd.output"}}
        with processes_below_the_agent(descendants, {}), unittest.mock.patch(
                "agent_command_tab.agent_process_tree.list_the_files_a_process_has_open",
                lambda process_id, only_those_open_for_writing: paths_written_to_by_process_id.get(process_id)):
            background_shell_process_ids = find_process_ids_of_the_shells_of_claude_code_tasks(descendants, {"b7x1ehsbe"})
            # Without background tasks, or before the hooks have listed them, neither shell is found.
            self.assertEqual([find_process_ids_of_the_shells_of_claude_code_tasks(descendants, task_ids) for task_ids in (set(), None)],
                             [frozenset(), frozenset()])
        self.assertEqual(background_shell_process_ids, frozenset({3}))
        self.assertEqual(choose_command_the_agent_is_waiting_on(descendants, TURN_STARTED_AT, frozenset(), NOW, background_shell_process_ids),
                         (foreground_python, 1050.0))
        # Once only the background command runs, the agent waits on none.
        self.assertIsNone(choose_command_the_agent_is_waiting_on(descendants[:2], TURN_STARTED_AT, frozenset(), NOW,
                                                                 background_shell_process_ids))

    def test_a_command_claude_codes_monitor_tool_runs_is_not_the_one_the_agent_waits_on(self):
        # Started in this turn, each in a shell of Claude Code's shell tool: first a Monitor, then the command the agent waits on.
        monitor_shell, monitor_tail = ProcessInfo(3, 1, "bash", 1001.0), ProcessInfo(4, 3, "tail", 1001.1)
        foreground_shell, foreground_python = ProcessInfo(5, 1, "bash", 1050.0), ProcessInfo(6, 5, "python3", 1050.1)
        descendants = [monitor_shell, monitor_tail, foreground_shell, foreground_python]
        arguments_by_process_id = {
            3: ["/bin/bash", "-c", "source snapshot.sh && eval 'tail -f build.log' < /dev/null && pwd -P >| /tmp/claude-1a2b-cwd"],
            5: ["/bin/bash", "-c", "source snapshot.sh && eval 'python3 run.py' < /dev/null && pwd -P >| /tmp/claude-3c4d-cwd"]}
        # By the processes alone, the older of the two is taken.
        self.assertEqual(choose_command_the_agent_is_waiting_on(descendants, TURN_STARTED_AT, frozenset(), NOW), (monitor_tail, 1001.0))
        # The Monitor's shell writes to a socket: to no file, so to none of a task the hooks could list it by.
        paths_written_to_by_process_id = {3: {}, 5: {1: "/tmp/claude-502/-repo/tasks/bq2m4k0wd.output"}}
        with processes_below_the_agent(descendants, arguments_by_process_id), unittest.mock.patch(
                "agent_command_tab.agent_process_tree.list_the_files_a_process_has_open",
                lambda process_id, only_those_open_for_writing: paths_written_to_by_process_id.get(process_id)):
            # Before the hooks have reported any task, with none running, and with another one running.
            monitor_shell_process_ids = [find_process_ids_of_the_shells_of_claude_code_tasks(descendants, task_ids)
                                         for task_ids in (None, set(), {"b7x1ehsbe"})]
        self.assertEqual(monitor_shell_process_ids, [frozenset({3})] * 3)
        self.assertEqual(choose_command_the_agent_is_waiting_on(descendants, TURN_STARTED_AT, frozenset(), NOW, monitor_shell_process_ids[0]),
                         (foreground_python, 1050.0))
        # Once only the Monitor's command runs, the agent waits on none.
        self.assertIsNone(choose_command_the_agent_is_waiting_on(descendants[:2], TURN_STARTED_AT, frozenset(), NOW,
                                                                 monitor_shell_process_ids[0]))

    def test_a_command_more_than_six_shells_down_is_the_one_the_agent_waits_on(self):
        with sleep_running_eight_shells_down() as (outermost_shell_process_id, sleep_process):
            # The processes below an agent as the dashboard lists them: at every level. (A command counts once it has run 2 s.)
            command_process, _ = choose_command_the_agent_is_waiting_on(
                list_descendant_processes(outermost_shell_process_id), time.time() - 60, frozenset(), time.time() + 60)
            self.assertEqual(command_process, sleep_process)


class ChildProcessTests(unittest.TestCase):
    """The sub rows of an open tab row and the background shells."""

    def test_claude_codes_children_are_named_by_the_command_run_in_each_shell(self):
        # As seen below a real Claude Code: its caffeinate, a `bash -c` running a
        # background python3, and one running the command it waits on.
        caffeinate = ProcessInfo(2, 1, "caffeinate", 900.0)
        background_shell, background_python = ProcessInfo(3, 1, "bash", 1001.0), ProcessInfo(4, 3, "python3", 1001.1)
        foreground_shell, foreground_python = ProcessInfo(5, 1, "bash", 1050.0), ProcessInfo(6, 5, "python3", 1050.1)
        hook_shell, hook = ProcessInfo(7, 1, "sh", 1099.5), ProcessInfo(8, 7, "cc-status", 1099.6)  # a hook, just started
        descendants = [foreground_python, foreground_shell, background_python, background_shell, caffeinate, hook_shell, hook]
        children = list_child_processes_of_the_agent(1, descendants, NOW)
        self.assertEqual(children, [(caffeinate, caffeinate), (background_shell, background_python),
                                    (foreground_shell, foreground_python)])  # oldest first, without the hook
        # Between turns, every command left running is in the background (caffeinate is a helper, not a shell).
        self.assertEqual([child_process_is_a_background_shell(child, shown, shown.started_at, False, None) for child, shown in children],
                         [False, True, True])
        # In a turn begun at 1040, only the command from before it is known to be: one started in
        # the turn may be the command the agent waits on, whichever came first.
        self.assertEqual([child_process_is_a_background_shell(child, shown, shown.started_at, True, 1040.0) for child, shown in children],
                         [False, True, False])
        self.assertEqual([child_process_is_a_background_shell(child, shown, shown.started_at, True, 1000.0) for child, shown in children],
                         [False, False, False])  # both started in the turn: neither counts until it ends
        self.assertEqual([child_process_is_a_background_shell(child, shown, shown.started_at, True, None) for child, shown in children],
                         [False, False, False])  # when the turn began isn't known yet

    def test_codexs_own_program_below_its_node_launcher_counts_as_the_agent(self):
        codex = ProcessInfo(2, 1, "codex", 100.0)
        kept_terminal = ProcessInfo(3, 2, "zsh", 200.0)  # open between commands, with nothing running
        wrapper, build = ProcessInfo(4, 2, "zsh", 300.0), ProcessInfo(6, 5, "cargo", 300.2)
        inner_shell = ProcessInfo(5, 4, "bash", 300.1)
        descendants = [codex, kept_terminal, wrapper, inner_shell, build]
        children = list_child_processes_of_the_agent(1, descendants, NOW)
        self.assertEqual(children, [(kept_terminal, kept_terminal), (wrapper, build)])  # shells inside shells are looked through
        self.assertEqual([child_process_is_a_background_shell(child, shown, shown.started_at, False, None) for child, shown in children],
                         [False, True])

    def test_a_claude_code_wait_loop_from_an_earlier_turn_stays_in_the_background_in_this_one(self):
        # Its shell started at 900, before the turn; the sleep it runs now started at 1050, in the turn.
        with processes_below_the_agent([WAIT_LOOP_SHELL, WAIT_LOOP_SLEEP], {4: WAIT_LOOP_SHELL_ARGUMENTS, 5: ["sleep", "30"]}):
            (entry,) = read_child_processes(1, True, TURN_STARTED_AT, False, NOW)
        self.assertEqual(entry["command_text"], "until ! kill -0 31762; do sleep 30; done")
        self.assertEqual(entry["command_started_at"], 900.0)
        self.assertTrue(entry["is_background_shell"])

    def test_a_command_in_a_terminal_codex_kept_from_an_earlier_turn_goes_by_the_commands_start(self):
        # Codex runs a later turn's command in a shell started in an earlier one.
        descendants = [ProcessInfo(2, 1, "codex", 10.0), ProcessInfo(7, 2, "zsh", 20.0), ProcessInfo(8, 7, "python3", 1030.0)]
        with processes_below_the_agent(descendants, {7: ["zsh"], 8: ["python3", "train.py"]}):
            (entry,) = read_child_processes(1, True, TURN_STARTED_AT, False, NOW)
            (entry_in_a_later_turn,) = read_child_processes(1, True, 1100.0, False, NOW + 100)
        self.assertEqual((entry["command_text"], entry["command_started_at"]), ("python3 train.py", 1030.0))
        self.assertFalse(entry["is_background_shell"])  # it may be the command the agent waits on
        self.assertTrue(entry_in_a_later_turn["is_background_shell"])

    def test_the_arguments_of_a_child_that_is_shown_as_itself_are_read_once(self):
        python_process = ProcessInfo(2, 1, "python3", 1000.0)  # a command the agent runs without a shell
        read_arguments_and_environment = unittest.mock.Mock(return_value=(["python3", "train.py"], {}))
        with unittest.mock.patch("agent_command_tab.agent_process_tree.list_descendant_processes",
                                 lambda process_id, maximum_depth: [python_process]), \
                unittest.mock.patch("agent_command_tab.agent_process_tree.read_process_arguments_and_environment",
                                    read_arguments_and_environment):
            (entry,) = read_child_processes(1, False, None, False, NOW)
        self.assertEqual(entry["command_text"], "python3 train.py")
        read_arguments_and_environment.assert_called_once_with(2)  # for whether it is a Claude Code shell, and for its name

    def test_a_command_is_named_by_its_program_and_arguments(self):
        self.assertEqual(build_command_text(["/opt/homebrew/bin/python3", "-m", "unittest"], "Python"), "python3 -m unittest")
        self.assertEqual(build_command_text(["bash", "-c", "echo a\necho b"], "bash"), "bash -c echo a echo b")
        self.assertEqual(build_command_text([], "caffeinate"), "caffeinate")
        # A Claude Code scratchpad folder is just "scratchpad", so the end of the command shows.
        self.assertEqual(build_command_text(
            ["bash", f"/private/tmp/claude-502/-Users-someone-code-x/{CONVERSATION_ID}/scratchpad/step5/run.sh"], "bash"),
            "bash scratchpad/step5/run.sh")

    def test_claude_codes_shell_is_named_by_the_command_it_was_given(self):
        # As Claude Code 2.1 runs a background wait loop; a ' in the command is '"'"'.
        wrapper = ("source /Users/someone/.claude/shell-snapshots/snapshot-bash-1-x.sh 2>/dev/null || true && shopt -u "
                   "extglob 2>/dev/null || true && eval 'until ! kill -0 31762; do sleep 30; done; grep -hE '\"'\"'DONE|KILL'\"'\"' "
                   "a.log' < /dev/null && pwd -P >| /tmp/claude-73e9-cwd")
        self.assertEqual(find_the_command_a_claude_code_shell_runs(["/bin/bash", "-c", wrapper]),
                         "until ! kill -0 31762; do sleep 30; done; grep -hE 'DONE|KILL' a.log")
        # A command with a heredoc has no `< /dev/null` after it.
        heredoc_wrapper = ("source /Users/someone/.claude/shell-snapshots/snapshot-bash-1-x.sh 2>/dev/null || true && eval "
                           "'python3 << '\"'\"'EOF'\"'\"'\nprint(1)\nEOF' && pwd -P >| /tmp/claude-aa1e-cwd")
        self.assertEqual(find_the_command_a_claude_code_shell_runs(["/bin/bash", "-c", heredoc_wrapper]),
                         "python3 << 'EOF'\nprint(1)\nEOF")
        self.assertIsNone(find_the_command_a_claude_code_shell_runs(["bash", "-c", "sleep 30"]))  # Codex's, or anyone's
        self.assertIsNone(find_the_command_a_claude_code_shell_runs(["python3", "train.py"]))

    def test_detached_processes_go_to_the_conversation_whose_id_their_arguments_or_folder_name(self):
        scratchpad = f"/private/tmp/claude-502/-Users-someone-code-x/{CONVERSATION_ID}/scratchpad"
        script_run = ProcessInfo(40, 1, "bash", 1020.0)
        run_in_scratchpad = ProcessInfo(41, 1, "python3", 1010.0)
        other = ProcessInfo(42, 1, "Safari", 1030.0)
        from_before_the_agent = ProcessInfo(43, 1, "bash", 900.0)  # a process ID reused, or an earlier conversation's
        detached_processes = [(script_run, ["bash", f"{scratchpad}/run.sh"], "/Users/someone/code/x"),
                              (run_in_scratchpad, ["python3", "train.py"], scratchpad + "/step4"),
                              (other, ["/Applications/Safari.app/Contents/MacOS/Safari"], "/"),
                              (from_before_the_agent, ["bash", f"{scratchpad}/old.sh"], "/")]
        self.assertEqual(choose_detached_processes_of_the_conversation(detached_processes, CONVERSATION_ID, 1000.0),
                         [(run_in_scratchpad, ["python3", "train.py"]), (script_run, ["bash", f"{scratchpad}/run.sh"])])
        self.assertEqual(choose_detached_processes_of_the_conversation(detached_processes, None, 1000.0), [])  # Codex: no ID

    def test_codex_shared_background_server_started_in_the_scratchpad_is_not_the_conversation_job(self):
        # As Codex 0.167 starts it: a codex run in the scratchpad starts the server, which keeps that folder.
        scratchpad = f"/private/tmp/claude-502/-Users-someone-code-x/{CONVERSATION_ID}/scratchpad"
        codex_program = "/Users/someone/.codex/packages/app-server-daemon/releases/0.167.0-aarch64-apple-darwin/bin/codex"
        server = ProcessInfo(50, 1, "codex", 1010.0)
        server_helper = ProcessInfo(51, 1, "codex", 1011.0)
        job = ProcessInfo(52, 1, "python3", 1012.0)
        detached_processes = [
            (server, [codex_program, "app-server", "--listen", "unix://", "--analytics-default-enabled", "--managed-daemon"],
             scratchpad + "/test_folder"),
            (server_helper, [codex_program, "app-server", "daemon", "pid-update-loop"], scratchpad + "/test_folder"),
            (job, ["python3", "train.py"], scratchpad + "/test_folder")]
        self.assertEqual(choose_detached_processes_of_the_conversation(detached_processes, CONVERSATION_ID, 1000.0),
                         [(job, ["python3", "train.py"])])

    def test_a_real_detached_process_is_found_and_listed_in_the_background(self):
        # A shell starts python3 in the background and ends, so launchd adopts python3.
        started_at = time.time() - 1
        # Its own ID, so the same test run at the same time elsewhere (another agent) isn't found too.
        conversation_id = str(uuid.uuid4())
        shell = subprocess.run(["/bin/sh", "-c", f'"$0" -c "import time; time.sleep(30)" {conversation_id} >/dev/null 2>&1 & echo $!',
                                sys.executable], capture_output=True, text=True, check=True)
        detached_process_id = int(shell.stdout.strip())
        try:
            found = []

            def python_runs_with_the_conversation_id():
                # Between its fork and its exec it has the shell's arguments, which name the ID too.
                # (Not by sys.executable: a venv's python runs on as the framework's Python.app.)
                found[:] = choose_detached_processes_of_the_conversation(list_detached_processes(started_at), conversation_id, started_at)
                return bool(found) and found[0][1][1:3] == ["-c", "import time; time.sleep(30)"]
            self.assertTrue(wait_until(python_runs_with_the_conversation_id, seconds=10), found)
            self.assertEqual([process.process_id for process, _ in found], [detached_process_id])
            self.assertEqual(found[0][0].parent_process_id, 1)
            (entry,) = [entry for entry in read_child_processes(os.getpid(), False, None, True, time.time(), found)
                        if entry["child"].process_id == detached_process_id]
            self.assertTrue(entry["is_background_shell"])  # it runs in the background, on its own
            self.assertIn(f"import time; time.sleep(30) {conversation_id}", entry["command_text"])
            self.assertGreater(entry["resource_usage"][0], 0)
        finally:
            os.kill(detached_process_id, signal.SIGKILL)

    def test_a_shell_is_known_by_the_claude_code_task_file_it_writes_to(self):
        with tempfile.TemporaryDirectory() as folder:
            os.makedirs(os.path.join(folder, "tasks"))
            shells = {}

            def start_shell(standard_output, command="sleep 30; true"):
                # Its input read only (a terminal this process reads is open for writing too), its
                # errors where its output goes, not to this process's (in Claude Code, a task file
                # too), and in a process group of its own, so the sleep in it is killed with it.
                with open(os.devnull) as nothing_to_read:
                    return subprocess.Popen(["/bin/bash", "-c", command], stdin=nothing_to_read, stdout=standard_output,
                                            stderr=subprocess.STDOUT, start_new_session=True)
            for name, output_file_name, command in (
                    ("task", os.path.join("tasks", "b3jvsqvs2.output"), "sleep 30; true"),
                    # Bash sends the loop's output to /dev/null and keeps its own on another descriptor.
                    ("loop sent to /dev/null", os.path.join("tasks", "b5loop.output"), "while true; do sleep 30; done > /dev/null 2>&1"),
                    ("other file", "notes.txt", "sleep 30; true")):
                with open(os.path.join(folder, output_file_name), "w") as output_file:
                    shells[name] = start_shell(output_file, command)
            shells["pipe"] = start_shell(subprocess.PIPE)  # as a Monitor's socket
            # A Monitor's shell: Claude Code's shell tool wrapper, writing to a socket.
            shells["monitor"] = start_shell(subprocess.PIPE, "true && eval 'sleep 30; true' < /dev/null && pwd -P >| /dev/null")
            shells["device"] = start_shell(subprocess.DEVNULL)
            try:
                self.assertEqual(list_the_files_a_process_has_open(shells["device"].pid, only_those_open_for_writing=True),
                                 {1: "/dev/null", 2: "/dev/null"})
                self.assertEqual(list_the_files_a_process_has_open(shells["pipe"].pid, only_those_open_for_writing=True), {})
                entries_by_process_id = {}

                def every_shell_runs_its_sleep():  # which then names it (a shell counts once it has run 2 s)
                    entries_by_process_id.clear()
                    entries_by_process_id.update({entry["child"].process_id: entry for entry in read_child_processes(
                        os.getpid(), False, None, False, time.time() + 60) if entry["child"].process_id in {
                            shell.pid for shell in shells.values()}})
                    return len(entries_by_process_id) == len(shells) and all(
                        not entry["command_text"].startswith(("sh ", "bash ")) for entry in entries_by_process_id.values())
                self.assertTrue(wait_until(every_shell_runs_its_sleep, seconds=10), entries_by_process_id)
                self.assertEqual(
                    {name: (entries_by_process_id[shell.pid]["claude_code_task_id"],
                            entries_by_process_id[shell.pid]["is_a_shell_writing_to_a_file_of_no_claude_code_task"],
                            entries_by_process_id[shell.pid]["is_a_claude_code_monitor_shell"])
                     for name, shell in shells.items()},
                    {"task": ("b3jvsqvs2", False, False), "loop sent to /dev/null": ("b5loop", False, False),
                     "other file": (None, True, False), "pipe": (None, False, False), "monitor": (None, False, True),
                     "device": (None, False, False)})
                # The shell of a task that runs in the background is found among them by that file,
                # and the Monitor's with it, also when no task is known.
                shell_processes = [read_process_info(shell.pid) for shell in shells.values()]
                self.assertEqual(find_process_ids_of_the_shells_of_claude_code_tasks(shell_processes, {"b5loop"}),
                                 frozenset({shells["loop sent to /dev/null"].pid, shells["monitor"].pid}))
                self.assertEqual(find_process_ids_of_the_shells_of_claude_code_tasks(shell_processes, None),
                                 frozenset({shells["monitor"].pid}))
            finally:
                for shell in shells.values():
                    os.killpg(shell.pid, signal.SIGKILL)
                    shell.wait()
                    if shell.stdout is not None:
                        shell.stdout.close()

    def test_a_real_child_of_this_process(self):
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        try:
            child_processes = []

            def the_child_is_read():
                child_processes[:] = read_child_processes(os.getpid(), False, None, True, time.time() + 60)  # (it counts once it has run 2 s)
                return any(entry["child"].process_id == child.pid for entry in child_processes)
            self.assertTrue(wait_until(the_child_is_read, seconds=10), "the child was never read")
            entry = next(entry for entry in child_processes if entry["child"].process_id == child.pid)
            self.assertIn("import time; time.sleep(30)", entry["command_text"])
            self.assertEqual(entry["command_started_at"], entry["child"].started_at)  # not a shell: the command itself
            self.assertFalse(entry["is_background_shell"])  # not a shell
            self.assertFalse(entry["is_a_shell_writing_to_a_file_of_no_claude_code_task"])
            self.assertFalse(entry["is_a_claude_code_monitor_shell"])
            self.assertGreater(entry["resource_usage"][0], 0)  # its memory
        finally:
            child.kill()
            child.wait()

    def test_a_command_more_than_six_shells_down_names_its_row_and_counts_as_left_in_the_background(self):
        with sleep_running_eight_shells_down() as (outermost_shell_process_id, _):
            # (A child counts once it has run 2 s.)
            (entry,) = [entry for entry in read_child_processes(os.getpid(), False, None, False, time.time() + 60)
                        if entry["child"].process_id == outermost_shell_process_id]
            self.assertEqual(entry["command_text"], "sleep 30")  # not the outermost shell
            self.assertTrue(entry["is_background_shell"])  # between turns, a shell with a command running in it


class HelperProcessTests(unittest.TestCase):
    def test_commands_below_codex_and_its_open_terminal_still_count_after_an_idle_recording(self):
        # Agent 1 is Codex's node launcher, with its own program (4), an MCP server (9) and an open terminal (7).
        between_turns = [ProcessInfo(4, 1, "codex", 10.0), ProcessInfo(9, 4, "node", 10.5), ProcessInfo(7, 4, "bash", 20.0)]
        helper_process_ids = choose_helper_process_ids(between_turns)
        self.assertEqual(helper_process_ids, frozenset({9}))
        python_process = ProcessInfo(8, 7, "python3", 1030.0)
        self.assertEqual(choose_command_the_agent_is_waiting_on(between_turns + [python_process], TURN_STARTED_AT,
                                                                {"codex-code-mode-host"}, NOW, helper_process_ids),
                         (python_process, 1030.0))


class AgentProgramInsideAnAgentTests(unittest.TestCase):
    def test_an_agent_program_counts_only_when_a_shell_started_it(self):
        started_as_names = {3: "claude", 5: "node", 6: "codex", 8: "python3"}
        # A `claude -p` run by Claude Code's shell tool (agent 1, its shell 2).
        self.assertTrue(an_agent_program_runs_through_a_shell_below(
            [ProcessInfo(2, 1, "zsh", 10.0), ProcessInfo(3, 2, "2.1.310", 11.0)], started_as_names.get))
        # A `codex exec` run by Codex's shell tool, below its node launcher (agent 1 is Codex's node launcher).
        self.assertTrue(an_agent_program_runs_through_a_shell_below(
            [ProcessInfo(4, 1, "codex", 10.0), ProcessInfo(7, 4, "bash", 11.0), ProcessInfo(5, 7, "node", 11.5),
             ProcessInfo(6, 5, "codex", 11.6)], started_as_names.get))
        # Codex's own program below its node launcher, and a command that isn't an agent.
        self.assertFalse(an_agent_program_runs_through_a_shell_below(
            [ProcessInfo(6, 1, "codex", 10.0), ProcessInfo(7, 6, "bash", 11.0), ProcessInfo(8, 7, "python3", 11.5)],
            started_as_names.get))


class ProcessTreeReadingTests(unittest.TestCase):
    def test_this_test_process_is_below_its_parent(self):
        own_process = read_process_info(os.getpid())
        self.assertEqual(own_process.parent_process_id, os.getppid())
        self.assertLess(own_process.started_at, time.time() + 1)
        self.assertIn(os.getpid(), [process.process_id for process in list_descendant_processes(os.getppid())])

    def test_a_process_that_is_gone(self):
        self.assertIsNone(read_process_info(999999))
        self.assertEqual(read_name_the_process_was_started_as(999999), "")
        self.assertIsNone(list_the_files_a_process_has_open(999999))
        self.assertEqual(list_child_process_ids(999999), [])

    def test_every_child_is_listed_when_there_are_more_than_the_first_list_has_room_for(self):
        children = [subprocess.Popen(["/bin/sleep", "30"]) for _ in range(5)]
        try:
            # As for launchd, whose children can outnumber the IDs asked for at first.
            with unittest.mock.patch("agent_command_tab.agent_process_tree.CHILD_PROCESS_IDS_LISTED_AT_FIRST", 2):
                child_process_ids = list_child_process_ids(os.getpid())
            self.assertLessEqual({child.pid for child in children}, set(child_process_ids))
        finally:
            for child in children:
                child.kill()
                child.wait()

    def test_the_folders_and_files_a_process_has_open_only_for_reading_are_listed_too(self):  # as agy's presence lock
        with tempfile.TemporaryDirectory() as folder:
            folder = os.path.realpath(folder)  # /private/var/..., as the kernel names it
            lock_path = os.path.join(folder, "conversation.lock")
            open(lock_path, "w").close()
            folder_descriptor, lock_descriptor = os.open(folder, os.O_RDONLY), os.open(lock_path, os.O_RDONLY)
            try:
                self.assertLessEqual({folder_descriptor: folder, lock_descriptor: lock_path}.items(),
                                     list_the_files_a_process_has_open(os.getpid()).items())
                self.assertFalse({folder, lock_path} & set(
                    list_the_files_a_process_has_open(os.getpid(), only_those_open_for_writing=True).values()))
            finally:
                os.close(folder_descriptor)
                os.close(lock_descriptor)

    def test_a_program_started_through_a_symbolic_link_is_named_as_typed(self):
        # As ~/.local/bin/claude links to an executable named after its version.
        with tempfile.TemporaryDirectory() as folder:
            link_path = os.path.join(folder, "my-tool")
            os.symlink("/bin/sleep", link_path)
            process = subprocess.Popen([link_path, "30"])
            try:
                self.assertEqual(read_process_info(process.pid).name, "sleep")
                self.assertEqual(read_name_the_process_was_started_as(process.pid), "my-tool")
            finally:
                process.kill()
                process.wait()


class ResourceUsageTests(unittest.TestCase):
    def test_a_process_tree_counts_its_running_descendants_memory_and_cpu_time(self):
        child = subprocess.Popen([sys.executable, "-c", "while True: pass"])  # uses CPU time, unlike a sleep
        try:
            self.assertTrue(wait_until(lambda: read_process_resource_usage(child.pid)[1] > 0.2))
            # Read in this order, each total only grows afterwards, so the tree's share beyond this
            # process's own is at least the child's.
            child_memory, child_cpu_seconds = read_process_resource_usage(child.pid)
            own_memory, own_cpu_seconds = read_process_resource_usage(os.getpid())
            tree_memory, tree_cpu_seconds = read_process_tree_resource_usage(os.getpid())
            self.assertGreater(own_memory, 1024 ** 2)
            self.assertGreater(tree_memory - own_memory, child_memory / 2)
            self.assertGreaterEqual(tree_cpu_seconds - own_cpu_seconds, child_cpu_seconds)
        finally:
            child.kill()
            child.wait()
        self.assertIsNone(read_process_tree_resource_usage(child.pid))  # gone

    def test_resource_totals_reach_a_command_more_than_six_levels_down(self):
        with sleep_running_eight_shells_down() as (outermost_shell_process_id, sleep_process):
            self.assertNotIn(sleep_process.process_id, [process.process_id for process in list_descendant_processes(
                outermost_shell_process_id, maximum_depth=6)])  # more than six levels down
            # Only the sleep is given any memory and CPU time here (not the shells above it, which
            # really use some), so the totals are these exactly when the sleep itself is counted.
            with unittest.mock.patch("agent_command_tab.agent_process_tree.read_process_resource_usage",
                                     lambda process_id: (4096, 1.5) if process_id == sleep_process.process_id else (0, 0.0)):
                self.assertEqual(read_process_tree_resource_usage(outermost_shell_process_id), (4096, 1.5))

    def test_a_finished_childs_cpu_time_stays_in_the_tree(self):
        _, cpu_seconds_before = read_process_tree_resource_usage(os.getpid())
        busy_loop = "import time\nend = time.process_time() + 0.3\nwhile time.process_time() < end: pass"
        subprocess.run([sys.executable, "-c", busy_loop], check=True)
        _, cpu_seconds_after = read_process_tree_resource_usage(os.getpid())
        self.assertGreaterEqual(cpu_seconds_after - cpu_seconds_before, 0.25)

    def test_cpu_percent_between_two_readings(self):
        self.assertIsNone(compute_cpu_percent(None, (5.0, 100.0)))  # the first reading
        self.assertEqual(compute_cpu_percent((5.0, 100.0), (6.5, 103.0)), 50)
        self.assertEqual(compute_cpu_percent((5.0, 100.0), (4.0, 103.0)), 0)  # a descendant that ended took its time along


if __name__ == "__main__":
    unittest.main()
