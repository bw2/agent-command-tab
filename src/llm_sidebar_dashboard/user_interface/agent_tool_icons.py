"""Small icons that tell agent tools apart in the list's icon column.

They are drawn from pixel grids in code, so no logo files ship with the project. A tool
without an icon here gets its name drawn as a small image instead.
"""

from AppKit import (NSAttributedString, NSColor, NSFontAttributeName, NSForegroundColorAttributeName, NSImage,
                    NSMakeRect, NSRectFill)

# One character per point, top row first: "#" body, "E" eye, " " empty.
# Claude Code's mascot, the four-legged figure in its start-up banner.
CLAUDE_CODE_MASCOT_PIXEL_ROWS = [
    "  ############  ",
    "  ############  ",
    "  ##E######E##  ",
    "  ##E######E##  ",
    "  ############  ",
    "################",
    "################",
    "  ############  ",
    "   # #    # #   ",
    "   # #    # #   ",
]
# Codex's ">_" prompt mark from its start-up banner.
CODEX_PROMPT_PIXEL_ROWS = [
    "##          ",
    " ##         ",
    "  ##        ",
    "   ##       ",
    "  ##        ",
    " ##         ",
    "##          ",
    "            ",
    "     #######",
    "     #######",
]


def build_pixel_grid_image(pixel_rows, colors_by_character):
    """Returns an NSImage, one point per grid cell, drawn on demand so it stays sharp at any
    scale and follows light and dark mode when given system colors.

    Args:
        pixel_rows: Equal-length strings, top row first; a space is transparent.
        colors_by_character: {character: NSColor}.
    """
    width, height = len(pixel_rows[0]), len(pixel_rows)

    def draw(destination_rect):
        for row_index, pixel_row in enumerate(pixel_rows):
            for column_index, character in enumerate(pixel_row):
                if character != " ":
                    colors_by_character[character].set()
                    NSRectFill(NSMakeRect(column_index, height - 1 - row_index, 1, 1))  # bottom-left origin
        return True

    return NSImage.imageWithSize_flipped_drawingHandler_((width, height), False, draw)


def build_text_image(text, font):
    """Returns an NSImage of text in the secondary label color, for a tool without an icon."""
    attributed_text = NSAttributedString.alloc().initWithString_attributes_(
        text, {NSFontAttributeName: font, NSForegroundColorAttributeName: NSColor.secondaryLabelColor()})
    size = attributed_text.size()

    def draw(destination_rect):
        attributed_text.drawAtPoint_((0, 0))
        return True

    return NSImage.imageWithSize_flipped_drawingHandler_((size.width, size.height), False, draw)


def build_agent_tool_icons():
    """Returns {agent tool name: NSImage} for the tools that have a drawn icon."""
    clay_orange = NSColor.colorWithSRGBRed_green_blue_alpha_(215 / 255, 119 / 255, 87 / 255, 1.0)
    return {
        "Claude Code": build_pixel_grid_image(CLAUDE_CODE_MASCOT_PIXEL_ROWS, {"#": clay_orange, "E": NSColor.blackColor()}),
        "Codex": build_pixel_grid_image(CODEX_PROMPT_PIXEL_ROWS, {"#": NSColor.labelColor()}),
    }
