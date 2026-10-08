#!/usr/bin/env python3
import argparse
import asyncio
import errno
import fcntl
import json
import os
from pathlib import Path
import subprocess
import socket
import struct
import sys
import time

from websockets.asyncio.client import unix_connect

HOME = Path.home()
CLI = HOME / '.local/bin/codex'
DATA = Path(__file__).resolve().parent / 'data'
UPDATE_INTERVAL = 24 * 60 * 60


def command(*args, timeout=30):
    argv = [str(CLI), *args]
    if sys.platform == 'linux' and args[:2] == ('app-server', 'daemon') and args[2:3] in (
            ('start',), ('bootstrap',), ('update',), ('restart',)):
        # The PID-managed daemon must outlive the maintenance oneshot's cgroup.
        argv = ['/usr/bin/systemd-run', '--user', '--scope', '--quiet', '--', *argv]
    result = subprocess.run(argv, stdin=subprocess.DEVNULL,
                            capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError(f'codex {" ".join(args)} failed: {result.stderr.strip()}')
    return result.stdout


def reachable(path):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
        probe.settimeout(2)
        try:
            probe.connect(str(path))
        except OSError as error:
            if error.errno in (errno.ENOENT, errno.ECONNREFUSED):
                return False
            raise
    return True


def versions():
    socket_path = HOME / '.codex/app-server-control/app-server-control.sock'
    if not socket_path.exists() or not reachable(socket_path):
        package = HOME / '.codex/packages/app-server-daemon/current/bin/codex'
        managed = None
        if package.is_file():
            managed = subprocess.check_output([str(package), '--version'], text=True,
                                              timeout=10).strip().split()[-1]
        # daemon version exits with an error for absent or stale sockets.
        return {'status': 'stopped', 'socketPath': str(socket_path),
                'cliVersion': command('--version').strip().split()[-1],
                'managedCodexPath': str(package), 'managedCodexVersion': managed,
                'appServerVersion': None}
    return json.loads(command('app-server', 'daemon', 'version'))


async def loaded_threads(socket_path):
    async with unix_connect(socket_path, open_timeout=5) as ws:
        counter = 0

        async def rpc(method, params):
            nonlocal counter
            counter += 1
            await ws.send(json.dumps({'id': counter, 'method': method, 'params': params}))
            while True:
                message = json.loads(await asyncio.wait_for(ws.recv(), 5))
                if message.get('id') == counter:
                    if 'error' in message:
                        raise RuntimeError(f'{method}: {message["error"]}')
                    return message['result']

        await rpc('initialize', {'clientInfo': {'name': 'codex_daemon_maintenance',
                                               'version': '1.0'},
                                 'capabilities': {'experimentalApi': True}})
        await ws.send(json.dumps({'method': 'initialized'}))
        count = 0
        cursor = None
        seen = set()
        while True:
            result = await rpc('thread/loaded/list', {'limit': 100, 'cursor': cursor})
            if not isinstance(result.get('data'), list):
                raise RuntimeError('Cannot verify loaded sessions; update deferred')
            count += len(result['data'])
            cursor = result.get('nextCursor')
            if cursor is None:
                return count
            if cursor in seen:
                raise RuntimeError('Repeated session cursor; update deferred')
            seen.add(cursor)


def shared_daemon_pid(socket_path):
    if sys.platform != 'linux' or not socket_path:
        return None
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
        probe.settimeout(2)
        probe.connect(socket_path)
        pid, uid, _ = struct.unpack('3i', probe.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
        if uid != os.getuid() or pid <= 0:
            raise RuntimeError('Cannot verify shared daemon process ownership')
        return pid


def standalone_workers(socket_path=None):
    daemon_pid = shared_daemon_pid(socket_path)
    output = subprocess.check_output(['/bin/ps', '-axo', 'pid=,ppid=,command='], text=True)
    processes = {}
    for line in output.splitlines():
        fields = line.strip().split(None, 2)
        if len(fields) == 3:
            processes[int(fields[0])] = (int(fields[1]), fields[2].split()[0])

    def belongs_to_daemon(pid):
        seen = set()
        while pid in processes and pid not in seen:
            if pid == daemon_pid:
                return True
            seen.add(pid)
            pid = processes[pid][0]
        return False

    workers = []
    for pid, (_, executable) in processes.items():
        # Idle daemon children are replaced with their parent; independent sessions are not.
        if belongs_to_daemon(pid):
            continue
        # Self-update may remove release files still needed by independent CLI sessions.
        if '/packages/standalone/releases/' in str(Path(executable).resolve()):
            workers.append(pid)
    return workers


def idle(info):
    if info.get('status') == 'running':
        count = asyncio.run(loaded_threads(info['socketPath']))
        if count:
            return f'{count} loaded session(s); update deferred until they close'
    elif info.get('status') != 'stopped':
        raise RuntimeError(f'Unknown daemon status: {info.get("status")!r}')
    workers = standalone_workers(info.get('socketPath') if info.get('status') == 'running' else None)
    if workers:
        return f'Independent standalone processes {workers}; update deferred'
    return None


def needs_sync(info):
    target = info.get('cliVersion')
    if not target:
        raise RuntimeError('CLI version unavailable')
    package = Path(info['managedCodexPath']).resolve()
    host = package.parent / 'codex-code-mode-host'
    return (info.get('managedCodexVersion') != target or
            info.get('appServerVersion') != target or
            not package.is_file() or not os.access(package, os.X_OK) or
            not host.is_file() or not os.access(host, os.X_OK))


def reconcile(info):
    if not info.get('managedCodexVersion'):
        if info.get('status') == 'running':
            command('app-server', 'daemon', 'stop')
        command('app-server', 'daemon', 'bootstrap', timeout=300)
        info = versions()
    if info.get('status') == 'stopped' and info.get('managedCodexVersion') == info.get('cliVersion'):
        command('app-server', 'daemon', 'start', timeout=60)
        info = versions()
    if needs_sync(info):
        command('app-server', 'daemon', 'update', '--from-cli', '--yes', timeout=300)
    elif info.get('status') != 'running':
        command('app-server', 'daemon', 'start', timeout=60)
    final = versions()
    if final.get('status') != 'running' or needs_sync(final):
        raise RuntimeError(f'Daemon version verification failed: {final}')
    return final


def maintain(state, check_updates=False):
    before = versions()
    reason = idle(before)
    if reason:
        return {'result': 'deferred', 'reason': reason, 'versions': before}
    reconcile(before)
    if check_updates or time.time() - state.get('last_update_check', 0) >= UPDATE_INTERVAL:
        # The official installer owns package selection and release cleanup.
        command('update', timeout=300)
        state['last_update_check'] = time.time()
    before = versions()
    # Recheck after download because a client may have connected during it.
    reason = idle(before)
    if reason:
        return {'result': 'deferred', 'reason': reason, 'versions': before}
    final = reconcile(before)
    return {'result': 'healthy', 'versions': final}


def save_state(state):
    temp = DATA / 'state.tmp'
    temp.write_text(json.dumps(state, indent=2) + '\n')
    temp.replace(DATA / 'state.json')


def main():
    parser = argparse.ArgumentParser(description='Maintain the local shared Codex daemon when idle.')
    parser.add_argument('action', choices=['run', 'status'])
    parser.add_argument('--check-updates', action='store_true', help='Check for a CLI update now')
    args = parser.parse_args()
    if args.action == 'status':
        info = versions()
        print(json.dumps({'versions': info,
                          'healthy_now': info.get('status') == 'running' and not needs_sync(info),
                          'deferred_reason': idle(info),
                          'last_run': json.loads((DATA / 'state.json').read_text())
                          if (DATA / 'state.json').exists() else None}, indent=2))
        return
    DATA.mkdir(mode=0o700, exist_ok=True)
    with (DATA / 'maintenance.lock').open('w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print('Maintenance is already running')
            return
        state = json.loads((DATA / 'state.json').read_text()) if (DATA / 'state.json').exists() else {}
        try:
            report = maintain(state, args.check_updates)
        except Exception as error:
            report = {'result': 'error', 'reason': str(error)}
            if 'not managed by codex app-server daemon' in str(error):
                report['recovery'] = 'Verify the unmanaged daemon identity and loaded sessions, then complete the one-time migration described in README.md'
            try:
                report['versions'] = versions()
            except Exception as inspection:
                report['inspection_error'] = str(inspection)
        state = {'last_update_check': state.get('last_update_check', 0), **report}
        state['checked_at'] = time.strftime('%Y-%m-%dT%H:%M:%S%z')
        save_state(state)
        print(json.dumps(state))
        if state['result'] == 'error':
            raise SystemExit(1)


if __name__ == '__main__':
    main()
