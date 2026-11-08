"""Writes stand-in programs (a node, a claude, a codex) for the tests that run one."""

import os


def write_executable_script(folder, name, text):
    """Writes text to folder/name, makes it executable, and returns its path."""
    path = os.path.join(folder, name)
    with open(path, "w") as script_file:
        script_file.write(text)
    os.chmod(path, 0o755)
    return path
