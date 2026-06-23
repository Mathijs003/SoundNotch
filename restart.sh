#!/bin/bash
PLIST="$HOME/Library/LaunchAgents/com.mathijscop.soundnotch.plist"
launchctl unload "$PLIST" && launchctl load "$PLIST" && echo "Restarted."
