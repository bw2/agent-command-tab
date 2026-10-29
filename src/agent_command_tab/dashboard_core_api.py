"""The boundary between the core and its views.

Every user action is an ActionRequest. The menu bar list builds requests exactly as a later
web page, phone app or second computer would, and the core runs them through one registry
that knows each action's safety flags.
"""

import collections

ActionRequest = collections.namedtuple("ActionRequest", ["action", "target", "arguments"], defaults=[None])
ActionRequest.__doc__ = ("One user action. target is a dict with any of computer_id, window_id, tab_id and "
                         "session_id; arguments is a dict, for example {\"new_title\": \"Reviews\"}.")

ActionResult = collections.namedtuple("ActionResult", ["succeeded", "message"], defaults=[""])
ActionResult.__doc__ = "What an action did. message explains a failure in plain words."

ActionDefinition = collections.namedtuple(
    "ActionDefinition", ["handler", "may_come_from_another_computer", "types_into_a_session"])
ActionDefinition.__doc__ = ("A registered action: an async handler taking an ActionRequest and returning an "
                            "ActionResult, plus the flags later network views are checked against.")


class ActionRegistry:
    """Maps action names to their handlers and flags."""

    def __init__(self):
        self.action_definitions_by_name = {}

    def register_action(self, action_name, handler, may_come_from_another_computer, types_into_a_session):
        """Registers an action.

        Args:
            action_name: For example "rename_tab".
            handler: async function(ActionRequest) -> ActionResult.
            may_come_from_another_computer: False for moves, drags and list reordering.
            types_into_a_session: True for actions that type into an agent's terminal.
        """
        self.action_definitions_by_name[action_name] = ActionDefinition(
            handler, may_come_from_another_computer, types_into_a_session)

    async def perform_action(self, action_request, request_comes_from_another_computer=False):
        """Runs a request and returns its ActionResult; never raises.

        Args:
            action_request: An ActionRequest.
            request_comes_from_another_computer: True for requests from a network view (not in
                version 1); such requests run only for actions that allow it.
        """
        action_definition = self.action_definitions_by_name.get(action_request.action)
        if action_definition is None:
            return ActionResult(False, f"unknown action {action_request.action!r}")
        if request_comes_from_another_computer and not action_definition.may_come_from_another_computer:
            return ActionResult(False, f"{action_request.action} can only be done on this computer")
        try:
            return await action_definition.handler(action_request)
        except Exception as error:  # An action must never take down the core.
            return ActionResult(False, f"{action_request.action} failed: {error}")
