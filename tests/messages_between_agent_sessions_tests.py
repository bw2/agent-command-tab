"""The message each agent session gets from "Talk to Each Other" (messages_between_agent_sessions)."""

import unittest

from agent_command_tab.agent_adapters.base import MESSAGES_THROUGH_CODEX_QUEUE, MESSAGES_THROUGH_THE_SEND_MESSAGE_TOOL
from agent_command_tab.messages_between_agent_sessions import (AgentSessionForMessages,
                                                               build_message_asking_agent_session_to_talk_to_the_others)

PLANNING = AgentSessionForMessages("Claude Code", MESSAGES_THROUGH_THE_SEND_MESSAGE_TOOL, "planning-3a", 101,
                                   "/Users/me/project", "planning")
REVIEWS = AgentSessionForMessages("Claude Code", MESSAGES_THROUGH_THE_SEND_MESSAGE_TOOL, "reviews-7f", 102,
                                  "/Users/me/project", "reviews")
CODEX_TESTS = AgentSessionForMessages("Codex", MESSAGES_THROUGH_CODEX_QUEUE, "01a15e4e-de92-7f31-af6e-877571d0444b", 103,
                                      "/Users/me/project/tests", "codex tests")
CODEX_DOCS = AgentSessionForMessages("Codex", MESSAGES_THROUGH_CODEX_QUEUE, "01a15e4e-facd-7073-85e7-90d8392b1d54", 104,
                                     "/Users/me/project/docs", "codex docs")


class MessageAskingAgentSessionToTalkToTheOthersTests(unittest.TestCase):
    def test_a_claude_code_session_learns_how_to_reach_claude_code_and_codex_sessions(self):
        message = build_message_asking_agent_session_to_talk_to_the_others("  Please sync up.\n", PLANNING, [REVIEWS, CODEX_TESTS])
        self.assertEqual(message.split("\n"), [
            "Please sync up.",
            "",
            'You are the Claude Code session "planning-3a" (process ID 101, folder /Users/me/project, iTerm2 tab "planning").',
            "",
            "The other sessions:",
            '- the Claude Code session "reviews-7f" (process ID 102, folder /Users/me/project, iTerm2 tab "reviews"). '
            'To message it, use your SendMessage tool with to: "reviews-7f".',
            "- the Codex session 01a15e4e-de92-7f31-af6e-877571d0444b (process ID 103, folder /Users/me/project/tests, "
            'iTerm2 tab "codex tests"). To message it, run: codex queue --thread 01a15e4e-de92-7f31-af6e-877571d0444b '
            '--message="<your message>"',
        ])

    def test_a_codex_session_can_message_codex_sessions_but_not_claude_code_sessions(self):
        message = build_message_asking_agent_session_to_talk_to_the_others("Please sync up.", CODEX_TESTS, [PLANNING, CODEX_DOCS])
        lines = message.split("\n")
        self.assertEqual(lines[2], "You are the Codex session 01a15e4e-de92-7f31-af6e-877571d0444b (process ID 103, "
                                   'folder /Users/me/project/tests, iTerm2 tab "codex tests").')
        self.assertIn("A Codex session has no way to send a Claude Code session a message", lines[5])
        self.assertIn("but it can message you", lines[5])
        self.assertTrue(lines[6].endswith('run: codex queue --thread 01a15e4e-facd-7073-85e7-90d8392b1d54 --message="<your message>"'))

    def test_an_unknown_folder_says_so(self):
        message = build_message_asking_agent_session_to_talk_to_the_others("Hi.", PLANNING, [REVIEWS._replace(working_folder="")])
        self.assertIn("folder unknown", message)


if __name__ == "__main__":
    unittest.main()
