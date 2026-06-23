#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$SCRIPT_DIR/.venv"
PLIST_LABEL="com.mathijscop.soundnotch"
PLIST_DEST="$HOME/Library/LaunchAgents/$PLIST_LABEL.plist"

echo "==> Setting up virtual environment..."
/opt/homebrew/bin/python3 -m venv "$VENV_DIR"
"$VENV_DIR/bin/pip" install --quiet --upgrade pip
"$VENV_DIR/bin/pip" install --quiet -r "$SCRIPT_DIR/requirements.txt"

echo "==> Installing LaunchAgent..."
mkdir -p "$HOME/Library/LaunchAgents"

cat > "$PLIST_DEST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$PLIST_LABEL</string>
    <key>ProgramArguments</key>
    <array>
        <string>$VENV_DIR/bin/python3</string>
        <string>$SCRIPT_DIR/spotify_bar.py</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardErrorPath</key>
    <string>/tmp/soundnotch.err</string>
    <key>StandardOutPath</key>
    <string>/tmp/soundnotch.out</string>
    <key>LSUIElement</key>
    <true/>
</dict>
</plist>
PLIST

echo "==> Loading LaunchAgent (starting now + on every login)..."
launchctl unload "$PLIST_DEST" 2>/dev/null || true
launchctl load "$PLIST_DEST"

echo ""
echo "Done! SoundNotch is running. Look for '♪' in your menu bar."
echo "To uninstall, run: ./uninstall.sh"
