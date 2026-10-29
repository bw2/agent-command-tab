"""The app's own hooks in Claude Code's and Codex's settings:
installing, removing and checking them (user request, 2026-10-27).

Each hook runs hook_event_forwarder.py, which sends the event to the app. The events tell the
app when an agent's session ended normally (so a crash leaves an "exited" row), and
Claude Code's background shells and agents (the lights after a tab's name). The entries
are the app's by their command, which names hook_event_forwarder.py, so installing again
replaces them and removing takes out only them. Each file the installer changes is copied first
to <file>.agent-command-tab-backup.

- Claude Code: settings.json in each config folder in use (claude_code.list_claude_config_folders,
  so a Claude Code started with CLAUDE_CONFIG_DIR=~/.claude_work gets them too). A session
  reads its hooks when it starts, so only sessions started afterwards report.
- Codex: hooks.json in its home folder. Codex runs a new hook only once the user approves it in
  its /hooks screen, which records the approval in config.toml under
  [hooks.state."<hooks.json path>:<event>:<group>:<hook>"], with a hash of
  the hook as it was then, so a changed hook needs approving again (compute_codex_approval_hash).
"""

import hashlib
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
# The events each agent tool's hooks report, each with the tools a PreToolUse or PostToolUse
# entry is limited to (Claude Code's background shells and agents start from Bash and Agent tool
# calls; Task is the Agent tool's older name), so other tool calls start no forwarder. Claude
# Code's prompts and turn ends tell when a turn ended while iTerm2's status still says working, as
# its Claude Code integration keeps it while a background task runs, and the main agent's tool
# calls after a Stop that another Stop hook blocked tell that the turn goes on, from the start of
# its first command (PreToolUse; code review, 2026-10-28;
# hook_event_tracking.find_when_the_hooks_ended_the_turn).
HOOK_EVENTS_BY_AGENT_TOOL_NAME = {
    "Claude Code": [("SessionStart", None), ("UserPromptSubmit", None), ("Stop", None), ("SubagentStop", None),
                    ("SessionEnd", None), ("PreToolUse", "Bash|Agent|Task"), ("PostToolUse", "Bash|Agent|Task")],
    "Codex": [("SessionStart", None), ("Stop", None), ("SessionEnd", None)],
}
# Codex's /hooks screen shows a hook's statusMessage as its name, "Unnamed hook" without one, and
# its status line shows it while the hook runs (user request, 2026-10-27). Claude Code's hooks have
# no names (its statusMessage is only spinner text), so its entries go without.
HOOK_NAMES_BY_AGENT_TOOL_NAME = {"Codex": "Report to AgentCommandTab"}
CODEX_SESSION_END_LONGEST_TIMEOUT = 3  # seconds: Codex lowers a longer SessionEnd hook timeout to this
HOOKS_ARE_INSTALLED = "installed"
HOOKS_ARE_MISSING = "missing"
HOOKS_AWAIT_APPROVAL = "needs approval"  # Codex: in hooks.json, not yet approved in its /hooks screen


def build_forwarder_command(agent_tool_name, python_path=None, forwarder_path=FORWARDER_FILE_PATH):
    """Returns the hook command: this Python (python_path, or the one running), isolated (-I) and
    without site packages (-S), running the forwarder."""
    parts = (python_path or sys.executable, "-I", "-S", forwarder_path, agent_tool_name)
    return " ".join(shlex.quote(part) for part in parts)


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


def build_the_apps_hook_group(command, matcher, hook_name=None):
    hook = {"type": "command", "command": command, "async": True, "timeout": SECONDS_A_HOOK_MAY_RUN}
    return dict({"matcher": matcher} if matcher else {},
                hooks=[dict(hook, statusMessage=hook_name) if hook_name else hook])


def add_the_apps_hooks(settings, command, events, hook_name=None):
    """Returns the settings with one entry of the app's per event, after the user's own, in place
    of any the app added before (with another command, other events or another name).

    Args:
        settings: The settings file's contents.
        command: build_forwarder_command's command.
        events: [(event name, matcher or None)], from HOOK_EVENTS_BY_AGENT_TOOL_NAME.
        hook_name: From HOOK_NAMES_BY_AGENT_TOOL_NAME, or None for unnamed entries.
    """
    settings = remove_the_apps_hooks(settings)
    hooks = dict(settings.get("hooks") or {})
    for event_name, matcher in events:
        hooks[event_name] = list(hooks.get(event_name) or []) + [build_the_apps_hook_group(command, matcher, hook_name)]
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


def list_positions_of_the_apps_hooks(settings, command, events, hook_name=None):
    """Returns [(event name, entry index, hook index)] of the app's hook for each event, or None
    unless every event has the entry add_the_apps_hooks adds (hook_group_is_the_same_as)."""
    hooks = settings.get("hooks") if isinstance(settings.get("hooks"), dict) else {}
    positions = []
    for event_name, matcher in events:
        hook_groups = hooks.get(event_name) if isinstance(hooks.get(event_name), list) else []
        group_indexes = [index for index, hook_group in enumerate(hook_groups)
                         if hook_group_is_the_same_as(hook_group, build_the_apps_hook_group(command, matcher, hook_name))]
        if not group_indexes:
            return None
        positions.append((event_name, group_indexes[0], 0))
    return positions


def convert_event_name_to_codex_hook_state_name(event_name):
    """Returns the name Codex's config.toml gives an event in its hook approvals ("SessionEnd" -> "session_end")."""
    return re.sub(r"(?<!^)(?=[A-Z])", "_", event_name).lower()


def compute_codex_approval_hash(event_name, hook_group):
    """Returns the trusted_hash Codex records in config.toml when the user approves the hook in an
    entry of the app's (hook_hash in Codex's codex-rs/hooks/src/engine/discovery.rs): SHA-256 of
    the event's name and the entry, as JSON with sorted keys and no spaces. Of the changes Codex
    makes to an entry before hashing it, only one touches the app's: a SessionEnd hook's timeout
    is lowered to CODEX_SESSION_END_LONGEST_TIMEOUT."""
    hook = dict(hook_group["hooks"][0])
    if event_name == "SessionEnd":
        hook["timeout"] = min(hook["timeout"], CODEX_SESSION_END_LONGEST_TIMEOUT)
    identity = dict(hook_group, event_name=convert_event_name_to_codex_hook_state_name(event_name), hooks=[hook])
    return "sha256:" + hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def codex_has_approved_the_hooks(config_text, hooks_file_path, settings, positions):
    """Whether Codex's config.toml records an approval of the hook at each position
    (list_positions_of_the_apps_hooks) as it is in the settings now. An approval of an earlier
    version, such as one without its name, doesn't count: Codex skips the changed hook until the
    user approves it again."""
    for event_name, group_index, hook_index in positions:
        state_name = f"{hooks_file_path}:{convert_event_name_to_codex_hook_state_name(event_name)}:{group_index}:{hook_index}"
        state_section = re.search(rf'^\[hooks\.state\."{re.escape(state_name)}"\]$(.*?)(?=^\[|\Z)', config_text,
                                  re.MULTILINE | re.DOTALL)
        trusted_hash_line = f'trusted_hash = "{compute_codex_approval_hash(event_name, settings["hooks"][event_name][group_index])}"'
        if state_section is None or trusted_hash_line not in state_section.group(1):
            return False
    return True


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
    """Adds the app's hooks to the agent tool's settings files; returns the files changed. A file
    that has them already (perhaps naming the same Python by another link) is left as it is, so
    Codex's approval of them stays.

    Raises:
        OSError, ValueError: A file couldn't be read or written; files changed before it stay changed.
    """
    command = build_forwarder_command(agent_tool_name)
    events = HOOK_EVENTS_BY_AGENT_TOOL_NAME[agent_tool_name]
    hook_name = HOOK_NAMES_BY_AGENT_TOOL_NAME.get(agent_tool_name)
    changed_file_paths = []
    for file_path in list_settings_file_paths(agent_tool_name):
        settings = read_settings_file(file_path)
        if list_positions_of_the_apps_hooks(settings, command, events, hook_name) is not None:
            continue
        new_settings = add_the_apps_hooks(settings, command, events, hook_name)
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
    settings_and_positions_by_file_path = {}
    for file_path in file_paths:
        try:
            settings = read_settings_file(file_path)
        except (OSError, ValueError):
            return HOOKS_ARE_MISSING
        positions = list_positions_of_the_apps_hooks(settings, command, events, HOOK_NAMES_BY_AGENT_TOOL_NAME.get(agent_tool_name))
        if positions is None:
            return HOOKS_ARE_MISSING
        settings_and_positions_by_file_path[file_path] = (settings, positions)
    if not file_paths:
        return HOOKS_ARE_MISSING
    if agent_tool_name == "Codex":
        try:
            with open(os.path.join(find_codex_home_folder(), "config.toml")) as config_file:
                config_text = config_file.read()
        except OSError:
            config_text = ""
        if not all(codex_has_approved_the_hooks(config_text, file_path, settings, positions)
                   for file_path, (settings, positions) in settings_and_positions_by_file_path.items()):
            return HOOKS_AWAIT_APPROVAL
    return HOOKS_ARE_INSTALLED
