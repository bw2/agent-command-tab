"""Monitors in read_monitors_from_screens's format, for the tests of the sidebar's placement and of
the settings window, which arrange them in different ways."""

# A 1500 x 1000 monitor with a 30-point menu bar.
BUILT_IN_SCREEN = {"name": "Built-in", "frame": (0, 0, 1500, 1000), "visible_frame": (0, 0, 1500, 970)}
# Two monitors without a menu bar, to the left of the built-in one and to its right (taller, and lower).
DELL_SCREEN_TO_THE_LEFT = {"name": "Dell", "frame": (-1920, 0, 1920, 1080), "visible_frame": (-1920, 0, 1920, 1080)}
STUDIO_SCREEN_TO_THE_RIGHT = {"name": "Studio", "frame": (1500, -200, 2500, 1400), "visible_frame": (1500, -200, 2500, 1400)}
