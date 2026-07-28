#!/usr/bin/env bash
# Remove the voice-journal-formatter launchd agent. Notes, config, and state are left alone.
set -euo pipefail

LABEL="com.voice-journal-formatter.processor"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

if [[ -f "$PLIST" ]]; then
  launchctl unload "$PLIST" 2>/dev/null || true
  rm "$PLIST"
  echo "Removed $LABEL."
else
  echo "$LABEL is not installed."
fi

echo "Your notes, config, and state were not touched."
echo "State lives in ~/.local/state/voice-journal-formatter (unless you changed state_dir)."
