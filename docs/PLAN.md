# AgentCommandTab: implementation plan

2026-10-18. Matches [REQUIREMENTS.md](REQUIREMENTS.md) as approved on 2026-10-18. Reviewed with `/code-review-until-done` for 6 rounds (96 consensus findings fixed; stopped by decision after round 6, with the remaining open questions moved into Phase 0). Approved 2026-10-18; Phase 0 in progress. Requirement IDs (R1, C6, Q7, ...) refer to that document.

## 1. Summary

- One Python process. AppKit (through PyObjC) draws the menu bar item, dropdown panel, sidebar and settings window on the main thread. A background thread runs an asyncio loop that talks to iTerm2 through the official `iterm2` package.
- The background thread turns everything it learns into an immutable snapshot: computer, monitors, windows, agent sessions, status, previews. The UI only draws snapshots, and user actions go back to the background thread as requests. iTerm2 stays the source of truth.
- Each agent tool (Claude Code, Codex, ...) is an adapter: how to recognize it in a tab, how to launch it, how to read its hook events and files.
- Status comes from iTerm2's per-session `status` variable, from agent hook events, and from cheap screen checks (per-tool patterns, and a fingerprint for "stuck"), combined by one pure function that lets the newest signal win.
- The list is drawn in the style of pyinstrument's console output (R36 to R41).
- The code is split into a **core** (state, adapters, iTerm2) and **views** (the menu bar and sidebar UI) that talk only through snapshots and serializable action requests. Version 1 has one view, in the same process. The same boundary later serves a web page or phone app, and other computers running the dashboard (section 18).

**Deviations from the requirements.**
- (Approved 2026-10-18.) R8.2 (double-click a name to rename): in the dropdown, a single click on a tab focuses iTerm2 at once, which closes the popover, so a double-click can never arrive there. Tabs in the dropdown are renamed from the right-click menu. Double-click rename works everywhere in the sidebar, and on window rows in both.
- (Approved 2026-10-18.) R22 (tools without an adapter "are listed with a basic working or idle status based on screen activity"): a tool that neither reports status to iTerm2 nor has a settings entry can't be told apart from any other program, so it isn't listed. Such tools are listed once declared in settings (name and command pattern), then get the screen-activity status (section 11).
- (Added in milestone 2, for your review.) R5 (the sidebar auto-hides): the /goal also asked for a sidebar that "takes up space". macOS lets no app reserve screen space for other apps, so the setting offers three modes: hide until the pointer touches the edge (R5), always visible over other windows, and docked, where the app keeps iTerm2 windows on that monitor out of the sidebar's strip (narrowing or moving them, and giving them their old frame back when docking ends, unless you moved them meanwhile). Other apps' windows can still reach under a docked sidebar.
- (Added in milestone 2.) The sidebar's monitor is remembered by its name (NSScreen's localizedName), not by display UUID as section 8 planned; two identical monitors with the same name can't be told apart.
- (Approved 2026-10-18.) Requirements terminology ("in front" only while iTerm2 is the active app): while the dashboard itself is active (you are typing in the dropdown), the R41 highlight stays on iTerm2's last front tab instead of disappearing. This affects only the highlight; "done" still turns into "idle" only when the tab is really in front by the requirements' definition (section 6).

## 2. Existing tools

Checked on 2026-10-18. No tool covers even 5 of the 9 core features while you keep using iTerm2's own windows and tabs. The best iTerm2-based tools reach 2 full and 4 partial. herdr (found later that day, last row) covers most of them, but only for agents moved into its own panes.

| Tool | What it covers | Why it isn't a base |
|---|---|---|
| iTerm2 3.7.6 built-ins: Cockpit, Session Status tool, tab groups, menu bar count (https://iterm2.com/documentation-session-status.html) | Status per session, click to jump, Codex through hand-written hooks | No rename, no drag between windows from a list, no new agent tab, no monitor grouping, no screen-edge sidebar |
| iterm2-claude-cockpit (https://github.com/igor-grubic/iterm2-claude-cockpit), 1 star | Window, tab and pane tree in an iTerm2 side panel; reorder within one window | Its rename changes only its own label; "cross-window moves are not supported"; no license file |
| Agents sidebar (https://github.com/hex/iterm-agents-sidebar), 2 stars, MIT | Status in an iTerm2 side panel, Claude Code and Codex, stalled-session detection | Inside each iTerm2 window, not the screen edge; no rename or moves. Useful reference for stall detection. |
| Pitwall (https://github.com/vvojciech/pitwall), MIT | Hooks tagged with `ITERM_SESSION_ID`, side panel | Same limits |
| cctop (https://github.com/st0012/cctop), CC Status Bar (https://github.com/usedhonda/cc-status-bar), both MIT | Menu bar status lists, focus by terminal device | Flat lists; no rename, moves or monitors |
| cmux (https://github.com/manaflow-ai/cmux), GPL-3.0 | Workspace sidebar with agent status | A separate terminal app that replaces iTerm2 |
| iTermSidebar (https://github.com/afomi/iterm2-status-bar), MIT | Matches iTerm2 windows to monitors | Read-only, no agent status |
| AgentBar (https://github.com/michalstrnadel/AgentBar), 9 stars, MIT; found 2026-10-19 | The closest in idea: a menu bar app (and notch pill) that shows each session's status, jumps to the exact iTerm2 tab, answers permission prompts with Allow or Deny, starts new tasks and counts tokens; Claude Code, Codex and 7 more | No window and tab tree, no renaming, no drags between windows, no monitor grouping, no screen-edge sidebar. Its permission answering is worth borrowing. |
| AgentDeck (https://github.com/puritysb/AgentDeck), 267 stars, MIT; found 2026-10-19 | One local service sends agent status to a menu bar popup, phones, Stream Deck and LED displays; context percent per session | A status list, not a window organizer: no tree, renaming or moves |
| herdr (https://github.com/herdrdev/herdr), 44,670 stars, Apache-2.0; unaffiliated native client Herdr UI (https://github.com/penso/herdr-gpui) and phone and web clients | A terminal workspace manager that owns the agents' terminals: workspaces, tabs and panes; agent states (blocked, working, done, idle, unknown) rolled up in a sidebar; renaming; mouse use with right-click menus; Claude Code, Codex and 16 more agents; several machines in one window; a socket API that agents use to prompt each other; panes that keep running when the client detaches | The agents must run in herdr's own panes: herdr runs as one program inside one terminal tab, like tmux, so iTerm2's windows and tabs stop being what you organize. No menu bar item in its docs. The closest tool overall; a later adapter could list herdr's agents through its socket API. |

Useful references: iTerm2's tab-moving example (https://iterm2.com/python-api/examples/movetab.html); AiTerm's code that sets real iTerm2 tab titles (https://github.com/dhondtlaurens/aiterm/blob/main/daemon/aitermd/iterm_bridge.py); ccmanager's MIT-licensed screen-state detectors for Claude Code, Codex and others (https://github.com/kbwo/ccmanager); notes on Codex hook gaps in CodeStatus (https://github.com/henriquegpb/codestatus).

## 3. Facts already checked on this Mac

iTerm2 3.7.6, Claude Code 2.1.302, Codex 0.154.0 (0.163.0 since Phase 0), `iterm2` package 2.30, Python 3.13, PyObjC 12.1, macOS 27.

| Fact | How checked | Consequence |
|---|---|---|
| For Claude Code tabs, iTerm2's `jobName` is the version number (`2.1.302`), not `claude`; `commandLine` is `claude --dangerously-skip-permissions` | AppleScript `variable named` on live tabs | Detect agents by the first word of `commandLine`, never by `jobName` |
| iTerm2 has a per-session `status` variable (read `idle` and `working`) | Same | Subscribe to its changes instead of polling; values for asking, done and error still to be checked |
| iTerm2 3.7.6 contains a built-in function `iterm2.get_session_status(session_id: ...)` and the "Program Status Protocol (OSC 7501)" | Strings in the iTerm2 program | Backup source if the variable lacks detail |
| `Window.async_set_tabs(tabs)` accepts tabs from any window and moves them; `Tab.async_move_to_window()`; `Tab.async_set_title`; `Window.async_set_title`; `Window.async_create_tab(profile, command, index, profile_customizations, select)`; `Window.async_set_frame` | `iterm2` 2.30 source and https://iterm2.com/python-api/ | All moves, renames and new tabs go through documented calls |
| `iterm2` package is GPL-2.0-or-later | Package metadata | License decision (section 12) |
| Codex 0.154 has hooks `SessionStart`, `UserPromptSubmit`, `PreToolUse`, `PermissionRequest`, `PostToolUse`, `Stop`, `Interrupt`, `SessionEnd`, with fields `prompt`, `tool_name`, `tool_input`, `last_assistant_message`, `transcript_path`; it does not send OSC 7501 (Codex 0.163 does, see PHASE0_RESULTS.md) | Strings in the Codex program | Codex 0.154 status and previews need hooks |
| Codex session files (`~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl`) contain `token_count` events with `rate_limits.primary` and `rate_limits.secondary` (each with `used_percent`, `window_minutes`, `resets_at`; `secondary` was `null` here), plus `total_token_usage`, `last_token_usage` and `model_context_window` | Read the newest local file | Source for Codex usage bars (R3.1), one bar per non-null window, and Codex context fill (R15) |
| `claude agents --json` gives `pid`, `sessionId`, `name`, `kind`, `cwd`, `status`, `startedAt` for every live Claude Code session (field list observed, not documented) | Ran it | Maps a Claude Code process to its transcript file, and gives the session name for default tab names |
| `$ITERM_SESSION_ID` has the form `w0t1p0:<GUID>`; the GUID after the colon equals AppleScript's session `unique id` and the API's `session_id`. Each session's `tty` matches the terminal of its Claude Code process | AppleScript listing compared with `ps` and the API | Hook events are matched to tabs on the GUID only (the `w0t1p0` prefix goes stale when a tab moves) |
| The Python API connects from an outside process with no permission prompt (on this Mac, with the API already enabled) | Read-only experiment script | First run may still prompt on other Macs; handle both |
| `iterm2.get_session_status(session_id: ...)` returns JSON: `status`, `turn_open`, `detail`, `text_color`, `dot_color`, `background_tasks` | Same | Richer than the `status` variable; use it for detail, and the variable for change events |
| iTerm2's `path` variable is empty or stale for agent sessions (it showed the folder the shell was in, not the agent's folder) | Same | Take the working folder from the agent process (`claude agents --json` `cwd`, or the process's current folder), not from `path` |
| A window frame from `Window.async_get_frame()` was `(0, 0, 2056, 1290)`, the same as the monitor's usable area from `NSScreen` | Same, one monitor only | Same bottom-left coordinate system; still to be checked with 2 monitors |

## 4. Architecture

### Threads

```
main thread: AppKit event loop                 background thread: asyncio event loop
---------------------------------------        ------------------------------------------
menu bar "C" item, dropdown panel,             iTerm2 connection and subscriptions
auto-hide sidebar, settings window,            hook event listener (local socket)
shared outline list                            screen fingerprints, file readers
          ^                                                   |
          |   snapshot (immutable)  via AppHelper.callAfter   |
          +---------------------------------------------------+
          |                                                   ^
          +-- action request (rename, move, focus, send) -----+
                via asyncio.run_coroutine_threadsafe
```

- After an action, the background thread re-reads iTerm2 and publishes a new snapshot. The UI never guesses the result.
- All shared state lives on the background thread, including the window list order (R11, R12): the snapshot carries it, so the dropdown and the sidebar (and later remote views) show the same order. The main thread only holds the latest snapshot plus UI-only state (expanded rows, selection, filter text).
- The core never calls AppKit. Monitor rectangles come from `NSScreen` on the main thread, which sends a "screen layout changed" message with plain rectangles to the core at startup and on every `NSApplicationDidChangeScreenParametersNotification`.
- Snapshot deliveries to the main thread are coalesced: at most a few per second, and only the newest is drawn.

### Core and views

```
             views (version 1: menu bar + sidebar, same process)
             later: web page, phone app, other computers
                  |                          ^
   action request |                          | snapshot stream
   (JSON-ready)   v                          |
   +-----------------------------------------------------+
   | core: perform_action(request) -> result              |
   |       subscribe_to_snapshots(callback)               |
   |  adapters, status, iTerm2 connection, file readers   |
   +-----------------------------------------------------+
```

- Every user action is an `ActionRequest` data class: action name, target (`computer_id`, `window_id` or `session_id`), and arguments, for example `{"action": "rename_tab", "computer_id": "...", "session_id": "...", "new_title": "..."}`. The core checks it, runs it and returns an `ActionResult`. The menu bar UI builds requests exactly the way a remote view would.
- Each action type is registered with flags: `may_come_from_another_computer` (moves, drags and `reorder_window_list`: no; rename, focus, /compact, new agent session: yes) and `types_into_a_session` (/compact, /clear, interrupt, introductions). Later network views can only send actions whose flags allow it. Dragging a window row within its monitor group sends `reorder_window_list` with the window ID and the window it was dropped before.
- Version 1 adds only this boundary (about 150 lines), with no network code.

### Snapshot shape (designed for C6)

```
ComputerSnapshot            computer_id, computer_name, taken_at, codex_usage, hooks_status_per_agent_tool
  MonitorEntry[]            monitor_id, name, frame, visible_frame      (one entry when 1 monitor)
                            windows listed in the core's window list order
    WindowEntry[]           window_id, title, window_title_set_by_user, frame, last_prompt_time,
                            total_tab_count   (including tabs hidden because they have no agent)
      AgentSessionEntry[]   tab_id, session_id, tab_title, tab_title_set_by_user, agent_session_name,
                            agent_tool_name, working_folder,
                            status, status_reason, status_since, preview_line, details,
                            context_fill_percent, importance_level, background_task_count,
                            last_user_interaction_time, ended_abnormally_at, is_in_front,
                            peer_messaging_address   (for introductions, section 18)
```

- Plain data classes that convert to JSON. A web page, phone app or second computer would receive the same JSON. The computer level already sits at the top. Version 1 has one local computer.
- `dump-snapshot` writes the current snapshot as JSON, for tests and bug reports.

### Connecting to iTerm2

- **Access token without `NSAppleScript`.** The `iterm2` package gets its access token through AppleScript. With PyObjC's AppKit installed, it would use `NSAppleScript` on whatever thread connects, while Apple says to use that class from the main thread. Its retry after an HTTP 401 also happens inside `Connection.async_create()`, where the caller can't intercept it. So the app never lets the package choose:
  - at startup, before any connection, the app sets `iterm2.auth.gAppKitAvailable = False`. `authenticate()` reads that flag on every call, so the package then always fetches its token with `CommandLineApplescriptRunner`, which runs `osascript` as a subprocess and is safe on any thread, including in its own retry after a rejected token;
  - checked in Phase 0 (docs/PHASE0_RESULTS.md): a fresh and a stale token both connected from a background thread's asyncio loop, and the `NSAppleScript` runner was never called.
- The first run may show a macOS permission prompt. Whether it does depends on how the app was started (from a terminal inside iTerm2, or at login by launchd), which Phase 0 checks.
- If the Python API is off, the panel shows one line pointing to iTerm2 Settings > General > Magic > Enable Python API.
- **Reconnecting means restarting.** The `iterm2` package keeps process-wide state (the shared `App` object, delegate pointers, a module-level table of notification handlers) that is reset only inside `Connection.run()`, so a second `async_create()` in the same process would reuse a stale `App` bound to the dead connection. So when the connection drops (iTerm2 quit or restarted), the app process exits with a "reconnect" status. The package gives no usable signal for a drop (with `async_create()` its dispatch task just prints a traceback, and pending requests hang), so a watchdog task awaits `connection.websocket.wait_closed()` and the dispatch task's completion. When either finishes, it posts a message to the main thread, which ends the app with the reconnect status. A connect attempt that fails exits the same way. `agent-command-tab run` is a tiny supervisor that starts the app process and restarts it after a backoff (1 s, growing to 30 s) when it exits that way; the login item runs the same supervisor. Hook events arriving while the app restarts are not lost (section 6). Phase 0 tests a restart of iTerm2 with the prototype running.

### Subscriptions

| `iterm2` monitor | Used for |
|---|---|
| `NewSessionMonitor`, `SessionTerminationMonitor`, `LayoutChangeMonitor` | Tabs and windows opened, closed or moved |
| `VariableMonitor` on each first-pane session's `commandLine` | An agent session starts, or the foreground job changes. A change alone does not mean the agent exited: Claude Code's Ctrl+G opens an editor in the same terminal, and Ctrl+Z suspends the agent. The core keeps the agent PID found at detection and treats the agent as gone only when that process no longer exists; while it lives, the row keeps its state. |
| Keystroke notifications | Interaction times (R26) and the unsent-draft guard (R15.1). The `Keystroke` object in iterm2 2.30 has no session, so either one `KeystrokeMonitor(connection, session=<id>)` per agent session, created and removed with the session, or one raw subscription (`async_subscribe_to_keystroke_notification`) reading the notification's `session` field. Phase 0 picks one. Per keystroke, only these facts are kept, never the characters: the time, whether it inserts text, whether it was Enter, its modifier keys, and whether it was a deletion or clearing key (section 8). |
| `VariableMonitor` on each first-pane session's `status` | Status changes (source 2 in section 6) |
| `VariableMonitor` on tab `titleOverrideFormat` and window `titleOverrideFormat` | Names set by the user (here or with iTerm2's Edit Tab Title or Edit Window Title). The tab `title` variable is the formatted tab-bar title, which includes Claude Code's `claude`, so it can't tell a user's title from the agent's. iTerm2 rejects the identifier "all" for the tab scope, so the tab title is watched as the session-scope variable `tab.titleOverrideFormat` with "all", and the window title at window scope with "all" (both checked in milestone 1). There is no periodic full re-read. |
| `FocusMonitor` | Tab in front, combining iTerm2's app-active, key-window and selected-tab updates. Two separate facts come out of it: **seen** (the tab was selected in the key iTerm2 window while iTerm2 was the active app, per the requirements' definition; this alone drives done becoming idle), and **highlighted** (R41). While the dashboard itself is the active app (for example while you type in the dropdown's filter or rename field), the highlight stays on iTerm2's last front tab instead of disappearing, but nothing counts as seen. The core learns this from a main-thread "dashboard became active or inactive" message, since it never calls AppKit. Cost: one remembered tab ID. |
| Frame poll every 2 s, only with 2 or more monitors | Windows dragged between monitors by hand |

## 5. Finding agent sessions (R7, R14, C4)

- For each window, each tab, only the tab's first session: read `commandLine`, `jobPid` and `tty`. The working folder comes from the agent process (for Claude Code, `cwd` in `claude agents --json`; otherwise the process's current folder, looked up by `jobPid`), because iTerm2's `path` variable can be empty or stale.
- Each adapter has `matches_command_line(command_line)`: the first word's base name against a pattern (`claude`; `codex` or `node .../codex`). Users can add patterns for other tools in settings.
- Codex's `commandLine` is `node /opt/homebrew/bin/codex ...` in 0.154 and `codex ...` in 0.163 (Phase 0); the pattern accepts both.

## 6. Status (R20 to R22)

States in urgency order (R20), with their dots (R26.1):

| Urgency | State | Dot |
|---|---|---|
| 1 | asking | light blue ● (was purple; light blue everywhere since milestone 2, user request: the dot, the menu bar "C" in Auto color, and the asking count) |
| 2 | error | red ✕ |
| 3 | stuck | amber ◆, slow pulse |
| 4 | working | blinking ●, green for routine work, shifting toward blue as importance rises |
| 5 | done (tab not in front since the turn ended) | steady green ✔ |
| 6 | idle | gray ○ |
| 7 | unknown | gray dotted ◌ |
| 8 | exited after a crash (R14.1) | dim gray ⊘ |

Sources, most specific first:

1. **Hook events** (needs Q7). A tiny command, `agent-command-tab-hook`, is called by Claude Code and Codex hooks. It reads the event JSON from standard input, adds the session GUID (the part of `ITERM_SESSION_ID` after the colon) and its own timestamp, sends one line to the app's local socket under `~/Library/Application Support/agent-command-tab/`, and exits. It never prints (Claude Code feeds some hook output into the conversation) and never fails. It gives: asking (permission requested; for Claude Code, a question through `AskUserQuestion`), working (prompt submitted, tool use), done (turn finished), error (Claude Code's `StopFailure`), and for Codex, turn ended by the user (its `Interrupt` hook, registered for this reason: idle, with open requests closed).
   - **Only the tab's own agent counts.** Agent processes started inside a tab's agent (for example `claude -p` or `codex exec` run by a shell tool, as review and agent-runner workflows do) inherit the tab's `ITERM_SESSION_ID` and fire the same user-level hooks. The forwarder therefore also sends its parent process ID and the payload's `session_id`. The core accepts an event as the tab's own only if its `session_id` belongs to the tab's foreground agent: the agent PID recorded at detection, matched through `claude agents --json` (PID to session ID) or the forwarder's parent PID. Hook state is keyed by (session GUID, agent `session_id`, `agent_id`). Events from other agent sessions in the same tab count at most as background-process lights, never as the row's state. Fixtures include a nested `claude -p` run finishing while the tab's agent keeps working.
   - **Cost and never stalling the agent:**
     - By default Claude Code waits for every command hook to finish. Since the dashboard never returns a decision, the installer registers its entries with `"async": true` and a short `timeout` (Codex accepts the same two fields, Phase 0).
     - The forwarder keeps only the fields the adapters use, truncates long strings to about 2,000 characters, and drops `tool_response` and full file contents from `tool_input`. Payloads can otherwise be large (a `Write` call carries the whole file), and a macOS local socket buffers only about 8 KB, so a large send could block.
     - One overall deadline (about 100 ms) covers connect plus send (`socket.settimeout`); on timeout the event is dropped.
     - It imports only the standard library (never AppKit or `iterm2`) and is registered only for the events the adapters use.
     - Phase 0 measures its time per event; the target is under 30 ms.
   - **Surviving app restarts, with bounded disk use:**
     - The app keeps the reduced hook state per (session GUID, `agent_id`) (section 6, "Combining the sources") and, after each change, writes it as one checkpoint file per session (written to a temporary name, then renamed). The checkpoint holds every open permission request and question with its preview fields, so nothing open is lost by keeping only the latest checkpoint.
     - The forwarder writes an event to disk only when it cannot reach the app (the app is not running or is restarting). Each such event goes to its own uniquely named file (`<session GUID>.<agent_id>.<timestamp>.<random>.json`), so concurrent forwarders never overwrite each other.
     - On startup the app loads the checkpoints, replays the pending event files newer than each checkpoint in timestamp order through the same rules as live events, writes fresh checkpoints, and deletes the replayed event files. Restarting twice in a row gives the same result.
     - Cleanup: a session's checkpoint is deleted when its tab closes or its agent ends; at startup, checkpoints whose session GUID matches no live iTerm2 session are deleted; pending event files older than 3 days are deleted; the folder is capped at 20 MB (oldest pending event files deleted first).
     - So disk use is one small checkpoint per live agent session (a few KB), plus pending events only while the app is not running.
     - Sessions with no hook event since the app started also get the screen-pattern check (source 3).
2. **iTerm2's `status` variable.** For Claude Code it is fed by iTerm2's Claude Code integration, which is itself a set of user-level hooks (`~/.config/iterm2/cc-status`, which runs `it2 set-status`); Claude Code sends no status codes of its own (Phase 0). So it works without the dashboard's hooks only where that integration is installed, and it shares the hooks' gaps: it stays `waiting` after a permission prompt is approved until the tool finishes, and agents nested in the tab (a `claude -p` run by the tab's agent) rewrite it. The core therefore ignores it for a tab while the tab's own agent has a tool call open, or while another agent session in the same tab is active (its hook events have another `session_id`, from its `SessionStart` until its `SessionEnd`). Codex 0.163 sets it itself through OSC 7501 (`idle`, `working`, `waiting` while its approval menu is shown, unset after exit), so for Codex it is accurate around approvals and is the main source; Codex 0.154 sent nothing. Two escape codes may feed it: OSC 7501 (the cross-terminal Program Status Protocol, with fixed values such as idle, working, waiting, done, failed) and OSC 21337 (iTerm2's own status code, whose text is free, for example "Working", and which iTerm2 triggers and its `it2` tool can also set). The variable is not in iTerm2's documentation. So the adapter maps only known words, case-insensitively (idle, working, waiting, done, failed or error, and iTerm2's Working, Waiting and Idle), and treats any other text as no signal. Phase 0 records what each code and a trigger actually put in the variable. The mapping is a dictionary lookup per status change, so the cost is negligible.
3. **Screen patterns** (working or idle fallback, for tools with neither hooks nor OSC 7501, such as Codex before 0.163 without hooks). Every 3 seconds, read the bottom lines of the first pane and match the adapter's `working_screen_pattern` (for example a spinner or "esc to interrupt") and `prompt_screen_pattern`. Each adapter must define these; a generic default ("screen changed in the last few seconds") covers unknown tools. **How the screen is read:** all screen reads (this 3-second check, the 30-second fingerprint and the empty-prompt check) make one `Session.async_get_screen_contents()` call, which returns the visible screen only (typically 40 to 80 lines), and take the lines they need in Python. No `iterm2.Transaction` is used: a transaction locks iTerm2 until it ends, and the line-range call (`async_get_contents`) fetches style data anyway, so it would save nothing. This fallback also applies to sessions that have hooks but no iTerm2 status (Codex before 0.163): a screen reading of the empty prompt overrides a stale hook "working" once the hook state is older than the reading.
4. **Screen fingerprint** (stuck signal only). Every 30 seconds, hash the last 20 visible lines after removing the lines matched by the adapter's `progress_noise_pattern`: the spinner, elapsed-time and token counters that change every second even when nothing happens. Without that masking the hash always changes and "stuck" never fires; masking too much hides real progress. Phase 0 recorded screens every 3 s during a 45-second command; the parts that changed with no progress were, for Claude Code, the spinner glyph (`✳ ✶ ✢ ✻`), the elapsed and token counts in `Thinking… (5s · ↓ 97 tokens)`, the ` · 4s` suffix of the tool line, the `(4s)` suffix of the command line, and the tool line's `⏺` bullet, which blinks; for Codex, the elapsed time in `Working (5s • esc to interrupt)` and the braille-dot idle animation (U+2800 to U+28FF) around its input box. These become the fixtures for `progress_noise_pattern`. Never subscribe to every redraw: agent spinners redraw many times a second.
5. **Derived**: stuck = thinking with no fingerprint change and no hook event for the threshold (default 10 minutes, Q6; settings label "Agent is stuck if it thinks for more than: [10] minutes"). An agent waiting on a command it started is not thinking (user request, milestone 2): every 2 seconds the core lists the working agent's descendant processes (libproc, no subprocess; `agent_process_tree.py`), and one that started during the current turn is the command it waits on, except: the tool's own helpers by name (Claude Code's `caffeinate`, Codex's `codex-code-mode-host`) and processes that were running while the session was idle (recorded every 30 seconds between turns: MCP servers, from the second turn on), with everything below them; and a shell with nothing running in it (Codex keeps a terminal open between commands). A command counts only after it has run for 2 seconds, so hooks (iTerm2's `cc-status` runs on every tool call) and status line commands never show. Known limit: an MCP server that Codex starts at the first prompt counts as a command during that first turn. The row then shows "running" with the command's own elapsed time and is never stuck; the state's reason names the command ("running python3", named below the shell it runs in, by the name it was started as, so a nested Claude Code is "claude" rather than its executable's version-number name), in the snapshot only, since rows have no tooltips (user request, 2026-10-19). The stuck clock restarts when the command ends. A turn starts when the status turns working from anything but working or asking; for a session first seen mid-turn, processes started 30 seconds after the agent count.

**Combining the sources.**
- Two pure functions:
  - `apply_hook_event_to_session_hook_state(session_hook_state, event)` returns the new hook state for one session: per `agent_id`, its latest activity, its open permission requests and questions (with preview fields), and its last turn times. It implements the open-request rules below. This is also what the checkpoint files store.
  - `compute_session_status_from_sources(session_hook_state, iterm2_status_value, iterm2_status_change_time, screen_pattern_state, last_fingerprint_change_time, last_turn_end_time, last_time_tab_was_seen, tab_is_highlighted, now, settings)` returns `(state, reason, since)`, combining the agents' states with the other sources.

  Both are tested exhaustively with event sequences as fixtures.
- **Freshness:** the newest signal wins. A hook event outranks iTerm2's `status` only if it is newer than the last `status` change. This handles interrupts: Claude Code sends no Stop event when the user presses Esc, so after an interrupt (from the dashboard or typed in iTerm2) iTerm2's newer `idle` replaces the stale "working" hook event. For Codex, its `Interrupt` hook (sent for Esc and for "No" at its approval menu) and, from 0.163, its own `idle` status do the same; without either, the screen's empty-prompt reading does. An interrupt sent by the dashboard changes nothing by itself: the row stays working until one of these signals confirms the turn stopped (section 8).
- **Done and seen:** the core keeps, per session, the time the last turn ended and the last time its tab was seen (selected in the key iTerm2 window while iTerm2 was active, section 4). Done means the turn ended after the tab was last seen; a turn that ends while the tab is being seen goes straight to idle. A turn ends when iTerm2's status changes to done, or to idle from working or waiting (`status_change_ends_a_turn`). That time is kept when the status changes again, so done lasts until the tab is seen even when the status later says idle, and Codex, which reports only idle when its turn ends, gets done too (user request, 2026-10-19). A session already idle when the app starts shows idle, since when its turn ended is unknown. The highlight kept while the dashboard is active never counts as seen, so a turn that finishes while you type in the dropdown stays done.
- **A Stop event is provisional.** Another installed Stop hook can block stopping and keep the turn going (Claude Code's goal feature works this way). Done counts as "at the prompt" for typing commands (R15.1) only once iTerm2's `status` (or the screen prompt pattern) also says idle or done, with no newer hook event.
- **Several agents in one session:** background subagents fire the same tool hooks as the main agent, told apart by `agent_id`. The core keeps hook state per `agent_id`.
- **Open requests are tracked one by one.** Each agent keeps a list of its open permission requests and questions, separate from its other tool activity. Claude Code's `PermissionRequest` input has no `tool_use_id`, so a request is matched to its tool call by tool name and input. Each event from an async forwarder is stamped when that forwarder starts, so the `PreToolUse` that comes just before a `PermissionRequest` can arrive after it; arrival order is never used to decide which came first.
- **When an open request ends (this overrides "newest wins" for asking):** a request ends on any of:
  - `PostToolUse` or `PostToolUseFailure` for the same tool name and input;
  - `UserPromptSubmit`, `Stop` or `StopFailure` from the same agent;
  - a newer iTerm2 `status` meaning idle (when not ignored, see source 2);
  - the screen showed the adapter's `permission_dialog_screen_pattern` (for Claude Code 2.1.303, a `Do you want to ...?` line together with `Esc to cancel`) and no longer does, or a new `Interrupted` line appeared since the request opened (a "No" or Esc). A dialog that was never seen (wording the pattern doesn't know, such as the plan approval question) keeps the request open. The core reads the screen as soon as the session starts asking, so the count of `Interrupted` lines from before the user could answer is known, then 300 ms after each keystroke in that session and every 2 seconds while the request is open, one screen read each time, so the cost is limited to sessions that are asking. The dialog is searched in the last 30 non-blank lines, since a fresh or cleared tab leaves blank rows below it.

  It never ends because of a `PreToolUse` for the same tool call (whichever order they arrive in), another tool call of the same agent finishing (agents run tools in parallel), a newer iTerm2 `working`, or activity from another agent. When the match is ambiguous, the request stays open. Phase 0 showed why the screen check is needed: Claude Code sends no event when the user approves (the next event is the tool's `PostToolUse`, when the tool ends), denies (choosing "No" ends the turn with `Interrupted · What should Claude do instead?`), or presses Esc, and iTerm2's status stays `waiting` after an approval. After the dialog goes away, the agent counts as working until the next signal (an approved tool runs; a denial or Esc is followed by iTerm2's `idle` about 3 seconds later, or the screen's empty-prompt reading). **Codex differs:** its own status says `waiting` exactly while its approval menu is shown, and both Esc and "No" send its `Interrupt` hook, so no screen check is needed for Codex 0.163. But a Codex `PermissionRequest` hook alone does not open a request: with `approvals_reviewer = "auto_review"` (an automatic reviewer model, set on this Mac) the hook fires, no one is asked, and the status stays `working`. A Codex request opens only when its status is `waiting` (or, for Codex without status, when the screen shows `Would you like to run`). Fixtures cover approval during a long tool call, Esc at a permission prompt, a manual denial, a `PermissionRequest` that arrives before its `PreToolUse`, and a parallel tool call finishing while a request is open. The row shows the most urgent state across the session's agents, and background subagents also count as background-process lights (R26.2). Fixtures include a permission request in the main agent while a subagent keeps running tools.
- Window rows take the most urgent state of their agent sessions plus counts in urgency order ("1 asking, 1 working").

**Ending (R14.1).**
- A tab or window that the user closes (it leaves the layout, reported by `LayoutChangeMonitor`, or iTerm2 reports the session terminated) is removed from the list at once. iTerm2 delays the termination event while a close can still be undone, so the layout change is the trigger.
- When the agent's process no longer exists (checked by its PID, not by a `commandLine` change, see section 4) in a tab that stays open, the app waits a 2-second grace period, because the async session-end hook can arrive after iTerm2 reports the change. A session-end event with a normal reason (for example the user exiting the prompt, or Codex's `SessionEnd`) within that time, or arriving later, removes the row; a later one also removes an "exited" row already shown. No event, or an error reason, counts as a crash: the row stays gray and "exited" for the configured time. In Phase 0 the async `SessionEnd` arrived before the process was gone for both tools: Claude Code about 1 s before (reason `prompt_input_exit`, or `other` when `/exit` was typed during a running turn), Codex 5 to 7 s before (reason `other`, while it shut down its MCP servers). All three reasons count as normal. So a normal `SessionEnd` from the tab's own agent removes the row as soon as it arrives (except Claude Code's reason `clear`, which `/clear` sends just before starting a new session in the same process, section 8), and detection skips that agent PID while it shuts down (otherwise its unchanged `commandLine` would bring the row back); the process check and grace period only decide the cases with no event.
- Without hooks, every agent exit in an open tab looks like a crash, so the README recommends installing hooks.

### Importance (R26)

A pure function, `compute_session_importance(state, last_user_interaction_time, recent_activity_summary, now, importance_rules)`, returns a level from 0 (dim) to 3 (brightest and bold):

- asking adds the most;
- user interaction in the last 2 minutes adds one level. Interaction means keystrokes in that session (keystroke notifications, attributed per session as in the subscription table; the app keeps only the time, never the keys), bringing its tab to the front, or a prompt-submitted hook event;
- work importance adds up to one level, from the turn's activity summary built from hook events:
  - number of distinct files edited;
  - edits to source files versus docs or tests (by file extension and path patterns);
  - turn length;
  - number of tool calls.

  The thresholds and patterns live in settings (`importance_rules`) with sensible defaults.

The level sets the row's brightness and font weight. For working sessions it also sets the dot's hue between green (routine) and blue (important). A window row uses its most important agent session's level.

**Background-process lights (R26.2).** The count comes from `background_tasks` in `iterm2.get_session_status`, called on every `status` change and every hook event for that session, plus every 10 seconds for sessions whose count is not zero (a background shell can start or finish without any status change). Background subagents known from hooks are added only if Phase 0 shows that `background_tasks` doesn't already count them, so nothing is counted twice. Claude Code's `Stop` hook also lists the tasks, so the hover panel can name them. Up to 4 small lights follow the dot; beyond that, a number.

## 7. Previews, context fill and usage (R15, R23, R24, R3.1)

**Previews.** A pure function, `choose_preview_text` (`session_preview_computation.py`), picks in order: what it is asking (tool name and short input, or the question text), the tool it is running ("Bash: python3 -m unittest"), the start of what it said since the last prompt, the last prompt ("You: ..."). The hover panel shows the last prompt, the last message (shortened to about 2,000 characters), the current tool, the time in the state and the working folder. Sources are the hook fields listed in section 3. Without hooks: the last non-blank screen lines with box-drawing characters removed.

As built first (user request, 2026-10-19, before hooks): the preview comes from the end of the session's conversation file, read every 5 seconds together with the context size and only when the file changed (1 to 3 ms per read). For Claude Code, records are read newest first back to the last prompt; a tool_use with no tool_result yet and no text after it is the open tool call; Claude Code's own user records (command echoes, notifications, meta records, the summary after /compact) are not prompts, and its "last-prompt" record stands in for a prompt older than the part read. For Codex 0.163, prompts and replies are `item_completed` events with UserMessage and AgentMessage items (earlier versions: `user_message` and `agent_message` events), and a tool call with a command and no output record yet is the open one; Codex's polling calls (`write_stdin`, `wait`) are skipped. A reply's first line that holds only a time (some users have every reply start with one) is dropped. The open tool call shows only while the session is working, stuck or asking. The preview is one more level that opens and closes, closed at first (user requests, 2026-10-19): the tab row's arrow opens or closes it, two gray lines under the tab row starting under the name, with the tree's lines carried down beside them. The top bar's expand or collapse button leaves the previews as they are; an expanded window row with previews ends in one button that opens all of its tabs' previews, or closes them once all are open (its icon shows the current state, like the top bar's). A tab row without a preview has nothing to open. The dropdown's height counts only the open previews.

**Context fill (R15).**
- Codex: the newest `token_count` event in that session's file. Fill = `last_token_usage.total_tokens` / `model_context_window`. Not `total_token_usage`, which is a running sum over the whole session (79,399 versus 29,106 in the local file checked). The same blank-if-over-100% rule as for Claude Code applies, and a multi-turn fixture where the sum exceeds the window guards it.
- Claude Code: the app can't use the status line (R3.1), so it reads the session's transcript. Its path comes first from the hook's `transcript_path`; without hooks, from `<config folder>/projects/<folder>/<sessionId>.jsonl`, trying each configured Claude Code config folder (section 12). The last assistant message's token counts (input plus cache read plus cache creation) give the used size. The context size depends on the model: the adapter reads the model ID from the hook payload's `model` field or the transcript's assistant entries, and looks up its size in a small table that the user can override. A session can use a long-context variant of the same model (for example 1M instead of 200k tokens), and Phase 0 checks where that shows up. If the model or its size is unknown, or the used tokens exceed the assumed size, the app shows blank. Read only the file's tail, and only when the file changed.
- Both are internal formats, so readers fail quietly (blank instead of a wrong number) and are covered by fixture tests. Codex 0.154 already calls its rollout files "legacy local sessions" and ships `codex migrate-rollouts` to move them to a new "paginated thread history", so the Codex readers (usage, context fill, startup order) may stop finding data after a Codex update or migration. Phase 0 checks whether new sessions still write rollout files and where `token_count` and `rate_limits` live after migration.

**When `claude agents --json` runs.** It is a large program (the installed Claude Code file is about 268 MB), so it is not polled freely. Claude Code keeps its live-session list inside each config folder (`<config folder>/sessions/<pid>.json`), so each run is done once per configured config folder, with `CLAUDE_CONFIG_DIR` set to that folder, and the results are merged by process ID (Phase 0 confirms that the output depends on the folder). Each run is an asyncio subprocess with a 5-second timeout, started:
- when a new Claude Code session is detected;
- right after the app sends /clear;
- on every `SessionStart` hook event (registered for this reason), because /clear and /resume start a new session ID inside the same process;
- otherwise every 60 seconds, and only while Claude Code sessions without hooks exist.

A session whose ID may be stale (between a /clear and the next run) shows a blank context fill rather than the old transcript's number.

**File reading off the event loop.** All file reading (transcripts, Codex session files, saved hook state) runs in worker threads (`asyncio.to_thread`), so a large file never blocks the iTerm2 connection or the hook listener. Transcripts can be large (81 Claude Code transcripts on this Mac exceed 10 MB, the largest about 50 MB), so readers start from the end: the context size and turn start come from the last 512 KB of the conversation file (`context_window_usage.read_last_lines`), and the startup-order reader reads backward in chunks until it finds what it needs. The app publishes its first snapshot before startup ordering finishes and re-sorts once (R11) when it does.

**Codex usage bars (R3.1).** Once a minute (a setting), look at recently changed Codex session files and take the `token_count` event with the newest recorded timestamp that has `rate_limits`. A file can be the newest by modification time yet have no usage numbers (for example a session that just started), so file order is not used. The last valid reading is kept when newer files have none. The reader remembers each file's last read position, so each pass reads only new lines. Draw one bar per non-null limit window under `rate_limits` (`primary`, `secondary`), labeled from its `window_minutes` (for example "5 h" or "week"), with percent used and reset time, for example `codex week ███████░░░ 68%  resets Thu 9:00`. Fixtures cover one window and two, and files whose modification order differs from their event order.

## 8. User interface

### Shared list

There are two instances of the same view controller class, each owning its own `NSOutlineView` (AppKit's tree list): one in the dropdown panel (`NSPopover` under the menu bar item) and one in the sidebar (`NSPanel`). An AppKit view can only be in one window at a time. Both instances share the row-drawing code and receive the same snapshots, and each keeps its own expanded rows and selection.

**Applying snapshots without breaking editing or dragging.** NSOutlineView tracks expansion by item object identity and rebuilds row views on reload, so rebuilding from every new snapshot would collapse rows and cancel a rename or a drag. Instead:
- each controller keeps one item object per monitor, window and session ID for as long as that ID exists;
- a new snapshot whose rows are the same as before (same items, same order) only replaces the items' entries and redraws the visible rows. When rows are added, removed or moved, the outline view is reloaded, and each item's expanded or collapsed state is then set again from the controller's own record, parents before children (`reloadData` keeps the expansion of item objects it already knows, and expanding a row whose parent is collapsed does nothing). Because the item objects survive, so does the selection. The reload also redraws every neighbor whose tree connector changed (closing a window's last tab turns the previous tab's `├─` into `└─`). This is simpler than inserting and removing individual rows, and it only happens when tabs or windows open, close or move, so the cost is negligible (implemented in milestone 1);
- while an in-row rename editor or a drag is active, incoming snapshots are held, and the newest is applied when the edit or drag ends. The filter field is outside the rows, so it never holds updates: each snapshot is applied with the current filter text;
- the elapsed-time text is refreshed by a 0.6-second timer (it also blinks working dots) that refills only the visible rows whose look changed: each row view remembers what it shows, and an unchanged row is neither refilled nor laid out again. A list that is not on screen (the closed dropdown, the hidden sidebar) skips redraws and is brought up to date just before it appears.
- **Speed (user request, milestone 2: common actions must feel instant with 20 windows and 100 agent sessions).** Measured off-screen with that many rows: one expand or collapse 7 ms, a refresh tick 7 ms, a new snapshot 26 ms, expand all or collapse all 40 to 60 ms (row by row, not through `reloadData`), fitting the dropdown 9 ms. Clicks toggle at once, without the outline view's slide animation. The core reads all windows and tabs at the same time, with one request per session, tab and window for all their variables (`read_iterm2_variables`), so a full read took 2.5 ms for 9 tabs (it was 16 ms, one request per variable, one tab after another), about 25 ms for 100. A confirmed rename shows the new name at once; the next snapshot confirms it.

**Drawing in pyinstrument's style (R36 to R41).**
- The outline view's own indentation and disclosure triangles are turned off. Each row is a small view whose parts read left to right as in R38: a text field with the tree connector prefix (gray; in a monospaced font its width depends only on the depth), the status dot (a tiny custom view, so it can blink or pulse, R26.1), the background-process lights (R26.2), a text field with the name (brightness and weight from importance), and a text field with the details (gray: a collapsed window's state counts); a tab row with a preview has it on a second gray line (section 7). At the right edge of a tab row, columns line up from row to row (user requests, milestone 2): the context's size in tokens ("425k", right-aligned), the agent tool's icon, the state (left-aligned) and the elapsed time (right-aligned). The list sets every column's width to fit its widest text; the icon column is shown only when two or more agent tools are running, and the context column only while some session's size is known. The context size comes from the end of the session's own conversation file every 5 seconds, read again only when the file changed; the file is looked up again for each new process or Claude Code conversation and every minute (Codex's /new starts a new file in the same process), and the size is cleared while no file is found. Claude Code's error replies (model `<synthetic>`, all-zero usage) are skipped (`context_window_usage.py`): for Claude Code the latest reply's input, cache-write and cache-read tokens in its transcript (found by the `sessionId` from `claude agents --json` in `~/.claude*/projects/*/`), the number its status line shows as total_input_tokens; for Codex `last_token_usage.total_tokens` of the latest `token_count` event in its conversation file. Codex 0.163 doesn't keep that file open (a live check found nothing with lsof), so it is found by scanning `$CODEX_HOME/sessions/YYYY/MM/DD/` from the process's start to today: the most recently written file whose first line (session_meta) names the process's folder and a start no earlier than the process's (`find_codex_conversation_file`; two Codex sessions in one folder can both get the newer file). Icons are drawn from pixel grids in code (`agent_tool_icons.py`: Claude Code's four-legged mascot, Codex's ">_"), so no logo files ship; a tool without one gets its name drawn instead. Rows have no tooltips (user request, 2026-10-19). A pure function, `build_row_text_parts(row, position_in_tree)`, returns the text and style for the text fields.
- A monospaced system font. No row backgrounds or borders; the only icons are the tool icons above. The tab in front gets a highlighted name (R41).
- Default names (R9.1, R15.0): a window without a title of its own shows the folder name of its first agent session. A tab without a title of its own shows the agent session's name (for Claude Code, `name` in `claude agents --json`), else its folder name. "Its own" means a non-empty `titleOverrideFormat` (set through this app or iTerm2's Edit Tab Title or Edit Window Title), not the `claude` title Claude Code sets.
- A collapsed window shows its sessions' state counts, `1 working, 2 idle`, in gray, right-aligned (R40, without its `[3 tabs hidden]` prefix: user request, milestone 2). An expanded window shows no counts, since its tab rows show each state.
- Everything starts expanded. One button beside the settings button expands or collapses all monitor and window rows (R8.1; user request, 2026-10-19): it collapses all when everything is expanded and otherwise expands all. Its icon shows the current state, arrows out while everything is expanded and arrows in otherwise, and its tooltip says what a click does (user request, 2026-10-19). It shows only while the list has two or more windows (user request, 2026-10-19); without it, the Sort pop-up and the message line reach the settings button.

### Interactions

| Action | Result |
|---|---|
| Click a row | Nothing (user requests, milestone 2 and 2026-10-19). |
| Click a row's arrow (› closed, ⌄ open; between the tree lines and the dot) | Opens or closes just that row at once, without the outline view's slide animation (user request, 2026-10-19): a monitor's or window's rows below it, or a tab's preview. Tabs without a preview have no arrow but keep its room, so names line up. |
| Press and hold a tab row | Peek (user request, milestone 2): after 0.15 seconds of holding (shorter is a click), the tab is shown as by a double-click; releasing the button puts back what was in front before: each iTerm2 window's selected tab, then iTerm2's front window if iTerm2 was in front, or else the app that was (for the dropdown, the app in front before the dropdown opened; the dropdown stays open meanwhile). The outline view's `mouseDown_` reports the press and the release around AppKit's own tracking, and a drag cancels the peek. |
| Double-click a tab row, or Show tab in its right-click menu | Focus that window and tab. The calls are `Session.async_activate(select_tab=True, order_window_front=True)` then `App.async_activate(raise_all_windows=False)`, because the default (`raise_all_windows=True`) would bring every iTerm2 window on every monitor forward. When the dashboard itself is the active app, macOS may refuse another app's activation request, so the main thread first yields activation to iTerm2 (`NSApp.yieldActivationToApplication_` with iTerm2's running-application object). In the dropdown this brings iTerm2 forward and closes the popover. |
| Double-click a window row | Shows the window (R8.2): `Window.async_activate`, so the window comes forward with the tab it already shows, even one running no agent (Show window in the right-click menu does the same). Until 2026-10-19 a single click on a monitor or window row expanded or collapsed it, and the double-click put it back; the arrows replaced that (user request). |
| F2, or Rename in the right-click menu | Rename in place: window through `Window.async_set_title`, tab through `Tab.async_set_title` (empty restores the default name). Double-click no longer renames (user request, milestone 2). This is an approved deviation from R8.2 (section 1). |
| Right-click a monitor row | Expand or Collapse |
| Right-click a window row | Expand or Collapse; then Show window; Rename; then New tab with Claude Code session…, New tab with Codex session…, other configured agent tools; then Move to monitor > (two or more monitors); then Close window, after an "Are you sure?" alert with Yes and No (No is the default), since closing ends the agent sessions in it (user request, milestone 2) |
| Right-click a tab row | Show tab; Rename; Duplicate this tab (user request, milestone 2: like Chrome's, a new tab right after this one in the same folder, continuing a copy of the conversation where the tool can fork it: Claude Code `--resume <sessionId from claude agents --json> --fork-session`, Codex `codex fork <id>` with the ID from its conversation file's name (found as for the token column); otherwise a fresh session); Move to window > (when other windows are listed); Extract to new window (disabled, with a gray reason, when the tab is its window's only tab, counting hidden tabs); then, after a separator (user request, 2026-10-19), /compact and /clear (enabled only while the row is idle or done; /clear asks "Are you sure?"; the core checks the state and the input box again before typing, as below, and an alert says why when it doesn't type) and Kill Session ("Are you sure?", then SIGTERM to the agent process and SIGKILL if it still runs 5 seconds later; the tab stays open with its shell). Interrupt (R15.2) is not built yet |
| Click the "+" row at the end of the list (on the tree's vertical line), or right-click empty space | New iTerm2 window with an agent session (R13.1; user request, 2026-10-19: the "+" moved from the bottom bar into the list): "New Claude Code Window", "New Codex Window", one per agent tool the settings' Harnesses section includes; it starts in the folder of the tab in front, else of the tab seen most recently within the last 24 hours, else the home folder (`choose_folder_for_new_agent_window`; no folder picker: user request, milestone 2) |
| Drag a tab row | Section 9 |
| Drag a window row | Reorder the list (R12), or move it to another monitor's group (R32) |
| "Settings" button at the bottom left (a button with its name rather than a gear icon, so it is easy to see; the expand or collapse button sits at the top right and the Sort pop-up at the top left; user requests, milestone 2 and 2026-10-19) | Settings window |
| Drag the dropdown's top bar (its empty space) | Detaches the dropdown into the list window (user request, 2026-10-19): "Show as" becomes Window, the window opens where the dropdown was and follows the pointer until the button comes up (`ListWindowController.follow_the_pointer_until_the_button_comes_up`). Dragging the list window (by its title bar) and releasing the pointer on the menu bar icon, which is highlighted meanwhile, turns it back into the dropdown ("Show as" becomes Popup) |
| Settings button, while the dropdown is open | The settings window opens above the dropdown, which stays open while the settings window is (`update_dropdown_closing_behavior`, shared with peeking); the settings window is resizable, its rows scroll when it is shorter than they are, and its frame is saved (`settings_window_frame`) (user requests, 2026-10-19) |
| Click the menu bar icon | Shows the list the way "Show as" asks (user request, 2026-10-19): Window brings the list window to the front (opening it at its saved frame if it was closed), Sidebar slides the sidebar in for 5 seconds as "Show the sidebar" does, Popup opens or closes the dropdown. |
| Right-click the menu bar icon | Show the sidebar (always this text: the sidebar appears for 5 seconds, also while the list shows as a window or a popup, then hides once the pointer isn't on it; a sidebar that is always shown stays as it is); Settings…; Quit. Until 2026-10-19 the sidebar was turned on with a settings checkbox, "Enable the sidebar at the edge of the screen"; "Show as" replaced it (an old settings file with that checkbox on starts as Sidebar). |

### New agent session (R13, C5)

`Window.async_create_tab` (or `iterm2.Window.async_create` for a new window) with the default profile customized through `profile_customizations`:
- the chosen folder, via `LocalWriteOnlyProfile.set_initial_directory_mode` and `set_custom_directory` (both public);
- iTerm2's "send text at start" setting carrying the adapter's launch command. There is no named setter for it, but the public constructor `LocalWriteOnlyProfile(values)` accepts raw profile keys (the key name, probably "Initial Text", is confirmed in Phase 0), so iTerm2 itself types the command once the shell starts.

The tab keeps a normal shell, so it stays open if the agent exits. (Passing the command as the tab's `command` would close the tab when the agent exits.) Fallback if Phase 0 finds the key doesn't work: wait for the new shell's prompt (iTerm2's prompt notification, or a short wait), then `async_send_text(launch_command + "\r", suppress_broadcast=True)`. Default folder: the working folder of the window's selected agent session.

**Failed starts (R14.1).** The core marks each tab it opens for a new agent session as pending. If no agent matching the chosen adapter appears in that tab within 10 seconds (for example a wrong launch command, the command missing from the new shell's PATH, or a crash at startup), the tab gets a gray "exited: failed to start" row, kept for the configured time like other abnormal ends, with the tab's last screen lines in the hover panel.

### Sending /compact and /clear (R15, R15.1)

As built first (user request, 2026-10-19): the state check and the screen check below, in the core just before typing (`handle_send_slash_command`, `iterm2_actions.type_slash_command`, `screen_readings.input_area_is_empty`); the keystroke-based draft flag isn't built yet, so the screen check alone guards. The adapters give the input line's marker, the pattern of the line below the input area (Claude Code's `─` rule, Codex's model and folder line) and the pause before Enter.

Rules, all checked just before typing:
- the agent is the terminal's foreground job: not suspended with Ctrl+Z, and no editor in front (its row keeps the state from before, so the state alone can't tell);
- the state is idle or done, confirmed as in section 6 (a Stop event alone is not enough);
- the input box is empty. Both of these checks must pass:
  - **draft flag:** set only by keystrokes that insert text into the box (printable characters, and paste as described below), not by Esc, arrow keys, Tab, Shift+Tab, function keys or other Ctrl and Cmd shortcuts, which leave the box as it was. Cleared by the user's own submission (a plain Enter with no modifier keys, or a prompt-submitted hook event), never by Shift+Enter or Option+Enter (they add a new line inside the box) and never by a turn ending (a draft typed during a turn is still in the box afterwards);
  - **screen check:** the adapter's `input_area_is_empty(screen_lines)` check finds the whole input area empty, including continuation lines of a multi-line draft (a draft can have an empty first line with text below it, for example after Ctrl-U on the first of two lines). If the adapter can't determine the extent of the input area on the current screen, the check fails and the commands stay disabled. The screen check is the authoritative guard: when it and the draft flag disagree, the item stays disabled. **Placeholders and suggestions:** both tools show dimmed text in an empty box (Claude Code: `Try "refactor <filepath>"` or a suggested next prompt such as `run the command again`; Codex: `Ask Codex to do anything`), and a typed draft is not dimmed. The screen read already carries each cell's style, so "empty" means no characters after the prompt marker other than faint ones (`style_at(x).faint`, Phase 0), plus, for Codex, the braille-dot characters of its idle animation (U+2800 to U+28FF), which it draws in and around the box. The input line is the last line that starts with the tool's prompt marker (`❯` then a no-break space for Claude Code, `›` for Codex) and sits directly above the box's lower edge (Claude Code's `─` rule, Codex's model and folder line); earlier prompts in the transcript and the highlighted item of a slash-command menu start with the same marker.

  Paste counts as text-inserting: Cmd+V, and pastes through iTerm2's Edit menu. Phase 0 checks whether iTerm2 reports Cmd+V (and menu pastes) as keystroke notifications; if not, the screen check alone guards after a paste.

  So that erasing a draft doesn't keep the command disabled, an empty screen reading taken after the last keystroke also clears the draft flag, when that keystroke was a deletion or clearing key (Backspace, Ctrl-U, Ctrl-C) or any other key that doesn't insert text (Esc, arrows, Tab). After a text-inserting keystroke, the screen reading alone cannot override the flag. If either check fails, the item is disabled with the gray reason "you have unsent text in this tab", because typing would append "/compact" to the draft and submit it as a prompt. Without a screen pattern for the tool, the commands stay disabled. Keystroke tracking records, per keystroke, only the time, whether it inserts text, whether it was Enter, its modifier keys, and whether it was a deletion or clearing key. Fixtures cover Esc to interrupt, Esc or arrow keys at a permission prompt, and Shift+Tab;
- /clear asks for confirmation.

Sending: `Session.async_send_text(command, suppress_broadcast=True)`, a pause, then `Session.async_send_text("\r", suppress_broadcast=True)`. The separate Enter keeps the agent's input box from treating the burst as pasted text, where the carriage return would become a newline instead of submitting. Codex groups 3 or more characters arriving within 8 ms of each other as a paste, and for 120 ms afterwards an Enter inserts a newline (`paste_burst.rs`). So the pause is an adapter setting: 300 ms for Codex, 150 ms for Claude Code. After sending, the app re-reads the input box half a second later; if the command is still in it, it reports "the command may not have been submitted" instead of retrying (a box that is off screen then can't be checked and counts as submitted). `suppress_broadcast=True` keeps iTerm2's Broadcast Input from also sending the command to other sessions. Command names come from the adapter. The menu items are disabled, with a gray reason, in other states. In Phase 0 this submitted `/compact` and `/clear` in both tools: typing the command opened each tool's slash-command menu with the exact command highlighted first (Claude Code listed `/autocompact` second), and the separate Enter ran it.

What follows each command (Phase 0), which the status rules use:
- Claude Code `/compact`: `PreCompact` (trigger `manual`) at once, then `SessionStart` with source `compact` about 4 to 7 s later, same session ID. iTerm2's status does not change during compaction, so the row shows "compacting" (counted as working) between the two events.
- Codex `/compact`: `PreCompact` then `PostCompact` about 4 s later; its own status is `working` in between.
- Claude Code `/clear`: `SessionEnd` with reason `clear`, then `SessionStart` with source `clear` 0.4 s later, with a new session ID and transcript, in the same process. The row stays and follows the new session ID (matched by the hook's parent process ID).
- Codex `/clear` ("clear the terminal and start a new chat"): no hook event; the new chat's session ID first appears with its first prompt, and at exit Codex sends one `SessionEnd` for each chat.

### Interrupt (R15.2)

`Session.async_send_text("\x1b", suppress_broadcast=True)` (Esc, the interrupt key of both Claude Code and Codex; the adapter can override it). Enabled while working or stuck, and only when the agent itself is the terminal's foreground job (its `commandLine` matches the adapter, so not while it is suspended with Ctrl+Z or an editor is open) and the screen shows no known non-interrupt mode (for example Claude Code's transcript viewer, opened with Ctrl+O, where Esc only closes the viewer). Otherwise the item is disabled with a gray reason. Esc is treated as a request: the row stays working until a newer iTerm2 status or screen reading confirms the turn stopped.

### Window order (R11, R12, Q1 to Q3)

- At startup, a pure function, `order_windows_by_last_prompt_time`, sorts by the newest last-prompt time among each window's agent sessions. "Prompt" means a message the user typed.
- **Source: each tool's prompt history file.** Both tools keep one, with one line per prompt the user typed, including a session ID and a time:
  - Claude Code: `<config folder>/history.jsonl`, fields `sessionId` and `timestamp` (also `project`, `display`);
  - Codex: `<Codex home>/history.jsonl`, fields `session_id` and `ts`.

  Checked on this Mac: 3.4 MB and 0.5 MB. The reader reads each file backward from the end, in a worker thread, until it has found the newest prompt of every live session, or reached the start. It keeps only session IDs and times, never the prompt text. This is cheaper and simpler than scanning transcripts (up to about 50 MB each), which contain tool results and internal messages stored as user-role entries.
- **Fallback:** transcripts, read backward and counting only entries with the user's own text (fixtures include sessions whose last user-role entries are tool results), for a session missing from the history file. For Claude Code the transcript is found as in section 7. For Codex the session file comes from the hook's `transcript_path`, or failing that from the session file the `codex` process has open (Phase 0).
- Windows without a time come next, in front-to-back order. Phase 0 checks whether iTerm2's `App.terminal_windows` follows the stacking order. If not, the source is macOS's window list (`CGWindowListCopyWindowInfo` from pyobjc-framework-Quartz, matched to iTerm2 windows by frame), which adds a dependency and some brittle matching; failing both, the order is iTerm2's window list order.
- While running, the order is stable. New windows go on top (Q2). Dragging window rows sends `reorder_window_list` to the core, which changes only the app's list (shared by the dropdown and the sidebar through the snapshot), not iTerm2; it is not saved across restarts (Q3).

### Menu bar item (R1 to R3)

An `NSStatusItem` showing a "C" in a configurable color, followed by the number of asking agent sessions. Clicking opens the dropdown panel. A right-click menu offers: Show sidebar, Settings, Quit.

### Auto-hide sidebar (R4 to R6)

- The app sets the accessory activation policy (`NSApplicationActivationPolicyAccessory`) at startup, in code, because a `pipx`-installed Python process has no Info.plist. It then has no Dock icon and can own non-activating panels.
- A non-activating `NSPanel` that floats above normal windows and takes keyboard focus only when you click into a text field. The sidebar gets the collection behaviors can-join-all-Spaces, full-screen-auxiliary and stationary, and a window level above normal windows, so it also appears over full-screen iTerm2 windows (each of which is its own Space).
- Three modes (milestone 2, see section 1): auto-hide (below), always visible over other windows, and docked, where the core keeps iTerm2 windows on that monitor out of the sidebar's strip (`compute_frame_clear_of_area`; windows get narrower, or move once they would be under 400 points wide; their old frames are restored when docking ends, unless the user moved them since; a window the user moves back into the strip is made room for again and later restored to where the user put it; frames are read fresh before each adjustment; a window that closes mid-resize is skipped; full-screen windows are left alone, and one that is full screen when docking ends keeps its record for a later undocking; on Quit, and on Ctrl+C in the terminal that started the app, the room is given back first, waiting at most 3 seconds). The old frames are also saved to `docked_window_frames.json` next to the settings, so windows still get their room back after the app restarts (a reconnect when iTerm2 quits, a crash); a record whose window is gone moves to a window that has no record and still has the narrowed frame, since iTerm2 gives the windows it restores new IDs.
- Only outer edges can be chosen (R5). A pure function, `find_outer_screen_edges(screen_frames)`, drops any left or right edge that touches another monitor. The chosen monitor is saved by its name (milestone 2; a display UUID was planned). The default monitor is the one with the menu bar (`NSScreen.screens()[0]`), not `NSScreen.mainScreen()`, which follows the focused window.
- **Default and fallback edge:** the sidebar always goes on the menu-bar monitor (a separate monitor setting was removed at the user's request on 2026-10-19). A pure function, `choose_sidebar_edge(saved_edge, screens)`, picks from the outer edges of all connected monitors: the saved edge of the menu-bar monitor if it is outer; otherwise its other edge; otherwise the outer edge nearest the menu-bar monitor (for example the rightmost monitor's right edge, when three monitors stand in a row with the menu bar on the middle one). The default (right edge) goes through the same function.
- On every "screen layout changed" message the saved edge is checked again with that function. While the saved edge borders another monitor, the settings window says where the sidebar is instead (`describe_sidebar_placement_fallback`); the saved edge is kept, so it applies again once that monitor moves or is unplugged. Tests cover one, two and three monitors, including a middle menu-bar monitor.
- **Detecting the pointer at the edge, without blocking clicks there:** no window sits on the edge. A global mouse-moved monitor (`NSEvent.addGlobalMonitorForEventsMatchingMask_handler_` for mouse movement, which needs no Accessibility permission; it only watches, it doesn't consume events) plus a local monitor for the app's own windows tests the pointer against a 2-pixel rectangle along the chosen edge of the chosen monitor. A panel there would either cover the edge of every window beneath it (a maximized iTerm2 window's scroll bar and resize edge) or, if made click-through, might miss the pointer. If Phase 0b finds the monitor unreliable, the fallback is a 10 Hz check of `NSEvent.mouseLocation()`. When the pointer reaches the edge, the sidebar slides in. It slides out half a second after the pointer leaves, but not while anything belonging to the sidebar is open: a rename or drag, a right-click menu or submenu (tracked with the menu delegate's `menuWillOpen` and `menuDidClose`), the /clear confirmation, or the hover panel. When the last of these closes, the app checks the pointer position again.
- Width is changed by dragging the sidebar's inner edge, and saved. A borderless window gets no resizing edges from macOS, so a 6-point strip along the inner edge shows the left-right arrows pointer (a tracking area that stays active while another app is in front) and does the drag itself. Two things make the arrows appear (found with real mouse events on 2026-10-19): macOS ignores pointer changes from an app that isn't active, which the sidebar never makes the dashboard, so the window server's undocumented "SetsCursorInBackground" setting is turned on (as other menu bar utilities do; without it the edge still drags); and the list's own pointer areas reset the arrows right after the pointer enters the strip, so the sidebar window has them turned off (`disableCursorRects`; the rename field then shows the plain arrow instead of the I-beam).

### Global shortcuts, filter and keyboard navigation (Q8)

- **Shortcuts:** one opens the dropdown from any app; another focuses the next agent session that is asking (in urgency, then list order). Registration uses macOS's hot key service (`RegisterEventHotKey`, which needs no special permission and consumes the key so it never reaches iTerm2), through a small wrapper package or PyObjC if Phase 0 finds a way. There is deliberately no global key monitor fallback: such a monitor can only watch keys, not swallow them, so the shortcut would also be typed into the focused agent session's input box. If `RegisterEventHotKey` can't be used, the alternatives are an active event tap (`CGEventTapCreate`, which can swallow the key but needs the Accessibility or Input Monitoring permission) or shipping without global shortcuts; either becomes a decision for you, listed in section 1.
- **Built (R3.2, 2026-10-19): the show-the-list shortcut uses an active event tap.** The user chose Shift-Command-Tab (default) or Command-Fn-Tab, to go with the app's name, over combinations that `RegisterEventHotKey` could take; every Command-Tab combination with T is taken in iTerm2 except Control-Option-Command-T (Command-T New Tab, Shift-Command-T Undo Close, read from iTerm2's menu). Tested with real key presses: macOS registers Shift-Command-Tab, and even Command-Tab, with `RegisterEventHotKey` but never delivers them, since the app switcher takes them first (Fn is invisible to that call, so Command-Fn-Tab is just Command-Tab to it); an event tap at the HID level (`kCGHIDEventTap`) sees them first and can hold them back, and Fn is visible there. `global_shortcut_key_filter.py` holds back only the chosen combination and the Tab release that follows it; `decide_what_to_do_with_key_event` is a pure function with tests. While the app switcher is open (Command held since a Command-Tab, also with Shift or Fn), the combination is let through to it, where it moves backward or forward (user request). The tap runs on its own thread with its own run loop, so key presses never wait for the main thread's run loop; each key event does take Python's global lock once (its CoreGraphics reads keep the lock, through `ctypes.PyDLL`), so while another thread runs Python a key press waits a moment. It is turned off (not removed) while the setting is Off; macOS turns a tap off when it answers too slowly, and it is turned back on, forgetting what it remembered (missed key events may have made that stale). A held-back press stays held back until its Tab release, repeats included. CoreGraphics is called through ctypes (no extra package). It needs the Accessibility permission, which macOS checks for the app that started it (iTerm2, when started from iTerm2, so the permission must be turned on for iTerm2); without it, Settings show a button that opens System Settings > Privacy & Security > Accessibility, and the app tries again whenever it becomes active or its menu bar icon is clicked (the sidebar slides in without making the app active). The shortcut only shows the list, never hides it (user choice): the dropdown opens, the sidebar slides in for a few seconds, the window comes to the front.
- **Filter:** a filter field at the top of the list. It takes keyboard focus only when clicked, or when the dropdown opens by shortcut. Typing narrows rows by name, folder or agent tool; Esc clears it.
- **Keyboard navigation:** while the list has focus, arrow keys move the selection, Right and Left expand and collapse, Return focuses the selected tab, and F2 renames.

### Settings (R34, R35)

Stored as JSON in `~/Library/Application Support/agent-command-tab/settings.json`:

| Setting | Default |
|---|---|
| Show as (radio buttons "Show as: Window, Sidebar, Popup" at the top of the settings window; user request, 2026-10-19) | Popup. Window is a regular window (`list_window.py`) you can move, resize and minimize, with its own list; it remembers its frame (`list_window_frame`), opens in the middle of the main screen when that frame is on no connected screen, and closing it only hides it |
| Shortcut (`global_shortcut`, a pop-up under "Show as"; user request, 2026-10-19) | Shift-Command-Tab; or Command-Fn-Tab, or Off (see "Global shortcuts" above) |
| Window: Always on top (`list_window_is_always_on_top`, in the settings window's Window section) | Off: a regular window level; on, the floating level, above other apps' windows |
| Sidebar edge, width | Right edge of the menu-bar monitor if that edge is outer (otherwise as chosen by `choose_sidebar_edge`), 300 points |
| Agent tools to detect, with patterns and launch commands | Claude Code (`claude`), Codex (`codex`) |
| Claude Code config folders | `~/.claude`, plus `$CLAUDE_CONFIG_DIR` if set when the app first runs; users can add more (for example a second account's folder) |
| Codex home folder | `~/.codex`, or `$CODEX_HOME` if set when the app first runs |
| Paths to the `claude` and `codex` commands | Found at first run on the shell's PATH (for example `~/.local/bin/claude`) |
| Stuck threshold | 10 minutes |
| How long a crashed agent session's row stays | 5 minutes |
| Importance rules (file count, source patterns, turn length, tool calls) | Defaults in section 6 |
| Show Codex usage, refresh interval | On, 1 minute |
| Icon color, show asking count | Auto (light blue while any agent session is asking, red for an error, orange when stuck, otherwise green), on |
| List sort order (the Sort pop-up at the top of the list; user request, 2026-10-19) | Manual. `list_sorting.py`: Manual shows iTerm2's tab order and the window order set by dragging; Activity puts asking, error, stuck, working, done, idle in that order, the latest state change first among equals; Alphabetically sorts by name ignoring case; Created Time puts the newest agent session first. Windows follow their first tab in that order. Sorting changes only what the list shows, so Manual always comes back unchanged; while sorted, a tab can only be dragged to the end of another window, and windows only between monitors. The pop-up is at the left of the top bar; it loses its "Sort:" label below about 217 points (only a narrow sidebar), and would hide below 185, narrower than the sidebar can be |
| Shown columns (Settings checkboxes; user request, 2026-10-19) | State, State Duration and Used Token Count on; Created Time (when the agent's process started: "6:08 AM" today, otherwise "Oct 17") and Time Since Created off (the setting's internal name is `existence_duration`); Memory Usage and CPU Usage off (user request, 2026-10-19: the agent process and every process below it, read every 3 seconds with libproc's `proc_pid_rusage`; memory is the physical footprint Activity Monitor shows, "512M" or "1.4G"; CPU is the change in CPU time between two readings, "12%", 100% being one core). Left to right (`TAB_ROW_COLUMNS`): tokens, created time, time since created, memory, CPU, tool icon, state, state duration. A narrow sidebar or window drops created time, time since created, memory and CPU first |
| Harnesses (the settings window's last section; user request, 2026-10-19) | "Include:" a checkbox per agent tool (`is_included` in `agent_tools`), with its launch command under it. A tool left out isn't listed (the core leaves its sessions out of the snapshot) and isn't offered in the menus that start agent sessions |
| Start at login | Off |

The config folders and command paths are resolved once, at first run, and saved here. The app never relies on the environment at runtime, because when it starts at login, launchd gives it a minimal PATH (`/usr/bin:/bin:/usr/sbin:/sbin`) and none of the shell profile's variables. Without saved paths, `claude agents --json` would not be found and the config-folder defaults would differ from a terminal launch. The saved command path is the stable one found on PATH (a link that Claude Code's updater keeps current), never the running process's own file, which is named after its version (section 3) and disappears after an update. If a saved path stops existing, the app looks it up again (in the saved PATH folders) and shows a settings warning if the command cannot be run.

## 9. Drag and drop (R12, R18, R32)

- Rows carry the iTerm2 tab or window ID under private drag types.
- **Tab within its window.** iTerm2 needs the full tab order, but the list hides tabs without an agent. A pure function, `compute_full_tab_order_after_move(full_tab_ids, moved_tab_id, drop_before_visible_tab_id)`, keeps hidden tabs in place relative to their neighbors and returns tab IDs. The action layer then reads the window's current tabs from iTerm2, maps each returned ID to its current `Tab` object (`async_set_tabs` takes `Tab` objects and reads `tab.tab_id` from each), and calls `Window.async_set_tabs(tab_objects)`.
- **Tab to another window.** Same function on the target window's current tab list (with the moved tab inserted), the same mapping to `Tab` objects (the moved tab's object comes from its source window), then `target_window.async_set_tabs(...)`. iTerm2 moves the tab; a window left without tabs closes.
- **Tab to a new window.** `Tab.async_move_to_window()`, which works only when the tab's window has more than one tab (counting hidden tabs); otherwise the item is disabled.
- **Window rows.** Dropping within the same monitor group reorders the app's list only. Dropping into another monitor's group moves the window (section 10).
- **Agents keep running.** These calls move iTerm2's tab object as dragging a tab in iTerm2 does: the process and terminal device stay the same. A window of a different size sends a normal resize signal and the agent redraws. Phase 0 tests this with a counter loop and a live Claude Code turn.
- **Tab groups** are ignored, but iTerm2 keeps a group's tabs together and may adjust an order. The list shows whatever iTerm2 reports afterwards.
- Drops that make no sense are refused during the drag.

## 10. Monitors (R31 to R33)

- `NSScreen.screens()` gives monitor rectangles, read on the main thread and sent to the core as plain rectangles (section 4). `Window.async_get_frame()` gives window rectangles. Phase 0 confirms they share one coordinate system (origin at the bottom-left of the main monitor).
- `assign_windows_to_monitors_by_largest_overlap` (pure) puts each window on the monitor it overlaps most. Monitor groups go left to right (Q4) and appear only with 2 or more monitors.
- Moving a window keeps its size if it fits (otherwise shrinks it to the target's usable area), keeps its relative position, and calls `Window.async_set_frame`. A full-screen window can't simply be given a new frame, so it is taken out of full screen, moved, and put back into full screen on the target monitor (R32 has no full-screen exception). Phase 0 checks this works through iTerm2's API; if it doesn't, the restriction is listed in section 1 as a deviation for your approval.
- Updates on the main thread's "screen layout changed" message and the 2-second frame poll.

## 11. Agent tool adapters (C4)

| Member | Purpose |
|---|---|
| `agent_tool_name`, `short_label` | "Claude Code", "Codex" |
| `matches_command_line(command_line)` | Recognize the tool in a tab |
| `launch_command` | For new agent sessions (a setting) |
| `compact_command`, `clear_command` | For R15.1 (None when the tool has no equivalent) |
| `hook_settings_file_paths(config_folders)`, `build_hook_entries()` | For `install-hooks`, one settings file per configured config folder |
| `translate_hook_event(event)` | Hook JSON to state, reason and preview fields |
| `find_history_file(session, config_folders)`, `read_last_prompt_time(path)`, `read_context_fill(path)` | Startup order and context fill |
| `working_screen_pattern`, `prompt_screen_pattern`, `progress_noise_pattern`, `permission_dialog_screen_pattern` | Required regular expressions for the screen fallback, the empty-prompt check, stuck masking and denial-with-comment (section 6); the generic adapter has defaults |
| `input_area_is_empty(screen_lines)` | Whole input area empty, including continuation lines (R15.1); returns "unknown" when the area can't be found, which keeps commands disabled |

Built-in adapters: Claude Code, Codex, and a generic one. Simple tools (name, pattern, launch command, screen patterns) can be declared in settings without code. Other tools come later.

**How the generic adapter finds tools (R22).** A first-pane session whose foreground job is not a shell (its `commandLine` doesn't start with a known shell such as `zsh`, `bash` or `fish`) and whose iTerm2 `status` variable holds a recognized value (section 6, source 2) is listed as an agent session of the generic adapter, named after the first word of its `commandLine`. Tools with neither a status report nor a settings entry are not listed: nothing distinguishes them from any other program, so R22's "otherwise listed with a basic working or idle status" applies to tools declared in settings without a status report. This narrowing of R22 is listed in section 1 for approval.

## 12. Packaging, license, conventions

- Install with `pipx install agent-command-tab` (or `uv tool install`); run `agent-command-tab run`. `install-login-item` writes a per-user launch agent for start at login, with the absolute path to the installed command, and relies on the paths saved in settings (section 8), not on the shell's environment. A py2app `.app` can come later.
- Dependencies: `iterm2` (brings `protobuf` and `websockets`) and `pyobjc-framework-Cocoa`. `pyobjc-framework-Quartz` was planned (for a display UUID and a window-order fallback) but is not used; the global shortcut's key filter calls CoreGraphics through ctypes, so it needs no extra package either.
- License: undecided. `iterm2` is GPL-2.0-or-later. Our files could be MIT, which is compatible with the GPL, but a bundled `.app` that includes `iterm2` would be distributed under GPL terms. GPL-2.0-or-later for the whole project avoids that split. Not legal advice.
- Conventions: Python 3.10 or later; standard `unittest` with files named `*_tests.py`; Google-style docstrings; no type hints; `print()` for diagnostics behind `--verbose`; logic in pure functions; long descriptive names.
- `install-hooks --agent claude-code|codex [--config-dir PATH]` shows the exact change, makes a dated backup, adds entries next to existing hooks, refuses if the file changed meanwhile; `uninstall-hooks` removes only its own entries (Q7). Without `--config-dir` it offers every configured config folder (section 8 settings).
  - **Claude Code:** `<config folder>/settings.json`.
  - **Codex:** `<Codex home>/hooks.json` (plain JSON, the same layout as Claude Code's `hooks` block; the `hooks` feature is on by default, Phase 0). No TOML writing is needed: Codex itself records approvals in `config.toml`. Codex skips new or changed hooks until the user approves them in Codex's `/hooks` screen, so the installer ends with that instruction. The app shows "installed, waiting for approval" until the first real hook event from Codex arrives.

## 13. Code layout and size

Line counts are my rough estimates.

```
src/agent_command_tab/
  command_line.py                          run (the restarting supervisor), install-hooks, uninstall-hooks,
                                           install-login-item, dump-snapshot                                        ~200
  app_controller.py                        starts both threads, routes snapshots and actions                       ~120
  dashboard_core_api.py                    ActionRequest, ActionResult, action registry and flags, subscriptions   ~150
  iterm2_connection_thread.py              connection, reconnect, subscriptions                                     ~250
  iterm2_snapshot_builder.py               iTerm2 objects to snapshot data classes                                  ~200
  iterm2_actions.py                        focus, rename, reorder, move, new tab or window, send text, set frame    ~200
  snapshot_data_classes.py                 section 4                                                                ~120
  agent_adapters/                          base, Claude Code, Codex, generic                                        ~450
  status_sources/                          hook socket listener and saved hook state, iTerm2 status variable,
                                           screen patterns and fingerprints, keystroke tracking                     ~400
  session_hook_state_reducer.py            apply_hook_event_to_session_hook_state, checkpoints                      ~250
  session_status_computation.py            compute_session_status_from_sources, urgency order, stuck rule           ~200
  session_preview_computation.py           section 7                                                                ~120
  session_importance_computation.py        compute_session_importance, turn activity summary                       ~150
  agent_history_file_readers.py            last prompt time, context fill, Codex usage                              ~250
  window_list_ordering.py                  startup order, stable order, manual moves                                ~120
  tab_order_computation.py                 section 9                                                                ~80
  monitor_assignment.py                    section 10                                                               ~120
  settings_store.py                                                                                                 ~120
  hook_installer.py, hook_event_forwarder.py                                                                        ~250
  user_interface/
    menu_bar_status_item.py                                                                                         ~150
    agent_session_outline_view.py          rows, editing, menus, drag and drop                                      ~700
    row_text_rendering.py                  pyinstrument-style text pieces                                           ~200
    global_hot_keys.py                     RegisterEventHotKey wrapper (Q8); built instead as global_shortcut_key_filter.py, an event tap, in the package folder   ~100
    session_details_hover_panel.py                                                                                  ~120
    popover_container.py                                                                                            ~80
    sidebar_panel.py                       edge panel, slide, resize                                                ~300
    settings_window.py                                                                                              ~300
tests/                                     *_tests.py plus fixtures                                                 ~1,500
spikes/                                    Phase 0 experiments
```

Total: roughly 5,700 lines of app code (the sum of the estimates above) plus 1,500 of tests.

## 14. Difficulty and risk by feature

*Code*: amount. *Complexity*: how hard to get right. *Brittleness*: how likely to break when iTerm2, Claude Code or Codex change. *Overhead*: CPU, memory, disk while running.

| Feature | Code | Complexity | Brittleness | Overhead | Main risk and mitigation |
|---|---|---|---|---|---|
| Connection, snapshots, subscriptions | Medium | Medium | Low | Low | asyncio and AppKit on two threads; Phase 0 prototype |
| iTerm2 authentication flag, connection watchdog and restart supervisor | Small | Medium | Medium | Low | Setting `iterm2.auth.gAppKitAvailable` relies on a module variable of the `iterm2` package, which could change in a release, so the `iterm2` version is pinned and a test checks that the `NSAppleScript` runner is never used. A dropped connection restarts the whole app, so the list disappears for about a second and reappears |
| Start at login | Small | Low | Medium | None | launchd's minimal environment; relies on the paths saved at first run |
| Agent detection | Small | Low | Medium | None | Process names change (already seen: version number as `jobName`); match `commandLine`, test with fixtures |
| Status | Medium | **High** | **Medium to High** | Low (one screen read every 2 s only for sessions that are asking) | Several sources disagree; one pure function with exhaustive tests built from the Phase 0 recordings. For Claude Code, iTerm2's status is itself hook-driven, nested agents rewrite it, and nothing reports an approval, so ending "asking" depends on recognizing the permission dialog on screen; Codex 0.163 reports its own status and is simpler |
| Stuck detection | Small | Medium | **High** | Low with fingerprints | **Would be high if it watched every redraw**; fingerprints every 30 s. Depends on per-tool masking of spinner and counter lines, which breaks when a tool changes its screen layout; fixtures from recorded screens |
| Screen-pattern fallback (working or idle without hooks) | Small | Medium | **High** | Low (every 3 s, bottom lines only) | Screen scraping of each tool's interface; per-adapter patterns with fixtures |
| Previews | Medium | Medium | Medium | Low | Hook payloads change; adapters ignore unknown fields |
| Hook forwarder | Small | Medium | Low | **Low to Medium**: one short process per hook event; disk: a few small files per live agent session, pruned on session end and at startup | Registered as async hooks so agents never wait; standard-library-only forwarder; trimmed payloads; one 100 ms deadline for connect plus send; only the needed events; measured in Phase 0 (target under 30 ms per event) |
| `claude agents --json` runs | Small | Low | Medium | Low to Medium: a large program, run at detection, after /clear, on `SessionStart` and every 60 s only for hookless sessions | Asyncio subprocess with a timeout; blank fill while a session ID may be stale |
| Context fill | Medium | Medium | **High** | Low | Internal file formats and model context sizes; Codex already calls its rollout files legacy; fail quietly, fixtures, overridable size table |
| Codex usage bars | Small | Low | **High** | Low (per-file read positions) | Internal, already-legacy file format; newest reading chosen by event timestamp, not file order; fail quietly |
| Startup order by last prompt | Small | Low to Medium | Medium | Low at startup (prompt history files of a few MB, read backward once) | Internal history-file formats; transcripts only as a fallback; worker threads; first snapshot shown before ordering finishes; fallback to window order |
| Menu bar item | Small | Low | Low | None | Colored "C" must stay legible on light and dark menu bars |
| pyinstrument-style rows | Medium | Medium | Low | Low | Custom text in a stock outline view; prototype in Phase 0 |
| Rename and focus | Small | Low | Low | None | Claude Code may overwrite titles; Phase 0 checks |
| New agent session (tab or window) | Small | Low | Low to Medium | None | "Send text at start" through a raw profile key (name confirmed in Phase 0); fallback types the command after the shell prompt appears, which without shell integration needs a short wait |
| /compact and /clear | Small | Medium | **High** | None | Typing at the wrong moment, into a draft, or into other sessions through Broadcast Input; at-the-prompt and empty-input rules, separate Enter, `suppress_broadcast=True`. Depends on each tool's empty-prompt screen pattern, so a screen redesign in Claude Code or Codex disables the commands until the pattern is updated |
| Drag tabs between windows | Medium | **High** | Medium | None | Drag and drop in PyObjC, hidden-tab order mapping, iTerm2's move semantics; Phase 0 test, pure order function |
| Window list reorder | Small | Low | Low | None | |
| Importance (R26) | Medium | Medium | Medium | Low | Keystroke events from iTerm2 (keep only times); activity rules are guesses that need tuning and are weaker for Codex |
| Background-process lights (R26.2) | Small | Low | Medium | Low | `background_tasks` comes from an undocumented iTerm2 function |
| Exited rows (R14.1) | Small | Medium | Medium | None | Telling a crash from a normal exit needs session-end hooks |
| Default names (R9.1, R15.0) | Small | Low | Medium | None | `name` from `claude agents --json`, whose fields are observed but not documented; folder name otherwise |
| Interrupt (R15.2) | Small | Medium | **Medium to High** | None | Its safety checks (working or stuck status, agent in the foreground, no known non-interrupt screen mode) and its confirmation depend on status detection and screen patterns |
| New window (R13.1) | Small | Low | Low | None | Folder picker from a non-activating panel needs the app to activate briefly |
| Monitors | Small to Medium | Low to Medium | Low | Low | Coordinate systems; Phase 0 check |
| Auto-hide sidebar | Medium | Medium to High | Medium to High | Low | Typing (rename, filter) and starting drags in a non-activating panel of an inactive accessory app, tracking areas, every desktop including full-screen Spaces, slide timing. Prototyped in Phase 0. Fallback if typing there proves unreliable: the sidebar is read-only for names, and Rename opens the dropdown |
| Applying snapshots to the list | Medium | **High** | Low | Low | Stable item objects, row-level updates, holding updates during rename or drag; prototyped in Phase 0 with live snapshots |
| Settings window | Medium | Low | Low | None | Mostly form code |
| Hook installer | Small to Medium | Medium | Medium | None | Editing users' settings files safely in each config folder; diff, backup, refuse on concurrent change; Codex needs approval in `/hooks` (no feature flag, Phase 0) |
| Global shortcuts (Q8) | Small | Medium | Medium | None | macOS hot key registration (`RegisterEventHotKey`) from Python may need an extra package; no global-monitor fallback, because it would let the shortcut keys reach the agent's input box |
| Filter and keyboard navigation (Q8) | Small | Low | Low | None | |
| Core and views boundary (C6, section 18) | Small | Low | Low | None | Keep snapshots and action requests JSON-ready; no network code in version 1 |
| Later: introduce agent sessions to each other (section 18) | Small for Claude Code and Codex; Medium to Large for other tools | Medium | Medium | None | Claude Code has native messaging between sessions; Codex uses `codex queue`, which reached a running session in Phase 0; other tools need a relay this app provides |
| Later: web page or phone app (section 18) | Medium | Medium | Low | Low | Needs the network security work below if reachable from other devices |
| Later: shared view across computers (section 18) | **Large** | **High** | Medium | Medium | **Warning:** discovery, pairing, encryption and permissions for a service that can type into terminals |

**Warnings.**
- Context fill for Claude Code and the Codex usage bars depend on internal file formats. Expect them to break occasionally after updates. They should show blanks, not wrong numbers.
- /compact, /clear, Interrupt, stuck detection, the end of Claude Code's "asking" and the hookless working or idle fallback all read each tool's screen. One screen redesign in Claude Code or Codex can disable all of them together until that adapter's patterns are updated. The app should then fall back safely (commands disabled, no stuck state), never type into a session on a guess.
- Drag and drop between windows is the most complex and least certain part. If Phase 0 shows that iTerm2's tab move disturbs a running agent session, this feature should be cut to "Move to window" menu items or dropped.
- The shared view across computers (C6) is the largest possible addition, with security exposure. Only the data boundary is planned.

## 15. Phases

Effort figures are my rough estimates for one developer working with an AI coding agent.

| Phase | Contents | Done when | Effort |
|---|---|---|---|
| 0a. Quick checks | Scripts in `spikes/` answering the quick questions in section 16 | Each has a recorded answer | 1 to 1.5 days |
| 0b. UI prototypes | The menu bar app with an asyncio iTerm2 thread; the outline view in the popover with live snapshots, rename and drag; the non-activating sidebar panel with the edge trigger, rename and drag, also over a full-screen Space | Each prototype works, or its fallback is chosen | 2 to 3 days |
| 1. Read-only list | Connection thread, snapshots, detection, `status` variable, menu bar "C" with count, dropdown with pyinstrument-style tree, expand and collapse all | The dropdown shows the right windows and agent session tabs with live status | 3 days |
| 2. Hooks and data | Hook forwarder, listener, saved hook state, installer; screen patterns and fingerprints, with fixtures; stuck; exited rows (R14.1); keystroke tracking and the draft flag; previews and hover panel; context fill; Codex usage bars | A real hook event arrives from both Claude Code and Codex (after Codex's `/hooks` approval); Codex shows working and done, and asking for whichever kinds of waiting Phase 0 showed its hooks report; previews and context fill appear; crashed sessions leave gray rows and closed tabs vanish | 3.5 days |
| 3. Actions | Focus, rename with default names, new agent session (tab, and new window with folder picker), /compact, /clear and Interrupt (using the Phase 2 draft flag and screen checks), settings window | All right-click and click actions work | 2 days |
| 4. Ordering and drag and drop | Startup order, stable order, window reorder, tab moves within and between windows | Agent sessions keep running across moves; list matches iTerm2 | 2 days |
| 5. Importance and monitors | Importance levels (using Phase 2's keystroke tracking), dot hues and background lights; monitor groups and window moves | Rows brighten when asking or recently used; works with 1 to 3 monitors, including unplugging | 1.5 days |
| 6. Auto-hide sidebar | Edge panel, slide, resize, settings | Slides in on any desktop, including over a full-screen iTerm2 window, without taking focus | 1.5 days |
| 7. Global shortcuts, filter and keyboard navigation (Q8) | Hot keys, filter field, arrow-key navigation (section 8) | Shortcuts work from any app; filtering and navigation work without the mouse | 1 day |
| 8. Release | pyproject, pipx install, start at login, README with screenshots, license, CI on GitHub Actions | A fresh Mac installs and runs it from the README | 1 day |

Total: roughly 18 to 21 working days. This is a rough range, not a commitment. The UI phases (1, 4 and 6) are the most likely to run over, because they depend on how PyObjC's outline view and non-activating panels behave in practice.

## 16. Phase 0 questions

| Question | Why | Test |
|---|---|---|
| Does `Window.async_set_tabs` move a tab between windows without interrupting a running agent session? | R18 | Move a tab running a counter loop, then a Claude Code session mid-turn |
| What values does the `status` variable take for asking, done and error, for Claude Code with and without iTerm2's own hooks? What do OSC 7501, OSC 21337 and a "Set Tab Status" trigger each put in it? | Status | Watch it through each state; print each escape code by hand; add a test trigger |
| What is Codex's `commandLine`, and does a Codex tab get any `status` value? | Detection, status | Open a Codex tab |
| Does a tab title set with `Tab.async_set_title` survive Claude Code's own title updates? | R16 | Rename a tab and run a few turns |
| Which session-end reasons do Claude Code and Codex hooks report for Ctrl-D, /exit, a crash and a kill, and what does iTerm2 report when the user closes the tab or the window? | R14.1 | End sessions each way, including closing the tab and closing the window, and log the events |
| Does `async_send_text(command)` followed by a separate `"\r"` submit the command in Claude Code and in Codex, with and without an open slash-command menu, and what does each tool's empty prompt line look like on screen? | R15.1 | Send /compact to an idle session each way; record the prompt line |
| Which file does Codex 0.154 read hooks from (`hooks.json` or `config.toml`), must `codex_hooks` be enabled, and how does `/hooks` approval behave after an upgrade? | Q7, Phase 2 | Install a test hook and watch for events |
| What do Claude Code's and Codex's screens look like during a long turn: which lines change every second without real progress? | Stuck masking, screen fallback | Record screens every 30 s during a long turn |
| Where does a Claude Code session's model and long-context variant show up (hook `model` field, transcript entries)? | R15 context fill | Compare a normal and a long-context session |
| Does `App.terminal_windows` list windows in front-to-back order? | R11 fallback | Reorder windows by clicking and compare |
| How should keystrokes be tied to sessions: one `KeystrokeMonitor(session=...)` per agent session, or one raw keystroke subscription reading its `session` field? Which costs less with 20 sessions? | R26, R15.1 | Type into two tabs with each approach |
| How many `PreToolUse`/`PostToolUse` events does Codex send in a turn with several tool calls, which events fire for a Codex question and for a permission prompt, and does `ITERM_SESSION_ID` reach Codex hook processes (also when Codex's shared app-server daemon is in use)? | Codex gaps (REQUIREMENTS), Phase 2 | Run such turns and log every event with its environment |
| How long does the hook forwarder take per event? | Overhead | Time 100 events |
| Does the app work when started at login by launchd: PATH, environment, the iTerm2 permission prompt, finding `claude`? | R35, section 8 settings | Run the experiment script as a launch agent and log what it sees |
| Does the agent session keep its row when Claude Code opens an editor (Ctrl+G) or is suspended (Ctrl+Z)? | R14.1 | Try both and watch `commandLine` and the agent PID |
| Does `LocalWriteOnlyProfile({"Initial Text": launch_command})` (key name to confirm) start the agent in a new tab, together with the custom directory? If not, does typing the launch command after the prompt appears work reliably, with and without shell integration? | R13, R13.1 | Open 10 tabs each way |
| Does Codex 0.154 still write rollout files for new sessions, and where do `token_count` and `rate_limits` live after `codex migrate-rollouts --apply`? | R3.1, R15, R11 | Inspect a new session, then migrate a copy of the Codex folder |
| Does Claude Code's `"async": true` hook setting keep agents from waiting, and does Codex have an equivalent? | Hook forwarder cost | Add a slow test hook and time a tool call |
| Does Esc interrupt in every agent state the app enables it for, and what does each tool show in modes where Esc does something else (Claude Code's transcript viewer, a suspended agent)? | R15.2 | Try Esc in each state and record the screens |
| Does `codex queue --thread <session> --message <text>` reach an interactive Codex session in an iTerm2 tab? | Later: introductions | Queue a message to a running session |
| Does `claude agents --json` list only the sessions of the config folder in `CLAUDE_CONFIG_DIR`? | Default names, transcripts | Run it with each config folder on this Mac |
| With the `osascript` token runner and the wrapped `Connection.authenticate`, does connecting (including a rejected handshake) never use `NSAppleScript`? Does the supervisor restart the app cleanly after iTerm2 restarts? | Section 4 connecting | Restart iTerm2 while the prototype runs; force a stale token; log every authentication call |
| What does `background_tasks` in `get_session_status` count (background shells, subagents, both), and when does it change? | R26.2 | Start a background shell and a subagent and poll it |
| Do Claude Code and Codex let an async `SessionEnd` hook finish as they exit, and does it arrive before or after iTerm2 reports the change? | R14.1 | Exit with Ctrl-D and /exit while logging arrival times |
| How long does one `async_get_screen_contents()` call take, every 3 s for 20 sessions? | Screen checks overhead | Time it with 20 tabs |
| Does iTerm2 report Cmd+V and Edit-menu pastes as keystroke notifications? | R15.1 draft flag | Paste into a tab while monitoring |
| What hook events and screens follow a permission denial with a comment, in Claude Code and Codex? | Asking ends correctly | Deny with a comment and log events and screens |
| From the dashboard while it is the active app, does yielding activation let iTerm2 come forward reliably? | R17 | Click tab rows while typing in the dropdown filter |
| Which Codex hooks fire when the user presses Esc mid-turn? | Codex interrupts | Interrupt a Codex turn and log events |
| Does a global mouse-moved monitor see the pointer at the screen edge reliably, also over a full-screen iTerm2 window? (Phase 0b) | R5 | Move the pointer to the edge in each situation |
| Can a full-screen iTerm2 window be moved to another monitor through the API (leave full screen, set the frame, enter full screen again)? | R32 | Try it on two monitors |
| Do the PyObjC event loop and an asyncio thread with an open iTerm2 connection run smoothly together? | Architecture | Minimal menu bar app listing tab names live |
| Does an `NSOutlineView` in an `NSPopover` handle drag and drop, in-place editing and attributed-string rows well from PyObjC, while snapshots keep arriving? (Phase 0b) | R16, R18, R36 | Prototype fed live fake snapshots every 0.5 s while renaming and dragging |
| Can a non-activating edge panel in an accessory app take typing (rename, filter), start drags, and track the pointer, also over a full-screen iTerm2 Space? (Phase 0b) | R5, R6, R10, R16, R18 | Prototype the edge panel with the same outline view |
| Do window frames and `NSScreen` frames share a coordinate system? | R31 | Compare on two monitors |
| Does the `codex` process keep its session file open (for finding it without hooks)? | R11, R15 | `lsof` on a running Codex. Answered in milestone 2: no (0.163), so the file is found by folder and start time instead. |
| Which way of registering a global shortcut works from an unbundled Python process? | Q8 | Try the candidates. Answered on 2026-10-19 for the chosen Command-Tab combinations: only an active event tap, since the app switcher takes them before `RegisterEventHotKey` (section 8, "Global shortcuts"). |

## 17. Testing

- Unit tests for every pure function: status computation (every source combination, freshness after an interrupt, a Stop event followed by a continuing Stop hook, stuck timing, done becoming idle: turn ends in the background, tab focused, tab left, and turn ends while in front; normal versus abnormal endings, closed tabs), hook-event session matching on the GUID after the colon of `ITERM_SESSION_ID`, the empty-input rule for typed commands, importance levels (each rule, the 2-minute interaction window), outer screen edges, default names, preview choice, context fill and usage readers (fixture files, including malformed lines), window ordering, tab order computation (hidden tabs, ends, cross-window), monitor assignment (overlaps, ties, negative coordinates), adapter matching, row text building, hook installer (merge, backup, refusal, uninstall).
- Snapshot fixtures from `dump-snapshot` test the snapshot builder without iTerm2.
- A UI smoke test builds the outline view from fixtures. Each phase ends with a short manual checklist against real iTerm2 windows.
- Live checks in throwaway iTerm2 windows (`spikes/`), run on 2026-10-18 with Claude Code 2.1.303 and Codex 0.163.0: `live_agent_sessions_check.py` passed all 12 checks. A Claude Code session showed "running" during a 20-second command, its tab was moved into another window by the dashboard's move action while the command ran, and it kept its session and finished the command (the screen showed the last number and the turn ended). A Codex session was listed as Codex, went from working back to idle, and got its context size and conversation ID. `live_list_actions_check.py` passed all 17 checks driving the list itself (the real list controller, fed real snapshots, in an off-screen window, connected to the real core; drags played back through the calls AppKit makes during a drag, so only AppKit's own mouse tracking is left out): the window menu's "New tab with Claude Code session…" started Claude Code in a new tab; a tab and a window were renamed from the list; a tab was dragged above its neighbor and iTerm2's tab order changed; a window was dragged above another in the list; a tab whose agent was running a 20-second command was dragged into another window, kept its session and finished the command; "Show tab" brought the tab and its window to the front. `core_actions_live_check.py` (rename, move, reorder, focus, new sessions), `status_rules_live_check.py` (asking ends 0.6 s after approval; stuck) and `window_geometry_live_check.py` (docked narrowing and restore; moves between two monitors simulated as screen halves) passed earlier. With real mouse events (computer control, `spikes/drag_test_windows.py` for the throwaway windows), in the running app's dropdown: a Claude Code tab dragged onto another window moved there with the same session (the emptied window closed), a tab dragged above its neighbor changed iTerm2's tab order, and press-and-hold showed a tab and put the previous one back on release with the dropdown still open. `settings_and_docked_mode_live_check.py` (2026-10-19) runs the real app objects with a temporary copy of the settings and changes the Settings window's controls the way clicks do; all 8 checks passed: floating mode saved and the sidebar stayed visible, the asking count turned off and saved, docked mode narrowed the real iTerm2 window out of the strip (2056 to 1764 points wide) and Auto-hide gave it back its exact frame. Not yet tried: a real second monitor (this Mac has only its built-in display) and moving a full-screen window to it.
- GitHub Actions on a macOS runner runs the unit tests.

## 18. Designed for later

These are not in version 1. Version 1 keeps the places they plug in.

### Introducing a window's agent sessions to each other

**Goal.** Right-click a window: "Introduce agent sessions". Each agent session in that window is told which other agent sessions share the window: their names, agent tools, folders, current previews, and how to message them. An optional setting does this automatically when a window gains or loses an agent session.

**What exists today.**
- Each Claude Code session already has a name (for example `profile-photo-3a`, the `name` field of `claude agents --json`) and its own messaging socket.
- Claude Code sessions can list each other and send each other messages with built-in tools, but nothing tells a session which others belong to the same project window. The introduction makes that explicit.
- Codex has `codex queue --thread <session> --message <text>` ("Queue a message for an existing session"). In Phase 0 it reached an idle interactive Codex session in an iTerm2 tab, which ran the message as a normal turn 1 to 9 seconds later. It is the delivery path for Codex, with no typing into the terminal and no relay, so the cost for Codex is Small.

**Design.**
- An `introduce_window_agent_sessions` action, flagged `types_into_a_session`. It follows the same rules as /compact (R15.1): at the prompt, empty input box, separate Enter, and `suppress_broadcast=True`.
- Each adapter gets an optional `peer_messaging` description:
  - **native** (Claude Code): the address is the session name, and the introduction text says to use Claude Code's messaging tools.
  - **queued** (Codex): the address is the Codex session ID (from any of its hook events), and the app delivers with `codex queue --thread <id> --message <text>`.
  - **through the dashboard's relay** (other tools): a small MCP server (Model Context Protocol, the standard way to give an agent extra tools, which Claude Code and Codex both support) shipped by this app, with tools such as `list_agent_sessions_in_my_window` and `send_message_to_agent_session`. The relay delivers a message by typing it into the target at its next idle moment, or holds it in a mailbox the target reads.
- A pure function, `build_introduction_text(target_session, peer_sessions, adapter)`, writes the message. Introductions are short and factual. Claude Code treats messages from other sessions as information, not as orders from the user.
- Version 1 already stores `peer_messaging_address` in each snapshot entry.
- **Risk:** automatic introductions type into sessions without a click. They should only ever happen at the prompt, at most once per window change, with a visible log in the hover panel.

### Several computers on one network

**Goal.** Dashboards on several computers find each other, pair, and show one shared tree with a computer level on top (computer 1 > monitor 1 > window 1 > tab 1; computer 2 > window 1 > tab 2). From any computer you can control another computer's agent sessions (focus, rename, /compact, /clear, interrupt, new agent session, introductions), but not move or drag tabs or windows between computers.

**Design.**
- **Discovery:** Bonjour (macOS's built-in local network discovery, available through PyObjC as `NSNetService`), advertising a service type such as `_llm-sidebar._tcp`.
- **Pairing:** the first connection shows a short code on both screens to confirm. Each computer then stores the other's public key and trusts only that.
- **Transport:** encrypted (TLS with the paired keys), carrying the same JSON snapshots and action requests as the local boundary.
- **Permissions:** per paired computer, view only or view and control. Only actions flagged `may_come_from_another_computer` are accepted. Each remote action is logged and shown.
- **Merge:** each computer's `ComputerSnapshot` keeps its own `computer_id`. The UI's top level becomes computers when more than one is present. Drags never cross a computer boundary.
- **Off by default:** a setting turns on network sharing. With it off, nothing listens on the network.

**Warnings.**
- This is the largest feature considered so far, about as big as version 1's core.
- A network service that can type into terminals is a serious security exposure; it needs a security review before release.
- On a work-managed computer, it may need the IT department's approval.
- Claude Code's Remote Control already offers viewing and answering sessions from another device, which covers part of the need with no new code.

### Web page or phone app

**Design.**
- A local web server (off by default) serves the snapshot stream over WebSocket and accepts action requests, using the same flags.
- A web page can render the same pyinstrument-style tree.
- A phone app can use the same protocol over the multi-computer pairing above, rather than a separate scheme.
- Version 1 work that makes this cheap: the core and views boundary, and JSON-ready snapshots and requests.
