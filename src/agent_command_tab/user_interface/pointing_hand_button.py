"""A button that shows the pointing hand while the pointer is over it (user request, 2026-10-19).

Used for the list's icon buttons (a row's arrow, a window's preview button, the "+" row) and the
top and bottom bars' buttons. The sidebar window turns cursor rects off (they reset its edge's
left-right arrows), so the hand is set from a tracking area, which also works while another app
is in front (sidebar_panel.allow_pointer_changes_while_another_app_is_in_front); the cursor rect
covers the dropdown and the list window, where cursor rects are on.
"""

import objc
from AppKit import (NSButton, NSCursor, NSTrackingActiveAlways, NSTrackingArea, NSTrackingCursorUpdate,
                    NSTrackingInVisibleRect, NSTrackingMouseEnteredAndExited, NSZeroRect)


class PointingHandButton(NSButton):
    """An NSButton whose pointer is the pointing hand, unless the button is hidden."""

    def initWithFrame_(self, frame):
        self = objc.super(PointingHandButton, self).initWithFrame_(frame)
        if self is None:
            return None
        self.addTrackingArea_(NSTrackingArea.alloc().initWithRect_options_owner_userInfo_(
            NSZeroRect, NSTrackingCursorUpdate | NSTrackingMouseEnteredAndExited | NSTrackingActiveAlways
            | NSTrackingInVisibleRect, self, None))
        return self

    def resetCursorRects(self):
        # The visible part only: cursor rects aren't clipped to the scroll view, so a row's button
        # scrolled partly under the top bar would show the hand over the bar.
        if not self.isHiddenOrHasHiddenAncestor():
            self.addCursorRect_cursor_(self.visibleRect(), NSCursor.pointingHandCursor())

    def cursorUpdate_(self, event):
        if not self.isHiddenOrHasHiddenAncestor():
            NSCursor.pointingHandCursor().set()

    def mouseEntered_(self, event):
        if not self.isHiddenOrHasHiddenAncestor():
            NSCursor.pointingHandCursor().set()

    def mouseExited_(self, event):
        NSCursor.arrowCursor().set()
