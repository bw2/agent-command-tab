"""Starting the app whenever iTerm2 starts (user request, 2026-10-28): an AppleScript in iTerm2's
AutoLaunch folder, which iTerm2 runs each time it starts, that starts this copy of the app however it
was installed (pip, pipx or Homebrew).

A program started this way runs with iTerm2's macOS permissions, as one started from an iTerm2 tab
does, so the keyboard shortcut keeps the Accessibility permission given to iTerm2. A launch agent that
started the app at login (tried first, 2026-10-28) ran it as the Python interpreter itself, which lacks
that permission, and macOS listed it in Login Items as "python". iTerm2 runs a .py AutoLaunch script
only with its own Python runtime, a separate download, so this one is an AppleScript, which iTerm2 runs
as it is (checked with iTerm2 3.7.6, and in its source: iTermScriptsMenuController.m).

The app adds the script when it starts while the settings' "Start with iTerm2" is on (the default), and
takes it out when that is turned off. A script already there is kept while the program it starts
exists, so the copy that added it keeps starting; one whose program is gone (that copy was uninstalled)
is written again for this copy. A copy started while another runs exits at once (command_line's running
lock), so reopening iTerm2 while the app still runs doesn't start a second copy.
"""

import os
import plistlib
import shlex
import subprocess

from agent_command_tab.installed_copy_paths import find_python_of_this_copy, replace_homebrew_version_folder_with_its_stable_link

AUTOLAUNCH_SCRIPT_FILE_NAME = "AgentCommandTab.scpt"
ITERM2_PREFERENCES_FILE_PATH = os.path.expanduser("~/Library/Preferences/com.googlecode.iterm2.plist")
# iTerm2's scripts folder, unless its settings name another (find_iterm2_autolaunch_folder). iTerm2
# reaches it through ~/.config/iterm2/AppSupport, a link to ~/Library/Application Support/iTerm2.
ITERM2_SCRIPTS_FOLDER = os.path.expanduser("~/Library/Application Support/iTerm2/Scripts")
LOG_FILE_PATH = os.path.expanduser("~/Library/Logs/agent-command-tab.log")
# The AppleScript's line that names the program it starts, read back by read_program_of_autolaunch_script.
PROGRAM_COMMENT_PREFIX = "-- program: "
SECONDS_TO_WAIT_FOR_THE_APPLESCRIPT_COMPILER = 30


def find_iterm2_autolaunch_folder(preferences_file_path=ITERM2_PREFERENCES_FILE_PATH):
    """Returns the AutoLaunch folder iTerm2 runs scripts from: in the scripts folder its settings name
    (General > Magic > "Use custom scripts folder"; one that has a space in its path or isn't a folder
    is ignored, as iTerm2 does), else in ITERM2_SCRIPTS_FOLDER."""
    try:
        with open(preferences_file_path, "rb") as preferences_file:
            preferences = plistlib.load(preferences_file)
    except (OSError, ValueError):  # plistlib.InvalidFileException is a ValueError
        preferences = {}
    custom_scripts_folder = preferences.get("CustomScriptsFolder") if isinstance(preferences, dict) else None
    if preferences.get("UseCustomScriptsFolder") is True and isinstance(custom_scripts_folder, str):
        custom_scripts_folder = os.path.expanduser(custom_scripts_folder)
        if " " not in custom_scripts_folder and os.path.isdir(custom_scripts_folder):
            return os.path.join(custom_scripts_folder, "AutoLaunch")
    return os.path.join(ITERM2_SCRIPTS_FOLDER, "AutoLaunch")


def build_command_that_starts_this_copy_of_the_app(python_path=None):
    """Returns the program and arguments that start this copy of the app, restarted when iTerm2's
    connection drops (`agent-command-tab run`): the Python running the app (python_path, or
    find_python_of_this_copy's), by a path that survives upgrades, running the command line module."""
    return [replace_homebrew_version_folder_with_its_stable_link(python_path) if python_path else find_python_of_this_copy(),
            "-m", "agent_command_tab.command_line", "run"]


def escape_for_applescript_string(text):
    """Returns text to put between an AppleScript string's double quotes."""
    return text.replace("\\", "\\\\").replace('"', '\\"')


def build_autolaunch_script_source(program_arguments, log_file_path=LOG_FILE_PATH):
    """Returns the AppleScript that starts program_arguments in the background, with its output
    appended to log_file_path, and returns at once (do shell script waits for a command unless its
    output is redirected and it ends in &)."""
    shell_command = (" ".join(shlex.quote(argument) for argument in program_arguments)
                     + f" >> {shlex.quote(log_file_path)} 2>&1 &")
    return ("-- Added by AgentCommandTab, which iTerm2 starts with this script whenever iTerm2 starts.\n"
            "-- AgentCommandTab's Settings > Start with iTerm2 removes it.\n"
            f"{PROGRAM_COMMENT_PREFIX}{program_arguments[0]}\n"
            f'do shell script "{escape_for_applescript_string(shell_command)}"\n')


def read_program_of_autolaunch_script(autolaunch_script_path):
    """Returns the program the AutoLaunch script starts (its PROGRAM_COMMENT_PREFIX line, read with
    osadecompile), or None when there is no such script or it names none."""
    if not os.path.exists(autolaunch_script_path):
        return None
    try:
        source = subprocess.run(["/usr/bin/osadecompile", autolaunch_script_path], capture_output=True, text=True,
                                check=True, timeout=SECONDS_TO_WAIT_FOR_THE_APPLESCRIPT_COMPILER).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    return next((line[len(PROGRAM_COMMENT_PREFIX):] for line in source.splitlines() if line.startswith(PROGRAM_COMMENT_PREFIX)),
                None)


def write_autolaunch_script(source, autolaunch_script_path):
    """Compiles the AppleScript source with osacompile into a hidden file next to
    autolaunch_script_path (iTerm2 skips names that start with a dot), which then replaces it.

    Raises:
        OSError: The folder can't be made, or osacompile fails (its message is in the error).
    """
    folder = os.path.dirname(autolaunch_script_path)
    os.makedirs(folder, exist_ok=True)
    temporary_path = os.path.join(folder, f".{os.getpid()}-{os.path.basename(autolaunch_script_path)}")
    try:
        completed_process = subprocess.run(["/usr/bin/osacompile", "-o", temporary_path], input=source, capture_output=True,
                                           text=True, timeout=SECONDS_TO_WAIT_FOR_THE_APPLESCRIPT_COMPILER)
    except subprocess.TimeoutExpired as error:
        raise OSError(f"osacompile didn't finish: {error}") from error
    if completed_process.returncode != 0:
        if os.path.exists(temporary_path):
            os.remove(temporary_path)
        raise OSError(f"osacompile failed: {completed_process.stderr.strip()}")
    os.replace(temporary_path, autolaunch_script_path)


def update_autolaunch_script_to_match_setting(app_starts_with_iterm2, program_arguments, autolaunch_script_path,
                                              log_file_path=LOG_FILE_PATH):
    """Adds, keeps or removes the AutoLaunch script so that it matches the "Start with iTerm2" setting.

    Args:
        app_starts_with_iterm2: The setting.
        program_arguments: build_command_that_starts_this_copy_of_the_app's command, written into a
            new script.
        autolaunch_script_path: Where iTerm2 finds it (find_iterm2_autolaunch_folder, AUTOLAUNCH_SCRIPT_FILE_NAME).
        log_file_path: Where the app started by iTerm2 writes its output.

    Returns:
        "added", "kept", "removed" or "nothing" (off, and there was no script).
    """
    if not app_starts_with_iterm2:
        if not os.path.lexists(autolaunch_script_path):
            return "nothing"
        os.remove(autolaunch_script_path)
        return "removed"
    program = read_program_of_autolaunch_script(autolaunch_script_path)
    if program is not None and os.path.exists(program):
        return "kept"
    write_autolaunch_script(build_autolaunch_script_source(program_arguments, log_file_path), autolaunch_script_path)
    return "added"
