#!/usr/bin/env bash
# Install voice-journal-formatter as a launchd agent that scans on an interval.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LABEL="com.voice-journal-formatter.processor"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG_DIR="$HOME/Library/Logs"
CONFIG="${VOICE_JOURNAL_CONFIG:-$HOME/.config/voice-journal-formatter/config.toml}"
INTERVAL="${VOICE_JOURNAL_INTERVAL:-300}"
PYTHON="${PYTHON:-$(command -v python3)}"

if [[ "$(uname)" != "Darwin" ]]; then
  echo "This installer is macOS-only (launchd). On Linux, run 'voice-journal-formatter --scan'" >&2
  echo "from a systemd timer or cron entry instead." >&2
  exit 1
fi

if [[ ! -f "$CONFIG" ]]; then
  echo "No config at $CONFIG" >&2
  echo "Create it first:" >&2
  echo "  mkdir -p \"$(dirname "$CONFIG")\"" >&2
  echo "  cp \"$REPO/config.example.toml\" \"$CONFIG\"" >&2
  exit 1
fi

echo "Validating configuration..."
PYTHONPATH="$REPO" VOICE_JOURNAL_CONFIG="$CONFIG" "$PYTHON" -m voice_journal_formatter --check

mkdir -p "$HOME/Library/LaunchAgents" "$LOG_DIR"

sed -e "s|__PYTHON__|$PYTHON|g" \
    -e "s|__REPO__|$REPO|g" \
    -e "s|__CONFIG__|$CONFIG|g" \
    -e "s|__LOG_DIR__|$LOG_DIR|g" \
    -e "s|__INTERVAL__|$INTERVAL|g" \
    -e "s|__PATH__|/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin|g" \
    "$REPO/install/$LABEL.plist.template" > "$PLIST"

launchctl unload "$PLIST" 2>/dev/null || true
launchctl load "$PLIST"

echo
echo "Installed $LABEL (every ${INTERVAL}s)."
echo "  logs:   $LOG_DIR/voice-journal-formatter.log"
echo "  config: $CONFIG"
echo
echo "Captures that already exist are ignored. To backfill them once:"
echo "  PYTHONPATH=\"$REPO\" VOICE_JOURNAL_CONFIG=\"$CONFIG\" $PYTHON -m voice_journal_formatter --process-existing"
