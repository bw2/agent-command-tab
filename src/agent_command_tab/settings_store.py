"""Settings, stored as JSON in ~/Library/Application Support/agent-command-tab/settings.json.

Settings are added here as the features that use them are built. Command paths are resolved
once, at first run, and saved, because an app started at login gets a minimal PATH, so the
folders programs are usually installed in are looked in too (USUAL_PROGRAM_FOLDERS).
"""

import json
import os
import pwd
import re
import shutil
import subprocess
import tempfile

from agent_command_tab.list_sorting import LIST_SORT_ORDERS  # the Sort pop-up's choices

SETTINGS_FOLDER = os.path.expanduser("~/Library/Application Support/agent-command-tab")
# The app's folder before it was renamed from llm-sidebar-dashboard to AgentCommandTab (2026-10-19).
SETTINGS_FOLDER_OF_THE_OLD_NAME = os.path.expanduser("~/Library/Application Support/llm-sidebar-dashboard")
SETTINGS_FILE_PATH = os.path.join(SETTINGS_FOLDER, "settings.json")
# Reading the shell's start-up files can take several seconds (7.7 for one .bash_profile, 2026-10-25)
SECONDS_TO_WAIT_FOR_THE_LOGIN_SHELL = 20
# The shells whose syntax the login shell check uses (`-i -l -c` and "$1"); fish, tcsh and csh take
# other syntax, so they aren't asked (code review, 2026-10-28).
LOGIN_SHELLS_THAT_CAN_BE_ASKED = frozenset({"bash", "zsh", "sh", "dash", "ksh"})
# Where programs such as claude and codex are usually installed (Homebrew on Apple silicon and on
# Intel, Claude Code's installer), which an app started at login has no PATH to: it
# gets launchd's /usr/bin:/bin:/usr/sbin:/sbin (code review, 2026-10-28).
USUAL_PROGRAM_FOLDERS = ("/opt/homebrew/bin", "/usr/local/bin", "~/.local/bin")
ENVIRONMENT_SETTING_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")  # "ANTHROPIC_MODEL=x claude"

# How the list is shown (user request, 2026-10-19): popup, the dropdown under the menu bar icon;
# sidebar, at the edge of the screen (then the icon slides it in); window, a regular window (then
# the menu bar icon brings it to the front). In the settings pop-up's order (window first until
# 2026-10-27, user request).
SHOW_AS_CHOICES = ["popup", "sidebar", "window"]
# The global shortcut that shows the list from any app (global_shortcut_key_filter; user request,
# 2026-10-19).
GLOBAL_SHORTCUT_CHOICES = ["shift_command_tab", "command_fn_tab", "off"]
# The app's look (user request, 2026-10-27): auto follows macOS's Light or Dark setting.
APPEARANCE_CHOICES = ["auto", "dark", "light"]
# The actions that ask "Are you sure?" with a "Remember my choice" checkbox: a Yes with it checked
# puts the action in actions_that_run_without_asking (user request, 2026-10-27).
ACTIONS_THAT_ASK_FOR_CONFIRMATION = ["clear", "kill", "close_window"]
# The windows whose frames ([x, y, width, height]) are saved, with their smallest sizes.
# The settings window is never narrower than the width its rows are laid out for.
SMALLEST_WINDOW_SIZES = {"list_window_frame": (250, 200), "settings_window_frame": (540, 240)}
SIDEBAR_EDGES = ["right", "left"]
# auto_hide: slides in when the pointer touches the edge; floating: always visible over windows;
# docked: always visible, and iTerm2 windows on that monitor are kept out of its area.
SIDEBAR_MODES = ["auto_hide", "floating", "docked"]
# A tab row's information columns that the settings can show or hide, in their left-to-right
# order (the tool icon column, right before state, shows by itself when two or more agent tools
# run). Memory and CPU usage count the agent and every process below it. The state and its
# duration come first, right after the name, and CPU before memory, since 2026-10-28 (user requests).
LIST_COLUMNS = ["state", "state_duration", "context_tokens", "created_time", "existence_duration", "cpu_usage", "memory_usage"]

DEFAULT_SETTINGS = {
    "show_as": "popup",
    "global_shortcut": "shift_command_tab",
    "appearance": "auto",
    "app_starts_with_iterm2": True,  # through a script in iTerm2's AutoLaunch folder, added when the app starts (start_with_iterm2; user request, 2026-10-28)
    # Never shown in the settings window (the user's choice): to be asked again, take an action out of this list in the file.
    "actions_that_run_without_asking": [],
    "list_window_frame": None,  # [x, y, width, height] of the "Show as: Window" window, once it has been shown
    "settings_window_frame": None,  # the settings window's, once it has been moved or resized (user request, 2026-10-19)
    "list_window_is_always_on_top": False,
    "sidebar_mode": "auto_hide",
    "sidebar_edge": "right",  # of the menu-bar monitor
    "sidebar_width": 400,
    # The settings window's Harnesses section: a tool not included is left out of the list and of
    # the menus that start new agent sessions (user request, 2026-10-19).
    "agent_tools": [
        {"agent_tool_name": "Claude Code", "launch_command": "claude", "is_included": True},
        {"agent_tool_name": "Codex", "launch_command": "codex", "is_included": True},
    ],
    "list_sort_order": "manual",
    "list_sort_order_is_reversed": False,  # a second click on a sorted column's title (user request, 2026-10-26)
    "shown_columns": ["context_tokens", "state", "state_duration"],
    "idle_tabs_are_hidden": False,  # the Hide Idle / Show Idle button at the top of the list (user request, 2026-10-21)
}


def is_valid_setting_value(name, value):
    """Returns whether a stored value has the right shape for the setting (else the default is used)."""
    default_value = DEFAULT_SETTINGS[name]
    if name == "show_as":
        return value in SHOW_AS_CHOICES
    if name == "global_shortcut":
        return value in GLOBAL_SHORTCUT_CHOICES
    if name == "appearance":
        return value in APPEARANCE_CHOICES
    if name == "actions_that_run_without_asking":
        return isinstance(value, list) and all(action_name in ACTIONS_THAT_ASK_FOR_CONFIRMATION for action_name in value)
    if name in SMALLEST_WINDOW_SIZES:
        smallest_width, smallest_height = SMALLEST_WINDOW_SIZES[name]
        return value is None or (isinstance(value, list) and len(value) == 4
                                 and all(isinstance(number, (int, float)) and not isinstance(number, bool) for number in value)
                                 and value[2] >= smallest_width and value[3] >= smallest_height)
    if name == "sidebar_edge":
        return value in SIDEBAR_EDGES
    if name == "sidebar_mode":
        return value in SIDEBAR_MODES
    if name == "list_sort_order":
        return value in LIST_SORT_ORDERS
    if name == "shown_columns":
        return isinstance(value, list) and all(column_name in LIST_COLUMNS for column_name in value)
    if name == "sidebar_width":
        return isinstance(value, (int, float)) and not isinstance(value, bool) and 200 <= value <= 1200
    if name == "agent_tools":
        return (isinstance(value, list) and value
                and all(isinstance(tool, dict) and isinstance(tool.get("agent_tool_name"), str) and tool["agent_tool_name"]
                        and isinstance(tool.get("launch_command"), str) and tool["launch_command"].strip()
                        and isinstance(tool.get("is_included", True), bool)
                        for tool in value))
    return isinstance(value, type(default_value))


def merge_settings_with_defaults(stored_settings):
    """Returns the defaults overlaid with every valid stored value (invalid or unknown values are
    dropped). A file from before "Show as" with the old sidebar checkbox on shows the sidebar."""
    settings = json.loads(json.dumps(DEFAULT_SETTINGS))  # a deep copy
    if isinstance(stored_settings, dict):
        if "show_as" not in stored_settings and stored_settings.get("sidebar_is_on") is True:
            settings["show_as"] = "sidebar"
        for name, value in stored_settings.items():
            if name in DEFAULT_SETTINGS and is_valid_setting_value(name, value):
                settings[name] = value
    # Files from before the Harnesses section have no "is_included": those tools are included.
    settings["agent_tools"] = [dict({"is_included": True}, **agent_tool) for agent_tool in settings["agent_tools"]]
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


def find_codex_command_path(settings, find_command_path=find_program_on_path_or_in_the_usual_folders):
    """Returns the codex program that "Talk to Each Other" runs `codex queue` with: the first word
    of the Codex tool's launch command (resolved to a full path at first run), looked up on PATH
    and in the usual folders when it is a bare name, or None when it isn't found. A launch
    command that starts with another program (a wrapper) gives the codex found that way."""
    launch_command = next((agent_tool["launch_command"] for agent_tool in settings["agent_tools"]
                           if agent_tool["agent_tool_name"] == "Codex"), "")
    first_word = os.path.expanduser((launch_command.split() or [""])[0])  # "~/bin/codex" too, as Settings accepts
    if os.path.basename(first_word) != "codex":
        first_word = "codex"
    return first_word if os.path.isabs(first_word) else find_command_path(first_word)


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
    """Returns the saved settings merged with the defaults; a missing or unreadable file gives the defaults."""
    try:
        with open(settings_file_path) as settings_file:
            stored_settings = json.load(settings_file)
    except FileNotFoundError:
        stored_settings = {}
    except (OSError, ValueError) as error:
        print(f"settings file {settings_file_path} could not be read ({error}); using defaults")
        stored_settings = {}
    return merge_settings_with_defaults(stored_settings)


def write_json_file_atomically(data, file_path):
    """Writes data as JSON to a temporary file that then replaces file_path, so a crash never leaves half a file."""
    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    file_descriptor, temporary_path = tempfile.mkstemp(dir=os.path.dirname(file_path), suffix=".tmp")
    with os.fdopen(file_descriptor, "w") as temporary_file:
        json.dump(data, temporary_file, indent=1)
    os.replace(temporary_path, file_path)


def save_settings(settings, settings_file_path=SETTINGS_FILE_PATH):
    """Writes the settings atomically."""
    write_json_file_atomically(settings, settings_file_path)


def resolve_launch_command_paths(settings, find_command_path=find_program_on_path_or_in_the_usual_folders):
    """Returns settings whose bare launch commands ("claude") are replaced by full paths found on
    PATH or in the usual folders.

    Only the first word is resolved, and only when it is a bare name that is found, so a
    command the user typed with a path or extra arguments is kept as written.
    """
    resolved_settings = dict(settings)
    resolved_settings["agent_tools"] = []
    for agent_tool in settings["agent_tools"]:
        words = agent_tool["launch_command"].split(" ", 1)
        if "/" not in words[0]:
            full_path = find_command_path(words[0])
            if full_path:
                words[0] = full_path
        resolved_settings["agent_tools"].append(dict(agent_tool, launch_command=" ".join(words)))
    return resolved_settings


def ask_the_login_shell_whether_it_knows_command(command_name):
    """Returns True if the user's login shell knows command_name as an alias, function, builtin or
    program, False if it doesn't, and None if the shell couldn't be asked or didn't answer in time.
    New agent sessions start by typing the launch command into that shell, so an alias such as
    `yc` works there although it is on no PATH. Takes a moment (it reads the shell's start-up
    files), so the settings window calls it off the main thread. A shell not in
    LOGIN_SHELLS_THAT_CAN_BE_ASKED (fish, tcsh) isn't asked."""
    shell = os.environ.get("SHELL") or pwd.getpwuid(os.getuid()).pw_shell or "/bin/zsh"
    if os.path.basename(shell) not in LOGIN_SHELLS_THAT_CAN_BE_ASKED:
        return None
    try:
        completed_process = subprocess.run(
            [shell, "-i", "-l", "-c", 'command -v -- "$1" > /dev/null', shell, command_name],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=SECONDS_TO_WAIT_FOR_THE_LOGIN_SHELL)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return completed_process.returncode == 0


def find_launch_command_problem(launch_command, find_command_path=find_program_on_path_or_in_the_usual_folders,
                                ask_the_login_shell=ask_the_login_shell_whether_it_knows_command):
    """Returns "" when the launch command's program can be run, else a short explanation.

    Leading NAME=value words (environment settings) are skipped. A path must be an executable
    file; a bare name must be on PATH or known to the login shell (such as an alias). A program
    written with a shell variable or quotes ("$HOME/bin/claude") can't be checked without running
    the shell's expansion, and a name the login shell didn't answer about in time may well be an
    alias, so both count as fine.
    """
    words = launch_command.split()
    while words and ENVIRONMENT_SETTING_PATTERN.match(words[0]):
        words = words[1:]
    if not words:
        return "No command"
    if any(character in words[0] for character in "$`'\""):
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


def move_the_settings_folder_of_the_old_name(old_folder=SETTINGS_FOLDER_OF_THE_OLD_NAME, new_folder=SETTINGS_FOLDER):
    """Moves the folder the app kept its settings and docked window frames in under its old name
    to the new one, once: only while the old one exists and the new one doesn't yet."""
    if os.path.isdir(old_folder) and not os.path.exists(new_folder):
        os.rename(old_folder, new_folder)


def load_settings_resolving_paths_at_first_run(settings_file_path=SETTINGS_FILE_PATH):
    """Returns the settings; at first run (no file yet) resolves the launch commands' paths and saves them."""
    is_first_run = not os.path.exists(settings_file_path)
    settings = load_settings(settings_file_path)
    if is_first_run:
        settings = resolve_launch_command_paths(settings)
        save_settings(settings, settings_file_path)
    return settings
