"""Settings, stored as JSON in ~/Library/Application Support/agent-command-tab/settings.json (R34, R35).

Settings are added here as the features that use them are built; PLAN.md section 8 lists the
full set. Command paths are resolved once, at first run, and saved, because an app started at
login gets a minimal PATH (PLAN.md section 8, "Settings").
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
SECONDS_TO_WAIT_FOR_THE_LOGIN_SHELL = 5
ENVIRONMENT_SETTING_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")  # "ANTHROPIC_MODEL=x claude"

# "auto" follows the most urgent agent session: light blue while one is asking, red for an
# error, orange when one is stuck, otherwise green.
ICON_COLOR_NAMES = ["auto", "teal", "blue", "purple", "pink", "red", "orange", "green"]
# How the list is shown (user request, 2026-10-19): window, a regular window (then the menu bar
# icon brings it to the front); sidebar, at the edge of the screen (then the icon slides it in);
# popup, the dropdown under the menu bar icon.
SHOW_AS_CHOICES = ["window", "sidebar", "popup"]
# The windows whose frames ([x, y, width, height]) are saved, with their smallest sizes.
# The settings window is never narrower than the width its rows are laid out for.
SMALLEST_WINDOW_SIZES = {"list_window_frame": (250, 200), "settings_window_frame": (540, 240)}
SIDEBAR_EDGES = ["right", "left"]
# auto_hide: slides in when the pointer touches the edge; floating: always visible over windows;
# docked: always visible, and iTerm2 windows on that monitor are kept out of its area.
SIDEBAR_MODES = ["auto_hide", "floating", "docked"]
# A tab row's information columns that the settings can show or hide, in their left-to-right
# order (the tool icon column, between cpu_usage and state, shows by itself when two or more
# agent tools run). Memory and CPU usage count the agent and every process below it.
LIST_COLUMNS = ["context_tokens", "created_time", "existence_duration", "memory_usage", "cpu_usage", "state", "state_duration"]

DEFAULT_SETTINGS = {
    "show_as": "popup",
    "list_window_frame": None,  # [x, y, width, height] of the "Show as: Window" window, once it has been shown
    "settings_window_frame": None,  # the settings window's, once it has been moved or resized (user request, 2026-10-19)
    "list_window_is_always_on_top": False,
    "sidebar_mode": "auto_hide",
    "sidebar_edge": "right",  # of the menu-bar monitor
    "sidebar_width": 400,
    "icon_color": "auto",
    "icon_shows_asking_count": True,
    # The settings window's Harnesses section: a tool not included is left out of the list and of
    # the menus that start new agent sessions (user request, 2026-10-19).
    "agent_tools": [
        {"agent_tool_name": "Claude Code", "launch_command": "claude", "is_included": True},
        {"agent_tool_name": "Codex", "launch_command": "codex", "is_included": True},
    ],
    "stuck_minutes": 10,
    "list_sort_order": "manual",
    "shown_columns": ["context_tokens", "state", "state_duration"],
}


def is_valid_setting_value(name, value):
    """Returns whether a stored value has the right shape for the setting (else the default is used)."""
    default_value = DEFAULT_SETTINGS[name]
    if name == "icon_color":
        return value in ICON_COLOR_NAMES
    if name == "show_as":
        return value in SHOW_AS_CHOICES
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
    if name == "stuck_minutes":
        return isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 240
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


def build_core_settings(settings):
    """Returns the part of the settings that the core uses: the stuck time limit, the agent
    tools left out of the list, and whether memory and CPU use are read (only while one of
    their columns is shown)."""
    return {
        "stuck_threshold_seconds": settings["stuck_minutes"] * 60,
        "left_out_agent_tool_names": list_left_out_agent_tool_names(settings),
        "resource_usage_is_read": bool({"memory_usage", "cpu_usage"} & set(settings["shown_columns"])),
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


def resolve_launch_command_paths(settings, find_command_path=shutil.which):
    """Returns settings whose bare launch commands ("claude") are replaced by full paths found on PATH.

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


def command_is_known_to_the_login_shell(command_name):
    """Returns whether the user's login shell knows command_name as an alias, function, builtin
    or program. New agent sessions start by typing the launch command into that shell, so an
    alias such as `yc` works there although it is on no PATH. Takes a moment (it reads the
    shell's start-up files), so the settings window calls it off the main thread."""
    shell = os.environ.get("SHELL") or pwd.getpwuid(os.getuid()).pw_shell or "/bin/zsh"
    try:
        completed_process = subprocess.run(
            [shell, "-i", "-l", "-c", 'command -v -- "$1" > /dev/null', shell, command_name],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=SECONDS_TO_WAIT_FOR_THE_LOGIN_SHELL)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return completed_process.returncode == 0


def find_launch_command_problem(launch_command, find_command_path=shutil.which,
                                ask_the_login_shell=command_is_known_to_the_login_shell):
    """Returns "" when the launch command's program can be run, else a short explanation.

    Leading NAME=value words (environment settings) are skipped. A path must be an executable
    file; a bare name must be on PATH or known to the login shell. A program written with a
    shell variable or quotes ("$HOME/bin/claude") can't be checked without running the shell's
    expansion, so it counts as fine.
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
    if find_command_path(first_word) or ask_the_login_shell(first_word):
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
