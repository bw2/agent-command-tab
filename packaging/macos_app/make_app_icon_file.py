"""Writes the app's icon (agent_command_tab/user_interface/app_icon.py, drawn in code) as the
.icns file an app bundle needs.

Usage: python3 make_app_icon_file.py OUTPUT.icns
"""

import os
import subprocess
import sys
import tempfile

from AppKit import (NSBitmapImageRep, NSCalibratedRGBColorSpace, NSCompositingOperationCopy, NSGraphicsContext,
                    NSMakeRect, NSPNGFileType, NSZeroRect)

from agent_command_tab.user_interface.app_icon import build_app_icon

# The point sizes macOS asks an .icns for; each is also drawn at twice its size for Retina screens.
ICON_POINT_SIZES = (16, 32, 128, 256, 512)


def write_icon_png(icon, pixel_size, png_file_path):
    """Draws the icon into a square bitmap pixel_size pixels wide and saves it as a PNG."""
    bitmap = NSBitmapImageRep.alloc().initWithBitmapDataPlanes_pixelsWide_pixelsHigh_bitsPerSample_samplesPerPixel_hasAlpha_isPlanar_colorSpaceName_bytesPerRow_bitsPerPixel_(
        None, pixel_size, pixel_size, 8, 4, True, False, NSCalibratedRGBColorSpace, 0, 0)
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.setCurrentContext_(NSGraphicsContext.graphicsContextWithBitmapImageRep_(bitmap))
    icon.drawInRect_fromRect_operation_fraction_(NSMakeRect(0, 0, pixel_size, pixel_size), NSZeroRect,
                                                 NSCompositingOperationCopy, 1.0)
    NSGraphicsContext.restoreGraphicsState()
    if not bitmap.representationUsingType_properties_(NSPNGFileType, {}).writeToFile_atomically_(png_file_path, True):
        raise SystemExit(f"could not write {png_file_path}")


def write_icns_file(icns_file_path):
    """Writes every size into an .iconset folder, which iconutil turns into the .icns file."""
    icon = build_app_icon()
    with tempfile.TemporaryDirectory() as temporary_folder:
        iconset_folder = os.path.join(temporary_folder, "AgentCommandTab.iconset")
        os.mkdir(iconset_folder)
        for point_size in ICON_POINT_SIZES:
            write_icon_png(icon, point_size, os.path.join(iconset_folder, f"icon_{point_size}x{point_size}.png"))
            write_icon_png(icon, 2 * point_size, os.path.join(iconset_folder, f"icon_{point_size}x{point_size}@2x.png"))
        if subprocess.call(["iconutil", "--convert", "icns", "--output", icns_file_path, iconset_folder]) != 0:
            raise SystemExit("iconutil could not make the .icns file")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    write_icns_file(sys.argv[1])
