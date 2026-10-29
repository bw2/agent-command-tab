"""Guards the private or undocumented parts of the `iterm2` package that the core relies on.

If a new `iterm2` release renames them, these tests fail instead of the app silently going
back to NSAppleScript on a background thread, losing half of its connection watchdog, or
failing every read of iTerm2's windows.
"""

import inspect
import os
import unittest
from unittest import mock

import iterm2
import iterm2.auth
import iterm2.rpc


class Iterm2PackageInternalsTests(unittest.TestCase):
    def test_turning_off_appkit_makes_authentication_use_the_osascript_runner(self):
        self.assertTrue(hasattr(iterm2.auth, "gAppKitAvailable"))
        saved_flag = iterm2.auth.gAppKitAvailable
        saved_cookie = os.environ.pop("ITERM2_COOKIE", None)
        saved_key = os.environ.get("ITERM2_KEY")
        try:
            iterm2.auth.gAppKitAvailable = False
            with mock.patch.object(iterm2.auth, "applescript_auth_disabled", return_value=False), \
                    mock.patch.object(iterm2.auth, "request_cookie_and_key", return_value="cookie key") as request:
                iterm2.auth.authenticate()
            self.assertIs(request.call_args[0][2], iterm2.auth.CommandLineApplescriptRunner)
        finally:
            iterm2.auth.gAppKitAvailable = saved_flag
            for name, value in (("ITERM2_COOKIE", saved_cookie), ("ITERM2_KEY", saved_key)):
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value

    def test_connection_still_has_the_dispatch_future_the_watchdog_waits_on(self):
        self.assertIn("_Connection__dispatch_forever_future", vars(iterm2.Connection()))

    def test_variable_request_still_takes_several_names_for_a_session_tab_or_window(self):
        # read_iterm2_variables reads several variables in one request through this function.
        parameters = inspect.signature(iterm2.rpc.async_variable).parameters
        self.assertEqual(list(parameters), ["connection", "session_id", "sets", "gets", "tab_id", "window_id"])
        self.assertTrue(hasattr(iterm2.api_pb2.VariableResponse.Status, "Value"))


if __name__ == "__main__":
    unittest.main()
