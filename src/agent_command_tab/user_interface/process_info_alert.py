"""A sub row's Info window (user request, 2026-10-29): what is known about a process an agent
started, read-only, in text that can be selected and copied."""

import time

from AppKit import (NSAlert, NSAttributedString, NSColor, NSFont, NSFontAttributeName, NSFontWeightRegular,
                    NSForegroundColorAttributeName, NSMakeRect, NSMutableAttributedString, NSMutableParagraphStyle,
                    NSParagraphStyleAttributeName, NSScrollView, NSTextAlignmentLeft, NSTextTab, NSTextView)

from agent_command_tab.user_interface.row_text_rendering import format_elapsed_time, format_memory_size

ACCESSORY_WIDTH = 560
LARGEST_ACCESSORY_HEIGHT = 380  # a longer command scrolls
GAP_BEFORE_VALUES = 12
GAP_BETWEEN_FIELDS = 8
LONGEST_COMMAND_IN_AN_ALERT = 100  # characters of a command named in the Kill question
LARGEST_TEXT_VIEW_SIZE = 1e7


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


def list_process_info_lines(details, now):
    """Returns [(label, value)] for the Info window, in the order shown. The process's
    subprocesses are listed, each with its ID, and CPU and memory are their total with it, as
    the row's columns count them, labeled so (user requests, 2026-10-29).

    Args:
        details: The details of a read_child_process_details result
            (DashboardCore.handle_read_child_process_details).
        now: The current time, for how long ago the process started.
    """
    subprocesses = details["subprocesses"]
    total_label = " (total, including subprocesses)" if subprocesses else ""
    lines = [
        ("Process ID", str(details["process_id"])),
        ("Started", f"{format_date_and_time(details['started_at'])} ({format_elapsed_time(now - details['started_at'])} ago)"),
        ("Working directory", details["working_folder"] or "unknown"),
    ]
    if subprocesses:
        lines.append(("Subprocesses", "\n".join(
            f"{shorten_command_for_alert(subprocess['command_text'])} ({subprocess['process_id']})" for subprocess in subprocesses)))
    lines += [
        ("CPU", f"{details['cpu_percent']}%{total_label}"),
        ("CPU time", f"{format_elapsed_time(details['cpu_seconds'])}{total_label}"),
        ("Memory", f"{format_memory_size(details['memory_bytes'])}{total_label}"),
    ]
    if details["agent_command"]:  # a Claude Code shell: the command, then the shell's own command line around it
        lines.append(("Command", details["agent_command"]))
    lines.append(("Full command line", details["command_line"]))
    return lines


def build_process_info_text(lines, font):
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
    """Returns the Info window: an alert with OK and the process's details in a text view that
    can be selected but not edited, as tall as its text up to LARGEST_ACCESSORY_HEIGHT.

    Args:
        details, now: See list_process_info_lines.
    """
    alert = NSAlert.alloc().init()
    alert.setMessageText_("Process info")
    text_view = NSTextView.alloc().initWithFrame_(NSMakeRect(0, 0, ACCESSORY_WIDTH, LARGEST_ACCESSORY_HEIGHT))
    text_view.setEditable_(False)
    text_view.setSelectable_(True)
    text_view.setDrawsBackground_(False)
    text_view.setVerticallyResizable_(True)
    text_view.setHorizontallyResizable_(False)
    text_view.setMaxSize_((LARGEST_TEXT_VIEW_SIZE, LARGEST_TEXT_VIEW_SIZE))
    text_view.textContainer().setContainerSize_((ACCESSORY_WIDTH, LARGEST_TEXT_VIEW_SIZE))
    text_view.textContainer().setWidthTracksTextView_(True)
    font = NSFont.monospacedSystemFontOfSize_weight_(NSFont.smallSystemFontSize(), NSFontWeightRegular)
    text_view.textStorage().setAttributedString_(build_process_info_text(list_process_info_lines(details, now), font))
    text_view.layoutManager().ensureLayoutForTextContainer_(text_view.textContainer())
    text_height = (text_view.layoutManager().usedRectForTextContainer_(text_view.textContainer()).size.height
                   + 2 * text_view.textContainerInset().height)
    scroll_view = NSScrollView.alloc().initWithFrame_(NSMakeRect(0, 0, ACCESSORY_WIDTH, min(text_height, LARGEST_ACCESSORY_HEIGHT)))
    scroll_view.setDrawsBackground_(False)
    scroll_view.setHasVerticalScroller_(True)
    scroll_view.setAutohidesScrollers_(True)
    text_view.setFrame_(NSMakeRect(0, 0, scroll_view.contentSize().width, text_height))
    scroll_view.setDocumentView_(text_view)
    alert.setAccessoryView_(scroll_view)
    alert.layout()
    return alert
