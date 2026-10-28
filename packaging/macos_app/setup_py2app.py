"""py2app's settings for AgentCommandTab.app. Run build_app.sh, which calls `python setup_py2app.py py2app`
in a build environment where the app's package is installed (docs/RELEASING.md)."""

import importlib.metadata
import sysconfig

from setuptools import setup

# Don't change it: macOS ties the permissions a user grants the app (controlling iTerm2,
# Accessibility for the shortcut) to this name, so a new name asks for them again.
BUNDLE_IDENTIFIER = "com.longfellowlabs.AgentCommandTab"
VERSION = importlib.metadata.version("agent-command-tab")

setup(
    name="AgentCommandTab",
    app=["app_main.py"],
    options={"py2app": {
        "iconfile": "build/AgentCommandTab.icns",
        # The bundled iterm2 package is GPL-2.0-or-later, so the app carries that license and says so.
        "resources": ["../../LICENSE", "GPL-2.0.txt", "NOTICE.txt"],
        # Copied as plain files rather than zipped: the hooks run hook_event_forwarder.py by its path.
        "packages": ["agent_command_tab"],
        "plist": {
            "CFBundleIdentifier": BUNDLE_IDENTIFIER,
            "CFBundleName": "AgentCommandTab",
            "CFBundleDisplayName": "AgentCommandTab",
            "CFBundleShortVersionString": VERSION,
            "CFBundleVersion": VERSION,
            "NSHumanReadableCopyright": "Copyright (c) 2026 LongfellowLabs LLC. MIT License.",  # as in LICENSE
            # The oldest macOS the bundled Python runs on: 15 for Homebrew's, 10.13 for python.org's.
            "LSMinimumSystemVersion": sysconfig.get_config_var("MACOSX_DEPLOYMENT_TARGET"),
            "LSUIElement": True,  # a menu bar app: no Dock icon, also for the process that restarts the app
            # Shown when macOS asks the user to let the app control iTerm2 (iTerm2's API access key).
            "NSAppleEventsUsageDescription": "AgentCommandTab asks iTerm2 for access to iTerm2's Python API.",
        },
    }},
)
