"""The colors of the menu bar icon and the list's rows, by name: the rows' color names
(row_text_rendering), which the icon uses too. "light_blue" is the color of "asking" everywhere
(user request, milestone 2)."""

from AppKit import NSAppearanceNameAqua, NSAppearanceNameDarkAqua, NSColor


def make_srgb_color_maker(red, green, blue):
    """Returns a function that makes this color, like NSColor.systemRedColor does for red."""
    return lambda: NSColor.colorWithSRGBRed_green_blue_alpha_(red / 255, green / 255, blue / 255, 1)


def appearance_is_dark(appearance):
    """Returns whether this NSAppearance (a view's effectiveAppearance, for example) is dark mode's."""
    return appearance.bestMatchFromAppearancesWithNames_([NSAppearanceNameAqua, NSAppearanceNameDarkAqua]) == NSAppearanceNameDarkAqua


def make_light_and_dark_mode_color_maker(light_mode_color_maker, dark_mode_color_maker):
    """Returns a function that makes a color drawn as light_mode_color_maker's in light mode and
    as dark_mode_color_maker's in dark mode, as the system colors are."""
    def choose_color_for_appearance(appearance):
        if appearance_is_dark(appearance):
            return dark_mode_color_maker()
        return light_mode_color_maker()
    return lambda: NSColor.colorWithName_dynamicProvider_(None, choose_color_for_appearance)


# Purple is a fixed color, since on current macOS the system purple is a magenta.
COLOR_MAKERS_BY_NAME = {
    "purple": make_srgb_color_maker(150, 95, 255), "red": NSColor.systemRedColor, "orange": NSColor.systemOrangeColor,
    "green": NSColor.systemGreenColor, "light_blue": NSColor.systemCyanColor, "gray": NSColor.secondaryLabelColor,
    "yellow": NSColor.systemYellowColor,  # the background task lights (R26.2)
    # The Tokens column's share of the context window from 50% (user request, 2026-10-27). The system
    # yellow is hard to read as text on white (contrast 1.5 to 1), so light mode has a darker amber (4 to 1).
    "amber": make_light_and_dark_mode_color_maker(make_srgb_color_maker(168, 118, 0), NSColor.systemYellowColor),
}


def build_color_from_name(color_name, fallback_color_maker=NSColor.labelColor):
    """Returns the color with this name, or the fallback's color for a name not in COLOR_MAKERS_BY_NAME."""
    return COLOR_MAKERS_BY_NAME.get(color_name, fallback_color_maker)()
