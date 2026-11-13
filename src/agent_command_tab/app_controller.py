"""Starts the AppKit main thread and the core's background thread, and routes snapshots and actions.

Snapshots arrive on the background thread and are handed to the
main thread with AppHelper.callAfter, coalesced so that only the newest is drawn. The app
controller is also the owner that the list controllers, the panels, the sidebar, the menu bar
item and the settings window report to.
"""

import os
import sys
import threading
import time

from AppKit import (NSAlert, NSAlertFirstButtonReturn, NSAlertSecondButtonReturn, NSAlertStyleWarning, NSApp, NSAppearance,
                    NSAppearanceNameAqua, NSAppearanceNameDarkAqua, NSApplication, NSApplicationActivationPolicyAccessory, NSApplicationDidBecomeActiveNotification,
                    NSApplicationDidChangeScreenParametersNotification, NSApplicationDidResignActiveNotification,
                    NSApplicationWillTerminateNotification, NSEvent, NSEventModifierFlagCommand, NSEventModifierFlagShift,
                    NSMakeRect, NSMenu, NSMenuItem, NSModalPanelWindowLevel, NSMouseInRect, NSPasteboard, NSPasteboardTypeString,
                    NSPopoverBehaviorApplicationDefined,
                    NSPopoverBehaviorTransient, NSRunningApplication, NSWorkspace, NSWorkspaceApplicationKey,
                    NSWorkspaceDidActivateApplicationNotification)
from Foundation import NSNotificationCenter, NSObject, NSRunLoop, NSRunLoopCommonModes, NSTimer
from PyObjCTools import AppHelper
import objc

from agent_command_tab.agent_process_tree import list_the_process_and_its_ancestor_ids
from agent_command_tab.command_line import RECONNECT_EXIT_STATUS, SECONDS_TO_WAIT_FOR_ROOM_BACK_BEFORE_EXITING
from agent_command_tab.dashboard_core_api import ActionRequest
from agent_command_tab.docked_window_frames_store import DOCKED_WINDOW_FRAMES_FILE_PATH
from agent_command_tab.global_shortcut_key_filter import GlobalShortcutKeyFilter
from agent_command_tab.hook_event_forwarder import HOOK_EVENTS_SOCKET_PATH
from agent_command_tab.hook_states_store import HOOK_STATES_FILE_PATH
from agent_command_tab.hooks_installer import BACKUP_FILE_SUFFIX, install_hooks, list_settings_file_paths
from agent_command_tab.dashboard_core import DashboardCoreThread
from agent_command_tab.monitor_assignment import compute_frame_clear_of_area
from agent_command_tab.pause_records_store import PAUSE_RECORDS_FILE_PATH
from agent_command_tab.saved_agent_sessions_store import (SAVED_AGENT_SESSIONS_FILE_PATH, choose_saved_windows_to_restore,
                                                          delete_saved_items, load_saved_windows, merge_saved_windows,
                                                          rename_saved_session, rename_saved_window, save_saved_windows)
from agent_command_tab.settings_store import (SMALLEST_WINDOW_SIZES, build_core_settings, find_launch_command,
                                              list_included_agent_tool_names, load_settings, save_settings)
from agent_command_tab.snapshot_data_classes import build_window_title_shown_in_the_list, choose_folder_for_new_agent_window
from agent_command_tab.terminal_backends.iterm2_terminal_backend import ITERM2_BUNDLE_IDENTIFIER, list_running_iterm2_applications
from agent_command_tab.start_with_iterm2 import (START_WITH_ITERM2_SETTING_TITLE, build_command_that_starts_this_copy_of_the_app,
                                                 find_autolaunch_script_path, find_package_folder_of_this_copy,
                                                 update_autolaunch_script_to_match_setting)
from agent_command_tab.user_interface.agent_session_outline_view import AgentSessionListController
from agent_command_tab.user_interface.app_icon import build_app_icon
from agent_command_tab.user_interface.list_window import ListWindowController
from agent_command_tab.user_interface.menu_bar_status_item import MenuBarStatusItemController
from agent_command_tab.user_interface.popover_container import ListPanelController, build_dropdown_popover
from agent_command_tab.user_interface.process_and_session_info_alerts import build_process_info_alert, build_session_info_alert
from agent_command_tab.user_interface.saved_sessions_window import SavedSessionsWindowController, shorten_home_folder
from agent_command_tab.user_interface.screen_layout import (compute_list_window_content_rect_for_a_dragged_sidebar,
                                                            convert_rect_to_frame_list, grow_frame_to_smallest_size,
                                                            read_monitors_from_screens)
from agent_command_tab.user_interface.settings_window import SettingsWindowController
from agent_command_tab.user_interface.sidebar_panel import SidebarController
from agent_command_tab.user_interface.talk_to_each_other_alert import build_talk_to_each_other_alert

SECONDS_TO_SHOW_CONNECTION_PROBLEM_BEFORE_EXITING = 10
SECONDS_BEFORE_SAVING_A_MOVED_WINDOWS_FRAME = 0.5
# The settings' Appearance (settings_store.APPEARANCE_CHOICES): the
# NSAppearance name the app is drawn in, or None to follow macOS's Light or Dark setting.
APPEARANCE_NAMES_BY_CHOICE = {"auto": None, "dark": NSAppearanceNameDarkAqua, "light": NSAppearanceNameAqua}
# The settings whose change can put the sidebar where an open settings window is: every setting
# the sidebar reads, and the only ones whose change reaches it (SidebarController.apply_settings).
SETTINGS_THAT_PLACE_THE_SIDEBAR = ("show_as", "sidebar_mode", "sidebar_edge", "sidebar_width")


def build_main_menu_with_the_settings_and_edit_shortcuts(settings_target):
    """Returns a main menu holding an app menu whose Settings items open the settings with
    Command-comma, as in nearly every Mac app, and Command-period,
    and an Edit menu: Undo, Redo, Cut, Copy, Paste and Select All.

    The dashboard, an accessory app, never shows a menu bar, but AppKit finds the Command-key
    shortcuts in the main menu's items; without them, Command-C, V, X, A and Z do
    nothing in its text fields (the Talk to Each Other note, renames, the settings). The Edit
    items have no target, so each goes to the field being edited.

    Args:
        settings_target: The object whose settingsShortcutPressed_ opens the settings (the AppController).
    """
    app_menu = NSMenu.alloc().initWithTitle_("AgentCommandTab")
    for key in (",", "."):
        settings_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("Settings…", "settingsShortcutPressed:", key)
        settings_item.setKeyEquivalentModifierMask_(NSEventModifierFlagCommand)
        settings_item.setTarget_(settings_target)
        app_menu.addItem_(settings_item)
    app_menu_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("AgentCommandTab", None, "")
    app_menu_item.setSubmenu_(app_menu)
    edit_menu = NSMenu.alloc().initWithTitle_("Edit")
    for title, action_name, key, modifier_flags in (
            ("Undo", "undo:", "z", NSEventModifierFlagCommand),
            ("Redo", "redo:", "z", NSEventModifierFlagCommand | NSEventModifierFlagShift),
            ("Cut", "cut:", "x", NSEventModifierFlagCommand),
            ("Copy", "copy:", "c", NSEventModifierFlagCommand),
            ("Paste", "paste:", "v", NSEventModifierFlagCommand),
            ("Select All", "selectAll:", "a", NSEventModifierFlagCommand)):
        menu_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, action_name, key)
        menu_item.setKeyEquivalentModifierMask_(modifier_flags)
        edit_menu.addItem_(menu_item)
    edit_menu_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("Edit", None, "")
    edit_menu_item.setSubmenu_(edit_menu)
    main_menu = NSMenu.alloc().initWithTitle_("")
    main_menu.addItem_(app_menu_item)
    main_menu.addItem_(edit_menu_item)
    return main_menu


def choose_what_the_global_shortcut_does(show_as, sidebar_mode, sidebar_is_shown, list_window_is_in_front, dropdown_is_open):
    """Returns what a press of the global shortcut does: it shows the list the way the settings
    ask, or hides it when it shows, except that a sidebar set to stay
    visible stays, or comes back if the menu bar icon's menu hid it.

    Returns:
        "show_sidebar", "hide_sidebar", "nothing", "show_window", "hide_window",
        "open_dropdown" or "close_dropdown".
    """
    if show_as == "sidebar":
        if sidebar_mode != "auto_hide":
            return "nothing" if sidebar_is_shown else "show_sidebar"
        return "hide_sidebar" if sidebar_is_shown else "show_sidebar"
    if show_as == "window":
        return "hide_window" if list_window_is_in_front else "show_window"
    return "close_dropdown" if dropdown_is_open else "open_dropdown"


def yield_activation_to(application):
    """Lets an app (an NSRunningApplication) become the active one when it asks: macOS 14 and
    later may refuse another app's activation while this one is active unless this one hands it
    over. Older macOS has no such hand-over and needs none."""
    if NSApp.respondsToSelector_("yieldActivationToApplication:"):
        NSApp.yieldActivationToApplication_(application)


class AppController(NSObject):
    """Owns the views, the settings and the connection thread."""

    def initWithVerbose_(self, verbose):
        self = objc.super(AppController, self).init()
        if self is None:
            return None
        self.settings = load_settings()
        self.reserved_screen_area = None
        self.application_in_front_before_dropdown = None
        # The last app other than the dashboard to be active: hiding the list window with the
        # shortcut gives the keyboard back to it. Noted at the start (the app that started the
        # dashboard sends no activation notification), when the window is shown, and whenever
        # another app becomes active (anotherApplicationBecameActive_). The core hears of it too
        # (note_application_unless_it_is_the_dashboard), so it is noted once the core's thread exists.
        self.last_other_active_application = None
        self.peek_in_progress = None  # {"application_in_front"} while a tab row is held
        self.settings_save_is_scheduled = False  # a moved window's frame waits to be written (window_frame_changed)
        self.core_settings_sent_last = None  # apply_settings_to_the_core_and_every_list's
        self.peek_waiting_for_put_back = None  # a released peek until the app in front before is back
        self.list_window_drag_check_timer = None  # while the list window's title bar is dragged
        self.latest_snapshot_lock = threading.Lock()
        self.latest_snapshot = None
        self.snapshot_delivery_is_scheduled = False
        self.dropdown_list_controller = AgentSessionListController.alloc().initWithOwner_(self)
        self.dropdown_panel_controller = ListPanelController.alloc().initWithListController_(self.dropdown_list_controller)
        self.popover = build_dropdown_popover(self.dropdown_panel_controller)
        self.popover.setDelegate_(self)  # popoverWillClose_
        self.status_item_controller = MenuBarStatusItemController.alloc().initWithPopover_owner_(self.popover, self)
        self.sidebar_controller = SidebarController.alloc().initWithOwner_(self)
        self.list_window_controller = ListWindowController.alloc().initWithOwner_(self)
        for list_controller in self.list_all_list_controllers():
            # Only the list on screen rebuilds its rows for each snapshot and setting; the others
            # do once they appear.
            list_controller.defers_rebuilding_while_hidden = True
        self.settings_window_controller = SettingsWindowController.alloc().initWithOwner_(self)
        # The saved sessions (saved_agent_sessions_store): the main thread
        # alone reads and writes their file, so a rename or delete never waits on the core.
        self.saved_agent_sessions_file_path = SAVED_AGENT_SESSIONS_FILE_PATH
        self.saved_windows = load_saved_windows(self.saved_agent_sessions_file_path)
        self.saved_sessions_window_controller = SavedSessionsWindowController.alloc().initWithOwner_(self)
        # Calls back on the key filter's own thread.
        self.global_shortcut_key_filter = GlobalShortcutKeyFilter(lambda: AppHelper.callAfter(self.global_shortcut_pressed))
        self.connection_thread = DashboardCoreThread(self.receive_snapshot_on_background_thread,
                                                     self.receive_connection_lost_on_background_thread, verbose,
                                                     DOCKED_WINDOW_FRAMES_FILE_PATH, PAUSE_RECORDS_FILE_PATH,
                                                     hook_events_socket_path=HOOK_EVENTS_SOCKET_PATH,
                                                     hook_states_file_path=HOOK_STATES_FILE_PATH,
                                                     report_terminal_problem=self.receive_terminal_problem_on_background_thread)
        self.note_application_unless_it_is_the_dashboard(NSWorkspace.sharedWorkspace().frontmostApplication())
        notification_center = NSNotificationCenter.defaultCenter()
        for notification_name, selector_name in (
                (NSApplicationDidChangeScreenParametersNotification, "screenParametersChanged:"),
                (NSApplicationDidBecomeActiveNotification, "dashboardBecameActive:"),
                (NSApplicationDidResignActiveNotification, "dashboardResignedActive:"),
                (NSApplicationWillTerminateNotification, "applicationWillTerminate:")):
            notification_center.addObserver_selector_name_object_(self, selector_name, notification_name, None)
        NSWorkspace.sharedWorkspace().notificationCenter().addObserver_selector_name_object_(
            self, "anotherApplicationBecameActive:", NSWorkspaceDidActivateApplicationNotification, None)
        return self

    @objc.python_method
    def start(self):
        self.connection_thread.start()
        self.connection_thread.send_monitors(read_monitors_from_screens())
        if self.last_other_active_application is not None:
            self.connection_thread.send_last_other_active_application_process_id(
                self.last_other_active_application.processIdentifier())
        self.apply_settings_to_the_core_and_every_list(self.settings)
        self.apply_appearance(self.settings["appearance"])
        self.apply_start_with_iterm2_setting(self.settings["app_starts_with_iterm2"])
        self.global_shortcut_key_filter.use_global_shortcut(self.settings["global_shortcut"])
        if self.global_shortcut_key_filter.lacks_permission():
            print("agent-command-tab: the global shortcut needs the Accessibility permission (see Settings)", flush=True)
        if self.settings["show_as"] == "window":
            self.list_window_controller.show(self.settings["list_window_frame"], takes_keyboard_focus=False,
                                             stays_behind_other_windows=True)
        # Always once at start, also when not docked (reserve_screen_area only sends changes): the
        # core waits for it before giving back room still owed from before a restart.
        self.connection_thread.send_reserved_screen_area(self.reserved_screen_area)

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
        for list_controller in self.list_all_list_controllers():
            list_controller.apply_snapshot(snapshot)
        self.status_item_controller.show_snapshot(snapshot)
        if self.popover.isShown() and not self.dropdown_list_controller.rename_in_progress:
            self.dropdown_panel_controller.fit_size_to_rows()

    @objc.python_method
    def receive_connection_lost_on_background_thread(self, message):
        AppHelper.callAfter(self.exit_to_reconnect, message)

    @objc.python_method
    def receive_terminal_problem_on_background_thread(self, message):
        AppHelper.callAfter(self.show_terminal_problem, message)

    @objc.python_method
    def show_terminal_problem(self, message):
        """Shows a terminal that runs but can't be reached (message), or clears that line (None);
        the other terminals' agents stay listed meanwhile, so the sort control and Hide Idle keep their places."""
        for list_panel_controller in self.list_all_list_panel_controllers():
            list_panel_controller.show_message(message or "", sort_control_and_hide_idle_step_aside=False)

    @objc.python_method
    def exit_to_reconnect(self, message):
        """Shows the problem for a while, then exits so the supervisor reconnects.

        The `iterm2` package can't reconnect within one process. The app also runs without iTerm2
        (with tmux), so the message doesn't ask for iTerm2; a connection refused while iTerm2 runs
        shows its own hint (show_terminal_problem).
        """
        print(f"agent-command-tab: {message}; exiting so the supervisor reconnects", flush=True)
        explanation = f"{message}. Retrying soon."
        for list_panel_controller in self.list_all_list_panel_controllers():
            list_panel_controller.show_message(explanation)
        self.status_item_controller.show_connection_problem()
        AppHelper.callLater(SECONDS_TO_SHOW_CONNECTION_PROBLEM_BEFORE_EXITING, self.exit_now_with_reconnect_status)

    @objc.python_method
    def exit_now_with_reconnect_status(self):
        self.save_settings_before_exiting()  # as quit does
        sys.stdout.flush()
        os._exit(RECONNECT_EXIT_STATUS)

    # ----- owner methods for the list controllers and panels -----

    @objc.python_method
    def list_all_list_panel_controllers(self):
        """The dropdown's, the sidebar's and the list window's panels: each has its own list controller."""
        return [self.dropdown_panel_controller, self.sidebar_controller.list_panel_controller,
                self.list_window_controller.list_panel_controller]

    @objc.python_method
    def list_all_list_controllers(self):
        return [list_panel_controller.list_controller for list_panel_controller in self.list_all_list_panel_controllers()]

    @objc.python_method
    def menu_bar_icon_clicked(self):
        """A left click on the menu bar icon shows the list the way the settings ask: the dropdown
        opens or closes, an auto-hiding sidebar slides in for a few seconds or, while it is in, out
        (a sidebar set to stay visible stays, as for the global
        shortcut, and comes back if the menu's Show popup or Show window hid it), or the window
        comes to the front. An open settings window closes first, so a
        dropdown it kept open closes too. It also retries a global shortcut that lacked the
        permission, since the sidebar slides in without making the app active (the other retry)."""
        self.settings_window_controller.close_if_open()
        self.retry_the_global_shortcut_if_it_lacks_permission()
        self.close_a_dropdown_the_menu_showed()
        if self.settings["show_as"] == "sidebar":
            if self.settings["sidebar_mode"] == "auto_hide" and self.sidebar_controller.is_shown:
                self.sidebar_controller.hide_now()
            else:
                self.show_the_sidebar()
        elif self.settings["show_as"] == "window":
            self.show_the_list_window()
        else:
            self.status_item_controller.toggle_popover()

    @objc.python_method
    def show_the_list_as(self, show_as_choice):
        """The menu bar icon's menu's "Show popup", "Show sidebar" and "Show window": shows the list as that
        "Show as" choice would, whatever "Show as" is, without
        changing it. Nothing happens while the list already shows that way; otherwise the other
        ways it shows are hidden first, a sidebar set to stay visible too (until it is shown
        again), and a window behind other windows too. A sidebar shown
        while "Show as" is another slides out again after a few seconds, as an auto-hiding one does."""
        if show_as_choice in self.list_the_ways_the_list_shows():
            return
        if show_as_choice != "popup" and self.popover.isShown():
            self.close_dropdown()
        if show_as_choice != "sidebar" and self.sidebar_controller.is_shown:
            self.sidebar_controller.hide_until_shown_again()
        if show_as_choice != "window" and self.list_window_controller.window.isVisible():
            self.hide_the_list_window()
        if show_as_choice == "popup":
            self.status_item_controller.show_popover()
        elif show_as_choice == "sidebar":
            self.show_the_sidebar()
        else:
            self.show_the_list_window()

    @objc.python_method
    def list_the_ways_the_list_shows(self):
        """Returns which of "popup", "sidebar" and "window" show the list right now: the window
        only while it is in front (as for the global shortcut), so Show window brings forward one
        that other windows cover or that is on another desktop."""
        return [show_as_choice for show_as_choice, is_shown in (
            ("popup", self.popover.isShown()), ("sidebar", self.sidebar_controller.is_shown),
            ("window", self.list_window_controller.is_in_front())) if is_shown]

    @objc.python_method
    def show_the_sidebar(self):
        """Slides the sidebar in: one set to stay visible (Show as: Sidebar, an Always mode) for
        good, a docked one taking its room again, also after the menu hid it
        (SidebarController.hide_until_shown_again); any other for a few seconds."""
        if self.settings["show_as"] == "sidebar" and self.settings["sidebar_mode"] != "auto_hide":
            self.sidebar_controller.show_again()
        else:
            self.sidebar_controller.show_for_a_few_seconds()

    @objc.python_method
    def close_a_dropdown_the_menu_showed(self):
        """Closes a dropdown that the menu bar icon's menu's Show popup opened while "Show as" is
        Sidebar or Window (an open settings window keeps it open), before a click on the icon or
        the global shortcut shows the list the way the settings ask.
        Returns whether it closed one."""
        if self.settings["show_as"] == "popup" or not self.popover.isShown():
            return False
        self.close_dropdown()
        return not self.popover.isShown()

    @objc.python_method
    def global_shortcut_pressed(self):
        """The global shortcut shows the list as the menu bar icon does and, pressed again, hides
        it, giving the keyboard back to the app that had it
        (choose_what_the_global_shortcut_does). The list it shows has the first agent asking a
        question selected, or while none asks, the tab in front."""
        closed_a_dropdown_the_menu_showed = self.close_a_dropdown_the_menu_showed()
        what_to_do = choose_what_the_global_shortcut_does(
            self.settings["show_as"], self.settings["sidebar_mode"], self.sidebar_controller.is_shown,
            self.list_window_controller.is_in_front(), self.popover.isShown())
        if what_to_do in ("show_sidebar", "hide_sidebar") and closed_a_dropdown_the_menu_showed:
            # The sidebar takes no keyboard, so the app that had it before the dropdown gets it
            # back, as when the shortcut closes the dropdown itself.
            self.give_activation_to(self.application_in_front_before_dropdown)
        if what_to_do == "show_sidebar":
            self.show_the_sidebar()  # for a few seconds, or for good if it stays visible
            self.sidebar_controller.list_controller.select_the_first_asking_tab_or_else_the_tab_in_front()
        elif what_to_do == "hide_sidebar":
            self.sidebar_controller.hide_now()
        elif what_to_do == "show_window":
            self.show_the_list_window()
            self.list_window_controller.list_controller.select_the_first_asking_tab_or_else_the_tab_in_front()
        elif what_to_do == "hide_window":
            self.hide_the_list_window()
            if NSApp.isActive():  # otherwise (always on top) the app with the keyboard keeps it
                self.give_activation_to(self.last_other_active_application)
        elif what_to_do == "open_dropdown":
            self.status_item_controller.show_popover()
            self.dropdown_list_controller.select_the_first_asking_tab_or_else_the_tab_in_front()
        elif what_to_do == "close_dropdown":
            self.close_dropdown()
            self.give_activation_to(self.application_in_front_before_dropdown)

    @objc.python_method
    def close_the_list_after_escape(self, list_controller):
        """Esc in a list closes it and gives the keyboard back to the app that had it, as a second
        press of the global shortcut does; a sidebar set to stay
        visible stays, and gives the keyboard back."""
        if list_controller is self.dropdown_list_controller:
            self.close_dropdown()
            self.give_activation_to(self.application_in_front_before_dropdown)
            return
        if list_controller is self.list_window_controller.list_controller:
            self.hide_the_list_window()
        elif self.sidebar_controller.hides_by_itself():  # also one the menu's Show sidebar slid in
            self.sidebar_controller.hide_now()
        self.give_activation_to(self.last_other_active_application)

    def settingsShortcutPressed_(self, sender):
        self.open_settings()

    @objc.python_method
    def show_the_list_window(self):
        """Brings the list window to the front with the keyboard (the menu bar icon and the shortcut)."""
        self.note_application_unless_it_is_the_dashboard(NSWorkspace.sharedWorkspace().frontmostApplication())
        self.list_window_controller.show(self.settings["list_window_frame"])
        self.place_the_settings_and_saved_sessions_windows_above_the_lists(self.popover.isShown())

    @objc.python_method
    def give_activation_to(self, application):
        """Makes an app the active one again (nothing for None)."""
        if application is not None:
            yield_activation_to(application)
            application.activateWithOptions_(0)

    @objc.python_method
    def global_shortcut_lacks_permission(self):
        """Whether a global shortcut is chosen but macOS refuses its key filter (no Accessibility permission)."""
        return self.global_shortcut_key_filter.lacks_permission()

    @objc.python_method
    def retry_the_global_shortcut_if_it_lacks_permission(self):
        """The Accessibility permission may have been given since macOS refused the shortcut's key
        filter (Settings' button opens its page): tries again, and updates an open settings window."""
        if self.global_shortcut_key_filter.lacks_permission():
            self.global_shortcut_key_filter.use_global_shortcut(self.settings["global_shortcut"])
            self.settings_window_controller.reflect_settings(self.settings)

    @objc.python_method
    def window_frame_changed(self, setting_name, frame):
        """The list window or the settings window was moved or resized: its frame is kept under
        setting_name (settings_store.SMALLEST_WINDOW_SIZES), so it opens there next time. Nothing
        else depends on a frame, so settings_changed's other steps are skipped, and the settings
        file is written at most every SECONDS_BEFORE_SAVING_A_MOVED_WINDOWS_FRAME while it moves (a
        drag moves it at every pointer step), the last time after it stops."""
        if frame == self.settings[setting_name]:
            return
        self.settings = dict(self.settings, **{setting_name: frame})
        if not self.settings_save_is_scheduled:
            self.settings_save_is_scheduled = True
            AppHelper.callLater(SECONDS_BEFORE_SAVING_A_MOVED_WINDOWS_FRAME, self.save_settings_if_scheduled)

    @objc.python_method
    def save_settings_if_scheduled(self):
        if self.settings_save_is_scheduled:
            self.settings_save_is_scheduled = False
            save_settings(self.settings)

    # Dragging the dropdown's top bar detaches it into the list window, and dropping the list
    # window on the menu bar icon turns it back into the dropdown.
    # Dragging the sidebar's top bar turns it into the list window too.

    @objc.python_method
    def top_bar_dragged(self, list_panel_controller, start_point_on_screen):
        """A list's top bar was dragged: the dropdown's turns into the list window where the
        dropdown was, and the sidebar's into the list window at the window's last size (the user's
        choice), its top bar under the pointer. The window then follows the pointer until the
        button comes up."""
        list_window = self.list_window_controller.window
        if list_panel_controller is self.dropdown_panel_controller and self.popover.isShown():
            dropdown_window = list_panel_controller.view().window()
            content_rect = dropdown_window.convertRectToScreen_(list_panel_controller.view().frame())
            # A dropdown with few rows is shorter than the list window may be; it grows downward.
            window_frame = grow_frame_to_smallest_size(
                convert_rect_to_frame_list(list_window.frameRectForContentRect_(content_rect)),
                SMALLEST_WINDOW_SIZES["list_window_frame"])
        elif list_panel_controller is self.sidebar_controller.list_panel_controller:
            # Its saved frame, or the size it was built with if it has never been shown.
            saved_frame = self.settings["list_window_frame"]
            content_size = list_window.contentRectForFrameRect_(
                NSMakeRect(*saved_frame) if saved_frame is not None else list_window.frame()).size
            content_rect = compute_list_window_content_rect_for_a_dragged_sidebar(
                convert_rect_to_frame_list(self.sidebar_controller.window.frame()), start_point_on_screen,
                (content_size.width, content_size.height))
            window_frame = convert_rect_to_frame_list(list_window.frameRectForContentRect_(NSMakeRect(*content_rect)))
            # At once, without sliding: the window takes its place. Even one shown from the menu,
            # which "Show as: Window" would leave in for a few seconds.
            self.sidebar_controller.hide_now(animate=False)
        else:
            return
        pointer_offset = (start_point_on_screen[0] - window_frame[0], start_point_on_screen[1] - window_frame[1])
        show_as_before = self.settings["show_as"]
        self.settings_changed(dict(self.settings, show_as="window", list_window_frame=window_frame))
        if show_as_before == "window":
            # The dropdown or the sidebar shown from the menu bar icon's menu while the list shows
            # as a window, which may be closed or behind other windows: settings_changed closes
            # the dropdown and shows the window only when "Show as" changes.
            self.close_dropdown()
            self.list_window_controller.show(window_frame, takes_keyboard_focus=not self.settings_window_controller.is_open())
            self.place_the_settings_and_saved_sessions_windows_above_the_lists(self.popover.isShown())
            if self.settings_window_controller.is_open():  # it stays in front, as when settings_changed shows the window
                self.settings_window_controller.window.orderFront_(None)
        # A window that was open already (behind other windows, say) keeps its frame in show, so
        # it takes the dragged one here, or pointer_offset wouldn't put its top bar under the pointer.
        list_window.setFrame_display_(NSMakeRect(*window_frame), True)
        self.list_window_controller.follow_the_pointer_until_the_button_comes_up(pointer_offset)

    @objc.python_method
    def list_window_drag_started(self):
        """The list window's title bar is being dragged: until the button comes up, the menu bar
        icon is highlighted while the pointer is on it."""
        if self.list_window_drag_check_timer is None:
            self.list_window_drag_check_timer = NSTimer.timerWithTimeInterval_target_selector_userInfo_repeats_(
                0.05, self, "listWindowDragCheckTimerFired:", None, True)
            NSRunLoop.currentRunLoop().addTimer_forMode_(self.list_window_drag_check_timer, NSRunLoopCommonModes)

    def listWindowDragCheckTimerFired_(self, timer):
        if NSEvent.pressedMouseButtons() & 1:
            self.list_window_is_being_dragged()
            return
        timer.invalidate()
        self.list_window_drag_check_timer = None
        self.list_window_drag_ended()

    @objc.python_method
    def list_window_is_being_dragged(self):
        self.status_item_controller.highlight(self.pointer_is_on_the_menu_bar_icon())

    @objc.python_method
    def list_window_drag_ended(self):
        """Dropped on the menu bar icon, the list window turns back into the dropdown: with Show
        as: Window, Show as becomes Popup; a window the menu's Show window opened for another Show
        as just hides, leaving the settings as they are."""
        self.status_item_controller.highlight(False)
        if not self.pointer_is_on_the_menu_bar_icon():
            return
        if self.settings["show_as"] == "window":
            self.settings_changed(dict(self.settings, show_as="popup"))
        else:
            self.hide_the_list_window()
        self.status_item_controller.show_popover()

    @objc.python_method
    def pointer_is_on_the_menu_bar_icon(self):
        # NSMouseInRect counts the frame's top edge as inside: a pointer pushed against the top of
        # the screen, the usual way to aim at the menu bar, is exactly there.
        return NSMouseInRect(NSEvent.mouseLocation(), self.status_item_controller.find_icon_frame_on_screen(), False)

    @objc.python_method
    def submit_action(self, action_request):
        """Sends an action to the core; returns a future that completes with its ActionResult."""
        if action_request.action in ("focus_agent_session", "focus_window", "peek_agent_session"):
            # macOS may refuse another app's activation while the dashboard is active, so hand it over first.
            for iterm2_application in list_running_iterm2_applications():
                yield_activation_to(iterm2_application)
        future = self.connection_thread.submit_action(action_request)
        future.add_done_callback(self.bring_forward_the_app_running_the_terminal_client_of_the_result)
        return future

    @objc.python_method
    def bring_forward_the_app_running_the_terminal_client_of_the_result(self, future):
        """After an action that showed a tab in a tmux client running inside a terminal app other
        than iTerm2 (its result's details {"terminal_client_process_id"}; the core brings an iTerm2
        tab running the client to the front itself): brings that app to the front, the nearest app
        above the client's process. Runs on the core's thread, so it hands the work to the main thread."""
        action_result = None if future.cancelled() or future.exception() else future.result()
        client_process_id = ((action_result.details or {}).get("terminal_client_process_id")
                             if action_result is not None and action_result.succeeded else None)
        if client_process_id is not None:
            AppHelper.callAfter(self.bring_forward_the_app_running_the_process, client_process_id)

    @objc.python_method
    def bring_forward_the_app_running_the_process(self, process_id):
        """Brings the nearest app above a process (the terminal app running a tmux client) to the front."""
        for ancestor_process_id in list_the_process_and_its_ancestor_ids(process_id):
            application = NSRunningApplication.runningApplicationWithProcessIdentifier_(ancestor_process_id)
            if application is not None and ancestor_process_id != os.getpid():
                self.give_activation_to(application)
                return

    def popoverDidShow_(self, notification):
        self.place_the_settings_and_saved_sessions_windows_above_the_lists(dropdown_is_open=True)  # an open settings window stays above it

    def popoverWillClose_(self, notification):
        self.status_item_controller.note_dropdown_closing()  # so a click on the icon that closed it doesn't open it again
        self.dropdown_list_controller.cancel_rename()  # an open rename would keep the list from updating
        self.place_the_settings_and_saved_sessions_windows_above_the_lists(dropdown_is_open=False)

    @objc.python_method
    def a_peek_or_a_drag_keeps_the_dropdown_open(self):
        """Whether a tab shown for a moment (a peek brings iTerm2 forward) or a row being dragged in
        the dropdown keeps the dropdown open, though the dashboard may not be the active app meanwhile."""
        return (self.peek_in_progress is not None or self.peek_waiting_for_put_back is not None
                or self.dropdown_list_controller.drag_in_progress)

    @objc.python_method
    def update_dropdown_closing_behavior(self):
        """The dropdown closes when you click elsewhere, except while a tab is shown for a moment,
        while a row is dragged in it and while the settings window is
        open (changing settings mustn't close it)."""
        keeps_the_dropdown_open = self.a_peek_or_a_drag_keeps_the_dropdown_open() or self.settings_window_controller.is_open()
        self.popover.setBehavior_(NSPopoverBehaviorApplicationDefined if keeps_the_dropdown_open else NSPopoverBehaviorTransient)

    @objc.python_method
    def settings_window_closed(self):
        self.update_dropdown_closing_behavior()

    @objc.python_method
    def list_window_closed(self):
        """The list window's close button hid it: the settings window no longer stays level with it."""
        self.place_the_settings_and_saved_sessions_windows_above_the_lists(self.popover.isShown())

    @objc.python_method
    def hide_the_list_window(self):
        """Hides the list window, as its close button does (list_window_closed)."""
        self.list_window_controller.hide()
        self.list_window_closed()

    @objc.python_method
    def note_application_in_front_before_dropdown(self, application):
        """The dropdown activates the dashboard; a peek ends by bringing back what was in front
        before, and Esc or the shortcut give it the keyboard back. When that was the dashboard
        itself with no window left to take the keyboard (the list window that Show popup hid, or
        the settings window that a click on the icon closed), the last other active app stands
        in; a settings window, Saved sessions window or list window still open gets the keyboard back."""
        if (application is not None and application.processIdentifier() == os.getpid()
                and not self.settings_window_controller.is_open() and not self.saved_sessions_window_controller.is_open()
                and not self.list_window_controller.window.isVisible()):
            application = self.last_other_active_application
        self.application_in_front_before_dropdown = application

    @objc.python_method
    def start_peek(self, session_id):
        """Pressing and holding a tab row: shows the tab until the button is released. The dropdown
        stays open meanwhile, though iTerm2 becomes the active app."""
        peek_still_being_put_back, self.peek_waiting_for_put_back = self.peek_waiting_for_put_back, None
        if peek_still_being_put_back is not None:
            # Held again before the last peek was put back: iTerm2 is still in front, so take over
            # what that peek was to bring back (its own put-back now does nothing).
            self.peek_in_progress = dict(peek_still_being_put_back)
        else:
            application_in_front = NSWorkspace.sharedWorkspace().frontmostApplication()
            if application_in_front is not None and application_in_front.processIdentifier() == os.getpid():
                # The dashboard is in front: with the dropdown open, bring back what was in front before
                # it opened; with the list window in front, the dashboard itself; otherwise (Settings or
                # a rename is open) bring back nothing.
                if self.popover.isShown():
                    application_in_front = self.application_in_front_before_dropdown
                elif not self.list_window_controller.window.isKeyWindow():
                    application_in_front = None
            self.peek_in_progress = {"application_in_front": application_in_front}
        self.update_dropdown_closing_behavior()
        self.submit_action(ActionRequest("peek_agent_session", {"session_id": session_id}))

    @objc.python_method
    def end_peek(self):
        """The button was released: puts back whatever tab, window or app was in front before."""
        peek, self.peek_in_progress = self.peek_in_progress, None
        if peek is None:
            return
        application = peek["application_in_front"]
        iterm2_was_in_front = application is not None and application.bundleIdentifier() == ITERM2_BUNDLE_IDENTIFIER
        self.peek_waiting_for_put_back = peek
        end_peek_future = self.submit_action(ActionRequest("end_peek", {}, {"bring_back_iterm2_window": iterm2_was_in_front}))
        # Only once the core is done (after the peek's own activation of iTerm2, which may still
        # be under way), or iTerm2 would come back in front of the app brought back here.
        end_peek_future.add_done_callback(
            lambda future: AppHelper.callAfter(self.bring_back_application_after_peek, peek, iterm2_was_in_front))

    @objc.python_method
    def bring_back_application_after_peek(self, peek, iterm2_was_in_front):
        if self.peek_waiting_for_put_back is not peek:
            return  # a newer hold took over; its own release puts things back
        self.peek_waiting_for_put_back = None
        if not iterm2_was_in_front:
            self.give_activation_to(peek["application_in_front"])
        self.update_dropdown_closing_behavior()

    @objc.python_method
    def tab_was_focused(self, list_controller):
        """The dropdown closes after a tab is focused; the sidebar stays until the pointer leaves."""
        if list_controller is self.dropdown_list_controller and self.popover.isShown():
            self.popover.performClose_(None)

    @objc.python_method
    def row_drag_began_or_ended(self):
        self.update_dropdown_closing_behavior()

    @objc.python_method
    def rows_changed(self, list_controller):
        if list_controller is self.dropdown_list_controller and self.popover.isShown():
            self.dropdown_panel_controller.fit_size_to_rows()

    @objc.python_method
    def list_agent_tool_names(self):
        """The agent tools the menus offer for new agent sessions: those the settings include."""
        return list_included_agent_tool_names(self.settings)

    @objc.python_method
    def confirm_closing_window(self, window_entry):
        """Asks "Are you sure?" before an iTerm2 window, and the agent sessions in it, is closed."""
        agent_session_count = len(window_entry.agent_sessions)
        other_tab_count = window_entry.total_tab_count - agent_session_count
        explanation = (f'Closing "{build_window_title_shown_in_the_list(window_entry)}" ends the {agent_session_count} agent '
                       f'session{"" if agent_session_count == 1 else "s"} running in it')
        if other_tab_count:
            explanation += f' and closes its {other_tab_count} other tab{"" if other_tab_count == 1 else "s"}'
        return self.ask_for_confirmation(explanation + ".", "close_window")

    @objc.python_method
    def ask_for_confirmation(self, explanation, action_name):
        """Asks "Are you sure?" with this explanation before the action (one of
        settings_store.ACTIONS_THAT_ASK_FOR_CONFIRMATION); returns True for Yes. No is the default
        button, so Return keeps things as they are. A Yes with "Remember my choice" checked is
        saved, and the action then runs without asking; a No isn't remembered, so a menu item
        never silently does nothing (with no setting to ask again)."""
        if action_name in self.settings["actions_that_run_without_asking"]:
            return True
        alert = NSAlert.alloc().init()
        alert.setAlertStyle_(NSAlertStyleWarning)
        alert.setMessageText_("Are you sure?")
        alert.setInformativeText_(explanation)
        alert.addButtonWithTitle_("No")  # the first button is the default and sits rightmost
        alert.addButtonWithTitle_("Yes")
        alert.setShowsSuppressionButton_(True)  # AppKit's "Do not show this message again" checkbox
        alert.suppressionButton().setTitle_("Remember my choice")
        alert.suppressionButton().setToolTip_(
            "With Yes, this action will be applied without asking from now on. A No isn't remembered.")
        answer_is_yes = self.run_alert_in_front(alert) == NSAlertSecondButtonReturn
        if answer_is_yes and alert.suppressionButton().state():
            self.settings_changed(dict(self.settings, actions_that_run_without_asking=[
                *self.settings["actions_that_run_without_asking"], action_name]))
        return answer_is_yes

    @objc.python_method
    def install_hooks(self, agent_tool_names):
        """Adds the app's hooks to these agent tools' settings files (the list's Install Hooks
        button), once the user agrees, then has the core check them again."""
        home_folder = os.path.expanduser("~")
        file_paths = [shorten_home_folder(file_path, home_folder) for agent_tool_name in agent_tool_names
                      for file_path in list_settings_file_paths(agent_tool_name)]
        alert = NSAlert.alloc().init()
        alert.setMessageText_(f"Install the hooks for {' and '.join(agent_tool_names)}?")
        alert.setInformativeText_(
            "They are added to:\n\n" + "\n".join(file_paths)
            # The installer copies a file only the first time.
            + f"\n\nThe first time, each file is copied to its name plus {BACKUP_FILE_SUFFIX}, which keeps it as it was "
              "before the app changed it. Sessions started after this will use the hooks."
            + (" Codex then asks you to approve them: type /hooks in Codex." if "Codex" in agent_tool_names else ""))
        alert.addButtonWithTitle_("Install")
        alert.addButtonWithTitle_("Cancel")
        if self.run_alert_in_front(alert) != NSAlertFirstButtonReturn:
            return
        # Each tool on its own, so a file of one that can't be changed doesn't keep the others' hooks out.
        failures = []
        for agent_tool_name in agent_tool_names:
            try:
                install_hooks(agent_tool_name)
            except (OSError, ValueError) as error:
                failures.append(f"{agent_tool_name}: {error}")
        if failures:
            self.show_message_alert("The hooks couldn't be installed", "\n".join(failures))
        self.connection_thread.send_hooks_changed()

    @objc.python_method
    def ask_for_note_asking_agent_sessions_to_talk_to_each_other(self, agent_session_entries, left_out_agent_session_entries):
        """Shows the Talk to Each Other window; returns (the note as the
        user left it, the agent session entries chosen to get it: one, or all of them), or None for Cancel or
        an empty note."""
        alert, note_text_view, recipient_choices = build_talk_to_each_other_alert(agent_session_entries,
                                                                                  left_out_agent_session_entries)
        if self.run_alert_in_front(alert) != NSAlertFirstButtonReturn or not note_text_view.string().strip():
            return None
        return note_text_view.string().strip(), recipient_choices.list_chosen_recipients()

    @objc.python_method
    def show_message_alert(self, title, text):
        """Shows an alert with only an OK button (run_alert_in_front)."""
        alert = NSAlert.alloc().init()
        alert.setMessageText_(title)
        alert.setInformativeText_(text)
        self.run_alert_in_front(alert)

    @objc.python_method
    def run_alert_in_front(self, alert):
        """Shows an alert and returns the button chosen, after closing the dropdown, which (with a
        settings window kept above it) would cover the alert while it waits for an answer. The
        sidebar, above other apps' windows at the status bar's level, goes just under the alert's
        level meanwhile: a wide one would cover it. The alert shows the app's icon: NSAlert takes
        Python.app's own (its rocket), whatever NSApp's icon is set to."""
        alert.setIcon_(build_app_icon())
        self.close_dropdown()
        NSApp.activateIgnoringOtherApps_(True)
        sidebar_window = self.sidebar_controller.window
        sidebar_window_level = sidebar_window.level()
        sidebar_window.setLevel_(NSModalPanelWindowLevel - 1)
        try:
            return alert.runModal()
        finally:
            sidebar_window.setLevel_(sidebar_window_level)

    @objc.python_method
    def submit_action_and_report_failure(self, action_request, failure_title, after_success=None, after_failure=None,
                                         after_any_result=None):
        """Sends an action the user chose from a menu to the core and, should it fail, says why
        in an alert, since the list alone wouldn't show it (a command not typed, say). Once it
        succeeds, after_success(action_result), if given, runs on the main thread; once it
        fails, after_failure(), if given, does. after_any_result(action_result), if given, runs
        on the main thread first either way (for the details of what a partly failed action did)."""

        def handle_action_result(action_result):
            if after_any_result is not None:
                after_any_result(action_result)
            if not action_result.succeeded:
                if after_failure is not None:
                    after_failure()
                self.report_action_failure(action_result, failure_title)
            elif after_success is not None:
                after_success(action_result)
        self.submit_action(action_request).add_done_callback(
            lambda done_future: AppHelper.callAfter(handle_action_result, done_future.result()))

    @objc.python_method
    def report_action_failure(self, action_result, failure_title):
        self.show_message_alert(failure_title, action_result.message[:1].upper() + action_result.message[1:] + ".")

    @objc.python_method
    def show_process_info(self, session_id, process_id):
        """Shows a sub row's Info window once the core has read its
        process's details, or an alert saying why it couldn't."""
        self.submit_action_and_report_failure(
            ActionRequest("read_child_process_details", {"session_id": session_id}, {"process_id": process_id}),
            "The process info couldn't be read",
            lambda action_result: self.run_alert_in_front(build_process_info_alert(action_result.details, time.time())))

    @objc.python_method
    def show_agent_session_info(self, agent_session_entry):
        """Shows a tab row's Session info window once the core has read its agent session's
        details, or an alert saying why it couldn't."""
        self.submit_action_and_report_failure(
            ActionRequest("read_agent_session_details", {"session_id": agent_session_entry.session_id}),
            "The session info couldn't be read",
            lambda action_result: self.run_alert_in_front(
                build_session_info_alert(agent_session_entry, action_result.details, time.time())))

    @objc.python_method
    def copy_contact_info(self, session_id):
        """Puts on the clipboard what another agent session needs to send this one a message
        (a tab row's "Copy contact info"), or says why it can't take messages."""

        def put_text_on_the_clipboard(action_result):
            pasteboard = NSPasteboard.generalPasteboard()
            pasteboard.clearContents()
            pasteboard.setString_forType_(action_result.details["text"], NSPasteboardTypeString)
        self.submit_action_and_report_failure(ActionRequest("build_contact_info", {"session_id": session_id}),
                                              "The contact info wasn't copied", put_text_on_the_clipboard)

    @objc.python_method
    def find_launch_command(self, agent_tool_name):
        return find_launch_command(self.settings, agent_tool_name)

    @objc.python_method
    def duplicate_agent_session(self, tab_id, folder, agent_tool_name):
        """Opens a copy of a tab's agent session right after it, continuing a copy of its
        conversation where the tool allows (like Chrome's Duplicate).
        A failure (the tab's folder deleted since, say) is reported, as for the other menu actions."""
        self.submit_action_and_report_failure(
            ActionRequest("duplicate_agent_session", {"tab_id": tab_id},
                          {"folder": folder or "~", "launch_command": self.find_launch_command(agent_tool_name)}),
            "The tab wasn't duplicated")

    @objc.python_method
    def start_agent_session_from_menu(self, window_id, folder, agent_tool_name):
        """Starts an agent tool in a new tab of a window, or in a new window in the
        folder of the tab in front, else of the tab used most recently within a day, else the
        home folder (no folder picker). A failure is reported."""
        launch_command = self.find_launch_command(agent_tool_name)
        if window_id is not None:
            self.submit_action_and_report_failure(ActionRequest("start_agent_session", {"window_id": window_id},
                                                                {"folder": folder or "~", "launch_command": launch_command}),
                                                  "The agent wasn't started")
            return
        with self.latest_snapshot_lock:
            snapshot = self.latest_snapshot
        self.close_dropdown()
        self.submit_action_and_report_failure(ActionRequest("start_agent_session", {}, {
            "folder": choose_folder_for_new_agent_window(snapshot, time.time(), os.path.expanduser("~")),
            "launch_command": launch_command}), "The agent wasn't started", self.select_the_new_window_once_it_shows)

    @objc.python_method
    def select_the_new_window_once_it_shows(self, action_result):
        """Has every list select the tab row of the window start_agent_session opened once the
        window shows in it (in the folder of another window, it looked
        like that window)."""
        if action_result.details is None:  # iTerm2 gave no window: its session ended right away
            return
        for list_controller in self.list_all_list_controllers():
            list_controller.select_window_once_it_shows(action_result.details["window_id"])

    # ----- saved sessions -----

    @objc.python_method
    def has_saved_agent_sessions(self):
        """Whether anything is saved, which puts "Restore sessions…" in the menu bar icon's menu
        and makes it clickable in the row menus."""
        return bool(self.saved_windows)

    @objc.python_method
    def open_saved_sessions_window(self):
        """Opens the Saved sessions window ("Restore sessions…" in the menu bar icon's menu and the
        row menus), clear of a shown sidebar."""
        self.saved_sessions_window_controller.show(self.saved_windows, self.settings["saved_sessions_window_frame"])
        self.move_window_clear_of_the_sidebar(self.saved_sessions_window_controller)
        self.place_the_settings_and_saved_sessions_windows_above_the_lists(self.popover.isShown())

    @objc.python_method
    def save_agent_sessions(self, session_ids):
        """Saves these agent sessions' conversations (a row's "Save session" or "Save sessions"):
        the core works out each one's conversation; those it found are added to the saved list,
        also when others weren't saved, which an alert then names with the reason."""

        def keep_the_saved_windows(action_result):
            saved_windows = (action_result.details or {}).get("saved_windows") or []
            if saved_windows:
                self.change_saved_windows(merge_saved_windows(self.saved_windows, saved_windows))
        self.submit_action_and_report_failure(
            ActionRequest("save_agent_sessions", {"session_ids": session_ids}),
            "The session wasn't saved" if len(session_ids) == 1 else "Not every session was saved",
            after_any_result=keep_the_saved_windows)

    @objc.python_method
    def change_saved_windows(self, saved_windows):
        """Keeps the new saved list, writes it to its file and shows it in an open Saved sessions
        window. A write that fails (a full disk) is reported; the list stays changed until the app quits."""
        self.saved_windows = saved_windows
        try:
            save_saved_windows(self.saved_agent_sessions_file_path, saved_windows)
        except OSError as error:
            self.show_message_alert("The saved sessions couldn't be written", f"{self.saved_agent_sessions_file_path}: {error}")
        self.saved_sessions_window_controller.show_saved_windows(saved_windows)

    @objc.python_method
    def rename_saved_window(self, window_key, new_name):
        self.change_saved_windows(rename_saved_window(self.saved_windows, window_key, new_name))

    @objc.python_method
    def rename_saved_session(self, conversation_id, new_name):
        self.change_saved_windows(rename_saved_session(self.saved_windows, conversation_id, new_name))

    @objc.python_method
    def delete_saved_items(self, window_keys, conversation_ids):
        self.change_saved_windows(delete_saved_items(self.saved_windows, window_keys, conversation_ids))

    @objc.python_method
    def restore_saved_agent_sessions(self, window_keys, conversation_ids):
        """Asks the core to resume the saved sessions of these saved windows and these saved
        sessions, with each agent tool's launch command from the settings. Each one it resumed
        leaves the saved list; an alert names those that failed and
        why, and they stay saved."""
        saved_windows_to_restore = choose_saved_windows_to_restore(self.saved_windows, window_keys, conversation_ids)
        saved_session_count = sum(len(saved_window["saved_sessions"]) for saved_window in saved_windows_to_restore)

        def take_the_restored_sessions_out_of_the_list(action_result):
            restored_conversation_ids = set((action_result.details or {}).get("restored_conversation_ids") or [])
            if restored_conversation_ids:
                self.change_saved_windows(delete_saved_items(self.saved_windows, set(), restored_conversation_ids))
        self.submit_action_and_report_failure(
            ActionRequest("restore_saved_agent_sessions", {}, {
                "saved_windows": saved_windows_to_restore,
                "launch_command_by_agent_tool_name": {agent_tool["agent_tool_name"]: agent_tool["launch_command"]
                                                      for agent_tool in self.settings["agent_tools"]}}),
            "The session wasn't restored" if saved_session_count == 1 else "Not every session was restored",
            after_any_result=take_the_restored_sessions_out_of_the_list)

    @objc.python_method
    def open_settings(self):
        """Opens the settings window; over an open dropdown, which stays open while it is,
        and clear of a shown sidebar."""
        self.settings_window_controller.show(self.settings)
        self.move_window_clear_of_the_sidebar(self.settings_window_controller)
        self.place_the_settings_and_saved_sessions_windows_above_the_lists(self.popover.isShown())
        self.update_dropdown_closing_behavior()

    @objc.python_method
    def move_window_clear_of_the_sidebar(self, window_controller):
        """Moves the settings window or the Saved sessions window (window_controller's) sideways,
        keeping its size where the monitor has room, off a shown sidebar's strip: the sidebar is
        above other windows and would cover it. Beside a sidebar too wide to leave the window its
        smallest width (SMALLEST_WINDOW_SIZES of the controller's frame_setting_name), it stays
        where it is: made narrower, its rows were cut off."""
        sidebar_controller = self.sidebar_controller
        if not sidebar_controller.window.isVisible() or sidebar_controller.placement is None:
            return
        window = window_controller.window
        window_frame = convert_rect_to_frame_list(window.frame())
        frame_clear_of_the_sidebar = compute_frame_clear_of_area(
            window_frame, convert_rect_to_frame_list(sidebar_controller.window.frame()), sidebar_controller.placement.edge,
            sidebar_controller.screens[sidebar_controller.placement.screen_index]["visible_frame"], window_frame[2])
        if (frame_clear_of_the_sidebar is not None
                and frame_clear_of_the_sidebar[2] >= SMALLEST_WINDOW_SIZES[window_controller.frame_setting_name][0]):
            window.setFrame_display_(NSMakeRect(*frame_clear_of_the_sidebar), True)

    @objc.python_method
    def place_the_settings_and_saved_sessions_windows_above_the_lists(self, dropdown_is_open):
        """Puts the settings window and the Saved sessions window at the level of the windows they
        would otherwise open under: just above an open dropdown, and level with the list window
        while that is shown and always on top (in front of it when opened); otherwise at the normal
        level. Not the sidebar's: they open clear of it instead (move_window_clear_of_the_sidebar),
        so other apps' windows can still cover them (and covering the settings window closes it)."""
        window_levels = []
        if dropdown_is_open:
            window_levels.append(self.dropdown_panel_controller.view().window().level() + 1)
        list_window = self.list_window_controller.window
        if list_window.isVisible() and self.settings["list_window_is_always_on_top"]:
            window_levels.append(list_window.level())
        for window_controller in (self.settings_window_controller, self.saved_sessions_window_controller):
            window_controller.set_window_level(max(window_levels) if window_levels else None)

    # ----- settings -----

    @objc.python_method
    def settings_changed(self, new_settings):
        """Saves new settings and applies them to the global shortcut, the appearance, the sidebar,
        the list window (shown when "Show as" turns to Window, hidden when it turns away) and an
        open settings window."""
        show_as_before = self.settings["show_as"]
        list_window_was_always_on_top = self.settings["list_window_is_always_on_top"]
        sidebar_may_have_moved = any(new_settings[name] != self.settings[name] for name in SETTINGS_THAT_PLACE_THE_SIDEBAR)
        if new_settings["global_shortcut"] != self.settings["global_shortcut"]:
            self.global_shortcut_key_filter.use_global_shortcut(new_settings["global_shortcut"])
        if new_settings["appearance"] != self.settings["appearance"]:
            self.apply_appearance(new_settings["appearance"])
        if new_settings["app_starts_with_iterm2"] != self.settings["app_starts_with_iterm2"]:
            self.apply_start_with_iterm2_setting(new_settings["app_starts_with_iterm2"])
        self.settings = new_settings
        save_settings(new_settings)
        self.settings_save_is_scheduled = False  # a frame waiting to be written is in new_settings too
        list_window_is_shown_now = new_settings["show_as"] == "window" and show_as_before != "window"
        if new_settings["show_as"] != show_as_before:
            self.close_dropdown()
            if list_window_is_shown_now:
                # Chosen in the settings window, which keeps the keyboard.
                self.list_window_controller.show(new_settings["list_window_frame"],
                                                 takes_keyboard_focus=not self.settings_window_controller.is_open())
            elif show_as_before == "window":
                self.list_window_controller.hide()
        self.apply_settings_to_the_core_and_every_list(new_settings, sidebar_settings_changed=sidebar_may_have_moved)
        self.settings_window_controller.reflect_settings(new_settings)
        if sidebar_may_have_moved:  # a sidebar widened at its edge or in the window would cover them
            for window_controller in (self.settings_window_controller, self.saved_sessions_window_controller):
                if window_controller.is_open():
                    self.move_window_clear_of_the_sidebar(window_controller)
        # Also with the settings window closed: an open Saved sessions window stays above a list
        # window just shown or put on top.
        self.place_the_settings_and_saved_sessions_windows_above_the_lists(self.popover.isShown())
        if self.settings_window_controller.is_open() and (
                list_window_is_shown_now or new_settings["list_window_is_always_on_top"] != list_window_was_always_on_top):
            self.settings_window_controller.window.orderFront_(None)  # the list window just shown or put on top stays behind it

    @objc.python_method
    def apply_appearance(self, appearance_choice):
        """Draws the app's windows, the sidebar and the dropdown always dark, always light, or, for
        "auto", in macOS's Light or Dark setting. The dropdown is set by itself: a popover takes the
        look of the menu bar it opens from rather than the app's, and on "auto" it keeps doing so.
        The menu bar icon keeps the menu bar's look either way."""
        appearance_name = APPEARANCE_NAMES_BY_CHOICE[appearance_choice]
        appearance = NSAppearance.appearanceNamed_(appearance_name) if appearance_name else None
        NSApp.setAppearance_(appearance)
        self.popover.setAppearance_(appearance)

    @objc.python_method
    def apply_start_with_iterm2_setting(self, app_starts_with_iterm2):
        """Adds, keeps or removes the script in iTerm2's AutoLaunch folder that starts this copy of
        the app whenever iTerm2 starts (start_with_iterm2), and says so in the output when it adds or
        removes it."""
        autolaunch_script_path = find_autolaunch_script_path()
        try:
            what_was_done = update_autolaunch_script_to_match_setting(
                app_starts_with_iterm2, build_command_that_starts_this_copy_of_the_app(), find_package_folder_of_this_copy(),
                autolaunch_script_path)
        except OSError as error:
            print(f"agent-command-tab: couldn't {'add' if app_starts_with_iterm2 else 'remove'} {autolaunch_script_path} "
                  f"({error})", flush=True)
            return
        if what_was_done == "added":
            print(f"agent-command-tab: added {autolaunch_script_path}, so iTerm2 starts the app whenever iTerm2 starts "
                  f"(turning off Settings > {START_WITH_ITERM2_SETTING_TITLE} removes it)", flush=True)
        elif what_was_done == "removed":
            print(f"agent-command-tab: removed {autolaunch_script_path}; iTerm2 no longer starts the app", flush=True)

    @objc.python_method
    def apply_settings_to_the_core_and_every_list(self, settings, sidebar_settings_changed=True):
        """Hands the settings to the core, the sidebar, the list window and the three lists: at
        the start and after every change. The core gets them only when its part changed (each time
        it builds a whole snapshot on its thread to compare), and the sidebar only when one of
        SETTINGS_THAT_PLACE_THE_SIDEBAR changed."""
        core_settings = build_core_settings(settings)
        if core_settings != self.core_settings_sent_last:
            self.core_settings_sent_last = core_settings
            self.connection_thread.send_core_settings(core_settings)
        if sidebar_settings_changed:
            self.sidebar_controller.apply_settings(settings)
        self.list_window_controller.apply_settings(settings)
        self.apply_list_settings(settings)

    @objc.python_method
    def list_sort_order_chosen(self, list_sort_order, list_sort_order_is_reversed):
        """The Sort pop-up at the top of the dropdown, the sidebar or the list window, or a click
        on a column's title: one choice for all, saved with the settings."""
        if ((list_sort_order, list_sort_order_is_reversed)
                != (self.settings["list_sort_order"], self.settings["list_sort_order_is_reversed"])):
            self.settings_changed(dict(self.settings, list_sort_order=list_sort_order,
                                       list_sort_order_is_reversed=list_sort_order_is_reversed))

    @objc.python_method
    def idle_tabs_are_hidden_chosen(self, idle_tabs_are_hidden):
        """The Hide Idle / Show Idle button at the top of the dropdown, the sidebar or the list window: one choice for all, saved with the settings."""
        if idle_tabs_are_hidden != self.settings["idle_tabs_are_hidden"]:
            self.settings_changed(dict(self.settings, idle_tabs_are_hidden=idle_tabs_are_hidden))

    @objc.python_method
    def apply_list_settings(self, settings):
        """Gives the dropdown's, the sidebar's and the list window's lists the same sort order, idle tabs shown or hidden, and shown columns."""
        for list_panel_controller in self.list_all_list_panel_controllers():
            list_panel_controller.show_list_sort_order(settings["list_sort_order"], settings["list_sort_order_is_reversed"])
            list_panel_controller.show_idle_tabs_are_hidden(settings["idle_tabs_are_hidden"])
            list_panel_controller.list_controller.set_shown_columns(settings["shown_columns"])

    @objc.python_method
    def reserve_screen_area(self, reserved_area):
        """Docked sidebar: asks the core to keep iTerm2 windows out of the sidebar's area,
        {"frame": (x, y, width, height), "edge": "right" or "left", "monitor_visible_frame": (...)}
        (DashboardCore.set_reserved_screen_area), or None to end it."""
        if reserved_area != self.reserved_screen_area:
            self.reserved_screen_area = reserved_area
            self.connection_thread.send_reserved_screen_area(reserved_area)

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
        self.settings_window_controller.screen_layout_changed()

    def anotherApplicationBecameActive_(self, notification):
        if self.peek_in_progress is None and self.peek_waiting_for_put_back is None:  # a peek activates iTerm2 only for a moment
            self.note_application_unless_it_is_the_dashboard(notification.userInfo()[NSWorkspaceApplicationKey])

    @objc.python_method
    def note_application_unless_it_is_the_dashboard(self, application):
        """Notes an active app (an NSRunningApplication, or None) as last_other_active_application,
        unless it is the dashboard itself, and tells the core once it runs (the core looks for a
        tmux client inside it)."""
        if application is not None and application.processIdentifier() != os.getpid():
            self.last_other_active_application = application
            if self.connection_thread.is_alive():
                self.connection_thread.send_last_other_active_application_process_id(application.processIdentifier())

    def dashboardBecameActive_(self, notification):
        self.connection_thread.send_dashboard_is_active(True)
        self.retry_the_global_shortcut_if_it_lacks_permission()

    def dashboardResignedActive_(self, notification):
        self.connection_thread.send_dashboard_is_active(False)
        # An open settings window keeps the dropdown open while you click elsewhere in the dashboard
        # (update_dropdown_closing_behavior); a click in another app still closes it, unless a peek
        # brought iTerm2 forward on purpose or a row is dragged in the dropdown. A drag usually
        # starts as a hold, so it ends a peek right away, and macOS can tell the dashboard it is no
        # longer active only after that peek is over (which closed the dropdown).
        if not self.a_peek_or_a_drag_keeps_the_dropdown_open():
            self.close_dropdown()

    @objc.python_method
    def quit(self):
        """Quits, first saving what waits to be saved and giving iTerm2 windows back the room a
        docked sidebar took."""
        self.save_settings_before_exiting()
        self.give_room_back_before_exiting()
        sys.stdout.flush()  # os._exit skips it, losing the last lines of the log
        os._exit(0)

    def applicationWillTerminate_(self, notification):
        # For example after Ctrl+C in the terminal that started the app, or at logout.
        self.save_settings_before_exiting()
        self.give_room_back_before_exiting()

    @objc.python_method
    def save_settings_before_exiting(self):
        """Saves a launch command or width being typed in the settings window (its field saves it
        as its editing ends), a name being typed in the Saved sessions window, and a moved
        window's frame waiting to be saved."""
        for window_controller in (self.settings_window_controller, self.saved_sessions_window_controller):
            if window_controller.window is not None:
                window_controller.window.makeFirstResponder_(None)
        self.save_settings_if_scheduled()

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
    # For anything else that shows NSApp's icon; the dialogs get it from run_alert_in_front.
    application.setApplicationIconImage_(build_app_icon())
    app_controller = AppController.alloc().initWithVerbose_(verbose)
    application.setMainMenu_(build_main_menu_with_the_settings_and_edit_shortcuts(app_controller))
    app_controller.start()
    # runEventLoop(installInterrupt=True) installs its Ctrl+C handler only when no NSApplication
    # exists yet, and one was made above, so it is installed here. Ctrl+C then terminates the app,
    # which calls applicationWillTerminate_.
    AppHelper.installMachInterrupt()
    AppHelper.runEventLoop()
    # Ctrl+C can stop the event loop without terminating the app.
    app_controller.save_settings_before_exiting()
    app_controller.give_room_back_before_exiting()
    return 0
