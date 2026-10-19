"""Agent tool adapters and the rule that picks one for a tab."""

from llm_sidebar_dashboard.agent_adapters.claude_code import ClaudeCodeAdapter
from llm_sidebar_dashboard.agent_adapters.codex import CodexAdapter
from llm_sidebar_dashboard.agent_adapters.generic import GenericAdapter
from llm_sidebar_dashboard.session_status_computation import map_iterm2_status_value_to_state


def build_built_in_agent_tool_adapters():
    """Returns the adapters with their own rules, in matching order, and the generic adapter."""
    return [ClaudeCodeAdapter(), CodexAdapter()], GenericAdapter()


def list_slash_commands_of_the_built_in_agent_tools():
    """Returns every built-in adapter's slash_commands once each, in their order: the tab menu
    lists them all for every tab and enables those its tool takes, so the menu keeps its shape."""
    specific_adapters, generic_adapter = build_built_in_agent_tool_adapters()
    return tuple(dict.fromkeys(command for adapter in [*specific_adapters, generic_adapter] for command in adapter.slash_commands))


def find_agent_tool_adapter_for_tab(command_line, iterm2_status_value, specific_adapters, generic_adapter):
    """Returns the adapter for a tab's first pane, or None when the tab runs no agent.

    Args:
        command_line: iTerm2's `commandLine` for the tab's first session.
        iterm2_status_value: iTerm2's `status` variable for that session, or None.
        specific_adapters: Adapters tried in order (Claude Code, Codex, tools from settings).
        generic_adapter: Used when no specific adapter matches, the job is not a shell, and the
            status variable holds a recognized value.

    Returns:
        An AgentToolAdapter or None.
    """
    for adapter in specific_adapters:
        if adapter.matches_command_line(command_line):
            return adapter
    if generic_adapter.matches_command_line(command_line) and map_iterm2_status_value_to_state(iterm2_status_value):
        return generic_adapter
    return None
