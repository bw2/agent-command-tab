"""The app's own hooks in Claude Code's settings: installing, removing and checking them.

Each hook runs hook_event_forwarder.py, which sends the event to the app. The events tell the
app when an agent's session ended normally (so a crash leaves an "exited" row), and
Claude Code's background shells and agents (the lights after a tab's name). The entries
are the app's by their command, which names hook_event_forwarder.py, so installing again
replaces them and removing takes out only them. Each file the installer changes is copied first
to <file>.agent-command-tab-backup, the first time only, so that keeps the file as it was before
the app changed it ({} for a file the app created).

The hooks go in settings.json in each Claude Code config folder in use
(claude_code.list_claude_config_folders, so a Claude Code started with
CLAUDE_CONFIG_DIR=~/.claude_work gets them too). A session reads its hooks when it starts, so
only sessions started afterwards report.

Earlier versions also added hooks to Codex's hooks.json. Codex 0.165 and later run hooks in their
shared background server (`codex app-server`), whose process belongs to no tab, so those events
reached no row (checked with Codex 0.169); the app takes them out
(take_out_the_hooks_only_earlier_versions_added).
"""

import json
import os
import shlex
import shutil

from agent_command_tab.agent_adapters.claude_code import list_claude_config_folder_paths
from agent_command_tab.agent_adapters.codex import find_codex_home_folder
from agent_command_tab.installed_copy_paths import find_python_of_this_copy, replace_homebrew_version_folder_with_its_stable_link
from agent_command_tab.settings_store import (SETTINGS_FILE_PATH, find_launch_command, load_settings,
                                              read_environment_settings_of_launch_command, write_json_file_atomically)

FORWARDER_FILE_NAME = "hook_event_forwarder.py"
FORWARDER_FILE_PATH = replace_homebrew_version_folder_with_its_stable_link(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), FORWARDER_FILE_NAME))
SECONDS_A_HOOK_MAY_RUN = 10
BACKUP_FILE_SUFFIX = ".agent-command-tab-backup"
# The events each agent tool's hooks report, each with the tools a PreToolUse or PostToolUse
# entry is limited to (Claude Code's background shells and agents start from Bash and Agent tool
# calls; Task is the Agent tool's older name), so other tool calls start no forwarder. Claude
# Code's prompts and turn ends tell when a turn ended while iTerm2's status still says working, as
# its Claude Code integration keeps it while a background task runs, and the main agent's tool
# calls after a Stop that another Stop hook blocked tell that the turn goes on, from the start of
# its first command (PreToolUse; hook_event_tracking.find_when_the_hooks_ended_the_turn).
HOOK_EVENTS_BY_AGENT_TOOL_NAME = {
    "Claude Code": [("SessionStart", None), ("UserPromptSubmit", None), ("Stop", None), ("SubagentStop", None),
                    ("SessionEnd", None), ("PreToolUse", "Bash|Agent|Task"), ("PostToolUse", "Bash|Agent|Task")],
}
# The agent tools whose hooks only earlier versions added, which the app takes out
# (take_out_the_hooks_only_earlier_versions_added) and never installs.
AGENT_TOOL_NAMES_WHOSE_HOOKS_ONLY_EARLIER_VERSIONS_ADDED = ("Codex",)
HOOKS_ARE_INSTALLED = "installed"
HOOKS_ARE_MISSING = "missing"
# The Python or the forwarder this copy's hooks run is gone, though the app still runs on it: a
# Homebrew upgrade removed the Python a pipx environment was made with, say (this_copys_hooks_can_run).
HOOKS_CANT_RUN = "can't run"
# Set by the core, not by check_hooks: installed, but new Claude Code sessions send no events
# (DashboardCore.mark_hooks_that_send_no_events).
HOOKS_SEND_NO_EVENTS = "send no events"


def build_forwarder_command(agent_tool_name, python_path=None, forwarder_path=FORWARDER_FILE_PATH):
    """Returns the hook command: this Python (python_path, or the one running), isolated (-I) and
    without site packages (-S), running the forwarder. The running Python is named by a path that
    survives upgrades (installed_copy_paths.find_python_of_this_copy: a venv's bin/python), and a
    Python in a Homebrew version folder by its stable opt/ path, as FORWARDER_FILE_PATH is, so the
    hooks keep working after a `brew upgrade` that keeps the Python version. The forwarder of a
    copy installed with pip, pipx or Homebrew is in the venv's lib/python3.X/site-packages, so a
    venv made again with another Python version (a formula that moves to a newer Python, say) needs
    Install Hooks again; check_hooks then reports the hooks missing, since their command differs."""
    python_path = replace_homebrew_version_folder_with_its_stable_link(python_path) if python_path else find_python_of_this_copy()
    parts = (python_path, "-I", "-S", forwarder_path, agent_tool_name)
    return " ".join(shlex.quote(part) for part in parts)


def hook_is_the_apps(hook):
    """Whether a hook ({"type": "command", "command": ...}) runs the forwarder."""
    return isinstance(hook, dict) and FORWARDER_FILE_NAME in str(hook.get("command", ""))


def remove_the_apps_hooks_from_hook_group(hook_group):
    """Returns a hooks entry ({"matcher": ..., "hooks": [hook, ...]}) without the app's hooks,
    keeping the other tools' hooks that were added to it; None when only the app's were in it."""
    if not (isinstance(hook_group, dict) and isinstance(hook_group.get("hooks"), list)
            and any(map(hook_is_the_apps, hook_group["hooks"]))):
        return hook_group
    kept_hooks = [hook for hook in hook_group["hooks"] if not hook_is_the_apps(hook)]
    return dict(hook_group, hooks=kept_hooks) if kept_hooks else None


def remove_the_apps_hooks(settings):
    """Returns the settings without the app's hooks (remove_the_apps_hooks_from_hook_group); an
    event left without entries is left out, and so is "hooks" when only the app's entries were in it."""
    hooks = settings.get("hooks")
    if not isinstance(hooks, dict):
        return dict(settings)
    kept_hooks = {}
    for event_name, hook_groups in hooks.items():
        if not isinstance(hook_groups, list):
            kept_hooks[event_name] = hook_groups
            continue
        kept_hook_groups = [kept_hook_group for kept_hook_group in map(remove_the_apps_hooks_from_hook_group, hook_groups)
                            if kept_hook_group is not None]
        if kept_hook_groups or not hook_groups:
            kept_hooks[event_name] = kept_hook_groups
    if not kept_hooks and hooks:
        return {name: value for name, value in settings.items() if name != "hooks"}
    return dict(settings, hooks=kept_hooks)


def build_the_apps_hook_group(command, matcher):
    return dict({"matcher": matcher} if matcher else {},
                hooks=[{"type": "command", "command": command, "async": True, "timeout": SECONDS_A_HOOK_MAY_RUN}])


def add_the_apps_hooks(settings, command, events):
    """Returns the settings with one entry of the app's per event, after the user's own, in place
    of any the app added before (with another command or other events).

    Args:
        settings: The settings file's contents.
        command: build_forwarder_command's command.
        events: [(event name, matcher or None)], from HOOK_EVENTS_BY_AGENT_TOOL_NAME.
    """
    settings = remove_the_apps_hooks(settings)
    hooks = dict(settings.get("hooks") or {})
    for event_name, matcher in events:
        hooks[event_name] = list(hooks.get(event_name) or []) + [build_the_apps_hook_group(command, matcher)]
    return dict(settings, hooks=hooks)


def normalize_forwarder_command(command):
    """Returns a hook command's words with its program as the file it leads to, so the venv's
    python, python3 and python3.11 (links to one program) give the same command; None for a
    command that can't be split. The forwarder runs without site packages (-S), so which of
    those links started it makes no difference."""
    try:
        words = shlex.split(command)
    except ValueError:
        return None
    return [os.path.realpath(words[0])] + words[1:] if words else None


def hook_group_is_the_same_as(hook_group, expected_hook_group):
    """Whether a hooks entry is the one add_the_apps_hooks adds (expected_hook_group), its command
    perhaps naming the same Python by another link (normalize_forwarder_command): the app's entry
    point runs as python3.11, `python -m agent_command_tab.command_line` as python."""
    if not (isinstance(hook_group, dict) and isinstance(hook_group.get("hooks"), list) and len(hook_group["hooks"]) == 1
            and isinstance(hook_group["hooks"][0], dict)):
        return False
    hook, expected_hook = hook_group["hooks"][0], expected_hook_group["hooks"][0]
    return (dict(hook_group, hooks=None) == dict(expected_hook_group, hooks=None)
            and dict(hook, command=None) == dict(expected_hook, command=None)
            and normalize_forwarder_command(str(hook.get("command", ""))) == normalize_forwarder_command(expected_hook["command"]))


def settings_have_the_apps_hooks(settings, command, events):
    """Whether every event has the entry add_the_apps_hooks adds (hook_group_is_the_same_as)."""
    hooks = settings.get("hooks") if isinstance(settings.get("hooks"), dict) else {}
    return all(isinstance(hooks.get(event_name), list)
               and any(hook_group_is_the_same_as(hook_group, build_the_apps_hook_group(command, matcher))
                       for hook_group in hooks[event_name])
               for event_name, matcher in events)


def list_settings_file_paths(agent_tool_name):
    """Returns the files that hold the agent tool's hooks, in folders that exist (a tool that
    isn't installed has none): Claude Code's settings.json files, or, for Codex, whose hooks only
    earlier versions added, the hooks.json files of list_codex_home_folders."""
    if agent_tool_name == "Claude Code":
        folders = list_claude_config_folder_paths()
        return [os.path.join(folder, "settings.json") for folder in folders if os.path.isdir(folder)]
    return [os.path.join(folder, "hooks.json") for folder in list_codex_home_folders() if os.path.isdir(folder)]


def list_codex_home_folders(settings_file_path=SETTINGS_FILE_PATH):
    """Returns the Codex home folders that earlier versions put the app's hooks in: its own
    (find_codex_home_folder), and the one the Codex launch command in the settings sets
    (CODEX_HOME=... in front of its program), since a session reads its hooks only from its own home."""
    codex_home_setting = read_environment_settings_of_launch_command(
        find_launch_command(load_settings(settings_file_path), "Codex")).get("CODEX_HOME")
    return list(dict.fromkeys([find_codex_home_folder()] + ([os.path.expanduser(codex_home_setting)] if codex_home_setting else [])))


def read_settings_file(file_path):
    """Returns a settings file's contents ({} when it doesn't exist yet).

    Raises:
        ValueError: It isn't a JSON object, so changing it could lose the user's settings; the
            message names the file.
    """
    if not os.path.exists(file_path):
        return {}
    with open(file_path) as settings_file:
        try:
            settings = json.load(settings_file)
        except ValueError as error:
            raise ValueError(f"{file_path} isn't valid JSON ({error})") from error
    if not isinstance(settings, dict):
        raise ValueError(f"{file_path} doesn't hold a JSON object")
    return settings


def write_settings_file(settings, file_path):
    """Copies the file to its backup the first time only, so the backup keeps the file as it was
    before the app first changed it (each write replaced it, with a later install or a removal
    leaving the app's own version there), then replaces the file at once
    with the new contents, keeping its permissions. A symlink (a dotfiles manager's) stays: the
    file it points to is the one replaced."""
    file_path = os.path.realpath(file_path)
    mode = 0o644
    if os.path.exists(file_path):
        if not os.path.exists(file_path + BACKUP_FILE_SUFFIX):
            shutil.copy2(file_path, file_path + BACKUP_FILE_SUFFIX)
        mode = os.stat(file_path).st_mode & 0o777
    elif not os.path.exists(file_path + BACKUP_FILE_SUFFIX):
        # There was no file: the backup says so with {}, what a missing file reads as, or the next
        # write would copy the app's own file there.
        with open(file_path + BACKUP_FILE_SUFFIX, "w") as backup_file:
            backup_file.write("{}\n")
    write_json_file_atomically(settings, file_path, indent=2, ensure_ascii=False, file_mode=mode)


def install_hooks(agent_tool_name):
    """Adds the app's hooks to the agent tool's settings files; returns the files changed. A file
    that has them already (perhaps naming the same Python by another link) is left as it is.

    Raises:
        OSError, ValueError: A file couldn't be read or written; files changed before it stay changed.
    """
    command = build_forwarder_command(agent_tool_name)
    events = HOOK_EVENTS_BY_AGENT_TOOL_NAME[agent_tool_name]
    changed_file_paths = []
    for file_path in list_settings_file_paths(agent_tool_name):
        settings = read_settings_file(file_path)
        if settings_have_the_apps_hooks(settings, command, events):
            continue
        new_settings = add_the_apps_hooks(settings, command, events)
        if new_settings != settings:
            write_settings_file(new_settings, file_path)
            changed_file_paths.append(file_path)
    return changed_file_paths


def remove_hooks(agent_tool_name):
    """Takes the app's hooks out of the agent tool's settings files; returns the files changed."""
    changed_file_paths = []
    for file_path in list_settings_file_paths(agent_tool_name):
        settings = read_settings_file(file_path)
        new_settings = remove_the_apps_hooks(settings)
        if new_settings != settings:
            write_settings_file(new_settings, file_path)
            changed_file_paths.append(file_path)
    return changed_file_paths


def take_out_the_hooks_only_earlier_versions_added():
    """Takes the hooks that only earlier versions added (AGENT_TOOL_NAMES_WHOSE_HOOKS_ONLY_EARLIER_VERSIONS_ADDED)
    out of the agent tools' files, as the app starts, and prints each file changed, or why one
    couldn't be. Codex's still started a forwarder for each event, which reached no tab (see the
    module's docstring), and would fail on every event once the app is uninstalled. A hook the user
    added to an event after the app's moves up a place in that event's list, so Codex, which
    records its approval of a hook by its place, asks for that approval again in its /hooks screen."""
    for agent_tool_name in AGENT_TOOL_NAMES_WHOSE_HOOKS_ONLY_EARLIER_VERSIONS_ADDED:
        try:
            changed_file_paths = remove_hooks(agent_tool_name)
        except (OSError, ValueError) as error:
            print(f"agent-command-tab: the hooks earlier versions added for {agent_tool_name} couldn't be taken out: {error}", flush=True)
            continue
        for file_path in changed_file_paths:
            print(f"agent-command-tab: took the hooks earlier versions added for {agent_tool_name} out of {file_path}", flush=True)


def this_copys_hooks_can_run(python_path=None, forwarder_path=FORWARDER_FILE_PATH):
    """Returns whether the Python and the forwarder this copy's hooks run exist. The app can go on
    running after its Python is gone, so its hooks then fail, silently, since they run in the
    background, and the list then shows wrong states without a word.

    Args:
        python_path: The hooks' Python; find_python_of_this_copy's when None.
        forwarder_path: The hooks' hook_event_forwarder.py.
    """
    return os.access(python_path or find_python_of_this_copy(), os.X_OK) and os.path.isfile(forwarder_path)


def check_hooks(agent_tool_name):
    """Returns HOOKS_CANT_RUN when the Python or the forwarder this copy's hooks run is gone
    (this_copys_hooks_can_run), else HOOKS_ARE_INSTALLED when every settings file of the agent
    tool has the app's hooks with this app's command, and HOOKS_ARE_MISSING otherwise (also
    when no file can be read)."""
    if not this_copys_hooks_can_run():
        return HOOKS_CANT_RUN  # installing them again wouldn't help: the app itself needs reinstalling
    command = build_forwarder_command(agent_tool_name)
    events = HOOK_EVENTS_BY_AGENT_TOOL_NAME[agent_tool_name]
    file_paths = list_settings_file_paths(agent_tool_name)
    for file_path in file_paths:
        try:
            settings = read_settings_file(file_path)
        except (OSError, ValueError):
            return HOOKS_ARE_MISSING
        if not settings_have_the_apps_hooks(settings, command, events):
            return HOOKS_ARE_MISSING
    return HOOKS_ARE_INSTALLED if file_paths else HOOKS_ARE_MISSING
