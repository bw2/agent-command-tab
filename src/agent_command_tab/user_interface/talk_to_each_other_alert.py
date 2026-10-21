"""The Talk to Each Other window: the note sent to the selected agent sessions, which the user can
edit before sending it (user request, 2026-10-21)."""

from AppKit import (NSAlert, NSBezelBorder, NSColor, NSEventModifierFlagCommand, NSFont, NSMakeRect, NSScrollView,
                    NSTextField, NSTextView, NSView, NSViewWidthSizable)

from agent_command_tab.messages_between_agent_sessions import (DEFAULT_NOTE_ASKING_AGENT_SESSIONS_TO_TALK_TO_EACH_OTHER,
                                                               SECONDS_A_MESSAGE_WAITS_FOR_AN_AGENT_AT_ITS_PROMPT)

ACCESSORY_WIDTH = 460
NOTE_BOX_HEIGHT = 110
GAP = 6
LARGEST_TEXT_VIEW_SIZE = 1e7
EXPLANATION = (
    "Each session's copy ends with its own name and, for each of the others, its name, process ID, folder and "
    "tab, and how to message it: a Claude Code session with Claude Code's SendMessage tool, a Codex session "
    "with codex queue. A Codex session can't message a Claude Code session. A Claude Code session gets its copy "
    "once it waits at its prompt with an empty input box (for up to "
    f"{SECONDS_A_MESSAGE_WAITS_FOR_AN_AGENT_AT_ITS_PROMPT // 60} minutes); a Codex session gets it after any "
    "turn under way. Command-Return sends.")


def list_tab_titles_for_alert(agent_session_entries):
    """Returns the tabs' titles, for example '"planning", "reviews"'."""
    return ", ".join(f'"{entry.tab_title}"' for entry in agent_session_entries)


def build_note_text_view(frame_size):
    """Returns a plain-text view of the scroll view's content size that wraps lines and leaves
    quotes and dashes as typed."""
    note_text_view = NSTextView.alloc().initWithFrame_(NSMakeRect(0, 0, frame_size.width, frame_size.height))
    note_text_view.setMinSize_((0, frame_size.height))
    note_text_view.setMaxSize_((LARGEST_TEXT_VIEW_SIZE, LARGEST_TEXT_VIEW_SIZE))
    note_text_view.setVerticallyResizable_(True)
    note_text_view.setHorizontallyResizable_(False)
    note_text_view.setAutoresizingMask_(NSViewWidthSizable)
    note_text_view.textContainer().setContainerSize_((frame_size.width, LARGEST_TEXT_VIEW_SIZE))
    note_text_view.textContainer().setWidthTracksTextView_(True)
    note_text_view.setRichText_(False)
    note_text_view.setAllowsUndo_(True)
    note_text_view.setAutomaticQuoteSubstitutionEnabled_(False)
    note_text_view.setAutomaticDashSubstitutionEnabled_(False)
    note_text_view.setFont_(NSFont.systemFontOfSize_(NSFont.systemFontSize()))
    note_text_view.setString_(DEFAULT_NOTE_ASKING_AGENT_SESSIONS_TO_TALK_TO_EACH_OTHER)
    return note_text_view


def build_talk_to_each_other_alert(agent_session_entries, left_out_agent_session_entries):
    """Returns (alert, note_text_view): an alert with Send (also Command-Return) and Cancel (also
    Esc), a "Message to send:" box holding the default note, and what each session's copy adds.

    Args:
        agent_session_entries: The selected agent sessions that get the message.
        left_out_agent_session_entries: The selected ones whose tools can't take part.
    """
    alert = NSAlert.alloc().init()
    alert.setMessageText_("Talk to Each Other")
    informative_text = (f"Sends a message to these {len(agent_session_entries)} agent sessions: "
                        f"{list_tab_titles_for_alert(agent_session_entries)}.")
    if left_out_agent_session_entries:
        informative_text += (" Left out, since their tools can't take messages from other sessions: "
                             f"{list_tab_titles_for_alert(left_out_agent_session_entries)}.")
    alert.setInformativeText_(informative_text)
    alert.addButtonWithTitle_("Send")
    alert.buttons()[0].setKeyEquivalentModifierMask_(NSEventModifierFlagCommand)  # Return alone starts a new line
    alert.addButtonWithTitle_("Cancel")  # NSAlert gives a button titled Cancel the Esc key
    explanation_field = NSTextField.wrappingLabelWithString_(EXPLANATION)
    explanation_field.setFont_(NSFont.systemFontOfSize_(NSFont.smallSystemFontSize()))
    explanation_field.setTextColor_(NSColor.secondaryLabelColor())
    explanation_height = explanation_field.cell().cellSizeForBounds_(NSMakeRect(0, 0, ACCESSORY_WIDTH, LARGEST_TEXT_VIEW_SIZE)).height
    message_label = NSTextField.labelWithString_("Message to send:")
    message_label_height = message_label.fittingSize().height
    accessory_view = NSView.alloc().initWithFrame_(NSMakeRect(
        0, 0, ACCESSORY_WIDTH, explanation_height + GAP + NOTE_BOX_HEIGHT + GAP + message_label_height))
    explanation_field.setFrame_(NSMakeRect(0, 0, ACCESSORY_WIDTH, explanation_height))
    scroll_view = NSScrollView.alloc().initWithFrame_(NSMakeRect(0, explanation_height + GAP, ACCESSORY_WIDTH, NOTE_BOX_HEIGHT))
    scroll_view.setBorderType_(NSBezelBorder)
    scroll_view.setHasVerticalScroller_(True)
    note_text_view = build_note_text_view(scroll_view.contentSize())
    scroll_view.setDocumentView_(note_text_view)
    message_label.setFrame_(NSMakeRect(0, explanation_height + GAP + NOTE_BOX_HEIGHT + GAP, ACCESSORY_WIDTH, message_label_height))
    for subview in (explanation_field, scroll_view, message_label):
        accessory_view.addSubview_(subview)
    alert.setAccessoryView_(accessory_view)
    alert.layout()
    alert.window().setInitialFirstResponder_(note_text_view)
    return alert, note_text_view
