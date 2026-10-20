"""The global shortcut that shows the list from any app (user request, 2026-10-19).

The shortcut is Shift-Command-Tab (the default) or Command-Fn-Tab. macOS's app switcher takes
both before macOS's usual way of making a global shortcut (RegisterEventHotKey) sees them, also
with Fn held, so they are caught by a key filter instead: an active event tap (CGEventTapCreate)
that sees every key press before the app switcher does and holds back only the chosen shortcut.
While the app switcher is open (Command still held after a Command-Tab press), the shortcut goes
to the app switcher, where Shift-Command-Tab moves backward (user request). Both were checked with
real key presses on 2026-10-19.

The filter needs the Accessibility permission (macOS checks it for the app that started this one,
such as iTerm2). It runs on its own thread, so key presses never wait for the main thread's run
loop, but each key event takes Python's global lock once, so while another thread runs Python
a key press waits a moment for it. It calls CoreGraphics through ctypes, so it needs no extra
package.
"""

import collections
import ctypes
import threading

CORE_GRAPHICS_PATH = "/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics"
CORE_GRAPHICS = ctypes.CDLL(CORE_GRAPHICS_PATH)
# The reads made for every key event keep Python's global lock (PyDLL): ctypes.CDLL would let go
# of it around each call, and each time the key event could wait for another thread to give it back.
CORE_GRAPHICS_KEEPING_THE_PYTHON_LOCK = ctypes.PyDLL(CORE_GRAPHICS_PATH)
CORE_FOUNDATION = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")

# CGEventType values (CGEventTypes.h): the events the filter asks for, and the two that say macOS
# turned the filter off (it answered too slowly, or secure typing such as a password field began).
KEY_DOWN_EVENT, KEY_UP_EVENT, MODIFIER_KEYS_CHANGED_EVENT = 10, 11, 12
FILTER_TURNED_OFF_EVENTS = (0xFFFFFFFE, 0xFFFFFFFF)
KEY_EVENT_MASK = (1 << KEY_DOWN_EVENT) | (1 << KEY_UP_EVENT) | (1 << MODIFIER_KEYS_CHANGED_EVENT)
# CGEventField values: whether a key press is the key repeating, and which key it is.
KEY_REPEAT_FIELD, KEY_CODE_FIELD = 8, 9
TAB_KEY_CODE = 48
# CGEventFlags masks.
SHIFT_FLAG, CONTROL_FLAG, OPTION_FLAG, COMMAND_FLAG, FN_FLAG = 0x20000, 0x40000, 0x80000, 0x100000, 0x800000
# The modifier key that each shortcut choice (settings_store.GLOBAL_SHORTCUT_CHOICES other than
# "off") adds to Command-Tab.
EXTRA_MODIFIER_FLAG_BY_GLOBAL_SHORTCUT = {"shift_command_tab": SHIFT_FLAG, "command_fn_tab": FN_FLAG}
# CGEventTapCreate's location (before the window server routes the event), placement (ahead of
# other filters) and option (may hold events back, which is what needs the Accessibility permission).
HID_EVENT_TAP_LOCATION, HEAD_INSERT_PLACEMENT, ACTIVE_FILTER_OPTION = 0, 0, 0

EventTapCallback = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_void_p)
CORE_GRAPHICS.CGEventTapCreate.argtypes = [ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint64,
                                           EventTapCallback, ctypes.c_void_p]
CORE_GRAPHICS.CGEventTapCreate.restype = ctypes.c_void_p
CORE_GRAPHICS.CGEventTapEnable.argtypes = [ctypes.c_void_p, ctypes.c_bool]
CORE_GRAPHICS.CGEventTapEnable.restype = None
CORE_GRAPHICS_KEEPING_THE_PYTHON_LOCK.CGEventGetFlags.argtypes = [ctypes.c_void_p]
CORE_GRAPHICS_KEEPING_THE_PYTHON_LOCK.CGEventGetFlags.restype = ctypes.c_uint64
CORE_GRAPHICS_KEEPING_THE_PYTHON_LOCK.CGEventGetIntegerValueField.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
CORE_GRAPHICS_KEEPING_THE_PYTHON_LOCK.CGEventGetIntegerValueField.restype = ctypes.c_int64
CORE_FOUNDATION.CFMachPortCreateRunLoopSource.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_long]
CORE_FOUNDATION.CFMachPortCreateRunLoopSource.restype = ctypes.c_void_p
CORE_FOUNDATION.CFRunLoopGetCurrent.argtypes = []
CORE_FOUNDATION.CFRunLoopGetCurrent.restype = ctypes.c_void_p
CORE_FOUNDATION.CFRunLoopAddSource.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
CORE_FOUNDATION.CFRunLoopAddSource.restype = None
CORE_FOUNDATION.CFRunLoopRun.argtypes = []
CORE_FOUNDATION.CFRunLoopRun.restype = None
RUN_LOOP_COMMON_MODES = ctypes.c_void_p.in_dll(CORE_FOUNDATION, "kCFRunLoopCommonModes")

# What the filter remembers between key events: whether the app switcher is open, and whether the
# next Tab key release belongs to a press that was held back (and so is held back too).
KeyFilterState = collections.namedtuple("KeyFilterState", ["app_switcher_is_open", "tab_release_is_held_back"])
START_KEY_FILTER_STATE = KeyFilterState(app_switcher_is_open=False, tab_release_is_held_back=False)


def decide_what_to_do_with_key_event(event_type, key_code, flags, is_key_repeat, global_shortcut, state):
    """Returns (what_to_do, new_state) for one key event. what_to_do is "let_through", "hold_back",
    or "hold_back_and_show_the_list" for a first press of the chosen shortcut while the app
    switcher is closed. A held-back press is held back until its release, key repeats included.

    The app switcher opens on Command-Tab, also with Shift or Fn held, and stays open until
    Command is let go; meanwhile the shortcut is let through to it. Tab with Control or Option
    held is always let through.
    """
    if event_type == MODIFIER_KEYS_CHANGED_EVENT:
        if state.app_switcher_is_open and not flags & COMMAND_FLAG:
            return "let_through", state._replace(app_switcher_is_open=False)
        return "let_through", state
    if key_code != TAB_KEY_CODE:
        return "let_through", state
    if event_type == KEY_UP_EVENT:
        if state.tab_release_is_held_back:
            return "hold_back", state._replace(tab_release_is_held_back=False)
        return "let_through", state
    if is_key_repeat and state.tab_release_is_held_back:
        return "hold_back", state  # a held-back press repeating, even after a modifier key was let go
    if event_type != KEY_DOWN_EVENT or not flags & COMMAND_FLAG or flags & (CONTROL_FLAG | OPTION_FLAG):
        return "let_through", state
    shortcut_flag = EXTRA_MODIFIER_FLAG_BY_GLOBAL_SHORTCUT.get(global_shortcut)
    if shortcut_flag is not None and flags & (SHIFT_FLAG | FN_FLAG) == shortcut_flag and not state.app_switcher_is_open:
        return ("hold_back" if is_key_repeat else "hold_back_and_show_the_list"), state._replace(tab_release_is_held_back=True)
    return "let_through", state._replace(app_switcher_is_open=True)  # Command-Tab, with or without Shift or Fn


class GlobalShortcutKeyFilter:
    """Holds back the chosen global shortcut from every app and calls shortcut_pressed() for
    each first press, on the filter's own thread. Made at the first use_global_shortcut that
    macOS allows, it then stays for the app's lifetime, turned on and off with the setting."""

    def __init__(self, shortcut_pressed):
        self.shortcut_pressed = shortcut_pressed
        self.global_shortcut = "off"
        # Written on the filter's thread; also reset by use_global_shortcut while the filter is off.
        self.state = START_KEY_FILTER_STATE
        self.tap = None
        self.callback = EventTapCallback(self.filter_key_event)  # kept here: ctypes frees a callback nothing holds

    def use_global_shortcut(self, global_shortcut):
        """Switches to a settings_store.GLOBAL_SHORTCUT_CHOICES choice ("off" turns the filter
        off). Without the Accessibility permission macOS refuses the filter (lacks_permission());
        calling again, once the permission may have been given, tries again."""
        filter_was_off = self.global_shortcut == "off"
        self.global_shortcut = global_shortcut
        if global_shortcut == "off":
            if self.tap is not None:
                CORE_GRAPHICS.CGEventTapEnable(self.tap, False)
            return
        if self.tap is not None:
            if filter_was_off:
                # Key events missed while it was off (a Tab release, Command let go) may have left
                # the state stale; while it is off, no key event changes the state meanwhile.
                self.state = START_KEY_FILTER_STATE
            CORE_GRAPHICS.CGEventTapEnable(self.tap, True)
            return
        self.tap = CORE_GRAPHICS.CGEventTapCreate(HID_EVENT_TAP_LOCATION, HEAD_INSERT_PLACEMENT, ACTIVE_FILTER_OPTION,
                                                  KEY_EVENT_MASK, self.callback, None) or None
        if self.tap is not None:
            threading.Thread(target=self.run_filter_thread, name="global-shortcut-key-filter", daemon=True).start()

    def lacks_permission(self):
        """Returns whether a shortcut is chosen but macOS refused the filter (no Accessibility permission)."""
        return self.global_shortcut != "off" and self.tap is None

    def run_filter_thread(self):
        source = CORE_FOUNDATION.CFMachPortCreateRunLoopSource(None, self.tap, 0)
        CORE_FOUNDATION.CFRunLoopAddSource(CORE_FOUNDATION.CFRunLoopGetCurrent(), source, RUN_LOOP_COMMON_MODES)
        CORE_FOUNDATION.CFRunLoopRun()

    def filter_key_event(self, proxy, event_type, event, user_data):
        """Called by macOS for every key event on the Mac: returns the event to let it through,
        or None to hold it back. An error lets the event through, since a filter that held back
        everything would leave the Mac without a keyboard."""
        try:
            if event_type in FILTER_TURNED_OFF_EVENTS:
                # Key events were missed while macOS had it off, so start over rather than hold
                # back a later Tab release or send the shortcut to an app switcher already closed.
                self.state = START_KEY_FILTER_STATE
                if self.global_shortcut != "off":
                    CORE_GRAPHICS.CGEventTapEnable(self.tap, True)
                return event
            what_to_do, self.state = decide_what_to_do_with_key_event(
                event_type, CORE_GRAPHICS_KEEPING_THE_PYTHON_LOCK.CGEventGetIntegerValueField(event, KEY_CODE_FIELD),
                CORE_GRAPHICS_KEEPING_THE_PYTHON_LOCK.CGEventGetFlags(event),
                bool(CORE_GRAPHICS_KEEPING_THE_PYTHON_LOCK.CGEventGetIntegerValueField(event, KEY_REPEAT_FIELD)),
                self.global_shortcut, self.state)
            if what_to_do == "hold_back_and_show_the_list":
                self.shortcut_pressed()
            return event if what_to_do == "let_through" else None
        except Exception as error:
            print(f"global shortcut key filter: {error!r}; letting the key through", flush=True)
            return event
