"""Phase 0 follow-up: which OSC 7501 state word means "failed" to iTerm2, and does OSC 21337 accept "Waiting"?

Opens one throwaway iTerm2 window, sends status reports into it as program output, prints
what the `status` variable shows after each, and closes the window.
"""

import asyncio

import iterm2
import iterm2.auth

iterm2.auth.gAppKitAvailable = False

REPORTS_TO_TRY = [
    "\x1b]7501;state=idle\x1b\\", "\x1b]7501;state=failed\x1b\\",
    "\x1b]7501;state=idle\x1b\\", "\x1b]7501;state=error\x1b\\",
    "\x1b]7501;state=idle\x1b\\", "\x1b]7501;state=blocked:kind=input\x1b\\",
    "\x1b]21337;status=Idle\x07", "\x1b]21337;status=Waiting\x07",
]


async def try_reports(connection):
    """Sends each report into a test window's session and prints the resulting status."""
    app = await iterm2.async_get_app(connection)
    original_window = app.current_terminal_window
    original_session = original_window.current_tab.current_session if original_window else None
    test_window = await iterm2.Window.async_create(connection)
    session = test_window.tabs[0].sessions[0]
    try:
        for report in REPORTS_TO_TRY:
            await session.async_inject(report.encode())
            await asyncio.sleep(0.8)
            printable_report = report.replace("\x1b", "ESC").replace("\x07", "BEL")
            print(f"{printable_report:45} -> status variable: {await session.async_get_variable('status')!r}")
    finally:
        await test_window.async_close(force=True)
        if original_session is not None:
            await original_session.async_activate(select_tab=True, order_window_front=True)


iterm2.run_until_complete(try_reports)
