import asyncio
import unittest
from unittest.mock import AsyncMock, patch

import maintain as m


class MaintenanceTests(unittest.TestCase):
    def test_linux_daemon_start_uses_independent_scope(self):
        with patch.object(m.sys, 'platform', 'linux'), \
                patch.object(m.subprocess, 'run') as run:
            run.return_value.returncode = 0
            run.return_value.stdout = ''
            m.command('app-server', 'daemon', 'start')
            argv = run.call_args.args[0]
            self.assertEqual(argv[:5], ['/usr/bin/systemd-run', '--user', '--scope', '--quiet', '--'])
            self.assertEqual(argv[5:], [str(m.CLI), 'app-server', 'daemon', 'start'])

    def test_cli_update_and_macos_daemon_start_run_directly(self):
        for platform, args in [('linux', ('update',)),
                               ('darwin', ('app-server', 'daemon', 'start'))]:
            with self.subTest(platform=platform), patch.object(m.sys, 'platform', platform), \
                    patch.object(m.subprocess, 'run') as run:
                run.return_value.returncode = 0
                m.command(*args)
                self.assertEqual(run.call_args.args[0], [str(m.CLI), *args])

    def test_absent_socket_reports_stopped_without_daemon_rpc(self):
        with patch.object(m.Path, 'exists', return_value=False), \
                patch.object(m.Path, 'is_file', return_value=False), \
                patch.object(m, 'command', return_value='codex-cli 1') as command:
            self.assertEqual(m.versions()['status'], 'stopped')
            command.assert_called_once_with('--version')

    def test_stopped_matching_package_starts_without_reinstall(self):
        stopped = {'status': 'stopped', 'cliVersion': '1', 'managedCodexVersion': '1'}
        running = {**stopped, 'status': 'running', 'appServerVersion': '1'}
        with patch.object(m, 'versions', return_value=running), \
                patch.object(m, 'needs_sync', return_value=False), \
                patch.object(m, 'command') as command:
            self.assertEqual(m.reconcile(stopped), running)
            command.assert_called_once_with('app-server', 'daemon', 'start', timeout=60)

    def test_stale_socket_reports_stopped(self):
        with patch.object(m.Path, 'exists', return_value=True), \
                patch.object(m.Path, 'is_file', return_value=False), \
                patch.object(m, 'reachable', return_value=False), \
                patch.object(m, 'command', return_value='codex-cli 1'):
            self.assertEqual(m.versions()['status'], 'stopped')

    def test_loaded_chat_defers_every_mutation(self):
        info = {'status': 'running', 'socketPath': '/tmp/test.sock'}
        with patch.object(m, 'versions', return_value=info), \
                patch.object(m, 'loaded_threads', AsyncMock(return_value=1)), \
                patch.object(m, 'command') as command:
            self.assertEqual(m.maintain({}, True)['result'], 'deferred')
            command.assert_not_called()

    def test_unreadable_activity_blocks_update(self):
        info = {'status': 'running', 'socketPath': '/tmp/test.sock'}
        with patch.object(m, 'versions', return_value=info), \
                patch.object(m, 'loaded_threads', AsyncMock(side_effect=RuntimeError('RPC failed'))), \
                patch.object(m, 'command') as command:
            with self.assertRaisesRegex(RuntimeError, 'RPC failed'):
                m.maintain({}, True)
            command.assert_not_called()

    def test_new_client_during_download_defers_second_switch(self):
        with patch.object(m, 'versions', return_value={}), \
                patch.object(m, 'idle', side_effect=[None, 'session opened']), \
                patch.object(m, 'reconcile') as reconcile, \
                patch.object(m, 'command') as command:
            self.assertEqual(m.maintain({}, True)['result'], 'deferred')
            reconcile.assert_called_once()
            command.assert_called_once_with('update', timeout=300)

    def test_healthy_daemon_checks_online_once_per_day(self):
        state = {'last_update_check': m.time.time()}
        with patch.object(m, 'versions', return_value={}), \
                patch.object(m, 'idle', return_value=None), \
                patch.object(m, 'reconcile', return_value={'status': 'running'}), \
                patch.object(m, 'command') as command:
            self.assertEqual(m.maintain(state)['result'], 'healthy')
            command.assert_not_called()

    def test_independent_cli_defers_self_update(self):
        with patch.object(m, 'standalone_workers', return_value=[123]):
            self.assertIn('123', m.idle({'status': 'stopped'}))

    def test_standalone_worker_detected_through_install_symlink(self):
        with patch.object(m.subprocess, 'check_output',
                          return_value=f'123 1 {m.CLI} --no-daemon\n'), \
                patch.object(m.Path, 'resolve',
                             return_value=m.Path('/home/u/.codex/packages/standalone/releases/1/bin/codex')):
            self.assertEqual(m.standalone_workers(), [123])

    def test_shared_host_is_excluded_but_independent_host_is_protected(self):
        release = '/home/u/.codex/packages/standalone/releases/1/bin/'
        processes = (f'10 1 {release}codex app-server\n'
                     f'11 10 {release}codex-code-mode-host\n'
                     f'20 1 {release}codex --no-daemon\n'
                     f'21 20 {release}codex-code-mode-host\n')
        with patch.object(m, 'shared_daemon_pid', return_value=10), \
                patch.object(m.subprocess, 'check_output', return_value=processes):
            self.assertEqual(m.standalone_workers('/tmp/shared.sock'), [20, 21])

    def test_unknown_parent_does_not_exclude_standalone_worker(self):
        process = '11 99 /home/u/.codex/packages/standalone/releases/1/bin/codex-code-mode-host\n'
        with patch.object(m, 'shared_daemon_pid', return_value=10), \
                patch.object(m.subprocess, 'check_output', return_value=process):
            self.assertEqual(m.standalone_workers('/tmp/shared.sock'), [11])

    def test_missing_host_requires_repair_even_when_versions_match(self):
        info = {'cliVersion': '1', 'appServerVersion': '1', 'managedCodexVersion': '1',
                'managedCodexPath': '/missing/bin/codex'}
        self.assertTrue(m.needs_sync(info))

    def test_loaded_list_pagination_cannot_hide_busy_chat(self):
        ws = AsyncMock()
        ws.recv.side_effect = [
            '{"id":1,"result":{}}',
            '{"id":2,"result":{"data":[],"nextCursor":"next"}}',
            '{"id":3,"result":{"data":["busy"],"nextCursor":null}}',
        ]
        context = AsyncMock()
        context.__aenter__.return_value = ws
        with patch.object(m, 'unix_connect', return_value=context):
            self.assertEqual(asyncio.run(m.loaded_threads('/tmp/test.sock')), 1)


if __name__ == '__main__':
    unittest.main()
