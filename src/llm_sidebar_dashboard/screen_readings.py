"""What the dashboard reads from an agent's screen (PLAN.md section 6, sources 3 and 4). Pure functions.

- Whether a permission dialog is showing: Claude Code reports nothing when the user answers a
  permission prompt, and iTerm2's status stays "waiting" until the approved tool finishes
  (Phase 0), so "asking" ends when the dialog is gone from the screen.
- A fingerprint of the screen with the lines that change while nothing happens masked out
  (spinners, elapsed times, token counts, clocks), for "stuck" detection.
"""

import hashlib
import re

LINES_IN_FINGERPRINT = 20
# Changes in every redraw of any tool: clocks (also in status lines) and braille-dot animations.
COMMON_PROGRESS_NOISE_PATTERNS = [
    re.compile(r"\d{1,2}:\d{2}(?::\d{2})?\s?(?:AM|PM)?"),
    re.compile(r"[⠀-⣿]"),
]


def clean_screen_line(line):
    """Returns a screen line with iTerm2's empty cells (NUL characters) turned into spaces."""
    return line.replace("\x00", " ")


def screen_shows_permission_dialog(screen_lines, permission_dialog_screen_patterns):
    """Returns whether every pattern of the adapter's permission dialog appears in the last 30
    non-blank lines (a fresh or cleared tab leaves blank rows below the dialog).

    Args:
        screen_lines: The visible screen's lines.
        permission_dialog_screen_patterns: Compiled patterns that must all match some line.
    """
    bottom_lines = [line for line in map(clean_screen_line, screen_lines) if line.strip()][-30:]
    return all(any(pattern.search(line) for line in bottom_lines) for pattern in permission_dialog_screen_patterns)


def count_lines_matching(screen_lines, pattern):
    """Returns how many screen lines match pattern (0 when pattern is None)."""
    if pattern is None:
        return 0
    return sum(1 for line in screen_lines if pattern.search(clean_screen_line(line)))


def compute_screen_fingerprint(screen_lines, progress_noise_patterns):
    """Returns a hash of the last non-blank lines with the adapter's progress noise removed.

    Args:
        screen_lines: The visible screen's lines.
        progress_noise_patterns: Compiled patterns for text that changes without real progress.
    """
    patterns = list(progress_noise_patterns) + COMMON_PROGRESS_NOISE_PATTERNS
    cleaned_lines = []
    for line in screen_lines:
        line = clean_screen_line(line)
        for pattern in patterns:
            line = pattern.sub(" ", line)
        line = " ".join(line.split())  # removed noise and a blinking bullet also shift the spacing
        if line:
            cleaned_lines.append(line)
    return hashlib.sha256("\n".join(cleaned_lines[-LINES_IN_FINGERPRINT:]).encode()).hexdigest()
