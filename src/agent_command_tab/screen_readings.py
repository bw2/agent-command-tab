"""What the dashboard reads from an agent's screen. Pure functions.

- Whether a permission dialog is showing: Claude Code reports nothing when the user answers a
  permission prompt, and iTerm2's status stays "waiting" until the approved tool finishes, so
  "asking" ends when the dialog is gone from the screen.
"""

import re

LONGEST_INPUT_AREA_LINES = 12  # a draft longer than this counts as not empty
# Cells an empty input box may hold besides faint text: blanks, iTerm2's empty cells, a
# no-break space, and Codex's braille-dot idle animation.
EMPTY_INPUT_CELL_PATTERN = re.compile(r"[ \x00 ⠀-⣿]")


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


def read_typed_input_area_text(screen_lines_with_faint_flags, input_line_marker, input_area_end_pattern):
    """Returns the text typed into the agent's input box, or None when the box can't be found on
    screen.

    The input line is the last line starting with the marker; the input area runs from it to
    the line just below it that matches input_area_end_pattern (a multi-line draft continues on
    the lines in between). Typed text is what follows the marker except faint cells (the tool's
    placeholder or suggested prompt) and EMPTY_INPUT_CELL_PATTERN cells, with the area's lines
    joined by newlines and empty lines at the start and end left out.

    Args:
        screen_lines_with_faint_flags: For each visible line, (text, faint_flags): its
            characters and, for each, whether it is drawn faint.
        input_line_marker: The character that starts the input line ("❯", "›").
        input_area_end_pattern: Compiled pattern of the line just below the input area.
    """
    marker_line_indexes = [index for index, (text, _) in enumerate(screen_lines_with_faint_flags)
                           if text.lstrip(" \x00").startswith(input_line_marker)]
    if not marker_line_indexes:
        return None
    input_line_index = marker_line_indexes[-1]
    last_index_to_look_at = min(input_line_index + LONGEST_INPUT_AREA_LINES, len(screen_lines_with_faint_flags) - 1)
    end_index = next((index for index in range(input_line_index + 1, last_index_to_look_at + 1)
                      if input_area_end_pattern.search(clean_screen_line(screen_lines_with_faint_flags[index][0]))), None)
    if end_index is None:
        return None
    typed_lines = []
    for index in range(input_line_index, end_index):
        text, faint_flags = screen_lines_with_faint_flags[index]
        first_input_position = text.index(input_line_marker) + 1 if index == input_line_index else 0
        typed_lines.append("".join(
            text[position] for position in range(first_input_position, len(text))
            if not (position < len(faint_flags) and faint_flags[position]) and not EMPTY_INPUT_CELL_PATTERN.match(text[position])))
    return "\n".join(typed_lines).strip("\n")


def input_area_is_empty(screen_lines_with_faint_flags, input_line_marker, input_area_end_pattern):
    """Returns whether the agent's input box holds no typed text, so a command typed into it
    can't join a draft (read_typed_input_area_text). Fails safe: False when the box can't be
    found on screen."""
    return read_typed_input_area_text(screen_lines_with_faint_flags, input_line_marker, input_area_end_pattern) == ""


def count_lines_matching(screen_lines, pattern):
    """Returns how many screen lines match pattern (0 when pattern is None)."""
    if pattern is None:
        return 0
    return sum(1 for line in screen_lines if pattern.search(clean_screen_line(line)))
