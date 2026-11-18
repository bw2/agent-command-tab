"""Settings, stored as JSON in ~/Library/Application Support/agent-command-tab/settings.json.

Launch commands are kept as
written ("claude", not the full path found at first run, which a Node version switch can make
stale): they are typed into the user's own shell, which finds the program. A program the app
runs itself is looked for on PATH and, since an app started without a shell's PATH may still
lack the user's folders (login_shell_environment), in the folders programs are usually
installed in (USUAL_PROGRAM_FOLDERS).
"""

import copy
import json
import os
import pwd
import re
import shlex
import shutil
import subprocess
import sys
import tempfile

from agent_command_tab.list_sorting import LIST_SORT_ORDERS  # the Sort pop-up's choices

SETTINGS_FOLDER = os.path.expanduser("~/Library/Application Support/agent-command-tab")
# The app's folder before it was renamed from llm-sidebar-dashboard to AgentCommandTab.
SETTINGS_FOLDER_OF_THE_OLD_NAME = os.path.expanduser("~/Library/Application Support/llm-sidebar-dashboard")
SETTINGS_FILE_PATH = os.path.join(SETTINGS_FOLDER, "settings.json")
# Reading the shell's start-up files can take several seconds (7.7 for one .bash_profile)
SECONDS_TO_WAIT_FOR_THE_LOGIN_SHELL = 20
# The shells whose syntax the login shell check uses (`-i -l -c` and "$1"); fish, tcsh and csh take
# other syntax, so they aren't asked.
LOGIN_SHELLS_THAT_CAN_BE_ASKED = frozenset({"bash", "zsh", "sh", "dash", "ksh"})
# Where programs such as claude and codex are usually installed (Homebrew on Apple silicon and on
# Intel, Claude Code's installer), which an app started at login has no PATH to: it
# gets launchd's /usr/bin:/bin:/usr/sbin:/sbin.
USUAL_PROGRAM_FOLDERS = ("/opt/homebrew/bin", "/usr/local/bin", "~/.local/bin")
# One "NAME=value" word in front of a launch command's program, the value maybe quoted, and the
# space after it ("ANTHROPIC_MODEL=x CLAUDE_CONFIG_DIR=~/.claude_work claude", "CODEX_HOME='/Users/me/Codex Work' codex").
LEADING_ENVIRONMENT_SETTING_PATTERN = re.compile(r"""([A-Za-z_][A-Za-z0-9_]*)=((?:'[^']*'|"(?:[^"\\]|\\.)*"|\\.|[^\s'"\\])*)(?:\s+|$)""")

# How the list is shown: popup, the dropdown under the menu bar icon;
# sidebar, at the edge of the screen (then the icon slides it in); window, a regular window (then
# the menu bar icon brings it to the front). In the settings pop-up's order.
SHOW_AS_CHOICES = ["popup", "sidebar", "window"]
# The global shortcut that shows the list from any app (global_shortcut_key_filter).
GLOBAL_SHORTCUT_CHOICES = ["shift_command_tab", "command_fn_tab", "off"]
# The app's look: auto follows macOS's Light or Dark setting.
APPEARANCE_CHOICES = ["auto", "dark", "light"]
# The actions that ask "Are you sure?" with a "Remember my choice" checkbox: a Yes with it checked
# puts the action in actions_that_run_without_asking. "kill" is Kill
# session; "kill_process", a sub row's Kill, "kill_with_the_delete_key", the Delete key's kill of
# the selected rows' sessions, and "kill_process_with_the_delete_key", its kill of selected process
# rows, are each remembered apart.
ACTIONS_THAT_ASK_FOR_CONFIRMATION = ["clear", "kill", "close_window", "kill_process", "kill_with_the_delete_key",
                                     "kill_process_with_the_delete_key"]
# The windows whose frames ([x, y, width, height]) are saved, with their smallest sizes.
# The settings window is never narrower than the width its rows are laid out for.
SMALLEST_WINDOW_SIZES = {"list_window_frame": (250, 200), "settings_window_frame": (540, 240),
                         "saved_sessions_window_frame": (480, 220)}
SIDEBAR_EDGES = ["right", "left"]
# auto_hide: slides in when the pointer touches the edge; floating: always visible over windows;
# docked: always visible, and iTerm2 windows on that monitor are kept out of its area.
SIDEBAR_MODES = ["auto_hide", "floating", "docked"]
# The narrowest and widest the sidebar can be, typed in the settings or dragged at its edge (sidebar_panel).
SMALLEST_SIDEBAR_WIDTH = 200
LARGEST_SIDEBAR_WIDTH = 1200
# A tab row's information columns that the settings can show or hide, in their left-to-right
# order (the tool icon column, right before state, shows by itself when two or more agent tools
# run). Memory and CPU usage count the agent and every process below it. The state comes first,
# right after the name, and CPU before memory.
LIST_COLUMNS = ["state", "context_tokens", "created_time", "existence_duration", "time_since_last_message", "cpu_usage",
                "memory_usage"]
# Columns the app no longer has, which a saved shown_columns setting may still name: they are left
# out of it, so the other columns it names stay shown. The time since the last status change
# ("Status Changed") was removed.
REMOVED_LIST_COLUMNS = ["state_duration"]
# The settings whose value is one of a fixed list of choices.
CHOICES_BY_SETTING_NAME = {"show_as": SHOW_AS_CHOICES, "global_shortcut": GLOBAL_SHORTCUT_CHOICES, "appearance": APPEARANCE_CHOICES,
                           "sidebar_edge": SIDEBAR_EDGES, "sidebar_mode": SIDEBAR_MODES, "list_sort_order": LIST_SORT_ORDERS}

DEFAULT_SETTINGS = {
    "show_as": "popup",
    "global_shortcut": "shift_command_tab",
    "appearance": "auto",
    "app_starts_with_iterm2": True,  # through a script in iTerm2's AutoLaunch folder, added when the app starts (start_with_iterm2)
    # Never shown in the settings window: to be asked again, take an action out of this list in the file.
    "actions_that_run_without_asking": [],
    "list_window_frame": None,  # [x, y, width, height] of the "Show as: Window" window, once it has been shown
    "settings_window_frame": None,  # the settings window's, once it has been moved or resized
    "saved_sessions_window_frame": None,  # the Saved sessions window's, as the settings window's
    "list_window_is_always_on_top": False,
    "sidebar_mode": "auto_hide",
    "sidebar_edge": "right",  # of the menu-bar monitor
    "sidebar_width": 400,
    # The settings window's Harnesses section: a tool not included is left out of the list and of
    # the menus that start new agent sessions.
    "agent_tools": [
        {"agent_tool_name": "Claude Code", "launch_command": "claude", "is_included": True},
        {"agent_tool_name": "Codex", "launch_command": "codex", "is_included": True},
        {"agent_tool_name": "agy", "launch_command": "agy", "is_included": True},
    ],
    "list_sort_order": "manual",
    "list_sort_order_is_reversed": False,  # a second click on a sorted column's title
    "shown_columns": ["context_tokens", "state", "time_since_last_message"],
    "idle_tabs_are_hidden": False,  # the Hide Idle / Show Idle button at the top of the list
    "search_is_case_sensitive": False,  # the "aA" checkbox beside the search box at the top of the list
}


def is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def frame_is_valid(frame):
    """Whether a stored value is a frame: (x, y, width, height), four numbers."""
    return isinstance(frame, (list, tuple)) and len(frame) == 4 and all(map(is_number, frame))


def is_valid_setting_value(name, value):
    """Returns whether a stored value has the right shape for the setting (else the default is used)."""
    default_value = DEFAULT_SETTINGS[name]
    if name in CHOICES_BY_SETTING_NAME:
        return value in CHOICES_BY_SETTING_NAME[name]
    if name == "actions_that_run_without_asking":
        return isinstance(value, list) and all(action_name in ACTIONS_THAT_ASK_FOR_CONFIRMATION for action_name in value)
    if name in SMALLEST_WINDOW_SIZES:
        smallest_width, smallest_height = SMALLEST_WINDOW_SIZES[name]
        return value is None or (frame_is_valid(value) and value[2] >= smallest_width and value[3] >= smallest_height)
    if name == "shown_columns":
        return isinstance(value, list) and all(column_name in LIST_COLUMNS for column_name in value)
    if name == "sidebar_width":
        return is_number(value) and SMALLEST_SIDEBAR_WIDTH <= value <= LARGEST_SIDEBAR_WIDTH
    if name == "agent_tools":
        return (isinstance(value, list) and value
                and all(isinstance(tool, dict) and isinstance(tool.get("agent_tool_name"), str) and tool["agent_tool_name"]
                        and isinstance(tool.get("launch_command"), str) and tool["launch_command"].strip()
                        and isinstance(tool.get("is_included", True), bool)
                        for tool in value))
    return isinstance(value, type(default_value))


def merge_settings_with_defaults(stored_settings):
    """Returns the defaults overlaid with every valid stored value (invalid or unknown values are
    dropped). A file from before "Show as" with the old sidebar checkbox on shows the sidebar, and
    the columns a file names that the app no longer has (REMOVED_LIST_COLUMNS) are left out."""
    settings = copy.deepcopy(DEFAULT_SETTINGS)
    if isinstance(stored_settings, dict):
        if "show_as" not in stored_settings and stored_settings.get("sidebar_is_on") is True:
            settings["show_as"] = "sidebar"
        if isinstance(stored_settings.get("shown_columns"), list):
            stored_settings = dict(stored_settings, shown_columns=[
                column_name for column_name in stored_settings["shown_columns"] if column_name not in REMOVED_LIST_COLUMNS])
        for name, value in stored_settings.items():
            if name in DEFAULT_SETTINGS and is_valid_setting_value(name, value):
                settings[name] = value
    # Files from before the Harnesses section have no "is_included": those tools are included.
    settings["agent_tools"] = [dict({"is_included": True}, **agent_tool) for agent_tool in settings["agent_tools"]]
    # A tool added to the app since the file was saved (agy) joins its tools, at the end.
    stored_agent_tool_names = {agent_tool["agent_tool_name"] for agent_tool in settings["agent_tools"]}
    settings["agent_tools"] += [dict(agent_tool) for agent_tool in DEFAULT_SETTINGS["agent_tools"]
                                if agent_tool["agent_tool_name"] not in stored_agent_tool_names]
    return settings


def list_included_agent_tool_names(settings):
    """Returns the agent tools that the Harnesses section includes, in their settings order."""
    return [agent_tool["agent_tool_name"] for agent_tool in settings["agent_tools"] if agent_tool["is_included"]]


def list_left_out_agent_tool_names(settings):
    """Returns the agent tools that the Harnesses section leaves out of the list and the menus."""
    return [agent_tool["agent_tool_name"] for agent_tool in settings["agent_tools"] if not agent_tool["is_included"]]


def replace_agent_tool_field(agent_tools, tool_index, field_name, value):
    """Returns a copy of the agent_tools setting with one tool's field (such as "launch_command"
    or "is_included") set to value; the settings it came from are not changed."""
    return [dict(agent_tool, **{field_name: value}) if index == tool_index else dict(agent_tool)
            for index, agent_tool in enumerate(agent_tools)]


def find_program_on_path_or_in_the_usual_folders(program_name):
    """Returns the full path of a program found on PATH, else in USUAL_PROGRAM_FOLDERS, or None."""
    return shutil.which(program_name) or shutil.which(
        program_name, path=os.pathsep.join(os.path.expanduser(folder) for folder in USUAL_PROGRAM_FOLDERS))


def build_environment_with_the_programs_folder_first_on_path(program_path, environment):
    """Returns a copy of environment for running program_path, with the program's folder first on
    PATH, so that a script finds the interpreter installed next to it: npm puts `node` in the
    folder of the `codex` it installs, a script that starts with `#!/usr/bin/env node`, which
    fails with "env: node: No such file or directory" on a PATH without that folder."""
    return dict(environment, PATH=os.pathsep.join(folder for folder in (os.path.dirname(program_path), environment.get("PATH"))
                                                  if folder))


def split_off_the_environment_settings_of_launch_command(launch_command):
    """Returns (the NAME=value words in front of the launch command's program, each as its match
    of LEADING_ENVIRONMENT_SETTING_PATTERN: group 1 the name, group 2 the value as written, group 0
    the whole word with the space after it; the launch command from its program on)."""
    environment_settings = []
    rest = launch_command.lstrip()
    while (match := LEADING_ENVIRONMENT_SETTING_PATTERN.match(rest)):
        environment_settings.append(match)
        rest = rest[match.end():]
    return environment_settings, rest


def read_environment_settings_of_launch_command(launch_command):
    """Returns {name: value} of the NAME=value words in front of the launch command's program,
    each value as the shell reads it, its quotes taken off and a "~" left as written
    ("CODEX_HOME='/Users/me/Codex Work' codex" gives {"CODEX_HOME": "/Users/me/Codex Work"})."""
    return {match.group(1): "".join(shlex.split(match.group(2)))
            for match in split_off_the_environment_settings_of_launch_command(launch_command)[0]}


def split_command_line_into_words(command_line):
    """Returns the words of a command line (a launch command, or the command line of a tab's
    program, which may be None) as a shell reads them, without their quotes and backslashes;
    possibly none. With a quote left open, the words are those between its spaces, as written."""
    if not command_line:
        return []
    try:
        return shlex.split(command_line)
    except ValueError:
        return command_line.split()


def split_launch_command_into_words_after_environment_settings(launch_command):
    """Returns the launch command's words from its program on (split_command_line_into_words):
    without the leading NAME=value words (environment settings, as in "CODEX_HOME=~/.codex-work codex")."""
    return split_command_line_into_words(split_off_the_environment_settings_of_launch_command(launch_command)[1])


def read_program_of_launch_command(launch_command):
    """Returns the launch command's program, after any environment settings, as the shell the
    command is typed into reads it: without its quotes and backslashes, and with a leading ~ and
    its $VARIABLEs filled in ('"/opt/My Tools/codex" -s read-only' gives "/opt/My Tools/codex",
    "~/bin/codex" the one in the home folder); "" for a launch command without a program. With a
    quote left open, the program is the text up to the first space, as written."""
    words = split_launch_command_into_words_after_environment_settings(launch_command)
    return os.path.expandvars(os.path.expanduser(words[0])) if words else ""


def find_launch_command(settings, agent_tool_name):
    """Returns the agent tool's launch command from the settings, or "" for a tool they don't list."""
    return next((agent_tool["launch_command"] for agent_tool in settings["agent_tools"]
                 if agent_tool["agent_tool_name"] == agent_tool_name), "")


def find_codex_command_path(settings, find_command_path=find_program_on_path_or_in_the_usual_folders):
    """Returns the codex program that "Talk to Each Other" runs `codex queue` with: the program
    of the Codex tool's launch command (read_program_of_launch_command), looked up on PATH and in
    the usual folders when it is a bare name, or None when it isn't found. A launch command that
    starts with another program (a wrapper) gives the codex found that way."""
    program = read_program_of_launch_command(find_launch_command(settings, "Codex"))
    if os.path.basename(program) != "codex":
        program = "codex"
    return program if os.path.isabs(program) else find_command_path(program)


def build_core_settings(settings, find_command_path=find_program_on_path_or_in_the_usual_folders):
    """Returns the part of the settings that the core uses: the agent tools left out of the
    list, whether memory and CPU use are read (only while one of their columns is shown or the
    list is sorted by one of them), and the codex program for `codex queue`
    (find_codex_command_path)."""
    return {
        "left_out_agent_tool_names": list_left_out_agent_tool_names(settings),
        "resource_usage_is_read": bool({"memory_usage", "cpu_usage"} & {*settings["shown_columns"], settings["list_sort_order"]}),
        "codex_command_path": find_codex_command_path(settings, find_command_path),
    }


def load_settings(settings_file_path=SETTINGS_FILE_PATH):
    """Returns the saved settings merged with the defaults; a missing or unreadable file gives the
    defaults. An unreadable one is said on stderr: dump-snapshot's stdout holds only its JSON."""
    try:
        with open(settings_file_path) as settings_file:
            stored_settings = json.load(settings_file)
    except FileNotFoundError:
        stored_settings = {}
    except (OSError, ValueError) as error:
        print(f"settings file {settings_file_path} could not be read ({error}); using defaults", file=sys.stderr)
        stored_settings = {}
    return merge_settings_with_defaults(stored_settings)


def read_json_file(file_path, expected_type):
    """Returns the JSON value in the file, or None when the file is missing or unreadable or its
    value isn't of expected_type (list or dict)."""
    try:
        with open(file_path) as file:
            value = json.load(file)
    except (OSError, ValueError):
        return None
    return value if isinstance(value, expected_type) else None


def write_json_file_atomically(data, file_path, indent=1, ensure_ascii=True, file_mode=None):
    """Writes data as JSON, ending in a newline, to a temporary file that then replaces file_path,
    so a crash never leaves half a file; a write that fails (a full disk) deletes the temporary
    file and raises.

    Args:
        data: The value to write.
        file_path: The file to replace.
        indent, ensure_ascii: As json.dump takes them.
        file_mode: The new file's permissions, or None for the owner's alone (0o600).
    """
    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    file_descriptor, temporary_path = tempfile.mkstemp(dir=os.path.dirname(file_path), suffix=".tmp")
    try:
        with os.fdopen(file_descriptor, "w") as temporary_file:
            json.dump(data, temporary_file, indent=indent, ensure_ascii=ensure_ascii)
            temporary_file.write("\n")
        if file_mode is not None:
            os.chmod(temporary_path, file_mode)
        os.replace(temporary_path, file_path)
    except BaseException:
        try:
            os.remove(temporary_path)
        except OSError:
            pass
        raise


def save_settings(settings, settings_file_path=SETTINGS_FILE_PATH):
    """Writes the settings atomically."""
    write_json_file_atomically(settings, settings_file_path)


def run_script_in_the_login_shell(script, arguments=(), shell=None, keeps_the_output=False):
    """Runs script in the user's login shell (or in shell) as an interactive login shell, after
    it has read its start-up files, with arguments as $1, $2, ...

    Returns:
        The finished process (its stdout as bytes when keeps_the_output, else None), or None when
        the shell isn't one of LOGIN_SHELLS_THAT_CAN_BE_ASKED, can't be started, or takes longer
        than SECONDS_TO_WAIT_FOR_THE_LOGIN_SHELL.
    """
    shell = shell or os.environ.get("SHELL") or pwd.getpwuid(os.getuid()).pw_shell or "/bin/zsh"
    if os.path.basename(shell) not in LOGIN_SHELLS_THAT_CAN_BE_ASKED:
        return None
    try:
        # In a session of its own, without a terminal: an interactive shell takes the terminal of
        # the tab the app was started from, which a shell killed at the timeout never gives back
        # (Ctrl+C there then no longer reaches the app).
        return subprocess.run([shell, "-i", "-l", "-c", script, shell, *arguments], stdin=subprocess.DEVNULL,
                              stdout=subprocess.PIPE if keeps_the_output else subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                              timeout=SECONDS_TO_WAIT_FOR_THE_LOGIN_SHELL, start_new_session=True)
    except (OSError, subprocess.TimeoutExpired):
        return None


def ask_the_login_shell_whether_it_knows_command(command_name):
    """Returns True if the user's login shell knows command_name as an alias, function, builtin or
    program, False if it doesn't, and None if the shell couldn't be asked or didn't answer in time.
    New agent sessions start by typing the launch command into that shell, so an alias such as
    `yc` works there although it is on no PATH. Takes a moment (it reads the shell's start-up
    files), so the settings window calls it off the main thread. A shell not in
    LOGIN_SHELLS_THAT_CAN_BE_ASKED (fish, tcsh) isn't asked."""
    completed_process = run_script_in_the_login_shell('command -v -- "$1" > /dev/null', [command_name])
    return None if completed_process is None else completed_process.returncode == 0


def find_launch_command_problem(launch_command, find_command_path=find_program_on_path_or_in_the_usual_folders,
                                ask_the_login_shell=ask_the_login_shell_whether_it_knows_command):
    """Returns "" when the launch command's program can be run, else a short explanation.

    Leading NAME=value words (environment settings) are skipped, and the program is read as the
    shell reads it, without its quotes and backslashes ("/opt/My\\ Tools/claude", "\\claude"). A
    path must be an executable file; a bare name must be on PATH or known to the login shell
    (such as an alias). A program that still holds shell syntax then (a variable or a command
    substitution, as in "$HOME/bin/claude"; or a quote or backslash, as the first word of a
    launch command with a quote left open may) can't be checked without running the shell's
    expansion, and a name the login shell didn't answer about in time may well be an alias, so
    both count as fine.
    """
    words = split_launch_command_into_words_after_environment_settings(launch_command)
    if not words:
        return "No command"
    if any(character in words[0] for character in "$`'\"\\"):
        return ""
    first_word = os.path.expanduser(words[0])
    if "/" in first_word:
        if not os.path.isfile(first_word):
            return f"{words[0]} doesn't exist"
        if not os.access(first_word, os.X_OK):
            return f"{words[0]} can't be run (it isn't executable)"
        return ""
    if find_command_path(first_word) or ask_the_login_shell(first_word) is not False:
        return ""
    return f"{first_word}: command not found (not on PATH, and not an alias or function in your shell)"


def build_launch_command_by_agent_tool_name(agent_tools):
    """Returns {agent tool name: its launch command} for the agent_tools setting."""
    return {agent_tool["agent_tool_name"]: agent_tool["launch_command"] for agent_tool in agent_tools}


def find_installed_agent_tool_names(agent_tools, find_problem):
    """Returns the names of the agent tools (the agent_tools setting) that are installed, as far
    as the app can tell: those whose launch command can be run (find_problem, given a launch
    command, returns no problem). The app gives it the problems find_launch_command_problem found
    off the main thread, since asking the login shell about a command takes a moment, so the
    settings window shows the same answers without a check of its own."""
    return frozenset(agent_tool["agent_tool_name"] for agent_tool in agent_tools if not find_problem(agent_tool["launch_command"]))


def move_the_settings_folder_of_the_old_name(old_folder=SETTINGS_FOLDER_OF_THE_OLD_NAME, new_folder=SETTINGS_FOLDER):
    """Moves the folder the app kept its settings and docked window frames in under its old name
    to the new one, once: only while the old one exists and the new one doesn't yet."""
    if os.path.isdir(old_folder) and not os.path.exists(new_folder):
        os.rename(old_folder, new_folder)
