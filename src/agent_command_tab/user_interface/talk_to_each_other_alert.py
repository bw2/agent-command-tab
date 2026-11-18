"""The Talk to Each Other window: the note sent to the selected agent sessions, which the user can
edit before sending it, and which of them gets it."""

import collections

import objc
from AppKit import (NSAlert, NSBezelBorder, NSButton, NSColor, NSControlStateValueOff, NSControlStateValueOn,
                    NSEventModifierFlagCommand, NSFont, NSMakeRect, NSScrollView, NSTextField, NSView, NSViewWidthSizable)
from Foundation import NSObject

from agent_command_tab.messages_between_agent_sessions import DEFAULT_NOTE_ASKING_AGENT_SESSIONS_TO_TALK_TO_EACH_OTHER
from agent_command_tab.user_interface.control_helpers import LARGEST_TEXT_VIEW_SIZE, make_text_view_that_wraps_lines

ACCESSORY_WIDTH = 460
NOTE_BOX_HEIGHT = 110
GAP = 6
GAP_BETWEEN_SECTIONS = 12
RADIO_BUTTON_INDENT = 8
# The room for the "Send the message to:" radio buttons, which scroll when they need more
# (build_recipients_scroll_view): about nine and a half of them show then, the one cut short showing that the list goes on.
LARGEST_RECIPIENTS_HEIGHT = 200
# What the message sent adds to the note (messages_between_agent_sessions.build_message_asking_agent_session_to_talk_to_the_others).
EXPLANATION = ("The message will also include each agent's name, process ID, folder and tab title, and how to message each of "
               "the others: for a Codex session, a command with its conversation ID and Codex home folder. A Codex session "
               "has no way to message a Claude Code session, so the message it gets says so instead.")


def build_recipient_titles(tab_titles):
    """Returns the title of each agent session's radio button, in the order of tab_titles (the
    list's, top first): its tab's title, and for tabs with the same title (two sessions started
    in one folder) which of them it is, counted from the top of the list, such as
    "project (1 of 2)" and "project (2 of 2)". Two buttons with one title would send the message
    to different sessions with nothing to tell them apart."""
    count_by_tab_title = collections.Counter(tab_titles)
    number_by_tab_title = collections.Counter()
    recipient_titles = []
    for tab_title in tab_titles:
        number_by_tab_title[tab_title] += 1
        recipient_titles.append(tab_title if count_by_tab_title[tab_title] == 1
                                else f"{tab_title} ({number_by_tab_title[tab_title]} of {count_by_tab_title[tab_title]})")
    return recipient_titles


class RecipientChoices(NSObject):
    """The "Send the message to:" radio buttons: one per agent session (build_recipient_titles),
    then "each of the N agents", chosen at first. They share this target and its action, so AppKit
    lets only one be on."""

    @objc.python_method
    def build_radio_buttons(self, agent_session_entries):
        """Makes the buttons; returns them, top first."""
        self.recipients_by_radio_button = {}
        titles_and_recipients = [(title, [entry]) for title, entry in zip(
            build_recipient_titles([entry.tab_title for entry in agent_session_entries]), agent_session_entries)] + [
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


def build_recipients_scroll_view(radio_buttons):
    """Returns a scroll view of the accessory's width that holds the radio buttons, top first: as
    tall as they are, up to LARGEST_RECIPIENTS_HEIGHT. A longer list scrolls, so the window fits
    on the screen however many agent sessions were selected; it shows its end at first, where the
    button chosen at first is. One view holds every button, since AppKit lets only one be on
    among the radio buttons of one view."""
    recipients_view = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, ACCESSORY_WIDTH, 0))
    recipients_view.setAutoresizingMask_(NSViewWidthSizable)  # as wide as the room a scroller leaves
    y = 0
    for radio_button in reversed(radio_buttons):  # from the bottom up
        button_height = radio_button.fittingSize().height
        radio_button.setFrame_(NSMakeRect(RADIO_BUTTON_INDENT, y, ACCESSORY_WIDTH - RADIO_BUTTON_INDENT, button_height))
        radio_button.setAutoresizingMask_(NSViewWidthSizable)
        recipients_view.addSubview_(radio_button)
        y += button_height + GAP / 2
    scroll_view = NSScrollView.alloc().initWithFrame_(NSMakeRect(0, 0, ACCESSORY_WIDTH, min(y, LARGEST_RECIPIENTS_HEIGHT)))
    scroll_view.setDrawsBackground_(False)
    scroll_view.setHasVerticalScroller_(True)
    scroll_view.setAutohidesScrollers_(True)
    # The width the scroll view has room for now: less than its own where macOS always shows scroll bars.
    recipients_view.setFrameSize_((scroll_view.contentSize().width, y))
    scroll_view.setDocumentView_(recipients_view)
    return scroll_view


def list_tab_titles_for_alert(agent_session_entries):
    """Returns the tabs' titles, for example '"planning", "reviews"'."""
    return ", ".join(f'"{entry.tab_title}"' for entry in agent_session_entries)


def build_note_text_view(frame_size):
    """Returns a plain-text view of the scroll view's content size that wraps lines and leaves
    quotes and dashes as typed."""
    note_text_view = make_text_view_that_wraps_lines(frame_size.width, frame_size.height)
    note_text_view.setMinSize_((0, frame_size.height))
    note_text_view.setAutoresizingMask_(NSViewWidthSizable)
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
    of them (RecipientChoices, which must be kept while the alert runs; a long list of them
    scrolls, build_recipients_scroll_view), a "Message draft:" box holding the default note, and
    what the message adds.

    Args:
        agent_session_entries: The selected agent sessions that can take part.
        left_out_agent_session_entries: The selected ones whose tools can't take part.
    """
    alert = NSAlert.alloc().init()
    alert.setMessageText_("Talk to Each Other")  # with the app's icon
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
    recipients_scroll_view = build_recipients_scroll_view(radio_buttons)
    recipients_height = recipients_scroll_view.frame().size.height
    subviews_and_frames.append((recipients_scroll_view, NSMakeRect(0, y, ACCESSORY_WIDTH, recipients_height)))
    y += recipients_height + GAP / 2
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
