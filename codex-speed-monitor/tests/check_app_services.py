"""Exercise real workspace app events and launchd jobs without quitting ChatGPT."""
import os
from pathlib import Path
import plistlib
import re
import signal
import subprocess
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
DOMAIN = f'gui/{os.getuid()}'


def command(*args, check=True):
    return subprocess.run(args, check=check, capture_output=True, text=True)


def wait_for(predicate, description, timeout=25):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            print('PASS:', description, flush=True)
            return
        time.sleep(.25)
    raise AssertionError(description)


def main():
    with tempfile.TemporaryDirectory(prefix='app-services-check-') as tmp:
        path = Path(tmp)
        app_id = f'local.codex.service-test.p{os.getpid()}'
        labels = [f'{app_id}.service{i}' for i in range(2)]
        app = path / 'ServiceTest.app'
        macos = app / 'Contents/MacOS'
        macos.mkdir(parents=True)
        (app / 'Contents/Info.plist').write_bytes(plistlib.dumps({
            'CFBundleIdentifier': app_id, 'CFBundleExecutable': 'ServiceTest',
            'CFBundleName': 'ServiceTest', 'CFBundlePackageType': 'APPL',
        }))
        source = path / 'app.swift'
        source.write_text('import AppKit\nlet app = NSApplication.shared\napp.setActivationPolicy(.regular)\napp.run()\n')
        command('/usr/bin/swiftc', str(source), '-o', str(macos / 'ServiceTest'))
        supervisor = path / 'supervisor'
        command('/usr/bin/swiftc', '-swift-version', '6', str(ROOT / 'app_services.swift'), '-o', str(supervisor))
        app_pid = None
        watcher = None

        def service_pid(label):
            output = command('/bin/launchctl', 'print', f'{DOMAIN}/{label}', check=False).stdout
            match = re.search(r'^\s*pid = (\d+)', output, re.M)
            return int(match[1]) if match else None

        def all_started():
            return all(service_pid(label) for label in labels)

        def all_stopped():
            return all(service_pid(label) is None for label in labels)

        def open_app():
            nonlocal app_pid
            command('/usr/bin/open', '-n', '-g', str(app))
            def find_pid():
                nonlocal app_pid
                lines = command('/bin/ps', '-axo', 'pid,command').stdout.splitlines()
                for line in lines:
                    if line.strip().endswith(str(macos / 'ServiceTest')):
                        app_pid = int(line.split()[0])
                        return True
                return False
            wait_for(find_pid, 'test app started')

        try:
            for label in labels:
                config = path / f'{label}.plist'
                config.write_bytes(plistlib.dumps({'Label': label, 'ProgramArguments': ['/bin/sleep', '600'],
                                                  'RunAtLoad': False, 'KeepAlive': False}))
                command('/bin/launchctl', 'bootstrap', DOMAIN, str(config))
            watcher = subprocess.Popen([str(supervisor), app_id, *labels], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            time.sleep(1)
            assert watcher.poll() is None
            wait_for(all_stopped, 'services stay stopped without app')
            open_app()
            wait_for(all_started, 'both services start with app')
            before = [service_pid(label) for label in labels]
            time.sleep(6)
            assert before == [service_pid(label) for label in labels], 'running services were restarted'
            print('PASS: periodic reconciliation preserves running PIDs', flush=True)
            os.kill(before[0], signal.SIGTERM)
            wait_for(lambda: service_pid(labels[0]) not in (None, before[0]), 'exited service recovers')
            os.kill(app_pid, signal.SIGTERM)
            app_pid = None
            wait_for(all_stopped, 'both services stop when app exits')
            time.sleep(6)
            assert all_stopped(), 'service restarted without app'
            open_app()
            wait_for(all_started, 'both services start when app reopens')
        finally:
            if app_pid:
                os.kill(app_pid, signal.SIGTERM)
            if watcher:
                watcher.terminate()
                out, err = watcher.communicate(timeout=5)
                print(out.strip())
                assert not err, err
            for label in labels:
                command('/bin/launchctl', 'bootout', f'{DOMAIN}/{label}', check=False)


if __name__ == '__main__':
    main()
