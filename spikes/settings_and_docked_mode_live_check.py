"""End-to-end check of Settings and the docked sidebar with the real app objects, without mouse clicks.

Usage: settings_and_docked_mode_live_check.py

Runs the real AppController (its own menu bar icon shows during the run) with a temporary copy of
your settings (showing the list as an auto-hiding sidebar, with Always on top off) and temporary docked-window
and pause-records files, so your own files are not touched. Each step changes a
Settings control the way a click does (choose the item, then send the control's action):
1. "Show:" set to "Always: Above other windows": saved, and the sidebar stays visible.
2. "Always on top" turned on: saved (and turned off again at the end).
3. "Show:" set to "Always: Resize iTerm2 windows": the iTerm2 windows on the sidebar's screen move out of its strip.
4. "Show:" set back to "Auto-hide": those windows get their exact frames back.
Then it gives back any room, as Quit does, and exits. Your iTerm2 windows are resized in steps 3 and 4.
"""

import asyncio
import os
import shutil
import sys
import tempfile
import threading
import time
import traceback

from AppKit import NSApplication, NSApplicationActivationPolicyAccessory
from PyObjCTools import AppHelper

from agent_command_tab import app_controller as app_controller_module
from agent_command_tab import settings_store

TEMPORARY_FOLDER = tempfile.mkdtemp(prefix="settings_and_docked_mode_live_check_")
TEMPORARY_SETTINGS_PATH = os.path.join(TEMPORARY_FOLDER, "settings.json")
# Your settings, from the folder of the app's old name if it hasn't run since it was renamed.
for settings_file_path in (settings_store.SETTINGS_FILE_PATH,
                           os.path.join(settings_store.SETTINGS_FOLDER_OF_THE_OLD_NAME, "settings.json")):
    if os.path.exists(settings_file_path):
        shutil.copy(settings_file_path, TEMPORARY_SETTINGS_PATH)
        break
# The sidebar steps need the list shown as a sidebar, and step 2 needs Always on top off, so its
# check sees the click save a change. Auto-hide at start, so no window is docked before the
# frames are first read (they would come back wider in step 4).
settings_store.save_settings(dict(settings_store.load_settings(TEMPORARY_SETTINGS_PATH), show_as="sidebar",
                                  list_window_is_always_on_top=False, sidebar_mode="auto_hide"), TEMPORARY_SETTINGS_PATH)
# The app module looks these names up when it runs, so the copy is used instead of your files,
# and your settings folder isn't moved from the app's old name.
app_controller_module.move_the_settings_folder_of_the_old_name = lambda: None
app_controller_module.load_settings_resolving_paths_at_first_run = lambda: settings_store.load_settings(TEMPORARY_SETTINGS_PATH)
app_controller_module.save_settings = lambda settings: settings_store.save_settings(settings, TEMPORARY_SETTINGS_PATH)
app_controller_module.DOCKED_WINDOW_FRAMES_FILE_PATH = os.path.join(TEMPORARY_FOLDER, "docked_window_frames.json")
app_controller_module.PAUSE_RECORDS_FILE_PATH = os.path.join(TEMPORARY_FOLDER, "pause_records.json")


def run_on_main_thread(function):
    finished = threading.Event()
    box = {}

    def call():
        try:
            box["value"] = function()
        finally:
            finished.set()

    AppHelper.callAfter(call)
    finished.wait(15)
    return box.get("value")


def choose_popup_item(popup, title):
    popup.selectItemWithTitle_(title)
    popup.sendAction_to_(popup.action(), popup.target())


def set_checkbox(checkbox, state):
    checkbox.setState_(state)
    checkbox.sendAction_to_(checkbox.action(), checkbox.target())


def run_checks(app_controller):
    results = {}
    connection_thread = app_controller.connection_thread
    try:
        connection_thread.core_ready.wait(30)
        core = connection_thread.core
        while core.app is None:
            time.sleep(0.1)

        async def read_window_frames():
            """Returns the frames of the iTerm2 windows docking may move: not full-screen ones, which the core leaves alone."""
            frames = {}
            for window in core.app.terminal_windows:
                if await window.async_get_fullscreen():
                    continue
                frame = await window.async_get_frame()
                frames[window.window_id] = (round(frame.origin.x), round(frame.origin.y), round(frame.size.width), round(frame.size.height))
            return frames

        def read_frames():
            return asyncio.run_coroutine_threadsafe(read_window_frames(), connection_thread.event_loop).result(15)

        def wait_until(condition, timeout):
            deadline = time.time() + timeout
            while time.time() < deadline:
                if condition():
                    return True
                time.sleep(0.5)
            return False

        frames_before = read_frames()
        print("iTerm2 window frames before:", frames_before, flush=True)
        run_on_main_thread(app_controller.open_settings)
        settings_window = app_controller.settings_window_controller
        results["Settings window opened"] = bool(run_on_main_thread(lambda: settings_window.window.isVisible()))

        run_on_main_thread(lambda: choose_popup_item(settings_window.sidebar_mode_popup, "Always: Above other windows"))
        time.sleep(1)
        results["floating mode saved"] = settings_store.load_settings(TEMPORARY_SETTINGS_PATH)["sidebar_mode"] == "floating"
        results["sidebar visible in floating mode"] = bool(run_on_main_thread(
            lambda: app_controller.sidebar_controller.is_shown and app_controller.sidebar_controller.window.isVisible()))

        run_on_main_thread(lambda: set_checkbox(settings_window.list_window_always_on_top_checkbox, 1))
        results["always on top turned on and saved"] = settings_store.load_settings(TEMPORARY_SETTINGS_PATH)["list_window_is_always_on_top"] is True

        run_on_main_thread(lambda: choose_popup_item(settings_window.sidebar_mode_popup, "Always: Resize iTerm2 windows"))
        results["docked mode saved"] = settings_store.load_settings(TEMPORARY_SETTINGS_PATH)["sidebar_mode"] == "docked"
        # The strip the sidebar takes, on whichever edge it is: the saved one, or the other when that borders another monitor.
        sidebar_frame = run_on_main_thread(lambda: app_controller.sidebar_controller.window.frame())
        strip_x, strip_width = sidebar_frame.origin.x, sidebar_frame.size.width

        def lies_outside_the_strip(x, width):
            return x + width <= strip_x + 1 or x >= strip_x + strip_width - 1
        results["iTerm2 windows moved out of the sidebar's strip"] = wait_until(
            lambda: all(lies_outside_the_strip(x, width) for x, y, width, height in read_frames().values()), 15)
        print("iTerm2 window frames while docked:", read_frames(), "strip from x =", strip_x, "to", strip_x + strip_width, flush=True)

        run_on_main_thread(lambda: choose_popup_item(settings_window.sidebar_mode_popup, "Auto-hide"))
        results["auto-hide saved"] = settings_store.load_settings(TEMPORARY_SETTINGS_PATH)["sidebar_mode"] == "auto_hide"
        results["iTerm2 windows got their exact frames back"] = wait_until(lambda: read_frames() == frames_before, 15)
        print("iTerm2 window frames after:", read_frames(), flush=True)
        run_on_main_thread(lambda: set_checkbox(settings_window.list_window_always_on_top_checkbox, 0))
    except BaseException:
        traceback.print_exc()  # os._exit below would drop it
        results["ran to the end without an error"] = False
    finally:
        run_on_main_thread(app_controller.give_room_back_before_exiting)
        for check, passed in results.items():
            print(("PASS  " if passed else "FAIL  ") + check, flush=True)
        all_passed = bool(results) and all(results.values())
        if all_passed:
            shutil.rmtree(TEMPORARY_FOLDER, ignore_errors=True)
        else:
            print("kept the temporary settings and saved window frames in", TEMPORARY_FOLDER, flush=True)
        os._exit(0 if all_passed else 1)


def main():
    application = NSApplication.sharedApplication()
    application.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
    app_controller = app_controller_module.AppController.alloc().initWithVerbose_(False)
    app_controller.start()
    threading.Thread(target=run_checks, args=(app_controller,), daemon=True).start()
    AppHelper.runEventLoop()


if __name__ == "__main__":
    sys.exit(main())
