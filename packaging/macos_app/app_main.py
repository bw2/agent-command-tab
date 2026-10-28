"""The .app's main script: the same as running `agent-command-tab` (the menu bar app, started again
whenever iTerm2's connection drops). Commands work too, for example
`AgentCommandTab.app/Contents/MacOS/AgentCommandTab remove-hooks`. Older macOS versions add a
"-psn_..." argument when Finder opens an app, which is left out."""

import os
import sys

from agent_command_tab.command_line import main

# py2app's startup script points these at a list of certificate authorities that build_app.sh takes
# out of the app. The app makes no TLS connections, and the programs it starts (claude, codex) must
# use this Mac's own certificates.
os.environ.pop("SSL_CERT_FILE", None)
os.environ.pop("SSL_CERT_DIR", None)

sys.exit(main([argument for argument in sys.argv[1:] if not argument.startswith("-psn_")]))
