"""The app's own icon (user request, 2026-10-28), drawn in code so no image file ships.

The Command key's ⌘, for the Command-Tab that the app's name plays on, white on a dark rounded
square, with a status dot in each of its loops in the list's colors: background's amber at the
top left, asking's blue at the top right, idle's gray at the bottom left and done's green at the
bottom right. The app's dialogs show it in place of Python's rocket: app_controller's
run_alert_in_front gives each one it, since NSAlert takes Python.app's own icon whatever NSApp's is.
"""

import math

from AppKit import (NSBezierPath, NSColor, NSGradient, NSImage, NSMakePoint, NSMakeRect, NSRoundLineCapStyle,
                    NSRoundLineJoinStyle)

APP_ICON_SIZE = 1024  # points, the size macOS draws app icons at; the drawing stays sharp at any size
# Apple's macOS icon grid: the rounded square sits this far inside the canvas.
ICON_BODY_INSET, ICON_BODY_SIZE, ICON_BODY_CORNER_RADIUS = 100, 824, 185
# The ⌘: a square of this half side whose sides run on into a loop of this radius at each corner.
COMMAND_KEY_CENTER, COMMAND_KEY_HALF_SIDE, COMMAND_KEY_LOOP_RADIUS, COMMAND_KEY_LINE_WIDTH = 512, 92, 112, 58
STATUS_DOT_RADIUS = 52


def make_srgb_color(red, green, blue, alpha=1.0):
    return NSColor.colorWithSRGBRed_green_blue_alpha_(red / 255, green / 255, blue / 255, alpha)


# The status colors as the list draws them in dark mode, by the loop's corner (x and y direction from the center).
STATUS_DOT_COLOR_BY_CORNER = {
    (-1, 1): make_srgb_color(255, 204, 0),  # background's amber, top left
    (1, 1): make_srgb_color(60, 211, 254),  # asking's blue, top right
    (-1, -1): make_srgb_color(142, 142, 147),  # idle's gray, bottom left
    (1, -1): make_srgb_color(48, 209, 88),  # done's green, bottom right
}


def build_app_icon():
    """Returns the icon as an NSImage that draws itself at whatever size it is shown."""
    def draw(destination_rect):
        draw_app_icon()
        return True
    return NSImage.imageWithSize_flipped_drawingHandler_((APP_ICON_SIZE, APP_ICON_SIZE), False, draw)


def draw_app_icon():
    """Draws the icon into the current graphics context, in a square APP_ICON_SIZE points wide."""
    body = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
        NSMakeRect(ICON_BODY_INSET, ICON_BODY_INSET, ICON_BODY_SIZE, ICON_BODY_SIZE), ICON_BODY_CORNER_RADIUS, ICON_BODY_CORNER_RADIUS)
    NSGradient.alloc().initWithStartingColor_endingColor_(make_srgb_color(58, 60, 66), make_srgb_color(24, 25, 28)).drawInBezierPath_angle_(body, -90)
    make_srgb_color(255, 255, 255, 0.10).set()  # a faint rim, so the square holds its shape on a dark background
    body.setLineWidth_(4)
    body.stroke()
    command_key = NSBezierPath.bezierPath()
    command_key.setLineWidth_(COMMAND_KEY_LINE_WIDTH)
    command_key.setLineCapStyle_(NSRoundLineCapStyle)
    command_key.setLineJoinStyle_(NSRoundLineJoinStyle)
    corner_offset = COMMAND_KEY_HALF_SIDE + COMMAND_KEY_LOOP_RADIUS
    for (sign_x, sign_y), start_angle in (((1, 1), 180), ((-1, 1), 270), ((-1, -1), 0), ((1, -1), 90)):
        # Each loop goes three quarters round, from where one side meets it to where the next side leaves it.
        loop_center = NSMakePoint(COMMAND_KEY_CENTER + sign_x * corner_offset, COMMAND_KEY_CENTER + sign_y * corner_offset)
        command_key.moveToPoint_(NSMakePoint(loop_center.x + COMMAND_KEY_LOOP_RADIUS * math.cos(math.radians(start_angle)),
                                             loop_center.y + COMMAND_KEY_LOOP_RADIUS * math.sin(math.radians(start_angle))))
        command_key.appendBezierPathWithArcWithCenter_radius_startAngle_endAngle_clockwise_(
            loop_center, COMMAND_KEY_LOOP_RADIUS, start_angle, start_angle - 270, True)
    for sign in (1, -1):  # the square's sides, from loop to loop
        command_key.moveToPoint_(NSMakePoint(COMMAND_KEY_CENTER + sign * COMMAND_KEY_HALF_SIDE, COMMAND_KEY_CENTER - corner_offset))
        command_key.lineToPoint_(NSMakePoint(COMMAND_KEY_CENTER + sign * COMMAND_KEY_HALF_SIDE, COMMAND_KEY_CENTER + corner_offset))
        command_key.moveToPoint_(NSMakePoint(COMMAND_KEY_CENTER - corner_offset, COMMAND_KEY_CENTER + sign * COMMAND_KEY_HALF_SIDE))
        command_key.lineToPoint_(NSMakePoint(COMMAND_KEY_CENTER + corner_offset, COMMAND_KEY_CENTER + sign * COMMAND_KEY_HALF_SIDE))
    make_srgb_color(245, 245, 247).set()
    command_key.stroke()
    for (sign_x, sign_y), color in STATUS_DOT_COLOR_BY_CORNER.items():
        color.set()
        NSBezierPath.bezierPathWithOvalInRect_(NSMakeRect(
            COMMAND_KEY_CENTER + sign_x * corner_offset - STATUS_DOT_RADIUS, COMMAND_KEY_CENTER + sign_y * corner_offset - STATUS_DOT_RADIUS,
            2 * STATUS_DOT_RADIUS, 2 * STATUS_DOT_RADIUS)).fill()
