"""Phase 0 experiment: how long does one full-screen read take per session?

Read-only. Prints only timings and line counts, never screen text.
"""

import time

import iterm2
import iterm2.auth

iterm2.auth.gAppKitAvailable = False
NUMBER_OF_READS_PER_SESSION = 10


async def time_screen_reads(connection):
    """Reads every first-pane session's screen several times and prints timing statistics."""
    app = await iterm2.async_get_app(connection)
    for window in app.terminal_windows:
        for tab in window.tabs:
            session = tab.sessions[0]
            durations_in_milliseconds = []
            line_count = 0
            for _ in range(NUMBER_OF_READS_PER_SESSION):
                start = time.perf_counter()
                contents = await session.async_get_screen_contents()
                durations_in_milliseconds.append((time.perf_counter() - start) * 1000)
                line_count = contents.number_of_lines
            durations_in_milliseconds.sort()
            print(f"session {session.session_id[:8]}: {line_count} lines, "
                  f"median {durations_in_milliseconds[len(durations_in_milliseconds) // 2]:.1f} ms, "
                  f"max {durations_in_milliseconds[-1]:.1f} ms")


iterm2.run_until_complete(time_screen_reads)
