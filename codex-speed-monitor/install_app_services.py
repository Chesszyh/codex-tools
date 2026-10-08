#!/usr/bin/env python3
"""Install monitoring services that start and stop with ChatGPT."""
import argparse
import os
from pathlib import Path
import plistlib
import json
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent
SERVICES = ('xyz.chesszyh.codex-speed-monitor', 'codex-cache-miss-notifier')
SUPERVISOR = 'xyz.chesszyh.chatgpt-monitor-services'
AGENTS = Path.home() / 'Library/LaunchAgents'
DATA = ROOT / 'data'
DOMAIN = f'gui/{os.getuid()}'


def launchctl(*args, required=True):
    deadline = time.monotonic() + 15
    while True:
        result = subprocess.run(['/bin/launchctl', *args], capture_output=True, text=True)
        # bootout can return before launchd finishes removing the old job.
        if args[0] == 'bootstrap' and result.returncode == 5 and time.monotonic() < deadline:
            time.sleep(.5)
            continue
        if required and result.returncode:
            raise RuntimeError(result.stderr.strip() or f'launchctl exited {result.returncode}')
        return


def write_plist(path, config):
    temporary = path.with_suffix('.plist.tmp')
    temporary.write_bytes(plistlib.dumps(config))
    temporary.replace(path)


def install_linux():
    from app_services_linux import SERVICES as units, SUPERVISOR as supervisor

    launcher = shutil.which('chatgpt')
    if not launcher:
        raise FileNotFoundError('找不到 chatgpt，请先安装 ChatGPT App。')
    app = Path(launcher).resolve().parent / 'ChatGPT'
    notifier = ROOT.parent / 'cache-miss-notifier/cache_miss_notifier.py'
    for path in (app, notifier, ROOT / 'monitor.py', ROOT / 'app_services_linux.py'):
        if not path.is_file():
            raise FileNotFoundError(path)
    for command in ('systemctl', 'inotifywait', 'notify-send'):
        if not shutil.which(command):
            raise FileNotFoundError(f'缺少命令：{command}')
    unit_dir = Path(os.environ.get('XDG_CONFIG_HOME', Path.home()/'.config')) / 'systemd/user'
    unit_dir.mkdir(parents=True, exist_ok=True)
    quote = lambda path: json.dumps(str(path))
    if (unit_dir/supervisor).exists():
        subprocess.run(['systemctl', '--user', 'stop', supervisor], check=True)
    subprocess.run(['systemctl', '--user', 'disable', '--now', *units], check=True)
    configs = {
        units[0]: ('Notify on Codex cache misses and large cache hits', notifier),
        units[1]: ('Codex Session Speed Monitor', ROOT / 'monitor.py'),
    }
    for name, (description, script) in configs.items():
        (unit_dir/name).write_text(
            f'[Unit]\nDescription={description}\nBindsTo={supervisor}\nAfter={supervisor}\n\n'
            f'[Service]\nType=simple\nExecStart={quote(sys.executable)} {quote(script)}\n'
            'Restart=on-failure\nRestartSec=2\nEnvironment=PYTHONUNBUFFERED=1\n', encoding='utf-8')
    (unit_dir/supervisor).write_text(
        '[Unit]\nDescription=Run Codex monitoring services with ChatGPT\n\n'
        f'[Service]\nType=simple\nExecStart={quote(sys.executable)} {quote(ROOT/"app_services_linux.py")} '
        f'--app-executable {quote(app)}\nRestart=on-failure\nRestartSec=2\n\n'
        '[Install]\nWantedBy=default.target\n', encoding='utf-8')
    subprocess.run(['systemctl', '--user', 'daemon-reload'], check=True)
    subprocess.run(['systemctl', '--user', 'enable', supervisor], check=True)
    subprocess.run(['systemctl', '--user', 'restart', supervisor], check=True)
    print('缓存提醒和输出速度检测已随 ChatGPT App 启停。')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    if sys.platform == 'linux':
        install_linux()
        return
    if sys.platform != 'darwin':
        parser.error('This installer requires Linux or macOS.')

    configs = {}
    for label in SERVICES:
        path = AGENTS / f'{label}.plist'
        config = plistlib.loads(path.read_bytes())
        if config.get('Label') != label:
            raise ValueError(f'Unexpected service label in {path}')
        for argument in (config['ProgramArguments'][0], config['ProgramArguments'][-1]):
            if not Path(argument).is_file():
                raise FileNotFoundError(argument)
        configs[label] = config
    DATA.mkdir(exist_ok=True)
    executable = DATA / 'app-services'
    temporary = DATA / 'app-services-new'
    subprocess.run(['/usr/bin/swiftc', '-swift-version', '6', str(ROOT / 'app_services.swift'), '-o', str(temporary)], check=True)
    temporary.replace(executable)
    launchctl('bootout', f'{DOMAIN}/{SUPERVISOR}', required=False)
    for label, config in configs.items():
        launchctl('bootout', f'{DOMAIN}/{label}', required=False)
        config['RunAtLoad'] = False
        config['KeepAlive'] = False
        path = AGENTS / f'{label}.plist'
        write_plist(path, config)
        launchctl('bootstrap', DOMAIN, str(path))
    logs = DATA / 'logs'
    logs.mkdir(exist_ok=True)
    path = AGENTS / f'{SUPERVISOR}.plist'
    write_plist(path, {
        'Label': SUPERVISOR,
        'ProgramArguments': [str(executable), 'com.openai.codex', *SERVICES],
        'RunAtLoad': True, 'KeepAlive': True, 'ThrottleInterval': 10,
        'StandardOutPath': str(logs / 'app-services.stdout.log'),
        'StandardErrorPath': str(logs / 'app-services.stderr.log'),
    })
    launchctl('bootstrap', DOMAIN, str(path))
    print('Both services now follow ChatGPT.')


if __name__ == '__main__':
    main()
