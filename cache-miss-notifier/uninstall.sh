#!/usr/bin/env bash
set -euo pipefail

if [[ $(uname -s) == Darwin ]]; then
  label=codex-cache-miss-notifier
  launchctl bootout "gui/$(id -u)/$label" 2>/dev/null || true
  rm -f "$HOME/Library/LaunchAgents/$label.plist"
  exit
fi

unit_dir="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"

systemctl --user disable --now codex-cache-miss-notifier.service || true
rm -f "$unit_dir/codex-cache-miss-notifier.service"
systemctl --user daemon-reload
