"""Check process selection and real systemd lifecycle without exiting ChatGPT."""
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app_services_linux import app_running


def command(*args):
    return subprocess.run(['systemctl', '--user', *args], check=True, capture_output=True, text=True).stdout


def wait_for(predicate, description):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if predicate():
            print('PASS:', description, flush=True)
            return
        time.sleep(.1)
    raise AssertionError(description)


def main():
    unit_dir = Path(os.environ.get('XDG_CONFIG_HOME', Path.home()/'.config')) / 'systemd/user'
    prefix = f'codex-app-services-test-{os.getpid()}'
    supervisor = prefix + '.service'
    workers = [prefix + f'-worker-{i}.service' for i in range(2)]
    files = [unit_dir/name for name in [supervisor, *workers]]
    app = None
    with tempfile.TemporaryDirectory(prefix='chatgpt-lifecycle-test-') as tmp:
        path = Path(tmp)
        executable = path/'TestApp'
        shutil.copyfile(shutil.which('sleep'), executable)
        executable.chmod(0o755)
        proc = path/'proc'
        entry = proc/'1'
        entry.mkdir(parents=True)
        (entry/'exe').symlink_to(executable)
        (entry/'cmdline').write_bytes(b'TestApp\0--type=renderer\0')
        assert not app_running(executable, proc), 'renderer was counted as main app'
        (entry/'cmdline').write_bytes(b'TestApp\0')
        assert app_running(executable, proc)
        print('PASS: renderer exclusion and main-process detection', flush=True)

        def pid(name):
            return int(command('show', name, '-p', 'MainPID', '--value').strip())

        def all_started():
            return all(pid(name) > 0 for name in workers)

        def all_stopped():
            return all(pid(name) == 0 for name in workers)

        try:
            for name in workers:
                (unit_dir/name).write_text(
                    f'[Unit]\nBindsTo={supervisor}\nAfter={supervisor}\n\n'
                    '[Service]\nExecStart=/usr/bin/sleep 600\nRestart=on-failure\nRestartSec=.2\n')
            (unit_dir/supervisor).write_text(
                f'[Service]\nExecStart={sys.executable} {ROOT/"app_services_linux.py"} '
                f'--app-executable {executable} --interval .2 --services {" ".join(workers)}\n')
            command('daemon-reload')
            command('start', supervisor)
            wait_for(all_stopped, 'services remain stopped without app')
            app = subprocess.Popen([str(executable), '600'])
            wait_for(all_started, 'both services start with app')
            previous = [pid(name) for name in workers]
            time.sleep(.7)
            assert previous == [pid(name) for name in workers]
            print('PASS: running workers are not restarted on each poll', flush=True)
            os.kill(previous[0], signal.SIGKILL)
            wait_for(lambda: pid(workers[0]) not in (0, previous[0]), 'failed service recovers')
            command('stop', workers[0])
            time.sleep(.7)
            assert pid(workers[0]) == 0, 'manual pause was overridden'
            print('PASS: intentional pause stays paused while app runs', flush=True)
            app.terminate()
            app.wait(timeout=5)
            app = None
            wait_for(all_stopped, 'both services stop after app exits')
            app = subprocess.Popen([str(executable), '600'])
            wait_for(all_started, 'both services start when app reopens')
            command('stop', supervisor)
            wait_for(all_stopped, 'stopping supervisor stops both services')
        finally:
            if app:
                app.terminate()
                app.wait(timeout=5)
            subprocess.run(['systemctl', '--user', 'stop', supervisor, *workers], check=False)
            for file in files:
                file.unlink(missing_ok=True)
            command('daemon-reload')


if __name__ == '__main__':
    main()
