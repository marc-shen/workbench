import hashlib
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
import macos_tailscale_mode as variants
import macos_tailscale_gui as gui
import tailscale_settings as settings
from workbench import builtin


class ModesTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.app = self.root / 'Applications/Tailscale.app'
        self.patches = [patch.object(variants, 'APP', self.app),
                        patch.object(gui, 'STATE', self.root / 'Library/Tailscale'),
                        patch.object(variants.server, 'binary', return_value=self.root / 'brew/bin/tailscale'),
                        patch.object(variants.server, 'daemon_files', return_value=[]),
                        patch.object(Path, 'home', return_value=self.root),
                        patch('sys.stdout', io.StringIO())]
        for context in self.patches:
            context.start(); self.addCleanup(context.stop)

    def test_new_noninteractive_install_defaults_to_personal(self):
        with patch('sys.stdin', io.StringIO('')):
            self.assertEqual(variants.mode(self.root, prompt=True), 'personal')
        self.assertFalse(variants.selection_file(self.root).exists())

    def test_interactive_prompt_and_default(self):
        for answer, expected in [('\n', 'personal'), ('2\n', 'server'), ('bad\n1\n', 'personal')]:
            stream = io.StringIO(answer); stream.isatty = lambda: True
            with patch('sys.stdin', stream):
                self.assertEqual(variants.mode(self.root, prompt=True), expected)

    def test_old_stopped_formula_and_saved_selection_are_preserved(self):
        variants.server.binary().parent.mkdir(parents=True)
        self.assertEqual(variants.mode(self.root), 'server')
        variants.save(self.root, 'personal')
        self.assertEqual(variants.mode(self.root), 'personal')
        self.assertEqual(variants.mode(self.root, 'server'), 'server')

    def test_unreadable_brew_opt_link_still_selects_server_for_repair(self):
        opt = variants.server.binary().parent.parent
        opt.symlink_to(self.root / 'private-cellar')
        exists = Path.exists
        def restricted(path):
            if path == variants.server.binary().parent:
                raise PermissionError(13, 'Permission denied', str(path))
            return exists(path)
        with patch.object(Path, 'exists', restricted):
            self.assertEqual(variants.mode(self.root), 'server')

    def test_failed_migration_does_not_save_or_stop_service(self):
        variants.save(self.root, 'server')
        with patch.object(gui, 'action', side_effect=RuntimeError('conflict')), patch.object(variants.server, 'service') as service:
            with self.assertRaisesRegex(RuntimeError, 'conflict'):
                variants.action(self.root, 'install', 'personal')
        service.assert_not_called()
        self.assertEqual(variants.mode(self.root), 'server')

    def test_updates_follow_saved_mode_without_prompting(self):
        for selected in variants.MODES:
            variants.save(self.root, selected)
            backend = gui if selected == 'personal' else variants.server
            with patch.object(backend, 'action', return_value=0) as action:
                self.assertEqual(variants.action(self.root, 'update'), 0)
            action.assert_called_once_with(self.root, 'update')

    def test_personal_settings_use_user_cli_and_separate_default_marker(self):
        variants.save(self.root, 'personal')
        self.app.mkdir(parents=True)
        settings.marker(self.root).write_text('{}')
        with patch.object(variants.server, 'run') as run, patch.object(variants.server, 'admin') as admin:
            variants.configure(self.root, {'accept-dns': False})
        admin.assert_not_called()
        args = run.call_args.args[0]
        self.assertEqual(args[:3], ['/usr/bin/env', 'TAILSCALE_BE_CLI=1', self.app / 'Contents/MacOS/Tailscale'])
        self.assertIn('--exit-node=', args)
        self.assertTrue((self.root / 'config/tailscale/personal-defaults-applied.json').exists())
        with patch.object(variants.server, 'run') as run:
            variants.configure(self.root, {'accept-dns': True})
        self.assertEqual(run.call_args.args[0][-2:], ['set', '--accept-dns=true'])

    def test_server_rejects_exit_client_but_allows_clear(self):
        variants.save(self.root, 'server')
        with patch.object(settings, 'apply') as apply:
            with self.assertRaisesRegex(RuntimeError, '不支持'):
                variants.configure(self.root, {'exit-node': 'fedora'})
            apply.assert_not_called()
            variants.configure(self.root, {'exit-node': False})
            apply.assert_called_once()

    def test_personal_exit_conflict_does_not_write_preferences_or_marker(self):
        variants.save(self.root, 'personal')
        with patch('macos_network_guard.require_exit_compatible', side_effect=RuntimeError('TUN 冲突')), \
                patch.object(settings, 'apply') as apply:
            with self.assertRaisesRegex(RuntimeError, 'TUN'):
                variants.configure(self.root, {'exit-node': 'fedora'})
            apply.assert_not_called()
            self.assertFalse((self.root / 'config/tailscale/personal-defaults-applied.json').exists())
            variants.configure(self.root, {'exit-node': False, 'accept-routes': True})
            apply.assert_called_once()

    def test_gui_selected_exit_conflict_is_reported_by_check(self):
        variants.save(self.root, 'personal')
        with patch.object(gui, 'action', return_value=0), patch.object(gui, 'state', return_value='Running'), \
                patch('macos_network_guard.check', side_effect=RuntimeError('TUN 冲突')):
            self.assertEqual(variants.action(self.root, 'check'), 1)
            self.assertEqual(variants.action(self.root, 'status'), 0)

    def test_old_identity_blocks_fresh_gui_install_but_not_existing_gui(self):
        gui.STATE.mkdir(parents=True)
        (gui.STATE / 'tailscaled.state').write_text('old identity')
        with patch.object(gui.sp, 'run', return_value=Mock(returncode=1)), \
                patch.object(gui.server, 'loaded', return_value=False), \
                patch.object(gui, 'installed', return_value=False), patch.object(gui, 'latest') as latest:
            with self.assertRaisesRegex(RuntimeError, '旧身份数据'):
                gui.install(self.root)
            latest.assert_not_called()
            with patch.object(gui, 'installed', return_value=True):
                gui.conflict_check()
        self.assertEqual((gui.STATE / 'tailscaled.state').read_text(), 'old identity')

    def test_malformed_gui_status_is_unavailable(self):
        with patch.object(gui, 'installed', return_value=True):
            for payload in ('[]', 'null', 'invalid'):
                with patch.object(gui.server, 'run', return_value=Mock(stdout=payload)):
                    self.assertEqual(gui.state(), 'Unavailable')
                with patch.object(gui.server, 'run', side_effect=sp.CalledProcessError(1, [], output=payload)):
                    self.assertEqual(gui.state(), 'Unavailable')

    def test_install_options_are_forwarded_only_on_macos(self):
        with patch.object(builtin, 'detect', return_value='macos'), patch.object(builtin, 'backend', return_value=0) as backend:
            self.assertEqual(builtin.main('tailscale', ['install', '--mode=server']), 0)
        backend.assert_called_once_with('macos_network.py', 'tailscale', 'install', '--mode=server')
        with patch.object(builtin, 'detect', return_value='steamdeck'):
            with self.assertRaises(RuntimeError):
                builtin.main('tailscale', ['install', '--mode=server'])

    def test_public_help_completion_and_plan_include_both_modes(self):
        env = dict(os.environ, SOFTWARE_HOME=str(self.root), WORKBENCH_BOOTSTRAP_PYTHON=sys.executable)
        for args in (['tailscale', 'install', '--help'], ['--complete', 'options', 'tailscale', 'install']):
            result = sp.run([sys.executable, REPO / 'bin/workbench', *args], env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('--mode=personal', result.stdout)
            self.assertIn('--mode=server', result.stdout)
        result = sp.run([sys.executable, REPO / 'bin/workbench', '--platform', 'macos', 'tailscale', 'install',
                         '--mode=personal', '--plan', '--json'], env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('--mode=personal', json.loads(result.stdout)['steps'][0]['command'])
        self.assertFalse(variants.selection_file(self.root).exists())

    def test_live_server_blocks_gui_before_any_download_or_installer(self):
        with patch.object(gui.sp, 'run', return_value=Mock(returncode=0)), \
                patch.object(gui, 'latest') as latest, patch.object(gui.server, 'admin') as admin:
            with self.assertRaisesRegex(RuntimeError, 'tailscaled'):
                gui.install(self.root)
        latest.assert_not_called(); admin.assert_not_called()

    def test_package_hash_mismatch_never_installs(self):
        def run(args, **kwargs):
            if str(args[-1]).endswith('.sha256'):
                return Mock(stdout='0' * 64)
            if '--output' in args:
                Path(args[args.index('--output') + 1]).write_bytes(b'bad download')
            return Mock(returncode=0)
        with patch.object(gui.server, 'run', side_effect=run), patch.object(gui.server, 'admin') as admin:
            with self.assertRaisesRegex(RuntimeError, 'SHA256'):
                gui.package(self.root, 'Tailscale-1.2.3-macos.pkg')
        admin.assert_not_called()

    def test_cached_package_is_hashed_and_signature_checked(self):
        name = 'Tailscale-1.2.3-macos.pkg'
        path = self.root / 'cache/tailscale' / name
        path.parent.mkdir(parents=True); path.write_bytes(b'package')
        with patch.object(gui.server, 'run', return_value=Mock(stdout=hashlib.sha256(b'package').hexdigest())) as run:
            self.assertEqual(gui.package(self.root, name), path)
        self.assertEqual(run.call_count, 3)
        self.assertEqual(run.call_args_list[1].args[0][:2], ['/usr/sbin/pkgutil', '--check-signature'])
        self.assertIn('--assess', run.call_args_list[2].args[0])

    def test_manifest_rejects_unexpected_package_path(self):
        data = {'MacZipsVersion': '1.2.3', 'MacZips': {'universal-package': '../evil.pkg'}}
        with patch.object(gui.server, 'run', return_value=Mock(stdout=json.dumps(data))):
            with self.assertRaisesRegex(RuntimeError, '安装包'):
                gui.latest()

    def test_first_install_uses_verified_pkg_and_terminal_installer(self):
        path = self.root / 'verified.pkg'
        with patch.object(gui, 'conflict_check'), patch.object(gui, 'installed', side_effect=[False, False, True]), \
                patch.object(gui, 'latest', return_value=('Tailscale-1.2.3-macos.pkg', '1.2.3')), \
                patch.object(gui, 'package', return_value=path) as package, \
                patch.object(gui.server, 'admin') as admin, patch.object(gui, 'link_cli'), \
                patch.object(gui, 'state', return_value='Unavailable'), patch.object(gui, 'defaults') as defaults:
            gui.install(self.root)
        package.assert_called_once_with(self.root, 'Tailscale-1.2.3-macos.pkg')
        admin.assert_called_once_with(['/usr/sbin/installer', '-verboseR', '-pkg', path, '-target', '/'], umask=0o022)
        defaults.assert_not_called()

    def test_existing_newer_gui_update_does_not_downgrade(self):
        with patch.object(gui, 'conflict_check'), patch.object(gui, 'installed', return_value=True), \
                patch.object(gui, 'latest', return_value=('Tailscale-1.2.3-macos.pkg', '1.2.3')), \
                patch.object(gui, 'version', return_value=(1, 3, 0)), patch.object(gui, 'package') as package, \
                patch.object(gui.server, 'admin') as admin, patch.object(gui, 'link_cli'), \
                patch.object(gui, 'state', return_value='Running'), patch.object(gui, 'defaults'):
            gui.install(self.root, update=True)
        package.assert_not_called(); admin.assert_not_called()

    def test_wrapper_replaces_link_without_modifying_old_binary(self):
        old = self.root / 'old-binary'; old.write_bytes(b'\xffnative executable')
        (self.root / 'bin').mkdir()
        (self.root / 'bin/tailscale').symlink_to(old)
        gui.link_cli(self.root)
        self.assertEqual(old.read_bytes(), b'\xffnative executable')
        self.assertIn('TAILSCALE_BE_CLI=1', (self.root / 'bin/tailscale').read_text())

    def test_pending_approval_is_not_recorded_as_configured(self):
        with patch.object(gui, 'install'), patch.object(gui, 'installed', return_value=True), \
                patch.object(gui, 'conflict_check'), patch.object(gui, 'state', return_value='NeedsLogin'), \
                patch.object(gui.server, 'run'), patch.object(gui, 'defaults') as defaults:
            self.assertEqual(gui.action(self.root, 'apply'), 2)
        defaults.assert_not_called()

    def test_wrapper_can_replace_cli_with_unreadable_homebrew_target_link(self):
        old = self.root / 'old-binary'; old.write_bytes(b'original')
        destination = self.root / 'bin/tailscale'
        destination.parent.mkdir(); destination.symlink_to(old)
        resolve = Path.resolve
        def restricted(path, *args, **kwargs):
            if path == destination:
                raise PermissionError(13, 'Permission denied', '/opt/homebrew/opt/tailscale')
            return resolve(path, *args, **kwargs)
        with patch.object(Path, 'resolve', restricted):
            gui.link_cli(self.root)
        self.assertIn('TAILSCALE_BE_CLI=1', destination.read_text())
        self.assertEqual(old.read_bytes(), b'original')
        backup = next((self.root / 'backups').glob('*/bin/tailscale'))
        self.assertEqual(backup.readlink(), old)

    def test_gui_restart_uses_user_cli_and_preserves_preferences(self):
        with patch.object(gui, 'installed', return_value=True), patch.object(gui, 'conflict_check'), \
                patch.object(gui, 'state', return_value='Running'), patch.object(gui, 'defaults'), \
                patch.object(gui.server, 'run') as run, patch.object(gui.server, 'admin') as admin:
            self.assertEqual(gui.action(self.root, 'restart'), 0)
        self.assertEqual([c.args[0][-1] for c in run.call_args_list][1:], ['down', 'up'])
        admin.assert_not_called()


if __name__ == '__main__':
    unittest.main()
