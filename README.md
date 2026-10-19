# LLM Sidebar Dashboard

A macOS menu bar app and optional screen-edge sidebar that lists the iTerm2 windows and tabs where AI coding agents are running (Claude Code, OpenAI Codex CLI, and others), shows what each agent is doing, and lets you rename, focus, reorder and move those tabs without interrupting the agents.

**Status: planning.** No app code yet. See [docs/REQUIREMENTS.md](docs/REQUIREMENTS.md) for what it will do and [docs/PLAN.md](docs/PLAN.md) for the design and the implementation phases.

## What it will do

- Menu bar icon whose dropdown lists every iTerm2 window that has at least one agent tab, with each window's tabs in a collapsible sublist.
- The same list as a sidebar on the edge of the screen, either kept visible or auto-hiding.
- A status dot per tab (idle, working, waiting for you, done, error, stuck) and a summary per window.
- Click a tab to bring its window and tab to the front.
- Rename windows and tabs in place.
- Right-click a window to open a new tab running an agent.
- Drag tabs to reorder them or move them to another window, without restarting the agent.
- With several monitors, group windows by monitor and drag windows between monitors.

## Requirements (planned)

- macOS with iTerm2 3.7 or later, and iTerm2's Python API turned on (Settings > General > Magic > Enable Python API).
- Python 3.10 or later.

## License

Not chosen yet. The app will use iTerm2's official `iterm2` Python package, which is licensed GPL-2.0-or-later, so this project's license needs to be GPL-compatible. See the open decisions in [docs/PLAN.md](docs/PLAN.md).
