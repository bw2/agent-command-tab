"""Small icons that tell agent tools apart in the list's icon column.

They are drawn in code, Claude Code's from a pixel grid and Codex's from the OpenAI logo's
outline, so no logo files ship with the project. A tool without an icon here gets its name
drawn as a small image instead.
"""

from AppKit import (NSAttributedString, NSColor, NSCompositingOperationSourceAtop, NSFontAttributeName,
                    NSForegroundColorAttributeName, NSImage, NSMakeRect, NSRectFill, NSRectFillUsingOperation)

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
# The OpenAI logo for Codex, in place of the ">_" from Codex's start-up banner (user request,
# 2026-10-27). The outline is Simple Icons' (simpleicons.org), which they release under CC0.
OPENAI_LOGO_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path d="'
    "M22.2819 9.8211a5.9847 5.9847 0 0 0-.5157-4.9108 6.0462 6.0462 0 0 0-6.5098-2.9"
    "A6.0651 6.0651 0 0 0 4.9807 4.1818"
    "a5.9847 5.9847 0 0 0-3.9977 2.9 6.0462 6.0462 0 0 0 .7427 7.0966 5.98 5.98 0 0 0 .511 4.9107 6.051 6.051 0 0 0 6.5146 2.9001"
    "A5.9847 5.9847 0 0 0 13.2599 24"
    "a6.0557 6.0557 0 0 0 5.7718-4.2058 5.9894 5.9894 0 0 0 3.9977-2.9001 6.0557 6.0557 0 0 0-.7475-7.0729z"
    "m-9.022 12.6081a4.4755 4.4755 0 0 1-2.8764-1.0408l.1419-.0804 4.7783-2.7582a.7948.7948 0 0 0 .3927-.6813"
    "v-6.7369l2.02 1.1686a.071.071 0 0 1 .038.052v5.5826a4.504 4.504 0 0 1-4.4945 4.4944z"
    "m-9.6607-4.1254a4.4708 4.4708 0 0 1-.5346-3.0137l.142.0852 4.783 2.7582a.7712.7712 0 0 0 .7806 0"
    "l5.8428-3.3685v2.3324a.0804.0804 0 0 1-.0332.0615L9.74 19.9502a4.4992 4.4992 0 0 1-6.1408-1.6464z"
    "M2.3408 7.8956a4.485 4.485 0 0 1 2.3655-1.9728V11.6a.7664.7664 0 0 0 .3879.6765l5.8144 3.3543-2.0201 1.1685"
    "a.0757.0757 0 0 1-.071 0l-4.8303-2.7865A4.504 4.504 0 0 1 2.3408 7.872z"
    "m16.5963 3.8558L13.1038 8.364 15.1192 7.2a.0757.0757 0 0 1 .071 0l4.8303 2.7913"
    "a4.4944 4.4944 0 0 1-.6765 8.1042v-5.6772a.79.79 0 0 0-.407-.667z"
    "m2.0107-3.0231l-.142-.0852-4.7735-2.7818a.7759.7759 0 0 0-.7854 0L9.409 9.2297V6.8974"
    "a.0662.0662 0 0 1 .0284-.0615l4.8303-2.7866a4.4992 4.4992 0 0 1 6.6802 4.66z"
    "M8.3065 12.863l-2.02-1.1638a.0804.0804 0 0 1-.038-.0567V6.0742a4.4992 4.4992 0 0 1 7.3757-3.4537l-.142.0805"
    "L8.704 5.459a.7948.7948 0 0 0-.3927.6813z"
    "m1.0976-2.3654l2.602-1.4998 2.6069 1.4998v2.9994l-2.5974 1.4997-2.6067-1.4997Z"
    '"/></svg>'
)
# Points square: a round shape this size looks about as big as the mascot, 10 points high.
OPENAI_LOGO_SIZE = 12


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


def build_svg_image(svg_text, size, color):
    """Returns an NSImage of an SVG's shapes filled with one color, drawn on demand so it stays
    sharp at any scale and follows light and dark mode when given a system color, or None
    before macOS 14, which can't read SVG.

    Args:
        svg_text: The SVG document.
        size: The image's width and height in points.
        color: NSColor.
    """
    svg_image = NSImage.alloc().initWithData_(svg_text.encode())
    if svg_image is None:
        return None

    def draw(destination_rect):
        svg_image.drawInRect_(destination_rect)
        color.set()
        NSRectFillUsingOperation(destination_rect, NSCompositingOperationSourceAtop)  # colors only the shapes
        return True

    return NSImage.imageWithSize_flipped_drawingHandler_((size, size), False, draw)


def build_text_image(text, font):
    """Returns an NSImage of text in the secondary label color, for a tool without an icon."""
    attributed_text = NSAttributedString.alloc().initWithString_attributes_(
        text, {NSFontAttributeName: font, NSForegroundColorAttributeName: NSColor.secondaryLabelColor()})
    size = attributed_text.size()

    def draw(destination_rect):
        attributed_text.drawAtPoint_((0, 0))
        return True

    return NSImage.imageWithSize_flipped_drawingHandler_((size.width, size.height), False, draw)


def describe_agent_tool_icon(agent_tool_name):
    """Returns the icon's hover text: the tool's name, with the company for Codex, whose icon is
    the OpenAI logo (user request, 2026-10-27)."""
    return {"Codex": "OpenAI Codex"}.get(agent_tool_name, agent_tool_name)


def build_agent_tool_icons():
    """Returns {agent tool name: NSImage} for the tools that have a drawn icon."""
    clay_orange = NSColor.colorWithSRGBRed_green_blue_alpha_(215 / 255, 119 / 255, 87 / 255, 1.0)
    icons_by_name = {
        "Claude Code": build_pixel_grid_image(CLAUDE_CODE_MASCOT_PIXEL_ROWS, {"#": clay_orange, "E": NSColor.blackColor()})}
    openai_logo = build_svg_image(OPENAI_LOGO_SVG, OPENAI_LOGO_SIZE, NSColor.labelColor())
    if openai_logo is not None:  # before macOS 14, Codex gets its name drawn instead
        icons_by_name["Codex"] = openai_logo
    return icons_by_name
