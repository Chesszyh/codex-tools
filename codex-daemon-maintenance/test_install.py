from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import install


class InstallerTests(unittest.TestCase):
    def test_systemd_paths_keep_spaces_and_literal_percent(self):
        units = install.linux_units(Path('/home/a b/100%/tools'), '/home/a b/venv/bin/python')
        service = units['codex-daemon-maintenance.service']
        self.assertIn('WorkingDirectory=/home/a b/100%%/tools', service)
        self.assertIn('ExecStart="/home/a b/venv/bin/python" ', service)

    def test_linux_install_enables_timer_and_runs_service(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(install.Path, 'home', return_value=Path(directory)), \
                patch.object(install.subprocess, 'run') as run:
            install.install_linux()
            units = Path(directory) / '.config/systemd/user'
            self.assertTrue((units / 'codex-daemon-maintenance.service').is_file())
            self.assertTrue((units / 'codex-daemon-maintenance.timer').is_file())
            self.assertEqual(run.call_args_list[2].args[0],
                             ['systemctl', '--user', 'enable', '--now', 'codex-daemon-maintenance.timer'])
            self.assertEqual(run.call_args_list[3].args[0],
                             ['systemctl', '--user', 'start', 'codex-daemon-maintenance.service'])

    def test_invalid_units_preserve_existing_installation(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(install.Path, 'home', return_value=Path(directory)), \
                patch.object(install.subprocess, 'run',
                             side_effect=install.subprocess.CalledProcessError(1, 'verify')):
            unit = Path(directory) / '.config/systemd/user/codex-daemon-maintenance.service'
            unit.parent.mkdir(parents=True)
            unit.write_text('previous config')
            with self.assertRaises(install.subprocess.CalledProcessError):
                install.install_linux()
            self.assertEqual(unit.read_text(), 'previous config')


if __name__ == '__main__':
    unittest.main()
