"""What the app's hooks report about each agent process (hook_event_tracking): which agent sent an
event, whether its session ended normally, and Claude Code's background shells and agents."""

import unittest

from agent_command_tab.hook_event_tracking import (apply_hook_event, find_agent_process_id_of_hook_event,
                                                   find_when_the_hooks_ended_the_turn)


def make_event(hook_event_name, sent_at, **fields):
    return dict({"agent_tool_name": "Claude Code", "hook_event_name": hook_event_name, "sent_at": sent_at,
                 "background_tasks": None, "background_task_launched_id": None}, **fields)


def apply_events(*events):
    hook_state = None
    for event in events:
        hook_state = apply_hook_event(hook_state, event)
    return hook_state


class FindAgentProcessIdOfHookEventTests(unittest.TestCase):
    def test_the_nearest_process_above_the_shell_the_hook_runs_in(self):
        # Claude Code runs its hooks in a shell; its executable is named after its version.
        self.assertEqual(find_agent_process_id_of_hook_event([[27904, "sh"], [27902, "2.1.311"], [3358, "bash"]]), 27902)
        self.assertEqual(find_agent_process_id_of_hook_event([[501, "codex"], [500, "zsh"]]), 501)  # run directly

    def test_an_agent_run_inside_a_tabs_agent_is_its_own(self):
        # A `claude -p` that the tab's Claude Code started through its shell tool.
        self.assertEqual(find_agent_process_id_of_hook_event(
            [[700, "sh"], [650, "2.1.311"], [640, "bash"], [600, "2.1.311"], [3358, "bash"]]), 650)

    def test_codexs_program_below_its_node_launcher_counts_as_the_launcher(self):
        self.assertEqual(find_agent_process_id_of_hook_event([[801, "sh"], [800, "codex"], [799, "node"], [700, "zsh"]]), 799)
        # A `codex exec` the tab's Codex started through a shell is its own.
        self.assertEqual(find_agent_process_id_of_hook_event([[901, "codex"], [900, "zsh"], [800, "codex"]]), 901)

    def test_none_without_a_process_above_the_shells(self):
        self.assertIsNone(find_agent_process_id_of_hook_event([[1, "sh"]]))
        self.assertIsNone(find_agent_process_id_of_hook_event([]))


class ApplyHookEventTests(unittest.TestCase):
    def test_a_normal_session_end_is_kept_but_clear_goes_on(self):
        hook_state = apply_events(make_event("SessionStart", 1.0, source="startup"))
        self.assertIsNone(hook_state["session_ended_at"])
        self.assertIsNone(apply_hook_event(hook_state, make_event("SessionEnd", 2.0, reason="clear"))["session_ended_at"])
        self.assertEqual(apply_hook_event(hook_state, make_event("SessionEnd", 3.0, reason="prompt_input_exit"))["session_ended_at"], 3.0)
        self.assertEqual(apply_hook_event(hook_state, make_event("SessionEnd", 3.0))["session_ended_at"], 3.0)  # Codex sends none

    def test_background_tasks_follow_launches_ends_and_turn_end_lists(self):
        # The sequence Claude Code 2.1.311 sent on 2026-10-27: a background shell, a background
        # agent that ends, then a turn end that lists the shell still running.
        hook_state = apply_events(
            make_event("SessionStart", 1.0, source="startup"),
            make_event("PostToolUse", 2.0, background_task_launched_id="bs6"),
            make_event("PostToolUse", 3.0, background_task_launched_id="a34"))
        self.assertEqual(hook_state["background_task_ids"], {"bs6", "a34"})
        hook_state = apply_hook_event(hook_state, make_event("SubagentStop", 4.0, agent_id="a34", background_tasks=[
            {"id": "bs6", "status": "running"}, {"id": "a34", "status": "running"}]))
        self.assertEqual(hook_state["background_task_ids"], {"bs6"})  # its own list still had it running
        hook_state = apply_hook_event(hook_state, make_event("Stop", 5.0, background_tasks=[{"id": "bs6", "status": "completed"}]))
        self.assertEqual(hook_state["background_task_ids"], set())

    def test_background_tasks_are_unknown_until_a_new_start_or_a_list(self):
        # The app started while the session already ran: what it runs isn't known yet.
        hook_state = apply_events(make_event("SessionStart", 1.0, source="clear"))
        self.assertIsNone(hook_state["background_task_ids"])
        # A launch doesn't make it known: others may run (the process tree goes on counting them).
        hook_state = apply_hook_event(hook_state, make_event("PostToolUse", 1.5, background_task_launched_id="b9"))
        self.assertIsNone(hook_state["background_task_ids"])
        hook_state = apply_hook_event(hook_state, make_event("Stop", 2.0, background_tasks=[{"id": "a1", "status": "running"}]))
        self.assertEqual(hook_state["background_task_ids"], {"a1"})
        # Codex's events list none, so its sessions stay unknown (the process tree counts its shells),
        # also after its SessionStart, which has source "startup" too (PHASE0_RESULTS.md).
        self.assertIsNone(apply_events(make_event("Stop", 1.0, agent_tool_name="Codex"))["background_task_ids"])
        self.assertIsNone(apply_events(make_event("SessionStart", 1.0, agent_tool_name="Codex", source="startup"),
                                       make_event("Stop", 2.0, agent_tool_name="Codex"))["background_task_ids"])

    def test_resume_or_a_new_session_in_the_same_process_isnt_its_end(self):
        hook_state = apply_events(make_event("SessionStart", 1.0, source="startup"), make_event("SessionEnd", 2.0, reason="resume"))
        self.assertIsNone(hook_state["session_ended_at"])  # Claude Code's /resume goes on in the same process
        hook_state = apply_events(make_event("SessionEnd", 3.0, reason="other"), make_event("SessionStart", 4.0, source="resume"))
        self.assertIsNone(hook_state["session_ended_at"])


class TurnEndTests(unittest.TestCase):
    def test_a_turn_end_counts_until_a_later_prompt_or_status_value(self):
        hook_state = apply_events(make_event("UserPromptSubmit", 10.0), make_event("Stop", 20.0))
        self.assertEqual(find_when_the_hooks_ended_the_turn(hook_state, 10.5), 20.0)  # iTerm2 still says what it said at 10.5
        self.assertIsNone(find_when_the_hooks_ended_the_turn(hook_state, 21.0))  # iTerm2 set a value since
        self.assertIsNone(find_when_the_hooks_ended_the_turn(apply_hook_event(hook_state, make_event("UserPromptSubmit", 30.0)), 10.5))
        self.assertIsNone(find_when_the_hooks_ended_the_turn(apply_events(make_event("UserPromptSubmit", 10.0)), 5.0))
        self.assertIsNone(find_when_the_hooks_ended_the_turn(None, 5.0))

    def test_a_tool_call_of_the_main_agent_after_the_stop_shows_the_turn_going_on(self):  # code review, 2026-10-28
        # Another Stop hook (Claude Code's goal feature) blocked the Stop, and the turn runs a command.
        hook_state = apply_events(make_event("UserPromptSubmit", 10.0), make_event("PostToolUse", 15.0), make_event("Stop", 20.0))
        self.assertEqual(find_when_the_hooks_ended_the_turn(hook_state, 10.5), 20.0)  # its tool call came before the Stop
        # A background agent's tool calls carry its agent_id, so they don't count; the main agent's do.
        self.assertEqual(find_when_the_hooks_ended_the_turn(
            apply_hook_event(hook_state, make_event("PreToolUse", 25.0, agent_id="a34")), 10.5), 20.0)
        hook_state = apply_hook_event(hook_state, make_event("PreToolUse", 25.0))
        self.assertIsNone(find_when_the_hooks_ended_the_turn(hook_state, 10.5))
        self.assertEqual(find_when_the_hooks_ended_the_turn(apply_hook_event(hook_state, make_event("Stop", 30.0)), 10.5), 30.0)

    def test_codex_goes_by_its_own_status(self):
        # Its hooks report no prompts, so a turn right after the Stop (a message `codex queue` held) couldn't undo it.
        hook_state = apply_events(make_event("Stop", 20.0, agent_tool_name="Codex"))
        self.assertIsNone(find_when_the_hooks_ended_the_turn(hook_state, 10.5))


if __name__ == "__main__":
    unittest.main()
