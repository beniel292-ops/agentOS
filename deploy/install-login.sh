#!/bin/bash
# Start AgentOS (./start-all.sh prod) every time you log in to this Mac.
# Usage: deploy/install-login.sh   (undo with deploy/uninstall-login.sh)
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
DEST="$HOME/Library/LaunchAgents/com.agentos.plist"
mkdir -p "$HOME/Library/LaunchAgents" "$HOME/Library/Logs"
sed -e "s|__REPO__|$REPO|g" -e "s|__HOME__|$HOME|g" "$REPO/deploy/com.agentos.plist" > "$DEST"
plutil -lint "$DEST" >/dev/null
launchctl bootout "gui/$(id -u)/com.agentos" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$DEST"
echo "Installed. AgentOS starts at login and now. Log: ~/Library/Logs/agentos.log"
