"""A button that shows the pointing hand while the pointer is over it.

Used for the list's icon buttons (a row's arrow, the "+" row's button) and the
top and bottom bars' buttons. The sidebar window turns cursor rects off (they reset its edge's
left-right arrows), so the hand is set from a tracking area, which also works while another app
is in front (sidebar_panel.allow_pointer_changes_while_another_app_is_in_front); the cursor rect
covers the dropdown and the list window, where cursor rects are on.
"""

import objc
from AppKit import (NSButton, NSCursor, NSTrackingActiveAlways, NSTrackingArea, NSTrackingCursorUpdate,
                    NSTrackingInVisibleRect, NSTrackingMouseEnteredAndExited, NSZeroRect)


class PointingHandButton(NSButton):
    """An NSButton whose pointer is the pointing hand, unless the button is hidden or grayed out
    (a click would do nothing)."""

    def initWithFrame_(self, frame):
        self = objc.super(PointingHandButton, self).initWithFrame_(frame)
        if self is None:
            return None
        self.addTrackingArea_(NSTrackingArea.alloc().initWithRect_options_owner_userInfo_(
            NSZeroRect, NSTrackingCursorUpdate | NSTrackingMouseEnteredAndExited | NSTrackingActiveAlways
            | NSTrackingInVisibleRect, self, None))
        return self

    @objc.python_method
    def shows_the_pointing_hand(self):
        return self.isEnabled() and not self.isHiddenOrHasHiddenAncestor()

    def setEnabled_(self, is_enabled):
        """Grays the button out or makes it clickable again, and has its cursor rect set again
        (resetCursorRects), which AppKit otherwise keeps as it was."""
        objc.super(PointingHandButton, self).setEnabled_(is_enabled)
        if self.window() is not None:
            self.window().invalidateCursorRectsForView_(self)

    def resetCursorRects(self):
        # The visible part only: cursor rects aren't clipped to the scroll view, so a row's button
        # scrolled partly under the top bar would show the hand over the bar.
        if self.shows_the_pointing_hand():
            self.addCursorRect_cursor_(self.visibleRect(), NSCursor.pointingHandCursor())

    def cursorUpdate_(self, event):
        if self.shows_the_pointing_hand():
            NSCursor.pointingHandCursor().set()

    def mouseEntered_(self, event):
        if self.shows_the_pointing_hand():
            NSCursor.pointingHandCursor().set()

    def mouseExited_(self, event):
        NSCursor.arrowCursor().set()
