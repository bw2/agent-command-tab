"""Builds hook events as the forwarder sends them, for the tests of the core and the hook states file."""


def make_event(hook_event_name, agent_process_id, **fields):
    """Returns a Claude Code hook event sent from agent_process_id, through the "sh" the agent runs
    its hooks in (the forwarder's parent, listed first in ancestor_processes), with every key
    hook_event_forwarder.reduce_hook_payload sends, and fields overriding the defaults."""
    return dict({"agent_tool_name": "Claude Code", "hook_event_name": hook_event_name, "reason": None, "source": None,
                 "agent_id": None, "background_task_launched_id": None, "background_task_launched_type": None,
                 "background_tasks": None, "ancestor_processes": [[agent_process_id + 1000, "sh"], [agent_process_id, "2.1.311"]],
                 "sent_at": 50.0}, **fields)
