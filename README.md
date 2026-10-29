<a href="https://github.com/bw2/agent-command-tab/actions/workflows/test.yml"><img src="https://github.com/bw2/agent-command-tab/actions/workflows/test.yml/badge.svg" align="right" alt="unit tests"></a>

# AgentCommandTab

A macOS menu bar app that lists the iTerm2 windows and tabs where AI coding agents run (Claude Code, OpenAI Codex CLI, and others), shows what each agent is doing, and lets you rename, focus, reorder and move those tabs without interrupting the agents. The list shows as a dropdown under the menu bar icon, a sidebar at the edge of the screen, or a regular window.

## What it does

- **One tree of your agents:** every iTerm2 window with at least one agent tab, with its tabs below it, grouped by monitor when you have several. Tabs without an agent are left out.
- **What each agent is doing:** a status dot and word per tab (asking for permission, thinking, running a tool, stuck, running in the background, done, idle, error), how long it has been so, the context size in tokens, and optionally when it started, its memory and CPU use. "Done" stays until you look at the tab. The menu bar icon counts the agents waiting for your answer.
- **A preview per tab:** what the agent is asking or running, what it said since your last prompt, or that prompt. Open it with the tab's arrow.
- **Control:** double-click to bring a window or tab to the front; press and hold to peek at a tab; rename in place; drag tabs between windows and windows between monitors; start a new agent window or tab; duplicate a tab; type `/compact` or `/clear` into an agent waiting at its prompt; end an agent's process; pause agents (with every process they started) before going offline and resume them later, per tab, window, monitor or all at once. Select several rows (Command-click, Shift-click) to act on all their agents at once (a busy agent gets `/compact` or `/clear` when its turn ends), or to ask them to talk to each other: each gets your note and how to message the others.
- **Sort and filter:** sort by activity, name, start time, memory or CPU use, or keep iTerm2's order; hide the idle tabs with one click.
- **Your choice of view:** a dropdown under the menu bar icon, a sidebar that hides at the screen's edge or stays visible (optionally keeping iTerm2 windows out of its way), or a regular window (optionally always on top).
- **A shortcut from any app:** Shift-Command-Tab (or Command-Fn-Tab, chosen in Settings) shows the list, and hides it when pressed again. While macOS's app switcher is open, the keys still go to it.

## Requirements

- macOS, with iTerm2 3.7 or later and iTerm2's Python API turned on: iTerm2 > Settings > General > Magic > Enable Python API.
- Python 3.10 or later (for an install with pip or pipx).

## Install

```bash
pipx install agent-command-tab        # or: pip install agent-command-tab
brew install bw2/tap/agent-command-tab   # Homebrew adds the bw2/tap repository by itself
```

From a clone of this repository:

```bash
python3 -m venv .venv && .venv/bin/pip install -e .
```

## Use

```bash
agent-command-tab                # the menu bar app; restarts itself if iTerm2's connection drops
agent-command-tab dump-snapshot  # prints the current list as JSON
agent-command-tab install-hooks  # adds the app's hooks to Claude Code's and Codex's settings (remove-hooks takes them out)
agent-command-tab --version      # prints the installed version, for bug reports
```

Click the "C" in the menu bar to see the list, right-click it for Settings. The first time, iTerm2 asks whether to allow the app to use its Python API.

From its first start on, iTerm2 starts the app whenever iTerm2 starts: the app adds `AgentCommandTab.scpt` to iTerm2's AutoLaunch folder (`~/Library/Application Support/iTerm2/Scripts/AutoLaunch`), which starts the copy you ran, however you installed it. Started that way, it has iTerm2's permissions, as when you start it from an iTerm2 tab. Settings > Start with iTerm2 turns that off and removes the script. Only one copy runs at a time; starting another prints that one is already running. Before uninstalling, turn Start with iTerm2 off and run `agent-command-tab remove-hooks`, since pip and Homebrew leave the script and the hooks behind.

The app's hooks tell a crashed agent from one that ended normally (its row stays, gray and "exited", for 5 minutes), and count Claude Code's background shells and agents. The list offers to install them (Install Hooks, at its bottom left) while an agent tool in it lacks them. Each settings file is copied to `<file>.agent-command-tab-backup` first. Sessions started afterwards use them, and Codex asks you to approve them first: type `/hooks` in Codex.

The shortcut needs the Accessibility permission (System Settings > Privacy & Security > Accessibility), because it has to see the keys before macOS's app switcher does. macOS gives it to the app that starts AgentCommandTab, so started from iTerm2, turn it on for iTerm2. Until it is on, Settings show a button that opens that page.

## Development

```bash
.venv/bin/python -m unittest discover -s tests -p '*_tests.py'
```

[docs/RELEASING.md](https://github.com/bw2/agent-command-tab/blob/main/docs/RELEASING.md) describes how to publish a version.

## License

MIT; see [LICENSE](https://github.com/bw2/agent-command-tab/blob/main/LICENSE). The app uses iTerm2's `iterm2` Python package, which is licensed GPL-2.0-or-later. pip and the Homebrew formula install it alongside as a separate package.
