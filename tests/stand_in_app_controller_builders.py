"""Builds a stand-in AppController for the tests of the app and its windows: a Mock that runs the
real AppController code of the methods a test names."""

import functools
import unittest.mock

from agent_command_tab.app_controller import AppController


def make_stand_in_app_controller_with_real_methods(method_names, **attributes):
    """Returns a Mock with these attributes whose methods named in method_names are AppController's
    own, run with the Mock as self."""
    stand_in_app_controller = unittest.mock.Mock(**attributes)
    for method_name in method_names:
        setattr(stand_in_app_controller, method_name, functools.partial(getattr(AppController, method_name), stand_in_app_controller))
    return stand_in_app_controller
