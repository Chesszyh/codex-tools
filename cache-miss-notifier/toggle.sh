#!/usr/bin/env bash
set -euo pipefail

unit=codex-cache-miss-notifier.service

if [[ $(uname -s) == Darwin ]]; then
  label=codex-cache-miss-notifier
  domain="gui/$(id -u)"
  agent_file="$HOME/Library/LaunchAgents/$label.plist"
  if launchctl print "$domain/$label" >/dev/null 2>&1; then
    launchctl bootout "$domain/$label"
    osascript -e 'display notification "Paused" with title "Codex Cache Watcher"'
  else
    launchctl bootstrap "$domain" "$agent_file"
    osascript -e 'display notification "Running" with title "Codex Cache Watcher"'
  fi
  exit
fi

if systemctl --user is-active --quiet "$unit"; then
  systemctl --user stop "$unit"
  notify-send --app-name="Codex Cache Watcher" "Codex cache notifier paused"
else
  systemctl --user start "$unit"
  notify-send --app-name="Codex Cache Watcher" "Codex cache notifier running"
fi
