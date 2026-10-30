import asyncio
import unittest
import unittest.mock

import iterm2

from agent_command_tab import iterm2_actions
from agent_command_tab.dashboard_core_api import ActionResult


class StartAgentSessionTests(unittest.TestCase):
    def test_a_new_window_returns_its_id_for_the_lists_to_select_it(self):  # user request, 2026-10-30
        for new_window, expected_result in ((unittest.mock.Mock(window_id="w9"), ActionResult(True, details={"window_id": "w9"})),
                                            (None, ActionResult(True))):  # iTerm2 gives no window when its session ended right away
            with unittest.mock.patch.object(iterm2.Window, "async_create", unittest.mock.AsyncMock(return_value=new_window)):
                self.assertEqual(asyncio.run(iterm2_actions.start_agent_session(None, None, None, "/tmp", "claude")),
                                 expected_result)


if __name__ == "__main__":
    unittest.main()
