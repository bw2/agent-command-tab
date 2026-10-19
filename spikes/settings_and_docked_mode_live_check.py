"""End-to-end check of Settings and the docked sidebar with the real app objects, without mouse clicks.

Usage: settings_and_docked_mode_live_check.py

Runs the real AppController (its own menu bar icon shows during the run) with a temporary copy of
your settings and docked-window file, so your own files are not touched. Each step changes a
Settings control the way a click does (choose the item, then send the control's action):
1. "Show:" set to "Always: Above other windows": saved, and the sidebar stays visible.
2. "Show number of agents waiting for my answer" turned off: saved.
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

from AppKit import NSApplication, NSApplicationActivationPolicyAccessory, NSScreen
from PyObjCTools import AppHelper

from llm_sidebar_dashboard import app_controller as app_controller_module
from llm_sidebar_dashboard import settings_store

TEMPORARY_FOLDER = tempfile.mkdtemp(prefix="settings_and_docked_mode_live_check_")
TEMPORARY_SETTINGS_PATH = os.path.join(TEMPORARY_FOLDER, "settings.json")
if os.path.exists(settings_store.SETTINGS_FILE_PATH):
    shutil.copy(settings_store.SETTINGS_FILE_PATH, TEMPORARY_SETTINGS_PATH)
# The app module looks these names up when it runs, so the copy is used instead of your files.
app_controller_module.load_settings_resolving_paths_at_first_run = lambda: settings_store.load_settings(TEMPORARY_SETTINGS_PATH)
app_controller_module.save_settings = lambda settings: settings_store.save_settings(settings, TEMPORARY_SETTINGS_PATH)
app_controller_module.DOCKED_WINDOW_FRAMES_FILE_PATH = os.path.join(TEMPORARY_FOLDER, "docked_window_frames.json")


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
            frames = {}
            for window in core.app.terminal_windows:
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
        screen_width = run_on_main_thread(lambda: NSScreen.mainScreen().frame().size.width)
        run_on_main_thread(app_controller.open_settings)
        settings_window = app_controller.settings_window_controller
        results["Settings window opened"] = bool(run_on_main_thread(lambda: settings_window.window.isVisible()))

        run_on_main_thread(lambda: choose_popup_item(settings_window.sidebar_mode_popup, "Always: Above other windows"))
        time.sleep(1)
        results["floating mode saved"] = settings_store.load_settings(TEMPORARY_SETTINGS_PATH)["sidebar_mode"] == "floating"
        results["sidebar visible in floating mode"] = bool(run_on_main_thread(
            lambda: app_controller.sidebar_controller.is_shown and app_controller.sidebar_controller.window.isVisible()))

        run_on_main_thread(lambda: set_checkbox(settings_window.asking_count_checkbox, 0))
        results["asking count turned off and saved"] = settings_store.load_settings(TEMPORARY_SETTINGS_PATH)["icon_shows_asking_count"] is False

        run_on_main_thread(lambda: choose_popup_item(settings_window.sidebar_mode_popup, "Always: Resize iTerm2 windows"))
        sidebar_width = settings_store.load_settings(TEMPORARY_SETTINGS_PATH)["sidebar_width"]
        strip_left_edge = screen_width - sidebar_width
        results["docked mode saved"] = settings_store.load_settings(TEMPORARY_SETTINGS_PATH)["sidebar_mode"] == "docked"
        results["iTerm2 windows moved out of the sidebar's strip"] = wait_until(
            lambda: all(x + width <= strip_left_edge + 1 for x, y, width, height in read_frames().values()), 15)
        print("iTerm2 window frames while docked:", read_frames(), "strip starts at x =", strip_left_edge, flush=True)

        run_on_main_thread(lambda: choose_popup_item(settings_window.sidebar_mode_popup, "Auto-hide"))
        results["auto-hide saved"] = settings_store.load_settings(TEMPORARY_SETTINGS_PATH)["sidebar_mode"] == "auto_hide"
        results["iTerm2 windows got their exact frames back"] = wait_until(lambda: read_frames() == frames_before, 15)
        print("iTerm2 window frames after:", read_frames(), flush=True)
        run_on_main_thread(lambda: set_checkbox(settings_window.asking_count_checkbox, 1))
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
