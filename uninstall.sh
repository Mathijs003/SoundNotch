#!/bin/bash
PLIST_LABEL="com.soundnotch.app"
PLIST_DEST="$HOME/Library/LaunchAgents/$PLIST_LABEL.plist"

echo "==> Stopping and removing LaunchAgent..."
launchctl unload "$PLIST_DEST" 2>/dev/null || true
rm -f "$PLIST_DEST"

echo "==> Removing virtual environment..."
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
rm -rf "$SCRIPT_DIR/.venv"

echo "Done. SoundNotch has been uninstalled."
