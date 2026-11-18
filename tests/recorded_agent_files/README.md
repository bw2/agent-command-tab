# Recorded agent files

Files that the agent tools really wrote, for `tests/recorded_agent_files_tests.py`: the app reads
these formats from programs it doesn't control, so its readers are tested on what those programs
actually write, not only on records written by hand for a test.

- `claude_code/<version>/transcript.jsonl`: a Claude Code conversation's transcript, and
  `hook_payloads.jsonl`: the payloads, one per line in the order they came, of the hooks the app
  installs (`hooks_installer.HOOK_EVENTS_BY_AGENT_TOOL_NAME`).
- `codex/<version>/rollout.jsonl`: a Codex conversation file.
- `agy/<version>/transcript_full.jsonl`: an agy conversation's whole transcript, which the app
  reads, and `transcript.jsonl`: the one agy keeps beside it and cuts long texts short in, which
  the app reads when there is no whole one.

Each comes from the same short conversation, which `dev/record_agent_conversations.py` holds with
each agent tool in a throwaway folder: write a file, run a shell command, reply, then run another
command in a second turn (Claude Code: one of them in the background; Codex: one turn only). They are
recorded through each tool's non-interactive mode (`claude -p`, `codex exec`, `agy -p`), which
writes the same kinds of conversation records as a session in a terminal tab. Codex's header (its
first record) is the exception: `codex exec` writes `"source": "exec"` there, where a session in a
terminal tab, which Codex 0.165 and later run through their shared background server, has
`"source": "vscode"` with the originator `codex-tui`. The app goes by that header to find a tab's conversation file and to
tell that the tab's turns run outside it. Neither is tested on this recording, whose header is not a
terminal tab's: all the tests check about its header is that it is not taken for one whose turns run
in the shared server.

The script removes what would identify the person who recorded them. The throwaway folder's path
and the home folder are replaced by `/Users/me/project` and `/Users/me`. From Claude Code's
transcript and Codex's conversation file, the instructions sent to
the model (the tool's own and the person's: their CLAUDE.md or AGENTS.md, skills, tools), the
context Claude Code attached (all but its type), the commands of the hooks that ran, and the
account's plan and usage are left out or replaced by
`(removed by dev/record_agent_conversations.py)`. The account IDs are replaced by zeros (`user-0`,
`00000000-0000-0000-0000-000000000000`), and the time zone Codex tells the model by `UTC`. Each
local time with a UTC offset (agy writes its times that way) is written as the same moment in UTC.
The records of agy's transcript and of Claude Code's hook payloads are changed in no other way
than those folders and times: agy's transcript keeps what agy itself adds for the model (the
current time in each prompt's record, a note about a changed setting in the first one's, and a
`SYSTEM_MESSAGE` record).

As a last check, the script saves nothing while a file still holds the person's user name, the
Mac's name, an email address, an API key that starts with `sk-`, or a line of 40 characters or
more from their own instruction files (`~/.claude/CLAUDE.md`, `~/.codex/AGENTS.md`,
`~/.gemini/GEMINI.md`), whether as written or with its quotes and backslashes escaped the way
JSON holds them. In that case it names what it found and, once it has gone through the other
agent tools, ends with exit status 1. It does not look for shorter lines, or for instructions
kept in other files (a project's CLAUDE.md, a skill).
`recorded_agent_files_tests.py` checks the saved files for home folders, email addresses and API
keys.

A recording is never edited by hand: a new version of an agent tool that writes its files
differently gets a new recording, in a folder of its own, from the script. The script leaves out
an agent tool that isn't installed, and a version that has a recording already: the tests pin
what that recording holds (its replies, times and token counts), and another conversation would
hold other ones. `--replace` records such a version again, after which the tests of that
recording need the new values.
