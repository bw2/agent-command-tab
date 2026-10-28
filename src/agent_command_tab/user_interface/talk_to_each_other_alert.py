"""The Talk to Each Other window: the note sent to the selected agent sessions, which the user can
edit before sending it (user request, 2026-10-21), and which of them gets it (user request, 2026-10-28)."""

import objc
from AppKit import (NSAlert, NSBezelBorder, NSButton, NSColor, NSControlStateValueOff, NSControlStateValueOn,
                    NSEventModifierFlagCommand, NSFont, NSMakeRect, NSScrollView, NSTextField, NSTextView, NSView,
                    NSViewWidthSizable)
from Foundation import NSObject

from agent_command_tab.messages_between_agent_sessions import DEFAULT_NOTE_ASKING_AGENT_SESSIONS_TO_TALK_TO_EACH_OTHER

ACCESSORY_WIDTH = 460
NOTE_BOX_HEIGHT = 110
GAP = 6
GAP_BETWEEN_SECTIONS = 12
RADIO_BUTTON_INDENT = 8
LARGEST_TEXT_VIEW_SIZE = 1e7
EXPLANATION = "The message will include the other agents' names, process IDs and working directories."  # the user's wording, 2026-10-28


class RecipientChoices(NSObject):
    """The "Send the message to:" radio buttons: one per agent session, then "each of the N
    agents", chosen at first. They share this target and its action, so AppKit lets only one be on."""

    @objc.python_method
    def build_radio_buttons(self, agent_session_entries):
        """Makes the buttons; returns them, top first."""
        self.recipients_by_radio_button = {}
        titles_and_recipients = [(entry.tab_title, [entry]) for entry in agent_session_entries] + [
            (f"each of the {len(agent_session_entries)} agents", list(agent_session_entries))]
        radio_buttons = []
        for title, recipients in titles_and_recipients:
            radio_button = NSButton.radioButtonWithTitle_target_action_(title, self, "recipientChosen:")
            radio_button.setState_(NSControlStateValueOff)
            self.recipients_by_radio_button[radio_button] = recipients
            radio_buttons.append(radio_button)
        radio_buttons[-1].setState_(NSControlStateValueOn)
        return radio_buttons

    def recipientChosen_(self, sender):
        pass  # the choice is read once the window closes (list_chosen_recipients)

    @objc.python_method
    def list_chosen_recipients(self):
        """Returns the agent session entries the chosen button stands for."""
        return next(recipients for radio_button, recipients in self.recipients_by_radio_button.items()
                    if radio_button.state() == NSControlStateValueOn)


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
    """Returns (alert, note_text_view, recipient_choices): an alert with Send (also Command-Return)
    and Cancel (also Esc), "Send the message to:" radio buttons choosing one agent session or each
    of them (RecipientChoices, which must be kept while the alert runs), a "Message draft:" box
    holding the default note, and what the message adds.

    Args:
        agent_session_entries: The selected agent sessions that can take part.
        left_out_agent_session_entries: The selected ones whose tools can't take part.
    """
    alert = NSAlert.alloc().init()
    alert.setMessageText_("Talk to Each Other")  # with the app's icon (blanked, then restored: user requests, 2026-10-27)
    # The user's wording, 2026-10-28 (the agents were listed with bullets after "introducing these 2 agents to each other:").
    informative_text = f"Send a message instructing these {len(agent_session_entries)} agents to talk to each other."
    if left_out_agent_session_entries:
        informative_text += ("\n\nLeft out, since their tools can't take messages from other sessions: "
                             f"{list_tab_titles_for_alert(left_out_agent_session_entries)}.")
    alert.setInformativeText_(informative_text)
    alert.addButtonWithTitle_("Send")
    alert.buttons()[0].setKeyEquivalentModifierMask_(NSEventModifierFlagCommand)  # Return alone starts a new line
    alert.addButtonWithTitle_("Cancel")  # NSAlert gives a button titled Cancel the Esc key
    explanation_field = NSTextField.wrappingLabelWithString_(EXPLANATION)
    explanation_field.setFont_(NSFont.systemFontOfSize_(NSFont.smallSystemFontSize()))
    explanation_field.setTextColor_(NSColor.secondaryLabelColor())
    explanation_height = explanation_field.cell().cellSizeForBounds_(NSMakeRect(0, 0, ACCESSORY_WIDTH, LARGEST_TEXT_VIEW_SIZE)).height
    message_label = NSTextField.labelWithString_("Message draft:")
    recipients_label = NSTextField.labelWithString_("Send the message to:")
    recipient_choices = RecipientChoices.alloc().init()
    radio_buttons = recipient_choices.build_radio_buttons(agent_session_entries)
    # Laid out from the bottom up: the explanation, the draft and its label, then the recipients and theirs.
    subviews_and_frames = [(explanation_field, NSMakeRect(0, 0, ACCESSORY_WIDTH, explanation_height))]
    y = explanation_height + GAP
    scroll_view = NSScrollView.alloc().initWithFrame_(NSMakeRect(0, y, ACCESSORY_WIDTH, NOTE_BOX_HEIGHT))
    scroll_view.setBorderType_(NSBezelBorder)
    scroll_view.setHasVerticalScroller_(True)
    note_text_view = build_note_text_view(scroll_view.contentSize())
    scroll_view.setDocumentView_(note_text_view)
    subviews_and_frames.append((scroll_view, scroll_view.frame()))
    y += NOTE_BOX_HEIGHT + GAP
    subviews_and_frames.append((message_label, NSMakeRect(0, y, ACCESSORY_WIDTH, message_label.fittingSize().height)))
    y += message_label.fittingSize().height + GAP_BETWEEN_SECTIONS
    for radio_button in reversed(radio_buttons):
        subviews_and_frames.append((radio_button, NSMakeRect(RADIO_BUTTON_INDENT, y, ACCESSORY_WIDTH - RADIO_BUTTON_INDENT,
                                                             radio_button.fittingSize().height)))
        y += radio_button.fittingSize().height + GAP / 2
    y += GAP / 2
    subviews_and_frames.append((recipients_label, NSMakeRect(0, y, ACCESSORY_WIDTH, recipients_label.fittingSize().height)))
    y += recipients_label.fittingSize().height
    accessory_view = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, ACCESSORY_WIDTH, y))
    for subview, frame in subviews_and_frames:
        subview.setFrame_(frame)
        accessory_view.addSubview_(subview)
    alert.setAccessoryView_(accessory_view)
    alert.layout()
    alert.window().setInitialFirstResponder_(note_text_view)
    return alert, note_text_view, recipient_choices
