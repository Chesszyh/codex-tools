#!/usr/bin/env python3
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import uuid

ROOT = Path(__file__).resolve().parent


def alive(pid):
    try:
        return Path(f'/proc/{pid}/stat').read_text().rpartition(') ')[2].split()[0] != 'Z'
    except FileNotFoundError:
        return False


def main():
    if sys.platform != 'linux':
        raise SystemExit('Run this integration check on Linux with a systemd user manager')
    with tempfile.TemporaryDirectory(prefix='codex-maintenance-lifecycle-') as temporary:
        directory = Path(temporary)
        for mode in ('direct', 'scoped'):
            pid_file = directory / f'{mode}.pid'
            fake_cli = directory / f'{mode}-codex'
            fake_cli.write_text(
                f'#!{sys.executable}\n'
                'import subprocess,sys\nfrom pathlib import Path\n'
                'p=subprocess.Popen([sys.executable,"-c","import time; time.sleep(120)"],'
                'stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,'
                'start_new_session=True)\n'
                f'Path({str(pid_file)!r}).write_text(str(p.pid))\n'
            )
            fake_cli.chmod(0o700)
            runner = directory / f'{mode}.py'
            runner.write_text(
                f'import sys,subprocess\nfrom pathlib import Path\nsys.path.insert(0,{str(ROOT)!r})\n'
                f'import maintain\nmaintain.CLI=Path({str(fake_cli)!r})\n'
                + ('maintain.command("app-server","daemon","start")\n' if mode == 'scoped'
                   else f'subprocess.run([{str(fake_cli)!r},"app-server","daemon","start"],check=True)\n')
            )
            unit = f'codex-maintenance-qa-{uuid.uuid4().hex[:12]}'
            pid = None
            try:
                subprocess.run(['/usr/bin/systemd-run', '--user', '--wait', '--collect', '--quiet',
                                f'--unit={unit}', '--property=Type=oneshot',
                                sys.executable, str(runner)], check=True, timeout=30)
                pid = int(pid_file.read_text())
                survived = alive(pid)
                if mode == 'direct':
                    assert not survived, 'Negative control did not reproduce oneshot child cleanup'
                    print('PASS: direct launch reproduces daemon death after maintenance exits')
                else:
                    assert survived, 'Scoped daemon died when maintenance exited'
                    cgroup = Path(f'/proc/{pid}/cgroup').read_text()
                    assert '.scope' in cgroup and unit not in cgroup, cgroup
                    print('PASS: scoped daemon survives maintenance exit in its own cgroup')
            finally:
                if pid is None and pid_file.exists():
                    pid = int(pid_file.read_text())
                if pid is not None and alive(pid):
                    os.kill(pid, signal.SIGTERM)
                subprocess.run(['systemctl', '--user', 'stop', f'{unit}.service'],
                               capture_output=True)


if __name__ == '__main__':
    main()
