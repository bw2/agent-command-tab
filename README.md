<a href="https://github.com/bw2/agent-command-tab/actions/workflows/test.yml"><img src="https://github.com/bw2/agent-command-tab/actions/workflows/test.yml/badge.svg" align="right" alt="unit tests"></a>

# AgentCommandTab

A macOS menu bar app that lists the iTerm2 windows and tabs, and the tmux sessions and windows, where AI coding agents run (Claude Code, OpenAI Codex CLI, and others), shows what each agent is doing, and lets you rename, focus, reorder and move those tabs without interrupting the agents.

## What it does

- **One tree of your agents:** every iTerm2 window and tmux session with at least one agent tab, with its tabs below it, grouped by monitor when you have several. Tabs without an agent are left out.
- **What each agent is doing:** a status dot and word per tab (asking a question or for permission, thinking, running a tool, stuck, running in the background, done, new, idle, paused, error), the context size in tokens and its share of the context window, and optionally when it started, how long it has run, its memory and CPU use. "Done" stays until you look at the tab, and a Claude Code session is "new" until you send it its first message. The menu bar icon counts the agents waiting for your answer.
- **A preview per tab:** what the agent is asking or running, what it said since your last prompt, or that prompt. The tab's arrow opens it, along with a row for each process the agent started (right-click one for Info or Kill).
- **Control:** double-click to bring a window or tab to the front; press and hold to peek at a tab; rename in place; drag tabs between windows and windows between monitors; start a new agent window or tab; duplicate a tab; type `/compact` or `/clear` into an agent waiting at its prompt; end an agent's process; pause agents (with every process they started) before going offline and resume them later, per tab, window, monitor or all at once. Select several rows (Command-click, Shift-click) to act on all their agents at once (a busy agent gets `/compact` or `/clear` when its turn ends), or to ask them to talk to each other: each gets your note and how to message the others.
- **Keyboard:** Return shows the selected tab or window, Delete ends its agent (asking first), R renames it, the Right and Left arrows open and close a tab's preview, typing a name's first letters selects its row, Command-comma opens Settings, and Esc closes the list.
- **Sort and filter:** sort by status, name, share of the context window, start time, memory or CPU use, or keep the tab order; hide the idle tabs with one click or Command-I (new ones stay).
- **Your choice of view:** a dropdown under the menu bar icon, a sidebar that hides at the screen's edge or stays visible (optionally keeping iTerm2 windows out of its way), or a regular window (optionally always on top).
- **A shortcut from any app:** Shift-Command-Tab (or Command-Fn-Tab, chosen in Settings) shows the list with the first agent waiting for your answer selected, and hides it when pressed again. While macOS's app switcher is open, the keys still go to it.

## Requirements

- macOS, with iTerm2 3.7 or later and iTerm2's Python API turned on (iTerm2 > Settings > General > Magic > Enable Python API), or tmux 3.2 or later, or both.
- Python 3.10 or later (for an install with pip or pipx).

## tmux

Agents running inside tmux, in any terminal app, are listed next to iTerm2's. Each tmux session is a window row, labeled "tmux:", and each of its tmux windows a tab row, judged by its first pane (as an iTerm2 tab is). The app reads the default tmux server (the one a plain `tmux` command uses) once a second.

Almost everything works as in iTerm2: the states, previews, process rows, /compact and /clear, Talk to Each Other, pause and resume, rename, new tabs and windows, Duplicate, moving a tab within a session or into another session, and Close. Some differences:

- tmux doesn't report agent states, so the app takes Claude Code's own report of each session, and for Codex the turn timer on its screen (a Codex approval request doesn't show as asking). Agent tools other than Claude Code and Codex aren't recognized in tmux.
- A tmux session has no window of its own, so it is listed with the menu-bar monitor and can't be moved to another monitor, and a tab can't move between iTerm2 and tmux.
- Showing a tab selects it in tmux and switches an attached client to its session, then brings that client's terminal to the front (its iTerm2 tab, or any other terminal app). With no client attached, the app says how to attach one.
- The app can't tell which tab of a terminal app other than iTerm2 is in front, so a "done" tab whose tmux client runs there stays "done" until you show it from the list.
- Typing into an agent (/compact, /clear, Talk to Each Other, pause and resume) is refused in a tmux window with `synchronize-panes` on, since the keys would reach its other panes too, and while its pane is in copy mode, which would take the keys.

## Install

```bash
pipx install agent-command-tab        # or: pip install agent-command-tab
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

From its first start on, iTerm2 starts the app whenever iTerm2 starts: the app adds `AgentCommandTab.scpt` to iTerm2's AutoLaunch folder (`~/Library/Application Support/iTerm2/Scripts/AutoLaunch`), which starts the copy you ran, however you installed it. Started that way, it has iTerm2's permissions, as when you start it from an iTerm2 tab. Turning off Settings > Launch AgentCommandTab whenever iTerm2 is started removes the script. Only one copy runs at a time; starting another prints that one is already running. Before uninstalling, turn that setting off and run `agent-command-tab remove-hooks`, since pip and pipx leave the script and the hooks behind.

The app's hooks tell a crashed agent from one that ended normally (its row stays, gray and "exited", for 5 minutes), count Claude Code's background shells and agents, and tell when a Claude Code turn ends while one of those still runs. The list offers to install them (Install Hooks, at its bottom left) while an agent tool in it lacks them. The first time the app changes a settings file, it copies it to `<file>.agent-command-tab-backup`. Sessions started afterwards use them, and Codex asks you to approve them first: type `/hooks` in Codex.

The shortcut needs the Accessibility permission (System Settings > Privacy & Security > Accessibility), because it has to see the keys before macOS's app switcher does. macOS gives it to the app that starts AgentCommandTab, so started from iTerm2, turn it on for iTerm2. Until it is on, Settings show a button that opens that page.

## Development

```bash
.venv/bin/python -m unittest discover -s tests -p '*_tests.py'
```

[docs/RELEASING.md](https://github.com/bw2/agent-command-tab/blob/main/docs/RELEASING.md) describes how to publish a version.

## License

MIT; see [LICENSE](https://github.com/bw2/agent-command-tab/blob/main/LICENSE). The app uses iTerm2's `iterm2` Python package, which is licensed GPL-2.0-or-later. pip and pipx install it alongside as a separate package.
