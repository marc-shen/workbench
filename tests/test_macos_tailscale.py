import io
import json
import os
from pathlib import Path
import subprocess as sp
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO / 'libexec'), str(REPO / 'lib')]
import macos_tailscale as ts
from workbench.privilege import needs_admin


class TailscaledTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='tailscaled test ')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.prefix = self.root / 'brew'
        (self.prefix / 'bin').mkdir(parents=True)
        (self.prefix / 'bin/brew').touch()
        self.bin = self.prefix / 'opt/tailscale/bin'
        self.bin.mkdir(parents=True)
        for name in ('tailscale', 'tailscaled'):
            path = self.bin / name; path.touch(); path.chmod(0o755)
        for target, value in [('brew_prefix', Mock(return_value=self.prefix)),
                              ('DAEMON', self.root / 'daemon.plist')]:
            p = patch.object(ts, target, value); p.start(); self.addCleanup(p.stop)
        self.output = io.StringIO()
        p = patch('sys.stdout', self.output); p.start(); self.addCleanup(p.stop)

    def test_existing_loaded_daemon_keeps_identity_and_connection(self):
        ts.DAEMON.touch()
        with patch.object(ts, 'conflict_check'), patch.object(ts, 'loaded', return_value=True), \
                patch.object(ts, 'wait_ready'), patch.object(ts, 'operator'), \
                patch.object(ts, 'run') as run, patch.object(ts, 'service') as service:
            ts.install(self.root)
        run.assert_not_called(); service.assert_not_called()
        self.assertEqual((self.root / 'bin/tailscale').resolve(), self.bin / 'tailscale')

    def test_first_install_uses_formula_and_system_service(self):
        for path in self.bin.iterdir(): path.unlink()
        commands = []
        def run(command, **kwargs):
            commands.append(command)
            for name in ('tailscale', 'tailscaled'):
                path = self.bin / name; path.touch(); path.chmod(0o755)
        def service(operation):
            self.assertEqual(operation, 'start'); ts.DAEMON.touch()
        with patch.object(ts, 'conflict_check'), patch.object(ts, 'loaded', side_effect=[False, False, True]), \
                patch.object(ts, 'wait_ready'), patch.object(ts, 'operator'), patch.object(ts, 'run', side_effect=run), \
                patch.object(ts, 'service', side_effect=service):
            ts.install(self.root)
        self.assertEqual(commands, [[self.prefix / 'bin/brew', 'install', '--formula', 'tailscale']])

    def test_update_restarts_only_when_formula_binary_changes(self):
        ts.DAEMON.touch()
        real = self.prefix / 'Cellar/tailscale/2/bin/tailscale'
        real.parent.mkdir(parents=True); real.touch(); real.chmod(0o755)
        def upgrade(command):
            self.assertEqual(command[1:], ['upgrade', '--formula', 'tailscale'])
            (self.bin / 'tailscale').unlink(); (self.bin / 'tailscale').symlink_to(real)
        with patch.object(ts, 'conflict_check'), patch.object(ts, 'loaded', return_value=True), \
                patch.object(ts, 'wait_ready'), patch.object(ts, 'operator'), \
                patch.object(ts, 'run', side_effect=upgrade), patch.object(ts, 'service') as service:
            ts.install(self.root, update=True)
        service.assert_called_once_with('restart')

    def test_service_management_always_uses_terminal_sudo(self):
        with patch.object(ts.sys.stdin, 'isatty', return_value=False), patch.object(ts, 'run') as run:
            ts.service('start')
        self.assertEqual(run.call_args.args[0], ['/usr/bin/sudo', '-n', '--', self.prefix / 'bin/brew',
                                               'services', 'start', 'tailscale'])

    def test_new_log_is_private_and_user_readable(self):
        ts.prepare_log()
        log = self.prefix / 'var/log/tailscaled.log'
        self.assertEqual(log.stat().st_mode & 0o777, 0o600)
        self.assertEqual(log.stat().st_uid, os.getuid())

    def test_gui_and_residual_extension_conflicts_do_not_install_or_stop_anything(self):
        with patch.object(ts.sp, 'run', return_value=Mock(returncode=0)), \
                patch.object(ts, 'service') as service, patch.object(ts, 'run') as run:
            with self.assertRaisesRegex(RuntimeError, 'GUI'):
                ts.install(self.root)
        run.assert_not_called(); service.assert_not_called()

    def test_user_daemon_and_other_system_daemon_are_not_silently_overwritten(self):
        for results in ([True], [False, True]):
            with patch.object(Path, 'exists', return_value=False), \
                    patch.object(ts.sp, 'run', return_value=Mock(returncode=1)), \
                    patch.object(ts, 'loaded', side_effect=results):
                with self.assertRaisesRegex(RuntimeError, '服务'):
                    ts.conflict_check()

    def test_needs_login_is_a_ready_daemon_even_with_nonzero_cli_status(self):
        with patch.object(ts.sp, 'run', return_value=Mock(returncode=1, stdout='{"BackendState":"NeedsLogin"}')):
            self.assertEqual(ts.wait_ready()['BackendState'], 'NeedsLogin')

    def test_operator_uses_system_socket_and_current_real_user(self):
        with patch.object(ts.pwd, 'getpwuid', return_value=Mock(pw_name='test-user')), patch.object(ts, 'admin') as admin:
            ts.operator()
        self.assertEqual(admin.call_args.args[0], [self.bin / 'tailscale', '--socket=/var/run/tailscaled.socket',
                                                 'set', '--operator=test-user'])

    def test_apply_does_not_reset_a_connected_node(self):
        with patch.object(ts, 'install'), patch.object(ts, 'wait_ready', return_value={'BackendState': 'Running'}), \
                patch.object(ts, 'admin') as admin:
            self.assertEqual(ts.action(self.root, 'apply'), 0)
        admin.assert_not_called()

    def test_noninteractive_login_returns_pending_instead_of_hanging(self):
        with patch.object(ts, 'install'), patch.object(ts, 'wait_ready', return_value={'BackendState': 'NeedsLogin'}), \
                patch.object(ts.sys.stdin, 'isatty', return_value=False), patch.object(ts, 'admin') as admin:
            self.assertEqual(ts.action(self.root, 'apply'), 2)
        admin.assert_not_called()

    def test_login_is_bounded_and_does_not_reset_preferences(self):
        with patch.object(ts, 'install'), patch.object(ts, 'wait_ready', return_value={'BackendState': 'NeedsLogin'}), \
                patch.object(ts.sys.stdin, 'isatty', return_value=True), patch.object(ts, 'admin') as admin:
            ts.action(self.root, 'apply')
        command = admin.call_args.args[0]
        self.assertEqual(command[-2:], ['up', '--timeout=60s'])
        self.assertNotIn('--reset', command)

    def test_check_is_read_only_and_reports_unloaded_or_unauthenticated_daemon(self):
        for state, expected in [('Running', 0), ('Stopped', 0), ('NeedsLogin', 1), ('NeedsMachineAuth', 1)]:
            with patch.object(ts, 'installed', return_value=True), patch.object(ts, 'loaded', return_value=True), \
                    patch.object(ts.sp, 'run', return_value=Mock(stdout=json.dumps({'BackendState': state}))), \
                    patch.object(ts, 'admin') as admin:
                self.assertEqual(ts.status(check=True), expected)
                admin.assert_not_called()
        with patch.object(ts, 'installed', return_value=True), patch.object(ts, 'loaded', return_value=False):
            self.assertEqual(ts.status(check=True), 1)

    def test_stop_and_restart_manage_daemon_without_logging_out(self):
        for operation in ('stop', 'restart'):
            with patch.object(ts, 'service') as service, patch.object(ts, 'conflict_check'), \
                    patch.object(ts, 'wait_ready'), patch.object(ts, 'admin') as admin:
                ts.action(self.root, operation)
            service.assert_called_once_with(operation)
            admin.assert_not_called()

    def test_all_mutating_service_actions_share_sudo_session(self):
        for action in ('install', 'update', 'apply', 'start', 'stop', 'restart'):
            self.assertTrue(needs_admin([dict(component='tailscale', action=action)]))
        for action in ('check', 'status', 'logs', 'open'):
            self.assertFalse(needs_admin([dict(component='tailscale', action=action)]))


if __name__ == '__main__':
    unittest.main()
