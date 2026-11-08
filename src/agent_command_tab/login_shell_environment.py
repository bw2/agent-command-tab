"""The user's login shell environment, for a copy of the app started without it.

iTerm2 starts the app through its AutoLaunch script (start_with_iterm2) with the environment
launchd gives apps: PATH is only PATH_THAT_MACOS_GIVES_APPS, and the variables the user's shell
start-up files set are missing. A `codex` installed with npm (a `#!/usr/bin/env node` script)
then can't find node, a `claude` installed through a Node version manager isn't found, and
CODEX_HOME, CLAUDE_CONFIG_DIR and TMUX_TMPDIR aren't seen. Started that way, the app asks the
login shell once for its environment and takes PATH and those variables from it. Started from a
shell, it has them already, and the shell isn't asked.
"""

import os

from agent_command_tab.settings_store import run_script_in_the_login_shell

# The PATH launchd gives the apps it starts, and so the programs they start. A shell's PATH always
# has more: macOS's /etc/paths, which every login shell reads, adds /usr/local/bin.
PATH_THAT_MACOS_GIVES_APPS = ("/usr/bin", "/bin", "/usr/sbin", "/sbin")
# The variables, besides PATH, that the app reads and that users set in their shell's start-up files.
VARIABLES_TAKEN_FROM_THE_LOGIN_SHELL = ("CODEX_HOME", "CLAUDE_CONFIG_DIR", "TMUX_TMPDIR")
# Printed by the shell before and after its environment, so that whatever its start-up files print is left out.
ENVIRONMENT_OUTPUT_MARKER = "AGENT_COMMAND_TAB_ENVIRONMENT_MARKER"


def environment_lacks_the_login_shells(environment):
    """Returns whether environment's PATH has no folder beyond PATH_THAT_MACOS_GIVES_APPS, as for a
    program that launchd or iTerm2's AutoLaunch script started rather than a shell."""
    return set(filter(None, environment.get("PATH", "").split(os.pathsep))) <= set(PATH_THAT_MACOS_GIVES_APPS)


def parse_environment_between_markers(shell_output):
    """Returns {name: value} from the `env -0` output (NUL-separated name=value entries) between
    the two ENVIRONMENT_OUTPUT_MARKERs in shell_output (bytes), or None when either is missing."""
    marker = ENVIRONMENT_OUTPUT_MARKER.encode()
    _, first_marker, rest = shell_output.partition(marker)
    environment_output, last_marker, _ = rest.rpartition(marker)
    if not first_marker or not last_marker:
        return None
    return dict(entry.decode(errors="replace").partition("=")[::2] for entry in environment_output.split(b"\0") if b"=" in entry)


def read_login_shell_environment(shell=None):
    """Returns the environment of the user's login shell (or of shell) once it has read its
    start-up files as an interactive login shell does, or None when it couldn't be run
    (run_script_in_the_login_shell) or printed no environment."""
    completed_process = run_script_in_the_login_shell(
        f"printf %s {ENVIRONMENT_OUTPUT_MARKER}; /usr/bin/env -0; printf %s {ENVIRONMENT_OUTPUT_MARKER}", shell=shell,
        keeps_the_output=True)
    return None if completed_process is None else parse_environment_between_markers(completed_process.stdout)


def build_environment_with_the_login_shells(environment, login_shell_environment):
    """Returns a copy of environment whose PATH is the login shell's followed by environment's own
    folders it lacks, with the VARIABLES_TAKEN_FROM_THE_LOGIN_SHELL that environment lacks."""
    combined_environment = dict(environment)
    path_folders = login_shell_environment.get("PATH", "").split(os.pathsep) + environment.get("PATH", "").split(os.pathsep)
    combined_environment["PATH"] = os.pathsep.join(dict.fromkeys(folder for folder in path_folders if folder))
    for variable_name in VARIABLES_TAKEN_FROM_THE_LOGIN_SHELL:
        if login_shell_environment.get(variable_name) and not environment.get(variable_name):
            combined_environment[variable_name] = login_shell_environment[variable_name]
    return combined_environment


def add_the_login_shells_environment_when_started_without_it(environment, read_environment=read_login_shell_environment):
    """When environment (os.environ, changed in place) lacks the login shell's
    (environment_lacks_the_login_shells), adds the login shell's PATH and variables to it
    (build_environment_with_the_login_shells), so that the programs the app looks for and runs,
    and the folders it reads, are the ones the user's shell would use. Prints what it did.

    Args:
        environment: The environment to complete, os.environ for the app.
        read_environment: read_login_shell_environment, or a stand-in for tests.
    """
    if not environment_lacks_the_login_shells(environment):
        return
    login_shell_environment = read_environment()
    if login_shell_environment is None:
        print("agent-command-tab: started without a shell's PATH, and the login shell's environment couldn't be read; "
              "programs are looked for in the usual folders too", flush=True)
        return
    combined_environment = build_environment_with_the_login_shells(environment, login_shell_environment)
    added_variable_names = [variable_name for variable_name in VARIABLES_TAKEN_FROM_THE_LOGIN_SHELL
                            if combined_environment.get(variable_name) != environment.get(variable_name)]
    environment.update(combined_environment)
    print(f"agent-command-tab: started without a shell's PATH; took PATH{''.join(', ' + name for name in added_variable_names)} "
          "from the login shell", flush=True)
