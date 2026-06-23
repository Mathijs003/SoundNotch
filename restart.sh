#!/bin/bash
PLIST="$HOME/Library/LaunchAgents/com.soundnotch.app.plist"
launchctl unload "$PLIST" && launchctl load "$PLIST" && echo "Restarted."
