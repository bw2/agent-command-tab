"""The colors of the menu bar icon and the list's rows, by name: the rows' color names
(row_text_rendering), which the icon uses too. "light_blue" is the color of "asking" everywhere
(user request, milestone 2)."""

from AppKit import NSColor


def make_srgb_color_maker(red, green, blue):
    """Returns a function that makes this color, like NSColor.systemRedColor does for red."""
    return lambda: NSColor.colorWithSRGBRed_green_blue_alpha_(red / 255, green / 255, blue / 255, 1)


# Purple is a fixed color, since on current macOS the system purple is a magenta.
COLOR_MAKERS_BY_NAME = {
    "purple": make_srgb_color_maker(150, 95, 255), "red": NSColor.systemRedColor, "orange": NSColor.systemOrangeColor,
    "green": NSColor.systemGreenColor, "light_blue": NSColor.systemCyanColor, "gray": NSColor.secondaryLabelColor,
}


def build_color_from_name(color_name, fallback_color_maker=NSColor.labelColor):
    """Returns the color with this name, or the fallback's color for a name not in COLOR_MAKERS_BY_NAME."""
    return COLOR_MAKERS_BY_NAME.get(color_name, fallback_color_maker)()
