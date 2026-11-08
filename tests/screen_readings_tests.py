"""Screens recorded from Claude Code 2.1.302 and Codex 0.163.0, as iTerm2 returns them (NULs for empty cells)."""

import unittest

from agent_command_tab.agent_adapters.claude_code import ClaudeCodeAdapter
from agent_command_tab.agent_adapters.codex import CodexAdapter
from agent_command_tab.screen_readings import (count_lines_matching, input_area_is_empty, read_typed_input_area_text,
                                               screen_shows_permission_dialog)

CLAUDE_LONG_TURN_AT_6_SECONDS = [
    " ▐▛███▛█\x00\x00\x00Claude Code\x00v2.1.302", "▝▜██████▀\x00\x00Haiku 5.5 · Claude Max",
    "❯ Use the Bash tool to run exactly this command: python3 -c \"import time;       ",
    "  time.sleep(45)\"                                                               ",
    "⏺\x00Sleeping 45 seconds via python3 · 4s", "  ⎿  $ python3 -c \"import time; time.sleep(45)\" (4s)",
    "\x00\x00\x00\x00\x00(ctrl+b to run in background)", "✳\x00Thinking… (5s · ↓ 97 tokens)",
    "──────────────────────────────────────────── test-claude-slash-commands ─", "❯ ",
    "────────────────────────────────────────────────────────────────────────────────",
    "\x00\x00[10/18/26 03:52:35 PM]  someones-MacBook:/private/tmp/claude-502/-Users-som…", "\x00\x00⏸ manual mode on",
]
CLAUDE_PERMISSION_PROMPT = [
    " Bash command", "\x00Run perl command that sleeps 10s then prints marker",
    "\x00perl\x00-e\x00'sleep\x0010;\x00print\x00\"A-done\\n\"'", "\x00This\x00command\x00requires\x00approval",
    " Do you want to proceed?", " ❯ 1. Yes                                                   ",
    "   2. Yes, and don’t ask again for: perl *", "   4. No", "\x00Esc to cancel · Tab to amend",
]
CODEX_LONG_TURN_AT_6_SECONDS = [
    "\x00\x00>_ OpenAI Codex (v0.163.0)", "› Run exactly this shell command inside the sandbox (it needs no escalation):",
    "  sleep 45", "•\x00Working\x00(5s • esc to interrupt) · 1 background terminal running · /ps to view…",
    "› Ask Codex to do anything", "\x00\x00GPT-6-Astra low fast · /private/tmp/claude-502/-Users-someone-project-base6-…",
]


class PermissionDialogTests(unittest.TestCase):
    def test_claude_code_dialog(self):
        patterns = ClaudeCodeAdapter.permission_dialog_screen_patterns
        self.assertTrue(screen_shows_permission_dialog(CLAUDE_PERMISSION_PROMPT, patterns))
        self.assertFalse(screen_shows_permission_dialog(CLAUDE_LONG_TURN_AT_6_SECONDS, patterns))

    def test_claude_code_edit_and_fetch_dialogs(self):
        patterns = ClaudeCodeAdapter.permission_dialog_screen_patterns
        for question in ("Do you want to make this edit to parser.py?", "Do you want to allow Claude to fetch this content?"):
            screen = [" Edit file", f" {question}", " ❯ 1. Yes", "   2. No", "\x00Esc to cancel · Tab to amend"]
            self.assertTrue(screen_shows_permission_dialog(screen, patterns), question)

    def test_dialog_near_the_top_of_a_tall_mostly_blank_screen(self):
        # A fresh or cleared tab: Claude Code draws from the top and the rows below stay empty.
        screen = CLAUDE_PERMISSION_PROMPT + ["\x00" * 80] * 50
        self.assertTrue(screen_shows_permission_dialog(screen, ClaudeCodeAdapter.permission_dialog_screen_patterns))

    def test_counting_interrupted_turn_lines(self):
        pattern = ClaudeCodeAdapter.interrupted_turn_screen_pattern
        screen = ["⏺ Ran 1 shell command", "  ⎿  Interrupted · What should Claude do instead?", "❯ "]
        self.assertEqual(count_lines_matching(screen, pattern), 1)
        self.assertEqual(count_lines_matching(screen, None), 0)

    def test_no_patterns_means_nothing_to_check(self):
        self.assertTrue(screen_shows_permission_dialog(CODEX_LONG_TURN_AT_6_SECONDS, ()))


def mark_faint(text, faint_flags_as_letters=""):
    """Returns (text, faint_flags), the flags written as letters: "f" faint, "n" not."""
    return text, [letter == "f" for letter in faint_flags_as_letters.ljust(len(text), "n")]


CLAUDE_INPUT_BOX_PLACEHOLDER = [
    mark_faint("──────────────────────────────────────────── test-claude-slash-commands ─"),
    mark_faint('❯\xa0Try "refactor <filepath>"', "nn" + "f" * 25),
    mark_faint("─" * 80),
    mark_faint("  ⏸ manual mode on · ← for agents"),
]
CODEX_INPUT_BOX_PLACEHOLDER = [
    mark_faint("⠁   ⠈         ⠄          ⢀                       ⠐ ⠐"),
    mark_faint("›⠁Ask Codex to do anything\x00\x00\x00⠈\x00\x00⠂", "nn" + "f" * 24),
    mark_faint("        ⠐        ⠄                                    ⠄⠂"),
    mark_faint("  GPT-6-Astra low fast · /private/tmp/…/codex_test_project"),
    mark_faint("  ? for shortcuts"),
]


class InputAreaIsEmptyTests(unittest.TestCase):
    def test_only_a_box_holding_no_text_is_empty(self):  # what read_typed_input_area_text reads it as: "", text, or None
        claude_draft = list(CLAUDE_INPUT_BOX_PLACEHOLDER)
        claude_draft[1] = mark_faint("❯\xa0draft text")
        adapter = ClaudeCodeAdapter()
        self.assertEqual([input_area_is_empty(screen, adapter.input_line_marker, adapter.input_area_end_pattern)
                          for screen in (CLAUDE_INPUT_BOX_PLACEHOLDER, claude_draft, CLAUDE_INPUT_BOX_PLACEHOLDER[:2])],
                         [True, False, False])  # a box that can't be read (no lower edge) fails safe


class ReadTypedInputAreaTextTests(unittest.TestCase):
    def read(self, adapter, screen_lines_with_faint_flags):
        return read_typed_input_area_text(screen_lines_with_faint_flags, adapter.input_line_marker, adapter.input_area_end_pattern)

    def test_a_command_left_in_the_box_is_read_without_the_placeholder(self):
        self.assertEqual(self.read(ClaudeCodeAdapter(), CLAUDE_INPUT_BOX_PLACEHOLDER), "")
        self.assertEqual(self.read(CodexAdapter(), CODEX_INPUT_BOX_PLACEHOLDER), "")
        # An Enter taken as a new line leaves the command on the first of two lines.
        screen = CLAUDE_INPUT_BOX_PLACEHOLDER[:1] + [mark_faint("❯\xa0/compact"), mark_faint("  ")] + CLAUDE_INPUT_BOX_PLACEHOLDER[2:]
        self.assertEqual(self.read(ClaudeCodeAdapter(), screen), "/compact")

    def test_a_typed_draft_is_read_also_from_a_continuation_line(self):  # without its spaces
        claude_draft = list(CLAUDE_INPUT_BOX_PLACEHOLDER)
        claude_draft[1] = mark_faint("❯\xa0draft text")
        self.assertEqual(self.read(ClaudeCodeAdapter(), claude_draft), "drafttext")
        codex_draft = [CODEX_INPUT_BOX_PLACEHOLDER[0], mark_faint("› draft text"), CODEX_INPUT_BOX_PLACEHOLDER[3]]
        self.assertEqual(self.read(CodexAdapter(), codex_draft), "drafttext")
        screen = CLAUDE_INPUT_BOX_PLACEHOLDER[:1] + [mark_faint("❯\xa0"), mark_faint("  second line of the draft")] + \
            CLAUDE_INPUT_BOX_PLACEHOLDER[2:]
        self.assertEqual(self.read(ClaudeCodeAdapter(), screen), "secondlineofthedraft")

    def test_an_earlier_prompt_in_the_transcript_doesnt_count(self):
        screen = [mark_faint("❯ an earlier prompt"), mark_faint("⏺ the reply")] + CLAUDE_INPUT_BOX_PLACEHOLDER
        self.assertEqual(self.read(ClaudeCodeAdapter(), screen), "")

    def test_a_box_that_is_not_on_screen_reads_as_none(self):
        self.assertIsNone(self.read(ClaudeCodeAdapter(), [mark_faint("✻ Compacting conversation…")]))
        self.assertIsNone(self.read(ClaudeCodeAdapter(), [mark_faint("⏺ a reply with no input box below it")]))
        self.assertIsNone(self.read(ClaudeCodeAdapter(), CLAUDE_INPUT_BOX_PLACEHOLDER[:2]))  # no lower edge on screen


if __name__ == "__main__":
    unittest.main()
