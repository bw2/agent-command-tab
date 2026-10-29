"""Paths to this installed copy of the app that other programs keep and run later (the hooks in Claude
Code's and Codex's settings, the script in iTerm2's AutoLaunch folder that starts the app), chosen to
survive upgrades."""

import os
import re
import sys

# A Homebrew install's files are in a folder named after its version (<prefix>/Cellar/<name>/<version>),
# which `brew upgrade` deletes; <prefix>/opt/<name> always leads to the installed version.
HOMEBREW_VERSION_FOLDER_PATTERN = re.compile(r"^(.*)/Cellar/([^/]+)/[^/]+/(.*)$")


def replace_homebrew_version_folder_with_its_stable_link(path, path_exists=os.path.exists):
    """Returns path with a Homebrew version folder (<prefix>/Cellar/<name>/<version>/) replaced by
    <prefix>/opt/<name>/, so that a hook or AutoLaunch script naming it keeps working after an upgrade
    (user request, 2026-10-28). Other paths, and one whose opt/ counterpart doesn't exist, are
    returned as they are."""
    match = HOMEBREW_VERSION_FOLDER_PATTERN.match(path)
    if not match:
        return path
    stable_path = f"{match.group(1)}/opt/{match.group(2)}/{match.group(3)}"
    return stable_path if path_exists(stable_path) else path


def find_python_of_this_copy(executable_path=None, prefix=None, base_prefix=None, path_exists=os.path.exists):
    """Returns the Python running the app, by a path that survives upgrades: in a virtual environment
    (pip, pipx, Homebrew), its bin/python, which every one has whatever Python version made it (a
    venv made again with Python 3.13 has no bin/python3.11, which the hooks named until 2026-10-28,
    user request); otherwise sys.executable. Either way, a Homebrew version folder becomes its opt/
    link (replace_homebrew_version_folder_with_its_stable_link).

    Args:
        executable_path, prefix, base_prefix: sys.executable, sys.prefix and sys.base_prefix when None.
        path_exists: os.path.exists, or a stand-in for tests.
    """
    executable_path = executable_path or sys.executable
    prefix = prefix or sys.prefix
    if prefix != (base_prefix or sys.base_prefix) and path_exists(os.path.join(prefix, "bin", "python")):
        executable_path = os.path.join(prefix, "bin", "python")
    return replace_homebrew_version_folder_with_its_stable_link(executable_path, path_exists)
