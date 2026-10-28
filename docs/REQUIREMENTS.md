# AgentCommandTab: requirements

Version 1, approved 2026-10-18 (with the review fixes of the same day).

**Terminology.**
- An *agent tool* is an AI coding agent program (Claude Code, OpenAI Codex CLI, ...).
- An *agent session* is one running copy of an agent tool in an iTerm2 tab.
- *Asking* means an agent session is waiting for the user: a permission prompt or a question.
- The tab *in front* is the selected tab of the frontmost iTerm2 window, only while iTerm2 is the active app.

## Purpose

See at a glance what every agent session in iTerm2 is doing, jump to the one that needs you, and organize iTerm2 windows and tabs, from a macOS menu bar dropdown or an auto-hiding sidebar.

## Constraints

- C1. macOS only. iTerm2 only (3.7.6 or later), with iTerm2's Python API turned on.
- C2. Written in Python. Uses iTerm2's official `iterm2` Python package.
- C3. Not tied to one person's setup: no hard-coded paths, accounts or folder layouts. Suitable for open-source release on GitHub.
- C4. Works with any agent tool through adapters. Claude Code and Codex are supported from the start, as close to equally as each tool allows (see "Known gaps for Codex"). Adding another agent tool takes a small adapter, not changes throughout the code.
- C5. May interrupt or type into a running agent session at the user's request (R15.1, R15.2), and may start a new agent session in an existing window or a new one (R13, R13.1).
- C6. For now, everything stays on the Mac: no network access and no telemetry. The design must make the later features in "Designed for later" possible without a rewrite: a phone app or web page as another view and control interface, introductions between agent sessions, and a shared view across several computers.

## Features

Make sure to carefully assess the relative complexity and difficulty of features in terms the amount of code required for the feature, the complexity of the code, the brittleness of the code supporting that feature, the computational, memory, and disk overhead needed to support it. WARN me about features where the complexity, difficulty, and overhead may not be worth the relative value of the feature.

### Menu bar

- R1. A menu bar icon on the right side of the menu bar, next to other apps' icons. The icon is a "C", drawn in color (the most urgent agent session's color, R2) rather than the usual monochrome, so it is easy to tell apart from other "C" icons such as Cynthia's.
- R2. The icon shows the number of agent sessions that are asking, in the color of the most urgent agent session (the setting that hid the number, and the icon color setting, were removed on 2026-10-25, user request).
- R3. Clicking the icon opens a dropdown panel with the window list and everything in R7 to R41.
- R3.1. Codex usage bars at the bottom: one bar per limit Codex reports (for example the weekly limit), with the percent used and the reset time. The app re-reads Codex's own session files every minute (a setting) and shows the latest numbers Codex recorded; they only change when a Codex session talks to OpenAI. Claude Code usage is left out of version 1: it is only available locally through Claude Code's status line command, and the user prefers not to wrap their existing status line. **Dropped (user decision, 2026-10-27).**
- R3.2. A global shortcut shows the list from any app the way "Show as" asks: it opens the dropdown, slides in the sidebar, or brings the window to the front. Pressed again, it hides the list (user request, 2026-10-21): it closes the dropdown, slides an auto-hiding sidebar out, or hides the window while the window is in front, and gives the keyboard back to the app that had it; a sidebar set to stay visible stays. Settings offer Shift-Command-Tab (the default), Command-Fn-Tab and Off. While macOS's app switcher is open (Command still held after Command-Tab), the keys go to the app switcher instead. It needs the Accessibility permission; without it, Settings say so and open that page of System Settings (user request, 2026-10-19).

### Sidebar

- R4. Optional sidebar showing the same list and controls as the dropdown.
- R5. The sidebar auto-hides: it slides in when the pointer touches the chosen screen edge and slides out when the pointer leaves, or at once on a click in another app's window (user request, 2026-10-22). It is always on the menu bar's monitor; the edge (right or left, default right) is a setting. Only outer edges are offered, meaning edges that don't border another monitor, because the pointer slides past a shared edge.
- R6. The sidebar never takes keyboard focus from iTerm2 unless the user clicks into a text field, and appears on every macOS desktop (Space).

### Window list

- R7. One row per iTerm2 window that has at least one agent session. Windows without one are not listed.
- R8. Each window row expands and collapses to show its agent session tabs as a sublist.
- R8.1. All rows start expanded, including rows that appear later. Collapse choices are kept until the app restarts. "Expand all" and "collapse all" controls sit near the top right, above the list (since 2026-10-19 one button that does either, beside the settings button).
- R8.2. Gestures:
  - click a window or monitor row's arrow (› or ⌄): expand or collapse it; a tab row's arrow opens or closes its preview (since 2026-10-19; before, a click anywhere on the row did this);
  - double-click a name, or choose Rename in the right-click menu: rename;
  - the right-click menu also has "Focus window";
  - drag a window row within its monitor group: reorder the list (R12);
  - drag a window row into another monitor's group: move the window (R32).
- R9. A window row shows the window's name, the status dot of its most urgent agent session (R20), and counts in urgency order (for example "1 asking, 2 working").
- R9.1. Default window name, until the user renames it: the folder name of the window's first agent session (for example "SpliceAI-lookup"). Needed because iTerm2 otherwise shows the current tab's title, which Claude Code sets to "claude" in every tab.
- R10. Rename a window in place from its row. This sets the real iTerm2 window title (as iTerm2's Edit Window Title does). An empty name returns to the default.
- R11. Order when the app starts: the window with the most recent prompt sent to any of its agent sessions comes first, read from each agent tool's own history. Windows with no history follow, in macOS's front-to-back window order. **Dropped (user decision, 2026-10-27):** the list starts in iTerm2's window order.
- R12. Order while running: stays stable (rows don't jump as states change). New windows appear at the top. The user can drag window rows to reorder the list. This changes only the list, not iTerm2, and isn't kept across restarts.
- R13. Right-click a window row to open a new tab in that window and start an agent session in it, with a choice of agent tool (Claude Code, Codex, others configured). The new tab starts in the folder of the window's selected agent session.
- R13.1. Start an agent session in a new iTerm2 window, from a "+" button next to the settings button or the right-click menu on empty space in the list, with a choice of agent tool and a folder picker that defaults to the folder used last.

### Tab list

- R14. One row per tab whose first pane runs an agent session. Other tabs and other panes are ignored. Split panes may become a sublist later.
- R14.1. When an agent session ends normally (for example Ctrl-D or /exit), its row disappears at once. When it ends abnormally (crash, killed, failed to start), its row stays in gray, marked "exited", for 5 minutes (a setting), then disappears. A window whose last agent session ended follows the same rule. **Built (user request, 2026-10-27)** for agents whose hooks (R14.2) reported: one whose process is gone without a normal session end is shown as exited, for a fixed 5 minutes rather than a setting. An agent whose hooks never reported (started before they were installed, or since the app started without an event yet) can't be told apart from one that ended normally, so its row just goes, as does that of an agent ended with Kill session.
- R14.2. The app's own hooks (user request, 2026-10-27): a small command that Claude Code's and Codex's hooks run, which sends the event to the app. An Install Hooks button adds them to the agent tools' settings; it is in a note at the bottom left of the list, shown while an agent tool with a session in the list lacks them (for Codex, also while Codex hasn't approved them in its /hooks screen). `agent-command-tab install-hooks` and `remove-hooks` do the same from the command line.
- R15. A tab row shows the tab's name, the agent tool, a status dot, how long it has been in that state, and how full its context window is (for example "62% ctx"), if the agent tool reports it. Right-click for a context menu that allows me to send /compact, /clear.
- R15.0. Default tab name, until the user renames it: the agent session's own name if the tool has one (Claude Code names each session, for example "profile-photo-3a"), otherwise the folder name. Needed because Claude Code sets every tab's title to "claude".
- R15.1. Sending a command is allowed only while the agent session is idle or done (sitting at its input prompt); the menu of several selected rows (R17.1) also reaches sessions in the middle of a turn, each getting the command when its turn ends. The app re-checks the status just before typing, asks for confirmation before /clear, and otherwise shows the menu item disabled with a gray reason. Each agent tool's adapter defines its own command names (Codex's may differ from Claude Code's).
- R15.2. The tab row's right-click menu has "Interrupt", which stops the current turn (it sends Esc, the agent tool's own interrupt key). It is enabled while the agent session is working or stuck.
- R15.3. Pause and resume agent sessions (user request, 2026-10-20), for example before going offline and after coming back: "Pause session" and "Resume session" in a tab row's right-click menu, "Pause N sessions" and "Resume N sessions" in window and monitor rows' menus, and one button at the right end of the list's bottom bar that pauses all, or resumes all once all are paused. A pause suspends the agent as Ctrl+Z does and stops every process below it; a resume types `fg` at the tab's shell prompt and continues those processes. `fg` is typed only when the shell that runs the agent is in front (not a shell or tmux started at its prompt), its cursor line looks exactly as it did right after the pause, and no other job is suspended in that shell; otherwise an alert says why and what to type. Ctrl+U goes before `fg`, clearing whatever the line held already right after the pause (keys typed while the pause ran, or a terminal reply meant for the agent). Processes below the agent that run as another user (with sudo) can't be stopped; an alert names them. If `fg` is typed by hand instead, or the tab is closed, the processes the pause stopped are continued too. A paused row shows the state "paused", also for an agent suspended with Ctrl+Z by hand. Ctrl+Z leaves the commands the agent started in their own process groups running, so that row still offers Pause, which stops them; Resume can't resume it safely (no pause saw its prompt) and says to type `fg`. Pause records are kept in a file next to the settings, so after the dashboard restarts, paused tabs are listed again, Resume works, and the processes below them are still continued. Time spent paused counts toward the agent's own time limits (a long command may be moved to the background).
- R15.4. Talk to Each Other (user request, 2026-10-21): in the menu of several selected rows (R17.1), it opens a small window with an editable "Message draft:" box holding a default note (the sessions may be working on related things, so each should message the others a summary of its work and work out how they can work together), with Send and Cancel. Each selected Claude Code or Codex session gets the note, then its own name, then, for each of the other selected sessions, its name or ID, process ID, folder, tab and how to message it: a Claude Code session by its name with Claude Code's SendMessage tool, a Codex session with `codex queue --thread <conversation ID>`. A Codex session can't message a Claude Code session, and its message says so. Codex sessions get the message through `codex queue`, which runs it after any turn under way. Claude Code has no supported way to queue a message from outside, so a Claude Code session's message is held until it waits at its prompt (its row shows idle or done) with an empty input box, then pasted there and submitted; after 30 minutes the app gives up. Sessions of other tools are left out, and the window names them. A Codex session can't take part while its conversation can't be identified: before its first prompt, right after /clear until its next prompt, and while another Codex session runs in the same folder. An alert names each session that didn't get the message and why.
- R16. Rename a tab in place from its row. This sets the real iTerm2 tab title (as iTerm2's Edit Tab Title does). An empty name returns to the default.
- R17. Click a tab row to bring its window and tab to the front.
- R17.1. A click selects a window or tab row, shown by an outline in the accent color, one outline around each run of adjacent selected rows; Command-click adds or removes a row, and Shift-click selects every row between the last one clicked and this one (user requests, 2026-10-21). Monitor rows and the "+" row can't be selected. Right-clicking one of two or more selected rows shows one menu for their agent sessions, a selected window counting as all of its tabs: /compact N sessions and /clear N sessions (every session whose agent takes the command and isn't paused; one in the middle of a turn gets it when the turn ends: Claude Code queues it itself, and the app holds it for Codex, which refuses commands during a task) and Kill sessions (Kill N sessions when a window row is selected, since it counts tabs that Hide Idle may hide); then Pause N sessions and Resume N sessions; then, last, Talk to each other… (R15.4; user request, 2026-10-27). A single row keeps its own menu, selected or not, as does an unselected row. Dragging one of several selected rows moves only that row.
- R18. Drag tab rows to reorder tabs within a window, or drop on another window to move the tab there. This changes the real iTerm2 tab order and window. The agent session keeps running without interruption.
- R19. iTerm2 tab groups are ignored. (iTerm2 keeps a group's tabs together, so it may adjust an order the user drags; the list shows what iTerm2 reports afterwards.)

### Status

- R20. Each agent session has one of these states, listed in urgency order (most urgent first):
  1. asking (permission prompt or question);
  2. error;
  3. stuck (working, but no visible progress for longer than 10 minutes, a fixed time since 2026-10-26, user request);
  4. working (a tab row says "thinking", or "running" while the agent waits on a command or on another tool for 5 seconds or more; window rows' counts say "working" for both; user requests, 2026-10-28);
  5. background (idle, but background processes it started keep running: background shells, background agents or detached jobs, not helpers such as a language server; a tab row says "running" in yellow, a window row's count "background"; user request, 2026-10-28);
  6. done (finished a turn, and its tab hasn't been in front since);
  7. idle;
  8. paused (its process is stopped by Pause or by Ctrl+Z, R15.3);
  9. unknown (no report yet);
  10. exited (only after an abnormal end, R14.1).
- R21. Each state has a distinct status dot: its own shape and color, so it works for color-blind users (R26.1). A window row's dot is the state of its most urgent agent session.
- R22. Status works for Claude Code and Codex. Other agent tools work if they report status through the terminal status signal (OSC 7501) or have an adapter. Otherwise they are listed with a basic working or idle status based on screen activity.
- R22.1. Rows keep a status after iTerm2 restarts (user request, 2026-10-21). iTerm2's tab bar then still shows each tab's status, but the status value its API returns stays empty until the agent's next hook event. While it is empty, a row takes the status iTerm2's tab bar shows; where that is empty too, a Claude Code row takes Claude Code's own report from `claude agents --json` (idle, working or asking, up to a minute old, never "done"). Because that report can be a minute old, nothing is typed into a Claude Code session while Claude Code's own session file, read just before typing, says it is anything but idle.

### Importance

- R26. Color and font style show each agent session's importance: how much it needs or deserves the user's attention. More important rows are brighter and bolder; less important ones are dimmer. Importance goes up when:
  - the agent session is asking (the biggest increase);
  - the user typed into or interacted with that agent session in the last 2 minutes;
  - it is working on complex or important code, judged by local activity rules from its hook events: editing many files, editing source code rather than docs or tests, long turns, many tool calls. The rules are adjustable in settings. No AI judgment in version 1.

  A window row shows the importance of its most important agent session. **Dropped (user decision, 2026-10-27),** with R26.1's shift of the working dot's hue: the tab in front has a bold name (R41).
- R26.1. Status dots:

  | State | Dot |
  |---|---|
  | asking | light blue ● |
  | working | blinking green ● |
  | background | steady yellow ● (user request, 2026-10-28) |
  | error | red ✕ |
  | stuck | amber ◆, slow pulse |
  | done | steady green ✔ (not blinking, so it differs from routine work) |
  | idle | gray ○ |
  | paused | violet ‖ |
  | unknown | gray dotted ◌ |
  | exited after a crash | dim gray ⊘ |

- R26.2. Each background process an agent session reports (for example a background shell command or a subagent) adds a small indicator light after its dot. Beyond a few lights, a number replaces them. **Built (user request, 2026-10-27)** with the lights after the name rather than the dot, so the names stay lined up: up to 4 yellow lights, then one light and the number ("•×6"). They count Claude Code's own list of its background shells and agents while its hooks (R14.2) report it, and otherwise the commands its shell tool left running in the background; either way, the detached jobs the session started too, which Claude Code's list doesn't hold (user request, 2026-10-28).

### Preview

- R23. Each tab row shows a one-line preview, in this order of preference: what it is asking (the tool asking permission, or the question), the tool it is running, the start of its last message, the user's last prompt. It is two gray lines under the tab's row, opened and closed by the row's arrow like one more level of the tree (user requests, 2026-10-19).
- R23.1. An open tab row also shows one sub row for each process its agent started (user request, 2026-10-27): the command, in gray, with when it started, how long ago, and its memory and CPU use in the tab rows' columns. A command the agent left running in the background has a yellow light, as in R26.2.
- R24. Hovering a tab row shows more: the last prompt, the last message (long text shortened), the current tool, the time in the current state, the working folder. **Dropped (user decision, 2026-10-27).**
- R25. An optional AI-written one-line summary per agent session, off by default (later version).

### Monitors

- R31. With 2 or more monitors connected, the list adds a top level: one collapsible group per monitor containing the windows on it, ordered left to right by physical position.
- R32. Dragging a window row into another monitor's group moves the window to that monitor.
- R33. The groups update when monitors are connected or disconnected, or windows are moved by hand.

### Settings

- R34. A settings button in the top bar of the dropdown, the sidebar and the list window (at the bottom until 2026-10-26, user request).
- R35. Settings include:
  - how the list is shown, "Show as: Window, Sidebar, Popup" (a regular window, the sidebar at the screen's edge, or the dropdown under the menu bar icon; user request, 2026-10-19), and for the window "Always on top";
  - the appearance: Auto (macOS's Light or Dark setting), Dark or Light (user request, 2026-10-27);
  - the sidebar's mode, edge and width (width also adjustable by dragging the edge of the sidebar);
  - which information columns tab rows show: Status, Time Since Last Status Change ("State" and "State Duration" until 2026-10-26, user request), Tokens Used (the context's size and its share of the context window, "134k (13%)"; user requests, 2026-10-26; the share in parentheses turns amber from 50% and red from 75%, user request, 2026-10-27), Created Time, Time Since Created, Memory Usage, CPU Usage (the agent and every process below it);
  - which harnesses are included (Claude Code, Codex): a tool left out isn't listed or offered for new sessions;
  - the list's sort order (Manual, Activity, Alphabetically, Tokens (by the share of the context window in use), Created Time, Memory Usage, CPU Usage, Status Change), also chosen with the Sort pop-up above the list, or by clicking a column's title: first click sorts, second reverses, third goes back to Manual (user requests, 2026-10-26);
  - whether idle tabs are hidden, chosen with the Hide Idle / Show Idle button at the right of the top bar (user request, 2026-10-26); a window whose tabs are all idle is hidden with them (user request, 2026-10-21);
  - which agent tools to detect (Claude Code, Codex), and the launch command for each;
  - how long a crashed agent session's row stays (default 5 minutes);
  - the importance rules (R26);
  - Codex usage bars on or off, and how often they are re-read (default 1 minute);
  - starting at login.

### Visual style

- R36. The list follows the look of pyinstrument's console output (https://github.com/joerick/pyinstrument): a minimal, text-first tree with no cards or boxes. Status dots and background-process lights (R26.1, R26.2) are the only graphic elements; otherwise text and color do the work.
- R37. Levels are joined by thin tree connectors (`├─`, `└─`, `│`), as in pyinstrument, in a monospaced font.
- R38. Each row reads left to right: the status dot, then the name in normal text, then secondary details in gray. Example (glyphs only illustrate; the final dots are drawn in color):
  ```
  LG UltraFine
  ├─ ● SpliceAI-lookup        1 asking, 1 working
  │  ├─ ● update README       Claude Code  asking 2m  permission: Bash git push
  │  └─ ● refactor scorer     Codex  working 4m  62% ctx  Bash: python3 -m unittest
  └─ ○ assistant              [2 tabs hidden] idle
  ```
- R39. Color and font weight follow R26 and R26.1. Secondary details (agent tool, times, context fill, previews, summaries of collapsed windows) are gray. Working and idle always look clearly different.
- R40. A collapsed window shows a gray one-line summary in place of its tabs, like pyinstrument's "[33 frames hidden]".
- R41. The tab in front is marked by a highlighted name (like pyinstrument's highlighted frames) rather than by extra icons.
- R42. Menu item titles are in sentence case: "Kill session", "Pause 3 sessions", "Talk to each other…" (user request, 2026-10-27).
- R43. Resting the pointer on a row's status dot, a process row's dot or a tab's background task lights shows what they mean, for example "Done: the agent is done and you haven't looked at its last message yet" (user requests, 2026-10-27). The rest of the row shows nothing on hover.

## Known gaps for Codex

Reported or observed on 2026-10-18; Phase 0 verifies them.

- Codex does not send the terminal status signal (OSC 7501), so its status depends on hooks or screen activity.
- Another project (CodeStatus, https://github.com/henriquegpb/codestatus) reports that Codex hooks have no event for "asking a question" and that a hook sees only the first tool use in each turn. "Asking", previews and the importance rules may therefore be less precise for Codex than for Claude Code.
- Codex has no messaging between sessions (relevant to introductions, below).

## Cost and risk of each feature

My rough ratings. *Code* is the amount, *Complexity* how hard it is to get right, *Brittleness* how likely it is to break when iTerm2, Claude Code or Codex change, *Overhead* the CPU, memory and disk it costs while running.

| Feature | Code | Complexity | Brittleness | Overhead | Notes |
|---|---|---|---|---|---|
| Menu bar icon and dropdown list (R1 to R3, R7 to R9) | Medium | Medium | Low | Low | Core. |
| Codex usage bars (R3.1; dropped 2026-10-27) | Small | Low | **High** | Low | Reads Codex's internal session file format, which can change in any release. Worth it only if a broken bar is acceptable now and then. |
| Auto-hide sidebar (R4 to R6) | Medium | Medium | Low | Low | Edge detection, slide in and out, never stealing focus, every desktop, drag to resize. |
| Default names (R9.1, R15.0) | Small | Low | Medium | Low | Claude Code's session names come from an undocumented file; falls back to the folder name. |
| Startup order by last prompt (R11; dropped 2026-10-27) | Small | Medium | Medium | Low | Reads each agent tool's history files once at startup (internal formats). Falls back to window order. |
| Window drag reorder (R12) | Small | Low | Low | None | Changes only the app's list. |
| New agent session in a tab or new window (R13, R13.1) | Small | Low | Low | None | |
| Exited rows (R14.1) | Small | Medium | Medium | None | Telling a normal exit from a crash relies on the agent's session-end hook; without hooks, every exit looks like a crash, so only agents whose hooks reported get an exited row (built 2026-10-27). |
| Context window fill (R15) | Medium | Medium | **Medium to High** | Low | Codex's session files include it. For Claude Code, without the status line, it has to be read from the transcript file (internal format) and needs each model's context size. |
| /compact, /clear and Interrupt (R15.1, R15.2) | Small | Low | Medium | None | Safe only as long as status detection is right; the at-the-prompt rule limits the damage. |
| Rename and focus (R10, R16, R17) | Small | Low | Low | None | |
| Drag tabs between windows (R18) | Medium | **High** | Medium | None | Hardest UI piece: drag and drop in the outline list, mapping visible rows to iTerm2's full tab order, re-reading iTerm2 afterwards. iTerm2's tab move is tested first. |
| Status (R20 to R22) | Medium to Large | **High** | Medium | Low | Combines hook events, iTerm2's status and screen activity. |
| "Stuck" detection (R20) | Small | Medium | Low | **Can be high** | **Warning:** watching every screen redraw is costly (agent spinners redraw many times a second, so 20 agent sessions could mean hundreds of updates a second). Planned instead: compare a screen fingerprint every 30 seconds, which is nearly free. |
| Importance (R26; dropped 2026-10-27) | Medium | Medium | Medium | Low | Recent interaction needs iTerm2's keystroke events: the app sees every keystroke in iTerm2 but keeps only the time. The activity rules are guesses and will need tuning; they depend on hook payloads (weaker for Codex). |
| Background-process lights (R26.2) | Small | Low | Medium | Low | Built 2026-10-27 from Claude Code's own list, which its Stop and SubagentStop hook events carry, and otherwise from the processes below the agent. iTerm2's count (`background_tasks`) comes from its Claude Code integration's hooks only. |
| Previews (R23, R24) | Medium | Medium | Medium | Low | R23 is built from the conversation files, without hooks (2026-10-19); the files' formats are internal and can change. R24 was dropped (2026-10-27). |
| Monitors (R31 to R33) | Small to Medium | Low to Medium | Low | Low | Window positions checked every 2 seconds only while 2 or more monitors are attached. Cheaper than it sounds. |
| Visual style (R36 to R41) | Medium | Medium | Low | Low | Custom-drawn rows with tree connectors and animated dots. |
| Later: core and views boundary for other views and computers | Small now | Low now | Low | None | Version 1 keeps all state in plain snapshots and all actions as plain requests, so later views can plug in. No network code in version 1. |
| Later: phone app or web page (view and control) | Medium | Medium | Low | Low | Needs the same authentication and permissions as the shared view below once it is reachable from other devices. |
| Later: introductions between agent sessions | Small for Claude Code; Medium to Large for Codex | Medium | Medium | None | Claude Code sessions can already message each other; Codex needs a relay this app would provide. |
| Later: shared view across computers | **Large** | **High** | Medium | Medium | **Warning:** discovery on the network, pairing and authentication, encryption, and syncing state between machines, plus the security exposure of a network service that can type into terminals. About as big as version 1's core. May need IT approval on a work-managed computer. |

## Designed for later

Not built in version 1, but version 1 must not block them.

- L1. **Introductions.** Right-click a window: "Introduce agent sessions". Each agent session in that window is told which other agent sessions share the window (name, agent tool, folder, what it's doing) and how to message them, so they can coordinate. An optional setting does this automatically when a window gains or loses an agent session. Introductions type into agent sessions, so they follow R15.1's at-the-prompt rule. Built on request as Talk to Each Other (R15.4); the automatic introductions were dropped (user decision, 2026-10-27).
- L2. **Several computers.** Dashboards on computers on the same network find each other and, after pairing with proper authentication and encryption, show one shared view with a computer level on top (computer 1 > monitor 1 > window 1 > tab 1; computer 2 > window 1 > tab 2). Agent sessions on another computer can be controlled (focus, rename, /compact, /clear, interrupt, new agent session, introductions), but tabs and windows can't be moved or dragged between computers. Off by default.
- L3. **Phone app or web page** as another view and control interface over the same state and actions.
- L4. AI-written summaries (R25). (AI judgment of work importance went with R26, dropped on 2026-10-27.)
- L5. A sidebar that stays visible and keeps windows out of its space.
- L6. Split panes as a sublist under each tab.

## Not in version 1

- Notifications (iTerm2 and Claude Code already send their own for permission prompts).
- Everything in "Designed for later".
- Support for iTerm2 tab groups.
- Terminals other than iTerm2.

## Decisions

All answered or approved on 2026-10-18.

- Q1. "Most recently typed into" means the last prompt sent to any agent session in the window, falling back to front-to-back window order (R11).
- Q2. A window that appears while the app is running goes to the top of the list (R12).
- Q3. A manually arranged window order is not kept across restarts (R12).
- Q4. Monitor groups are ordered left to right by physical position (R31).
- Q6. The default stuck time limit is 10 minutes (R20).
- Q7. The installer may add hook entries to Claude Code's and Codex's settings files, through an explicit `install-hooks` command that shows the change first, makes a backup, adds entries alongside existing hooks, and can remove them again. Without hooks, Claude Code still gets status from iTerm2, but there are no previews, and Codex gets only working or idle from screen activity.
- Q8. Version 1 includes global keyboard shortcuts (open the dropdown; jump to the next agent session that's asking) and type-to-filter with keyboard navigation in the list. Allow and Deny buttons for permission prompts, and a user-defined command list in the right-click menu, are left for later.
- Q10. Work importance (R26) is judged by local activity rules, not by AI, in version 1.
- Q11. Status dot shapes and colors are as in R26.1.

## Open questions

- Q9. License. The `iterm2` package is GPL-2.0-or-later. Our own files could be MIT (compatible with the GPL), but a bundled download that includes the `iterm2` package must be distributed under GPL terms. GPL-2.0-or-later for the whole project avoids that split. This is not legal advice.
