"""The boundary between the core and its views.

Every user action is an ActionRequest. The menu bar list builds requests exactly as a later
web page, phone app or second computer would, and the core runs them through one registry.
"""

import collections

ActionRequest = collections.namedtuple("ActionRequest", ["action", "target", "arguments"], defaults=[None])
ActionRequest.__doc__ = ("One user action. target is a dict with any of computer_id, window_id, tab_id and "
                         "session_id; arguments is a dict, for example {\"new_title\": \"Reviews\"}.")

ActionResult = collections.namedtuple("ActionResult", ["succeeded", "message", "details"], defaults=["", None])
ActionResult.__doc__ = ("What an action did. message explains a failure in plain words; details is what an action "
                        "that reads something returns (a dict), else None.")

class ActionRegistry:
    """Maps action names to their handlers. The flags for requests from another computer and for
    actions that type into a session, which nothing read, are gone (code review, 2026-10-29)."""

    def __init__(self):
        self.handler_by_action_name = {}

    def register_action(self, action_name, handler):
        """Registers an action.

        Args:
            action_name: For example "rename_tab".
            handler: async function(ActionRequest) -> ActionResult.
        """
        self.handler_by_action_name[action_name] = handler

    async def perform_action(self, action_request):
        """Runs a request (an ActionRequest) and returns its ActionResult; never raises."""
        handler = self.handler_by_action_name.get(action_request.action)
        if handler is None:
            return ActionResult(False, f"unknown action {action_request.action!r}")
        try:
            return await handler(action_request)
        except Exception as error:  # An action must never take down the core.
            return ActionResult(False, f"{action_request.action} failed: {error}")
