"""The app's own hooks in Claude Code's and Codex's settings (PLAN.md section 6, "Hook events"):
installing, removing and checking them (user request, 2026-10-27).

Each hook runs hook_event_forwarder.py, which sends the event to the app. The events tell the
app when an agent's session ended normally (so a crash leaves an "exited" row, R14.1), and
Claude Code's background shells and agents (the lights after a tab's name, R26.2). The entries
are the app's by their command, which names hook_event_forwarder.py, so installing again
replaces them and removing takes out only them. Each file the installer changes is copied first
to <file>.agent-command-tab-backup.

- Claude Code: settings.json in each config folder in use (claude_code.list_claude_config_folders,
  so a Claude Code started with CLAUDE_CONFIG_DIR=~/.claude_work gets them too). A session
  reads its hooks when it starts, so only sessions started afterwards report.
- Codex: hooks.json in its home folder. Codex runs a new hook only once the user approves it in
  its /hooks screen, which records the approval in config.toml under
  [hooks.state."<hooks.json path>:<event>:<group>:<hook>"] (PHASE0_RESULTS.md).
"""

import json
import os
import re
import shlex
import shutil
import sys
import tempfile

from agent_command_tab.agent_adapters.claude_code import list_claude_config_folders

FORWARDER_FILE_NAME = "hook_event_forwarder.py"
FORWARDER_FILE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), FORWARDER_FILE_NAME)
SECONDS_A_HOOK_MAY_RUN = 10
BACKUP_FILE_SUFFIX = ".agent-command-tab-backup"
# The events each agent tool's hooks report, each with the tools a PostToolUse entry is limited
# to (Claude Code's background shells and agents start from Bash and Agent tool calls; Task is
# the Agent tool's older name), so other tool calls start no forwarder. Claude Code's prompts and
# turn ends tell when a turn ended while iTerm2's status still says working, as its Claude Code
# integration keeps it while a background task runs (hook_event_tracking.find_when_the_hooks_ended_the_turn).
HOOK_EVENTS_BY_AGENT_TOOL_NAME = {
    "Claude Code": [("SessionStart", None), ("UserPromptSubmit", None), ("Stop", None), ("SubagentStop", None),
                    ("SessionEnd", None), ("PostToolUse", "Bash|Agent|Task")],
    "Codex": [("SessionStart", None), ("Stop", None), ("SessionEnd", None)],
}
HOOKS_ARE_INSTALLED = "installed"
HOOKS_ARE_MISSING = "missing"
HOOKS_AWAIT_APPROVAL = "needs approval"  # Codex: in hooks.json, not yet approved in its /hooks screen


def build_forwarder_command(agent_tool_name, python_path=sys.executable, forwarder_path=FORWARDER_FILE_PATH):
    """Returns the hook command: this Python, isolated (-I) and without site packages (-S), running the forwarder."""
    return " ".join(shlex.quote(part) for part in (python_path, "-I", "-S", forwarder_path, agent_tool_name))


def hook_group_is_the_apps(hook_group):
    """Whether a hooks entry ({"matcher": ..., "hooks": [{"type": "command", "command": ...}]}) runs the forwarder."""
    return isinstance(hook_group, dict) and isinstance(hook_group.get("hooks"), list) and any(
        isinstance(hook, dict) and FORWARDER_FILE_NAME in str(hook.get("command", "")) for hook in hook_group["hooks"])


def remove_the_apps_hooks(settings):
    """Returns the settings without the app's hook entries; an event left without entries is
    left out, and so is "hooks" when only the app's entries were in it."""
    hooks = settings.get("hooks")
    if not isinstance(hooks, dict):
        return dict(settings)
    kept_hooks = {}
    for event_name, hook_groups in hooks.items():
        if not isinstance(hook_groups, list):
            kept_hooks[event_name] = hook_groups
            continue
        kept_hook_groups = [hook_group for hook_group in hook_groups if not hook_group_is_the_apps(hook_group)]
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


def list_positions_of_the_apps_hooks(settings, command, events):
    """Returns [(event name, entry index, hook index)] of the app's hook for each event, or None
    unless every event has exactly the entry add_the_apps_hooks adds."""
    hooks = settings.get("hooks") if isinstance(settings.get("hooks"), dict) else {}
    positions = []
    for event_name, matcher in events:
        hook_groups = hooks.get(event_name) if isinstance(hooks.get(event_name), list) else []
        group_indexes = [index for index, hook_group in enumerate(hook_groups)
                         if hook_group == build_the_apps_hook_group(command, matcher)]
        if not group_indexes:
            return None
        positions.append((event_name, group_indexes[0], 0))
    return positions


def convert_event_name_to_codex_hook_state_name(event_name):
    """Returns the name Codex's config.toml gives an event in its hook approvals ("SessionEnd" -> "session_end")."""
    return re.sub(r"(?<!^)(?=[A-Z])", "_", event_name).lower()


def codex_has_approved_the_hooks(config_text, hooks_file_path, positions):
    """Whether Codex's config.toml records an approval for the hook at each position
    (list_positions_of_the_apps_hooks). It can't tell whether an approval recorded at the same
    position was for another hook: Codex keeps a hash of the hook it approved, whose recipe isn't known."""
    return all(f'[hooks.state."{hooks_file_path}:{convert_event_name_to_codex_hook_state_name(event_name)}:'
               f'{group_index}:{hook_index}"]' in config_text for event_name, group_index, hook_index in positions)


def find_codex_home_folder():
    return os.environ.get("CODEX_HOME") or os.path.expanduser("~/.codex")


def list_settings_file_paths(agent_tool_name):
    """Returns the files that hold the agent tool's hooks, in folders that exist (a tool that
    isn't installed has none)."""
    if agent_tool_name == "Claude Code":
        folders = [folder or os.path.expanduser("~/.claude") for folder in list_claude_config_folders()]
        return [os.path.join(folder, "settings.json") for folder in folders if os.path.isdir(folder)]
    codex_home_folder = find_codex_home_folder()
    return [os.path.join(codex_home_folder, "hooks.json")] if os.path.isdir(codex_home_folder) else []


def read_settings_file(file_path):
    """Returns a settings file's contents ({} when it doesn't exist yet).

    Raises:
        ValueError: It isn't a JSON object, so changing it could lose the user's settings.
    """
    if not os.path.exists(file_path):
        return {}
    with open(file_path) as settings_file:
        settings = json.load(settings_file)
    if not isinstance(settings, dict):
        raise ValueError(f"{file_path} doesn't hold a JSON object")
    return settings


def write_settings_file(settings, file_path):
    """Copies the file to its backup, then replaces it at once with the new contents, keeping its
    permissions. A symlink (a dotfiles manager's) stays: the file it points to is the one replaced."""
    file_path = os.path.realpath(file_path)
    mode = 0o644
    if os.path.exists(file_path):
        shutil.copy2(file_path, file_path + BACKUP_FILE_SUFFIX)
        mode = os.stat(file_path).st_mode & 0o777
    file_descriptor, temporary_path = tempfile.mkstemp(dir=os.path.dirname(file_path), suffix=".tmp")
    with os.fdopen(file_descriptor, "w") as temporary_file:
        json.dump(settings, temporary_file, indent=2, ensure_ascii=False)
        temporary_file.write("\n")
    os.chmod(temporary_path, mode)
    os.replace(temporary_path, file_path)


def install_hooks(agent_tool_name):
    """Adds the app's hooks to the agent tool's settings files; returns the files changed.

    Raises:
        OSError, ValueError: A file couldn't be read or written; files changed before it stay changed.
    """
    command = build_forwarder_command(agent_tool_name)
    events = HOOK_EVENTS_BY_AGENT_TOOL_NAME[agent_tool_name]
    changed_file_paths = []
    for file_path in list_settings_file_paths(agent_tool_name):
        settings = read_settings_file(file_path)
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


def check_hooks(agent_tool_name):
    """Returns HOOKS_ARE_INSTALLED when every settings file of the agent tool has the app's
    hooks with this app's command (and, for Codex, they are approved), HOOKS_AWAIT_APPROVAL for
    Codex hooks not yet approved, and HOOKS_ARE_MISSING otherwise (also when no file can be read)."""
    command = build_forwarder_command(agent_tool_name)
    events = HOOK_EVENTS_BY_AGENT_TOOL_NAME[agent_tool_name]
    file_paths = list_settings_file_paths(agent_tool_name)
    positions_by_file_path = {}
    for file_path in file_paths:
        try:
            positions_by_file_path[file_path] = list_positions_of_the_apps_hooks(read_settings_file(file_path), command, events)
        except (OSError, ValueError):
            positions_by_file_path[file_path] = None
    if not file_paths or None in positions_by_file_path.values():
        return HOOKS_ARE_MISSING
    if agent_tool_name == "Codex":
        try:
            with open(os.path.join(find_codex_home_folder(), "config.toml")) as config_file:
                config_text = config_file.read()
        except OSError:
            config_text = ""
        if not all(codex_has_approved_the_hooks(config_text, file_path, positions)
                   for file_path, positions in positions_by_file_path.items()):
            return HOOKS_AWAIT_APPROVAL
    return HOOKS_ARE_INSTALLED
