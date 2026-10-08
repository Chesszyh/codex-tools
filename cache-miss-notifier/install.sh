#!/usr/bin/env bash
set -euo pipefail

source_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
unit=codex-cache-miss-notifier

if [[ $(uname -s) == Darwin ]]; then
  agent_dir="$HOME/Library/LaunchAgents"
  agent_file="$agent_dir/$unit.plist"
  domain="gui/$(id -u)"
  python=$(command -v python3)
  install -d "$agent_dir"
  python3 - "$agent_file" "$python" "$source_dir/cache_miss_notifier.py" <<'PY'
import plistlib
import sys

path, python, script = sys.argv[1:]
with open(path, "wb") as output:
    plistlib.dump({
        "Label": "codex-cache-miss-notifier",
        "ProgramArguments": [python, script],
        "RunAtLoad": True,
        "KeepAlive": True,
    }, output)
PY
  launchctl bootout "$domain/$unit" 2>/dev/null || true
  launchctl bootstrap "$domain" "$agent_file"
  launchctl print "$domain/$unit"
  exit
fi

unit_dir="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"

install -d "$unit_dir"
sed "s|@EXEC_START@|$source_dir/cache_miss_notifier.py|" \
  "$source_dir/codex-cache-miss-notifier.service.in" \
  > "$unit_dir/codex-cache-miss-notifier.service"

systemctl --user daemon-reload
systemctl --user enable codex-cache-miss-notifier.service
systemctl --user restart codex-cache-miss-notifier.service
systemctl --user --no-pager --full status codex-cache-miss-notifier.service
