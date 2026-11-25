import contextlib
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
import tailscale_settings as settings
import deck_network as deck
from workbench import builtin


class SettingsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.cli = ['/test/tailscale', '--socket=/test/socket']
        self.output = io.StringIO()
        context = patch('sys.stdout', self.output); context.start(); self.addCleanup(context.stop)

    def test_defaults_are_applied_once_and_do_not_reset_future_choices(self):
        privileged = Mock()
        settings.apply(self.root, self.cli, privileged)
        privileged.assert_called_once_with([*self.cli, 'set', '--accept-dns=false',
                                            '--advertise-exit-node=false', '--exit-node='])
        self.assertEqual(settings.marker(self.root).stat().st_mode & 0o777, 0o600)
        settings.apply(self.root, self.cli, privileged, {'accept-dns': True})
        self.assertEqual(privileged.call_args.args[0], [*self.cli, 'set', '--accept-dns=true'])
        settings.apply(self.root, self.cli, privileged)
        self.assertEqual(privileged.call_count, 2)

    def test_first_explicit_choice_overrides_defaults_in_one_call(self):
        privileged = Mock()
        settings.apply(self.root, self.cli, privileged, {'exit-node': '100.64.0.9'})
        privileged.assert_called_once_with([*self.cli, 'set', '--accept-dns=false',
                                            '--advertise-exit-node=false', '--exit-node=100.64.0.9'])

    def test_failure_does_not_mark_defaults_as_completed(self):
        with self.assertRaisesRegex(RuntimeError, '设置失败'):
            settings.apply(self.root, self.cli, Mock(side_effect=sp.CalledProcessError(1, [])))
        self.assertFalse(settings.marker(self.root).exists())

    def test_boolean_and_exit_node_input_maps_to_native_flags(self):
        changes = settings.parse(['--accept-dns=false', '--advertise-exit-node', 'true', '--exit-node=false'])
        self.assertEqual(changes, {'accept-dns': False, 'advertise-exit-node': True, 'exit-node': False})
        self.assertIn('--exit-node=', settings.flags(changes))
        self.assertEqual(settings.parse(['--exit-node=my-server']), {'exit-node': 'my-server'})
        self.assertEqual(settings.parse(['--exit-node=']), {'exit-node': False})

    def test_invalid_and_contradictory_arguments_are_rejected(self):
        for args in ([], ['--accept-dns=yes'], ['--accept-routes=yes'], ['--exit-node=true'], ['--dns=false'],
                     ['--advertise-exit-node=true', '--exit-node=server'], ['--exit-node=--reset']):
            with self.subTest(args=args), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit): settings.parse(args)

    def test_subnet_routes_are_explicit_and_preserved_across_login(self):
        self.assertNotIn('accept-routes', settings.DEFAULTS)
        self.assertEqual(settings.parse(['--accept-routes=true', '--exit-node=false']),
                         {'accept-routes': True, 'exit-node': False})
        for enabled in (True, False):
            prefs = {'CorpDNS': False, 'RouteAll': enabled}
            with patch.object(settings.sp, 'run', return_value=Mock(returncode=0, stdout=json.dumps(prefs))):
                self.assertIn('--accept-routes=' + str(enabled).lower(), settings.login_flags(self.cli))
                settings.show(self.cli)
            self.assertIn('accept-routes: ' + str(enabled).lower(), self.output.getvalue())

    def test_invalid_route_preference_is_not_reported_as_a_real_value(self):
        with patch.object(settings.sp, 'run', return_value=Mock(returncode=0, stdout='{"CorpDNS":false,"RouteAll":"false"}')):
            with self.assertRaisesRegex(RuntimeError, '设置格式'):
                settings.preferences(self.cli)

    def test_show_reads_actual_preferences_and_never_prints_identity_data(self):
        prefs = {'CorpDNS': False, 'AdvertiseRoutes': ['10.0.0.0/24', '::/0'],
                 'ExitNodeID': 'node-id', 'Persist': {'PrivateNodeKey': 'SECRET'}}
        with patch.object(settings.sp, 'run', return_value=Mock(returncode=0, stdout=json.dumps(prefs))) as run:
            settings.show(self.cli)
        self.assertEqual(run.call_args.args[0], [*self.cli, 'debug', 'prefs'])
        output = self.output.getvalue()
        self.assertIn('accept-dns: false', output)
        self.assertIn('advertise-exit-node: true', output)
        self.assertIn('exit-node: node-id', output)
        self.assertNotIn('SECRET', output)
        self.assertFalse(settings.marker(self.root).exists())

    def test_show_failure_does_not_claim_default_values_are_live(self):
        with patch.object(settings.sp, 'run', return_value=Mock(returncode=1, stdout='')):
            with self.assertRaisesRegex(RuntimeError, '无法读取'): settings.show(self.cli)
        self.assertEqual(self.output.getvalue(), '')

    def test_first_login_carries_false_defaults_and_operator_into_up(self):
        prefs = {'CorpDNS': False, 'AdvertiseRoutes': [], 'OperatorUser': 'marc'}
        with patch.object(settings, 'preferences', return_value=prefs):
            self.assertEqual(settings.login_flags(self.cli), ['--accept-dns=false', '--advertise-exit-node=false',
                                                            '--exit-node=', '--operator=marc'])

    def test_login_preserves_enabled_choices_and_resolves_exit_node_id(self):
        prefs = {'CorpDNS': True, 'ExitNodeID': 'node123'}
        status = {'Peer': {'publickey': {'ID': 'node123', 'TailscaleIPs': ['100.64.0.9']}}}
        with patch.object(settings, 'preferences', return_value=prefs), \
                patch.object(settings.sp, 'run', return_value=Mock(stdout=json.dumps(status))):
            self.assertEqual(settings.login_flags(self.cli), ['--accept-dns=true', '--advertise-exit-node=false',
                                                             '--exit-node=100.64.0.9'])

    def test_deck_defaults_are_set_before_first_login(self):
        calls = []
        with patch.object(deck.os, 'environ', {}), patch.object(deck, 'text', return_value='pinned'), \
                patch.object(deck, 'network_sources', return_value={'deck-tailscale': {'revision': 'pinned'}}), \
                patch.object(settings, 'login_flags', return_value=['--accept-dns=false']), \
                patch.object(deck, 'run', side_effect=lambda args, **kw: calls.append(args)), \
                patch.object(deck.sp, 'run', return_value=Mock(stdout='{"BackendState":"NeedsLogin"}')):
            deck.authorize(self.root)
        default_index = next(i for i, command in enumerate(calls) if '--accept-dns=false' in command)
        login_index = next(i for i, command in enumerate(calls) if 'up' in command)
        self.assertLess(default_index, login_index)
        self.assertEqual(calls[default_index][0:3], ['sudo', '/opt/tailscale/tailscale', 'set'])

    def test_dispatch_uses_correct_platform_cli_and_preserves_unrelated_options(self):
        import macos_tailscale as mac
        import macos_tailscale_mode as variants
        for host in ('macos', 'steamdeck'):
            with patch.object(builtin, 'ROOT', self.root), patch.object(builtin, 'detect', return_value=host), \
                    patch.object(variants, 'mode', return_value='server'), \
                    patch.object(settings, 'apply') as apply:
                self.assertEqual(builtin.main('tailscale', ['set', '--accept-dns=false']), 0)
            args = apply.call_args.args
            self.assertEqual(args[-1], {'accept-dns': False})
            self.assertEqual(args[1], [mac.binary(), '--socket=' + mac.SOCKET] if host == 'macos' else ['/opt/tailscale/tailscale'])

    def test_help_completion_and_dry_run_work_without_changing_settings(self):
        env = dict(os.environ, SOFTWARE_HOME=str(self.root), WORKBENCH_BOOTSTRAP_PYTHON=sys.executable)
        for args in (['tailscale', 'set', '--help'], ['--complete', 'options', 'tailscale', 'set'],
                     ['tailscale', 'set', '--accept-dns=false', '--exit-node=false', '--accept-routes=true', '--plan', '--json']):
            result = sp.run([sys.executable, REPO / 'bin/workbench', *args], env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('--accept-dns=false', result.stdout)
            self.assertIn('--exit-node=', result.stdout)
            self.assertIn('--accept-routes=true', result.stdout)
        self.assertFalse(settings.marker(self.root).exists())


if __name__ == '__main__': unittest.main()
