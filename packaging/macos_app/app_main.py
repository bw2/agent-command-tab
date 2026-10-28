"""The .app's main script: the same as running `agent-command-tab` (the menu bar app, started again
whenever iTerm2's connection drops). Commands work too, for example
`AgentCommandTab.app/Contents/MacOS/AgentCommandTab remove-hooks`. Older macOS versions add a
"-psn_..." argument when Finder opens an app, which is left out."""

import sys

from agent_command_tab.command_line import main

sys.exit(main([argument for argument in sys.argv[1:] if not argument.startswith("-psn_")]))
