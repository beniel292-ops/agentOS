#!/bin/bash
# Stop starting AgentOS at login and remove the LaunchAgent.
set -euo pipefail
DEST="$HOME/Library/LaunchAgents/com.agentos.plist"
launchctl bootout "gui/$(id -u)/com.agentos" 2>/dev/null || true
rm -f "$DEST"
echo "Removed. (Processes already running keep running until you stop them.)"
