#!/usr/bin/env python3
"""Start and stop user monitoring services with the ChatGPT main process."""
import argparse
import os
from pathlib import Path
import signal
import subprocess
import threading


SERVICES = ('codex-cache-miss-notifier.service', 'codex-speed-monitor.service')
SUPERVISOR = 'codex-app-monitor-services.service'


def app_running(executable: Path, proc: Path = Path('/proc')) -> bool:
    target = str(executable.resolve())
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            linked = os.readlink(entry / 'exe').removesuffix(' (deleted)')
            if linked != target:
                continue
            arguments = (entry / 'cmdline').read_bytes().split(b'\0')
            if arguments[0] and not any(arg.startswith(b'--type=') for arg in arguments[1:]):
                return True
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            continue
    return False


def systemctl(action: str, services: list[str]) -> None:
    subprocess.run(['systemctl', '--user', action, *services], check=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app-executable', type=Path, required=True)
    parser.add_argument('--services', nargs='+', default=list(SERVICES))
    parser.add_argument('--interval', type=float, default=1)
    args = parser.parse_args()
    if args.interval <= 0:
        parser.error('--interval must be positive')
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    was_running = None
    try:
        while not stop.is_set():
            running = app_running(args.app_executable)
            if running != was_running:
                systemctl('start' if running else 'stop', args.services)
                print(f'ChatGPT: {"running" if running else "stopped"}', flush=True)
                was_running = running
            # Workers recover failures through systemd; intentional pauses stay paused.
            stop.wait(args.interval)
        return 0
    finally:
        systemctl('stop', args.services)


if __name__ == '__main__':
    raise SystemExit(main())
