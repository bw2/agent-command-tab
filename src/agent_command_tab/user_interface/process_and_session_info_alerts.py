"""A sub row's Info window, what is known about a process an agent started, and a tab row's
Session info window, what is known about its agent session: read-only, in text that can be
selected and copied."""

import time

from AppKit import (NSAlert, NSAttributedString, NSColor, NSFont, NSFontAttributeName, NSFontWeightRegular,
                    NSForegroundColorAttributeName, NSMakeRect, NSMutableAttributedString, NSMutableParagraphStyle,
                    NSParagraphStyleAttributeName, NSScrollView, NSTextAlignmentLeft, NSTextTab, NSView)

from agent_command_tab.context_window_usage import format_token_count
from agent_command_tab.user_interface.control_helpers import make_text_view_that_wraps_lines
from agent_command_tab.user_interface.row_text_rendering import format_memory_size, split_elapsed_time_into_counts_and_units

ACCESSORY_WIDTH = 560
LARGEST_TEXT_HEIGHT = 380  # a longer command scrolls
ROOM_ABOVE_THE_BUTTONS = 16  # between the text and OK
GAP_BEFORE_VALUES = 12
GAP_BETWEEN_FIELDS = 8
LONGEST_COMMAND_IN_AN_ALERT = 100  # characters of a command named in the Kill question


def shorten_command_for_alert(command_text):
    """Returns a command on one line, cut to LONGEST_COMMAND_IN_AN_ALERT characters with "…" when longer."""
    one_line_command = " ".join(command_text.split())
    if len(one_line_command) <= LONGEST_COMMAND_IN_AN_ALERT:
        return one_line_command
    return one_line_command[:LONGEST_COMMAND_IN_AN_ALERT - 1] + "…"


def format_date_and_time(timestamp):
    """Returns a local date and time such as "Oct 29, 7:14:47 AM"."""
    moment = time.localtime(timestamp)
    return f"{time.strftime('%b %d', moment).replace(' 0', ' ')}, {time.strftime('%I:%M:%S %p', moment).lstrip('0')}"


def format_elapsed_time_in_words(elapsed_seconds):
    """Returns an elapsed time with its units spelled out, to the precision the list's columns
    give it: "45 seconds", "1 minute", "1 hour 5 minutes" or "3 days"."""
    return " ".join(format_count_with_unit(count, unit) for count, unit in split_elapsed_time_into_counts_and_units(elapsed_seconds))


def format_count_with_unit(count, unit):
    """Returns a count and its unit, plural unless the count is 1, such as "1 day" or "3 days"."""
    return f"{count} {unit}" if count == 1 else f"{count} {unit}s"


def list_cpu_and_memory_lines(details):
    """Returns the CPU, CPU time and Memory lines of both windows: those of the process and every
    process below it together, as the row's columns count them, labeled so when it has subprocesses.

    Args:
        details: The details of a read_child_process_details or read_agent_session_details result.
    """
    total_label = " (total, including subprocesses)" if details["subprocesses"] else ""
    return [
        ("CPU", f"{details['cpu_percent']}%{total_label}"),
        ("CPU time", f"{format_elapsed_time_in_words(details['cpu_seconds'])}{total_label}"),
        ("Memory", f"{format_memory_size(details['memory_bytes'])}{total_label}"),
    ]


def list_process_info_lines(details, now):
    """Returns [(label, value)] for the Info window, in the order shown. The process's
    subprocesses are listed, each with its ID, and CPU and memory are their total with it, as
    the row's columns count them, labeled so.

    Args:
        details: The details of a read_child_process_details result
            (DashboardCore.handle_read_child_process_details).
        now: The current time, for how long ago the process started.
    """
    subprocesses = details["subprocesses"]
    lines = [
        ("Process ID", str(details["process_id"])),
        ("Started", f"{format_date_and_time(details['started_at'])} ({format_elapsed_time_in_words(now - details['started_at'])} ago)"),
        ("Working directory", details["working_folder"] or "unknown"),
    ]
    if subprocesses:
        lines.append(("Subprocesses", "\n".join(
            f"{shorten_command_for_alert(subprocess['command_text'])} ({subprocess['process_id']})" for subprocess in subprocesses)))
    lines += list_cpu_and_memory_lines(details)
    if details["agent_command"]:  # a Claude Code shell: the command, then the shell's own command line around it
        lines.append(("Command", details["agent_command"]))
    lines.append(("Full command line", details["command_line"]))
    return lines


def list_session_info_lines(agent_session_entry, details, now):
    """Returns [(label, value)] for a tab row's Session info window, in the order shown: first the
    session's name, which other Claude Code sessions message it by, with its agent's process ID,
    then its age and what else is known about it. CPU and memory are those of the agent and every
    process below it, as the row's columns count them.

    Args:
        agent_session_entry: The tab row's AgentSessionEntry.
        details: The details of a read_agent_session_details result
            (DashboardCore.handle_read_agent_session_details).
        now: The current time, for the session's age.
    """
    process_id_text = f"process ID {details['process_id']}"
    lines = [
        ("Session", f"{agent_session_entry.agent_session_name} ({process_id_text})" if agent_session_entry.agent_session_name
         else process_id_text),
        ("Age", f"{format_elapsed_time_in_words(now - details['started_at'])} (started {format_date_and_time(details['started_at'])})"),
        ("Agent tool", agent_session_entry.agent_tool_name),
        ("Tab", f'{details["tab_kind_name"]} "{agent_session_entry.tab_title}"'),
        ("Working directory", agent_session_entry.working_folder or details["working_folder"] or "unknown"),
        ("Conversation ID", details["conversation_id"] or "unknown"),
        ("Conversation file", details["conversation_file"] or "unknown"),
    ]
    if details["claude_config_folder"]:  # not the default folder
        lines.append(("Claude Code config folder", details["claude_config_folder"]))
    if details["codex_home_folder"]:
        lines.append(("CODEX_HOME", details["codex_home_folder"]))
    if agent_session_entry.context_tokens:
        lines.append(("Context", f"{format_token_count(agent_session_entry.context_tokens)} tokens" + (
            f" ({agent_session_entry.context_fill_percent}% of the context window)"
            if agent_session_entry.context_fill_percent is not None else "")))
    return lines + list_cpu_and_memory_lines(details) + [("Command line", details["command_line"])]


def build_info_text(lines, font):
    """Returns the lines as an attributed string: gray labels, then each value from the same
    column, its wrapped and later lines (a command with a heredoc has several) lined up under its first."""
    value_x = max(NSAttributedString.alloc().initWithString_attributes_(label, {NSFontAttributeName: font}).size().width
                  for label, _ in lines) + GAP_BEFORE_VALUES
    first_line_style = NSMutableParagraphStyle.alloc().init()
    first_line_style.setTabStops_([NSTextTab.alloc().initWithTextAlignment_location_options_(NSTextAlignmentLeft, value_x, {})])
    first_line_style.setHeadIndent_(value_x)
    first_line_style.setParagraphSpacingBefore_(GAP_BETWEEN_FIELDS)
    later_line_style = first_line_style.mutableCopy()
    later_line_style.setFirstLineHeadIndent_(value_x)
    later_line_style.setParagraphSpacingBefore_(0)
    text = NSMutableAttributedString.alloc().init()
    for index, (label, value) in enumerate(lines):
        first_value_line, *later_value_lines = value.split("\n")
        pieces = [("\n" if index else "", NSColor.labelColor(), first_line_style),
                  (label + "\t", NSColor.secondaryLabelColor(), first_line_style),
                  (first_value_line, NSColor.labelColor(), first_line_style)]
        pieces += [("\n" + later_value_line, NSColor.labelColor(), later_line_style) for later_value_line in later_value_lines]
        for piece, color, paragraph_style in pieces:
            text.appendAttributedString_(NSAttributedString.alloc().initWithString_attributes_(piece, {
                NSFontAttributeName: font, NSForegroundColorAttributeName: color, NSParagraphStyleAttributeName: paragraph_style}))
    return text


def build_process_info_alert(details, now):
    """Returns a sub row's Info window (build_info_alert).

    Args:
        details, now: See list_process_info_lines.
    """
    return build_info_alert("Process info", list_process_info_lines(details, now))


def build_session_info_alert(agent_session_entry, details, now):
    """Returns a tab row's Session info window (build_info_alert).

    Args:
        agent_session_entry, details, now: See list_session_info_lines.
    """
    return build_info_alert("Session info", list_session_info_lines(agent_session_entry, details, now))


def build_info_alert(title, lines):
    """Returns an Info window: an alert with OK and the lines (build_info_text) in a text view
    that can be selected but not edited, as tall as its text up to LARGEST_TEXT_HEIGHT, with
    ROOM_ABOVE_THE_BUTTONS below it.

    Args:
        title: The alert's title, such as "Process info".
        lines: [(label, value)], in the order shown.
    """
    alert = NSAlert.alloc().init()
    alert.setMessageText_(title)
    text_view = make_text_view_that_wraps_lines(ACCESSORY_WIDTH, LARGEST_TEXT_HEIGHT)
    text_view.setEditable_(False)
    text_view.setSelectable_(True)
    text_view.setDrawsBackground_(False)
    font = NSFont.monospacedSystemFontOfSize_weight_(NSFont.smallSystemFontSize(), NSFontWeightRegular)
    text_view.textStorage().setAttributedString_(build_info_text(lines, font))
    text_view.layoutManager().ensureLayoutForTextContainer_(text_view.textContainer())
    text_height = (text_view.layoutManager().usedRectForTextContainer_(text_view.textContainer()).size.height
                   + 2 * text_view.textContainerInset().height)
    scroll_view_height = min(text_height, LARGEST_TEXT_HEIGHT)
    scroll_view = NSScrollView.alloc().initWithFrame_(NSMakeRect(0, ROOM_ABOVE_THE_BUTTONS, ACCESSORY_WIDTH, scroll_view_height))
    scroll_view.setDrawsBackground_(False)
    scroll_view.setHasVerticalScroller_(True)
    scroll_view.setAutohidesScrollers_(True)
    text_view.setFrame_(NSMakeRect(0, 0, scroll_view.contentSize().width, text_height))
    scroll_view.setDocumentView_(text_view)
    accessory_view = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, ACCESSORY_WIDTH, scroll_view_height + ROOM_ABOVE_THE_BUTTONS))
    accessory_view.addSubview_(scroll_view)
    alert.setAccessoryView_(accessory_view)
    alert.layout()
    return alert
