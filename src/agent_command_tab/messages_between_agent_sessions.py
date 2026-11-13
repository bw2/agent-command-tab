"""The message "Talk to Each Other" sends each selected agent session.

Every session gets the user's note, then its own name, then, for each of the other sessions,
what it needs to send that session a message: a Claude Code session's name, which Claude Code's
SendMessage tool takes as the address; or a Codex session's conversation ID, for
`codex queue --thread <ID>`, which any agent with a shell can run. Claude Code has no supported
way in from outside a Claude Code session, so a Codex session can't message a Claude Code
session; its copy says so.
"""

import collections
import shlex

from agent_command_tab.agent_adapters.base import MESSAGES_THROUGH_CODEX_QUEUE, MESSAGES_THROUGH_THE_SEND_MESSAGE_TOOL
from agent_command_tab.agent_adapters.codex import build_codex_home_setting

# A Claude Code session's message is held until it waits at its prompt, for up to this long.
SECONDS_A_MESSAGE_WAITS_FOR_AN_AGENT_AT_ITS_PROMPT = 30 * 60
DEFAULT_NOTE_ASKING_AGENT_SESSIONS_TO_TALK_TO_EACH_OTHER = (
    "You and these other agents might be working on similar things right now. Please message them with a summary "
    "of what you're doing and figure out how you can all work together effectively.")

AgentSessionForMessages = collections.namedtuple(
    "AgentSessionForMessages",
    ["agent_tool_name", "how_other_agent_sessions_send_it_messages", "address", "process_id", "working_folder", "tab_title",
     "tab_kind_name", "codex_home_folder"], defaults=[None])
AgentSessionForMessages.__doc__ = (
    "What the messages say about one agent session: its tool (\"Codex\"), how other sessions send it messages "
    "(agent_adapters.base: MESSAGES_THROUGH_THE_SEND_MESSAGE_TOOL or MESSAGES_THROUGH_CODEX_QUEUE), its address "
    "for that (a Claude Code session's name, a Codex session's conversation ID), its agent's process ID, its "
    "working folder, its tab's title, what its terminal calls the tab (\"iTerm2 tab\", \"tmux window\"), and for "
    "a Codex session the CODEX_HOME `codex queue` needs to find its conversation (None when not known).")


def describe_agent_session_for_messages(agent_session):
    """Returns how the messages name a session, with its process ID, folder and tab, for example
    'the Claude Code session "analysis-1d" (process ID 4242, folder /x, iTerm2 tab "analysis")'."""
    name = (f'"{agent_session.address}"' if agent_session.how_other_agent_sessions_send_it_messages == MESSAGES_THROUGH_THE_SEND_MESSAGE_TOOL
            else agent_session.address)
    return (f"the {agent_session.agent_tool_name} session {name} (process ID {agent_session.process_id}, "
            f'folder {agent_session.working_folder or "unknown"}, {agent_session.tab_kind_name} "{agent_session.tab_title}")')


def explain_how_to_send_a_message_to(recipient):
    """Returns the sentence telling a session that can message the recipient how to: run
    `codex queue` for a Codex session, which any agent with a shell can, or use the SendMessage
    tool for a Claude Code session, which only another Claude Code session has."""
    if recipient.how_other_agent_sessions_send_it_messages == MESSAGES_THROUGH_CODEX_QUEUE:
        # The message attached to its option, so one starting with "-" isn't read as an option. Its
        # CODEX_HOME, since the sender's may differ.
        return (f"To message it, run: {build_codex_home_setting(recipient.codex_home_folder)}codex queue --thread "
                f"{shlex.quote(recipient.address)} "
                f'--message="<your message>"')
    return f'To message it, use your SendMessage tool with to: "{recipient.address}".'


def explain_why_sessions_of_agent_tool_cant_take_messages(agent_tool_name):
    """Returns why a session of a tool other than Claude Code and Codex is left out of messages
    between sessions, such as "Aider sessions can't take messages from other sessions"."""
    return f"{agent_tool_name} sessions can't take messages from other sessions"


def build_contact_info(agent_session):
    """Returns what a tab row's "Copy contact info" puts on the clipboard, to paste into
    another agent session so it can message this one: the session as Talk to Each Other names it,
    then how to send it a message, for example 'The Claude Code session "analysis-1d" (process ID
    4242, folder /x, iTerm2 tab "analysis"). To message it, use your SendMessage tool with to:
    "analysis-1d". Only Claude Code sessions have that tool, so a Codex session can't message it.'

    Args:
        agent_session: The AgentSessionForMessages of the session to message.
    """
    description = describe_agent_session_for_messages(agent_session)
    contact_info = f"{description[:1].upper()}{description[1:]}. {explain_how_to_send_a_message_to(agent_session)}"
    if agent_session.how_other_agent_sessions_send_it_messages == MESSAGES_THROUGH_THE_SEND_MESSAGE_TOOL:
        # Pasted into a Codex session, the contact info would otherwise ask it for a tool it lacks.
        return f"{contact_info} Only Claude Code sessions have that tool, so a Codex session can't message it."
    return contact_info


def explain_how_to_send_a_message(sender, recipient, recipient_gets_the_message_too):
    """Returns the sentence telling the sender how to send the recipient a message, or why it can't.

    Args:
        sender: The AgentSessionForMessages that gets this explanation.
        recipient: The AgentSessionForMessages it would send a message to.
        recipient_gets_the_message_too: Whether the recipient gets a Talk to Each Other message
            too, so it learns how to message the sender (only the one chosen in the Talk to Each
            Other window may get it).
    """
    if (recipient.how_other_agent_sessions_send_it_messages == MESSAGES_THROUGH_CODEX_QUEUE
            or sender.how_other_agent_sessions_send_it_messages == MESSAGES_THROUGH_THE_SEND_MESSAGE_TOOL):
        return explain_how_to_send_a_message_to(recipient)
    if recipient_gets_the_message_too:
        return ("A Codex session has no way to send a Claude Code session a message, so it can't hear from you directly, "
                "but it can message you.")
    return ("A Codex session has no way to send a Claude Code session a message, so it can't hear from you, "
            "and it wasn't told how to message you.")


def build_message_asking_agent_session_to_talk_to_the_others(note, recipient, other_agent_sessions, agent_sessions_that_get_the_message):
    """Returns the message one agent session gets from "Talk to Each Other".

    Args:
        note: The user's note, from the Talk to Each Other window.
        recipient: The AgentSessionForMessages that gets this message.
        other_agent_sessions: The AgentSessionForMessages of the other sessions, in list order.
        agent_sessions_that_get_the_message: The AgentSessionForMessages of every session that
            gets a Talk to Each Other message this time.
    """
    lines = [note.strip(), "", f"You are {describe_agent_session_for_messages(recipient)}.", "", "The other sessions:"]
    lines += [f"- {describe_agent_session_for_messages(other)}. "
              f"{explain_how_to_send_a_message(recipient, other, other in agent_sessions_that_get_the_message)}"
              for other in other_agent_sessions]
    return "\n".join(lines)
