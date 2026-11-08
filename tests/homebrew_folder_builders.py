"""Builds a stand-in Homebrew prefix: a file in agent-command-tab's version folder and Homebrew's
opt link to that folder, for the tests of paths that must survive `brew upgrade`
(installed_copy_paths.replace_homebrew_version_folder_with_its_stable_link)."""

import os


def make_file_in_a_homebrew_version_folder(prefix, path_in_the_version_folder):
    """Makes an empty file at <prefix>/Cellar/agent-command-tab/0.1.0/<path_in_the_version_folder>
    and returns its path."""
    path = os.path.join(prefix, "Cellar", "agent-command-tab", "0.1.0", path_in_the_version_folder)
    os.makedirs(os.path.dirname(path))
    open(path, "w").close()
    return path


def add_the_homebrew_opt_link(prefix):
    """Makes <prefix>/opt/agent-command-tab, Homebrew's link to the version folder that survives an upgrade."""
    os.makedirs(os.path.join(prefix, "opt"))
    os.symlink(os.path.join(prefix, "Cellar", "agent-command-tab", "0.1.0"), os.path.join(prefix, "opt", "agent-command-tab"))
