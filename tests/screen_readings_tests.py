"""Screens recorded in Phase 0 (Claude Code 2.1.302, Codex 0.163.0), as iTerm2 returns them (NULs for empty cells)."""

import unittest

from llm_sidebar_dashboard.agent_adapters.claude_code import ClaudeCodeAdapter
from llm_sidebar_dashboard.agent_adapters.codex import CodexAdapter
from llm_sidebar_dashboard.screen_readings import (compute_screen_fingerprint, count_lines_matching,
                                                   screen_shows_permission_dialog)

CLAUDE_LONG_TURN_AT_6_SECONDS = [
    " ▐▛███▛█\x00\x00\x00Claude Code\x00v2.1.302", "▝▜██████▀\x00\x00Haiku 5.5 · Claude Max",
    "❯ Use the Bash tool to run exactly this command: python3 -c \"import time;       ",
    "  time.sleep(45)\"                                                               ",
    "⏺\x00Sleeping 45 seconds via python3 · 4s", "  ⎿  $ python3 -c \"import time; time.sleep(45)\" (4s)",
    "\x00\x00\x00\x00\x00(ctrl+b to run in background)", "✳\x00Thinking… (5s · ↓ 97 tokens)",
    "──────────────────────────────────────────── phase0-test-claude-slash-commands ─", "❯ ",
    "────────────────────────────────────────────────────────────────────────────────",
    "\x00\x00[10/18/26 03:52:35 PM]  news-MacBook-Pro:/private/tmp/claude-502/-Users-wei…", "\x00\x00⏸ manual mode on",
]
CLAUDE_LONG_TURN_AT_9_SECONDS = [
    " ▐▛███▛█\x00\x00\x00Claude Code\x00v2.1.302", "▝▜██████▀\x00\x00Haiku 5.5 · Claude Max",
    "❯ Use the Bash tool to run exactly this command: python3 -c \"import time;       ",
    "  time.sleep(45)\"                                                               ",
    " \x00Sleeping 45 seconds via python3 · 7s", "  ⎿  $ python3 -c \"import time; time.sleep(45)\" (7s)",
    "\x00\x00\x00\x00\x00(ctrl+b to run in background)", "✶\x00Thinking… (9s · ↓ 97 tokens)",
    "──────────────────────────────────────────── phase0-test-claude-slash-commands ─", "❯ ",
    "────────────────────────────────────────────────────────────────────────────────",
    "\x00\x00[10/18/26 03:52:41 PM]  news-MacBook-Pro:/private/tmp/claude-502/-Users-wei…", "\x00\x00⏸ manual mode on",
]
CLAUDE_PERMISSION_PROMPT = [
    " Bash command", "\x00Run perl command that sleeps 10s then prints marker",
    "\x00perl\x00-e\x00'sleep\x0010;\x00print\x00\"phase0-A-done\\n\"'", "\x00This\x00command\x00requires\x00approval",
    " Do you want to proceed?", " ❯ 1. Yes                                                   ",
    "   2. Yes, and don’t ask again for: perl *", "   4. No", "\x00Esc to cancel · Tab to amend",
]
CODEX_LONG_TURN_AT_6_SECONDS = [
    "\x00\x00>_ OpenAI Codex (v0.163.0)", "› Run exactly this shell command inside the sandbox (it needs no escalation):",
    "  sleep 45", "•\x00Working\x00(5s • esc to interrupt) · 1 background terminal running · /ps to view…",
    "› Ask Codex to do anything", "\x00\x00GPT-6-Astra low fast · /private/tmp/claude-502/-Users-weisburd-project-base6-…",
]
CODEX_LONG_TURN_AT_9_SECONDS = [
    "\x00\x00>_ OpenAI Codex (v0.163.0)", "› Run exactly this shell command inside the sandbox (it needs no escalation):",
    "  sleep 45", "•\x00Working\x00(8s • esc to interrupt) · 1 background terminal running · /ps to view…",
    "›⠁Ask Codex to do anything⡀", "\x00\x00GPT-6-Astra low fast · /private/tmp/claude-502/-Users-weisburd-project-base6-…",
]


class FingerprintTests(unittest.TestCase):
    def test_claude_code_spinner_counters_bullet_and_clock_are_not_progress(self):
        noise = ClaudeCodeAdapter.progress_noise_patterns
        self.assertEqual(compute_screen_fingerprint(CLAUDE_LONG_TURN_AT_6_SECONDS, noise),
                         compute_screen_fingerprint(CLAUDE_LONG_TURN_AT_9_SECONDS, noise))

    def test_codex_elapsed_time_and_braille_animation_are_not_progress(self):
        noise = CodexAdapter.progress_noise_patterns
        self.assertEqual(compute_screen_fingerprint(CODEX_LONG_TURN_AT_6_SECONDS, noise),
                         compute_screen_fingerprint(CODEX_LONG_TURN_AT_9_SECONDS, noise))

    def test_new_output_is_progress(self):
        noise = ClaudeCodeAdapter.progress_noise_patterns
        with_new_output = CLAUDE_LONG_TURN_AT_9_SECONDS[:7] + ["  ⎿  test_parser.py PASSED"] + CLAUDE_LONG_TURN_AT_9_SECONDS[7:]
        self.assertNotEqual(compute_screen_fingerprint(CLAUDE_LONG_TURN_AT_9_SECONDS, noise),
                            compute_screen_fingerprint(with_new_output, noise))


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


if __name__ == "__main__":
    unittest.main()
