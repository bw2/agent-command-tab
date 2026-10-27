"""End-to-end check of the list's own actions with real Claude Code sessions in throwaway iTerm2 windows.

Usage: live_list_actions_check.py CLAUDE_TEST_FOLDER

Where live_agent_sessions_check.py calls the core's actions directly, this drives the list the
way a person does: the real list controller, fed the real snapshots, in an off-screen window,
connected to the real core. A drag is played back through the calls AppKit makes on the list
during a drag (pasteboard writer, drag start, validate drop, accept drop, drag end); only
AppKit's own mouse tracking is left out. Steps:
1. Two windows, each with a Claude Code (Haiku) tab.
2. The second window's right-click menu item "New tab with Claude Code session…" adds a tab.
3. The new tab and the second window are renamed in the list.
4. The new tab is dragged above the other tab of its window.
5. Whichever test window is lower in the list is dragged above the other.
6. The first window's agent runs a 21-second command; while it runs, its tab is dragged into
   the second window. The command must finish (the screen shows its last line), so the move
   didn't interrupt the agent.
7. "Show tab" brings that tab and its window to the front.
Closes its windows at the end. Touches nothing else.
"""

import asyncio
import os
import re
import sys
import threading
import time
import traceback

import iterm2
from AppKit import (NSApplication, NSApplicationActivationPolicyAccessory, NSBackingStoreBuffered,
                    NSDragOperationMove, NSMakeRect, NSMenu, NSWindow, NSWindowStyleMaskBorderless)

from agent_command_tab.dashboard_core_api import ActionRequest
from agent_command_tab.iterm2_connection_thread import Iterm2ConnectionThread
from agent_command_tab.screen_readings import clean_screen_line
from agent_command_tab.snapshot_data_classes import iterate_agent_session_entries
from agent_command_tab.user_interface.agent_session_outline_view import AgentSessionListController
from agent_command_tab.user_interface.popover_container import POPOVER_WIDTH, ListPanelController

CLAUDE_TEST_FOLDER = os.path.realpath(sys.argv[1])
CLAUDE_LAUNCH_COMMAND = "claude --permission-mode default --model haiku"
# Three lines over 21 seconds: Claude Code shows a finished command's output in full only up to three lines.
COUNTING_COMMAND = 'python3 -c "import time; [print(f\'count {i} of 3\', flush=True) or time.sleep(7) for i in range(1, 4)]"'
LAST_COUNT_LINE_PATTERN = re.compile(r"^\W*count 3 of 3\s*$", re.MULTILINE)  # not in the prompt, which holds the code
NEW_TAB_TITLE = "list-check-renamed-tab"
NEW_WINDOW_TITLE = "list-check-renamed-window"


class ListOwner:
    """Stands in for the app controller: sends the list's actions to the real core and keeps their results."""

    def __init__(self, connection_thread):
        self.connection_thread = connection_thread
        self.action_results = []

    def submit_action(self, action_request):
        self.action_results.append((action_request.action, self.connection_thread.submit_action(action_request).result(30)))

    def tab_was_focused(self, list_controller):
        pass

    def rows_changed(self, list_controller):
        pass

    def list_agent_tool_names(self):
        return ["Claude Code"]

    def find_launch_command(self, agent_tool_name):
        return CLAUDE_LAUNCH_COMMAND

    def start_agent_session_from_menu(self, window_id, folder, agent_tool_name):
        # As AppController.start_agent_session_from_menu does for a window's menu.
        self.submit_action(ActionRequest("start_agent_session", {"window_id": window_id},
                                         {"folder": folder or "~", "launch_command": self.find_launch_command(agent_tool_name)}))


def play_back_drag(list_controller, dragged_item, proposed_item, proposed_child_index):
    """Makes the calls AppKit makes on the list during a drag that ends over proposed_item at
    proposed_child_index (where AppKit proposes a drop between rows); returns whether it was accepted."""
    outline_view = list_controller.outline_view
    # The button goes down on the dragged row first: the list drags only the row under the press.
    list_controller.row_pressed(outline_view.rowForItem_(dragged_item))
    try:
        pasteboard_writer = list_controller.outlineView_pasteboardWriterForItem_(outline_view, dragged_item)
    finally:
        list_controller.row_released()
    if pasteboard_writer is None:
        return False
    list_controller.outlineView_draggingSession_willBeginAtPoint_forItems_(outline_view, None, (0, 0), [dragged_item])
    try:
        if list_controller.outlineView_validateDrop_proposedItem_proposedChildIndex_(
                outline_view, None, proposed_item, proposed_child_index) != NSDragOperationMove:
            return False
        return list_controller.outlineView_acceptDrop_item_childIndex_(outline_view, None, proposed_item, proposed_child_index)
    finally:
        list_controller.outlineView_draggingSession_endedAtPoint_operation_(outline_view, None, (0, 0), NSDragOperationMove)


def main():
    NSApplication.sharedApplication().setActivationPolicy_(NSApplicationActivationPolicyAccessory)
    snapshots = []
    snapshot_arrived = threading.Condition()

    def publish_snapshot(snapshot):
        with snapshot_arrived:
            snapshots.append(snapshot)
            snapshot_arrived.notify_all()

    connection_thread = Iterm2ConnectionThread(publish_snapshot, lambda message: print("connection lost:", message))
    connection_thread.start()
    connection_thread.core_ready.wait()
    core = connection_thread.core
    while core.app is None:
        time.sleep(0.1)
    owner = ListOwner(connection_thread)
    list_controller = AgentSessionListController.alloc().initWithOwner_(owner)
    list_controller.refresh_timer.invalidate()
    list_panel_controller = ListPanelController.alloc().initWithListController_(list_controller)
    list_window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
        NSMakeRect(-4000, -4000, POPOVER_WIDTH, 800), NSWindowStyleMaskBorderless, NSBackingStoreBuffered, False)
    list_window.setContentView_(list_panel_controller.view())
    list_window.orderFrontRegardless()  # "on screen" for the list, though far outside every monitor
    windows = []

    def run_on_core_loop(coroutine, timeout=60):
        return asyncio.run_coroutine_threadsafe(coroutine, connection_thread.event_loop).result(timeout)

    def show_latest_snapshot_in_the_list():
        with snapshot_arrived:
            snapshot = snapshots[-1] if snapshots else None
        if snapshot is not None:
            list_controller.apply_snapshot(snapshot)

    def find_entry(session_id):
        for _, window_entry, entry in iterate_agent_session_entries(snapshots[-1]) if snapshots else []:
            if entry.session_id == session_id:
                return window_entry, entry
        return None, None

    def find_window_entry(window_id):
        for monitor_entry in snapshots[-1].monitors if snapshots else []:
            for window_entry in monitor_entry.windows:
                if window_entry.window_id == window_id:
                    return window_entry
        return None

    def list_window_ids_in_list_order():
        return [window_entry.window_id for monitor_entry in snapshots[-1].monitors for window_entry in monitor_entry.windows]

    def wait_until(condition, timeout):
        deadline = time.time() + timeout
        while time.time() < deadline:
            with snapshot_arrived:
                if condition():
                    show_latest_snapshot_in_the_list()
                    return True
                snapshot_arrived.wait(0.5)
        show_latest_snapshot_in_the_list()
        return False

    def find_list_item(kind, item_id):
        show_latest_snapshot_in_the_list()
        return list_controller.items_by_key.get((kind, item_id))

    def rename_in_the_list(item, new_title):
        list_controller.begin_rename(item)
        assert list_controller.rename_in_progress
        row =list_controller.outline_view.rowForItem_(list_controller.item_being_renamed)
        name_text_field = list_controller.outline_view.viewAtColumn_row_makeIfNecessary_(0, row, True).name_text_field
        name_text_field.currentEditor().setString_(new_title)
        list_window.makeFirstResponder_(list_controller.outline_view)  # as a click elsewhere ends the edit

    async def open_window(initial_text, folder):
        profile = iterm2.LocalWriteOnlyProfile({"Initial Text": initial_text} if initial_text else {})
        profile.set_initial_directory_mode(iterm2.InitialWorkingDirectory.INITIAL_WORKING_DIRECTORY_CUSTOM)
        profile.set_custom_directory(folder)
        window = await iterm2.Window.async_create(core.connection, profile_customizations=profile)
        windows.append(window)
        return window.window_id, window.tabs[0].tab_id, window.tabs[0].sessions[0].session_id

    async def type_prompt(session_id, text):
        session = core.app.get_session_by_id(session_id)
        await session.async_send_text(text, suppress_broadcast=True)
        await asyncio.sleep(0.3)
        await session.async_send_text("\r", suppress_broadcast=True)

    async def read_screen(session_id):
        contents = await core.app.get_session_by_id(session_id).async_get_screen_contents()
        # iTerm2's empty cells (NULs) as spaces, so patterns with spaces and line ends match.
        return "\n".join(clean_screen_line(contents.line(index).string) for index in range(contents.number_of_lines))

    async def read_front_tab_and_window():
        window = core.app.current_terminal_window
        return (window.window_id, window.current_tab.tab_id) if window and window.current_tab else (None, None)

    async def read_tab_ids_of_window(window_id):
        return [tab.tab_id for tab in core.app.get_window_by_id(window_id).tabs]

    results = {}
    try:
        first_window_id, first_tab_id, first_session_id = run_on_core_loop(open_window(CLAUDE_LAUNCH_COMMAND, CLAUDE_TEST_FOLDER))
        second_window_id, second_tab_id, second_session_id = run_on_core_loop(open_window(CLAUDE_LAUNCH_COMMAND, CLAUDE_TEST_FOLDER))
        results["both agents listed and idle"] = wait_until(lambda: all(
            find_entry(session_id)[1] is not None and find_entry(session_id)[1].status in ("idle", "done")
            for session_id in (first_session_id, second_session_id)), 60)
        if not results["both agents listed and idle"]:
            return  # not at their prompts (a folder trust question, say), so nothing is typed; the exit code is 1

        # 2. Right-click menu of the second window: "New tab with Claude Code session…"
        second_window_item = find_list_item("window", second_window_id)
        list_controller.find_clicked_item = lambda: second_window_item
        menu = NSMenu.alloc().initWithTitle_("")
        list_controller.menuNeedsUpdate_(menu)
        new_tab_menu_item = next(menu_item for menu_item in menu.itemArray() if menu_item.title() == "New tab with Claude Code session…")
        new_tab_menu_item.target().performSelector_withObject_(new_tab_menu_item.action(), new_tab_menu_item)
        results["new tab action succeeded"] = owner.action_results[-1][1].succeeded

        def find_new_tab_entry():
            window_entry = find_window_entry(second_window_id)
            return next((entry for entry in (window_entry.agent_sessions if window_entry else ())
                         if entry.tab_id != second_tab_id), None)

        results["new tab runs Claude Code in the second window"] = wait_until(
            lambda: find_new_tab_entry() is not None and find_new_tab_entry().agent_tool_name == "Claude Code", 60)
        new_tab_id = find_new_tab_entry().tab_id

        # 3. Rename the new tab and the second window in the list.
        rename_in_the_list(find_list_item("tab", new_tab_id), NEW_TAB_TITLE)
        results["tab renamed"] = wait_until(lambda: find_new_tab_entry().tab_title == NEW_TAB_TITLE, 15)
        rename_in_the_list(find_list_item("window", second_window_id), NEW_WINDOW_TITLE)
        results["window renamed"] = wait_until(lambda: find_window_entry(second_window_id).title == NEW_WINDOW_TITLE, 15)

        # 4. Drag the new tab above the window's first tab.
        results["tab drop above its neighbor accepted"] = play_back_drag(
            list_controller, find_list_item("tab", new_tab_id), find_list_item("window", second_window_id), 0)
        results["tabs reordered in iTerm2"] = wait_until(
            lambda: [entry.tab_id for entry in find_window_entry(second_window_id).agent_sessions] == [new_tab_id, second_tab_id], 15)
        print("tab order in iTerm2 now:", run_on_core_loop(read_tab_ids_of_window(second_window_id)))

        # 5. Drag the lower test window above the other in the list (one monitor: windows are the top
        # level). New windows go on top of the list, so the second is likely above the first already,
        # and dragging it there would change nothing.
        window_ids = list_window_ids_in_list_order()
        lower_window_id, upper_window_id = sorted((first_window_id, second_window_id), key=window_ids.index, reverse=True)
        upper_window_item = find_list_item("window", upper_window_id)
        results["window drop accepted"] = play_back_drag(
            list_controller, find_list_item("window", lower_window_id), upper_window_item.parent_item,
            window_ids.index(upper_window_id))
        results["the lower test window now above the other in the list"] = wait_until(
            lambda: list_window_ids_in_list_order().index(lower_window_id) < list_window_ids_in_list_order().index(upper_window_id), 15)

        # 6. Drag the first window's tab into the second window while its agent runs a command.
        run_on_core_loop(type_prompt(first_session_id, f"Use the Bash tool to run exactly this command: {COUNTING_COMMAND}"))
        results["row shows running"] = wait_until(lambda: find_entry(first_session_id)[1].running_command_name != "", 60)
        second_window_item = find_list_item("window", second_window_id)
        results["tab drop into the other window accepted"] = play_back_drag(
            list_controller, find_list_item("tab", first_tab_id), second_window_item, len(second_window_item.child_items))
        results["tab is in the other window with the same session"] = wait_until(
            lambda: find_entry(first_session_id)[0] is not None and find_entry(first_session_id)[0].window_id == second_window_id, 15)
        results["still working right after the move"] = find_entry(first_session_id)[1].status == "working"
        results["turn ended after the command"] = wait_until(lambda: find_entry(first_session_id)[1].status in ("idle", "done"), 90)
        results["screen shows the command's last line (count 3 of 3)"] = bool(
            LAST_COUNT_LINE_PATTERN.search(run_on_core_loop(read_screen(first_session_id))))

        # 7. "Show tab" (also what a double-click does).
        list_controller.show_item(find_list_item("tab", second_tab_id))
        time.sleep(1)
        results["shown tab and its window are in front"] = run_on_core_loop(read_front_tab_and_window()) == (second_window_id, second_tab_id)
        failed_actions = [(action, result.message) for action, result in owner.action_results if not result.succeeded]
        results["every action the list sent succeeded"] = not failed_actions
        if failed_actions:
            print("failed actions:", failed_actions)
    except BaseException:
        traceback.print_exc()  # os._exit below would drop it
        results["ran to the end without an error"] = False
    finally:
        for check, passed in results.items():
            print(("PASS  " if passed else "FAIL  ") + check)
        list_window.orderOut_(None)
        for window in windows:
            try:
                run_on_core_loop(window.async_close(force=True), timeout=15)
            except Exception as exception:  # already gone (emptied by a move)
                print("closing a test window:", exception)
        # The new tab opened inside the second window, so it closed with it.
        sys.stdout.flush()  # os._exit skips it
        os._exit(0 if results and all(results.values()) else 1)


if __name__ == "__main__":
    main()
