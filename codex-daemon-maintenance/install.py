#!/usr/bin/env python3
import os
import importlib
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent
LABEL = 'xyz.chesszyh.codex-daemon-maintenance'
JOB = Path.home() / 'Library/LaunchAgents' / f'{LABEL}.plist'
DATA = ROOT / 'data'
UNIT = 'codex-daemon-maintenance'


def install_macos():
    JOB.parent.mkdir(parents=True, exist_ok=True)
    with JOB.open('wb') as file:
        plistlib.dump({'Label': LABEL,
                      'ProgramArguments': [sys.executable, str(ROOT / 'maintain.py'), 'run'],
                      'WorkingDirectory': str(ROOT),
                      'RunAtLoad': True, 'StartInterval': 900,
                      'EnvironmentVariables': {'PATH': '/usr/bin:/bin:/usr/sbin:/sbin'},
                      'StandardOutPath': str(DATA / 'launchd.log'),
                      'StandardErrorPath': str(DATA / 'launchd-error.log')}, file)
    domain = f'gui/{os.getuid()}'
    subprocess.run(['launchctl', 'bootout', f'{domain}/{LABEL}'], capture_output=True)
    subprocess.run(['launchctl', 'bootstrap', domain, str(JOB)], check=True)
    print(f'Installed {LABEL}; checks every 15 minutes while logged in')


def systemd_quote(value):
    return '"' + str(value).replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%') + '"'


def linux_units(root, python):
    return {
        f'{UNIT}.service': (
            '[Unit]\nDescription=Maintain the shared Codex daemon when idle\n\n'
            '[Service]\nType=oneshot\n'
            f'WorkingDirectory={str(root).replace("%", "%%")}\n'
            f'ExecStart={systemd_quote(python)} {systemd_quote(root / "maintain.py")} run\n'
            'Environment="PATH=/usr/local/bin:/usr/bin:/bin"\n'
            'TimeoutStartSec=20min\n'
        ),
        f'{UNIT}.timer': (
            '[Unit]\nDescription=Check shared Codex daemon maintenance every 15 minutes\n\n'
            '[Timer]\nOnStartupSec=30s\nOnUnitInactiveSec=15min\nAccuracySec=30s\n'
            f'Unit={UNIT}.service\n\n[Install]\nWantedBy=timers.target\n'
        ),
    }


def install_linux():
    directory = Path.home() / '.config/systemd/user'
    units = linux_units(ROOT, sys.executable)
    with tempfile.TemporaryDirectory(prefix='codex-maintenance-units-') as temporary:
        paths = []
        for name, content in units.items():
            path = Path(temporary) / name
            path.write_text(content)
            paths.append(str(path))
        subprocess.run(['systemd-analyze', '--user', 'verify', *paths], check=True)
    directory.mkdir(parents=True, exist_ok=True)
    for name, content in units.items():
        (directory / name).write_text(content)
    subprocess.run(['systemctl', '--user', 'daemon-reload'], check=True)
    subprocess.run(['systemctl', '--user', 'enable', '--now', f'{UNIT}.timer'], check=True)
    subprocess.run(['systemctl', '--user', 'start', f'{UNIT}.service'], check=True)
    print(f'Installed {UNIT}.timer; checks every 15 minutes while the user manager is running')


def main():
    if sys.platform not in ('darwin', 'linux'):
        raise SystemExit('This installer supports macOS and Linux with systemd')
    if not (Path.home() / '.local/bin/codex').is_file():
        raise SystemExit('Install the official standalone Codex CLI in ~/.local/bin first; see README.md')
    try:
        importlib.import_module('websockets.asyncio.client')
    except ImportError:
        raise SystemExit('This Python needs websockets>=14; install it before running this installer')
    DATA.mkdir(mode=0o700, exist_ok=True)
    if sys.platform == 'darwin':
        install_macos()
    else:
        install_linux()


if __name__ == '__main__':
    main()
