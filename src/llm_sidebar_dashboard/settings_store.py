"""Settings, stored as JSON in ~/Library/Application Support/llm-sidebar-dashboard/settings.json (R34, R35).

Settings are added here as the features that use them are built; PLAN.md section 8 lists the
full set. Command paths are resolved once, at first run, and saved, because an app started at
login gets a minimal PATH (PLAN.md section 8, "Settings").
"""

import json
import os
import pwd
import shutil
import subprocess
import tempfile

SETTINGS_FOLDER = os.path.expanduser("~/Library/Application Support/llm-sidebar-dashboard")
SETTINGS_FILE_PATH = os.path.join(SETTINGS_FOLDER, "settings.json")
SECONDS_TO_WAIT_FOR_THE_LOGIN_SHELL = 5

# "auto" follows the most urgent agent session: light blue while one is asking, red for an
# error, orange when one is stuck, otherwise green.
ICON_COLOR_NAMES = ["auto", "teal", "blue", "purple", "pink", "red", "orange", "green"]
SIDEBAR_EDGES = ["right", "left"]
# auto_hide: slides in when the pointer touches the edge; floating: always visible over windows;
# docked: always visible, and iTerm2 windows on that monitor are kept out of its area.
SIDEBAR_MODES = ["auto_hide", "floating", "docked"]

DEFAULT_SETTINGS = {
    "sidebar_is_on": False,
    "sidebar_mode": "auto_hide",
    "sidebar_edge": "right",
    "sidebar_monitor_name": "",  # "" means the menu-bar monitor
    "sidebar_width": 400,
    "icon_color": "auto",
    "icon_shows_asking_count": True,
    "agent_tools": [
        {"agent_tool_name": "Claude Code", "launch_command": "claude"},
        {"agent_tool_name": "Codex", "launch_command": "codex"},
    ],
    "stuck_minutes": 10,
}


def is_valid_setting_value(name, value):
    """Returns whether a stored value has the right shape for the setting (else the default is used)."""
    default_value = DEFAULT_SETTINGS[name]
    if name == "icon_color":
        return value in ICON_COLOR_NAMES
    if name == "sidebar_edge":
        return value in SIDEBAR_EDGES
    if name == "sidebar_mode":
        return value in SIDEBAR_MODES
    if name == "sidebar_width":
        return isinstance(value, (int, float)) and not isinstance(value, bool) and 200 <= value <= 1200
    if name == "stuck_minutes":
        return isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 240
    if name == "agent_tools":
        return (isinstance(value, list) and value
                and all(isinstance(tool, dict) and isinstance(tool.get("agent_tool_name"), str) and tool["agent_tool_name"]
                        and isinstance(tool.get("launch_command"), str) and tool["launch_command"].strip()
                        for tool in value))
    return isinstance(value, type(default_value))


def merge_settings_with_defaults(stored_settings):
    """Returns the defaults overlaid with every valid stored value (invalid or unknown values are dropped)."""
    settings = json.loads(json.dumps(DEFAULT_SETTINGS))  # a deep copy
    if isinstance(stored_settings, dict):
        for name, value in stored_settings.items():
            if name in DEFAULT_SETTINGS and is_valid_setting_value(name, value):
                settings[name] = value
    return settings


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
    """Returns "" when the launch command's first word can be run, else a short explanation.

    A path must be an executable file; a bare name must be on PATH or known to the login shell.
    """
    words = launch_command.split()
    if not words:
        return "No command"
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


def load_settings_resolving_paths_at_first_run(settings_file_path=SETTINGS_FILE_PATH):
    """Returns the settings; at first run (no file yet) resolves the launch commands' paths and saves them."""
    is_first_run = not os.path.exists(settings_file_path)
    settings = load_settings(settings_file_path)
    if is_first_run:
        settings = resolve_launch_command_paths(settings)
        save_settings(settings, settings_file_path)
    return settings
