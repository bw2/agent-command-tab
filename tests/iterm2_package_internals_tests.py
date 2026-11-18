"""Guards the private or undocumented parts of the `iterm2` package that the core relies on.

If a new `iterm2` release renames them, these tests fail instead of the app silently going
back to NSAppleScript on a background thread, losing half of its connection watchdog, or
failing every read of iTerm2's windows.
"""

import asyncio
import os
import unittest
from unittest import mock

import iterm2
import iterm2.auth

from agent_command_tab.terminal_backends.iterm2_terminal_backend import read_iterm2_variables


class Iterm2PackageInternalsTests(unittest.TestCase):
    def test_turning_off_appkit_makes_authentication_use_the_osascript_runner(self):
        # patch.object fails when the package no longer has gAppKitAvailable; patch.dict puts back
        # the environment that authenticate() fills in.
        with mock.patch.object(iterm2.auth, "gAppKitAvailable", False), mock.patch.dict(os.environ), \
                mock.patch.object(iterm2.auth, "applescript_auth_disabled", return_value=False), \
                mock.patch.object(iterm2.auth, "request_cookie_and_key", return_value="cookie key") as request:
            os.environ.pop("ITERM2_COOKIE", None)
            iterm2.auth.authenticate()
        self.assertIs(request.call_args[0][2], iterm2.auth.CommandLineApplescriptRunner)

    def test_connection_still_has_the_dispatch_future_the_watchdog_waits_on(self):
        self.assertIn("_Connection__dispatch_forever_future", vars(iterm2.Connection()))

    def test_variable_request_still_takes_several_names_for_a_session_tab_or_window(self):
        # read_iterm2_variables reads several variables in one request through iterm2.rpc.async_variable,
        # which runs here for real, up to the connection that would send its request to iTerm2.
        response = iterm2.api_pb2.ServerOriginatedMessage()
        response.variable_response.status = iterm2.api_pb2.VariableResponse.Status.Value("OK")
        response.variable_response.values.extend(['"zsh"', "null"])
        for id_field_name in ("session_id", "tab_id", "window_id"):
            connection = mock.Mock(async_send_message=mock.AsyncMock(), async_dispatch_until_id=mock.AsyncMock(return_value=response))
            self.assertEqual(asyncio.run(read_iterm2_variables(connection, ["title", "titleOverrideFormat"], **{id_field_name: "id-1"})),
                             ["zsh", None])
            variable_request = connection.async_send_message.await_args.args[0].variable_request
            self.assertEqual((variable_request.WhichOneof("scope"), getattr(variable_request, id_field_name)), (id_field_name, "id-1"))
            self.assertEqual(list(variable_request.get), ["title", "titleOverrideFormat"])


if __name__ == "__main__":
    unittest.main()
