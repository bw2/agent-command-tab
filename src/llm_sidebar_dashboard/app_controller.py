"""Starts the AppKit main thread and the core's background thread, and routes snapshots and actions.

PLAN.md section 4, "Threads". Snapshots arrive on the background thread and are handed to the
main thread with AppHelper.callAfter, coalesced so that only the newest is drawn. The app
controller is also the owner that the list controllers, the panels, the sidebar, the menu bar
item and the settings window report to.
"""

import os
import sys
import threading
import time

from AppKit import (NSAlert, NSAlertSecondButtonReturn, NSAlertStyleWarning, NSApp, NSApplication,
                    NSApplicationActivationPolicyAccessory, NSApplicationDidBecomeActiveNotification,
                    NSApplicationDidChangeScreenParametersNotification, NSApplicationDidResignActiveNotification,
                    NSApplicationWillTerminateNotification, NSRunningApplication)
from Foundation import NSNotificationCenter, NSObject
from PyObjCTools import AppHelper
import objc

from llm_sidebar_dashboard.command_line import RECONNECT_EXIT_STATUS
from llm_sidebar_dashboard.dashboard_core_api import ActionRequest
from llm_sidebar_dashboard.docked_window_frames_store import DOCKED_WINDOW_FRAMES_FILE_PATH
from llm_sidebar_dashboard.iterm2_connection_thread import Iterm2ConnectionThread
from llm_sidebar_dashboard.settings_store import load_settings_resolving_paths_at_first_run, save_settings
from llm_sidebar_dashboard.snapshot_data_classes import choose_folder_for_new_agent_window
from llm_sidebar_dashboard.user_interface.agent_session_outline_view import AgentSessionListController
from llm_sidebar_dashboard.user_interface.menu_bar_status_item import MenuBarStatusItemController
from llm_sidebar_dashboard.user_interface.popover_container import ListPanelController, build_dropdown_popover
from llm_sidebar_dashboard.user_interface.screen_layout import read_monitors_from_screens
from llm_sidebar_dashboard.user_interface.settings_window import SettingsWindowController
from llm_sidebar_dashboard.user_interface.sidebar_panel import SidebarController

ITERM2_BUNDLE_IDENTIFIER = "com.googlecode.iterm2"
SECONDS_TO_SHOW_CONNECTION_PROBLEM_BEFORE_EXITING = 10
SECONDS_TO_WAIT_FOR_ROOM_BACK_BEFORE_EXITING = 3


class AppController(NSObject):
    """Owns the views, the settings and the connection thread."""

    def initWithVerbose_(self, verbose):
        self = objc.super(AppController, self).init()
        if self is None:
            return None
        self.verbose = verbose
        self.settings = load_settings_resolving_paths_at_first_run()
        self.reserved_screen_area = None
        self.latest_snapshot_lock = threading.Lock()
        self.latest_snapshot = None
        self.snapshot_delivery_is_scheduled = False
        self.dropdown_list_controller = AgentSessionListController.alloc().initWithOwner_(self)
        self.dropdown_panel_controller = ListPanelController.alloc().initWithListController_(self.dropdown_list_controller)
        self.popover = build_dropdown_popover(self.dropdown_panel_controller)
        self.status_item_controller = MenuBarStatusItemController.alloc().initWithPopover_owner_(self.popover, self)
        self.sidebar_controller = SidebarController.alloc().initWithOwner_(self)
        self.settings_window_controller = SettingsWindowController.alloc().initWithOwner_(self)
        self.connection_thread = Iterm2ConnectionThread(self.receive_snapshot_on_background_thread,
                                                        self.receive_connection_lost_on_background_thread, verbose,
                                                        DOCKED_WINDOW_FRAMES_FILE_PATH)
        notification_center = NSNotificationCenter.defaultCenter()
        for notification_name, selector_name in (
                (NSApplicationDidChangeScreenParametersNotification, "screenParametersChanged:"),
                (NSApplicationDidBecomeActiveNotification, "dashboardBecameActive:"),
                (NSApplicationDidResignActiveNotification, "dashboardResignedActive:"),
                (NSApplicationWillTerminateNotification, "applicationWillTerminate:")):
            notification_center.addObserver_selector_name_object_(self, selector_name, notification_name, None)
        return self

    @objc.python_method
    def start(self):
        self.connection_thread.start()
        self.connection_thread.send_monitors(read_monitors_from_screens())
        self.connection_thread.send_stuck_threshold_seconds(self.settings["stuck_minutes"] * 60)
        self.sidebar_controller.apply_settings(self.settings)

    # ----- background thread to main thread -----

    @objc.python_method
    def receive_snapshot_on_background_thread(self, snapshot):
        with self.latest_snapshot_lock:
            self.latest_snapshot = snapshot
            if self.snapshot_delivery_is_scheduled:
                return
            self.snapshot_delivery_is_scheduled = True
        AppHelper.callAfter(self.deliver_latest_snapshot)

    @objc.python_method
    def deliver_latest_snapshot(self):
        with self.latest_snapshot_lock:
            snapshot = self.latest_snapshot
            self.snapshot_delivery_is_scheduled = False
        self.dropdown_list_controller.apply_snapshot(snapshot)
        self.sidebar_controller.list_controller.apply_snapshot(snapshot)
        self.status_item_controller.show_snapshot(snapshot)
        if self.popover.isShown() and not self.dropdown_list_controller.rename_in_progress:
            self.dropdown_panel_controller.fit_size_to_rows()

    @objc.python_method
    def receive_connection_lost_on_background_thread(self, message):
        AppHelper.callAfter(self.exit_to_reconnect, message)

    @objc.python_method
    def exit_to_reconnect(self, message):
        """Shows the problem for a while, then exits so the supervisor reconnects.

        The `iterm2` package can't reconnect within one process (PLAN.md section 4).
        """
        print(f"llm-sidebar-dashboard: {message}; exiting so the supervisor reconnects", flush=True)
        explanation = f"{message}. Retrying soon. Is iTerm2 running, with Settings > General > Magic > Enable Python API on?"
        self.dropdown_panel_controller.show_message(explanation)
        self.sidebar_controller.list_panel_controller.show_message(explanation)
        self.status_item_controller.show_connection_problem()
        AppHelper.callLater(SECONDS_TO_SHOW_CONNECTION_PROBLEM_BEFORE_EXITING, self.exit_now_with_reconnect_status)

    @objc.python_method
    def exit_now_with_reconnect_status(self):
        sys.stdout.flush()
        os._exit(RECONNECT_EXIT_STATUS)

    # ----- owner methods for the list controllers and panels -----

    @objc.python_method
    def submit_action(self, action_request):
        if action_request.action == "focus_agent_session":
            # macOS may refuse another app's activation while the dashboard is active, so hand it over first.
            for iterm2_application in NSRunningApplication.runningApplicationsWithBundleIdentifier_(ITERM2_BUNDLE_IDENTIFIER):
                NSApplication.sharedApplication().yieldActivationToApplication_(iterm2_application)
        self.connection_thread.submit_action(action_request)

    @objc.python_method
    def tab_was_focused(self, list_controller):
        """The dropdown closes after a tab is focused; the sidebar stays until the pointer leaves."""
        if list_controller is self.dropdown_list_controller and self.popover.isShown():
            self.popover.performClose_(None)

    @objc.python_method
    def rows_changed(self, list_controller):
        if list_controller is self.dropdown_list_controller and self.popover.isShown():
            self.dropdown_panel_controller.fit_size_to_rows()

    @objc.python_method
    def list_agent_tool_names(self):
        return [agent_tool["agent_tool_name"] for agent_tool in self.settings["agent_tools"]]

    @objc.python_method
    def confirm_closing_window(self, window_entry):
        """Asks "Are you sure?" before an iTerm2 window, and the agent sessions in it, is closed.
        No is the default button, so Return keeps the window."""
        agent_session_count = len(window_entry.agent_sessions)
        other_tab_count = window_entry.total_tab_count - agent_session_count
        explanation = (f'Closing "{window_entry.title}" ends the {agent_session_count} agent '
                       f'session{"" if agent_session_count == 1 else "s"} running in it')
        if other_tab_count:
            explanation += f' and closes its {other_tab_count} other tab{"" if other_tab_count == 1 else "s"}'
        alert = NSAlert.alloc().init()
        alert.setAlertStyle_(NSAlertStyleWarning)
        alert.setMessageText_("Are you sure?")
        alert.setInformativeText_(explanation + ".")
        alert.addButtonWithTitle_("No")  # the first button is the default and sits rightmost
        alert.addButtonWithTitle_("Yes")
        self.close_dropdown()
        NSApp.activateIgnoringOtherApps_(True)
        return alert.runModal() == NSAlertSecondButtonReturn

    @objc.python_method
    def start_agent_session_from_menu(self, window_id, folder, agent_tool_name):
        """Starts an agent tool in a new tab of a window (R13), or in a new window (R13.1) in the
        folder of the tab in front, else of the tab used most recently within a day, else the
        home folder (no folder picker: user request, milestone 2)."""
        launch_command = next((agent_tool["launch_command"] for agent_tool in self.settings["agent_tools"]
                               if agent_tool["agent_tool_name"] == agent_tool_name), "")
        if window_id is not None:
            self.submit_action(ActionRequest("start_agent_session", {"window_id": window_id},
                                             {"folder": folder or "~", "launch_command": launch_command}))
            return
        with self.latest_snapshot_lock:
            snapshot = self.latest_snapshot
        self.close_dropdown()
        self.submit_action(ActionRequest("start_agent_session", {}, {
            "folder": choose_folder_for_new_agent_window(snapshot, time.time(), os.path.expanduser("~")),
            "launch_command": launch_command}))

    @objc.python_method
    def open_settings(self):
        self.close_dropdown()
        self.settings_window_controller.show(self.settings)

    # ----- settings -----

    @objc.python_method
    def settings_changed(self, new_settings):
        """Saves new settings and applies them to the menu bar item, the sidebar and an open settings window."""
        self.settings = new_settings
        save_settings(new_settings)
        self.status_item_controller.redraw()
        self.sidebar_controller.apply_settings(new_settings)
        self.settings_window_controller.reflect_settings(new_settings)
        self.connection_thread.send_stuck_threshold_seconds(new_settings["stuck_minutes"] * 60)

    @objc.python_method
    def toggle_sidebar(self):
        self.settings_changed(dict(self.settings, sidebar_is_on=not self.settings["sidebar_is_on"]))

    @objc.python_method
    def reserve_screen_area(self, frame):
        """Docked sidebar: asks the core to keep iTerm2 windows out of frame (None ends it)."""
        if frame != self.reserved_screen_area:
            self.reserved_screen_area = frame
            self.connection_thread.send_reserved_screen_area(frame)

    @objc.python_method
    def sidebar_width_changed(self, width):
        self.settings_changed(dict(self.settings, sidebar_width=width))

    # ----- notifications -----

    @objc.python_method
    def close_dropdown(self):
        if self.popover.isShown():
            self.popover.performClose_(None)

    def screenParametersChanged_(self, notification):
        self.connection_thread.send_monitors(read_monitors_from_screens())
        self.sidebar_controller.screen_layout_changed()

    def dashboardBecameActive_(self, notification):
        self.connection_thread.send_dashboard_is_active(True)

    def dashboardResignedActive_(self, notification):
        self.connection_thread.send_dashboard_is_active(False)

    @objc.python_method
    def quit(self):
        """Quits, first giving iTerm2 windows back the room a docked sidebar took."""
        self.give_room_back_before_exiting()
        os._exit(0)

    def applicationWillTerminate_(self, notification):
        self.give_room_back_before_exiting()  # for example after Ctrl+C in the terminal that started the app

    @objc.python_method
    def give_room_back_before_exiting(self):
        """Ends docking and waits, at most a few seconds, until the iTerm2 windows have their room
        back. If that fails, the saved frames still let the next run restore them."""
        if self.reserved_screen_area is not None:
            self.reserved_screen_area = None
            self.connection_thread.give_room_back_and_wait(SECONDS_TO_WAIT_FOR_ROOM_BACK_BEFORE_EXITING)


def run_menu_bar_app(verbose):
    """Runs the menu bar app until it quits; returns the exit status."""
    application = NSApplication.sharedApplication()
    application.setActivationPolicy_(NSApplicationActivationPolicyAccessory)  # no Dock icon, no Info.plist needed
    app_controller = AppController.alloc().initWithVerbose_(verbose)
    app_controller.start()
    AppHelper.runEventLoop(installInterrupt=True)
    app_controller.give_room_back_before_exiting()  # Ctrl+C can stop the event loop without terminating the app
    return 0
