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
import macos_network as mac
import tailscale_settings as settings
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
        marker = settings.marker(self.root)
        marker.parent.mkdir(parents=True); marker.write_text('{"schema":1}')

    def test_existing_loaded_daemon_keeps_identity_and_connection(self):
        ts.DAEMON.touch()
        with patch.object(ts, 'conflict_check'), patch.object(ts, 'loaded', return_value=True), \
                patch.object(ts, 'wait_ready'), patch.object(ts, 'operator'), \
                patch.object(ts, 'run') as run, patch.object(ts, 'service') as service:
            ts.install(self.root)
        run.assert_not_called(); service.assert_not_called()
        self.assertEqual((self.root / 'bin/tailscale').resolve(), self.bin / 'tailscale')

    def test_first_install_uses_formula_and_system_service(self):
        settings.marker(self.root).unlink()
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
        self.assertEqual(commands[0], [self.prefix / 'bin/brew', 'install', '--formula', 'tailscale'])
        self.assertEqual(commands[1][-4:], ['set', '--accept-dns=false', '--advertise-exit-node=false', '--exit-node='])
        self.assertTrue(settings.marker(self.root).is_file())

    def test_update_restarts_only_when_formula_binary_changes(self):
        ts.DAEMON.touch()
        real = self.prefix / 'Cellar/tailscale/2/bin/tailscale'
        real.parent.mkdir(parents=True); real.touch(); real.chmod(0o755)
        def upgrade(command, **kwargs):
            self.assertEqual(command[1:], ['upgrade', '--formula', 'tailscale'])
            self.assertEqual(kwargs['umask'], 0o022)
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
        self.assertEqual(run.call_args.kwargs['umask'], 0o022)

    def test_homebrew_child_umask_does_not_relax_parent_private_files(self):
        old = os.umask(0o077)
        try:
            directory = self.root / 'public-bin'
            ts.run([sys.executable, '-c', 'import os,sys; os.mkdir(sys.argv[1])', str(directory)], umask=0o022)
            private = self.root / 'private-state'; private.touch()
            self.assertEqual(directory.stat().st_mode & 0o777, 0o755)
            self.assertEqual(private.stat().st_mode & 0o777, 0o600)
        finally:
            os.umask(old)

    def test_rerun_repairs_inaccessible_bin_without_reinstall_or_restart(self):
        ts.DAEMON.touch()
        daemon = self.bin / 'tailscaled'; daemon.chmod(0)
        self.bin.chmod(0)
        def chmod(command, **kwargs):
            self.assertEqual(command[:2], ['/bin/chmod', 'a+rx'])
            path = command[2]; path.chmod(path.stat().st_mode | 0o555)
        try:
            with patch.object(ts, 'conflict_check'), patch.object(ts, 'loaded', return_value=True), \
                    patch.object(ts, 'wait_ready'), patch.object(ts, 'operator'), \
                    patch.object(ts, 'admin', side_effect=chmod) as admin, \
                    patch.object(ts, 'run') as run, patch.object(ts, 'service') as service:
                ts.install(self.root)
            self.assertEqual([call.args[0][2] for call in admin.call_args_list], [self.bin, daemon])
            run.assert_not_called(); service.assert_not_called()
            self.assertTrue(os.access(daemon, os.X_OK))
        finally:
            self.bin.chmod(0o755)

    @unittest.skipUnless(sys.platform == 'darwin', 'macOS enforces symlink read permissions')
    def test_real_unreadable_homebrew_symlinks_are_repaired_before_resolve(self):
        opt = self.prefix / 'opt/tailscale'
        cellar = self.prefix / 'Cellar/tailscale/1.104.7'
        cellar.parent.mkdir(parents=True)
        opt.rename(cellar); opt.symlink_to(cellar)
        linked = self.prefix / 'var/homebrew/linked/tailscale'
        linked.parent.mkdir(parents=True); linked.symlink_to(cellar)
        cellar.chmod(0o700)
        ts.DAEMON.touch()
        commands = []
        def chmod(command, **kwargs):
            commands.append(command)
            # Execute the real chmod against only this test's own files, without sudo.
            self.assertTrue(command[-1].is_relative_to(self.root))
            sp.run(list(map(str, command)), check=True)
        try:
            for link in (opt, linked): os.chmod(link, 0, follow_symlinks=False)
            self.assertTrue(os.access(ts.binary(), os.X_OK))
            with self.assertRaises(PermissionError): ts.binary().resolve()
            with patch.object(ts, 'conflict_check'), patch.object(ts, 'loaded', return_value=True), \
                    patch.object(ts, 'wait_ready'), patch.object(ts, 'operator'), \
                    patch.object(ts, 'admin', side_effect=chmod), patch.object(ts, 'run') as run, \
                    patch.object(ts, 'service') as service:
                ts.install(self.root)
                ts.install(self.root)  # Existing Workbench symlink must also resolve on reruns.
            self.assertEqual(commands, [['/bin/chmod', '-h', 'a+rx', opt],
                                        ['/bin/chmod', '-h', 'a+rx', linked]])
            run.assert_not_called(); service.assert_not_called()
            self.assertEqual((self.root / 'bin/tailscale').resolve(), cellar / 'bin/tailscale')
            self.assertEqual(cellar.stat().st_mode & 0o777, 0o700)
        finally:
            for link in (opt, linked): os.chmod(link, 0o777, follow_symlinks=False)

    def test_permission_failure_reports_the_exact_path(self):
        target = '/opt/homebrew/opt/tailscale'
        with patch.object(sys, 'argv', ['macos_network.py', '--prefix', str(self.root), 'tailscale', 'install']), \
                patch.object(mac.platform, 'system', return_value='Darwin'), patch.object(mac.os, 'umask'), \
                patch.object(ts, 'action', side_effect=PermissionError(13, 'Permission denied', target)):
            with self.assertRaisesRegex(RuntimeError, target): mac.main()

    def test_both_homebrew_labels_are_detected_in_system_and_user_domains(self):
        for label in ts.LABELS:
            for domain in ('system', 'gui/501'):
                def launchctl(command, **kwargs):
                    return Mock(returncode=0 if command[-1] == domain + '/' + label else 1)
                with patch.object(ts.sp, 'run', side_effect=launchctl):
                    self.assertTrue(ts.loaded(domain=domain))
        with patch.object(ts.sp, 'run', return_value=Mock(returncode=1)):
            self.assertFalse(ts.loaded())

    def test_old_and_new_system_plists_count_as_installed(self):
        for path in ts.daemon_files():
            path.touch()
            self.assertTrue(ts.installed())
            path.unlink()
        self.assertFalse(ts.installed())

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
                patch.object(settings, 'login_flags', return_value=['--accept-dns=false', '--advertise-exit-node=false', '--exit-node=']), \
                patch.object(ts.sys.stdin, 'isatty', return_value=True), patch.object(ts, 'admin') as admin:
            ts.action(self.root, 'apply')
        command = admin.call_args.args[0]
        self.assertEqual(command[-5:], ['up', '--timeout=60s', '--accept-dns=false', '--advertise-exit-node=false', '--exit-node='])
        self.assertNotIn('--reset', command)

    def test_reconnect_stopped_node_uses_bare_up_to_preserve_all_settings(self):
        with patch.object(ts, 'install'), patch.object(ts, 'wait_ready', return_value={'BackendState': 'Stopped'}), \
                patch.object(ts.sys.stdin, 'isatty', return_value=True), patch.object(ts, 'admin') as admin:
            ts.action(self.root, 'apply')
        self.assertEqual(admin.call_args.args[0][-1], 'up')
        self.assertEqual(admin.call_args.kwargs, {'timeout': 60})

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
        for action in ('install', 'update', 'apply', 'start', 'stop', 'restart', 'set'):
            self.assertTrue(needs_admin([dict(component='tailscale', action=action)]))
        for action in ('check', 'status', 'logs', 'open', 'show'):
            self.assertFalse(needs_admin([dict(component='tailscale', action=action)]))


if __name__ == '__main__':
    unittest.main()
