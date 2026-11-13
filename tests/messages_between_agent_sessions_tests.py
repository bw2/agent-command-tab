"""The message each agent session gets from "Talk to Each Other", and the text a tab row's "Copy
contact info" copies (messages_between_agent_sessions)."""

import unittest

from agent_command_tab.agent_adapters.base import MESSAGES_THROUGH_CODEX_QUEUE, MESSAGES_THROUGH_THE_SEND_MESSAGE_TOOL
from agent_command_tab.messages_between_agent_sessions import (AgentSessionForMessages,
                                                               build_message_asking_agent_session_to_talk_to_the_others,
                                                               build_contact_info)

PLANNING = AgentSessionForMessages("Claude Code", MESSAGES_THROUGH_THE_SEND_MESSAGE_TOOL, "planning-3a", 101,
                                   "/Users/me/project", "planning", "iTerm2 tab")
REVIEWS = AgentSessionForMessages("Claude Code", MESSAGES_THROUGH_THE_SEND_MESSAGE_TOOL, "reviews-7f", 102,
                                  "/Users/me/project", "reviews", "tmux window")
CODEX_TESTS = AgentSessionForMessages("Codex", MESSAGES_THROUGH_CODEX_QUEUE, "01a15e4e-de92-7f31-af6e-877571d0444b", 103,
                                      "/Users/me/project/tests", "codex tests", "iTerm2 tab")
CODEX_DOCS = AgentSessionForMessages("Codex", MESSAGES_THROUGH_CODEX_QUEUE, "01a15e4e-facd-7073-85e7-90d8392b1d54", 104,
                                     "/Users/me/project/docs", "codex docs", "iTerm2 tab")


class MessageAskingAgentSessionToTalkToTheOthersTests(unittest.TestCase):
    def test_a_claude_code_session_learns_how_to_reach_claude_code_and_codex_sessions(self):
        message = build_message_asking_agent_session_to_talk_to_the_others("  Please sync up.\n", PLANNING, [REVIEWS, CODEX_TESTS],
                                                                           [PLANNING, REVIEWS, CODEX_TESTS])
        self.assertEqual(message.split("\n"), [
            "Please sync up.",
            "",
            'You are the Claude Code session "planning-3a" (process ID 101, folder /Users/me/project, iTerm2 tab "planning").',
            "",
            "The other sessions:",
            '- the Claude Code session "reviews-7f" (process ID 102, folder /Users/me/project, tmux window "reviews"). '
            'To message it, use your SendMessage tool with to: "reviews-7f".',
            "- the Codex session 01a15e4e-de92-7f31-af6e-877571d0444b (process ID 103, folder /Users/me/project/tests, "
            'iTerm2 tab "codex tests"). To message it, run: codex queue --thread 01a15e4e-de92-7f31-af6e-877571d0444b '
            '--message="<your message>"',
        ])

    def test_a_codex_session_can_message_codex_sessions_but_not_claude_code_sessions(self):
        message = build_message_asking_agent_session_to_talk_to_the_others("Please sync up.", CODEX_TESTS, [PLANNING, CODEX_DOCS],
                                                                           [CODEX_TESTS, PLANNING, CODEX_DOCS])
        lines = message.split("\n")
        self.assertEqual(lines[2], "You are the Codex session 01a15e4e-de92-7f31-af6e-877571d0444b (process ID 103, "
                                   'folder /Users/me/project/tests, iTerm2 tab "codex tests").')
        self.assertIn("A Codex session has no way to send a Claude Code session a message", lines[5])
        self.assertIn("but it can message you", lines[5])
        self.assertTrue(lines[6].endswith('run: codex queue --thread 01a15e4e-facd-7073-85e7-90d8392b1d54 --message="<your message>"'))

    def test_a_codex_sessions_own_codex_home_is_set_for_codex_queue(self):  # the sender's may be another
        codex_docs_with_its_own_home = CODEX_DOCS._replace(codex_home_folder="/Users/me/Codex Work")
        message = build_message_asking_agent_session_to_talk_to_the_others("Please sync up.", PLANNING, [codex_docs_with_its_own_home],
                                                                           [PLANNING, codex_docs_with_its_own_home])
        self.assertTrue(message.endswith("run: CODEX_HOME='/Users/me/Codex Work' codex queue --thread "
                                         '01a15e4e-facd-7073-85e7-90d8392b1d54 --message="<your message>"'))

    def test_a_codex_session_sent_the_only_message_isnt_promised_messages_from_claude_code(self):
        message = build_message_asking_agent_session_to_talk_to_the_others("Please sync up.", CODEX_TESTS, [PLANNING], [CODEX_TESTS])
        self.assertTrue(message.endswith("A Codex session has no way to send a Claude Code session a message, so it can't hear "
                                         "from you, and it wasn't told how to message you."))

    def test_an_unknown_folder_says_so(self):
        message = build_message_asking_agent_session_to_talk_to_the_others("Hi.", PLANNING, [REVIEWS._replace(working_folder="")],
                                                                           [PLANNING])
        self.assertIn("folder unknown", message)


class ContactInfoTests(unittest.TestCase):
    def test_a_claude_code_session_is_named_as_talk_to_each_other_names_it_with_its_send_message_address(self):
        self.assertEqual(build_contact_info(PLANNING),
                         'The Claude Code session "planning-3a" (process ID 101, folder /Users/me/project, iTerm2 tab '
                         '"planning"). To message it, use your SendMessage tool with to: "planning-3a". Only Claude Code '
                         "sessions have that tool, so a Codex session can't message it.")

    def test_a_codex_session_comes_with_its_codex_queue_command_and_its_own_codex_home(self):
        self.assertEqual(build_contact_info(CODEX_DOCS._replace(codex_home_folder="/Users/me/Codex Work")),
                         "The Codex session 01a15e4e-facd-7073-85e7-90d8392b1d54 (process ID 104, folder /Users/me/project/docs, "
                         "iTerm2 tab \"codex docs\"). To message it, run: CODEX_HOME='/Users/me/Codex Work' codex queue --thread "
                         '01a15e4e-facd-7073-85e7-90d8392b1d54 --message="<your message>"')


if __name__ == "__main__":
    unittest.main()
